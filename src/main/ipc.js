'use strict';

const { ipcMain, dialog, shell } = require('electron');
const config = require('./config');
const history = require('./history');
const registry = require('./agent/registry');
const client = require('./agent/client');
const loop = require('./agent/loop');

/** Aktif calisma durumu (ayni anda tek tur calisir). */
let current = null; // { controller, pendingApprovals: Map<callId, {resolve}> }

function register(getWindow) {
  const send = (channel, payload) => {
    const win = getWindow();
    if (win && !win.isDestroyed()) win.webContents.send(channel, payload);
  };

  // ---- Ayarlar ----
  ipcMain.handle('config:get', () => config.load());
  ipcMain.handle('config:set', (_e, patch) => config.save(patch || {}));
  ipcMain.handle('config:reset', () => config.reset());

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
  ipcMain.handle('agent:send', async (_e, { history: hist, text }) => {
    if (current) return { ok: false, error: 'Zaten calisan bir islem var.' };

    const controller = new AbortController();
    const pendingApprovals = new Map();
    current = { controller, pendingApprovals };

    try {
      const result = await loop.run({
        config: config.load(),
        history: hist || [],
        userText: text,
        signal: controller.signal,
        emit: (evt) => send('agent:event', evt),
        requestApproval: (req) =>
          new Promise((resolve) => {
            if (controller.signal.aborted) return resolve('abort');
            pendingApprovals.set(req.callId, resolve);
            send('agent:approval', req);
            controller.signal.addEventListener(
              'abort',
              () => {
                if (pendingApprovals.has(req.callId)) {
                  pendingApprovals.delete(req.callId);
                  resolve('abort');
                }
              },
              { once: true }
            );
          }),
      });
      return { ok: true, ...result };
    } catch (err) {
      send('agent:event', { type: 'error', message: err.message });
      send('agent:event', { type: 'done' });
      return { ok: false, error: err.message };
    } finally {
      current = null;
    }
  });

  ipcMain.handle('agent:approve', (_e, { callId, decision }) => {
    const resolve = current?.pendingApprovals.get(callId);
    if (!resolve) return false;
    current.pendingApprovals.delete(callId);
    resolve(decision);
    return true;
  });

  ipcMain.handle('agent:stop', () => {
    if (!current) return false;
    current.controller.abort();
    return true;
  });
}

module.exports = { register };
