'use strict';

/**
 * Ekran goruntusu — Electron'un desktopCapturer'i uzerinden.
 *
 * agent-core'daki (Python) yakalama yolundan bagimsizdir ve bilincli oyle:
 * bu katman Electron ana surecinde calisir, Python tarafi ise ayri ve bagimsiz
 * bir CLI'dir. Buraya pyobjc/pywin32 sokmak iki sistemi gereksizce baglardi.
 *
 * IZIN — macOS'ta ekran yakalama Ekran Kaydi (TCC) izni ister. Izin yoksa
 * desktopCapturer hata vermez, **bos veya siyah goruntu** dondurur; bu yuzden
 * once systemPreferences ile durum sorulur ve eksikse ne yapilacagi soylenir.
 */

const { desktopCapturer, screen, systemPreferences, nativeImage } = require('electron');
const { IS_MAC } = require('../platform');

/** Telegram'a gonderilecek gorselin azami genisligi (piksel). */
const MAX_WIDTH = 1280;

/** JPEG kalitesi — okunabilirlik ile boyut arasinda denge. */
const JPEG_QUALITY = 70;

/**
 * Ekran kaydi izni durumu.
 * @returns {{ok: boolean, reason: string}}
 */
function captureStatus() {
  if (!IS_MAC) return { ok: true, reason: '' };
  const status = systemPreferences.getMediaAccessStatus('screen');
  if (status === 'granted') return { ok: true, reason: '' };
  return {
    ok: false,
    reason: 'Ekran Kaydi izni yok. Sistem Ayarlari > Gizlilik ve Guvenlik > '
      + 'Ekran Kaydi altinda bu uygulamayi isaretleyip yeniden baslatin.',
  };
}

/**
 * Ekranin (veya bir pencerenin) goruntusunu JPEG olarak dondurur.
 *
 * @param {object} [opts]
 * @param {'screen'|'window'} [opts.type]  Varsayilan: tum ekran
 * @param {string} [opts.match]  Pencere adinda aranacak metin (type='window')
 * @returns {Promise<{buffer: Buffer, name: string, width: number, height: number}>}
 */
async function capture({ type = 'screen', match = '' } = {}) {
  const status = captureStatus();
  if (!status.ok) throw new Error(status.reason);

  // Retina'da mantiksal boyut kucuktur; olcek carpani olmadan bulanik cikar.
  const display = screen.getPrimaryDisplay();
  const scale = display.scaleFactor || 1;
  const thumbnailSize = {
    width: Math.round(display.size.width * scale),
    height: Math.round(display.size.height * scale),
  };

  const sources = await desktopCapturer.getSources({
    types: type === 'window' ? ['window'] : ['screen'],
    thumbnailSize,
    fetchWindowIcons: false,
  });

  if (!sources.length) {
    throw new Error(type === 'window'
      ? 'Acik pencere bulunamadi.'
      : 'Ekran kaynagi bulunamadi.');
  }

  let source = sources[0];
  if (match) {
    const needle = match.toLowerCase();
    source = sources.find((s) => s.name.toLowerCase().includes(needle)) || sources[0];
  }

  let image = source.thumbnail;
  if (image.isEmpty()) {
    throw new Error('Goruntu bos geldi — ekran kaydi izni verilmemis olabilir.');
  }

  // Telegram'a 4K gondermek gereksiz; hem yavas hem sikistirmada bozuluyor.
  const size = image.getSize();
  if (size.width > MAX_WIDTH) {
    image = image.resize({ width: MAX_WIDTH, quality: 'good' });
  }

  const resized = image.getSize();
  return {
    buffer: image.toJPEG(JPEG_QUALITY),
    name: source.name,
    width: resized.width,
    height: resized.height,
  };
}

/**
 * Acik pencerelerin adlarini z-sirasina yakin bir duzende dondurur.
 *
 * NOT: desktopCapturer z-sirasini **garanti etmez**. Pratikte macOS'ta on
 * plandaki pencere basta gelir, ama buna guvenilmemeli; bu yuzden aktif
 * pencere bilgisi platform.activeWindow() ile ayrica sorulur ve burasi
 * yalnizca liste icin kullanilir.
 */
async function listWindowNames(limit = 12) {
  const status = captureStatus();
  if (!status.ok) return [];
  const sources = await desktopCapturer.getSources({
    types: ['window'],
    thumbnailSize: { width: 1, height: 1 },   // piksel istemiyoruz, sadece ad
  });
  return sources.map((s) => s.name).filter(Boolean).slice(0, limit);
}

module.exports = { capture, captureStatus, listWindowNames, MAX_WIDTH };
