'use strict';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('agent', {
  // Ayarlar
  getConfig: () => ipcRenderer.invoke('config:get'),
  setConfig: (patch) => ipcRenderer.invoke('config:set', patch),
  resetConfig: () => ipcRenderer.invoke('config:reset'),
  pickFolder: () => ipcRenderer.invoke('config:pickFolder'),

  // Model sunucusu
  listModels: () => ipcRenderer.invoke('models:list'),
  warmup: () => ipcRenderer.invoke('models:warmup'),
  listTools: () => ipcRenderer.invoke('tools:list'),

  // Gecmis
  historyList: () => ipcRenderer.invoke('history:list'),
  historyGet: (id) => ipcRenderer.invoke('history:get', id),
  historySave: (session) => ipcRenderer.invoke('history:save', session),
  historyRemove: (id) => ipcRenderer.invoke('history:remove', id),
  historyClear: () => ipcRenderer.invoke('history:clear'),

  // Kabuk
  openExternal: (url) => ipcRenderer.invoke('shell:openExternal', url),
  showItem: (p) => ipcRenderer.invoke('shell:showItem', p),

  // Agent
  send: (payload) => ipcRenderer.invoke('agent:send', payload),
  approve: (callId, decision) => ipcRenderer.invoke('agent:approve', { callId, decision }),
  stop: () => ipcRenderer.invoke('agent:stop'),

  // Olaylar
  onEvent: (cb) => {
    const h = (_e, evt) => cb(evt);
    ipcRenderer.on('agent:event', h);
    return () => ipcRenderer.removeListener('agent:event', h);
  },
  onApproval: (cb) => {
    const h = (_e, req) => cb(req);
    ipcRenderer.on('agent:approval', h);
    return () => ipcRenderer.removeListener('agent:approval', h);
  },
  onOpenSettings: (cb) => {
    const h = () => cb();
    ipcRenderer.on('ui:openSettings', h);
    return () => ipcRenderer.removeListener('ui:openSettings', h);
  },
});
