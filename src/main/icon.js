'use strict';

/**
 * Ikili varlik dosyasi tasimamak icin uygulama/tepsi ikonunu calisma aninda uretir.
 * Cikti: gecerli bir RGBA PNG buffer'i.
 */

const zlib = require('zlib');

const CRC_TABLE = (() => {
  const t = new Int32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c;
  }
  return t;
})();

function crc32(buf) {
  let c = -1;
  for (let i = 0; i < buf.length; i++) c = CRC_TABLE[(c ^ buf[i]) & 0xff] ^ (c >>> 8);
  return (c ^ -1) >>> 0;
}

function chunk(type, data) {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length, 0);
  const typeBuf = Buffer.from(type, 'ascii');
  const crcBuf = Buffer.alloc(4);
  crcBuf.writeUInt32BE(crc32(Buffer.concat([typeBuf, data])), 0);
  return Buffer.concat([len, typeBuf, data, crcBuf]);
}

/** @param {(x:number,y:number)=>[number,number,number,number]} shader */
function makePng(size, shader) {
  const raw = Buffer.alloc((size * 4 + 1) * size);
  let p = 0;
  for (let y = 0; y < size; y++) {
    raw[p++] = 0; // filter: none
    for (let x = 0; x < size; x++) {
      const [r, g, b, a] = shader(x, y);
      raw[p++] = r; raw[p++] = g; raw[p++] = b; raw[p++] = a;
    }
  }

  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size, 0);
  ihdr.writeUInt32BE(size, 4);
  ihdr[8] = 8;  // bit depth
  ihdr[9] = 6;  // color type: RGBA
  ihdr[10] = 0; ihdr[11] = 0; ihdr[12] = 0;

  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', ihdr),
    chunk('IDAT', zlib.deflateSync(raw, { level: 9 })),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

/** Koyu yuvarlak zemin + parlak halka: kucuk boyutta da okunakli. */
function buildIcon(size = 32) {
  const c = (size - 1) / 2;
  const rOuter = size * 0.47;
  const rRingOut = size * 0.31;
  const rRingIn = size * 0.19;
  const rCore = size * 0.1;

  return makePng(size, (x, y) => {
    const dx = x - c;
    const dy = y - c;
    const d = Math.sqrt(dx * dx + dy * dy);

    // kenar yumusatma
    const edge = (radius) => Math.max(0, Math.min(1, radius - d + 0.5));

    const bgA = edge(rOuter);
    if (bgA <= 0) return [0, 0, 0, 0];

    const coreA = edge(rCore);
    const ringA = Math.min(edge(rRingOut), Math.max(0, Math.min(1, d - rRingIn + 0.5)));

    // Renkler
    const bg = [24, 28, 42];
    const accent = [86, 204, 242];

    let a = Math.round(255 * bgA);
    let col = bg;

    const mix = Math.max(coreA, ringA);
    if (mix > 0) {
      col = [
        Math.round(bg[0] + (accent[0] - bg[0]) * mix),
        Math.round(bg[1] + (accent[1] - bg[1]) * mix),
        Math.round(bg[2] + (accent[2] - bg[2]) * mix),
      ];
    }
    return [col[0], col[1], col[2], a];
  });
}

module.exports = { buildIcon };
