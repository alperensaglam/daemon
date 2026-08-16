'use strict';

/**
 * TelegramController — uzaktan kontrol katmani.
 *
 * Agent'a ikinci bir "kullanici arayuzu" ekler: mesaj girdi kuyruguna, onay
 * istekleri satir ici butonlara, sonuclar ve ekran goruntuleri sohbete gider.
 *
 * Mimari: runner.js'e iki noktadan baglanir —
 *   * `registerApprover('telegram', ...)` ile onay yarisinina katilir,
 *   * `onEvent(...)` ile calisma olaylarini dinler.
 * loop.js'te hicbir degisiklik gerekmez; onay sozlesmesi zaten enjekte
 * edilebilirdi.
 *
 * GUVENLIK
 *  * Yalnizca yapilandirilmis `userId` kabul edilir. Hem `message.from.id` hem
 *    `callback_query.from.id` kontrol edilir — ikincisi unutulursa yabanci
 *    biri butona basarak onay verebilirdi.
 *  * Yetkisiz mesajlara **cevap verilmez**. Cevap vermek, botun canli
 *    oldugunu ve dogru token'in bulundugunu dogrulardi.
 *  * Token asla mesajlara veya loglara yazilmaz (bkz. telegram.js scrub).
 */

const config = require('../config');
const runner = require('../agent/runner');
const registry = require('../agent/registry');
const { TelegramClient, escapeHtml } = require('./telegram');
const screenshot = require('./screenshot');
const stt = require('./stt');
const { activeWindow, OS_LABEL } = require('../platform');

/** Onay butonlarinin callback_data on eki. */
const CB = 'ap:';

/** Bir onay istegi cevapsiz kalirsa ne kadar beklenir. */
const APPROVAL_TIMEOUT_MS = 10 * 60 * 1000;

const RISK_LABEL = { safe: 'guvenli', write: 'yazma', danger: 'riskli' };

class TelegramController {
  constructor() {
    this._client = null;
    this._chatId = null;
    this._userId = null;
    this._unsubscribe = [];
    /** callId -> { messageId, resolve } */
    this._pending = new Map();
    this._lastRun = null;
    this._started = false;
    this._botName = '';
  }

  get isRunning() {
    return this._started;
  }

  status() {
    return {
      running: this._started,
      botName: this._botName,
      chatId: this._chatId,
    };
  }

  /**
   * Baslatir. Token/userId once ortam degiskeninden, sonra ayarlardan okunur.
   * @returns {Promise<{ok: boolean, error?: string, botName?: string}>}
   */
  async start() {
    if (this._started) return { ok: true, botName: this._botName };

    const cfg = config.load();
    const settings = cfg.telegram || {};
    const token = (process.env.TELEGRAM_BOT_TOKEN || settings.token || '').trim();
    const userId = String(
      process.env.TELEGRAM_USER_ID || settings.userId || ''
    ).trim();

    if (!token) return { ok: false, error: 'Bot token tanimli degil.' };
    if (!userId || !/^\d+$/.test(userId)) {
      return {
        ok: false,
        error: 'Gecerli bir kullanici ID gerekli (yalnizca rakam). '
          + '@userinfobot ile ogrenebilirsiniz.',
      };
    }

    this._userId = userId;
    // TELEGRAM_API_ROOT: Telegram'in kendi barindirilan Bot API sunucusunu
    // (telegram-bot-api) destekler; testlerde de sahte sunucuya yonlendirir.
    this._client = new TelegramClient(token, {
      apiRoot: process.env.TELEGRAM_API_ROOT || undefined,
    });

    let me;
    try {
      me = await this._client.getMe();
    } catch (err) {
      this._client = null;
      return { ok: false, error: err.message };
    }

    this._botName = me.username ? `@${me.username}` : (me.first_name || 'bot');
    this._started = true;

    this._unsubscribe.push(
      runner.registerApprover('telegram', (req, ctx) => this._onApproval(req, ctx))
    );
    this._unsubscribe.push(runner.onEvent((evt) => this._onAgentEvent(evt)));

    this._client.startPolling(
      (update) => this._handleUpdate(update),
      { onError: (err) => console.warn('Telegram:', err.message) }
    );

    console.log(`Telegram uzaktan kontrol acik: ${this._botName}`);
    return { ok: true, botName: this._botName };
  }

  async stop() {
    if (!this._started) return;
    this._started = false;
    this._client?.stopPolling();
    for (const off of this._unsubscribe) {
      try { off(); } catch { /* yoksay */ }
    }
    this._unsubscribe = [];
    // Bekleyen onaylar sonsuza kadar askida kalmasin.
    for (const [, entry] of this._pending) entry.resolve(null);
    this._pending.clear();
    this._client = null;
  }

  async restart() {
    await this.stop();
    return this.start();
  }

  // ------------------------------------------------------------------ //
  //  Yetkilendirme
  // ------------------------------------------------------------------ //

  /**
   * Gonderen yetkili mi?
   * Yetkisizse **sessizce** false doner; cevap vermek botun varligini
   * dogrulardi.
   */
  _authorize(from, where) {
    const id = String(from?.id ?? '');
    if (id && id === this._userId) return true;
    console.warn(
      `Telegram: yetkisiz ${where} reddedildi (id=${id || 'bilinmiyor'}, `
      + `kullanici=${from?.username || '-'})`
    );
    return false;
  }

  // ------------------------------------------------------------------ //
  //  Gelen guncellemeler
  // ------------------------------------------------------------------ //

  async _handleUpdate(update) {
    if (update.callback_query) return this._handleCallback(update.callback_query);
    if (update.message) return this._handleMessage(update.message);
  }

  async _handleCallback(query) {
    // KRITIK: buton basimlari da yetkilendirilmeli. Yalnizca mesajlari
    // kontrol etmek, yabancinin onay verebilmesi demek olurdu.
    if (!this._authorize(query.from, 'buton')) {
      await this._safe(() => this._client.answerCallbackQuery(query.id, ''));
      return;
    }

    const data = String(query.data || '');
    if (!data.startsWith(CB)) return;

    const [, callId, decision] = data.split(':');
    const entry = this._pending.get(callId);

    if (!entry) {
      await this._safe(() => this._client.answerCallbackQuery(
        query.id, 'Bu istek artik gecerli degil.'
      ));
      return;
    }

    this._pending.delete(callId);
    await this._safe(() => this._client.answerCallbackQuery(query.id, 'Alindi.'));

    const labels = {
      allow: '✅ Izin verildi',
      deny: '⛔ Reddedildi',
      always: '✅ Bu oturumda hep izin verildi',
    };
    await this._safe(() => this._client.editMessageText(
      this._chatId, entry.messageId,
      `${entry.text}\n\n<b>${labels[decision] || decision}</b>`,
      { reply_markup: { inline_keyboard: [] } }
    ));

    entry.resolve(decision);
  }

  async _handleMessage(message) {
    if (!this._authorize(message.from, 'mesaj')) return;

    // Sohbet kimligini ilk yetkili mesajdan ogreniyoruz; ayrica yazmaya gerek yok.
    this._chatId = message.chat.id;

    if (message.voice || message.audio) return this._handleVoice(message);

    const text = String(message.text || '').trim();
    if (!text) return;

    if (text.startsWith('/')) return this._handleCommand(text);
    return this._dispatch(text);
  }

  async _handleVoice(message) {
    const cfg = config.load();
    const voice = message.voice || message.audio;

    if (!stt.isConfigured(cfg)) {
      await this._send(
        '🎤 Sesli mesaj alindi ama <b>ses cozumleme yapilandirilmamis</b>.\n\n'
        + 'Ayarlar > Telegram > "Ses cozumleme adresi" alanina OpenAI uyumlu '
        + 'bir uc girin (ornegin yerel whisper.cpp server). '
        + 'Ollama ses cozumlemez.'
      );
      return;
    }

    await this._send('🎤 Ses cozumleniyor...');
    let text;
    try {
      const { buffer, path } = await this._client.downloadFile(voice.file_id);
      text = await stt.transcribe(buffer, cfg, {
        filename: path.split('/').pop() || 'ses.ogg',
      });
    } catch (err) {
      await this._send(`⚠️ Ses cozumlenemedi: ${escapeHtml(err.message)}`);
      return;
    }

    await this._send(`🗣 <i>${escapeHtml(text)}</i>`);
    return this._dispatch(text);
  }

  // ------------------------------------------------------------------ //
  //  Komutlar
  // ------------------------------------------------------------------ //

  async _handleCommand(raw) {
    const [command, ...rest] = raw.split(/\s+/);
    const argument = rest.join(' ').trim();
    const name = command.split('@')[0].toLowerCase();

    switch (name) {
      case '/start':
      case '/yardim':
      case '/help':
        return this._send(this._helpText());

      case '/durum':
      case '/status':
        return this._sendStatus();

      case '/ekran':
      case '/screen':
        return this._sendScreenshot(argument);

      case '/dur':
      case '/stop':
        return this._send(runner.stop()
          ? '⏹ Durdurma istegi gonderildi.'
          : 'Calisan bir islem yok.');

      case '/sifirla':
      case '/reset':
        runner.resetHistory('telegram');
        return this._send('🧹 Telegram sohbet gecmisi temizlendi.');

      default:
        // Komut degil de dogal dil olabilir ("/tmp klasorunu listele" gibi).
        return this._dispatch(raw);
    }
  }

  _helpText() {
    return [
      `<b>Masaustu Agent</b> — uzaktan kontrol`,
      '',
      'Duz metin veya sesli mesaj gonderin, agent calistirir.',
      '',
      '<b>Komutlar</b>',
      '/durum — agent ve aktif pencere durumu',
      '/ekran [pencere adi] — ekran goruntusu',
      '/dur — calisan islemi durdur',
      '/sifirla — sohbet gecmisini temizle',
      '/yardim — bu mesaj',
      '',
      'Riskli islemlerde buraya onay butonlari gelir.',
    ].join('\n');
  }

  async _sendStatus() {
    const cfg = config.load();
    const busy = runner.isBusy();
    const window = await activeWindow();

    const lines = [
      `<b>Agent:</b> ${busy ? '🟡 calisiyor' : '🟢 bosta'}`,
    ];
    if (busy) lines.push(`<b>Kaynak:</b> ${escapeHtml(runner.currentSource() || '-')}`);

    lines.push(
      `<b>Model:</b> ${escapeHtml(cfg.model)}`,
      `<b>Izin modu:</b> ${escapeHtml(cfg.permissionMode)}`,
      `<b>Calisma alani:</b> <code>${escapeHtml(cfg.workspaceRoot)}</code>`,
      `<b>Sistem:</b> ${escapeHtml(OS_LABEL)}`,
    );

    lines.push(
      '',
      `<b>Aktif pencere:</b> ${window.app
        ? escapeHtml([window.app, window.title].filter(Boolean).join(' — '))
        : '<i>belirlenemedi</i>'}`
    );

    // Son calismanin ozeti — "son state" istegi budur.
    if (this._lastRun) {
      const { at, tools, answer, stopped, error } = this._lastRun;
      lines.push(
        '',
        `<b>Son calisma</b> (${new Date(at).toLocaleTimeString('tr-TR')})`,
        tools.length
          ? `Araclar: ${escapeHtml(tools.join(', '))}`
          : 'Arac kullanilmadi',
      );
      if (error) lines.push(`Hata: ${escapeHtml(error)}`);
      else if (stopped) lines.push('Durum: durduruldu');
      else if (answer) lines.push(`Ozet: ${escapeHtml(answer.slice(0, 300))}`);
    } else {
      lines.push('', '<i>Bu oturumda henuz calisma yok.</i>');
    }

    const history = runner.historyLength('telegram');
    if (history) lines.push('', `<i>Sohbet gecmisi: ${history} mesaj</i>`);

    return this._send(lines.join('\n'));
  }

  async _sendScreenshot(match = '', caption = '') {
    try {
      const shot = await screenshot.capture(
        match ? { type: 'window', match } : { type: 'screen' }
      );
      await this._client.sendPhoto(
        this._chatId,
        shot.buffer,
        caption || `📸 ${escapeHtml(shot.name)}`
      );
    } catch (err) {
      await this._send(`⚠️ Ekran goruntusu alinamadi: ${escapeHtml(err.message)}`);
    }
  }

  // ------------------------------------------------------------------ //
  //  Agent'i calistirma
  // ------------------------------------------------------------------ //

  async _dispatch(text) {
    if (runner.isBusy()) {
      await this._send(
        '⏳ Su anda baska bir islem calisiyor. /dur ile durdurabilirsiniz.'
      );
      return;
    }
    this._lastRun = { at: Date.now(), tools: [], answer: '', stopped: false, error: '' };
    await this._send('▶️ Basladi...');
    // Beklemiyoruz: uzun surebilir, yoklama dongusu bloklanmamali.
    runner.start({ text, source: 'telegram' }).catch((err) => {
      this._send(`⚠️ ${escapeHtml(err.message)}`).catch(() => {});
    });
  }

  // ------------------------------------------------------------------ //
  //  Onay — satir ici butonlar
  // ------------------------------------------------------------------ //

  /**
   * runner'in onay yarisinda Telegram tarafi.
   *
   * `ctx.signal` baska bir kanal (uygulama arayuzu) once cevapladiginda
   * tetiklenir; o durumda buton mesaji "uygulamadan yanitlandi" olarak
   * guncellenir ve butonlar kaldirilir — kullanici artik gecersiz olan bir
   * butona basmaya calismasin diye.
   */
  async _onApproval(req, { signal }) {
    if (!this._started || !this._chatId) return null;

    const text = this._approvalText(req);
    let message;
    try {
      message = await this._client.sendMessage(this._chatId, text, {
        reply_markup: {
          inline_keyboard: [[
            { text: '✅ Izin ver', callback_data: `${CB}${req.callId}:allow` },
            { text: '⛔ Reddet', callback_data: `${CB}${req.callId}:deny` },
          ], [
            { text: '✅ Bu oturumda hep izin ver',
              callback_data: `${CB}${req.callId}:always` },
          ]],
        },
      });
    } catch (err) {
      console.warn('Telegram onay mesaji gonderilemedi:', err.message);
      return null;
    }

    return new Promise((resolve) => {
      let done = false;
      const settle = (value) => {
        if (done) return;
        done = true;
        clearTimeout(timer);
        this._pending.delete(req.callId);
        resolve(value);
      };

      this._pending.set(req.callId, {
        messageId: message.message_id,
        text,
        resolve: settle,
      });

      const timer = setTimeout(() => {
        this._safe(() => this._client.editMessageText(
          this._chatId, message.message_id,
          `${text}\n\n<b>⌛ Zaman asimi — cevap gelmedi.</b>`,
          { reply_markup: { inline_keyboard: [] } }
        ));
        settle(null);
      }, APPROVAL_TIMEOUT_MS);

      signal.addEventListener('abort', () => {
        this._safe(() => this._client.editMessageText(
          this._chatId, message.message_id,
          `${text}\n\n<b>↪️ Uygulama arayuzunden yanitlandi.</b>`,
          { reply_markup: { inline_keyboard: [] } }
        ));
        settle(null);
      }, { once: true });
    });
  }

  _approvalText(req) {
    const tool = registry.get(req.name);
    const lines = [
      '🔐 <b>Onay gerekiyor</b>',
      '',
      `<b>Arac:</b> <code>${escapeHtml(req.name)}</code>`,
      `<b>Risk:</b> ${escapeHtml(RISK_LABEL[req.risk] || req.risk)}`,
    ];
    if (tool?.description) {
      lines.push(`<i>${escapeHtml(tool.description.split('.')[0])}.</i>`);
    }
    if (req.reasons?.length) {
      lines.push('', '<b>Neden soruluyor:</b>');
      for (const reason of req.reasons) lines.push(`• ${escapeHtml(reason)}`);
    }
    if (req.preview) {
      lines.push('', '<b>Yapilacak islem:</b>',
        `<pre>${escapeHtml(String(req.preview).slice(0, 1500))}</pre>`);
    }
    return lines.join('\n');
  }

  // ------------------------------------------------------------------ //
  //  Agent olaylari -> sohbet
  // ------------------------------------------------------------------ //

  _onAgentEvent(event) {
    // Yalnizca Telegram'dan baslatilan calismalari raporlariz; aksi halde
    // kullanici masabasinda calisirken telefonu surekli titrer.
    const mine = runner.currentSource() === 'telegram';

    switch (event.type) {
      case 'tool_start':
        if (this._lastRun && !this._lastRun.tools.includes(event.name)) {
          this._lastRun.tools.push(event.name);
        }
        if (mine && event.risk && event.risk !== 'safe') {
          this._safe(() => this._send(
            `⚙️ <code>${escapeHtml(event.name)}</code> calistiriliyor...`
          ));
        }
        break;

      case 'text_delta':
        // Akis parcalari Telegram'a gonderilmez: saniyede onlarca mesaj
        // olurdu ve API hiz sinirina takilirdi. Nihai cevap tek seferde gider.
        break;

      case 'error':
        if (this._lastRun) this._lastRun.error = event.message;
        if (mine) this._safe(() => this._send(`⚠️ <b>Hata:</b> ${escapeHtml(event.message)}`));
        break;

      case 'run_end':
        if (event.source === 'telegram') this._finishRun(event);
        break;

      default:
        break;
    }
  }

  async _finishRun(event) {
    if (this._lastRun) {
      this._lastRun.stopped = Boolean(event.stopped);
      if (event.error) this._lastRun.error = event.error;
    }

    const cfg = config.load();
    const answer = this._lastAnswerText();
    if (this._lastRun) this._lastRun.answer = answer;

    if (event.stopped && !event.error) {
      await this._safe(() => this._send('⏹ Islem durduruldu.'));
    } else if (answer) {
      await this._safe(() => this._send(`✅ <b>Tamamlandi</b>\n\n${escapeHtml(answer)}`));
    } else if (!event.error) {
      await this._safe(() => this._send('✅ Tamamlandi.'));
    }

    if (cfg.telegram?.sendScreenshots) {
      await this._sendScreenshot('', event.error
        ? '📸 Hata anindaki ekran'
        : '📸 Islem sonrasi ekran');
    }
  }

  /**
   * Nihai cevap.
   *
   * runner gecmisi kaydettikten SONRA `run_end` yayinlar, dolayisiyla burada
   * gecmisteki son asistan mesaji nihai cevaptir. Akis parcalarini biriktirmeye
   * gerek yok.
   */
  _lastAnswerText() {
    return runner.lastAnswer('telegram');
  }

  // ------------------------------------------------------------------ //

  _send(text) {
    if (!this._client || !this._chatId) return Promise.resolve(null);
    return this._client.sendMessage(this._chatId, text);
  }

  /** Telegram hatalari agent akisini bozmamali. */
  async _safe(fn) {
    try {
      return await fn();
    } catch (err) {
      console.warn('Telegram:', err.message);
      return null;
    }
  }
}

module.exports = { TelegramController, CB };
