'use strict';

const path = require('path');
const {
  app, BrowserWindow, Tray, Menu, globalShortcut, nativeImage, shell,
} = require('electron');

const config = require('./config');
const ipc = require('./ipc');
const { buildIcon, buildTemplateIcon } = require('./icon');
const { IS_MAC } = require('./platform');
const { TelegramController } = require('./remote/controller');

const telegram = new TelegramController();

let mainWindow = null;
let tray = null;
let quitting = false;
let shortcutState = { accel: '', ok: false };

const getWindow = () => mainWindow;

function createWindow() {
  const icon = nativeImage.createFromBuffer(buildIcon(64));

  mainWindow = new BrowserWindow({
    width: 1120,
    height: 780,
    minWidth: 820,
    minHeight: 560,
    show: false,
    // styles.css'teki --bg ile ayni olmali: pencere ilk karede bu rengi
    // boyar, farkli olursa acilista bir kare eski renk yanip soner.
    backgroundColor: '#0B0F19',
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

  // macOS'ta menu sistem menu cubugundadir; setMenuBarVisibility orada
  // etkisizdir ve uygulama menusu createMenu() ile ayrica kurulur.
  if (!IS_MAC) mainWindow.setMenuBarVisibility(false);
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

function openSettings() {
  if (!mainWindow) createWindow();
  mainWindow.show();
  mainWindow.webContents.send('ui:openSettings');
}

/**
 * Uygulama menusu.
 *
 * macOS'ta menu kurmak zorunludur: menu yoksa Electron'un "Electron" adli
 * varsayilan menusu gelir ve renderer'daki Cmd+C/V/A yalnizca o varsayilanin
 * yan etkisi olarak calisir. Kendi menumuzu kurunca duzenleme rollerini de
 * kendimiz saglamak zorundayiz.
 */
function createMenu() {
  if (!IS_MAC) {
    Menu.setApplicationMenu(null);
    return;
  }
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    {
      role: 'appMenu',
      submenu: [
        { role: 'about', label: 'Masaustu Agent Hakkinda' },
        { type: 'separator' },
        { label: 'Ayarlar...', accelerator: 'Command+,', click: openSettings },
        { type: 'separator' },
        { role: 'hide', label: 'Masaustu Agent’i Gizle' },
        { role: 'hideOthers', label: 'Digerlerini Gizle' },
        { role: 'unhide', label: 'Tumunu Goster' },
        { type: 'separator' },
        { role: 'quit', label: 'Masaustu Agent’ten Cik' },
      ],
    },
    {
      label: 'Duzenle',
      submenu: [
        { role: 'undo', label: 'Geri Al' },
        { role: 'redo', label: 'Yinele' },
        { type: 'separator' },
        { role: 'cut', label: 'Kes' },
        { role: 'copy', label: 'Kopyala' },
        { role: 'paste', label: 'Yapistir' },
        { role: 'selectAll', label: 'Tumunu Sec' },
      ],
    },
    {
      label: 'Pencere',
      submenu: [
        { role: 'minimize', label: 'Simge Durumuna Kucult' },
        { role: 'zoom', label: 'Yakinlastir' },
        { type: 'separator' },
        { role: 'front', label: 'Tumunu One Getir' },
      ],
    },
  ]));
}

function createTray() {
  // macOS menu cubugu Retina'da 2x ister ve simgeler "template" olmalidir:
  // aksi halde koyu mavi daire acik temali menu cubugunda yanlis gorunur ve
  // sistem onu temaya gore yeniden renklendiremez.
  const icon = IS_MAC
    ? nativeImage.createFromBuffer(buildTemplateIcon(32), { scaleFactor: 2 })
    : nativeImage.createFromBuffer(buildIcon(16));
  if (IS_MAC) icon.setTemplateImage(true);

  tray = new Tray(icon);
  tray.setToolTip('Masaustu Agent');
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: 'Goster / Gizle', click: toggleWindow },
      { label: 'Ayarlar', click: openSettings },
      { type: 'separator' },
      { label: 'Cikis', click: () => { quitting = true; app.quit(); } },
    ])
  );

  // macOS'ta setContextMenu varken sol tik da menuyu acar; ayrica click
  // dinleyicisi baglamak pencereyi menuyle birlikte acip kapatir.
  if (!IS_MAC) tray.on('click', toggleWindow);
}

function registerShortcut() {
  const accel = config.load().globalShortcut;
  shortcutState = { accel: accel || '', ok: false };
  if (!accel) return shortcutState;
  try {
    globalShortcut.unregisterAll();
    shortcutState.ok = globalShortcut.register(accel, toggleWindow);
    if (!shortcutState.ok) console.warn(`Global kisayol kaydedilemedi: ${accel}`);
  } catch (err) {
    console.warn(`Global kisayol hatasi (${accel}):`, err.message);
    shortcutState.error = err.message;
  }
  // Kayit sessizce basarisiz olabiliyor (kisayol baska uygulamada, macOS'ta
  // Ctrl+Space girdi kaynagi degistirmede). Kullanici bunu ayarlarda gormeli.
  if (mainWindow) mainWindow.webContents.send('ui:shortcutState', shortcutState);
  return shortcutState;
}

const getShortcutState = () => shortcutState;

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
    ipc.register(getWindow, { registerShortcut, getShortcutState, telegram });
    createMenu();
    createWindow();
    createTray();
    registerShortcut();

    // Uzaktan kontrol ayarlarda aciksa baslat. Basarisizlik uygulamayi
    // durdurmaz; sebep ayarlar ekraninda gosterilir.
    if (config.load().telegram?.enabled) {
      telegram.start().then((result) => {
        if (!result.ok) console.warn('Telegram baslatilamadi:', result.error);
      });
    }

    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) createWindow();
    });
  });

  app.on('window-all-closed', () => {
    // Tepside kalmaya devam et; cikis yalnizca tepsi menusunden.
    // macOS'ta uygulamalar son pencere kapaninca da yasamaya devam eder.
    if (!config.load().minimizeToTray && !IS_MAC) app.quit();
  });

  app.on('before-quit', () => { quitting = true; telegram.stop(); });
  app.on('will-quit', () => globalShortcut.unregisterAll());
}
