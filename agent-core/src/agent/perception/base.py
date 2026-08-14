"""UITreeExtractor soyut arayuzu — platform genisleme dikisi.

Windows (UIAutomation), macOS (AXUIElement) ve Linux (AT-SPI2) implementasyonlari
ayni sozlesmeyi uygular. Ustteki katmanlar (TreePruner, CLI, LLM) yalnizca bu
arayuzu ve ``UINode`` tipini bilir; hicbir COM/ObjC detayini gormez.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..core.types import Rect, UINode


@dataclass(slots=True)
class ExtractResult:
    """Ham cikarim sonucu — henuz budanmamis."""

    nodes: list[UINode]
    window_title: str
    window_handle: int = 0
    process_name: str = ""
    extract_ms: float = 0.0
    truncated: bool = False       # guvenlik siniri nedeniyle agac kesildi mi

    # Pencere sinirlari isletim sisteminden alinir, agactan tahmin edilmez.
    # Simge durumundaki pencereleri Windows -32000,-32000'e park eder; agacin
    # kok dugumunden okumak bu durumda tum cocuklari "pencere disi" gosterir.
    window_rect: Rect = field(default_factory=lambda: Rect(0, 0, 0, 0))
    is_minimized: bool = False

    # Agac supheli sekilde bossa nedenini aciklar (or. askiya alinmis UWP).
    # Bos bir anlik goruntuyu sessizce dondurmek, LLM'in "sayfa bos" diye
    # yanlis sonuca varmasina yol acar.
    warning: str = ""


class UITreeExtractor(ABC):
    """Isletim sistemi erisilebilirlik agacini okuyan bilesen."""

    @abstractmethod
    def extract(self, hwnd: int | None = None) -> ExtractResult:
        """Aktif pencerenin (veya verilen tutamacin) ham UI agacini cikarir.

        Args:
            hwnd: Hedef pencere tutamaci. ``None`` ise aktif pencere kullanilir.

        Raises:
            NoActiveWindow: Aktif pencere yoksa.
            BackendUnavailable: Platform arayuzu baslatilamadiysa.
        """

    @abstractmethod
    def active_window_handle(self) -> int:
        """Aktif pencerenin tutamacini dondurur."""

    def close(self) -> None:
        """Kaynaklari birakir. Alt siniflar gerekirse gecersiz kilar."""
        return None
