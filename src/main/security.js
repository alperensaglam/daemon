'use strict';

const path = require('path');
const fs = require('fs');
const os = require('os');

const LIMITS = {
  readFileBytes: 200 * 1024,
  commandOutputBytes: 100 * 1024,
  commandTimeoutMs: 60_000,
  fetchBytes: 60 * 1024,
  toolResultChars: 30_000,
};

// Yuksek riskli komut desenleri — otomatik onay modunda bile kullanici onayi ister.
const DESTRUCTIVE_PATTERNS = [
  { re: /\bRemove-Item\b[^|;]*(-Recurse|-r\b)/i, why: 'klasoru icerigiyle birlikte siliyor' },
  { re: /\brmdir\b[^|;]*\/s/i, why: 'klasoru ozyinelemeli siliyor' },
  { re: /\bdel\b[^|;]*\/s/i, why: 'dosyalari ozyinelemeli siliyor' },
  { re: /\brm\s+(-\w*[rf]\w*\s+)+/i, why: 'zorla/ozyinelemeli silme' },
  { re: /\bformat(-volume)?\b/i, why: 'disk bicimlendirme' },
  { re: /\bdiskpart\b/i, why: 'disk bolumleme araci' },
  { re: /\bClear-Disk\b/i, why: 'diski temizliyor' },
  { re: /\breg(\.exe)?\s+delete\b/i, why: 'kayit defteri silme' },
  { re: /\bRemove-ItemProperty\b/i, why: 'kayit defteri degeri silme' },
  { re: /\b(shutdown|Stop-Computer|Restart-Computer)\b/i, why: 'bilgisayari kapatiyor/yeniden baslatiyor' },
  { re: /\bStop-Process\b[^|;]*-Force/i, why: 'surecleri zorla sonlandiriyor' },
  { re: /\bSet-ExecutionPolicy\b/i, why: 'betik guvenlik politikasini degistiriyor' },
  { re: /\b(Invoke-Expression|iex)\b/i, why: 'indirilen kodu calistirabilir' },
  { re: /\b(Invoke-WebRequest|curl|wget)\b[^|;]*\|\s*(iex|Invoke-Expression)/i, why: 'internetten indirip calistiriyor' },
  { re: /\bnetsh\b/i, why: 'ag ayarlarini degistiriyor' },
  { re: /\bbcdedit\b/i, why: 'onyukleme ayarlarini degistiriyor' },
  { re: /\bcipher\b[^|;]*\/w/i, why: 'disk uzerine yaziyor' },
  { re: /\bvssadmin\b[^|;]*delete/i, view: true, why: 'geri yukleme noktalarini siliyor' },
];

// Sistem icin kritik, her durumda yazma yasagi olan kokler.
const PROTECTED_ROOTS = [
  path.join(process.env.SystemRoot || 'C:\\Windows'),
  path.join(process.env.ProgramFiles || 'C:\\Program Files'),
  process.env['ProgramFiles(x86)'] || 'C:\\Program Files (x86)',
].filter(Boolean).map((p) => path.resolve(p).toLowerCase());

function expand(p) {
  if (!p) return p;
  let out = String(p).trim().replace(/^["']|["']$/g, '');
  if (out.startsWith('~')) out = path.join(os.homedir(), out.slice(1));
  out = out.replace(/%([^%]+)%/g, (m, name) => process.env[name] ?? m);
  return out;
}

/**
 * Bir yolu cozer ve workspace koku icinde mi diye bakar.
 * Symlink kacislarini yakalamak icin var olan en yakin ust klasor uzerinden realpath alinir.
 */
function resolvePath(input, workspaceRoot) {
  const raw = expand(input);
  if (!raw) throw new Error('Yol bos olamaz.');

  const root = path.resolve(expand(workspaceRoot));
  const abs = path.isAbsolute(raw) ? path.resolve(raw) : path.resolve(root, raw);

  const real = realpathNearest(abs);
  const realRoot = realpathNearest(root);

  const inside =
    real.toLowerCase() === realRoot.toLowerCase() ||
    real.toLowerCase().startsWith(realRoot.toLowerCase() + path.sep);

  const protectedHit = PROTECTED_ROOTS.some(
    (r) => real.toLowerCase() === r || real.toLowerCase().startsWith(r + path.sep)
  );

  return { path: abs, real, inside, protected: protectedHit, root: realRoot };
}

function realpathNearest(p) {
  let cur = path.resolve(p);
  const tail = [];
  for (let i = 0; i < 64; i++) {
    try {
      return path.join(fs.realpathSync.native(cur), ...tail.reverse());
    } catch {
      const parent = path.dirname(cur);
      if (parent === cur) return path.resolve(p);
      tail.push(path.basename(cur));
      cur = parent;
    }
  }
  return path.resolve(p);
}

/**
 * Bir arac cagrisi icin efektif riski hesaplar.
 * Donen: { risk: 'safe'|'write'|'danger', reasons: string[], forceAsk: boolean }
 */
function assessRisk(tool, args, config) {
  const reasons = [];
  let risk = tool.risk;
  let forceAsk = false;

  // Yol iceren argumanlari kontrol et
  for (const key of tool.pathArgs || []) {
    const value = args?.[key];
    if (!value) continue;
    let info;
    try {
      info = resolvePath(value, config.workspaceRoot);
    } catch {
      continue;
    }
    if (info.protected) {
      risk = 'danger';
      forceAsk = true;
      reasons.push(`"${info.path}" korumali bir sistem klasorunde`);
    } else if (!info.inside) {
      if (risk === 'safe') risk = 'write';
      else risk = 'danger';
      forceAsk = true;
      reasons.push(`"${info.path}" calisma alaninin (${info.root}) disinda`);
    }
  }

  // Komut icerigini tara
  if (typeof args?.command === 'string') {
    for (const { re, why } of DESTRUCTIVE_PATTERNS) {
      if (re.test(args.command)) {
        risk = 'danger';
        forceAsk = true;
        reasons.push(`komut ${why}`);
      }
    }
  }

  return { risk, reasons, forceAsk };
}

/**
 * Onay gerekiyor mu?
 * permissionMode: 'readonly' -> yalnizca safe calisir, digerleri reddedilir
 *                 'ask'      -> safe otomatik, write/danger onay ister
 *                 'auto'     -> danger disi otomatik; forceAsk yine de sorar
 */
function needsApproval(assessment, permissionMode) {
  if (assessment.forceAsk) return { approvalRequired: true, blocked: false };

  if (permissionMode === 'readonly') {
    if (assessment.risk === 'safe') return { approvalRequired: false, blocked: false };
    return { approvalRequired: false, blocked: true };
  }

  if (permissionMode === 'auto') {
    return { approvalRequired: assessment.risk === 'danger', blocked: false };
  }

  // 'ask'
  return { approvalRequired: assessment.risk !== 'safe', blocked: false };
}

function truncate(text, max = LIMITS.toolResultChars) {
  const s = String(text ?? '');
  if (s.length <= max) return s;
  return s.slice(0, max) + `\n\n[... ${s.length - max} karakter kesildi ...]`;
}

module.exports = {
  LIMITS,
  expand,
  resolvePath,
  assessRisk,
  needsApproval,
  truncate,
  DESTRUCTIVE_PATTERNS,
};
