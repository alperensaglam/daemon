"""Vision katmanının platformdan bağımsız tipleri."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..core.types import Rect, UINode


@dataclass(slots=True)
class Capture:
    """Yakalanmış pencere görüntüsü.

    ``scale`` alanı isteğe bağlı bir ayrıntı değildir: Retina ekranda görüntü,
    pencerenin nokta cinsinden boyutunun iki katı pikseldir. Ölçek
    taşınmazsa OCR'dan dönen her dikdörtgen 2x kayar ve piksel tıklamaları
    pencerenin yanlış çeyreğine düşer — sessizce.
    """

    image: Any                # PIL.Image
    origin: Rect              # pencerenin ekrandaki konumu
    scale: float = 1.0        # görüntü pikseli / origin birimi


@dataclass(slots=True)
class OcrResult:
    nodes: list[UINode] = field(default_factory=list)
    engine: str = ""
    ocr_ms: float = 0.0
    capture_ms: float = 0.0
    error: str = ""
    #: Gerçekte kullanılan dil. macOS Vision, Türkçe metin tanımayı
    #: desteklemeyebilir; o durumda İngilizceye düşülür ve bu alan bunu
    #: kaydeder — sessiz bir varsayım bırakmamak için.
    language_used: str = ""
