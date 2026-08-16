"""Windows OCR — Windows.Media.Ocr (WinRT).

İşletim sisteminde hazırdır, GPU istemez, milisaniyeler sürer. Python WinRT
binding'inin paket adı sürümler arasında değişti (``winsdk`` / ``winrt-*``),
bu yüzden sırayla denenir ve hiçbiri yoksa durum açıkça bildirilir.
"""

from __future__ import annotations

import time

from ..core.types import UINode
from .base import Capture, OcrResult
from .geometry import box_to_rect


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


def supported_languages(level: int = 0) -> list[str]:
    """WinRT motorunun profil dilleri (bilgi amaçlı)."""
    loaded, _ = _load_winrt_ocr()
    if loaded is None:
        return []
    _, ocr_mod, _, _ = loaded
    try:
        return [str(lang.language_tag)
                for lang in ocr_mod.OcrEngine.get_available_recognizer_languages()]
    except Exception:
        return []


def ocr_image(capture: Capture, language: str = "tr", level: int = 0) -> OcrResult:
    """Görüntüdeki metni Windows OCR ile çıkarır."""
    started = time.perf_counter()
    loaded, error = _load_winrt_ocr()
    if loaded is None:
        return OcrResult(engine="none", error=error)

    module_name, ocr_mod, imaging_mod, streams_mod = loaded

    try:
        import asyncio  # noqa: PLC0415
        import io  # noqa: PLC0415

        buffer = io.BytesIO()
        capture.image.save(buffer, format="PNG")
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
        return OcrResult(engine=module_name,
                         error=f"OCR çalıştırılamadı: {type(exc).__name__}: {exc}")

    nodes: list[UINode] = []
    for line in lines:
        text = getattr(line, "text", "").strip()
        if not text:
            continue
        words = list(getattr(line, "words", []) or [])
        if not words:
            continue
        left = min(w.bounding_rect.x for w in words)
        top = min(w.bounding_rect.y for w in words)
        right = max(w.bounding_rect.x + w.bounding_rect.width for w in words)
        bottom = max(w.bounding_rect.y + w.bounding_rect.height for w in words)

        # Windows OCR mutlak, sol-ust orijinli piksel kutulari verir.
        rect = box_to_rect(
            left, top, right - left, bottom - top,
            img_w=capture.image.width, img_h=capture.image.height,
            origin=capture.origin, scale=capture.scale,
            normalized=False, bottom_left=False,
        )
        nodes.append(UINode(role="OcrText", name=text, rect=rect,
                            enabled=True, focusable=False, patterns=frozenset()))

    return OcrResult(nodes=nodes, engine=module_name,
                     ocr_ms=(time.perf_counter() - started) * 1000.0,
                     language_used=language)
