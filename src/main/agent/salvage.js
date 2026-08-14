'use strict';

/**
 * Kucuk yerel modeller araç çağrısını bazen native `tool_calls` alaninda degil,
 * duz metin icinde JSON olarak yazar. O durumda hicbir arac calismaz ve
 * kullaniciya ham JSON gosterilir.
 *
 * Bu modul metinden arac cagrisi kurtarir ve JSON'u gorunur metinden temizler.
 * Yalnizca kayitli araclarin adlari kabul edilir; uydurma isimler yok sayilir.
 */

let counter = 0;

/** Metindeki dengeli { ... } bloklarini bulur (ic ice ve string-farkindalikli). */
function findJsonObjects(text) {
  const out = [];
  for (let i = 0; i < text.length; i++) {
    if (text[i] !== '{') continue;
    let depth = 0;
    let inStr = false;
    let esc = false;
    for (let j = i; j < text.length; j++) {
      const ch = text[j];
      if (inStr) {
        if (esc) esc = false;
        else if (ch === '\\') esc = true;
        else if (ch === '"') inStr = false;
        continue;
      }
      if (ch === '"') inStr = true;
      else if (ch === '{') depth++;
      else if (ch === '}') {
        depth--;
        if (depth === 0) {
          out.push({ start: i, end: j + 1, text: text.slice(i, j + 1) });
          i = j; // ic ice blokları tekrar taramayalim
          break;
        }
      }
    }
  }
  return out;
}

/** Farkli sarmalama bicimlerini {name, args} listesine indirger. */
function normalize(obj) {
  if (obj == null) return [];
  if (Array.isArray(obj)) return obj.flatMap(normalize);

  // { tool_calls: [...] }
  if (Array.isArray(obj.tool_calls)) return obj.tool_calls.flatMap(normalize);
  // { tool_call: {...} }
  if (obj.tool_call) return normalize(obj.tool_call);
  // { function: { name, arguments } }
  if (obj.function && typeof obj.function === 'object') {
    return [{ name: obj.function.name, args: obj.function.arguments }];
  }
  // { name, arguments | parameters | args | input }
  if (typeof obj.name === 'string') {
    return [{ name: obj.name, args: obj.arguments ?? obj.parameters ?? obj.args ?? obj.input ?? {} }];
  }
  return [];
}

/**
 * @param {string} content  Modelin gorunur metni
 * @param {(name:string)=>boolean} isKnownTool
 * @returns {{calls:Array, cleaned:string}}
 */
function salvageToolCalls(content, isKnownTool) {
  const text = String(content || '');
  if (!text.includes('{')) return { calls: [], cleaned: text };

  const calls = [];
  const cutRanges = [];

  // Once ```json ... ``` bloklari, sonra ciplak JSON nesneleri
  const fenceRe = /```(?:json|tool_call|tool_code)?\s*\n?([\s\S]*?)```/gi;
  const candidates = [];
  let m;
  while ((m = fenceRe.exec(text)) !== null) {
    candidates.push({ start: m.index, end: m.index + m[0].length, json: m[1] });
  }
  for (const obj of findJsonObjects(text)) {
    // Fence icinde kalanlari tekrar ekleme
    if (candidates.some((c) => obj.start >= c.start && obj.end <= c.end)) continue;
    candidates.push({ start: obj.start, end: obj.end, json: obj.text });
  }

  for (const c of candidates) {
    let parsed;
    try {
      parsed = JSON.parse(c.json.trim());
    } catch {
      continue;
    }
    const found = normalize(parsed).filter((x) => x.name && isKnownTool(x.name));
    if (found.length === 0) continue;

    for (const f of found) {
      const args = typeof f.args === 'string' ? f.args : JSON.stringify(f.args ?? {});
      calls.push({
        id: `salvaged_${Date.now()}_${counter++}`,
        type: 'function',
        function: { name: f.name, arguments: args },
      });
    }
    cutRanges.push([c.start, c.end]);
  }

  if (calls.length === 0) return { calls: [], cleaned: text };

  // Kurtarilan JSON'lari gorunur metinden cikar
  cutRanges.sort((a, b) => b[0] - a[0]);
  let cleaned = text;
  for (const [s, e] of cutRanges) cleaned = cleaned.slice(0, s) + cleaned.slice(e);

  return { calls, cleaned: cleaned.replace(/\n{3,}/g, '\n\n').trim() };
}

module.exports = { salvageToolCalls, findJsonObjects };
