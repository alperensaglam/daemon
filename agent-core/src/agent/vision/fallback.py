"""VisionFallback — erişilebilirlik ağacı sunmayan pencereler için.

Oyunlar, WebGL/Canvas uygulamaları ve bazı özel çizim yapan pencereler
erişilebilirlik API'sine hiçbir anlamlı düğüm vermez. O durumda **yalnızca o
pencerenin** görüntüsü alınıp OCR'dan geçirilir ve sonuçlar aynı ``UINode``
şemasına ``role="OcrText"`` olarak enjekte edilir — üst katman tek bir arayüz
görür, kaynağı ``snapshot.source`` alanından anlaşılır.

Bu modül **platformdan bağımsız orkestratördür**: yakalama ve OCR
implementasyonları ``capture_win``/``capture_mac`` ve ``ocr_win``/``ocr_mac``
içindedir, seçimi ``platform.resolve_backend`` yapar.
"""

from __future__ import annotations

import time

from .base import Capture, OcrResult

# Bilinen "opak" pencere sınıfları: erişilebilirlik ağacı beklemeye değmez.
OPAQUE_WINDOW_CLASSES = frozenset({
    "UnityWndClass", "UnrealWindow", "SDL_app", "GLFW30", "LWJGL",
    "Chrome_RenderWidgetHostHWND", "Godot_Engine", "SFML_Window",
})

#: macOS'ta pencere sınıfı yoktur; karşılığı, alt ağacın tek bir çizim
#: yüzeyinden ibaret olmasıdır.
OPAQUE_ROOT_ROLES = frozenset({"AXUnknown", "AXImage"})

# Bu sayıdan az kullanılabilir düğüm varsa pencere "opak" sayılır.
MIN_USEFUL_NODES = 3


def should_fallback(node_count: int, window_class: str = "",
                    root_role: str = "") -> bool:
    """Vision yoluna geçilmeli mi?"""
    if window_class in OPAQUE_WINDOW_CLASSES:
        return True
    if root_role in OPAQUE_ROOT_ROLES:
        return True
    return node_count < MIN_USEFUL_NODES


def _backends():
    """Bu platformun yakalama ve OCR modüllerini yükler."""
    from ..platform import load, resolve_backend  # noqa: PLC0415

    spec = resolve_backend()
    return load(spec.capture), load(spec.ocr)


def capture_window(handle: int) -> Capture:
    """Pencere görüntüsünü alır (platformun implementasyonuna yönlendirir)."""
    capture_mod, _ = _backends()
    return capture_mod.capture_window(handle)


def ocr_image(capture: Capture, language: str = "tr") -> OcrResult:
    """Görüntüdeki metni çıkarır (platformun implementasyonuna yönlendirir)."""
    _, ocr_mod = _backends()
    return ocr_mod.ocr_image(capture, language)


def extract_via_vision(handle: int, language: str = "tr") -> OcrResult:
    """Pencereyi yakala + OCR'la. Üst katmanın çağırdığı tek fonksiyon."""
    started = time.perf_counter()
    try:
        capture = capture_window(handle)
    except Exception as exc:
        return OcrResult(engine="none", error=f"Ekran görüntüsü alınamadı: {exc}")
    capture_ms = (time.perf_counter() - started) * 1000.0

    result = ocr_image(capture, language)
    result.capture_ms = capture_ms
    return result
