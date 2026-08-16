'use strict';

const os = require('os');
const { shell } = require('electron');
const { execFile } = require('child_process');
const { promisify } = require('util');
const { resolvePath, expand, truncate } = require('../../security');
const { IS_WIN, quoteArg } = require('../../platform');

const execFileAsync = promisify(execFile);

const EXEC_OPTS = { windowsHide: true, timeout: 20_000, maxBuffer: 8 * 1024 * 1024 };

/**
 * PowerShell komutu calistirir. Yalnizca Windows dallarindan cagrilir.
 * Argumanlar quoteArg ile tirnaklanir; kacis kurallari platforma gore degisir
 * (bkz. platform.js) — bu yuzden tirnaklama burada elle yapilmaz.
 */
function ps(command) {
  return execFileAsync(
    'powershell.exe',
    ['-NoProfile', '-NonInteractive', '-Command', command],
    EXEC_OPTS
  );
}

/** Bir araci dogrudan calistirir; arada kabuk yoktur, yani tirnaklama gerekmez. */
function run(bin, args) {
  return execFileAsync(bin, args, EXEC_OPTS);
}

const APP_EXAMPLES = IS_WIN
  ? '"C:\\Users\\...\\rapor.pdf", "https://google.com", "notepad", "calc"'
  : '"/Users/.../rapor.pdf", "https://google.com", "Safari", "Hesap Makinesi"';

const openPath = {
  name: 'open_path',
  risk: 'write',
  pathArgs: [],
  description:
    `Bir dosyayi, klasoru, uygulamayi veya web adresini varsayilan programla acar. Ornek hedefler: ${APP_EXAMPLES}.`,
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
    if (IS_WIN) {
      await ps(`Start-Process ${quoteArg(expanded)}`);
    } else {
      // -a uygulamayi ada gore arar (.app uzantisi ve /Applications yolu gerekmez).
      await run('open', ['-a', expanded]);
    }
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

    if (IS_WIN) {
      const filter = args.filter
        ? `| Where-Object { $_.ProcessName -like ${quoteArg('*' + args.filter + '*')} }`
        : '';
      const cmd = `Get-Process ${filter} | Sort-Object WorkingSet64 -Descending | Select-Object -First ${limit} ProcessName, Id, @{n='RAM_MB';e={[math]::Round($_.WorkingSet64/1MB,1)}} | Format-Table -AutoSize | Out-String -Width 120`;
      const { stdout } = await ps(cmd);
      return truncate(stdout.trim() || 'Eslesen surec bulunamadi.');
    }

    // ps ciktisi biciminden bagimsiz kalmak icin siralama/kesme JS tarafinda yapilir.
    // rss kilobayt cinsindendir.
    const { stdout } = await run('ps', ['-Ao', 'pid=,rss=,comm=']);
    const needle = String(args.filter || '').toLowerCase();

    const rows = stdout.split('\n')
      .map((line) => {
        const m = line.match(/^\s*(\d+)\s+(\d+)\s+(.*)$/);
        if (!m) return null;
        const command = m[3].trim();
        // comm tam yol verir; okunabilirlik icin son bileseni goster.
        const name = command.split('/').pop() || command;
        return { pid: parseInt(m[1], 10), ramMb: parseInt(m[2], 10) / 1024, name };
      })
      .filter((r) => r && (!needle || r.name.toLowerCase().includes(needle)))
      .sort((a, b) => b.ramMb - a.ramMb)
      .slice(0, limit);

    if (!rows.length) return 'Eslesen surec bulunamadi.';

    const nameWidth = Math.max(4, ...rows.map((r) => r.name.length));
    const header = `${'Ad'.padEnd(nameWidth)}  ${'PID'.padStart(7)}  ${'RAM_MB'.padStart(8)}`;
    const lines = rows.map(
      (r) => `${r.name.padEnd(nameWidth)}  ${String(r.pid).padStart(7)}  ${r.ramMb.toFixed(1).padStart(8)}`
    );
    return truncate([header, '-'.repeat(header.length), ...lines].join('\n'));
  },
};

/** PID'in surec adini dondurur; bulunamazsa null. */
async function processName(pid) {
  try {
    if (IS_WIN) {
      const { stdout } = await ps(`(Get-Process -Id ${pid} -ErrorAction Stop).ProcessName`);
      return stdout.trim() || null;
    }
    const { stdout } = await run('ps', ['-p', String(pid), '-o', 'comm=']);
    const name = stdout.trim();
    return name ? (name.split('/').pop() || name) : null;
  } catch {
    return null;
  }
}

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

    // Ad, oldurmeden ONCE okunmali; sonra surec artik yoktur.
    const name = await processName(pid);
    if (!name) throw new Error(`PID ${pid} bulunamadi.`);

    // Node'un kendi cagrisi iki platformda da calisir (Windows'ta
    // TerminateProcess'e maplenir), ayri kabuk yollari yazmaya gerek yok.
    process.kill(pid);
    return `Sonlandirildi: ${name} (${pid})`;
  },
};

/** Disk doluluk tablosu; alinamazsa bos dize. */
async function diskInfo() {
  try {
    if (IS_WIN) {
      const { stdout } = await ps(
        `Get-PSDrive -PSProvider FileSystem | Where-Object { $_.Used -ne $null } | Select-Object Name, @{n='Kullanilan_GB';e={[math]::Round($_.Used/1GB,1)}}, @{n='Bos_GB';e={[math]::Round($_.Free/1GB,1)}} | Format-Table -AutoSize | Out-String -Width 100`
      );
      return stdout.trim();
    }
    // -H: 1000 tabanli okunabilir birimler. Yalnizca gercek dosya sistemleri.
    const { stdout } = await run('df', ['-H', '-t', 'apfs,hfs,exfat,msdos']);

    // macOS bir APFS kabini bir sure yardimci birim olarak baglar (VM, Preboot,
    // Update, xarts...). Bunlar kullaniciyi ilgilendirmez ve ciktinin yarisini
    // kaplar; ayrica df'in son sutunlari (inode sayaclari) LLM icin gurultudur.
    const rows = stdout.split('\n').slice(1)
      .map((line) => line.trim().split(/\s+/))
      .filter((c) => c.length >= 9 && !/^\/System\/Volumes\/(VM|Preboot|Update|xarts|iSCPreboot|Hardware)$/.test(c.slice(8).join(' ')))
      .map((c) => ({ mount: c.slice(8).join(' '), size: c[1], used: c[2], avail: c[3], pct: c[4] }));

    if (!rows.length) return '';

    const mountWidth = Math.max(7, ...rows.map((r) => r.mount.length));
    const header = `${'Baglanti'.padEnd(mountWidth)}  ${'Boyut'.padStart(7)}  ${'Kullanilan'.padStart(10)}  ${'Bos'.padStart(7)}  ${'Doluluk'.padStart(7)}`;
    const lines = rows.map(
      (r) => `${r.mount.padEnd(mountWidth)}  ${r.size.padStart(7)}  ${r.used.padStart(10)}  ${r.avail.padStart(7)}  ${r.pct.padStart(7)}`
    );
    return [header, '-'.repeat(header.length), ...lines].join('\n');
  } catch {
    return '';
  }
}

/** Pil durumu; pil yoksa bos dize. */
async function batteryInfo() {
  try {
    if (IS_WIN) {
      const { stdout } = await ps(
        `$b = Get-CimInstance Win32_Battery -ErrorAction SilentlyContinue; if ($b) { "Pil: %$($b.EstimatedChargeRemaining)" }`
      );
      return stdout.trim();
    }
    const { stdout } = await run('pmset', ['-g', 'batt']);
    // Ornek satir: " -InternalBattery-0 (id=...)	82%; discharging; 4:13 remaining present: true"
    const m = stdout.match(/(\d+)%;\s*([^;]+)/);
    if (!m) return '';
    const states = { charging: 'sarj oluyor', discharging: 'kullanimda', charged: 'dolu', finishing: 'sarj tamamlaniyor' };
    const state = states[m[2].trim()] || m[2].trim();
    return `Pil: %${m[1]} (${state})`;
  } catch {
    return '';
  }
}

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

    const disks = await diskInfo();
    if (disks) lines.push('\nDiskler:\n' + disks);

    const battery = await batteryInfo();
    if (battery) lines.push(battery);

    return truncate(lines.join('\n'));
  },
};

module.exports = [openPath, listProcesses, killProcess, systemInfo];
