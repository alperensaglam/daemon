#!/usr/bin/env node
'use strict';

/**
 * Uzaktan kontrol katmani icin uctan uca dogrulama.
 *
 * Gercek Telegram'a baglanmaz: yerel bir **sahte Bot API sunucusu** kurar ve
 * TelegramClient'i ona yonlendirir. Boylece token, ag ve gercek bir bot
 * olmadan guvenlik acisindan kritik yollar kosulabilir:
 *
 *   1. Yetkisiz kullanicinin mesaji ve BUTONU reddediliyor mu
 *   2. Onay yarisinda ilk cevap kazaniyor, kaybedene iptal gidiyor mu
 *   3. Hic onaylayici yokken fail-closed (deny) mi
 *   4. Token hata metinlerine sizmiyor mu
 *   5. Uzun mesaj Telegram sinirina gore boluniyor mu
 *
 * Calistirma:  node scripts/test-remote.js
 */

const http = require('http');
const path = require('path');
const assert = require('assert');

const ROOT = path.join(__dirname, '..');
const { TelegramClient, splitMessage, escapeHtml } =
  require(path.join(ROOT, 'src/main/remote/telegram'));

const TOKEN = '123456:SAHTE-TOKEN-SIZMAMALI';

let passed = 0;
let failed = 0;

function check(name, fn) {
  try {
    fn();
    passed += 1;
    console.log(`  ✓ ${name}`);
  } catch (err) {
    failed += 1;
    console.log(`  ✗ ${name}\n      ${err.message}`);
  }
}

async function checkAsync(name, fn) {
  try {
    await fn();
    passed += 1;
    console.log(`  ✓ ${name}`);
  } catch (err) {
    failed += 1;
    console.log(`  ✗ ${name}\n      ${err.message}`);
  }
}

/** Sahte Bot API. Cagrilari kaydeder, istenirse hata dondurur. */
function startFakeApi() {
  const calls = [];
  let failNext = null;

  const server = http.createServer((req, res) => {
    let body = '';
    req.on('data', (c) => { body += c; });
    req.on('end', () => {
      const method = req.url.split('/').pop();
      let payload = {};
      try { payload = JSON.parse(body || '{}'); } catch { /* form-data */ }
      calls.push({ method, payload, url: req.url });

      res.setHeader('Content-Type', 'application/json');
      if (failNext) {
        const description = failNext;
        failNext = null;
        res.end(JSON.stringify({ ok: false, error_code: 400, description }));
        return;
      }
      if (method === 'getMe') {
        res.end(JSON.stringify({ ok: true, result: { id: 1, username: 'test_bot' } }));
        return;
      }
      res.end(JSON.stringify({
        ok: true,
        result: { message_id: calls.length, chat: { id: 42 } },
      }));
    });
  });

  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => {
      resolve({
        server,
        calls,
        port: server.address().port,
        failWith: (d) => { failNext = d; },
        close: () => new Promise((r) => server.close(r)),
      });
    });
  });
}

async function main() {
  console.log('\n=== Uzaktan kontrol dogrulamasi ===\n');

  // ---------------------------------------------------------------- //
  console.log('[1] Mesaj bolme ve HTML kacisi');

  check('4096 karakter siniri asilmiyor', () => {
    const parts = splitMessage('x'.repeat(10_000), 4096);
    assert.ok(parts.every((p) => p.length <= 4096), 'parca siniri asti');
    assert.strictEqual(parts.join('').length, 10_000);
  });

  check('satir sonundan bolmeyi tercih ediyor', () => {
    const text = ('satir\n'.repeat(2000));
    const parts = splitMessage(text, 100);
    assert.ok(parts.length > 1);
    assert.ok(parts[0].endsWith('satir'), 'satir ortasindan kesti');
  });

  check('bos metin bos mesaj uretmiyor', () => {
    assert.deepStrictEqual(splitMessage('', 4096), ['(bos)']);
  });

  check('HTML kacisi enjeksiyonu engelliyor', () => {
    const out = escapeHtml('<b>x</b> & <script>');
    assert.ok(!out.includes('<script>'), 'script etiketi kacmadi');
    assert.strictEqual(out, '&lt;b&gt;x&lt;/b&gt; &amp; &lt;script&gt;');
  });

  // ---------------------------------------------------------------- //
  console.log('\n[2] Token sizintisi');

  const api = await startFakeApi();
  const client = new TelegramClient(TOKEN, {
    apiRoot: `http://127.0.0.1:${api.port}`,
  });

  check('scrub token\'i temizliyor', () => {
    const dirty = `https://api.telegram.org/bot${TOKEN}/sendMessage patladi`;
    const clean = client.scrub(dirty);
    assert.ok(!clean.includes(TOKEN), 'token metinde kaldi');
    assert.ok(clean.includes('<TOKEN>'));
  });

  await checkAsync('API hatasi token icermiyor', async () => {
    api.failWith('Bad Request: chat not found');
    let message = '';
    try {
      await client.sendMessage(42, 'merhaba');
    } catch (err) {
      message = err.message;
    }
    assert.ok(message, 'hata firlatilmadi');
    assert.ok(!message.includes(TOKEN), `token sizdi: ${message}`);
    assert.ok(message.includes('chat not found'), 'asil sebep kayboldu');
  });

  await checkAsync('ag hatasi token icermiyor', async () => {
    const broken = new TelegramClient(TOKEN, {
      apiRoot: 'http://127.0.0.1:1',        // kapali port
    });
    let message = '';
    try {
      await broken.getMe();
    } catch (err) {
      message = err.message;
    }
    assert.ok(message, 'hata firlatilmadi');
    assert.ok(!message.includes(TOKEN), `token sizdi: ${message}`);
  });

  // ---------------------------------------------------------------- //
  console.log('\n[3] Onay yarisi (runner)');

  // Electron'suz calisabilmesi icin config/loop sahte.
  const Module = require('module');
  const originalResolve = Module._resolveFilename;
  const stubs = {
    electron: { app: { getPath: () => '/tmp' } },
  };
  Module._resolveFilename = function (request, ...rest) {
    if (stubs[request]) return request;
    return originalResolve.call(this, request, ...rest);
  };
  require.cache.electron = { id: 'electron', exports: stubs.electron, loaded: true };

  const runner = require(path.join(ROOT, 'src/main/agent/runner'));

  await checkAsync('onaylayici yoksa fail-closed (deny)', async () => {
    const decision = await runner.requestApproval({ callId: 'c0', name: 'x' });
    assert.strictEqual(decision, 'deny',
      'onaylayici yokken islem sessizce gecmemeli');
  });

  await checkAsync('ilk cevap kazaniyor, kaybedene iptal gidiyor', async () => {
    let loserAborted = false;

    const offFast = runner.registerApprover('hizli', async () => {
      await new Promise((r) => setTimeout(r, 10));
      return 'allow';
    });
    const offSlow = runner.registerApprover('yavas', (req, { signal }) =>
      new Promise((resolve) => {
        signal.addEventListener('abort', () => {
          loserAborted = true;
          resolve(null);
        }, { once: true });
        setTimeout(() => resolve('deny'), 5000);
      })
    );

    const decision = await runner.requestApproval({ callId: 'c1', name: 'x' });
    assert.strictEqual(decision, 'allow', 'hizli kanalin cevabi kazanmali');
    await new Promise((r) => setImmediate(r));
    assert.ok(loserAborted, 'kaybeden kanala iptal sinyali gitmedi');

    offFast(); offSlow();
  });

  await checkAsync('cevap veremeyen kanal yarisi bozmuyor', async () => {
    const offNull = runner.registerApprover('kapali', () => null);
    const offReal = runner.registerApprover('acik', async () => {
      await new Promise((r) => setTimeout(r, 5));
      return 'deny';
    });
    const decision = await runner.requestApproval({ callId: 'c2', name: 'x' });
    assert.strictEqual(decision, 'deny');
    offNull(); offReal();
  });

  await checkAsync('tum kanallar cevap veremezse deny', async () => {
    const off = runner.registerApprover('kapali', () => null);
    const decision = await runner.requestApproval({ callId: 'c3', name: 'x' });
    assert.strictEqual(decision, 'deny');
    off();
  });

  await checkAsync('hata veren kanal digerini engellemiyor', async () => {
    const offBad = runner.registerApprover('bozuk', () => {
      throw new Error('patladim');
    });
    const offGood = runner.registerApprover('saglam', async () => 'allow');
    const decision = await runner.requestApproval({ callId: 'c4', name: 'x' });
    assert.strictEqual(decision, 'allow');
    offBad(); offGood();
  });

  // ---------------------------------------------------------------- //
  console.log('\n[4] Yetkilendirme filtresi');

  const { TelegramController } = require(path.join(ROOT, 'src/main/remote/controller'));
  const controller = new TelegramController();
  controller._userId = '999';

  const warnings = [];
  const originalWarn = console.warn;
  console.warn = (...args) => warnings.push(args.join(' '));

  check('dogru ID kabul ediliyor', () => {
    assert.strictEqual(controller._authorize({ id: 999 }, 'mesaj'), true);
  });

  check('yanlis ID reddediliyor', () => {
    assert.strictEqual(controller._authorize({ id: 1234 }, 'mesaj'), false);
  });

  check('ID yoksa reddediliyor', () => {
    assert.strictEqual(controller._authorize({}, 'mesaj'), false);
    assert.strictEqual(controller._authorize(undefined, 'mesaj'), false);
  });

  check('string/number ID ayrimi sorun cikarmiyor', () => {
    assert.strictEqual(controller._authorize({ id: '999' }, 'mesaj'), true);
  });

  check('yetkisiz erisim loglaniyor', () => {
    assert.ok(warnings.some((w) => w.includes('yetkisiz')),
      'yetkisiz deneme loglanmadi');
  });
  console.warn = originalWarn;

  check('buton (callback_query) da yetkilendiriliyor', () => {
    // Kod yolunu okuyarak dogrula: _handleCallback ilk isi _authorize olmali.
    const source = controller._handleCallback.toString();
    const authIndex = source.indexOf('_authorize');
    const dataIndex = source.indexOf('this._pending');
    assert.ok(authIndex > -1, '_handleCallback yetkilendirme yapmiyor');
    assert.ok(authIndex < dataIndex,
      'yetkilendirme, istegi islemeden ONCE yapilmali');
  });

  // ---------------------------------------------------------------- //
  console.log('\n[5] Onay metni');

  check('onay metni riskli komutu gosteriyor', () => {
    const text = controller._approvalText({
      callId: 'c9',
      name: 'run_command',
      risk: 'danger',
      reasons: ['komut zorla/ozyinelemeli silme'],
      preview: 'rm -rf /tmp/deneme',
    });
    assert.ok(text.includes('run_command'));
    assert.ok(text.includes('riskli'));
    assert.ok(text.includes('rm -rf /tmp/deneme'));
    assert.ok(text.includes('zorla/ozyinelemeli silme'));
  });

  check('onay metni HTML enjeksiyonuna kapali', () => {
    const text = controller._approvalText({
      callId: 'c10', name: 'x', risk: 'write',
      preview: '<script>alert(1)</script>',
    });
    assert.ok(!text.includes('<script>'), 'onizleme kacilmadi');
  });

  await api.close();
  Module._resolveFilename = originalResolve;

  console.log(`\n=== ${passed} gecti, ${failed} kaldi ===\n`);
  return failed === 0 ? 0 : 1;
}

main().then((code) => process.exit(code)).catch((err) => {
  console.error('Test kosucusu hatasi:', err);
  process.exit(1);
});
