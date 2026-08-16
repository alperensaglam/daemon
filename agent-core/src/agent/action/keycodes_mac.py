"""Kanonik tuş adı -> macOS sanal tuş kodu (``kVK_*``) ve CGEvent bayrakları.

**Hiçbir import yoktur**; ``keycodes_win`` ile aynı gerekçe.

Önemli sınırlama — bu kodlar **konumsaldır**, karakter değil. ``kVK_ANSI_S``
"ABD düzeninde S'nin bulunduğu fiziksel tuş" demektir. Türkçe-F düzeninde o
konumda başka bir harf vardır, dolayısıyla ``meta+s`` fiziksel olarak S
konumundaki tuşa basar. Doğru çözüm ``UCKeyTranslate`` ile aktif düzeni ters
eşlemektir; bu sürümde yapılmadı ve README'de sınır olarak belirtildi.

Metin **yazma** bundan etkilenmez: ``type_unicode``
``CGEventKeyboardSetUnicodeString`` kullanır ve düzenden bağımsızdır.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
#  Sanal tuş kodları (Carbon HIToolbox/Events.h)
# --------------------------------------------------------------------------- #

KVK: dict[str, int] = {
    # Harfler — ANSI konumları
    "a": 0x00, "s": 0x01, "d": 0x02, "f": 0x03, "h": 0x04, "g": 0x05,
    "z": 0x06, "x": 0x07, "c": 0x08, "v": 0x09, "b": 0x0B, "q": 0x0C,
    "w": 0x0D, "e": 0x0E, "r": 0x0F, "y": 0x10, "t": 0x11, "o": 0x1F,
    "u": 0x20, "i": 0x22, "p": 0x23, "l": 0x25, "j": 0x26, "k": 0x28,
    "n": 0x2D, "m": 0x2E,
    # Rakamlar
    "1": 0x12, "2": 0x13, "3": 0x14, "4": 0x15, "5": 0x17, "6": 0x16,
    "7": 0x1A, "8": 0x1C, "9": 0x19, "0": 0x1D,
    # Noktalama
    "equals": 0x18, "minus": 0x1B, "rightbracket": 0x1E, "leftbracket": 0x21,
    "quote": 0x27, "semicolon": 0x29, "backslash": 0x2A, "comma": 0x2B,
    "slash": 0x2C, "period": 0x2F, "backtick": 0x32,
    # Kontrol tuşları
    "enter": 0x24, "tab": 0x30, "space": 0x31, "backspace": 0x33,
    "esc": 0x35, "capslock": 0x39,
    "delete": 0x75, "home": 0x73, "end": 0x77, "pgup": 0x74, "pgdn": 0x79,
    "left": 0x7B, "right": 0x7C, "down": 0x7D, "up": 0x7E,
    "clear": 0x47,
    # Değiştiriciler (gerçek key-down olayı göndermek için gerekir)
    "meta": 0x37, "shift": 0x38, "alt": 0x3A, "ctrl": 0x3B,
    # Fonksiyon tuşları — sıralı değildir, tek tek yazılmalıdır
    "f1": 0x7A, "f2": 0x78, "f3": 0x63, "f4": 0x76, "f5": 0x60, "f6": 0x61,
    "f7": 0x62, "f8": 0x64, "f9": 0x65, "f10": 0x6D, "f11": 0x67, "f12": 0x6F,
    "f13": 0x69, "f14": 0x6B, "f15": 0x71, "f16": 0x6A, "f17": 0x40,
    "f18": 0x4F, "f19": 0x50, "f20": 0x5A,
}

#: CGEventFlags — CGEventSetFlags için değiştirici maskeleri.
FLAGS: dict[str, int] = {
    "shift": 0x00020000,   # kCGEventFlagMaskShift
    "ctrl":  0x00040000,   # kCGEventFlagMaskControl
    "alt":   0x00080000,   # kCGEventFlagMaskAlternate
    "meta":  0x00100000,   # kCGEventFlagMaskCommand
}

#: macOS'ta karşılığı olmayan adlar — hata mesajı platformu adlandırsın diye.
UNSUPPORTED: frozenset[str] = frozenset({
    "printscreen", "numlock", "scrolllock", "apps", "insert", "pause",
    "f21", "f22", "f23", "f24",
})


def to_code(name: str) -> int:
    """Kanonik tuş adını ``kVK_*`` koduna çevirir.

    Raises:
        KeyError: Ad macOS'ta tanımlı değilse.
    """
    return KVK[name]
