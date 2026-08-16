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


@dataclass(frozen=True, slots=True)
class WindowInfo:
    """Ust duzey bir pencerenin ozeti — hedef secmek icin.

    ``handle`` platforma gore farkli bir sayidir (Windows'ta HWND, macOS'ta
    CGWindowID) ama her iki durumda da ``extract(handle)``a verilebilir.
    """

    handle: int
    title: str
    process_name: str = ""
    pid: int = 0
    rect: Rect = field(default_factory=lambda: Rect(0, 0, 0, 0))
    is_active: bool = False
    is_minimized: bool = False


def format_window_row(window: WindowInfo) -> str:
    """Tek satirlik listeleme bicimi. Platformdan bagimsiz, bu yuzden burada."""
    mark = "*" if window.is_active else (" " if not window.is_minimized else "_")
    proc = f" [{window.process_name}]" if window.process_name else ""
    return f"{mark} {window.handle:>10}  {window.title[:70]}{proc}"


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

    @abstractmethod
    def list_windows(self) -> list[WindowInfo]:
        """Gorunur ust duzey pencereler, on plandan arkaya dogru sirali.

        CLI'nin hedef secebilmesi icin gerekir; onceden bu is cli.py icinde
        satir-ici bir ``win32gui`` importuyla yapiliyordu, yani baska hicbir
        platformda calisamazdi.
        """

    def close(self) -> None:
        """Kaynaklari birakir. Alt siniflar gerekirse gecersiz kilar."""
        return None
