"""Niyet tablosu ve platform çevirisi."""

from __future__ import annotations

import pytest

from agent.action.keynames import (
    INTENTS,
    Combo,
    KeyParseError,
    intent_combo,
    parse_combo,
    translate,
)


# ------------------------------------------------------------------ niyetler

def test_select_all_platforma_gore_degisir():
    """Bu, sessiz bir hatanın regresyon testi.

    type_text(clear_first=True) Windows'ta ctrl+a gönderiyordu. macOS'ta
    ctrl+a Cocoa metin alanlarında "satır başına git" demektir — yani naif
    bir port alanı temizlemez, eski metnin üstüne yazar ve bunu hata
    vermeden yapar.
    """
    assert intent_combo("select_all", "win32") == Combo(("ctrl",), "a")
    assert intent_combo("select_all", "darwin") == Combo(("meta",), "a")


def test_quit_app_platforma_gore_degisir():
    assert intent_combo("quit_app", "win32") == Combo(("alt",), "f4")
    assert intent_combo("quit_app", "darwin") == Combo(("meta",), "q")


def test_clear_field_macos_geri_siler():
    """macOS'ta 'delete' ileri silmedir; alan temizleme backspace ister."""
    assert intent_combo("clear_field", "darwin").key == "backspace"
    assert intent_combo("clear_field", "win32").key == "delete"


def test_bilinmeyen_niyet_reddedilir():
    with pytest.raises(KeyParseError, match="Bilinmeyen niyet"):
        intent_combo("ucmak", "darwin")


@pytest.mark.parametrize("intent", sorted(INTENTS))
def test_her_niyetin_iki_platformda_da_karsiligi_var(intent):
    for platform in ("win32", "darwin"):
        combo = intent_combo(intent, platform)
        assert isinstance(combo, Combo) and combo.key


# -------------------------------------------------------------------- çeviri

def test_ceviri_darwin_disinda_etkisiz():
    assert translate("ctrl+s", "win32") == Combo(("ctrl",), "s")


def test_ceviri_standart_kisayolu_cmd_yapar():
    assert translate("ctrl+s", "darwin") == Combo(("meta",), "s")
    assert translate("ctrl+shift+z", "darwin") == Combo(("meta", "shift"), "z")


def test_ceviri_cocoa_kisayollarini_bozmaz():
    """macOS'ta ctrl+e (satır sonu) ve ctrl+k (satırı sil) gerçek kısayollardır.

    Bunları sessizce cmd'ye çevirmek hem bu kısayolları erişilemez kılar hem
    de istenmeyen komutlar tetikler; bu yüzden çeviri listesi dardır.
    """
    assert translate("ctrl+e", "darwin") == Combo(("ctrl",), "e")
    assert translate("ctrl+k", "darwin") == Combo(("ctrl",), "k")


def test_ceviri_fonksiyon_tuslarina_dokunmaz():
    assert translate("ctrl+f5", "darwin") == Combo(("ctrl",), "f5")


def test_ceviri_varsayilan_olarak_uygulanmaz():
    """parse_combo tek başına ASLA çeviri yapmamalı."""
    assert parse_combo("ctrl+s") == Combo(("ctrl",), "s")
