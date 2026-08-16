"""macOS girdi arka ucu — Quartz CGEvent.

``SendInput``ın karşılığı ``CGEventPost``tır. İki incelik var ve ikisi de
sessiz hatalara yol açar:

1. **Olay kaynağı ``kCGEventSourceStatePrivate`` olmalıdır.** ``HIDSystemState``
   kullanıcının o an fiziksel olarak bastığı değiştiricileri devralır; kullanıcı
   Shift'e basılı tutarken gönderilen ``meta+s`` sessizce ``meta+shift+s``e
   dönüşür.
2. **Hem gerçek değiştirici key-down olayı gönderilir hem de bayrak maskesi
   kurulur.** Yalnızca bayrak kurmak Cocoa uygulamalarında çalışır ama
   Chromium/Electron pencereleri bunu sık sık yok sayar.

Bu modüldeki her şey **Erişilebilirlik izni** ister: Catalina'dan beri başka
uygulamalara sentetik olay göndermek TCC ile kapılıdır. İzin yoksa çağrılar
hata vermez, olaylar sessizce yutulur — bu yüzden yürütücü izni ayrıca kontrol
eder.
"""

from __future__ import annotations

import time

from .keycodes_mac import FLAGS, KVK, UNSUPPORTED, to_code
from .keynames import Combo, KeyParseError, parse_combo

# --------------------------------------------------------------------------- #
#  Quartz bağlaması
# --------------------------------------------------------------------------- #

_Q = None


def _quartz():
    """Quartz modülü — ilk kullanımda yüklenir.

    Tembel yükleme, bu dosyanın Windows'ta ve pyobjc kurulu olmayan bir macOS'ta
    import edilebilmesini sağlar; yalnızca gerçekten tuş göndermeye çalışınca
    açıklayıcı bir hata verir.
    """
    global _Q
    if _Q is None:
        try:
            import Quartz  # noqa: PLC0415
        except ImportError as exc:                       # pragma: no cover
            raise RuntimeError(
                "Quartz yüklenemedi. macOS girdi arka ucu için: "
                "pip install pyobjc-framework-Quartz"
            ) from exc
        _Q = Quartz
    return _Q


_SOURCE = None


def _source():
    """Paylaşılan özel olay kaynağı (bkz. modül docstring'i, madde 1)."""
    global _SOURCE
    if _SOURCE is None:
        Q = _quartz()
        # kCGEventSourceStatePrivate = 1
        _SOURCE = Q.CGEventSourceCreate(1)
    return _SOURCE


def _codes(combo: str | Combo) -> tuple[list[str], int, int]:
    """Kombinasyonu ``([değiştirici adları], ana kod, bayrak maskesi)`` yapar."""
    parsed = parse_combo(combo)
    if parsed.key in UNSUPPORTED:
        raise KeyParseError(
            f"'{parsed.key}' tuşunun macOS'ta karşılığı yok."
        )
    try:
        main = to_code(parsed.key)
    except KeyError as exc:
        raise KeyParseError(
            f"'{parsed.key}' tuşunun macOS'ta karşılığı yok."
        ) from exc

    mask = 0
    for mod in parsed.mods:
        mask |= FLAGS.get(mod, 0)
    return list(parsed.mods), main, mask


# --------------------------------------------------------------------------- #
#  Ortak arka uç protokolü
# --------------------------------------------------------------------------- #

def press_combo(combo: str | Combo) -> None:
    """Bir tuş kombinasyonuna basar ve bırakır."""
    Q = _quartz()
    src = _source()
    mods, main, mask = _codes(combo)

    events = []
    # Değiştiriciler: gerçek key-down (bkz. modül docstring'i, madde 2)
    running = 0
    for mod in mods:
        running |= FLAGS.get(mod, 0)
        ev = Q.CGEventCreateKeyboardEvent(src, KVK[mod], True)
        Q.CGEventSetFlags(ev, running)
        events.append(ev)

    down = Q.CGEventCreateKeyboardEvent(src, main, True)
    Q.CGEventSetFlags(down, mask)
    events.append(down)

    up = Q.CGEventCreateKeyboardEvent(src, main, False)
    Q.CGEventSetFlags(up, mask)
    events.append(up)

    for mod in reversed(mods):
        running &= ~FLAGS.get(mod, 0)
        ev = Q.CGEventCreateKeyboardEvent(src, KVK[mod], False)
        Q.CGEventSetFlags(ev, running)
        events.append(ev)

    for ev in events:
        Q.CGEventPost(Q.kCGHIDEventTap, ev)


def type_unicode(text: str, chunk: int = 20, delay: float = 0.0) -> None:
    """Metni Unicode olayı olarak yazar.

    ``CGEventKeyboardSetUnicodeString``, Windows'taki ``KEYEVENTF_UNICODE``nin
    tam karşılığıdır: klavye düzeninden bağımsızdır, dolayısıyla Türkçe
    karakterler ve emoji kullanıcının düzeni ne olursa olsun doğru gider.
    Tuş kodu 0 olan bir olaya dize iliştirilir.
    """
    Q = _quartz()
    src = _source()

    for start in range(0, len(text), chunk):
        piece = text[start:start + chunk]
        for is_down in (True, False):
            ev = Q.CGEventCreateKeyboardEvent(src, 0, is_down)
            Q.CGEventKeyboardSetUnicodeString(ev, len(piece), piece)
            Q.CGEventPost(Q.kCGHIDEventTap, ev)
        if delay:
            time.sleep(delay)


def cursor_position() -> tuple[int, int]:
    """İmlecin ekran konumu (sol-üst orijin, point)."""
    Q = _quartz()
    ev = Q.CGEventCreate(None)
    point = Q.CGEventGetLocation(ev)
    return int(point.x), int(point.y)


def move_cursor(x: int, y: int) -> None:
    """İmleci mutlak ekran koordinatına taşır.

    Kaydırma için zorunludur: CGEvent tekerlek olayları **imlecin altındaki**
    pencereye gider, odaklanmış pencereye değil.
    """
    Q = _quartz()
    ev = Q.CGEventCreateMouseEvent(_source(), Q.kCGEventMouseMoved,
                                   Q.CGPointMake(float(x), float(y)), 0)
    Q.CGEventPost(Q.kCGHIDEventTap, ev)


def click_at(x: int, y: int, button: str = "left", restore_cursor: bool = True) -> None:
    """Mutlak ekran koordinatına tıklar.

    Yalnızca **son çare**; native AX eylemi varken buna düşülmez. AX rect'leri,
    CGWindowList sınırları ve CGEvent konumları aynı uzaydadır (sol-üst orijin,
    point), bu yüzden hiçbir çevrim gerekmez.
    """
    Q = _quartz()
    src = _source()
    old = cursor_position() if restore_cursor else None

    if button == "right":
        down_t, up_t, btn = Q.kCGEventRightMouseDown, Q.kCGEventRightMouseUp, 1
    else:
        down_t, up_t, btn = Q.kCGEventLeftMouseDown, Q.kCGEventLeftMouseUp, 0

    pos = Q.CGPointMake(float(x), float(y))
    move_cursor(x, y)

    for kind in (down_t, up_t):
        ev = Q.CGEventCreateMouseEvent(src, kind, pos, btn)
        # Tek tıklama olduğunu belirtmezsek bazı uygulamalar olayı yok sayar.
        Q.CGEventSetIntegerValueField(ev, Q.kCGMouseEventClickState, 1)
        Q.CGEventPost(Q.kCGHIDEventTap, ev)

    if old is not None:
        move_cursor(*old)


def scroll_wheel(clicks: int, horizontal: bool = False) -> None:
    """Fare tekerleği. Pozitif = yukarı/sağa.

    ``CGEventCreateScrollWheelEvent(source, unit, wheelCount, wheel1, wheel2)``
    — ``wheel1`` dikey, ``wheel2`` yataydır. İşaret yönü Windows ile aynı
    tutuldu (pozitif = yukarı); doğal kaydırma ayarı kullanıcı tarafında
    uygulanır ve sentetik olayları etkilemez.
    """
    Q = _quartz()
    # kCGScrollEventUnitLine = 1
    if horizontal:
        ev = Q.CGEventCreateScrollWheelEvent(_source(), 1, 2, 0, int(clicks))
    else:
        ev = Q.CGEventCreateScrollWheelEvent(_source(), 1, 1, int(clicks))
    Q.CGEventPost(Q.kCGHIDEventTap, ev)
