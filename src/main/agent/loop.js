'use strict';

const os = require('os');
const path = require('path');
const client = require('./client');
const registry = require('./registry');
const { assessRisk, needsApproval, truncate } = require('../security');
const { salvageToolCalls } = require('./salvage');

let runCounter = 0;

/**
 * Bir agent turu calistirir: model -> arac cagrilari -> model -> ... -> nihai cevap.
 *
 * @param {object} opts
 * @param {object} opts.config
 * @param {Array}  opts.history   Onceki mesajlar (system haric)
 * @param {string} opts.userText  Kullanicinin yeni mesaji
 * @param {AbortSignal} opts.signal
 * @param {(evt:object)=>void} opts.emit  Arayuze olay gonderir
 * @param {(req:object)=>Promise<'allow'|'deny'|'always'>} opts.requestApproval
 * @returns {Promise<{messages:Array, stopped:boolean}>}
 */
async function run({ config, history, userText, signal, emit, requestApproval }) {
  const runId = ++runCounter;
  const sessionAllow = new Set(); // "bu oturumda hep izin ver" denilen arac adlari

  const messages = [
    { role: 'system', content: buildSystemPrompt(config) },
    ...history,
    { role: 'user', content: userText },
  ];

  const tools = registry.toolSchemas();
  let stopped = false;

  for (let iteration = 1; iteration <= config.maxIterations; iteration++) {
    if (signal.aborted) { stopped = true; break; }

    emit({ type: 'assistant_start', runId, iteration });

    let result;
    try {
      result = await client.chat({
        config,
        messages,
        tools,
        signal,
        onText: (t) => emit({ type: 'text_delta', runId, text: t }),
        onThinking: (t) => emit({ type: 'thinking_delta', runId, text: t }),
      });
    } catch (err) {
      if (err.name === 'AbortError' || signal.aborted) { stopped = true; break; }
      emit({ type: 'error', runId, message: err.message });
      messages.push({ role: 'assistant', content: `[Hata: ${err.message}]` });
      break;
    }

    // Kucuk modeller araç çağrısını bazen native alan yerine duz metne JSON olarak yazar.
    // O durumda hicbir arac calismaz ve kullanici ham JSON gorur; burada kurtariyoruz.
    let toolCalls = result.toolCalls;
    let visible = result.content;
    if (toolCalls.length === 0 && visible) {
      const rescued = salvageToolCalls(visible, (n) => !!registry.get(n));
      if (rescued.calls.length) {
        toolCalls = rescued.calls;
        visible = rescued.cleaned;
        emit({ type: 'salvaged', runId, count: rescued.calls.length, cleaned: visible });
      }
    }

    emit({ type: 'assistant_end', runId, content: visible, thinking: result.thinking });

    const assistantMsg = { role: 'assistant', content: visible || '' };
    if (toolCalls.length) assistantMsg.tool_calls = toolCalls;
    messages.push(assistantMsg);

    if (!toolCalls.length) break;

    if (iteration === config.maxIterations) {
      emit({ type: 'error', runId, message: `Maksimum adim sayisina (${config.maxIterations}) ulasildi.` });
      messages.push({
        role: 'user',
        content: 'Adim sinirina ulasildi. Simdiye kadar yaptiklarini kisaca ozetle, yeni arac cagirma.',
      });
      continue;
    }

    for (const call of toolCalls) {
      if (signal.aborted) { stopped = true; break; }

      const outcome = await executeToolCall({
        call, config, signal, emit, runId, requestApproval, sessionAllow,
      });
      messages.push({
        role: 'tool',
        tool_call_id: call.id,
        name: call.function.name,
        content: outcome,
      });
    }

    if (stopped) break;
  }

  if (stopped) emit({ type: 'stopped', runId });
  emit({ type: 'done', runId });

  // system mesajini gecmise yazma
  return { messages: messages.slice(1), stopped };
}

async function executeToolCall({ call, config, signal, emit, runId, requestApproval, sessionAllow }) {
  const name = call.function?.name;
  const tool = registry.get(name);
  const callId = call.id;

  let args = {};
  try {
    const raw = call.function?.arguments;
    args = raw && raw.trim() ? JSON.parse(raw) : {};
  } catch (err) {
    emit({ type: 'tool_end', runId, callId, name, ok: false, result: 'Gecersiz JSON parametre.' });
    return `HATA: Parametreler gecerli JSON degil (${err.message}). Parametreleri duzeltip tekrar dene.`;
  }

  if (!tool) {
    emit({ type: 'tool_end', runId, callId, name, ok: false, result: 'Bilinmeyen arac.' });
    return `HATA: "${name}" adinda bir arac yok. Kullanilabilir araclar: ${registry.list().map((t) => t.name).join(', ')}`;
  }

  const assessment = assessRisk(tool, args, config);
  emit({ type: 'tool_start', runId, callId, name, args, risk: assessment.risk, reasons: assessment.reasons });

  const gate = needsApproval(assessment, config.permissionMode);

  if (gate.blocked) {
    const msg = 'Salt-okunur mod acik oldugu icin bu arac calistirilmadi.';
    emit({ type: 'tool_end', runId, callId, name, ok: false, result: msg });
    return `REDDEDILDI: ${msg} Kullaniciya izin modunu degistirmesi gerektigini soyle.`;
  }

  if (gate.approvalRequired && !(sessionAllow.has(name) && !assessment.forceAsk)) {
    let preview = '';
    try {
      preview = tool.preview ? await tool.preview(args, { config, signal }) : '';
    } catch (err) {
      preview = `[Onizleme olusturulamadi: ${err.message}]`;
    }

    const decision = await requestApproval({
      runId, callId, name, args, risk: assessment.risk, reasons: assessment.reasons, preview,
    });

    if (decision === 'always') sessionAllow.add(name);

    if (decision === 'deny') {
      const msg = 'Kullanici bu islemi reddetti.';
      emit({ type: 'tool_end', runId, callId, name, ok: false, result: msg });
      return `REDDEDILDI: ${msg} Bu islemi tekrar deneme; kullaniciya neden gerekli oldugunu acikla veya alternatif bir yol oner.`;
    }
    if (decision === 'abort') {
      emit({ type: 'tool_end', runId, callId, name, ok: false, result: 'Durduruldu.' });
      return 'DURDURULDU: Kullanici islemi iptal etti.';
    }
  }

  try {
    const output = await tool.handler(args, { config, signal });
    const text = truncate(String(output ?? '(sonuc yok)'));
    emit({ type: 'tool_end', runId, callId, name, ok: true, result: text });
    return text;
  } catch (err) {
    if (err.name === 'AbortError') {
      emit({ type: 'tool_end', runId, callId, name, ok: false, result: 'Durduruldu.' });
      return 'DURDURULDU: Islem iptal edildi.';
    }
    const msg = err.message || String(err);
    emit({ type: 'tool_end', runId, callId, name, ok: false, result: msg });
    return `HATA: ${msg}`;
  }
}

function buildSystemPrompt(config) {
  const now = new Date();
  // qwen3'te "/no_think" dusunme modunu kapatir. Baska modellerde zararsiz sekilde yok sayilir.
  const noThink = config.noThink ? '\n/no_think' : '';
  return [
    config.systemPrompt + noThink,
    '',
    '--- Ortam bilgisi ---',
    `Tarih ve saat: ${now.toLocaleString('tr-TR')} (${Intl.DateTimeFormat().resolvedOptions().timeZone})`,
    `Isletim sistemi: Windows (${os.release()})`,
    `Kullanici adi: ${os.userInfo().username}`,
    `Ev klasoru: ${os.homedir()}`,
    '',
    `CALISMA ALANI: ${config.workspaceRoot}`,
    desktopLine(config),
    '',
    'Yol kurallari:',
    '- Kullanici bir dosya adi soyledigunde (ornek: "notlar.txt"), aksi belirtilmedikce',
    '  bu dosya CALISMA ALANI icindedir.',
    '- Araclara tam yol yazma; sadece dosya adini veya goreceli yolu ver ("notlar.txt",',
    '  "raporlar/ocak.md"). Goreceli yollar calisma alanina gore cozulur.',
    '- Bir dosya bulunamazsa once list_dir ile calisma alanina bak, sonra farkli bir yol dene.',
    '- Calisma alani disindaki islemler kullanici onayi ister.',
  ].join('\n');
}

function desktopLine(config) {
  const desktop = path.join(os.homedir(), 'Desktop');
  const same = path.resolve(desktop).toLowerCase() === path.resolve(config.workspaceRoot).toLowerCase();
  return same
    ? '(Calisma alani zaten kullanicinin Masaustu klasorudur.)'
    : `Masaustu klasoru: ${desktop}`;
}

module.exports = { run };
