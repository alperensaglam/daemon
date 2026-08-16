'use strict';

const { spawn } = require('child_process');
const { resolvePath, LIMITS, truncate } = require('../../security');
const { shellSpec } = require('../../platform');

const SHELL = shellSpec();

function runShell(command, cwd, signal) {
  return new Promise((resolve) => {
    const child = spawn(
      SHELL.bin,
      SHELL.args(command),
      { cwd, windowsHide: true, signal }
    );

    let stdout = '';
    let stderr = '';
    let truncated = false;
    let finished = false;

    const cap = (chunk, target) => {
      if (target.length >= LIMITS.commandOutputBytes) { truncated = true; return target; }
      return target + chunk.toString('utf8');
    };

    child.stdout.on('data', (c) => { stdout = cap(c, stdout); });
    child.stderr.on('data', (c) => { stderr = cap(c, stderr); });

    const timer = setTimeout(() => {
      if (!finished) {
        try { child.kill(); } catch { /* yoksay */ }
        finished = true;
        resolve({ code: -1, stdout, stderr: stderr + `\n[Zaman asimi: ${LIMITS.commandTimeoutMs / 1000}s]`, truncated });
      }
    }, LIMITS.commandTimeoutMs);

    child.on('error', (err) => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      resolve({ code: -1, stdout, stderr: `${stderr}\n${err.message}`, truncated });
    });

    child.on('close', (code) => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      resolve({ code, stdout, stderr, truncated });
    });
  });
}

const runCommand = {
  name: 'run_command',
  risk: 'danger',
  pathArgs: ['cwd'],
  // Aciklamalar LLM'in arac semasina gidiyor. Sabit "PowerShell" yazilsaydi
  // model macOS'ta da Get-ChildItem uretmeye devam ederdi.
  description:
    `${SHELL.label} komutu calistirir ve ciktisini dondurur. Dosya okuma/yazma icin oncelikle read_file ve write_file araclarini tercih et; bunu sistem sorgulari, program calistirma ve baska aracin karsilamadigi isler icin kullan.`,
  parameters: {
    type: 'object',
    properties: {
      command: { type: 'string', description: `Calistirilacak ${SHELL.label} komutu.` },
      cwd: { type: 'string', description: 'Komutun calisacagi klasor. Bos birakilirsa calisma alani koku.' },
    },
    required: ['command'],
  },
  async preview(args, ctx) {
    const cwd = resolvePath(args.cwd || ctx.config.workspaceRoot, ctx.config.workspaceRoot);
    return `${SHELL.label} komutu calistirilacak\nKlasor: ${cwd.path}\n\n${args.command}`;
  },
  async handler(args, ctx) {
    const cwd = resolvePath(args.cwd || ctx.config.workspaceRoot, ctx.config.workspaceRoot);
    const res = await runShell(String(args.command), cwd.path, ctx.signal);

    const parts = [`Cikis kodu: ${res.code}`];
    if (res.stdout.trim()) parts.push(`--- STDOUT ---\n${res.stdout.trim()}`);
    if (res.stderr.trim()) parts.push(`--- STDERR ---\n${res.stderr.trim()}`);
    if (!res.stdout.trim() && !res.stderr.trim()) parts.push('(cikti yok)');
    if (res.truncated) parts.push('[Cikti boyut sinirinda kesildi]');
    return truncate(parts.join('\n\n'));
  },
};

module.exports = [runCommand];
