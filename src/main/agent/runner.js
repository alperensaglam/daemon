'use strict';

/**
 * Tek calisma noktasi — hem arayuz hem uzaktan kontrol buradan gecer.
 *
 * Bu katman ipc.js icinden cikarildi. Sebep: Telegram'dan gelen bir mesajin da
 * agent turu baslatabilmesi gerekiyor, ama ipc.js'teki mantik `ipcMain.handle`
 * icine gomuluydu ve yalnizca renderer cagirabiliyordu.
 *
 * Iki sorumlulugu var:
 *
 *  1. **Tek calisma kilidi.** Ayni anda tek tur calisir; ikinci istek reddedilir.
 *  2. **Onay dagitimi.** loop.js tek bir `requestApproval` bekler; burada birden
 *     fazla onaylayici (arayuz, Telegram) yaristirilir ve ILK cevap kazanir.
 *     Kaybedenlere iptal sinyali gider, boylece Telegram'daki buton mesaji
 *     "uygulamadan yanitlandi" diye guncellenebilir.
 */

const { EventEmitter } = require('events');
const config = require('../config');
const loop = require('./loop');

/** @type {{controller: AbortController, source: string}|null} */
let current = null;

const bus = new EventEmitter();
// Onay ve olay dinleyicileri sinirsiz sayida olabilir; Node'un 10 dinleyici
// uyarisi burada yaniltici olurdu.
bus.setMaxListeners(0);

/**
 * Kayitli onaylayicilar.
 * Her biri: (req, {signal}) => Promise<'allow'|'deny'|'always'|'abort'|null>
 * `null` dondurmek "bu kanal su an cevap veremez" demektir (ornegin pencere
 * kapali); yarisi bozmaz, digerlerinin cevabi beklenir.
 * @type {Map<string, Function>}
 */
const approvers = new Map();

/** Telegram gibi kendi gecmisini tutamayan kaynaklar icin sohbet gecmisi. */
const histories = new Map();
const MAX_HISTORY_MESSAGES = 40;

function isBusy() {
  return current !== null;
}

function currentSource() {
  return current?.source || null;
}

/**
 * Bir onaylayici kaydeder. Donen fonksiyon kaydi siler.
 *
 * @param {string} name  Tanilama icin kanal adi ('ui' | 'telegram')
 * @param {(req:object, ctx:{signal:AbortSignal})=>Promise<string|null>} fn
 */
function registerApprover(name, fn) {
  approvers.set(name, fn);
  return () => approvers.delete(name);
}

/** Olay akisina abone olur. Donen fonksiyon aboneligi biter. */
function onEvent(listener) {
  bus.on('event', listener);
  return () => bus.off('event', listener);
}

function emit(event) {
  bus.emit('event', event);
}

/**
 * Onayi tum kanallara sorar; ilk gecerli cevap kazanir.
 *
 * Hic onaylayici yoksa **reddeder**. Bu bilincli: onay isteyen bir islem,
 * soracak kimse olmadigi icin sessizce calismamali. (Eski davranista pencere
 * kapaliyken istek renderer'a gonderiliyor, cevap hic gelmiyor ve tur sonsuza
 * kadar askida kaliyordu.)
 */
function requestApproval(req) {
  return new Promise((resolve) => {
    const channels = [...approvers.entries()];
    if (!channels.length) {
      resolve('deny');
      return;
    }

    const ac = new AbortController();
    let settled = false;
    let pending = channels.length;

    const finish = (decision) => {
      if (settled || !decision) return;
      settled = true;
      ac.abort();                    // kaybedenlere "vazgectik" de
      resolve(decision);
    };

    for (const [name, fn] of channels) {
      Promise.resolve()
        .then(() => fn(req, { signal: ac.signal }))
        .then((decision) => {
          if (decision) finish(decision);
        })
        .catch((err) => {
          if (!ac.signal.aborted) {
            console.warn(`Onay kanali "${name}" hata verdi:`, err.message);
          }
        })
        .finally(() => {
          pending -= 1;
          // Tum kanallar "cevap veremem" dediyse fail-closed.
          if (pending === 0 && !settled) {
            settled = true;
            resolve('deny');
          }
        });
    }
  });
}

/**
 * Bir agent turu baslatir.
 *
 * @param {object} opts
 * @param {string} opts.text       Kullanicinin mesaji
 * @param {Array}  [opts.history]  Cagiran kendi gecmisini tutuyorsa
 * @param {string} [opts.source]   'ui' | 'telegram'
 */
async function start({ text, history, source = 'ui' }) {
  if (current) {
    return { ok: false, error: 'Zaten calisan bir islem var.', busy: true };
  }

  const controller = new AbortController();
  current = { controller, source };

  const cfg = config.load();
  // Arayuz kendi gecmisini gonderir; Telegram gonderemez, onunkini biz tutariz.
  const priorHistory = history ?? histories.get(source) ?? [];

  emit({ type: 'run_start', source, text });

  try {
    const result = await loop.run({
      config: cfg,
      history: priorHistory,
      userText: text,
      signal: controller.signal,
      emit,
      requestApproval,
    });

    if (history === undefined) {
      // Sistem mesajini sakla; yalnizca sohbet gecmisi tutulur.
      const kept = result.messages.filter((m) => m.role !== 'system');
      histories.set(source, kept.slice(-MAX_HISTORY_MESSAGES));
    }

    emit({ type: 'run_end', source, stopped: result.stopped });
    return { ok: true, ...result };
  } catch (err) {
    emit({ type: 'error', message: err.message });
    emit({ type: 'done' });
    emit({ type: 'run_end', source, stopped: true, error: err.message });
    return { ok: false, error: err.message };
  } finally {
    current = null;
  }
}

function stop() {
  if (!current) return false;
  current.controller.abort();
  return true;
}

/** Bir kaynagin sohbet gecmisini temizler. */
function resetHistory(source = 'telegram') {
  histories.delete(source);
}

/** Bir kaynagin sohbet gecmisi (kopya). */
function getHistory(source = 'telegram') {
  return [...(histories.get(source) || [])];
}

function historyLength(source = 'telegram') {
  return (histories.get(source) || []).length;
}

/** Bir kaynagin son asistan cevabi — uzaktan raporlama icin. */
function lastAnswer(source = 'telegram') {
  const history = histories.get(source) || [];
  for (let i = history.length - 1; i >= 0; i--) {
    const message = history[i];
    if (message.role === 'assistant' && message.content) {
      return String(message.content).trim();
    }
  }
  return '';
}

module.exports = {
  start,
  stop,
  isBusy,
  currentSource,
  onEvent,
  emit,
  registerApprover,
  requestApproval,
  resetHistory,
  getHistory,
  historyLength,
  lastAnswer,
};
