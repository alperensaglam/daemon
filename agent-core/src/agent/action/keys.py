"""Tus adi -> sanal tus kodu esleme ve SendInput sarmalayicisi.

``SendInput`` dogrudan ctypes ile cagrilir. pywin32'nin sarmalayicisi INPUT
yapisini dogru kurmayi cagirana birakiyor; burada yapiyi bir kez dogru tanimlayip
her yerde ayni yoldan gecmek daha guvenli.
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

# --------------------------------------------------------------------------- #
#  Sanal tus kodlari
# --------------------------------------------------------------------------- #

VK: dict[str, int] = {
    "backspace": 0x08, "tab": 0x09, "clear": 0x0C, "enter": 0x0D, "return": 0x0D,
    "shift": 0x10, "ctrl": 0x11, "control": 0x11, "alt": 0x12, "menu": 0x12,
    "pause": 0x13, "capslock": 0x14, "esc": 0x1B, "escape": 0x1B,
    "space": 0x20, "pgup": 0x21, "pageup": 0x21, "pgdn": 0x22, "pagedown": 0x22,
    "end": 0x23, "home": 0x24,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "printscreen": 0x2C, "insert": 0x2D, "delete": 0x2E, "del": 0x2E,
    "win": 0x5B, "lwin": 0x5B, "rwin": 0x5C, "apps": 0x5D,
    "numlock": 0x90, "scrolllock": 0x91,
}
for _i in range(1, 25):                      # F1..F24
    VK[f"f{_i}"] = 0x6F + _i
for _c in "0123456789":                      # rakamlar
    VK[_c] = ord(_c)
for _c in "abcdefghijklmnopqrstuvwxyz":      # harfler
    VK[_c] = ord(_c.upper())

MODIFIERS = {"ctrl", "control", "shift", "alt", "win", "lwin", "rwin"}

# Genisletilmis tus bayragi gerektirenler (nav tuslari, saginda ikizi olanlar).
_EXTENDED = {
    0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2C, 0x2D, 0x2E,
    0x5B, 0x5C, 0x5D, 0x90,
}


class KeyParseError(ValueError):
    """Taninmayan tus adi."""


def parse_combo(combo: str) -> tuple[list[int], int]:
    """``"ctrl+shift+s"`` -> ``([VK_CONTROL, VK_SHIFT], VK_S)``.

    Raises:
        KeyParseError: Bilinmeyen bir tus adi gecerse.
    """
    parts = [p.strip().lower() for p in str(combo).split("+") if p.strip()]
    if not parts:
        raise KeyParseError("Bos tus kombinasyonu.")

    mods: list[int] = []
    for part in parts[:-1]:
        if part not in MODIFIERS:
            raise KeyParseError(
                f"'{part}' bir degistirici tus degil. Gecerli olanlar: "
                f"{', '.join(sorted(MODIFIERS))}"
            )
        mods.append(VK[part])

    main = parts[-1]
    if main not in VK:
        raise KeyParseError(
            f"Bilinmeyen tus: '{main}'. Ornekler: enter, tab, esc, f5, ctrl+s, a"
        )
    return mods, VK[main]


# --------------------------------------------------------------------------- #
#  SendInput
# --------------------------------------------------------------------------- #

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP = 0x0001, 0x0002
KEYEVENTF_UNICODE, KEYEVENTF_SCANCODE = 0x0004, 0x0008

MOUSEEVENTF_MOVE, MOUSEEVENTF_ABSOLUTE = 0x0001, 0x8000
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010
MOUSEEVENTF_WHEEL, MOUSEEVENTF_HWHEEL = 0x0800, 0x1000
WHEEL_DELTA = 120


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG), ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", _INPUTUNION)]


_user32 = ctypes.windll.user32
_user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
_user32.SendInput.restype = wintypes.UINT


def _send(inputs: list[INPUT]) -> int:
    if not inputs:
        return 0
    array = (INPUT * len(inputs))(*inputs)
    return _user32.SendInput(len(inputs), array, ctypes.sizeof(INPUT))


def _key_input(vk: int, up: bool) -> INPUT:
    flags = KEYEVENTF_KEYUP if up else 0
    if vk in _EXTENDED:
        flags |= KEYEVENTF_EXTENDEDKEY
    return INPUT(type=INPUT_KEYBOARD,
                 union=_INPUTUNION(ki=_KEYBDINPUT(wVk=vk, wScan=0, dwFlags=flags,
                                                  time=0, dwExtraInfo=None)))


def _unicode_input(char: str, up: bool) -> INPUT:
    flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0)
    return INPUT(type=INPUT_KEYBOARD,
                 union=_INPUTUNION(ki=_KEYBDINPUT(wVk=0, wScan=ord(char),
                                                  dwFlags=flags, time=0,
                                                  dwExtraInfo=None)))


def press_combo(combo: str) -> None:
    """Bir tus kombinasyonuna basar ve birakir."""
    mods, main = parse_combo(combo)
    seq = [_key_input(m, up=False) for m in mods]
    seq.append(_key_input(main, up=False))
    seq.append(_key_input(main, up=True))
    seq.extend(_key_input(m, up=True) for m in reversed(mods))
    _send(seq)


def type_unicode(text: str, chunk: int = 40, delay: float = 0.0) -> None:
    """Metni Unicode olayi olarak yazar.

    ``KEYEVENTF_UNICODE`` klavye duzeninden bagimsizdir: Turkce karakterler
    (ç, ğ, ı, ö, ş, ü) ve emoji, kullanicinin duzeni ne olursa olsun dogru gider.
    Sanal tus kodlariyla yazmak bunu garanti etmez.
    """
    seq: list[INPUT] = []
    for ch in text:
        if ch == "\n":
            seq.append(_key_input(VK["enter"], up=False))
            seq.append(_key_input(VK["enter"], up=True))
        else:
            seq.append(_unicode_input(ch, up=False))
            seq.append(_unicode_input(ch, up=True))
        # Cok uzun metinlerde tek seferde gondermek olay kuyrugunu tasirabilir.
        if len(seq) >= chunk * 2:
            _send(seq)
            seq = []
            if delay:
                time.sleep(delay)
    _send(seq)


def click_at(x: int, y: int, button: str = "left", restore_cursor: bool = True) -> None:
    """Mutlak ekran koordinatina tiklar.

    Yalnizca **son care** olarak kullanilir; native UIA pattern'i varken buna
    dusulmez. Imlec varsayilan olarak eski yerine geri alinir, boylece
    kullanicinin faresi kacirilmaz.
    """
    old = wintypes.POINT()
    if restore_cursor:
        _user32.GetCursorPos(ctypes.byref(old))

    # SendInput mutlak koordinati 0..65535 normalize uzayda ister.
    vx = _user32.GetSystemMetrics(78) or _user32.GetSystemMetrics(0)   # SM_CXVIRTUALSCREEN
    vy = _user32.GetSystemMetrics(79) or _user32.GetSystemMetrics(1)   # SM_CYVIRTUALSCREEN
    ox = _user32.GetSystemMetrics(76)                                   # SM_XVIRTUALSCREEN
    oy = _user32.GetSystemMetrics(77)                                   # SM_YVIRTUALSCREEN
    nx = int((x - ox) * 65535 / max(vx - 1, 1))
    ny = int((y - oy) * 65535 / max(vy - 1, 1))

    down, up = ((MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP) if button == "right"
                else (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP))

    def mouse(flags: int, data: int = 0) -> INPUT:
        return INPUT(type=INPUT_MOUSE,
                     union=_INPUTUNION(mi=_MOUSEINPUT(dx=nx, dy=ny, mouseData=data,
                                                      dwFlags=flags, time=0,
                                                      dwExtraInfo=None)))

    _send([
        mouse(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE),
        mouse(down | MOUSEEVENTF_ABSOLUTE),
        mouse(up | MOUSEEVENTF_ABSOLUTE),
    ])

    if restore_cursor:
        _user32.SetCursorPos(old.x, old.y)


def scroll_wheel(clicks: int, horizontal: bool = False) -> None:
    """Fare tekerlegi. Pozitif = yukari/saga."""
    flags = MOUSEEVENTF_HWHEEL if horizontal else MOUSEEVENTF_WHEEL
    data = clicks * WHEEL_DELTA
    _send([INPUT(type=INPUT_MOUSE,
                 union=_INPUTUNION(mi=_MOUSEINPUT(dx=0, dy=0,
                                                  mouseData=ctypes.c_uint32(data).value,
                                                  dwFlags=flags, time=0,
                                                  dwExtraInfo=None)))])
