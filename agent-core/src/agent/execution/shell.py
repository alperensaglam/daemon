"""Kabuk şeridinin motoru: PowerShell / bash çağrısı.

Hibrit yürütmenin (bkz. ``router.py``) CLI tarafı. Tasarım kararları küçük ama
her biri gerçek bir başarısızlık biçimini kapatıyor:

* **stdin kapalı.** İnteraktif bir komut (``vim``, parola soran ``sudo``,
  ``git rebase -i``) kapalı stdin ile hemen düşer. Açık bırakılsaydı zaman
  aşımına kadar asılır ve agent 20 saniyesini hiçbir şey yapmadan harcardı.
* **Sayfalayıcılar kapalı.** ``git log`` varsayılan olarak ``less`` açar ve
  TTY'siz ortamda bile ANSI kaçış dizileriyle çıktı üretir. ``GIT_PAGER=cat`` +
  ``TERM=dumb`` bunu keser; aksi halde modelin bağlamına kaçış karakterleri
  dolar.
* **Çıktı kırpılır, ortası atılır.** Bir ``ls -R`` çıktısı 200 KB olabilir;
  bağlama sığmaz. Baş ve son korunur çünkü hata mesajları genellikle sonda,
  komutun ne yaptığı başta olur.
* **Engellenen kalıplar onaydan bağımsızdır.** ``ApprovalGate`` "sorulacak"
  şeyleri yönetir; buradaki liste "hiç sorulmayacak" olanları tutar. İkisi
  ayrı kavramdır: biri tercih, diğeri sınır.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable

from ..core.errors import ShellCommandBlocked

# --------------------------------------------------------------------------- #
#  Politika
# --------------------------------------------------------------------------- #
#
# Bunlar onayla bile çalıştırılmaz. Liste kısa tutuldu: uzun bir kara liste
# güvenlik yanılsaması üretir (her zaman atlatılabilir), asıl koruma onay
# kapısı + zaman aşımı + kapalı stdin'dir. Buradakiler yalnızca "yanlışlıkla
# üretilirse geri dönüşü olmayan" kalıplar.

_BLOCKED_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\brm\s+(-[a-zA-Z]+\s+)*(/|/\*|~|~/\*|\$HOME)\s*(;|&|$)",
     "kök veya ev dizinini silme"),
    (r":\s*\(\s*\)\s*\{.*\}\s*;\s*:", "fork bomb"),
    (r"\bmkfs(\.\w+)?\b", "dosya sistemi oluşturma (disk silinir)"),
    (r"\bdd\b[^|;]*\bof=/dev/(disk|sd|nvme|hd)", "ham diske yazma"),
    (r">\s*/dev/(sd|disk|nvme|hd)", "ham diske yönlendirme"),
    (r"\bdiskutil\s+(eraseDisk|eraseVolume|zeroDisk|reformat)",
     "disk biçimlendirme"),
    (r"\bformat\s+[a-zA-Z]:", "sürücü biçimlendirme"),
    (r"\bdel\s+/[sqf][^|;]*\b[a-zA-Z]:\\?\s*$", "sürücü kökünü silme"),
    (r"\bRemove-Item\b[^|;]*-Recurse[^|;]*(C:\\\\?\s|\$env:SystemRoot|\$env:USERPROFILE\s*$)",
     "sistem dizinini özyinelemeli silme"),
    (r"\bchmod\s+-R\s+777\s+/", "kök dizinde izin sıfırlama"),
    (r"\b(curl|wget)\b[^|;]*\|\s*(sudo\s+)?\w*sh\b",
     "indirilen betiği doğrudan kabuğa verme"),
    (r"\|\s*iex\b|\|\s*Invoke-Expression", "indirilen betiği PowerShell'e verme"),
    (r"\bhistory\s+-c\b|\bClear-History\b", "geçmiş silme"),
)

_COMPILED_BLOCKED = [(re.compile(p, re.IGNORECASE), why)
                     for p, why in _BLOCKED_PATTERNS]

#: Sayfalayıcıları, renkleri ve parola istemlerini kapatan ortam değişkenleri.
_QUIET_ENV = {
    "GIT_PAGER": "cat",
    "PAGER": "cat",
    "GIT_TERMINAL_PROMPT": "0",
    "GH_PROMPT_DISABLED": "1",
    "NO_COLOR": "1",
    "CLICOLOR": "0",
    "TERM": "dumb",
    "PYTHONUNBUFFERED": "1",
}


@dataclass(frozen=True, slots=True)
class ShellConfig:
    """Kabuk yürütme sınırları."""

    timeout: float = 20.0             # saniye; agent'ı bir komuta kilitlememek için
    max_output_chars: int = 4000      # ~1000 token
    cwd: str | None = None            # None ise sürecin çalışma dizini
    shell_command: tuple[str, ...] = ()   # boşsa platforma göre seçilir
    extra_blocked: tuple[str, ...] = ()   # projeye özel ek kalıplar
    inherit_env: bool = True


@dataclass(slots=True)
class ShellResult:
    """Bir kabuk komutunun sonucu."""

    ok: bool
    command: str
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    elapsed_ms: float = 0.0
    shell: str = ""
    cwd: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        """LLM'e giden biçim.

        Boş alanlar atlanır: ``"stderr": ""`` taşımak her adımda bedava token
        harcamaktır. ``exit_code`` daima gönderilir çünkü başarı ölçütü odur —
        kabuk şeridinde "doğrulama" budur, UI şeridindeki ``StateDiff``ın
        karşılığı.
        """
        out: dict[str, Any] = {
            "ok": self.ok,
            "exit_code": self.exit_code,
            "elapsed_ms": round(self.elapsed_ms, 1),
        }
        if self.stdout:
            out["stdout"] = self.stdout
        if self.stderr:
            out["stderr"] = self.stderr
        if self.timed_out:
            out["timed_out"] = True
        if self.error:
            out["error"] = self.error
        return out


def default_shell(platform: str | None = None,
                  which: Callable[[str], str | None] = shutil.which) -> list[str]:
    """Platformun kabuk çağrısını döndürür (komut metni sona eklenir).

    macOS/Linux'ta **login** kabuk (``-lc``) kullanılır. Sebep ölçülebilir:
    ``nvm``, ``pyenv``, Homebrew gibi araçlar PATH'i profil dosyalarında kurar;
    login olmayan bir kabukta ``node`` veya ``git`` bulunamaz ve agent var olan
    bir aracı "yok" sanır.
    """
    name = platform or sys.platform
    if name == "win32":
        for candidate in ("pwsh", "powershell"):
            if which(candidate):
                return [candidate, "-NoProfile", "-NonInteractive", "-Command"]
        return ["cmd.exe", "/c"]

    user_shell = os.environ.get("SHELL", "")
    if user_shell and os.path.basename(user_shell) in ("zsh", "bash", "sh"):
        return [user_shell, "-lc"]
    for candidate in ("/bin/zsh", "/bin/bash", "/bin/sh"):
        if os.path.exists(candidate):
            return [candidate, "-lc"]
    return ["/bin/sh", "-c"]


def blocked_reason(command: str, extra: tuple[str, ...] = ()) -> str:
    """Komut engellenen bir kalıba uyuyorsa gerekçe, uymuyorsa boş dize."""
    for pattern, why in _COMPILED_BLOCKED:
        if pattern.search(command):
            return why
    for pattern in extra:
        if re.search(pattern, command, re.IGNORECASE):
            return f"proje politikası: {pattern}"
    return ""


def clip_output(text: str, limit: int) -> str:
    """Uzun çıktıyı ortasından keser; baş ve son korunur."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    head = int(limit * 0.6)
    tail = limit - head
    dropped = len(text) - limit
    return (f"{text[:head]}\n… [{dropped} karakter atlandı] …\n{text[-tail:]}")


# --------------------------------------------------------------------------- #
#  Yürütücü
# --------------------------------------------------------------------------- #

class ShellRunner:
    """Komutları çalıştırır. Politika denetimi ``run`` içinde yapılır."""

    def __init__(self, config: ShellConfig | None = None,
                 platform: str | None = None,
                 runner: Callable[..., Any] = subprocess.run) -> None:
        """
        Args:
            config: Sınırlar (zaman aşımı, çıktı boyutu, çalışma dizini).
            platform: Kabuk seçimi için; ``None`` ise ``sys.platform``.
            runner: ``subprocess.run`` yerine geçebilecek çağrılabilir —
                testler gerçek süreç açmadan davranışı doğrulayabilsin diye.
        """
        self.config = config or ShellConfig()
        self._argv = list(self.config.shell_command) or default_shell(platform)
        self._run = runner

    @property
    def shell_name(self) -> str:
        return self._argv[0]

    def blocked(self, command: str) -> str:
        """Komut engelli mi? Engelliyse gerekçe, değilse boş dize.

        Ayrı bir sorgu olarak durur ki çağıran, kullanıcıya **sormadan önce**
        bakabilsin: hiçbir zaman çalıştırılmayacak bir komut için onay istemek,
        onay istemini gürültüye çevirir.
        """
        return blocked_reason((command or "").strip(), self.config.extra_blocked)

    # ------------------------------------------------------------------ #

    def run(self, command: str, timeout: float | None = None,
            cwd: str | None = None) -> ShellResult:
        """Komutu çalıştırır.

        Raises:
            ShellCommandBlocked: Komut engellenen bir kalıba uyuyorsa. Bu bir
                onay reddi değildir; onay verilse de çalıştırılmaz.
        """
        command = (command or "").strip()
        if not command:
            return ShellResult(ok=False, command="", error="boş komut")

        reason = self.blocked(command)
        if reason:
            raise ShellCommandBlocked(
                f"Komut politika gereği çalıştırılmadı ({reason}): {command}"
            )

        workdir = cwd or self.config.cwd or os.getcwd()
        if not os.path.isdir(workdir):
            return ShellResult(ok=False, command=command, cwd=workdir,
                               error=f"çalışma dizini yok: {workdir}")

        limit = timeout if timeout is not None else self.config.timeout
        started = time.perf_counter()
        argv = [*self._argv, command]

        try:
            completed = self._run(
                argv,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=limit,
                cwd=workdir,
                env=self._env(),
                stdin=subprocess.DEVNULL,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            return ShellResult(
                ok=False, command=command, exit_code=124, timed_out=True,
                stdout=self._clip(_decode(exc.stdout)),
                stderr=self._clip(_decode(exc.stderr)),
                elapsed_ms=(time.perf_counter() - started) * 1000.0,
                shell=self.shell_name, cwd=workdir,
                error=f"{limit:g} saniyede tamamlanmadı, süreç sonlandırıldı",
            )
        except FileNotFoundError as exc:
            return ShellResult(ok=False, command=command, exit_code=127,
                               shell=self.shell_name, cwd=workdir,
                               error=f"kabuk bulunamadı: {exc}")
        except OSError as exc:
            return ShellResult(ok=False, command=command, exit_code=1,
                               shell=self.shell_name, cwd=workdir,
                               error=f"komut başlatılamadı: {exc}")

        code = int(getattr(completed, "returncode", 0) or 0)
        return ShellResult(
            ok=code == 0,
            command=command,
            exit_code=code,
            stdout=self._clip(getattr(completed, "stdout", "")),
            stderr=self._clip(getattr(completed, "stderr", "")),
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            shell=self.shell_name,
            cwd=workdir,
        )

    # ------------------------------------------------------------------ #

    def _env(self) -> dict[str, str]:
        env = dict(os.environ) if self.config.inherit_env else {}
        env.update(_QUIET_ENV)
        return env

    def _clip(self, text: str) -> str:
        return clip_output(text, self.config.max_output_chars)


def _decode(value: Any) -> str:
    """``TimeoutExpired`` çıktısı ortamına göre bytes ya da str gelir."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)
