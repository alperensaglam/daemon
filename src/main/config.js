'use strict';

const fs = require('fs');
const path = require('path');
const os = require('os');
const { app } = require('electron');
const { IS_WIN } = require('./platform');

const CONFIG_FILE = () => path.join(app.getPath('userData'), 'config.json');

const buildSystemPrompt = (osName) => `Sen kullanicinin ${osName} bilgisayarinda calisan bir masaustu asistanisin.

Gorevin: kullanicinin verdigi isi ARACLARI KULLANARAK gercekten yapmak. Sadece nasil yapilacagini anlatma, yap.

Kurallar:
- Dosya/klasor hakkinda bir sey soruldugunda tahmin etme; list_dir veya read_file ile bak.
- Guncel bilgi gerektiginde (hava durumu, haber, fiyat) web_search kullan.
- Bir araci cagirmadan once ne yapacagini tek cumleyle soyle.
- Araclar hata dondurebilir. Hata alirsan nedenini oku ve farkli bir yolla dene, ayni cagriyi tekrarlama.
- Is bittiginde kisa ve net bir ozet ver.
- Kullanici Turkce yaziyorsa Turkce cevap ver.
- KISA yaz. Yerel modeller yavas calistigi icin her fazladan cumle beklemeye mal olur.
  Sorulani cevapla, gereksiz giris/tekrar/aciklama ekleme.`;

const DEFAULT_SYSTEM_PROMPT = buildSystemPrompt(IS_WIN ? 'Windows' : 'macOS');

/**
 * Daha once varsayilan olarak diske yazilmis istemler.
 *
 * systemPrompt config.json'a kaydediliyor; migrasyon olmadan Windows'ta
 * kurulmus bir profil macOS'a tasindiginda modele omur boyu "Windows'tasin"
 * demeye devam ederdi. Yalnizca bu listedeki metinler guncellenir — kullanicinin
 * kendi yazdigi istem asla ezilmez.
 */
const LEGACY_SYSTEM_PROMPTS = [
  buildSystemPrompt('Windows'),
  buildSystemPrompt('macOS'),
];

/** Eski varsayilan kisayollar; ayni migrasyon mantigi. */
const LEGACY_SHORTCUTS = ['Control+Shift+Space'];

/** Masaustu klasoru — Electron yerellestirilmis yolu bilir. */
function desktopDir() {
  try {
    return app.getPath('desktop');
  } catch {
    return path.join(os.homedir(), 'Desktop');
  }
}

const DEFAULTS = {
  baseUrl: 'http://localhost:11434/v1',
  apiKey: '',
  model: 'qwen3:4b',
  temperature: 0.3,
  stream: true,
  maxIterations: 25,
  permissionMode: 'ask', // 'ask' | 'auto' | 'readonly'
  // qwen3 ailesi varsayilan olarak her yanittan once uzun uzun "dusunur".
  // Bu makinede (CPU inference, ~12 tok/s) olculen etki: duz sohbet 111s -> 39s (2.8x).
  noThink: true,
  // Modeli RAM'de tutar; yoksa Ollama 5dk sonra bosaltir ve sonraki mesaj
  // 2.5 GB'i diskten yeniden yuklemeyi bekler.
  keepModelLoaded: true,
  workspaceRoot: desktopDir(),
  systemPrompt: DEFAULT_SYSTEM_PROMPT,
  searxngUrl: '',
  // CommandOrControl: Windows'ta Ctrl, macOS'ta Cmd olarak cozulur.
  globalShortcut: 'CommandOrControl+Shift+Space',
  minimizeToTray: true,

  // Uzaktan kontrol (Telegram). Token ve kullanici ID icin ortam degiskenleri
  // (TELEGRAM_BOT_TOKEN / TELEGRAM_USER_ID) onceliklidir; boylece kimlik
  // bilgisi diske hic yazilmadan da calistirilabilir.
  telegram: {
    enabled: false,
    token: '',
    userId: '',
    // Gorev bitince/hata alinca son ekran goruntusunu gonder.
    sendScreenshots: true,
    // OpenAI uyumlu /audio/transcriptions ucu. Bos ise sesli mesaj kapali.
    // Ollama ses cozumlemez; yerel whisper.cpp server gibi ayri bir uc gerekir.
    sttUrl: '',
    sttModel: 'whisper-1',
    sttLanguage: 'tr',
    sttApiKey: '',
  },
};

let cache = null;

/**
 * Diskteki ayarlari bu platformun varsayilanlariyla uyumlu hale getirir.
 * Yalnizca degeri hala eski bir VARSAYILAN olan alanlara dokunur.
 */
function migrate(stored) {
  const out = { ...stored };
  if (LEGACY_SYSTEM_PROMPTS.includes(out.systemPrompt)) {
    out.systemPrompt = DEFAULT_SYSTEM_PROMPT;
  }
  if (LEGACY_SHORTCUTS.includes(out.globalShortcut)) {
    out.globalShortcut = DEFAULTS.globalShortcut;
  }
  return out;
}

/**
 * Ic ice gecen alanlari koruyan birlestirme.
 *
 * Duz yayilim (`{...DEFAULTS, ...stored}`) `telegram` gibi nesne alanlarda
 * yanlis calisir: diskte yalnizca `{enabled:true}` varsa varsayilan model,
 * dil ve ekran goruntusu ayarlari **silinir**. Bu tur alanlar tek tek
 * birlestirilir.
 */
const NESTED_KEYS = ['telegram'];

function merge(base, patch) {
  const out = { ...base, ...patch };
  for (const key of NESTED_KEYS) {
    if (patch && typeof patch[key] === 'object' && patch[key] !== null) {
      out[key] = { ...base[key], ...patch[key] };
    } else {
      out[key] = { ...base[key] };
    }
  }
  return out;
}

function load() {
  if (cache) return cache;
  try {
    const raw = fs.readFileSync(CONFIG_FILE(), 'utf8');
    cache = merge(DEFAULTS, migrate(JSON.parse(raw)));
  } catch {
    cache = merge(DEFAULTS, {});
  }
  return cache;
}

function save(patch) {
  const next = merge(load(), patch);
  // Normalize
  next.baseUrl = String(next.baseUrl || DEFAULTS.baseUrl).replace(/\/+$/, '');
  next.temperature = Math.max(0, Math.min(2, Number(next.temperature) || 0));
  next.maxIterations = Math.max(1, Math.min(100, parseInt(next.maxIterations, 10) || 25));
  if (!['ask', 'auto', 'readonly'].includes(next.permissionMode)) next.permissionMode = 'ask';
  // Bos calisma alani, goreceli yollarin surecin cwd'sine cozulmesine yol acardi.
  if (!String(next.workspaceRoot || '').trim()) next.workspaceRoot = DEFAULTS.workspaceRoot;
  if (!String(next.model || '').trim()) next.model = DEFAULTS.model;

  cache = next;
  try {
    fs.mkdirSync(path.dirname(CONFIG_FILE()), { recursive: true });
    fs.writeFileSync(CONFIG_FILE(), JSON.stringify(next, null, 2), 'utf8');
  } catch (err) {
    console.error('Ayarlar kaydedilemedi:', err.message);
  }
  return next;
}

function reset() {
  cache = merge(DEFAULTS, {});
  return save({});
}

module.exports = { load, save, reset, desktopDir, DEFAULTS, DEFAULT_SYSTEM_PROMPT };
