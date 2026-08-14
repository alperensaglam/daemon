"""DPI farkindaligi — UIA'dan ONCE calismasi gerekir.

Neden kritik: sureç DPI-aware degilse Windows onu sanallastirir ve
``BoundingRectangle`` degerleri **olceklenmemis** koordinat uzayinda doner.
%150 olcekli bir ekranda 100,100'de gorunen bir buton icin UIA 150,150 der;
piksel fallback'i tikladiginda yanlis yere basar. Sessiz ve tespiti zor bir
hatadir, bu yuzden ilk is olarak ayarlanir.
"""

from __future__ import annotations

import ctypes
import sys

# DPI_AWARENESS_CONTEXT degerleri (windef.h)
_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)
_PER_MONITOR_AWARE = ctypes.c_void_p(-3)

_state: str | None = None


def ensure_dpi_aware() -> str:
    """Sureci mumkun olan en iyi DPI farkindalik seviyesine ayarlar.

    Birden fazla kez cagrilabilir; ilk basarili ayar kalicidir. Windows,
    farkindalik zaten ayarliysa hata dondurur — bu beklenen durumdur ve
    basarisizlik sayilmaz.

    Returns:
        Kullanilan yontemin adi, ornegin ``"per_monitor_v2"``.
    """
    global _state
    if _state is not None:
        return _state

    if sys.platform != "win32":
        _state = "not_windows"
        return _state

    # 1. Tercih: Per-Monitor V2 (Windows 10 1703+) — cok monitorlu, farkli
    #    olcekli kurulumlarda dogru davranan tek mod.
    try:
        user32 = ctypes.windll.user32
        if hasattr(user32, "SetProcessDpiAwarenessContext"):
            if user32.SetProcessDpiAwarenessContext(_PER_MONITOR_AWARE_V2):
                _state = "per_monitor_v2"
                return _state
            if user32.SetProcessDpiAwarenessContext(_PER_MONITOR_AWARE):
                _state = "per_monitor_v1"
                return _state
    except Exception:
        pass

    # 2. Windows 8.1+
    try:
        # PROCESS_PER_MONITOR_DPI_AWARE = 2
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
            _state = "shcore_per_monitor"
            return _state
    except Exception:
        pass

    # 3. Vista+ — sistem geneli, cok monitorlu kurulumda yetersiz ama hicten iyi
    try:
        if ctypes.windll.user32.SetProcessDPIAware():
            _state = "system_aware"
            return _state
    except Exception:
        pass

    _state = "unaware"
    return _state


def current_state() -> str:
    """Ayarlanan farkindalik seviyesini dondurur (henuz ayarlanmadiysa cagirir)."""
    return _state if _state is not None else ensure_dpi_aware()
