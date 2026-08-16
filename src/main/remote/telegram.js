'use strict';

/**
 * Telegram Bot API istemcisi — bagimliliksiz, long-polling tabanli.
 *
 * Neden hazir kutuphane yok: Bot API duz HTTPS + JSON. Electron'da global
 * `fetch` var ve proje zaten Ollama'ya boyle konusuyor (agent/client.js). Bir
 * paket eklemek 40+ bagimlilik getirirdi.
 *
 * Long-polling secildigi icin **port acmak, webhook kurmak veya makineyi
 * internete acmak gerekmez**; baglantiyi daima bu taraf baslatir.
 *
 * GUVENLIK — token URL'in icinde gider:
 *     https://api.telegram.org/bot<TOKEN>/sendMessage
 * Dolayisiyla herhangi bir hata mesaji, stack trace veya log satiri URL'i
 * icerirse token sizar. `scrub()` her disari cikan metinden token'i siler ve
 * bu sinifin firlattigi TUM hatalar oradan gecer.
 */

const API_ROOT = 'https://api.telegram.org';

/** Uzun yoklama bekleme suresi (sn). Telegram 50'ye kadar izin verir. */
const POLL_TIMEOUT_S = 50;

/** Telegram tek mesajda 4096 karakter kabul eder. */
const MAX_MESSAGE_CHARS = 4096;

class TelegramError extends Error {}

/** HTML parse_mode icin kacis. MarkdownV2'ye gore cok daha az tuzagi var. */
function escapeHtml(text) {
  return String(text ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

class TelegramClient {
  constructor(token, { fetchImpl, apiRoot } = {}) {
    if (!token) throw new TelegramError('Telegram bot token bos.');
    this._token = String(token);
    this._fetch = fetchImpl || globalThis.fetch;
    this._apiRoot = apiRoot || API_ROOT;
    this._offset = 0;
    this._polling = false;
    this._pollController = null;
  }

  /** Token'i her turlu metinden siler. Disari cikan her sey buradan gecer. */
  scrub(text) {
    return String(text ?? '').split(this._token).join('<TOKEN>');
  }

  _url(method) {
    return `${this._apiRoot}/bot${this._token}/${method}`;
  }

  async call(method, payload = {}, { signal } = {}) {
    let response;
    try {
      response = await this._fetch(this._url(method), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        signal,
      });
    } catch (err) {
      if (err.name === 'AbortError') throw err;
      throw new TelegramError(this.scrub(`${method}: ${err.message}`));
    }

    let body;
    const raw = await response.text();
    try {
      body = JSON.parse(raw);
    } catch {
      throw new TelegramError(
        this.scrub(`${method}: gecersiz yanit (HTTP ${response.status})`)
      );
    }

    if (!body.ok) {
      throw new TelegramError(
        this.scrub(`${method}: ${body.description || 'bilinmeyen hata'} `
          + `(kod ${body.error_code ?? '?'})`)
      );
    }
    return body.result;
  }

  /** Token gecerli mi ve bot kim? */
  getMe(opts) {
    return this.call('getMe', {}, opts);
  }

  async sendMessage(chatId, text, extra = {}) {
    // Uzun ciktilarda Telegram 400 doner; boluyoruz.
    const chunks = splitMessage(text, MAX_MESSAGE_CHARS);
    let last = null;
    for (const chunk of chunks) {
      last = await this.call('sendMessage', {
        chat_id: chatId,
        text: chunk,
        parse_mode: 'HTML',
        disable_web_page_preview: true,
        ...extra,
      });
    }
    return last;
  }

  editMessageText(chatId, messageId, text, extra = {}) {
    return this.call('editMessageText', {
      chat_id: chatId,
      message_id: messageId,
      text: text.slice(0, MAX_MESSAGE_CHARS),
      parse_mode: 'HTML',
      disable_web_page_preview: true,
      ...extra,
    });
  }

  answerCallbackQuery(callbackQueryId, text = '') {
    return this.call('answerCallbackQuery', {
      callback_query_id: callbackQueryId,
      text: text.slice(0, 200),
    });
  }

  /** Fotograf gonderir. `photo` bir Buffer olmalidir. */
  async sendPhoto(chatId, photo, caption = '', { signal } = {}) {
    const form = new FormData();
    form.append('chat_id', String(chatId));
    if (caption) {
      form.append('caption', caption.slice(0, 1024));
      form.append('parse_mode', 'HTML');
    }
    form.append(
      'photo',
      new Blob([photo], { type: 'image/jpeg' }),
      'ekran.jpg'
    );

    let response;
    try {
      response = await this._fetch(this._url('sendPhoto'), {
        method: 'POST', body: form, signal,
      });
    } catch (err) {
      if (err.name === 'AbortError') throw err;
      throw new TelegramError(this.scrub(`sendPhoto: ${err.message}`));
    }
    const body = await response.json().catch(() => ({}));
    if (!body.ok) {
      throw new TelegramError(
        this.scrub(`sendPhoto: ${body.description || `HTTP ${response.status}`}`)
      );
    }
    return body.result;
  }

  /** Bir dosyayi (ornegin sesli mesaji) indirir. */
  async downloadFile(fileId, { signal } = {}) {
    const file = await this.call('getFile', { file_id: fileId }, { signal });
    if (!file?.file_path) {
      throw new TelegramError('Dosya yolu alinamadi.');
    }
    const url = `${this._apiRoot}/file/bot${this._token}/${file.file_path}`;
    let response;
    try {
      response = await this._fetch(url, { signal });
    } catch (err) {
      if (err.name === 'AbortError') throw err;
      throw new TelegramError(this.scrub(`dosya indirme: ${err.message}`));
    }
    if (!response.ok) {
      throw new TelegramError(`dosya indirme: HTTP ${response.status}`);
    }
    return {
      buffer: Buffer.from(await response.arrayBuffer()),
      path: file.file_path,
    };
  }

  /**
   * Uzun yoklama dongusu. `onUpdate` her guncelleme icin cagrilir.
   *
   * Hata durumunda ustel geri cekilme uygulanir: ag koptugunda saniyede bir
   * istek atip hem Telegram'i hem log'u bogmak yerine bekleme suresi artar.
   */
  async startPolling(onUpdate, { onError } = {}) {
    if (this._polling) return;
    this._polling = true;
    this._pollController = new AbortController();
    const { signal } = this._pollController;

    let backoffMs = 0;

    while (this._polling) {
      try {
        const updates = await this.call('getUpdates', {
          offset: this._offset,
          timeout: POLL_TIMEOUT_S,
          allowed_updates: ['message', 'callback_query'],
        }, { signal });

        backoffMs = 0;

        for (const update of updates) {
          this._offset = update.update_id + 1;
          try {
            await onUpdate(update);
          } catch (err) {
            onError?.(new TelegramError(this.scrub(
              `guncelleme islenemedi: ${err.message}`
            )));
          }
        }
      } catch (err) {
        if (signal.aborted || err.name === 'AbortError') break;
        onError?.(err instanceof TelegramError
          ? err
          : new TelegramError(this.scrub(err.message)));
        backoffMs = Math.min(backoffMs ? backoffMs * 2 : 1000, 60_000);
        await sleep(backoffMs, signal);
      }
    }
    this._polling = false;
  }

  stopPolling() {
    this._polling = false;
    this._pollController?.abort();
  }

  get isPolling() {
    return this._polling;
  }
}

function sleep(ms, signal) {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener('abort', () => { clearTimeout(timer); resolve(); },
      { once: true });
  });
}

/** Uzun metni Telegram sinirina gore boler; mumkunse satir sonundan. */
function splitMessage(text, limit) {
  const s = String(text ?? '');
  if (s.length <= limit) return [s || '(bos)'];

  const parts = [];
  let rest = s;
  while (rest.length > limit) {
    let cut = rest.lastIndexOf('\n', limit);
    if (cut < limit * 0.5) cut = limit;   // uygun satir sonu yoksa sert kes
    parts.push(rest.slice(0, cut));
    rest = rest.slice(cut).replace(/^\n/, '');
  }
  if (rest) parts.push(rest);
  return parts;
}

module.exports = { TelegramClient, TelegramError, escapeHtml, splitMessage };
