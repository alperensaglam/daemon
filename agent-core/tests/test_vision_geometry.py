"""OCR kutusu -> ekran dikdörtgeni çevrimi.

Bu çevrim mac yolundaki en hata yatkın adımdır ve hatası sessizdir: kutular
kayar, tıklamalar yanlış yere gider, hiçbir istisna oluşmaz. Saf fonksiyon
olduğu için işletim sisteminden bağımsız test edilebilir.
"""

from __future__ import annotations

from agent.core.types import Rect
from agent.vision.geometry import box_to_rect

# 400x300 noktalık bir pencere, ekranda (100, 50) konumunda.
ORIGIN = Rect.from_origin_size(100, 50, 400, 300)


# ------------------------------------------------------- Windows yolu (mutlak)

def test_windows_mutlak_sol_ust_kutu():
    """Windows OCR mutlak, sol-üst orijinli piksel kutusu verir."""
    rect = box_to_rect(10, 20, 50, 15, img_w=400, img_h=300,
                       origin=ORIGIN, scale=1.0)
    assert rect.as_tuple() == (110, 70, 160, 85)


def test_olcek_bir_ise_kutu_aynen_otelenir():
    rect = box_to_rect(0, 0, 400, 300, img_w=400, img_h=300,
                       origin=ORIGIN, scale=1.0)
    assert rect.as_tuple() == ORIGIN.as_tuple()


# ------------------------------------------------------------- Retina ölçeği

def test_retina_olcegi_ofsetleri_yariya_indirir():
    """Görüntü 2x piksel; kutu da 2x. Sonuç nokta cinsinden yarıya iner.

    Ölçek uygulanmazsa her dikdörtgen 2x kayar ve piksel tıklamaları
    pencerenin yanlış çeyreğine düşer.
    """
    rect = box_to_rect(20, 40, 100, 30, img_w=800, img_h=600,
                       origin=ORIGIN, scale=2.0)
    assert rect.as_tuple() == (110, 70, 160, 85)


def test_retina_tam_kaplama_pencereyi_verir():
    rect = box_to_rect(0, 0, 800, 600, img_w=800, img_h=600,
                       origin=ORIGIN, scale=2.0)
    assert rect.as_tuple() == ORIGIN.as_tuple()


# ------------------------------------------------- Vision yolu (normalize+flip)

def test_vision_normalize_ve_sol_alt_orijin():
    """Vision 0..1 normalize ve SOL-ALT orijinli kutu verir.

    Üstteki %10'luk şerit: by=0.9, bh=0.1 -> ekranda EN ÜSTTE olmalı.
    Flip yapılmazsa en altta çıkar ve OCR düğümleri dikey olarak aynalanır.
    """
    rect = box_to_rect(0.0, 0.9, 1.0, 0.1, img_w=400, img_h=300,
                       origin=ORIGIN, scale=1.0,
                       normalized=True, bottom_left=True)
    assert rect.top == ORIGIN.top            # en ustte
    assert rect.height == 30                 # 300'un %10'u


def test_vision_alt_serit_altta_kalir():
    rect = box_to_rect(0.0, 0.0, 1.0, 0.1, img_w=400, img_h=300,
                       origin=ORIGIN, scale=1.0,
                       normalized=True, bottom_left=True)
    assert rect.bottom == ORIGIN.bottom


def test_vision_retina_ile_birlikte():
    """Normalize + flip + 2x ölçek birlikte doğru çalışmalı."""
    rect = box_to_rect(0.25, 0.5, 0.5, 0.25, img_w=800, img_h=600,
                       origin=ORIGIN, scale=2.0,
                       normalized=True, bottom_left=True)
    # x: 0.25*800 = 200 px -> 100 nokta -> 100+100 = 200
    # genislik: 0.5*800 = 400 px -> 200 nokta
    # y (alttan): 0.5, yukseklik 0.25 -> ust = 1 - 0.75 = 0.25 -> 150 px -> 75 nokta
    assert rect.as_tuple() == (200, 125, 400, 200)


def test_kutular_pencere_sinirlari_icinde_kalir():
    """Uçtan uca akıl sağlaması: normalize kutular pencereyi asmamalı.

    Canlı testte de doğrulandı (117/117 OCR düğümü pencere içinde kaldı);
    burada aynı değişmez sabit girdiyle korunuyor.
    """
    for bx, by, bw, bh in [(0, 0, 1, 1), (0.1, 0.1, 0.3, 0.2),
                           (0.5, 0.5, 0.5, 0.5), (0.0, 0.95, 1.0, 0.05)]:
        rect = box_to_rect(bx, by, bw, bh, img_w=800, img_h=600,
                           origin=ORIGIN, scale=2.0,
                           normalized=True, bottom_left=True)
        assert ORIGIN.left <= rect.left and rect.right <= ORIGIN.right
        assert ORIGIN.top <= rect.top and rect.bottom <= ORIGIN.bottom


def test_sifir_olcek_bolme_hatasi_vermez():
    rect = box_to_rect(10, 10, 20, 20, img_w=100, img_h=100,
                       origin=ORIGIN, scale=0.0)
    assert rect.width == 20
