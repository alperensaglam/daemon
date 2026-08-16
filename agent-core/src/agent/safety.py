"""Eylem risk siniflandirmasi ve onay kapisi.

Bir UI agent'i, dosya agent'indan farkli bir risk tasir: yanlis dugume tiklamak
"Sil", "Gonder", "Satin al" olabilir ve geri alinamaz. Ustelik hata sessizdir —
model dogru butona bastigini sanar.

Bu yuzden onay varsayilan olarak aciktir ve yuksek riskli eylemler ``--yes``
verilse bile sorar. Guvenligi kapatmak acik bir tercih olmali, unutulan bir
ayar degil.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from .core.types import UINode


class Risk(str, Enum):
    LOW = "low"        # okuma, odaklama, kaydirma
    MEDIUM = "medium"  # yazma, siradan tiklama
    HIGH = "high"      # yikici veya geri alinamaz


# Yuksek riskli eylem adlari.
#
# Iki tasarim karari:
#
# 1) Turkce ve Ingilizce birlikte taranir. Arayuz dili kullanicinin sistemine
#    gore degisir; tek dile bakmak yarim koruma olur.
#
# 2) Turkce EKLEMELI bir dildir, bu yuzden kok + \w* eslenir. "\bsil\b" kalibi
#    "Silme", "Sileceğim", "Kaldırma" gibi bicimleri kacirir — nitekim ilk
#    surumde "Ödeme Yap" dugmesi orta riskli sayildi.
#    Bu yaklasim bazen gereksiz soru sorar ("silindir" gibi kelimeler),
#    ama BILINCLI bir tercih: gereksiz sormanin bedeli bir tiklama,
#    kacirmanin bedeli geri alinamaz bir islem.
_HIGH_RISK_PATTERNS = [
    (r"\b(sil|kaldır|kaldir|delete|remove|eras|discard|temizle|clear)\w*",
     "silme islemi"),
    (r"\b(biçimlendir|bicimlendir|format)\w*", "bicimlendirme"),
    (r"\b(gönder|gonder|send|submit|publish|yayınla|yayinla|paylaş|paylas|share)\w*",
     "disariya gonderme"),
    (r"\b(satın al|satin al|buy|purchase|öde|ode|pay|checkout|sipariş|siparis)\w*",
     "odeme islemi"),
    (r"\b(çıkış|cikis|oturumu kapat|sign ?out|log ?out|quit|exit)\w*",
     "oturum kapatma"),
    (r"\b(sıfırla|sifirla|reset|restore|fabrika)\w*", "sifirlama"),
    (r"\bgeri yükle|\bgeri yukle", "geri yukleme"),
    (r"\b(yükle|yukle|install|kur|güncelle|guncelle|update|upgrade)\w*", "kurulum"),
    (r"\b(izin ver|allow|grant|onayla|kabul et|accept|agree)\w*", "izin/onay verme"),
    (r"\b(yeniden başlat|yeniden baslat|restart|shutdown|kapat)\w*",
     "kapatma/yeniden baslatma"),
]

_COMPILED = [(re.compile(p, re.IGNORECASE), why) for p, why in _HIGH_RISK_PATTERNS]

# Hicbir zaman UI'yi degistirmeyen eylemler.
_READ_ONLY_ACTIONS = frozenset({"get_state", "snapshot", "focus", "scroll", "wait"})

# --------------------------------------------------------------------------- #
#  Kabuk komutlari
# --------------------------------------------------------------------------- #
#
# Hibrit yurutme (bkz. execution/router.py) LLM'e kabuk erisimi de verir. Bir
# kabuk komutu, yanlis dugmeye tiklamaktan daha genis bir yetkidir: tek satir
# bir dizini silebilir. Bu yuzden risk siniflandirmasi UI eylemleriyle AYNI
# yerde yapilir — iki ayri politika olsaydi biri gunceleneme kalirdi.
#
# Siniflandirma komutun **her segmentine** bakar: "ls && rm -rf build" ilk
# tokenina bakip dusuk riskli sayilamaz.

_SHELL_ACTIONS = frozenset({"run_shell", "shell"})

# Yalnizca okuyan komutlar. Bunlari her seferinde sormak, onay istemini
# anlamsizlastirir (kullanici korkulukları okumadan "evet"e basmaya baslar).
_SHELL_READ_ONLY = frozenset({
    "ls", "dir", "pwd", "cd", "cat", "type", "head", "tail", "less", "wc",
    "grep", "rg", "findstr", "find", "which", "where", "echo", "date", "whoami",
    "hostname", "uname", "df", "du", "ps", "top", "env", "printenv", "stat",
    "file", "tree", "diff", "sort", "uniq", "cut", "awk", "sed", "jq", "basename",
    "dirname", "realpath", "sleep", "test", "true", "false",
    # PowerShell okuma cmdlet'leri
    "get-childitem", "get-content", "get-process", "get-location", "get-date",
    "select-string", "measure-object", "select-object", "where-object",
    "sort-object", "format-table", "out-string", "test-path", "get-item",
})

# Git alt komutlari ayri ele alinir: "git status" okuma, "git push" yayindir.
_GIT_READ_ONLY = frozenset({
    "status", "log", "diff", "show", "branch", "remote", "config", "blame",
    "describe", "rev-parse", "ls-files", "shortlog", "stash",
})

_HIGH_RISK_SHELL = [
    (r"\brm\s+(-\w+\s+)*-?\w*[rf]", "ozyinelemeli/zorlamali silme"),
    (r"\b(rmdir|unlink|shred|srm)\b", "silme"),
    (r"\bdel\s+/[sqf]|\bRemove-Item\b.*-Recurse", "ozyinelemeli silme"),
    (r"\b(mkfs|fdisk|diskutil\s+(erase|partition)|format\s+[a-z]:)", "disk bicimlendirme"),
    (r"\bdd\s+.*\bof=/dev/", "ham disk yazma"),
    (r"\b(shutdown|reboot|halt|poweroff)\b", "sistem kapatma"),
    (r"\b(kill|killall|pkill|taskkill|Stop-Process)\b", "surec sonlandirma"),
    (r"\bsudo\b|\bdoas\b|\bStart-Process\b.*-Verb\s+RunAs", "yetki yukseltme"),
    (r"\bchmod\b|\bchown\b|\bicacls\b|\battrib\b", "izin degistirme"),
    (r"\bgit\s+(push|reset\s+--hard|clean\s+-\w*[fd]|checkout\s+--|branch\s+-D)",
     "geri alinamaz git islemi"),
    (r"\b(npm|pnpm|yarn)\s+(publish|unpublish)|\btwine\s+upload", "paket yayinlama"),
    (r"\b(pip|npm|brew|apt|apt-get|winget|choco)\s+(install|uninstall|remove)",
     "paket kurulumu/kaldirma"),
    (r"\b(systemctl|launchctl|sc\.exe|net\s+stop)\b", "servis yonetimi"),
    (r"\b(curl|wget|iwr|Invoke-WebRequest)\b[^|;]*\|\s*(ba)?sh|\|\s*iex",
     "indirilen betigi dogrudan calistirma"),
    (r"\breg\s+(delete|add)\b|\bSet-ItemProperty\b.*HKLM", "kayit defteri yazma"),
    (r">\s*/dev/(sd|disk)", "ham cihaza yazma"),
    (r"\b(mv|move|Move-Item)\b", "tasima (uzerine yazabilir)"),
]

_COMPILED_SHELL = [(re.compile(p, re.IGNORECASE), why) for p, why in _HIGH_RISK_SHELL]

# Segment ayiricilari: her biri ayri bir komut baslatir.
_SEGMENT_SPLIT = re.compile(r"\|\||&&|;|\||\n")


def _shell_segments(command: str) -> list[str]:
    return [s.strip() for s in _SEGMENT_SPLIT.split(command or "") if s.strip()]


def _is_read_only_segment(segment: str) -> bool:
    """Segment yalnizca okuma yapiyor mu?

    Yonlendirme (``>``, ``>>``) tek basina yeter: ``echo x > dosya`` okuma
    degildir, dosyayi ezer.
    """
    if ">" in segment:
        return False
    tokens = segment.split()
    if not tokens:
        return False
    head = tokens[0].strip("'\"").rsplit("/", 1)[-1].rsplit("\\", 1)[-1].lower()
    if head == "git":
        subcommand = next((t for t in tokens[1:] if not t.startswith("-")), "")
        return subcommand.lower() in _GIT_READ_ONLY
    return head in _SHELL_READ_ONLY




@dataclass(slots=True)
class RiskAssessment:
    level: Risk
    reasons: list[str]
    description: str

    @property
    def is_high(self) -> bool:
        return self.level is Risk.HIGH


def assess(action: str, node: UINode | None = None, text: str | None = None) -> RiskAssessment:
    """Bir eylemin risk seviyesini belirler."""
    reasons: list[str] = []

    if action in _SHELL_ACTIONS:
        return assess_shell(text or "")

    if action in _READ_ONLY_ACTIONS:
        return RiskAssessment(Risk.LOW, [], _describe(action, node, text))

    level = Risk.MEDIUM

    if node is not None:
        haystack = f"{node.name} {node.value} {node.automation_id}"
        for pattern, why in _COMPILED:
            if pattern.search(haystack):
                level = Risk.HIGH
                reasons.append(f"eleman adi {why} ima ediyor")
                break

    return RiskAssessment(level, reasons, _describe(action, node, text))


def assess_shell(command: str) -> RiskAssessment:
    """Bir kabuk komutunun risk seviyesi.

    Uc seviye, uc farkli davranis:
        LOW    — sorulmadan calisir (salt okuma).
        MEDIUM — 'ask' modunda sorulur, '--yes' ile gecer.
        HIGH   — '--yes' verilse bile daima sorulur.
    """
    command = (command or "").strip()
    if not command:
        return RiskAssessment(Risk.LOW, [], "bos komut")

    reasons: list[str] = []
    for pattern, why in _COMPILED_SHELL:
        if pattern.search(command):
            reasons.append(why)
    if reasons:
        return RiskAssessment(Risk.HIGH, reasons, f"Kabuk komutu: {command}")

    segments = _shell_segments(command)
    if segments and all(_is_read_only_segment(s) for s in segments):
        return RiskAssessment(Risk.LOW, [], f"Kabuk komutu (salt okuma): {command}")

    return RiskAssessment(Risk.MEDIUM, [], f"Kabuk komutu: {command}")


def _describe(action: str, node: UINode | None, text: str | None) -> str:
    target = node.describe() if node else ""
    if action == "click":
        return f"{target} tiklanacak"
    if action == "type_text":
        preview = (text or "")[:60] + ("…" if text and len(text) > 60 else "")
        return f"{target} icine yazilacak: {preview!r}"
    if action == "press_key":
        return f"Tus gonderilecek: {text}"
    if action == "scroll":
        return f"Kaydirilacak: {text}"
    if action == "focus":
        return f"{target} odaklanacak"
    return f"{action} {target}".strip()


class ApprovalGate:
    """Eylemleri kullanici onayina baglar.

    Modlar:
        ``ask``      — her degistirici eylem sorulur (varsayilan)
        ``yes``      — siradan eylemler gecer, YUKSEK RISKLI olanlar yine sorulur
        ``dry_run``  — hicbir eylem calistirilmaz, yalnizca loglanir
    """

    def __init__(self, mode: str = "ask", prompt=input, output=print) -> None:
        if mode not in ("ask", "yes", "dry_run"):
            raise ValueError(f"Bilinmeyen mod: {mode}")
        self.mode = mode
        self._prompt = prompt
        self._out = output
        self._session_allowed: set[str] = set()

    @property
    def is_dry_run(self) -> bool:
        return self.mode == "dry_run"

    def check(self, action: str, node: UINode | None = None,
              text: str | None = None) -> tuple[bool, str]:
        """Eylem calistirilabilir mi?

        Returns:
            ``(izin_var, gerekce)``
        """
        risk = assess(action, node, text)

        if self.mode == "dry_run":
            self._out(f"[KURU] {risk.description}")
            return False, "kuru calisma modu — hicbir eylem uygulanmadi"

        if risk.level is Risk.LOW:
            return True, "dusuk risk"

        if self.mode == "yes" and not risk.is_high:
            return True, "otomatik onay"

        # Buraya gelen her sey soruluyor: ya mod 'ask' ya da risk yuksek.
        return self._ask(risk)

    def _ask(self, risk: RiskAssessment) -> tuple[bool, str]:
        banner = "!! YUKSEK RISK" if risk.is_high else "->"
        self._out(f"\n{banner} {risk.description}")
        for reason in risk.reasons:
            self._out(f"   sebep: {reason}")
        if risk.is_high and self.mode == "yes":
            self._out("   (--yes verilmis olsa da yuksek riskli eylemler daima sorulur)")

        try:
            answer = str(self._prompt("   Devam edilsin mi? [e/H] ")).strip().lower()
        except (EOFError, KeyboardInterrupt):
            self._out("   iptal edildi")
            return False, "kullanici iptal etti"

        if answer in ("e", "evet", "y", "yes"):
            return True, "kullanici onayladi"
        return False, "kullanici reddetti"
