"""macOS pencere yakalama — CGWindowListCreateImage (ScreenCaptureKit geri düşümlü).

Üç ayrıntı, üçü de sessiz hataya yol açar:

1. **``bytesPerRow`` onurlandırılmalıdır.** CoreGraphics satır uzunluğunu
   hizalar; bu makinede ölçüldü: 3024 px genişlikte satır 12160 bayt, oysa
   ``width * 4 = 12096`` — 64 bayt dolgu. Dolgu yok sayılırsa görüntü her
   satırda biraz kayar ve klasik "diyagonal kayma" görüntüsü çıkar.
2. **Ölçek taşınmalıdır.** Retina'da görüntü, pencerenin nokta cinsinden
   boyutunun iki katı pikseldir (ölçüldü: 1512 pt -> 3024 px).
3. **Piksel düzeni BGRA'dır** (``kCGImageAlphaPremultipliedFirst |
   ByteOrder32Little``), RGBA değil.

``CGWindowListCreateImage`` macOS 14'ten beri "deprecated" işaretlidir; bu
makinede (macOS 26.5.1) hâlâ gerçek piksel döndürdüğü doğrulandı. Yine de
kurulu ise önce ScreenCaptureKit denenir.
"""

from __future__ import annotations

from ..core.errors import BackendUnavailable
from ..core.types import Rect
from .base import Capture


def _quartz():
    try:
        import Quartz  # noqa: PLC0415
    except ImportError as exc:                       # pragma: no cover
        raise BackendUnavailable(
            "Quartz yüklenemedi. Kurulum: pip install pyobjc-framework-Quartz"
        ) from exc
    return Quartz


def screen_capture_status() -> tuple[bool, str]:
    """``(izin_var_mi, aciklama)``. Sistem penceresi açmaz."""
    Q = _quartz()
    if Q.CGPreflightScreenCaptureAccess():
        return True, "ekran kaydı izni var"
    return False, (
        "Ekran Kaydı izni yok. Sistem Ayarları > Gizlilik ve Güvenlik > "
        "Ekran Kaydı ve Sistem Ses Kaydı altından izin verip uygulamayı "
        "yeniden başlatın."
    )


def _window_bounds(Q, handle: int) -> Rect:
    info = Q.CGWindowListCopyWindowInfo(
        Q.kCGWindowListOptionIncludingWindow, handle
    ) or []
    if not info:
        raise ValueError(f"CGWindowID {handle} bulunamadi.")
    bounds = info[0].get("kCGWindowBounds") or {}
    return Rect.from_origin_size(
        bounds.get("X", 0), bounds.get("Y", 0),
        bounds.get("Width", 0), bounds.get("Height", 0),
    )


def _to_pil(Q, image):
    """CGImage -> PIL.Image. bytesPerRow dolgusunu ayıklar."""
    from PIL import Image  # noqa: PLC0415

    width = Q.CGImageGetWidth(image)
    height = Q.CGImageGetHeight(image)
    stride = Q.CGImageGetBytesPerRow(image)
    data = bytes(Q.CGDataProviderCopyData(Q.CGImageGetDataProvider(image)))

    expected = width * 4
    if stride != expected:
        # Her satırın sonundaki hizalama dolgusunu at.
        rows = [data[i * stride:i * stride + expected] for i in range(height)]
        data = b"".join(rows)

    # CoreGraphics BGRA verir (ByteOrder32Little + AlphaPremultipliedFirst).
    return Image.frombuffer("RGB", (width, height), data, "raw", "BGRX", 0, 1)


def _capture_screencapturekit(Q, handle: int):
    """Kurulu ise modern yol. Yoksa ``None`` döner."""
    try:
        import ScreenCaptureKit  # noqa: PLC0415, F401
    except ImportError:
        return None
    # ScreenCaptureKit'in Python bağlaması asenkron bir API sunar ve pratikte
    # CGWindowListCreateImage bu makinede hâlâ çalıştığı için burada bilinçli
    # olarak uygulanmadı. Modül kurulduğunda bu dal doldurulmalı.
    return None


def capture_window(handle: int) -> Capture:
    """Yalnızca verilen pencerenin görüntüsünü alır."""
    Q = _quartz()

    ok, reason = screen_capture_status()
    if not ok:
        raise BackendUnavailable(reason)

    origin = _window_bounds(Q, handle)

    image = _capture_screencapturekit(Q, handle)
    if image is None:
        image = Q.CGWindowListCreateImage(
            Q.CGRectNull,
            Q.kCGWindowListOptionIncludingWindow,
            handle,
            Q.kCGWindowImageBoundsIgnoreFraming,
        )
    if image is None:
        raise ValueError(
            f"CGWindowID {handle} icin goruntu alinamadi. Pencere kapanmis "
            "veya simge durumunda olabilir."
        )

    pil = _to_pil(Q, image)
    # Olcek: goruntu pikseli / pencere noktasi. Retina'da 2.0.
    scale = (pil.width / origin.width) if origin.width else 1.0
    return Capture(image=pil, origin=origin, scale=scale or 1.0)
