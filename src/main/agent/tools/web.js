'use strict';

const { LIMITS, truncate } = require('../../security');
const { IS_WIN } = require('../../platform');

// Bazi siteler platformla tutarsiz bir UA gorunce farkli icerik dondurur.
const UA = IS_WIN
  ? 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'
  : 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36';

function decodeEntities(s) {
  return String(s)
    .replace(/&nbsp;/gi, ' ')
    .replace(/&amp;/gi, '&')
    .replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>')
    .replace(/&quot;/gi, '"')
    .replace(/&#0?39;/gi, "'")
    .replace(/&#x27;/gi, "'")
    .replace(/&#(\d+);/g, (_, n) => String.fromCharCode(parseInt(n, 10)));
}

function htmlToText(html) {
  return decodeEntities(
    String(html)
      .replace(/<script[\s\S]*?<\/script>/gi, ' ')
      .replace(/<style[\s\S]*?<\/style>/gi, ' ')
      .replace(/<noscript[\s\S]*?<\/noscript>/gi, ' ')
      .replace(/<!--[\s\S]*?-->/g, ' ')
      .replace(/<\/(p|div|li|tr|h[1-6]|section|article|br)>/gi, '\n')
      .replace(/<br\s*\/?>/gi, '\n')
      .replace(/<[^>]+>/g, ' ')
  )
    .replace(/[ \t ]+/g, ' ')
    .replace(/\n\s*\n\s*\n+/g, '\n\n')
    .trim();
}

async function fetchText(url, signal, maxBytes) {
  const res = await fetch(url, {
    signal,
    redirect: 'follow',
    headers: { 'User-Agent': UA, 'Accept-Language': 'tr,en;q=0.8' },
  });
  if (!res.ok) throw new Error(`HTTP ${res.status} ${res.statusText} — ${url}`);
  if (!res.body) throw new Error(`${url} bos yanit dondurdu.`);

  const reader = res.body.getReader();
  const chunks = [];
  let total = 0;
  while (total < maxBytes) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    total += value.length;
  }
  try { await reader.cancel(); } catch { /* yoksay */ }

  return {
    body: Buffer.concat(chunks.map((c) => Buffer.from(c))).toString('utf8'),
    contentType: res.headers.get('content-type') || '',
    finalUrl: res.url || url,
  };
}

/** DuckDuckGo Lite sonuc sayfasini ayristirir. */
function parseDuckDuckGo(html) {
  const results = [];
  const linkRe = /<a[^>]+class="result-link"[^>]*href="([^"]+)"[^>]*>([\s\S]*?)<\/a>/gi;
  const snippetRe = /<td[^>]*class="result-snippet"[^>]*>([\s\S]*?)<\/td>/gi;

  const links = [];
  let m;
  while ((m = linkRe.exec(html)) !== null) {
    links.push({ url: decodeEntities(m[1]), title: htmlToText(m[2]) });
  }
  const snippets = [];
  while ((m = snippetRe.exec(html)) !== null) {
    snippets.push(htmlToText(m[1]));
  }

  for (let i = 0; i < links.length; i++) {
    let url = links[i].url;
    // DDG bazen /l/?uddg=<encoded> seklinde yonlendirme verir
    const redirect = url.match(/[?&]uddg=([^&]+)/);
    if (redirect) url = decodeURIComponent(redirect[1]);
    if (url.startsWith('//')) url = 'https:' + url;
    results.push({ title: links[i].title, url, snippet: snippets[i] || '' });
  }
  return results;
}

const webSearch = {
  name: 'web_search',
  risk: 'safe',
  pathArgs: [],
  description:
    'Internette arama yapar ve baslik + link + kisa ozet listesi dondurur. Guncel bilgi (haber, hava durumu, fiyat, tarih) gerektiginde kullan. Detay icin sonuclardan birini fetch_url ile ac.',
  parameters: {
    type: 'object',
    properties: {
      query: { type: 'string', description: 'Arama sorgusu.' },
      limit: { type: 'number', description: 'Kac sonuc dondurulsun (varsayilan 6).' },
    },
    required: ['query'],
  },
  async handler(args, ctx) {
    const query = String(args.query || '').trim();
    if (!query) throw new Error('Arama sorgusu bos olamaz.');
    const limit = Math.min(15, Math.max(1, parseInt(args.limit, 10) || 6));

    // SearXNG yapilandirildiysa once onu dene (daha guvenilir, JSON API)
    const searx = (ctx.config.searxngUrl || '').replace(/\/+$/, '');
    if (searx) {
      try {
        const url = `${searx}/search?q=${encodeURIComponent(query)}&format=json&language=tr`;
        const { body } = await fetchText(url, ctx.signal, 512 * 1024);
        const data = JSON.parse(body);
        const rows = (data.results || []).slice(0, limit).map(
          (r, i) => `${i + 1}. ${r.title}\n   ${r.url}\n   ${(r.content || '').slice(0, 300)}`
        );
        if (rows.length) return truncate(`"${query}" icin sonuclar (SearXNG):\n\n` + rows.join('\n\n'));
      } catch (err) {
        // SearXNG basarisiz — DuckDuckGo'ya dus
      }
    }

    const url = `https://lite.duckduckgo.com/lite/?q=${encodeURIComponent(query)}`;
    const { body } = await fetchText(url, ctx.signal, 512 * 1024);
    const results = parseDuckDuckGo(body).slice(0, limit);

    if (results.length === 0) {
      throw new Error(
        'Arama sonucu ayristirilamadi. DuckDuckGo sayfa yapisi degismis olabilir; Ayarlar > SearXNG adresi alanina bir SearXNG sunucusu girmeyi deneyin.'
      );
    }

    const rows = results.map((r, i) => `${i + 1}. ${r.title}\n   ${r.url}\n   ${r.snippet.slice(0, 300)}`);
    return truncate(`"${query}" icin sonuclar:\n\n` + rows.join('\n\n'));
  },
};

const fetchUrl = {
  name: 'fetch_url',
  risk: 'safe',
  pathArgs: [],
  description: 'Bir web sayfasinin icerigini indirir ve okunabilir metne cevirir.',
  parameters: {
    type: 'object',
    properties: {
      url: { type: 'string', description: 'Acilacak sayfanin tam adresi (https:// ile).' },
    },
    required: ['url'],
  },
  async handler(args, ctx) {
    let url = String(args.url || '').trim();
    if (!url) throw new Error('URL bos olamaz.');
    if (!/^https?:\/\//i.test(url)) url = 'https://' + url;

    const { body, contentType, finalUrl } = await fetchText(url, ctx.signal, LIMITS.fetchBytes * 4);

    if (/json/i.test(contentType)) {
      return truncate(`Kaynak: ${finalUrl}\n\n${body}`);
    }
    const text = /html/i.test(contentType) ? htmlToText(body) : body;
    if (!text.trim()) throw new Error(`${finalUrl} adresinden metin cikarilamadi (icerik turu: ${contentType}).`);
    return truncate(`Kaynak: ${finalUrl}\n\n${text}`, LIMITS.fetchBytes);
  },
};

module.exports = [webSearch, fetchUrl];
