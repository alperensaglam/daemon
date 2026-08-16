'use strict';

/**
 * Tek platform dikisi.
 *
 * Uygulamanin geri kalaninda process.platform kontrolu YOKTUR; her dallanma
 * buraya toplanir. Boylece bir platform davranisini degistirmek icin tek dosya
 * okunur ve yeni bir arac eklerken "bunu Windows'a mi yazdim" sorusu dogmaz.
 */

const os = require('os');

const IS_WIN = process.platform === 'win32';
const IS_MAC = process.platform === 'darwin';

/**
 * Isletim sisteminin kendi surum numarasi ("26.5.1", "10.0.26100").
 *
 * os.release() macOS'ta Darwin cekirdek surumunu verir (25.5.0), urun surumunu
 * degil. Ikisi arasinda aritmetik bir iliski kurmak cazip ama YANLIS: Apple
 * macOS 15'ten 26'ya atladi, yani eski "Darwin - 9" kurali kirildi.
 * process.getSystemVersion() Electron'un verdigi gercek degerdir.
 */
function systemVersion() {
  try {
    return process.getSystemVersion?.() || os.release();
  } catch {
    return os.release();
  }
}

const OS_LABEL = IS_WIN
  ? `Windows (${systemVersion()})`
  : IS_MAC
    ? `macOS (${systemVersion()})`
    : `${os.type()} (${os.release()})`;

/**
 * Komut calistirmak icin kullanilacak kabuk.
 *
 * macOS'ta login shell (-l) SART: Finder'dan acilan paketlenmis bir .app
 * minimal bir PATH devralir (/usr/bin:/bin:/usr/sbin:/sbin). Login shell
 * olmadan brew, node, python3 gibi her sey "command not found" verir.
 * Bu hata terminalden `npm start` ile test ederken GORUNMEZ; yalnizca
 * paketlendikten sonra ortaya cikar.
 */
function shellSpec() {
  if (IS_WIN) {
    return {
      bin: 'powershell.exe',
      args: (command) => [
        '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', command,
      ],
      label: 'PowerShell',
    };
  }
  return {
    bin: process.env.SHELL || '/bin/zsh',
    args: (command) => ['-lc', command],
    label: IS_MAC ? 'zsh' : 'sh',
  };
}

/**
 * Bir degeri kabuga guvenle gecirilebilecek tek tirnakli dize literaline cevirir.
 *
 * Kacis kurallari iki platformda FARKLIDIR, bu yuzden tek bir implementasyon
 * paylasilamaz:
 *   PowerShell -> tek tirnak icinde hicbir sey genisletilmez, tek kacis
 *                 tirnagi ikilemektir ('' ).
 *   POSIX      -> tek tirnak icinde tirnak kacisi yoktur; dizeyi kapatip
 *                 kacisli bir tirnak koyup yeniden acmak gerekir ('\'').
 *
 * JSON.stringify KULLANMA: her iki kabukta da cift tirnak icinde genisletme
 * yapilir ($ ve ` ile alt-ifade calisir), yani enjeksiyona acik olur.
 */
function quoteArg(value) {
  const s = String(value);
  if (IS_WIN) return `'${s.replace(/'/g, "''")}'`;
  return `'${s.replace(/'/g, "'\\''")}'`;
}

/**
 * On plandaki uygulamanin/pencerenin adi.
 *
 * macOS'ta `lsappinfo` kullanilir cunku **Erisilebilirlik izni istemez**;
 * `osascript ... System Events` ayni bilgiyi verir ama TCC onayina takilir ve
 * izin yokken sessizce bos doner. Windows'ta on plan penceresi P/Invoke ile
 * sorulur.
 *
 * En-iyi-caba bir bilgidir: cozulemezse bos dize doner, cagiran buna gore
 * davranmalidir.
 *
 * @returns {Promise<{app: string, title: string}>}
 */
async function activeWindow() {
  const { execFile } = require('child_process');
  const { promisify } = require('util');
  const run = promisify(execFile);
  const opts = { timeout: 5000, windowsHide: true };

  try {
    if (IS_MAC) {
      const { stdout: asn } = await run('lsappinfo', ['front'], opts);
      if (!asn.trim()) return { app: '', title: '' };
      const { stdout } = await run(
        'lsappinfo', ['info', '-only', 'name', asn.trim()], opts
      );
      // Bicim: "LSDisplayName"="Terminal"
      const match = stdout.match(/"LSDisplayName"\s*=\s*"([^"]*)"/);
      return { app: match ? match[1] : '', title: '' };
    }

    if (IS_WIN) {
      const script = [
        'Add-Type @"',
        'using System;using System.Runtime.InteropServices;using System.Text;',
        'public class Fg{',
        '[DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();',
        '[DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h,StringBuilder s,int n);',
        '[DllImport("user32.dll")] public static extern int GetWindowThreadProcessId(IntPtr h,out int p);}',
        '"@',
        '$h=[Fg]::GetForegroundWindow()',
        '$sb=New-Object System.Text.StringBuilder 512',
        '[void][Fg]::GetWindowText($h,$sb,512)',
        '$procId=0; [void][Fg]::GetWindowThreadProcessId($h,[ref]$procId)',
        '$name=(Get-Process -Id $procId -ErrorAction SilentlyContinue).ProcessName',
        '"$name`t$($sb.ToString())"',
      ].join('\n');
      const { stdout } = await run(
        'powershell.exe',
        ['-NoProfile', '-NonInteractive', '-Command', script],
        opts
      );
      const [app = '', title = ''] = stdout.trim().split('\t');
      return { app, title };
    }
  } catch {
    // Izin, zaman asimi veya arac yoklugu — bilgi zorunlu degil.
  }
  return { app: '', title: '' };
}

module.exports = { IS_WIN, IS_MAC, OS_LABEL, shellSpec, quoteArg, activeWindow };
