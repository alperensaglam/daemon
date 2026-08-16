"""Windows pencere yakalama — PrintWindow."""

from __future__ import annotations

from ..core.types import Rect
from .base import Capture


def capture_window(handle: int) -> Capture:
    """Yalnızca verilen pencerenin görüntüsünü alır.

    ``PrintWindow`` tercih edilir: pencere kısmen örtülü veya arka planda olsa
    bile içeriğini verir. Ekranın tamamını alıp kırpmak, üstteki başka bir
    pencereyi yakalama riski taşır.
    """
    import win32con  # noqa: PLC0415
    import win32gui  # noqa: PLC0415
    import win32ui  # noqa: PLC0415
    from ctypes import windll  # noqa: PLC0415

    from PIL import Image  # noqa: PLC0415

    left, top, right, bottom = win32gui.GetWindowRect(handle)
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        raise ValueError(f"Pencere boyutu geçersiz: {width}x{height}")

    window_dc = win32gui.GetWindowDC(handle)
    dc = win32ui.CreateDCFromHandle(window_dc)
    mem_dc = dc.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(dc, width, height)
    mem_dc.SelectObject(bitmap)

    try:
        # 3 = PW_RENDERFULLCONTENT: DirectComposition kullanan (WinUI, Chromium)
        # pencerelerde şart; 0 veya 1 ile siyah görüntü gelir.
        ok = windll.user32.PrintWindow(handle, mem_dc.GetSafeHdc(), 3)
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
        win32gui.ReleaseDC(handle, window_dc)

    # PER_MONITOR_V2 DPI farkindaligiyla GetWindowRect de bitmap de fiziksel
    # pikseldir, yani olcek 1.0'dir.
    return Capture(image=image, origin=Rect.from_ltrb(left, top, right, bottom),
                   scale=1.0)
