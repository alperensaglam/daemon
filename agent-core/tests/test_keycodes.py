"""Kanonik tuş adı -> platform kodu eşlemeleri.

Kod tabloları hiçbir şey import etmediği için bu testler **her iki işletim
sisteminde de** koşar; Windows kodlarını doğrulamak için Windows makinesi
gerekmez. ``skipif`` kullanılmamasının sebebi budur.
"""

from __future__ import annotations

import pytest

from agent.action import keycodes_mac as MAC
from agent.action import keycodes_win as WIN
from agent.action.keynames import KEY_NAMES, MODIFIERS, parse_combo


# ------------------------------------------------------------------ Windows

@pytest.mark.parametrize("name,code", [
    ("enter", 0x0D),
    ("a", ord("A")),
    ("f5", 0x74),
    ("s", ord("S")),
    ("n", ord("N")),
    ("f4", 0x73),
    ("esc", 0x1B),
    ("delete", 0x2E),
    ("meta", 0x5B),      # Win tuşu
])
def test_windows_sanal_tus_kodlari(name, code):
    assert WIN.to_code(name) == code


def test_windows_genisletilmis_tus_bayragi():
    """Nav tuşları KEYEVENTF_EXTENDEDKEY ister; yoksa numpad ikizleri tetiklenir."""
    for name in ("left", "right", "up", "down", "home", "end", "delete", "insert"):
        assert WIN.to_code(name) in WIN.EXTENDED


# -------------------------------------------------------------------- macOS

@pytest.mark.parametrize("name,code", [
    ("a", 0x00),
    ("s", 0x01),
    ("enter", 0x24),
    ("esc", 0x35),
    ("space", 0x31),
    ("f5", 0x60),
    ("meta", 0x37),      # Command
    ("ctrl", 0x3B),
    ("delete", 0x75),
])
def test_macos_sanal_tus_kodlari(name, code):
    assert MAC.to_code(name) == code


def test_macos_fonksiyon_tuslari_sirali_degildir():
    """F1..F12 macOS'ta ardışık DEĞİLDİR; formülle üretmek sessizce yanlış olur."""
    assert MAC.to_code("f1") == 0x7A
    assert MAC.to_code("f2") == 0x78     # f1 + 1 değil
    assert MAC.to_code("f3") == 0x63


def test_macos_desteklenmeyen_tuslar_adlandirilir():
    """printscreen/numlock macOS'ta yok; hata platformu söylemeli."""
    for name in ("printscreen", "numlock", "scrolllock", "apps"):
        assert name in MAC.UNSUPPORTED
        assert name not in MAC.KVK


# ------------------------------------------------------------------- kapsam

def test_her_degistiricinin_iki_platformda_da_kodu_var():
    for mod in MODIFIERS:
        assert mod in WIN.VK, f"{mod} Windows tablosunda yok"
        assert mod in MAC.KVK, f"{mod} macOS tablosunda yok"
        assert mod in MAC.FLAGS, f"{mod} CGEvent bayrak tablosunda yok"


def test_windows_tablosu_tum_kanonik_adlari_kapsar():
    """Ayrıştırılabilen her ad Windows'ta gönderilebilmeli."""
    eksik = sorted(n for n in KEY_NAMES if n not in WIN.VK)
    assert not eksik, f"Windows kod tablosunda eksik: {eksik}"


def test_macos_tablosu_desteklenmeyenler_disinda_tam():
    eksik = sorted(n for n in KEY_NAMES if n not in MAC.KVK and n not in MAC.UNSUPPORTED)
    assert not eksik, f"macOS kod tablosunda eksik ve UNSUPPORTED'da da yok: {eksik}"


@pytest.mark.parametrize("combo", ["ctrl+s", "meta+a", "alt+f4", "ctrl+shift+n", "ctrl+,"])
def test_ayristirilan_kombinasyon_iki_platformda_da_kodlanabilir(combo):
    """Uçtan uca: ayrıştır -> her iki tabloda da koda çevir."""
    parsed = parse_combo(combo)
    for mod in parsed.mods:
        assert isinstance(WIN.to_code(mod), int)
        assert isinstance(MAC.to_code(mod), int)
    assert isinstance(WIN.to_code(parsed.key), int)
    assert isinstance(MAC.to_code(parsed.key), int)
