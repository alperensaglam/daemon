'use strict';

const { ipcMain, dialog, shell } = require('electron');
const config = require('./config');
const history = require('./history');
const registry = require('./agent/registry');
const client = require('./agent/client');
const runner = require('./agent/runner');

/** Arayuzden cevap bekleyen onay istekleri: callId -> resolve */
const pendingApprovals = new Map();

function register(getWindow, hooks = {}) {
  const send = (channel, payload) => {
    const win = getWindow();
    if (win && !win.isDestroyed()) win.webContents.send(channel, payload);
  };

  // Kisayol degistiginde yeniden kaydedilmezse kullanici yeni kombinasyonu
  // uygulamayi kapatip acana kadar kullanamaz.
  const applyShortcut = () => hooks.registerShortcut?.();

  // ---- Ayarlar ----
  ipcMain.handle('config:get', () => config.load());
  ipcMain.handle('config:set', (_e, patch) => {
    const before = config.load().globalShortcut;
    const next = config.save(patch || {});
    if (next.globalShortcut !== before) applyShortcut();
    return next;
  });
  ipcMain.handle('config:reset', () => {
    const next = config.reset();
    applyShortcut();
    return next;
  });

  // Global kisayol sessizce kaydedilemeyebilir (baska uygulama tarafindan
  // kapilmis, macOS'ta Ctrl+Space girdi kaynagi degistirir). Renderer bunu
  // ayarlar ekraninda gosterir.
  ipcMain.handle('shortcut:state', () => hooks.getShortcutState?.() ?? null);

  // ---- Uzaktan kontrol (Telegram) ----
  ipcMain.handle('telegram:status', () => {
    const status = hooks.telegram?.status() ?? { running: false };
    // Ortam degiskeni ayarlardaki degeri ezer; kullanici bunu bilmeli, yoksa
    // "token'i degistirdim ama eski token calisiyor" diye tikanir.
    return {
      ...status,
      tokenFromEnv: Boolean(process.env.TELEGRAM_BOT_TOKEN),
      userIdFromEnv: Boolean(process.env.TELEGRAM_USER_ID),
    };
  });

  ipcMain.handle('telegram:apply', async (_e, patch) => {
    config.save({ telegram: patch || {} });
    const next = config.load().telegram;
    if (!hooks.telegram) return { ok: false, error: 'Denetleyici yok.' };

    await hooks.telegram.stop();
    if (!next.enabled) return { ok: true, running: false };

    const result = await hooks.telegram.start();
    return { ...result, running: hooks.telegram.isRunning };
  });

  ipcMain.handle('config:pickFolder', async () => {
    const win = getWindow();
    const res = await dialog.showOpenDialog(win, {
      title: 'Calisma alani klasorunu sec',
      properties: ['openDirectory'],
      defaultPath: config.load().workspaceRoot,
    });
    return res.canceled ? null : res.filePaths[0];
  });

  // ---- Model sunucusu ----
  ipcMain.handle('models:list', async () => {
    const cfg = config.load();
    try {
      const models = await client.listModels(cfg);
      return { ok: true, models };
    } catch (err) {
      return { ok: false, error: err.message };
    }
  });

  // Modeli RAM'de tutar — ilk mesajdaki uzun yukleme beklemesini onler.
  ipcMain.handle('models:warmup', () => client.warmup(config.load()));

  ipcMain.handle('tools:list', () => registry.list());

  // ---- Gecmis ----
  ipcMain.handle('history:list', () => history.list());
  ipcMain.handle('history:get', (_e, id) => history.get(id));
  ipcMain.handle('history:save', (_e, session) => history.save(session));
  ipcMain.handle('history:remove', (_e, id) => { history.remove(id); return history.list(); });
  ipcMain.handle('history:clear', () => { history.clear(); return []; });

  // ---- Kabuk ----
  ipcMain.handle('shell:openExternal', (_e, url) => shell.openExternal(url));
  ipcMain.handle('shell:showItem', (_e, p) => shell.showItemInFolder(p));

  // ---- Agent ----
  // Olaylari runner yayinlar; renderer da Telegram da ayni akisi dinler.
  runner.onEvent((evt) => send('agent:event', evt));

  // Arayuz, onay kanallarindan yalnizca biri. Pencere yoksa `null` doner ve
  // yaris digerlerine (Telegram) birakilir — istek askida kalmaz.
  runner.registerApprover('ui', (req, { signal }) => {
    const win = getWindow();
    if (!win || win.isDestroyed()) return null;

    return new Promise((resolve) => {
      pendingApprovals.set(req.callId, resolve);
      send('agent:approval', req);

      // Baska bir kanal once cevapladiginda arayuzdeki karti kapat.
      signal.addEventListener('abort', () => {
        if (pendingApprovals.delete(req.callId)) {
          send('agent:approvalResolved', { callId: req.callId });
          resolve(null);
        }
      }, { once: true });
    });
  });

  ipcMain.handle('agent:send', (_e, { history: hist, text }) =>
    runner.start({ text, history: hist || [], source: 'ui' })
  );

  ipcMain.handle('agent:approve', (_e, { callId, decision }) => {
    const resolve = pendingApprovals.get(callId);
    if (!resolve) return false;
    pendingApprovals.delete(callId);
    resolve(decision);
    return true;
  });

  ipcMain.handle('agent:stop', () => runner.stop());
}

module.exports = { register };
