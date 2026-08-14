'use strict';

const os = require('os');
const { shell } = require('electron');
const { execFile } = require('child_process');
const { promisify } = require('util');
const { resolvePath, expand, truncate } = require('../../security');

const execFileAsync = promisify(execFile);

function ps(command) {
  return execFileAsync(
    'powershell.exe',
    ['-NoProfile', '-NonInteractive', '-Command', command],
    { windowsHide: true, timeout: 20_000, maxBuffer: 8 * 1024 * 1024 }
  );
}

/**
 * PowerShell tek tirnakli dize literali uretir.
 * JSON.stringify KULLANMA: PowerShell cift tirnak icinde ters bolu escape degildir
 * ve $ isareti alt-ifade calistirir ($(...)), yani enjeksiyona acik olur.
 * Tek tirnak icinde hicbir sey genisletilmez; tek kacis, tirnagi ikilemektir.
 */
function psQuote(value) {
  return `'${String(value).replace(/'/g, "''")}'`;
}

const openPath = {
  name: 'open_path',
  risk: 'write',
  pathArgs: [],
  description:
    'Bir dosyayi, klasoru, uygulamayi veya web adresini varsayilan programla acar. Ornek hedefler: "C:\\Users\\...\\rapor.pdf", "https://google.com", "notepad", "calc".',
  parameters: {
    type: 'object',
    properties: {
      target: { type: 'string', description: 'Acilacak dosya yolu, klasor, URL veya uygulama adi.' },
    },
    required: ['target'],
  },
  async preview(args) {
    return `Acilacak: ${args.target}`;
  },
  async handler(args, ctx) {
    const raw = String(args.target || '').trim();
    if (!raw) throw new Error('Hedef bos olamaz.');

    if (/^https?:\/\//i.test(raw)) {
      await shell.openExternal(raw);
      return `Tarayicida acildi: ${raw}`;
    }

    const expanded = expand(raw);

    // Dosya/klasor mu?
    const looksLikePath = /[\\/]/.test(expanded) || /^[a-z]:/i.test(expanded) || /\.[a-z0-9]{1,5}$/i.test(expanded);
    if (looksLikePath) {
      let target;
      try {
        target = resolvePath(expanded, ctx.config.workspaceRoot);
      } catch {
        target = null;
      }
      if (target) {
        const err = await shell.openPath(target.path);
        if (!err) return `Acildi: ${target.path}`;
        // Acilmadiysa uygulama adi olarak denemeye devam et
      }
    }

    // Uygulama adi olarak calistir
    await ps(`Start-Process ${psQuote(expanded)}`);
    return `Baslatildi: ${expanded}`;
  },
};

const listProcesses = {
  name: 'list_processes',
  risk: 'safe',
  pathArgs: [],
  description: 'Calisan surecleri RAM kullanimina gore siralayarak listeler (ad, PID, bellek).',
  parameters: {
    type: 'object',
    properties: {
      filter: { type: 'string', description: 'Surec adinda aranacak metin (istege bagli).' },
      limit: { type: 'number', description: 'Kac surec listelensin (varsayilan 20).' },
    },
  },
  async handler(args) {
    const limit = Math.min(100, Math.max(1, parseInt(args.limit, 10) || 20));
    const filter = args.filter ? `| Where-Object { $_.ProcessName -like ${psQuote('*' + args.filter + '*')} }` : '';
    const cmd = `Get-Process ${filter} | Sort-Object WorkingSet64 -Descending | Select-Object -First ${limit} ProcessName, Id, @{n='RAM_MB';e={[math]::Round($_.WorkingSet64/1MB,1)}} | Format-Table -AutoSize | Out-String -Width 120`;
    const { stdout } = await ps(cmd);
    return truncate(stdout.trim() || 'Eslesen surec bulunamadi.');
  },
};

const killProcess = {
  name: 'kill_process',
  risk: 'danger',
  pathArgs: [],
  description: 'Bir sureci PID ile sonlandirir. Kaydedilmemis veriler kaybolabilir.',
  parameters: {
    type: 'object',
    properties: {
      pid: { type: 'number', description: 'Sonlandirilacak surecin PID degeri.' },
    },
    required: ['pid'],
  },
  async preview(args) {
    return `PID ${args.pid} sonlandirilacak. Kaydedilmemis veriler kaybolabilir.`;
  },
  async handler(args) {
    const pid = parseInt(args.pid, 10);
    if (!Number.isInteger(pid) || pid <= 0) throw new Error('Gecerli bir PID gerekli.');
    const { stdout } = await ps(`$p = Get-Process -Id ${pid} -ErrorAction Stop; $n = $p.ProcessName; Stop-Process -Id ${pid} -Force; "Sonlandirildi: $n ($([string]${pid}))"`);
    return stdout.trim();
  },
};

const systemInfo = {
  name: 'system_info',
  risk: 'safe',
  pathArgs: [],
  description: 'Bilgisayarin durumunu dondurur: isletim sistemi, CPU, RAM kullanimi, disk doluluk, calisma suresi.',
  parameters: { type: 'object', properties: {} },
  async handler() {
    const lines = [];
    lines.push(`Kullanici: ${os.userInfo().username}`);
    lines.push(`Bilgisayar: ${os.hostname()}`);
    lines.push(`Isletim sistemi: ${os.type()} ${os.release()} (${os.arch()})`);
    lines.push(`CPU: ${os.cpus()[0]?.model?.trim() || 'bilinmiyor'} — ${os.cpus().length} mantiksal cekirdek`);

    const totalGb = os.totalmem() / 1024 ** 3;
    const freeGb = os.freemem() / 1024 ** 3;
    lines.push(`RAM: ${(totalGb - freeGb).toFixed(1)} / ${totalGb.toFixed(1)} GB kullanimda (%${Math.round(((totalGb - freeGb) / totalGb) * 100)})`);

    const up = os.uptime();
    lines.push(`Calisma suresi: ${Math.floor(up / 3600)} saat ${Math.floor((up % 3600) / 60)} dakika`);
    lines.push(`Tarih/saat: ${new Date().toLocaleString('tr-TR')}`);

    try {
      const { stdout } = await ps(
        `Get-PSDrive -PSProvider FileSystem | Where-Object { $_.Used -ne $null } | Select-Object Name, @{n='Kullanilan_GB';e={[math]::Round($_.Used/1GB,1)}}, @{n='Bos_GB';e={[math]::Round($_.Free/1GB,1)}} | Format-Table -AutoSize | Out-String -Width 100`
      );
      if (stdout.trim()) lines.push('\nDiskler:\n' + stdout.trim());
    } catch { /* disk bilgisi alinamadi */ }

    try {
      const { stdout } = await ps(
        `$b = Get-CimInstance Win32_Battery -ErrorAction SilentlyContinue; if ($b) { "Pil: %$($b.EstimatedChargeRemaining)" }`
      );
      if (stdout.trim()) lines.push(stdout.trim());
    } catch { /* pil yok */ }

    return truncate(lines.join('\n'));
  },
};

module.exports = [openPath, listProcesses, killProcess, systemInfo];
