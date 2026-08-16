"""Kanonik tuş adı -> Windows sanal tuş kodu.

**Hiçbir import yoktur** (stdlib dahil). Amaç: tablo macOS'ta da yüklenebilsin
ve kod eşlemesi gerçek bir Windows makinesi olmadan test edilebilsin.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
#  Sanal tuş kodları
# --------------------------------------------------------------------------- #

VK: dict[str, int] = {
    "backspace": 0x08, "tab": 0x09, "clear": 0x0C, "enter": 0x0D,
    "shift": 0x10, "ctrl": 0x11, "alt": 0x12,
    "pause": 0x13, "capslock": 0x14, "esc": 0x1B,
    "space": 0x20, "pgup": 0x21, "pgdn": 0x22,
    "end": 0x23, "home": 0x24,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "printscreen": 0x2C, "insert": 0x2D, "delete": 0x2E,
    "meta": 0x5B, "apps": 0x5D,
    "numlock": 0x90, "scrolllock": 0x91,
    # Noktalama (OEM kodları, ABD düzeni konumları)
    "semicolon": 0xBA, "equals": 0xBB, "comma": 0xBC, "minus": 0xBD,
    "period": 0xBE, "slash": 0xBF, "backtick": 0xC0,
    "leftbracket": 0xDB, "backslash": 0xDC, "rightbracket": 0xDD, "quote": 0xDE,
}
for _i in range(1, 25):                      # F1..F24
    VK[f"f{_i}"] = 0x6F + _i
for _c in "0123456789":                      # rakamlar
    VK[_c] = ord(_c)
for _c in "abcdefghijklmnopqrstuvwxyz":      # harfler
    VK[_c] = ord(_c.upper())

#: Genişletilmiş tuş bayrağı gerektirenler (nav tuşları, sağında ikizi olanlar).
EXTENDED: frozenset[int] = frozenset({
    0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2C, 0x2D, 0x2E,
    0x5B, 0x5C, 0x5D, 0x90,
})

#: Bu platformda karşılığı olmayan kanonik adlar (hata mesajı için).
UNSUPPORTED: frozenset[str] = frozenset()


def to_code(name: str) -> int:
    """Kanonik tuş adını sanal tuş koduna çevirir.

    Raises:
        KeyError: Ad bu platformda tanımlı değilse.
    """
    return VK[name]
