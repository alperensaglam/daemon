"""Rect, tuş eşlemesi ve risk sınıflandırması."""

from __future__ import annotations

import pytest

from agent.action import keys as K
from agent.core.types import ActionResult, Rect
from agent.safety import ApprovalGate, Risk, assess
from helpers import node


# --------------------------------------------------------------------- Rect

def test_uia_rect_width_height_olarak_yorumlanir():
    """UIA property biçimi (left, top, WIDTH, HEIGHT) döner.

    Bu, projedeki en pahalı hatanın regresyon testi: struct biçimi (l,t,r,b)
    sanıldığında genişlik negatife düşüyor, alan sıfır çıkıyor ve düğümler
    'görünmez' diye eleniyordu (ölçüldü: Chrome'da 520 tıklanabilir düğüm).
    """
    r = Rect.from_uia([100, 50, 40, 30])
    assert (r.left, r.top, r.right, r.bottom) == (100, 50, 140, 80)
    assert r.width == 40 and r.height == 30
    assert r.area == 1200


def test_uia_rect_negatif_koordinat():
    r = Rect.from_uia([-8, -8, 1936, 1048])
    assert r.right == 1928 and r.bottom == 1040


def test_bozuk_rect_bos_doner():
    assert Rect.from_uia(None).is_empty
    assert Rect.from_uia([1, 2]).is_empty


def test_merkez_hesabi():
    assert Rect.from_ltrb(0, 0, 100, 50).center == (50, 25)


# --------------------------------------------------------------------- tuşlar

@pytest.mark.parametrize("combo,mods,key", [
    ("enter", (), "enter"),
    ("a", (), "a"),
    ("f5", (), "f5"),
    ("ctrl+s", ("ctrl",), "s"),
    ("ctrl+shift+n", ("ctrl", "shift"), "n"),
    ("alt+f4", ("alt",), "f4"),
])
def test_kombinasyon_ayristirma(combo, mods, key):
    """parse_combo kod değil **ad** döndürür.

    Aynı "ctrl+s" dizesi Windows'ta VK_CONTROL+VK_S, macOS'ta
    kVK_Control+kVK_ANSI_S demektir; kodu ayrıştırma katmanında üretmek iki
    platformun tablolarını oraya sızdırırdı. Kod eşlemesi test_keycodes.py'de.
    """
    assert K.parse_combo(combo) == K.Combo(mods, key)


@pytest.mark.parametrize("alias,kanonik", [
    ("cmd+s", "meta+s"),
    ("command+s", "meta+s"),
    ("win+s", "meta+s"),
    ("control+c", "ctrl+c"),
    ("option+f", "alt+f"),
    ("ctrl+return", "ctrl+enter"),
    ("ctrl+escape", "ctrl+esc"),
])
def test_esanlamlilar_kanonik_ada_cevrilir(alias, kanonik):
    """'meta' iki platformun işletim sistemi tuşunu birleştirir."""
    assert K.parse_combo(alias) == K.parse_combo(kanonik)


def test_bosluk_ve_buyuk_harf_toleransi():
    assert K.parse_combo(" CTRL + S ") == K.parse_combo("ctrl+s")


def test_noktalama_tuslari_ayristirilir():
    """Eski tabloda noktalama hiç yoktu; parse_combo('ctrl+,') hata veriyordu."""
    assert K.parse_combo("ctrl+,") == K.Combo(("ctrl",), "comma")
    assert K.parse_combo("ctrl+comma") == K.parse_combo("ctrl+,")


def test_tekrarlanan_degistirici_bir_kez_sayilir():
    assert K.parse_combo("ctrl+control+s") == K.Combo(("ctrl",), "s")


def test_bilinmeyen_tus_aciklayici_hata_verir():
    with pytest.raises(K.KeyParseError, match="Bilinmeyen tuş"):
        K.parse_combo("ctrl+kaplumbaga")


def test_degistirici_olmayan_tus_on_ekte_reddedilir():
    with pytest.raises(K.KeyParseError, match="değiştirici"):
        K.parse_combo("a+b")


def test_sonda_degistirici_reddedilir():
    """'ctrl+shift' bir eylem değildir; sonda gerçek bir tuş olmalı."""
    with pytest.raises(K.KeyParseError, match="değiştirici"):
        K.parse_combo("ctrl+shift")


def test_bos_kombinasyon_reddedilir():
    with pytest.raises(K.KeyParseError):
        K.parse_combo("")


# --------------------------------------------------------------------- risk

@pytest.mark.parametrize("name", [
    "Sil", "Dosyayı Sil", "Delete", "Kaldır", "Biçimlendir",
    "Gönder", "Satın Al", "Ödeme Yap", "Oturumu Kapat", "Sıfırla",
])
def test_yikici_isimler_yuksek_risk(name):
    assert assess("click", node(name=name)).level is Risk.HIGH


@pytest.mark.parametrize("name", [
    # Türkçe eklemeli bir dil: kök eşlemesi bu çekimleri de yakalamalı.
    "Silme", "Sileceğim", "Kaldırma", "Kaldırıldı", "Ödemeyi Tamamla",
    "Gönderiliyor", "Güncelleme", "Yeniden Başlat", "Sıfırlama",
])
def test_turkce_cekimler_de_yakalanir(name):
    assert assess("click", node(name=name)).level is Risk.HIGH


@pytest.mark.parametrize("name", ["Kaydet", "Aç", "Yenile", "Kopyala", "Geri"])
def test_siradan_isimler_orta_risk(name):
    assert assess("click", node(name=name)).level is Risk.MEDIUM


def test_okuma_eylemleri_dusuk_risk():
    for action in ("get_state", "snapshot", "focus", "scroll", "wait"):
        assert assess(action, node(name="Sil")).level is Risk.LOW


def test_ingilizce_ve_turkce_birlikte_taranir():
    """Arayüz dili kullanıcının sistemine göre değişir; tek dile bakmak
    yarım koruma olur."""
    assert assess("click", node(name="Remove item")).is_high
    assert assess("click", node(name="Öğeyi kaldır")).is_high


def test_risk_gerekcesi_bildirilir():
    a = assess("click", node(name="Hesabı Sil"))
    assert a.reasons and "silme" in a.reasons[0]


# --------------------------------------------------------------------- onay kapısı

def _gate(mode, answer="e"):
    return ApprovalGate(mode, prompt=lambda _: answer, output=lambda *_: None)


def test_ask_modu_sorar_ve_yaniti_uygular():
    assert _gate("ask", "e").check("click", node(name="Kaydet"))[0] is True
    assert _gate("ask", "h").check("click", node(name="Kaydet"))[0] is False


def test_yes_modu_siradan_eylemi_gecirir():
    assert _gate("yes", "h").check("click", node(name="Kaydet"))[0] is True


def test_yes_modu_yuksek_riski_yine_sorar():
    """--yes bir 'her şeye evet' anahtarı değil; yıkıcı eylemler yine sorulur."""
    asked = []
    gate = ApprovalGate("yes", prompt=lambda p: (asked.append(p), "h")[1],
                        output=lambda *_: None)
    allowed, _ = gate.check("click", node(name="Hepsini Sil"))
    assert asked and allowed is False


def test_dry_run_hicbir_seye_izin_vermez():
    gate = _gate("dry_run")
    for name in ("Kaydet", "Sil"):
        assert gate.check("click", node(name=name))[0] is False


def test_dry_run_ne_olacagini_yazar():
    lines = []
    gate = ApprovalGate("dry_run", output=lines.append)
    gate.check("click", node(name="Kaydet"))
    assert any("Kaydet" in line for line in lines)


def test_dusuk_riskli_eylem_ask_modunda_bile_sorulmaz():
    asked = []
    gate = ApprovalGate("ask", prompt=lambda p: (asked.append(p), "e")[1],
                        output=lambda *_: None)
    allowed, _ = gate.check("scroll", None, "down")
    assert allowed is True and not asked


def test_gecersiz_mod_reddedilir():
    with pytest.raises(ValueError):
        ApprovalGate("belki")


def test_kullanici_iptali_reddetme_sayilir():
    def boom(_):
        raise KeyboardInterrupt
    gate = ApprovalGate("ask", prompt=boom, output=lambda *_: None)
    assert gate.check("click", node(name="Kaydet"))[0] is False


# --------------------------------------------------------------------- ActionResult

def test_sonuc_sozlugu_bos_alanlari_tasimaz():
    d = ActionResult(ok=True, action="click", method="invoke", elapsed_ms=5.0).to_dict()
    assert "error" not in d and "detail" not in d and d["method"] == "invoke"


def test_basarisiz_sonuc_yardimcisi():
    r = ActionResult.failure("click", "olmadı")
    assert r.ok is False and r.error == "olmadı"
