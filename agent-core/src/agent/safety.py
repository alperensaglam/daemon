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
