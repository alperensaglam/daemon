"""VisionFallback — erişilebilirlik ağacı sunmayan pencereler için.

Oyunlar, WebGL/Canvas uygulamaları ve bazı özel çizim yapan pencereler UIA'ya
hiçbir anlamlı düğüm vermez. O durumda **yalnızca o pencerenin** görüntüsü
alınıp OCR'dan geçirilir ve sonuçlar aynı ``UINode`` şemasına ``role="OcrText"``
olarak enjekte edilir — üst katman tek bir arayüz görür.

OCR için Windows'un yerleşik ``Windows.Media.Ocr`` motoru tercih edilir:
işletim sisteminde hazırdır, GPU istemez, milisaniyeler sürer. Python WinRT
binding'inin paket adı sürümler arasında değişti (``winsdk`` / ``winrt-*``),
bu yüzden sırayla denenir ve hiçbiri yoksa durum açıkça bildirilir.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from ..core.types import Rect, UINode

# Bilinen "opak" pencere sınıfları: UIA ağacı beklemeye değmez.
OPAQUE_WINDOW_CLASSES = frozenset({
    "UnityWndClass", "UnrealWindow", "SDL_app", "GLFW30", "LWJGL",
    "Chrome_RenderWidgetHostHWND", "Godot_Engine", "SFML_Window",
})

# Bu sayıdan az kullanılabilir düğüm varsa pencere "opak" sayılır.
MIN_USEFUL_NODES = 3


@dataclass(slots=True)
class OcrResult:
    nodes: list[UINode]
    engine: str
    ocr_ms: float
    capture_ms: float
    error: str = ""


def should_fallback(node_count: int, window_class: str = "") -> bool:
    """Vision yoluna geçilmeli mi?"""
    if window_class in OPAQUE_WINDOW_CLASSES:
        return True
    return node_count < MIN_USEFUL_NODES


# --------------------------------------------------------------------------- #
#  Ekran görüntüsü
# --------------------------------------------------------------------------- #

def capture_window(hwnd: int):
    """Yalnızca verilen pencerenin görüntüsünü alır (PIL.Image döner).

    ``PrintWindow`` tercih edilir: pencere kısmen örtülü veya arka planda olsa
    bile içeriğini verir. Ekranın tamamını alıp kırpmak, üstteki başka bir
    pencereyi yakalama riski taşır.
    """
    import win32con
    import win32gui
    import win32ui
    from ctypes import windll
    from PIL import Image

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        raise ValueError(f"Pencere boyutu geçersiz: {width}x{height}")

    window_dc = win32gui.GetWindowDC(hwnd)
    dc = win32ui.CreateDCFromHandle(window_dc)
    mem_dc = dc.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(dc, width, height)
    mem_dc.SelectObject(bitmap)

    try:
        # 3 = PW_RENDERFULLCONTENT: DirectComposition kullanan (WinUI, Chromium)
        # pencerelerde şart; 0 veya 1 ile siyah görüntü gelir.
        ok = windll.user32.PrintWindow(hwnd, mem_dc.GetSafeHdc(), 3)
        if not ok:
            mem_dc.BitBlt((0, 0), (width, height), dc, (0, 0), win32con.SRCCOPY)

        info = bitmap.GetInfo()
        bits = bitmap.GetBitmapBits(True)
        image = Image.frombuffer(
            "RGB", (info["bmWidth"], info["bmHeight"]), bits, "raw", "BGRX", 0, 1
        )
    finally:
        win32gui.DeleteObject(bitmap.GetHandle())
        mem_dc.DeleteDC()
        dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, window_dc)

    return image, Rect.from_ltrb(left, top, right, bottom)


# --------------------------------------------------------------------------- #
#  OCR
# --------------------------------------------------------------------------- #

def _load_winrt_ocr():
    """Kurulu WinRT binding'ini bulur. Bulamazsa ``(None, sebep)`` döner."""
    attempts = []
    for module_name in ("winsdk", "winrt"):
        try:
            ocr = __import__(f"{module_name}.windows.media.ocr", fromlist=["OcrEngine"])
            graphics = __import__(f"{module_name}.windows.graphics.imaging",
                                  fromlist=["BitmapDecoder"])
            streams = __import__(f"{module_name}.windows.storage.streams",
                                 fromlist=["DataWriter", "InMemoryRandomAccessStream"])
            return (module_name, ocr, graphics, streams), ""
        except Exception as exc:
            attempts.append(f"{module_name}: {type(exc).__name__}")
    return None, (
        "Windows OCR binding'i bulunamadı (" + ", ".join(attempts) + "). "
        "Kurulum: pip install winsdk"
    )


def ocr_image(image, origin: Rect, language: str = "tr") -> OcrResult:
    """Görüntüdeki metni Windows OCR ile çıkarır."""
    started = time.perf_counter()
    loaded, error = _load_winrt_ocr()
    if loaded is None:
        return OcrResult([], engine="none", ocr_ms=0.0, capture_ms=0.0, error=error)

    module_name, ocr_mod, imaging_mod, streams_mod = loaded

    try:
        import asyncio
        import io

        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        data = buffer.getvalue()

        async def run() -> list:
            stream = streams_mod.InMemoryRandomAccessStream()
            writer = streams_mod.DataWriter(stream.get_output_stream_at(0))
            writer.write_bytes(data)
            await writer.store_async()
            decoder = await imaging_mod.BitmapDecoder.create_async(stream)
            bitmap = await decoder.get_software_bitmap_async()

            engine = ocr_mod.OcrEngine.try_create_from_user_profile_languages()
            if engine is None:
                engine = ocr_mod.OcrEngine.try_create_from_language(
                    __import__(f"{module_name}.windows.globalization",
                               fromlist=["Language"]).Language(language)
                )
            if engine is None:
                return []
            result = await engine.recognize_async(bitmap)
            return list(result.lines)

        lines = asyncio.run(run())
    except Exception as exc:
        return OcrResult([], engine=module_name, ocr_ms=0.0, capture_ms=0.0,
                         error=f"OCR çalıştırılamadı: {type(exc).__name__}: {exc}")

    nodes: list[UINode] = []
    for line in lines:
        text = getattr(line, "text", "").strip()
        if not text:
            continue
        rect = _line_rect(line, origin)
        nodes.append(UINode(role="OcrText", name=text, rect=rect,
                            enabled=True, focusable=False,
                            patterns=frozenset()))

    return OcrResult(nodes, engine=module_name,
                     ocr_ms=(time.perf_counter() - started) * 1000.0,
                     capture_ms=0.0)


def _line_rect(line, origin: Rect) -> Rect:
    """OCR satırının kelime kutularını birleştirip ekran koordinatına taşır."""
    words = list(getattr(line, "words", []) or [])
    if not words:
        return Rect(0, 0, 0, 0)
    left = min(w.bounding_rect.x for w in words)
    top = min(w.bounding_rect.y for w in words)
    right = max(w.bounding_rect.x + w.bounding_rect.width for w in words)
    bottom = max(w.bounding_rect.y + w.bounding_rect.height for w in words)
    return Rect.from_ltrb(
        int(origin.left + left), int(origin.top + top),
        int(origin.left + right), int(origin.top + bottom),
    )


def extract_via_vision(hwnd: int, language: str = "tr") -> OcrResult:
    """Pencereyi yakala + OCR'la. Üst katmanın çağırdığı tek fonksiyon."""
    started = time.perf_counter()
    try:
        image, origin = capture_window(hwnd)
    except Exception as exc:
        return OcrResult([], engine="none", ocr_ms=0.0, capture_ms=0.0,
                         error=f"Ekran görüntüsü alınamadı: {exc}")
    capture_ms = (time.perf_counter() - started) * 1000.0

    result = ocr_image(image, origin, language)
    result.capture_ms = capture_ms
    return result
