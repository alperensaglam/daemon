"""Tuş adı sözlüğü ve kombinasyon ayrıştırma — platformdan bağımsız katman.

Bu modül **hiçbir platform API'si import etmez**, yalnızca stdlib kullanır.
Bunun iki sonucu var:

1. ``agent.action.keys`` her iki işletim sisteminde de import edilebilir; eskiden
   modül seviyesindeki ``ctypes.windll`` yüzünden macOS'ta tüm test paketi
   toplama aşamasında çöküyordu.
2. Ayrıştırma mantığı gerçek bir klavye olmadan test edilebilir.

Tasarım kararı: ``parse_combo`` sanal tuş **kodu** değil **ad** döndürür.
Aynı ``"ctrl+s"`` dizesi Windows'ta ``VK_CONTROL+VK_S``, macOS'ta
``kVK_Control+kVK_ANSI_S`` demektir; kodu burada üretmek iki platformun
tablolarını bu dosyaya sızdırırdı. Adları döndürüp kod eşlemesini
``keycodes_win`` / ``keycodes_mac``a bırakmak katmanları ayrı tutar.
"""

from __future__ import annotations

from dataclasses import dataclass

# --------------------------------------------------------------------------- #
#  Kanonik adlar
# --------------------------------------------------------------------------- #

#: Kanonik değiştirici adları. ``meta`` iki platformun "işletim sistemi tuşu"nu
#: birleştirir: Windows'ta Win, macOS'ta Command. Böylece ``"meta+s"`` her iki
#: platformda da "kaydet" anlamına gelen taşınabilir bir kombinasyondur.
MODIFIERS = frozenset({"ctrl", "shift", "alt", "meta"})

#: Kullanıcının/LLM'in yazabileceği eşanlamlılar -> kanonik ad.
ALIASES = {
    # değiştiriciler
    "control": "ctrl",
    "option": "alt", "opt": "alt",
    "cmd": "meta", "command": "meta", "super": "meta",
    "win": "meta", "lwin": "meta", "rwin": "meta",
    # normal tuşlar
    "return": "enter",
    "escape": "esc",
    "del": "delete",
    "pageup": "pgup", "pagedown": "pgdn",
    "ins": "insert",
    "caps": "capslock",
}

#: Değiştirici olmayan, tanınan tuş adları.
KEY_NAMES: frozenset[str] = frozenset(
    {
        "backspace", "tab", "clear", "enter", "pause", "capslock", "esc",
        "space", "pgup", "pgdn", "end", "home",
        "left", "up", "right", "down",
        "insert", "delete",
        # Yalnızca Windows'ta karşılığı olanlar; macOS tablosu bunları
        # platform adını söyleyen bir hatayla reddeder.
        "printscreen", "numlock", "scrolllock", "apps",
        # Noktalama — eski tabloda hiç yoktu, dolayısıyla parse_combo("ctrl+,")
        # bilinmeyen tuş hatası veriyordu.
        "minus", "equals", "comma", "period", "slash", "backslash",
        "semicolon", "quote", "backtick", "leftbracket", "rightbracket",
    }
    | {f"f{i}" for i in range(1, 25)}
    | set("0123456789")
    | set("abcdefghijklmnopqrstuvwxyz")
)

#: Doğrudan yazılabilen noktalama karakteri -> kanonik ad ("," -> "comma").
PUNCTUATION = {
    "-": "minus", "=": "equals", ",": "comma", ".": "period", "/": "slash",
    "\\": "backslash", ";": "semicolon", "'": "quote", "`": "backtick",
    "[": "leftbracket", "]": "rightbracket",
}


class KeyParseError(ValueError):
    """Tanınmayan tuş adı veya geçersiz kombinasyon."""


@dataclass(frozen=True, slots=True)
class Combo:
    """Ayrıştırılmış tuş kombinasyonu. Karşılaştırılabilir ve JSON'lanabilir."""

    mods: tuple[str, ...]      # kanonik değiştiriciler, sıralı ve tekrarsız
    key: str                   # kanonik ana tuş adı

    def __str__(self) -> str:
        return "+".join([*self.mods, self.key])


def _canon(part: str) -> str:
    """Tek bir parçayı kanonik ada çevirir."""
    p = part.strip().lower()
    if p in PUNCTUATION:
        return PUNCTUATION[p]
    return ALIASES.get(p, p)


def parse_combo(combo: str | Combo) -> Combo:
    """``"ctrl+shift+s"`` -> ``Combo(("ctrl", "shift"), "s")``.

    Yalnızca **biçim** doğrulanır; tuşun o platformda karşılığı olup olmadığı
    kod tablosunun işidir (``keycodes_*.to_code``). Böylece "ctrl+printscreen"
    macOS'ta ayrıştırılır ama gönderilirken platformu adlandıran bir hata verir.

    Raises:
        KeyParseError: Boş kombinasyon, bilinmeyen tuş veya son sırada olmayan
            bir ana tuş varsa.
    """
    if isinstance(combo, Combo):
        return combo

    # "+" tuşunun kendisi ayırıcıyla çakışır: "ctrl++" -> ctrl + "+"
    raw = str(combo)
    parts = [p for p in raw.split("+")]
    if raw.endswith("++"):
        parts = [*raw[:-2].split("+"), "equals"]

    parts = [_canon(p) for p in parts if p.strip()]
    if not parts:
        raise KeyParseError("Boş tuş kombinasyonu.")

    mods: list[str] = []
    for part in parts[:-1]:
        if part not in MODIFIERS:
            raise KeyParseError(
                f"'{part}' bir değiştirici tuş değil. Geçerli olanlar: "
                f"{', '.join(sorted(MODIFIERS))}"
            )
        if part not in mods:
            mods.append(part)

    main = parts[-1]
    if main in MODIFIERS:
        raise KeyParseError(
            f"'{main}' bir değiştirici; kombinasyonun sonunda gerçek bir tuş olmalı "
            f"(örnek: {main}+s)."
        )
    if main not in KEY_NAMES:
        raise KeyParseError(
            f"Bilinmeyen tuş: '{main}'. Örnekler: enter, tab, esc, f5, ctrl+s, a"
        )
    return Combo(tuple(mods), main)


# --------------------------------------------------------------------------- #
#  Niyet tablosu
# --------------------------------------------------------------------------- #

#: Anlamı aynı, tuşu farklı olan işlemler.
#:
#: Yürütücüler kendi içlerinde ASLA düz kombinasyon dizesi kullanmaz; bunun
#: yerine ``intent_combo("select_all")`` çağırır. Sebep somut bir hata:
#: ``type_text(clear_first=True)`` Windows'ta ``ctrl+a`` + ``delete`` gönderiyordu.
#: macOS'ta ``ctrl+a`` Cocoa metin alanlarında "satır başına git", ``delete`` ise
#: ileri silme demektir — yani naif bir port alanı temizlemez, eski metnin
#: üstüne yazar ve bunu sessizce yapar.
INTENTS: dict[str, dict[str, str]] = {
    "select_all":   {"win32": "ctrl+a",   "darwin": "meta+a"},
    "copy":         {"win32": "ctrl+c",   "darwin": "meta+c"},
    "cut":          {"win32": "ctrl+x",   "darwin": "meta+x"},
    "paste":        {"win32": "ctrl+v",   "darwin": "meta+v"},
    "undo":         {"win32": "ctrl+z",   "darwin": "meta+z"},
    "redo":         {"win32": "ctrl+y",   "darwin": "meta+shift+z"},
    "save":         {"win32": "ctrl+s",   "darwin": "meta+s"},
    "find":         {"win32": "ctrl+f",   "darwin": "meta+f"},
    "new":          {"win32": "ctrl+n",   "darwin": "meta+n"},
    "refresh":      {"win32": "f5",       "darwin": "meta+r"},
    "close_window": {"win32": "ctrl+w",   "darwin": "meta+w"},
    "quit_app":     {"win32": "alt+f4",   "darwin": "meta+q"},
    # Alanı temizlemenin doğru yolu: macOS'ta "delete" geri silmedir, Windows'ta
    # ileri silme. Seçim zaten yapıldığı için ikisi de seçimi siler.
    "clear_field":  {"win32": "delete",   "darwin": "backspace"},
}


def intent_combo(intent: str, platform: str | None = None) -> Combo:
    """Bir niyeti (``"select_all"``) o platformun kombinasyonuna çevirir."""
    if platform is None:
        import sys  # noqa: PLC0415
        platform = sys.platform

    table = INTENTS.get(intent)
    if table is None:
        raise KeyParseError(
            f"Bilinmeyen niyet: '{intent}'. Geçerli olanlar: "
            f"{', '.join(sorted(INTENTS))}"
        )
    combo = table.get(platform)
    if combo is None:
        raise KeyParseError(f"'{intent}' niyetinin '{platform}' karşılığı tanımlı değil.")
    return parse_combo(combo)


#: ``translate`` yalnızca bu harfler için çeviri yapar — bunlar iki platformda da
#: aynı anlamı taşıyan standart uygulama kısayollarıdır.
_TRANSLATABLE = frozenset("acfnopqrsvwxyz")


def translate(combo: str | Combo, platform: str) -> Combo:
    """Windows alışkanlığıyla yazılmış bir kombinasyonu macOS'a uyarlar.

    **Varsayılan olarak çağrılmaz ve çağrıldığında görünür olmalıdır.** Sebep:
    macOS'ta ``ctrl`` gerçek ve yaygın bir değiştiricidir — ``ctrl+a`` satır başı,
    ``ctrl+e`` satır sonu, ``ctrl+k`` satır silme anlamına gelir ve her Cocoa
    metin alanında çalışır. Her ``ctrl``i sessizce ``cmd``ye çevirmek bu
    kısayolları erişilemez kılar ve kullanıcının istemediği komutları tetikler.

    Yalnızca tek değiştiricili ``ctrl+<harf>`` biçimi ve yalnızca standart
    uygulama kısayolları çevrilir; gerisi olduğu gibi döner.
    """
    parsed = parse_combo(combo)
    if platform != "darwin":
        return parsed
    if parsed.mods == ("ctrl",) and parsed.key in _TRANSLATABLE:
        return Combo(("meta",), parsed.key)
    if parsed.mods == ("ctrl", "shift") and parsed.key in _TRANSLATABLE:
        return Combo(("meta", "shift"), parsed.key)
    return parsed
