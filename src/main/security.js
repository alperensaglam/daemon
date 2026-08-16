'use strict';

const path = require('path');
const fs = require('fs');
const os = require('os');
const { IS_WIN } = require('./platform');

const LIMITS = {
  readFileBytes: 200 * 1024,
  commandOutputBytes: 100 * 1024,
  commandTimeoutMs: 60_000,
  fetchBytes: 60 * 1024,
  toolResultChars: 30_000,
};

// Yuksek riskli komut desenleri — otomatik onay modunda bile kullanici onayi ister.
//
// Desenler kabuk sozluguyle yazildigi icin platforma gore ayrilir: PowerShell
// listesi macOS'ta hicbir zaman eslesmez, dolayisiyla tek liste kullanilirsa
// macOS'ta pratikte HIC koruma kalmaz.

// Her iki kabukta da ayni yazilan komutlar.
const COMMON_PATTERNS = [
  { re: /\brm\s+(-\w*[rf]\w*\s+)+/i, why: 'zorla/ozyinelemeli silme' },
  { re: /\bshutdown\b/i, why: 'bilgisayari kapatiyor/yeniden baslatiyor' },
  { re: /\b(curl|wget)\b[^|;]*\|\s*(ba|z|)sh\b/i, why: 'internetten indirip calistiriyor' },
];

const WIN_PATTERNS = [
  { re: /\bRemove-Item\b[^|;]*(-Recurse|-r\b)/i, why: 'klasoru icerigiyle birlikte siliyor' },
  { re: /\brmdir\b[^|;]*\/s/i, why: 'klasoru ozyinelemeli siliyor' },
  { re: /\bdel\b[^|;]*\/s/i, why: 'dosyalari ozyinelemeli siliyor' },
  { re: /\bformat(-volume)?\b/i, why: 'disk bicimlendirme' },
  { re: /\bdiskpart\b/i, why: 'disk bolumleme araci' },
  { re: /\bClear-Disk\b/i, why: 'diski temizliyor' },
  { re: /\breg(\.exe)?\s+delete\b/i, why: 'kayit defteri silme' },
  { re: /\bRemove-ItemProperty\b/i, why: 'kayit defteri degeri silme' },
  { re: /\b(Stop-Computer|Restart-Computer)\b/i, why: 'bilgisayari kapatiyor/yeniden baslatiyor' },
  { re: /\bStop-Process\b[^|;]*-Force/i, why: 'surecleri zorla sonlandiriyor' },
  { re: /\bSet-ExecutionPolicy\b/i, why: 'betik guvenlik politikasini degistiriyor' },
  { re: /\b(Invoke-Expression|iex)\b/i, why: 'indirilen kodu calistirabilir' },
  { re: /\b(Invoke-WebRequest|curl|wget)\b[^|;]*\|\s*(iex|Invoke-Expression)/i, why: 'internetten indirip calistiriyor' },
  { re: /\bnetsh\b/i, why: 'ag ayarlarini degistiriyor' },
  { re: /\bbcdedit\b/i, why: 'onyukleme ayarlarini degistiriyor' },
  { re: /\bcipher\b[^|;]*\/w/i, why: 'disk uzerine yaziyor' },
  { re: /\bvssadmin\b[^|;]*delete/i, why: 'geri yukleme noktalarini siliyor' },
];

const MAC_PATTERNS = [
  { re: /\bsudo\b/i, why: 'yonetici yetkisiyle calisiyor' },
  { re: /\bdiskutil\b/i, why: 'disk bolumleme araci' },
  { re: /\bdd\b[^|;]*\bof=/i, why: 'diske ham veri yaziyor' },
  { re: /\bmkfs\b|\bnewfs(_\w+)?\b/i, why: 'disk bicimlendirme' },
  { re: /\bchmod\b[^|;]*-\w*R/i, why: 'ozyinelemeli izin degisikligi' },
  { re: /\bchown\b[^|;]*-\w*R/i, why: 'ozyinelemeli sahiplik degisikligi' },
  { re: /\blaunchctl\b/i, why: 'sistem servislerini degistiriyor' },
  { re: /\bkillall\b/i, why: 'surecleri ada gore topluca sonlandiriyor' },
  { re: /\bcsrutil\b/i, why: 'sistem butunlugu korumasini degistiriyor' },
  { re: /\bspctl\b[^|;]*--master-disable/i, why: 'Gatekeeper korumasini kapatiyor' },
  { re: /\bnvram\b/i, why: 'onyukleme degiskenlerini degistiriyor' },
  { re: /\bdefaults\s+delete\b/i, why: 'uygulama ayarlarini siliyor' },
  { re: />\s*\/dev\/\w+/i, why: 'aygita dogrudan yaziyor' },
  { re: /\bpmset\b/i, why: 'guc yonetimi ayarlarini degistiriyor' },
  { re: /\btmutil\s+(delete|disable)\b/i, why: 'Time Machine yedeklerini siliyor/kapatiyor' },
];

const DESTRUCTIVE_PATTERNS = [
  ...COMMON_PATTERNS,
  ...(IS_WIN ? WIN_PATTERNS : MAC_PATTERNS),
];

// Sistem icin kritik, her durumda yazma yasagi olan kokler.
//
// Windows listesi yalnizca Windows'ta uretilir: path.resolve('C:\\Windows')
// macOS'ta "<cwd>/C:\Windows" gibi cop bir yol dondurur ve gercek bir goreceli
// klasoru golgeleyebilirdi.
function buildProtectedRoots() {
  const roots = IS_WIN
    ? [
      process.env.SystemRoot || 'C:\\Windows',
      process.env.ProgramFiles || 'C:\\Program Files',
      process.env['ProgramFiles(x86)'] || 'C:\\Program Files (x86)',
    ]
    : [
      '/System', '/usr', '/bin', '/sbin', '/Library', '/Applications',
      '/private/etc', '/private/var/db', '/Volumes',
      path.join(os.homedir(), 'Library'),
    ];
  return roots.filter(Boolean).map((p) => path.resolve(p).toLowerCase());
}

const PROTECTED_ROOTS = buildProtectedRoots();

function expand(p) {
  if (!p) return p;
  let out = String(p).trim().replace(/^["']|["']$/g, '');
  if (out.startsWith('~')) out = path.join(os.homedir(), out.slice(1));
  // Windows sozdizimi (%VAR%) ve POSIX sozdizimi ($VAR / ${VAR}) birlikte
  // desteklenir; her ikisi de diger platformda zararsizca eslesmez.
  out = out.replace(/%([^%]+)%/g, (m, name) => process.env[name] ?? m);
  out = out.replace(/\$\{([A-Za-z_][A-Za-z0-9_]*)\}/g, (m, name) => process.env[name] ?? m);
  out = out.replace(/\$([A-Za-z_][A-Za-z0-9_]*)/g, (m, name) => process.env[name] ?? m);
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
