'use strict';

const fs = require('fs');
const fsp = require('fs/promises');
const path = require('path');
const { resolvePath, LIMITS, truncate } = require('../../security');

const SKIP_DIRS = new Set([
  'node_modules', '.git', '.svn', '$RECYCLE.BIN', 'System Volume Information',
  '.cache', 'AppData', '__pycache__', '.venv', 'venv', 'dist', 'build',
]);

const NUL = String.fromCharCode(0);

function fmtSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

/** Basit glob -> RegExp. Destek: *, ?, ** ve {a,b} */
function globToRegExp(pattern) {
  let re = '';
  for (let i = 0; i < pattern.length; i++) {
    const c = pattern[i];
    if (c === '*') {
      if (pattern[i + 1] === '*') { re += '.*'; i++; }
      else re += '[^\\\\/]*';
    } else if (c === '?') re += '[^\\\\/]';
    else if (c === '{') re += '(';
    else if (c === '}') re += ')';
    else if (c === ',') re += '|';
    else re += c.replace(/[.+^${}()|[\]\\]/g, '\\$&');
  }
  return new RegExp(`^${re}$`, 'i');
}

const listDir = {
  name: 'list_dir',
  risk: 'safe',
  pathArgs: ['path'],
  description:
    'Bir klasorun icerigini listeler (dosya adi, tip, boyut, degistirilme tarihi). Kullanicinin dosyalari hakkinda bir sey soruldugunda once bunu kullan.',
  parameters: {
    type: 'object',
    properties: {
      path: { type: 'string', description: 'Listelenecek klasorun yolu. Bos birakilirsa calisma alani koku kullanilir.' },
    },
  },
  async handler(args, ctx) {
    const target = resolvePath(args.path || ctx.config.workspaceRoot, ctx.config.workspaceRoot);
    const entries = await fsp.readdir(target.path, { withFileTypes: true });
    if (entries.length === 0) return `${target.path} bos.`;

    const rows = [];
    for (const e of entries) {
      let size = '';
      let mtime = '';
      try {
        const st = await fsp.stat(path.join(target.path, e.name));
        size = e.isDirectory() ? '' : fmtSize(st.size);
        mtime = st.mtime.toISOString().slice(0, 16).replace('T', ' ');
      } catch { /* erisim yok */ }
      rows.push(`${e.isDirectory() ? '[KLASOR]' : '[DOSYA] '} ${e.name}${size ? `  (${size})` : ''}${mtime ? `  ${mtime}` : ''}`);
    }
    rows.sort();
    return truncate(`${target.path} icerigi (${entries.length} oge):\n` + rows.join('\n'));
  },
};

const readFile = {
  name: 'read_file',
  risk: 'safe',
  pathArgs: ['path'],
  description: 'Bir metin dosyasinin icerigini okur. Ikili (binary) dosyalar icin uygun degildir.',
  parameters: {
    type: 'object',
    properties: {
      path: { type: 'string', description: 'Okunacak dosyanin yolu.' },
    },
    required: ['path'],
  },
  async handler(args, ctx) {
    const target = resolvePath(args.path, ctx.config.workspaceRoot);
    const st = await fsp.stat(target.path);
    if (st.isDirectory()) throw new Error(`${target.path} bir klasor, dosya degil. list_dir kullan.`);

    if (st.size > LIMITS.readFileBytes) {
      const fd = await fsp.open(target.path, 'r');
      try {
        const buf = Buffer.alloc(LIMITS.readFileBytes);
        await fd.read(buf, 0, LIMITS.readFileBytes, 0);
        return `[Dosya ${fmtSize(st.size)}, ilk ${fmtSize(LIMITS.readFileBytes)} gosteriliyor]\n\n` + buf.toString('utf8');
      } finally {
        await fd.close();
      }
    }

    const content = await fsp.readFile(target.path, 'utf8');
    if (content.includes(NUL)) {
      throw new Error('Bu dosya ikili (binary) gorunuyor, metin olarak okunamaz.');
    }
    return truncate(content);
  },
};

const writeFile = {
  name: 'write_file',
  risk: 'write',
  pathArgs: ['path'],
  description:
    'Bir dosyaya metin yazar. Dosya varsa uzerine yazar, yoksa olusturur. Ust klasorler otomatik olusturulur.',
  parameters: {
    type: 'object',
    properties: {
      path: { type: 'string', description: 'Yazilacak dosyanin yolu.' },
      content: { type: 'string', description: 'Dosyaya yazilacak tam icerik.' },
      append: { type: 'boolean', description: 'true ise dosyanin sonuna ekler, uzerine yazmaz.' },
    },
    required: ['path', 'content'],
  },
  async preview(args, ctx) {
    const target = resolvePath(args.path, ctx.config.workspaceRoot);
    const exists = fs.existsSync(target.path);
    const size = Buffer.byteLength(String(args.content ?? ''), 'utf8');
    const what = exists
      ? (args.append ? 'Mevcut dosyanin SONUNA eklenecek' : 'MEVCUT DOSYANIN UZERINE YAZILACAK')
      : 'Yeni dosya olusturulacak';
    return `${target.path}\n${what} (${fmtSize(size)})`;
  },
  async handler(args, ctx) {
    const target = resolvePath(args.path, ctx.config.workspaceRoot);
    await fsp.mkdir(path.dirname(target.path), { recursive: true });
    const content = String(args.content ?? '');
    if (args.append) await fsp.appendFile(target.path, content, 'utf8');
    else await fsp.writeFile(target.path, content, 'utf8');
    const st = await fsp.stat(target.path);
    return `Yazildi: ${target.path} (${fmtSize(st.size)})`;
  },
};

const searchFiles = {
  name: 'search_files',
  risk: 'safe',
  pathArgs: ['path'],
  description:
    'Bir klasor agacinda isim desenine uyan dosyalari arar. Desen ornekleri: "*.txt", "rapor*", "*.{jpg,png}".',
  parameters: {
    type: 'object',
    properties: {
      pattern: { type: 'string', description: 'Dosya adi deseni, ornegin *.txt' },
      path: { type: 'string', description: 'Aramanin baslayacagi klasor. Bos birakilirsa calisma alani koku.' },
      max_depth: { type: 'number', description: 'Kac klasor derinligine inilecek (varsayilan 4).' },
    },
    required: ['pattern'],
  },
  async handler(args, ctx) {
    const root = resolvePath(args.path || ctx.config.workspaceRoot, ctx.config.workspaceRoot);
    const rx = globToRegExp(String(args.pattern).replace(/^\*\*[\\/]/, ''));
    const maxDepth = Math.min(10, Math.max(1, parseInt(args.max_depth, 10) || 4));
    const found = [];

    async function walk(dir, depth) {
      if (depth > maxDepth || found.length >= 200) return;
      let entries;
      try {
        entries = await fsp.readdir(dir, { withFileTypes: true });
      } catch {
        return;
      }
      for (const e of entries) {
        if (found.length >= 200) return;
        const full = path.join(dir, e.name);
        if (e.isDirectory()) {
          if (SKIP_DIRS.has(e.name) || e.name.startsWith('.')) continue;
          await walk(full, depth + 1);
        } else if (rx.test(e.name)) {
          let size = '';
          try { size = fmtSize((await fsp.stat(full)).size); } catch { /* yoksay */ }
          found.push(`${full}${size ? `  (${size})` : ''}`);
        }
      }
    }

    await walk(root.path, 1);
    if (found.length === 0) {
      return `"${args.pattern}" desenine uyan dosya bulunamadi (${root.path} altinda, ${maxDepth} seviye).`;
    }
    return truncate(`${found.length} dosya bulundu:\n` + found.join('\n'));
  },
};

const deletePath = {
  name: 'delete_path',
  risk: 'danger',
  pathArgs: ['path'],
  description: 'Bir dosyayi veya klasoru siler. Geri alinamaz — yalnizca kullanici acikca isterse kullan.',
  parameters: {
    type: 'object',
    properties: {
      path: { type: 'string', description: 'Silinecek dosya veya klasor yolu.' },
      recursive: { type: 'boolean', description: 'Klasoru icerigiyle birlikte silmek icin true.' },
    },
    required: ['path'],
  },
  async preview(args, ctx) {
    const target = resolvePath(args.path, ctx.config.workspaceRoot);
    let kind = 'bulunamadi';
    try {
      kind = (await fsp.stat(target.path)).isDirectory() ? 'KLASOR' : 'dosya';
    } catch { /* yoksay */ }
    return `KALICI OLARAK SILINECEK: ${target.path}\nTur: ${kind}${args.recursive ? '\nIcerigiyle birlikte (recursive)' : ''}`;
  },
  async handler(args, ctx) {
    const target = resolvePath(args.path, ctx.config.workspaceRoot);
    const st = await fsp.stat(target.path);
    if (st.isDirectory()) {
      if (!args.recursive) await fsp.rmdir(target.path);
      else await fsp.rm(target.path, { recursive: true, force: true });
    } else {
      await fsp.unlink(target.path);
    }
    return `Silindi: ${target.path}`;
  },
};

module.exports = [listDir, readFile, writeFile, searchFiles, deletePath];
