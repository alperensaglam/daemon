'use strict';

const fs = require('fs');
const path = require('path');
const os = require('os');
const { app } = require('electron');

const CONFIG_FILE = () => path.join(app.getPath('userData'), 'config.json');

const DEFAULT_SYSTEM_PROMPT = `Sen kullanicinin Windows bilgisayarinda calisan bir masaustu asistanisin.

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
  workspaceRoot: path.join(os.homedir(), 'Desktop'),
  systemPrompt: DEFAULT_SYSTEM_PROMPT,
  searxngUrl: '',
  globalShortcut: 'Control+Shift+Space',
  minimizeToTray: true,
};

let cache = null;

function load() {
  if (cache) return cache;
  try {
    const raw = fs.readFileSync(CONFIG_FILE(), 'utf8');
    cache = { ...DEFAULTS, ...JSON.parse(raw) };
  } catch {
    cache = { ...DEFAULTS };
  }
  return cache;
}

function save(patch) {
  const next = { ...load(), ...patch };
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
  cache = null;
  return save({ ...DEFAULTS });
}

module.exports = { load, save, reset, DEFAULTS, DEFAULT_SYSTEM_PROMPT };
