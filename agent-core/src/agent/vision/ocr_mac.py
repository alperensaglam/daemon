"""macOS OCR — Vision framework (VNRecognizeTextRequest).

**Tanıma seviyesi taşıyıcı bir karardır, ayar değil.** Bu makinede ölçüldü
(macOS 26.5.1):

    accurate (level 0) -> 30 dil, tr-TR **var**
    fast     (level 1) ->  6 dil, tr-TR **yok**

Yani hız için ``fast``a geçmek Türkçe metin tanımayı sessizce bozar. Bu yüzden
varsayılan ``accurate``tır ve istenen dil o seviyede desteklenmiyorsa
İngilizceye düşülüp ``OcrResult.language_used`` alanına yazılır — sessiz bir
varsayım bırakılmaz.
"""

from __future__ import annotations

import time

from ..core.errors import BackendUnavailable
from ..core.types import UINode
from .base import Capture, OcrResult
from .geometry import box_to_rect

#: VNRequestTextRecognitionLevel
LEVEL_ACCURATE = 0
LEVEL_FAST = 1


def _vision():
    try:
        import Vision  # noqa: PLC0415
    except ImportError as exc:                       # pragma: no cover
        raise BackendUnavailable(
            "Vision yüklenemedi. Kurulum: pip install pyobjc-framework-Vision"
        ) from exc
    return Vision


def supported_languages(level: int = LEVEL_ACCURATE) -> list[str]:
    """Verilen tanıma seviyesinin desteklediği diller."""
    Vision = _vision()
    request = Vision.VNRecognizeTextRequest.alloc().init()
    try:
        result = (
            Vision.VNRecognizeTextRequest
            .supportedRecognitionLanguagesForTextRecognitionLevel_revision_error_(
                level, request.revision(), None
            )
        )
    except Exception:
        return []
    if isinstance(result, tuple):
        result = result[0]
    return [str(language) for language in (result or [])]


def _resolve_language(requested: str, level: int) -> tuple[list[str], str, str]:
    """İstenen dili destekleneni bulacak şekilde çözer.

    Returns:
        ``(vision_dil_listesi, kullanilan_dil, uyari)``
    """
    available = supported_languages(level)
    if not available:
        return [], requested, ""

    wanted = (requested or "en").lower()
    for language in available:
        if language.lower() == wanted or language.lower().startswith(wanted + "-"):
            return [language], language, ""

    fallback = next((lang for lang in available if lang.startswith("en")), available[0])
    return [fallback], fallback, (
        f"'{requested}' bu tanima seviyesinde desteklenmiyor; {fallback} kullanildi."
    )


def ocr_image(capture: Capture, language: str = "tr",
              level: int = LEVEL_ACCURATE) -> OcrResult:
    """Yakalanmış görüntüdeki metni Vision ile çıkarır."""
    started = time.perf_counter()
    Vision = _vision()

    try:
        import io  # noqa: PLC0415

        from Foundation import NSData  # noqa: PLC0415

        buffer = io.BytesIO()
        capture.image.save(buffer, format="PNG")
        data = NSData.dataWithBytes_length_(buffer.getvalue(), buffer.tell())

        languages, used, warning = _resolve_language(language, level)

        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(level)
        request.setUsesLanguageCorrection_(False)
        if languages:
            request.setRecognitionLanguages_(languages)

        handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(data, {})
        ok, error = handler.performRequests_error_([request], None)
        if not ok:
            return OcrResult(engine="vision", error=f"Vision hatasi: {error}",
                             language_used=used)

        observations = request.results() or []
    except Exception as exc:
        return OcrResult(engine="vision",
                         error=f"OCR calistirilamadi: {type(exc).__name__}: {exc}")

    img_w, img_h = capture.image.width, capture.image.height
    nodes: list[UINode] = []

    for observation in observations:
        candidates = observation.topCandidates_(1)
        if not candidates:
            continue
        text = str(candidates[0].string() or "").strip()
        if not text:
            continue

        box = observation.boundingBox()
        # Vision normalize (0..1) ve SOL-ALT orijinli kutu verir; cevrim
        # geometry.box_to_rect icinde ve orada birim testi var.
        rect = box_to_rect(
            box.origin.x, box.origin.y, box.size.width, box.size.height,
            img_w=img_w, img_h=img_h, origin=capture.origin,
            scale=capture.scale, normalized=True, bottom_left=True,
        )
        nodes.append(UINode(role="OcrText", name=text, rect=rect,
                            enabled=True, focusable=False, patterns=frozenset()))

    return OcrResult(
        nodes=nodes,
        engine="vision",
        ocr_ms=(time.perf_counter() - started) * 1000.0,
        language_used=used,
        error=warning,
    )
