'use strict';

const path = require('path');
const {
  app, BrowserWindow, Tray, Menu, globalShortcut, nativeImage, shell,
} = require('electron');

const config = require('./config');
const ipc = require('./ipc');
const { buildIcon } = require('./icon');

let mainWindow = null;
let tray = null;
let quitting = false;

const getWindow = () => mainWindow;

function createWindow() {
  const icon = nativeImage.createFromBuffer(buildIcon(64));

  mainWindow = new BrowserWindow({
    width: 1120,
    height: 780,
    minWidth: 820,
    minHeight: 560,
    show: false,
    backgroundColor: '#14161f',
    icon,
    title: 'Masaustu Agent',
    webPreferences: {
      preload: path.join(__dirname, '..', 'preload', 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      spellcheck: false,
    },
  });

  mainWindow.setMenuBarVisibility(false);
  mainWindow.loadFile(path.join(__dirname, '..', 'renderer', 'index.html'));

  mainWindow.once('ready-to-show', () => mainWindow.show());

  // Dis baglantilar varsayilan tarayicida acilsin
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });

  mainWindow.on('close', (e) => {
    if (!quitting && config.load().minimizeToTray) {
      e.preventDefault();
      mainWindow.hide();
    }
  });

  mainWindow.on('closed', () => { mainWindow = null; });
}

function toggleWindow() {
  if (!mainWindow) return createWindow();
  if (mainWindow.isVisible() && mainWindow.isFocused()) {
    mainWindow.hide();
  } else {
    mainWindow.show();
    mainWindow.focus();
  }
}

function createTray() {
  tray = new Tray(nativeImage.createFromBuffer(buildIcon(16)));
  tray.setToolTip('Masaustu Agent');
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: 'Goster / Gizle', click: toggleWindow },
      {
        label: 'Ayarlar',
        click: () => {
          if (!mainWindow) createWindow();
          mainWindow.show();
          mainWindow.webContents.send('ui:openSettings');
        },
      },
      { type: 'separator' },
      { label: 'Cikis', click: () => { quitting = true; app.quit(); } },
    ])
  );
  tray.on('click', toggleWindow);
}

function registerShortcut() {
  const accel = config.load().globalShortcut;
  if (!accel) return;
  try {
    globalShortcut.unregisterAll();
    const ok = globalShortcut.register(accel, toggleWindow);
    if (!ok) console.warn(`Global kisayol kaydedilemedi: ${accel}`);
  } catch (err) {
    console.warn(`Global kisayol hatasi (${accel}):`, err.message);
  }
}

// Tek ornek calissin
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (!mainWindow.isVisible()) mainWindow.show();
      mainWindow.focus();
    }
  });

  app.whenReady().then(() => {
    ipc.register(getWindow);
    createWindow();
    createTray();
    registerShortcut();

    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) createWindow();
    });
  });

  app.on('window-all-closed', () => {
    // Tepside kalmaya devam et; cikis yalnizca tepsi menusunden
    if (!config.load().minimizeToTray) app.quit();
  });

  app.on('before-quit', () => { quitting = true; });
  app.on('will-quit', () => globalShortcut.unregisterAll());
}
