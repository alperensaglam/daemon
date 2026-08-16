'use strict';

/**
 * Uzaktan kontrol — Electron icinde uctan uca entegrasyon testi.
 *
 * Gercek TelegramController'i, gercek runner'i ve gercek config'i kullanir;
 * yalnizca Bot API yerine yerel bir sahte sunucu konur (TELEGRAM_API_ROOT).
 * Boylece asil akis dogrulanir: gelen guncelleme -> yetkilendirme -> komut/
 * gorev -> onay klavyesi -> buton cevabi -> agent'a karar.
 *
 * Calistirma:  npx electron scripts/test-remote-integration.js
 */

const { app } = require('electron');
const http = require('http');
const assert = require('assert');
const path = require('path');

const ROOT = path.join(__dirname, '..');

let passed = 0;
let failed = 0;

async function check(name, fn) {
  try {
    await fn();
    passed += 1;
    console.log(`  ✓ ${name}`);
  } catch (err) {
    failed += 1;
    console.log(`  ✗ ${name}\n      ${err.message}`);
  }
}

/** Bir kosul saglanana kadar bekler (en-iyi-caba istekler icin). */
async function waitFor(predicate, timeoutMs, message) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await new Promise((r) => setTimeout(r, 20));
  }
  throw new Error(message || 'kosul zaman asimina ugradi');
}

function startFakeApi() {
  const calls = [];
  const server = http.createServer((req, res) => {
    let body = '';
    req.on('data', (c) => { body += c; });
    req.on('end', () => {
      const method = req.url.split('/').pop();
      let payload = {};
      try { payload = JSON.parse(body || '{}'); } catch { /* form-data */ }
      calls.push({ method, payload });
      res.setHeader('Content-Type', 'application/json');
      if (method === 'getMe') {
        res.end(JSON.stringify({ ok: true, result: { id: 7, username: 'sahte_bot' } }));
      } else if (method === 'getUpdates') {
        // Yoklama dongusu bos donsun; guncellemeleri elle enjekte ediyoruz.
        setTimeout(() => res.end(JSON.stringify({ ok: true, result: [] })), 50);
      } else {
        calls.length;
        res.end(JSON.stringify({
          ok: true, result: { message_id: calls.length, chat: { id: 42 } },
        }));
      }
    });
  });
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => resolve({
      calls, port: server.address().port,
      sent: (m) => calls.filter((c) => c.method === m),
      close: () => new Promise((r) => server.close(r)),
    }));
  });
}

async function main() {
  console.log('\n=== Uzaktan kontrol entegrasyon testi ===\n');

  const api = await startFakeApi();
  process.env.TELEGRAM_API_ROOT = `http://127.0.0.1:${api.port}`;
  process.env.TELEGRAM_BOT_TOKEN = '42:GIZLI';
  process.env.TELEGRAM_USER_ID = '999';

  const config = require(path.join(ROOT, 'src/main/config'));
  const runner = require(path.join(ROOT, 'src/main/agent/runner'));
  const { TelegramController } = require(path.join(ROOT, 'src/main/remote/controller'));

  config.save({ telegram: { enabled: true, sendScreenshots: false } });

  const controller = new TelegramController();

  console.log('[1] Baslatma');
  await check('getMe ile baglaniyor', async () => {
    const result = await controller.start();
    assert.ok(result.ok, result.error);
    assert.strictEqual(result.botName, '@sahte_bot');
  });

  await check('onaylayici olarak kaydoldu', async () => {
    // Kayit olduysa, onaylayici yokken donen 'deny' artik gelmemeli:
    // Telegram kanali chatId olmadigi icin null doner -> yine deny, ama
    // bu kez kanal SAYISI 0 degil. Dogrudan kanal sayisini kontrol edelim.
    const decision = await runner.requestApproval({ callId: 'x', name: 'y' });
    assert.strictEqual(decision, 'deny', 'chatId yokken deny beklenir');
  });

  console.log('\n[2] Yetkilendirme');
  await check('yetkisiz mesaj hicbir cevap uretmiyor', async () => {
    const before = api.sent('sendMessage').length;
    await controller._handleUpdate({
      message: { chat: { id: 5 }, from: { id: 1234 }, text: '/durum' },
    });
    assert.strictEqual(api.sent('sendMessage').length, before,
      'yetkisiz kullaniciya cevap gitti — botun varligini dogrular');
  });

  await check('yetkisiz buton onay veremiyor', async () => {
    let resolved = null;
    controller._chatId = 42;
    controller._pending.set('kritik', {
      messageId: 1, text: 'test', resolve: (d) => { resolved = d; },
    });
    await controller._handleUpdate({
      callback_query: { id: 'q1', from: { id: 1234 }, data: 'ap:kritik:allow' },
    });
    assert.strictEqual(resolved, null,
      'YABANCI BIRI ONAY VERDI — kritik guvenlik hatasi');
    controller._pending.delete('kritik');
  });

  console.log('\n[3] Komutlar');
  await check('/durum yanit uretiyor', async () => {
    const before = api.sent('sendMessage').length;
    await controller._handleUpdate({
      message: { chat: { id: 42 }, from: { id: 999 }, text: '/durum' },
    });
    const sent = api.sent('sendMessage');
    assert.ok(sent.length > before, '/durum cevap vermedi');
    const text = sent[sent.length - 1].payload.text;
    assert.ok(text.includes('Agent:'), 'durum ozeti yok');
    assert.ok(text.includes('Aktif pencere'), 'aktif pencere satiri yok');
    assert.ok(text.includes('Model'), 'model bilgisi yok');
  });

  await check('/yardim komut listesi doner', async () => {
    await controller._handleUpdate({
      message: { chat: { id: 42 }, from: { id: 999 }, text: '/yardim' },
    });
    const text = api.sent('sendMessage').pop().payload.text;
    assert.ok(text.includes('/durum') && text.includes('/ekran'));
  });

  await check('sesli mesaj yapilandirilmamissa acikca soyluyor', async () => {
    await controller._handleUpdate({
      message: {
        chat: { id: 42 }, from: { id: 999 },
        voice: { file_id: 'f1', duration: 2 },
      },
    });
    const text = api.sent('sendMessage').pop().payload.text;
    assert.ok(text.includes('cozumleme'), 'sessizce yok sayildi');
    assert.ok(text.includes('Ollama'), 'Ollama uyarisi yok');
  });

  console.log('\n[4] Onay klavyesi');
  await check('onay istegi inline keyboard ile gidiyor', async () => {
    controller._chatId = 42;
    const promise = controller._onApproval(
      { callId: 'c100', name: 'run_command', risk: 'danger',
        reasons: ['komut zorla silme'], preview: 'rm -rf /tmp/x' },
      { signal: new AbortController().signal }
    );
    await new Promise((r) => setTimeout(r, 60));

    const sent = api.sent('sendMessage').pop();
    const keyboard = sent.payload.reply_markup?.inline_keyboard;
    assert.ok(keyboard, 'inline keyboard yok');
    const buttons = keyboard.flat();
    assert.strictEqual(buttons.length, 3, '3 buton bekleniyordu');
    assert.ok(buttons.some((b) => b.callback_data === 'ap:c100:allow'));
    assert.ok(buttons.some((b) => b.callback_data === 'ap:c100:deny'));
    assert.ok(buttons.some((b) => b.callback_data === 'ap:c100:always'));
    assert.ok(sent.payload.text.includes('rm -rf /tmp/x'), 'onizleme yok');

    // Yetkili buton basimi karari dondurmeli.
    await controller._handleUpdate({
      callback_query: { id: 'q2', from: { id: 999 }, data: 'ap:c100:allow' },
    });
    assert.strictEqual(await promise, 'allow', 'buton karari gelmedi');
  });

  await check('buton basiminca mesaj guncelleniyor', async () => {
    const edit = api.sent('editMessageText').pop();
    assert.ok(edit, 'editMessageText cagrilmadi');
    assert.ok(edit.payload.text.includes('Izin verildi'));
    assert.deepStrictEqual(edit.payload.reply_markup.inline_keyboard, [],
      'butonlar kaldirilmadi — kullanici tekrar basabilir');
  });

  await check('arayuz once cevaplarsa Telegram karti kapaniyor', async () => {
    const before = api.sent('editMessageText').length;
    const ac = new AbortController();
    const promise = controller._onApproval(
      { callId: 'c200', name: 'write_file', risk: 'write' },
      { signal: ac.signal }
    );
    await new Promise((r) => setTimeout(r, 60));
    ac.abort();                                  // arayuz kazandi

    // Karar HEMEN donmeli: mesaj guncellemesi en-iyi-caba olduğu icin
    // beklenmez, yoksa Telegram yavaslarsa agent dongusu takilirdi.
    assert.strictEqual(await promise, null, 'iptal sonrasi null donmeli');

    // Guncelleme arkadan gelir; testin onu beklemesi gerekir.
    await waitFor(() => api.sent('editMessageText').length > before, 2000,
      'editMessageText gonderilmedi');
    const edit = api.sent('editMessageText').pop();
    assert.ok(edit.payload.text.includes('arayuzunden yanitlandi'),
      `beklenmeyen metin: ${edit.payload.text.slice(-60)}`);
    assert.deepStrictEqual(edit.payload.reply_markup.inline_keyboard, [],
      'gecersiz kalan butonlar kaldirilmadi');
  });

  console.log('\n[5] Kapanis');
  await check('stop() yoklamayi ve kayitlari birakiyor', async () => {
    await controller.stop();
    assert.strictEqual(controller.isRunning, false);
    assert.strictEqual(controller._pending.size, 0);
  });

  await api.close();
  console.log(`\n=== ${passed} gecti, ${failed} kaldi ===\n`);
  app.exit(failed === 0 ? 0 : 1);
}

app.whenReady().then(main).catch((err) => {
  console.error('Kosucu hatasi:', err);
  app.exit(1);
});
