"""OCR kutularını ekran dikdörtgenine çeviren saf matematik.

Ayrı bir modül olmasının sebebi: mac yolundaki en hata yatkın adım budur ve
hatası sessizdir — kutular kayar, tıklamalar yanlış yere gider, hiçbir istisna
oluşmaz. Buradaki her şey saf fonksiyondur ve birim testi vardır
(``tests/test_vision_geometry.py``).

İki eksen ayrı ayrı yanlış olabilir:

* **Orijin.** Windows OCR mutlak, sol-üst orijinli piksel kutuları verir.
  Apple Vision ise **normalize** (0..1) ve **sol-alt** orijinli kutular verir.
* **Ölçek.** Retina ekranda yakalanan görüntü, pencerenin nokta (point)
  cinsinden boyutunun iki katı pikseldir. Ölçek uygulanmazsa her kutu 2x kayar.
"""

from __future__ import annotations

from ..core.types import Rect


def box_to_rect(
    bx: float, by: float, bw: float, bh: float,
    *,
    img_w: int, img_h: int,
    origin: Rect,
    scale: float = 1.0,
    normalized: bool = False,
    bottom_left: bool = False,
) -> Rect:
    """Bir OCR kutusunu mutlak ekran dikdörtgenine çevirir.

    Args:
        bx, by, bw, bh: Kutu — normalize (0..1) veya görüntü pikseli.
        img_w, img_h: Yakalanan görüntünün piksel boyutu.
        origin: Pencerenin ekrandaki konumu (Windows'ta piksel, macOS'ta point).
        scale: Görüntü pikseli / origin birimi. Retina macOS'ta 2.0,
            Windows'ta 1.0.
        normalized: Kutu 0..1 aralığında mı (Apple Vision böyle verir).
        bottom_left: Kutunun y'si alttan mı ölçülüyor (Apple Vision böyle verir).

    Returns:
        Ekran koordinatlarında Rect (origin ile aynı birimde).
    """
    if normalized:
        bx, bw = bx * img_w, bw * img_w
        by, bh = by * img_h, bh * img_h

    if bottom_left:
        # Vision'da (bx, by) kutunun SOL-ALT köşesidir ve y yukarı doğru artar.
        top_px = img_h - (by + bh)
    else:
        top_px = by

    factor = scale if scale else 1.0
    return Rect.from_origin_size(
        origin.left + bx / factor,
        origin.top + top_px / factor,
        bw / factor,
        bh / factor,
    )
