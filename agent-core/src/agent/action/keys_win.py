"""Windows girdi arka ucu — ``SendInput`` sarmalayıcısı.

``SendInput`` doğrudan ctypes ile çağrılır. pywin32'nin sarmalayıcısı INPUT
yapısını doğru kurmayı çağırana bırakıyor; burada yapıyı bir kez doğru tanımlayıp
her yerde aynı yoldan geçmek daha güvenli.

``user32`` bağlaması **modül seviyesinde değil**, ilk kullanımda yapılır: aksi
halde bu dosyayı import etmek macOS'ta ``AttributeError: module 'ctypes' has no
attribute 'windll'`` ile patlar ve tüm test toplama aşamasını düşürür. Yapı
tanımları (``INPUT`` vb.) her platformda yüklenebilir kalır, böylece yapı
katmanı platformdan bağımsız olarak test edilebilir.
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

from .keycodes_win import EXTENDED, VK, to_code
from .keynames import Combo, KeyParseError, parse_combo

# --------------------------------------------------------------------------- #
#  SendInput sabitleri
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


_u32_cache = None


def _u32():
    """user32 bağlaması — ilk çağrıda kurulur (bkz. modül docstring'i)."""
    global _u32_cache
    if _u32_cache is None:
        user32 = ctypes.windll.user32
        user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
        user32.SendInput.restype = wintypes.UINT
        _u32_cache = user32
    return _u32_cache


def _send(inputs: list[INPUT]) -> int:
    if not inputs:
        return 0
    array = (INPUT * len(inputs))(*inputs)
    return _u32().SendInput(len(inputs), array, ctypes.sizeof(INPUT))


def _key_input(vk: int, up: bool) -> INPUT:
    flags = KEYEVENTF_KEYUP if up else 0
    if vk in EXTENDED:
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


def _codes(combo: str | Combo) -> tuple[list[int], int]:
    """Kombinasyonu ``([değiştirici kodları], ana kod)`` çiftine çevirir."""
    parsed = parse_combo(combo)
    try:
        mods = [to_code(m) for m in parsed.mods]
        main = to_code(parsed.key)
    except KeyError as exc:
        raise KeyParseError(
            f"'{parsed.key}' tuşunun Windows'ta karşılığı yok."
        ) from exc
    return mods, main


# --------------------------------------------------------------------------- #
#  Ortak arka uç protokolü
# --------------------------------------------------------------------------- #

def press_combo(combo: str | Combo) -> None:
    """Bir tuş kombinasyonuna basar ve bırakır."""
    mods, main = _codes(combo)
    seq = [_key_input(m, up=False) for m in mods]
    seq.append(_key_input(main, up=False))
    seq.append(_key_input(main, up=True))
    seq.extend(_key_input(m, up=True) for m in reversed(mods))
    _send(seq)


def type_unicode(text: str, chunk: int = 40, delay: float = 0.0) -> None:
    """Metni Unicode olayı olarak yazar.

    ``KEYEVENTF_UNICODE`` klavye düzeninden bağımsızdır: Türkçe karakterler
    (ç, ğ, ı, ö, ş, ü) ve emoji, kullanıcının düzeni ne olursa olsun doğru gider.
    Sanal tuş kodlarıyla yazmak bunu garanti etmez.
    """
    seq: list[INPUT] = []
    for ch in text:
        if ch == "\n":
            seq.append(_key_input(VK["enter"], up=False))
            seq.append(_key_input(VK["enter"], up=True))
        else:
            seq.append(_unicode_input(ch, up=False))
            seq.append(_unicode_input(ch, up=True))
        # Çok uzun metinlerde tek seferde göndermek olay kuyruğunu taşırabilir.
        if len(seq) >= chunk * 2:
            _send(seq)
            seq = []
            if delay:
                time.sleep(delay)
    _send(seq)


def cursor_position() -> tuple[int, int]:
    """İmlecin ekran konumu."""
    point = wintypes.POINT()
    _u32().GetCursorPos(ctypes.byref(point))
    return point.x, point.y


def move_cursor(x: int, y: int) -> None:
    """İmleci mutlak ekran koordinatına taşır."""
    _u32().SetCursorPos(int(x), int(y))


def click_at(x: int, y: int, button: str = "left", restore_cursor: bool = True) -> None:
    """Mutlak ekran koordinatına tıklar.

    Yalnızca **son çare** olarak kullanılır; native UIA pattern'i varken buna
    düşülmez. İmleç varsayılan olarak eski yerine geri alınır, böylece
    kullanıcının faresi kaçırılmaz.
    """
    u32 = _u32()
    old = wintypes.POINT()
    if restore_cursor:
        u32.GetCursorPos(ctypes.byref(old))

    # SendInput mutlak koordinatı 0..65535 normalize uzayda ister.
    vx = u32.GetSystemMetrics(78) or u32.GetSystemMetrics(0)   # SM_CXVIRTUALSCREEN
    vy = u32.GetSystemMetrics(79) or u32.GetSystemMetrics(1)   # SM_CYVIRTUALSCREEN
    ox = u32.GetSystemMetrics(76)                              # SM_XVIRTUALSCREEN
    oy = u32.GetSystemMetrics(77)                              # SM_YVIRTUALSCREEN
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
        u32.SetCursorPos(old.x, old.y)


def scroll_wheel(clicks: int, horizontal: bool = False) -> None:
    """Fare tekerleği. Pozitif = yukarı/sağa."""
    flags = MOUSEEVENTF_HWHEEL if horizontal else MOUSEEVENTF_WHEEL
    data = clicks * WHEEL_DELTA
    _send([INPUT(type=INPUT_MOUSE,
                 union=_INPUTUNION(mi=_MOUSEINPUT(dx=0, dy=0,
                                                  mouseData=ctypes.c_uint32(data).value,
                                                  dwFlags=flags, time=0,
                                                  dwExtraInfo=None)))])
