"""Tuş gönderme cephesi — platformun arka ucuna yönlendirir.

Bu modül eskiden Windows ``SendInput`` implementasyonunun kendisiydi ve modül
seviyesinde ``ctypes.windll`` kullandığı için macOS'ta import edilemiyordu;
tek satırlık bu bağımlılık tüm test paketinin toplanmasını düşürüyordu.

Şimdi yalnızca bir cephe: gerçek iş ``keys_win`` / ``keys_mac`` içinde, ad ve
ayrıştırma mantığı ise platformdan bağımsız ``keynames`` içinde. Genel arayüz
(``press_combo``, ``type_unicode``, ``click_at``, ``scroll_wheel``,
``parse_combo``, ``KeyParseError``) korundu, dolayısıyla çağıranların
değişmesi gerekmez.
"""

from __future__ import annotations

from typing import Any

# Geriye dönük uyumluluk: bu adlar buradan import ediliyordu.
from .keynames import (  # noqa: F401
    ALIASES,
    Combo,
    KEY_NAMES,
    KeyParseError,
    MODIFIERS,
    intent_combo,
    parse_combo,
    translate,
)

_backend: Any = None


def backend(platform: str | None = None):
    """Bu platformun girdi arka ucunu döndürür (ilk çağrıda yüklenir)."""
    global _backend
    if _backend is not None and platform is None:
        return _backend

    from ..platform import resolve_backend, load  # noqa: PLC0415

    module = load(resolve_backend(platform).keys)
    if platform is None:
        _backend = module
    return module


def press_combo(combo: str | Combo) -> None:
    """Bir tuş kombinasyonuna basar ve bırakır."""
    return backend().press_combo(combo)


def type_unicode(text: str, chunk: int = 40, delay: float = 0.0) -> None:
    """Metni klavye düzeninden bağımsız biçimde yazar."""
    return backend().type_unicode(text, chunk=chunk, delay=delay)


def click_at(x: int, y: int, button: str = "left", restore_cursor: bool = True) -> None:
    """Mutlak ekran koordinatına tıklar — yalnızca son çare."""
    return backend().click_at(x, y, button=button, restore_cursor=restore_cursor)


def scroll_wheel(clicks: int, horizontal: bool = False) -> None:
    """Fare tekerleği. Pozitif = yukarı/sağa."""
    return backend().scroll_wheel(clicks, horizontal=horizontal)


def move_cursor(x: int, y: int) -> None:
    """İmleci taşır. macOS'ta kaydırmadan önce zorunludur."""
    return backend().move_cursor(x, y)


def cursor_position() -> tuple[int, int]:
    """İmlecin ekran konumu."""
    return backend().cursor_position()
