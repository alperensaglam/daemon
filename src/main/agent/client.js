'use strict';

/**
 * OpenAI-uyumlu /v1/chat/completions istemcisi.
 * Ollama, LM Studio, llama.cpp server, OpenRouter, Groq vb. ile calisir.
 */

const THINK_OPEN = '<think>';
const THINK_CLOSE = '</think>';

function headers(config) {
  const h = { 'Content-Type': 'application/json' };
  if (config.apiKey && config.apiKey.trim()) h.Authorization = `Bearer ${config.apiKey.trim()}`;
  return h;
}

/**
 * Ham "fetch failed" mesaji kullaniciya hicbir sey anlatmaz — en olasi neden
 * model sunucusunun kapali olmasidir. Anlasilir ve eyleme donuk hale getir.
 */
function connectionError(err, config) {
  const code = err?.cause?.code || err?.code || '';
  if (code === 'ECONNREFUSED' || code === 'ENOTFOUND' || code === 'EAI_AGAIN' || /fetch failed/i.test(err?.message || '')) {
    return new Error(
      `Model sunucusuna baglanilamadi: ${config.baseUrl}\n\n` +
      'Kontrol listesi:\n' +
      '1. Ollama calisiyor mu?  PowerShell: ollama list\n' +
      '2. Calismiyorsa baslatin: ollama serve\n' +
      '3. Adres dogru mu? Ayarlar > Sunucu adresi' +
      (code ? `\n\n(teknik: ${code})` : '')
    );
  }
  return err;
}

async function listModels(config) {
  let res;
  try {
    res = await fetch(`${config.baseUrl}/models`, { headers: headers(config) });
  } catch (err) {
    throw connectionError(err, config);
  }
  if (!res.ok) throw new Error(`HTTP ${res.status} ${res.statusText}`);
  const data = await res.json();
  return (data.data || []).map((m) => m.id).sort();
}

/**
 * Modeli bellege yukler ve orada tutar (Ollama'ya ozel, en iyi caba).
 *
 * Ollama modeli 5 dakika bosta kalinca RAM'den atar; sonraki mesajda 2.5 GB
 * diskten yeniden yuklenir ve kullanici sebepsiz bekler. Bu cagri modeli
 * 30 dakika sabitler. OpenAI-uyumlu endpoint keep_alive kabul etmedigi icin
 * Ollama'nin yerel /api/chat ucu kullanilir; baska saglayicilarda 404 doner
 * ve sessizce yok sayilir.
 */
async function warmup(config) {
  if (!config.keepModelLoaded) return { ok: false, skipped: true };
  const base = config.baseUrl.replace(/\/v1\/?$/, '');
  try {
    const res = await fetch(`${base}/api/chat`, {
      method: 'POST',
      headers: headers(config),
      body: JSON.stringify({
        model: config.model,
        messages: [{ role: 'user', content: 'hi' }],
        stream: false,
        keep_alive: '30m',
        options: { num_predict: 1 },
      }),
    });
    return { ok: res.ok };
  } catch {
    return { ok: false };
  }
}

/**
 * <think>...</think> bloklarini akis halinde ayirir.
 * Etiketin parcali gelmesine karsi kismi tampon tutar.
 */
function createThinkSplitter(onText, onThinking) {
  let buffer = '';
  let inThink = false;

  function flush(final = false) {
    while (buffer.length > 0) {
      if (!inThink) {
        const idx = buffer.indexOf(THINK_OPEN);
        if (idx === -1) {
          // Etiketin yarisi gelmis olabilir; sondaki olasi kismi tut
          const keep = final ? 0 : partialTailLength(buffer, THINK_OPEN);
          const emit = buffer.slice(0, buffer.length - keep);
          if (emit) onText(emit);
          buffer = buffer.slice(buffer.length - keep);
          return;
        }
        if (idx > 0) onText(buffer.slice(0, idx));
        buffer = buffer.slice(idx + THINK_OPEN.length);
        inThink = true;
      } else {
        const idx = buffer.indexOf(THINK_CLOSE);
        if (idx === -1) {
          const keep = final ? 0 : partialTailLength(buffer, THINK_CLOSE);
          const emit = buffer.slice(0, buffer.length - keep);
          if (emit) onThinking(emit);
          buffer = buffer.slice(buffer.length - keep);
          return;
        }
        if (idx > 0) onThinking(buffer.slice(0, idx));
        buffer = buffer.slice(idx + THINK_CLOSE.length);
        inThink = false;
      }
    }
  }

  return {
    push(chunk) { buffer += chunk; flush(false); },
    end() { flush(true); },
  };
}

/** "abc<thi" gibi bir sonun, etiketin baslangici olma ihtimalini olcer. */
function partialTailLength(text, tag) {
  const max = Math.min(tag.length - 1, text.length);
  for (let n = max; n > 0; n--) {
    if (text.endsWith(tag.slice(0, n))) return n;
  }
  return 0;
}

/** Streaming delta'larini birlestirerek tam tool_calls dizisi olusturur. */
function createToolCallAccumulator() {
  const slots = new Map();
  return {
    push(deltas) {
      for (const d of deltas || []) {
        const index = d.index ?? 0;
        if (!slots.has(index)) slots.set(index, { id: '', type: 'function', function: { name: '', arguments: '' } });
        const slot = slots.get(index);
        if (d.id) slot.id = d.id;
        if (d.type) slot.type = d.type;
        if (d.function?.name) slot.function.name += d.function.name;
        if (d.function?.arguments) slot.function.arguments += d.function.arguments;
      }
    },
    result() {
      return [...slots.entries()]
        .sort((a, b) => a[0] - b[0])
        .map(([, v], i) => ({ ...v, id: v.id || `call_${Date.now()}_${i}` }))
        .filter((v) => v.function.name);
    },
  };
}

/**
 * Modeli cagirir.
 * @returns {Promise<{content:string, thinking:string, toolCalls:Array, finishReason:string}>}
 */
async function chat({ config, messages, tools, signal, onText, onThinking }) {
  const body = {
    model: config.model,
    messages,
    temperature: config.temperature,
    stream: !!config.stream,
  };
  if (tools && tools.length) {
    body.tools = tools;
    body.tool_choice = 'auto';
  }

  let res;
  try {
    res = await fetch(`${config.baseUrl}/chat/completions`, {
      method: 'POST',
      headers: headers(config),
      body: JSON.stringify(body),
      signal,
    });
  } catch (err) {
    if (err.name === 'AbortError') throw err; // iptal — oldugu gibi yukari gitsin
    throw connectionError(err, config);
  }

  if (!res.ok) {
    let detail = '';
    try { detail = (await res.text()).slice(0, 500); } catch { /* yoksay */ }
    throw new Error(`Model sunucusu hata dondurdu: HTTP ${res.status} ${res.statusText}${detail ? `\n${detail}` : ''}`);
  }

  if (!body.stream) {
    const data = await res.json();
    const choice = data.choices?.[0] || {};
    const msg = choice.message || {};

    let visible = '';
    let thinking = msg.reasoning_content || msg.reasoning || '';
    const splitter = createThinkSplitter(
      (t) => { visible += t; },
      (t) => { thinking += t; }
    );
    splitter.push(msg.content || '');
    splitter.end();

    if (thinking) onThinking?.(thinking);
    if (visible) onText?.(visible);

    return {
      content: visible,
      thinking,
      toolCalls: msg.tool_calls || [],
      finishReason: choice.finish_reason || (msg.tool_calls?.length ? 'tool_calls' : 'stop'),
    };
  }

  if (!res.body) {
    throw new Error(
      'Sunucu akisli yanit govdesi dondurmedi. Ayarlar > "Akisli yanit" secenegini kapatip tekrar deneyin.'
    );
  }

  // --- Streaming (SSE) ---
  let content = '';
  let thinking = '';
  let finishReason = 'stop';
  const toolAcc = createToolCallAccumulator();
  const splitter = createThinkSplitter(
    (t) => { content += t; onText?.(t); },
    (t) => { thinking += t; onThinking?.(t); }
  );

  const reader = res.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let nl;
      while ((nl = buffer.indexOf('\n')) !== -1) {
        const line = buffer.slice(0, nl).trim();
        buffer = buffer.slice(nl + 1);
        if (!line || line.startsWith(':')) continue;
        if (!line.startsWith('data:')) continue;

        const payload = line.slice(5).trim();
        if (payload === '[DONE]') continue;

        let chunk;
        try { chunk = JSON.parse(payload); } catch { continue; }

        const choice = chunk.choices?.[0];
        if (!choice) continue;
        const delta = choice.delta || {};

        if (delta.reasoning_content) { thinking += delta.reasoning_content; onThinking?.(delta.reasoning_content); }
        if (delta.reasoning) { thinking += delta.reasoning; onThinking?.(delta.reasoning); }
        if (delta.content) splitter.push(delta.content);
        if (delta.tool_calls) toolAcc.push(delta.tool_calls);
        if (choice.finish_reason) finishReason = choice.finish_reason;
      }
    }
  } finally {
    try { await reader.cancel(); } catch { /* yoksay */ }
  }

  splitter.end();

  const toolCalls = toolAcc.result();
  if (toolCalls.length && finishReason !== 'tool_calls') finishReason = 'tool_calls';

  return { content, thinking, toolCalls, finishReason };
}

module.exports = { chat, listModels, warmup };
