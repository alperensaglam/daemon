'use strict';

/**
 * Sesli mesaj -> metin.
 *
 * Telegram sesli mesajlari OGG/Opus olarak gonderir. Cevirisi icin
 * **OpenAI uyumlu** `/audio/transcriptions` ucu kullanilir; boylece yerelde
 * whisper.cpp server, faster-whisper veya LocalAI calistiran biri hicbir sey
 * degistirmeden kullanabilir.
 *
 * ONEMLI: Ollama'nin ses cevirisi YOKTUR. Ayarlarda ayri bir STT adresi
 * verilmezse bu ozellik kapalidir ve kullaniciya bunu acikca soyleriz —
 * sessizce yok saymak, "sesli mesaj gonderdim ama hicbir sey olmadi"
 * seklinde teshis edilmesi zor bir davranis olurdu.
 */

const DEFAULT_MODEL = 'whisper-1';

/**
 * @param {Buffer} audio       OGG/Opus ses verisi
 * @param {object} config      Uygulama ayarlari
 * @param {object} [opts]
 * @returns {Promise<string>}  Cozulen metin
 */
async function transcribe(audio, config, { signal, filename = 'ses.ogg' } = {}) {
  const telegram = config.telegram || {};
  const baseUrl = String(telegram.sttUrl || '').trim().replace(/\/+$/, '');

  if (!baseUrl) {
    throw new Error(
      'Sesli mesaj destegi kapali: Ayarlar > Telegram > "Ses cozumleme adresi" '
      + 'bos. OpenAI uyumlu bir /audio/transcriptions ucu gerekir '
      + '(ornegin yerel whisper.cpp server). Ollama ses cozumlemez.'
    );
  }

  const form = new FormData();
  form.append('file', new Blob([audio], { type: 'audio/ogg' }), filename);
  form.append('model', telegram.sttModel || DEFAULT_MODEL);
  // Cok dilli modellerde dil ipucu dogrulugu belirgin artirir.
  if (telegram.sttLanguage) form.append('language', telegram.sttLanguage);

  const headers = {};
  const apiKey = telegram.sttApiKey || config.apiKey;
  if (apiKey) headers.Authorization = `Bearer ${apiKey}`;

  const response = await fetch(`${baseUrl}/audio/transcriptions`, {
    method: 'POST', body: form, headers, signal,
  });

  const raw = await response.text();
  if (!response.ok) {
    throw new Error(
      `Ses cozumleme basarisiz (HTTP ${response.status}): ${raw.slice(0, 200)}`
    );
  }

  let text;
  try {
    text = JSON.parse(raw).text;
  } catch {
    text = raw;                       // duz metin donduren sunucular var
  }

  const result = String(text ?? '').trim();
  if (!result) throw new Error('Ses cozumlendi ama metin bos dondu.');
  return result;
}

function isConfigured(config) {
  return Boolean(String(config?.telegram?.sttUrl || '').trim());
}

module.exports = { transcribe, isConfigured };
