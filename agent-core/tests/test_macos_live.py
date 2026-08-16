"""macOS arka ucunun canli testleri — gercek pencere ve isletim sistemi izni ister.

Varsayilan olarak ATLANIR. Calistirmak icin:

    pytest --run-gui -m macos

Gereksinimler:
  * Sistem Ayarlari > Gizlilik ve Guvenlik > Erisilebilirlik  (agac okuma)
  * Sistem Ayarlari > Gizlilik ve Guvenlik > Ekran Kaydi      (vision fallback)
Izin, python binary'sine degil onu calistiran uygulamaya (Terminal.app, iTerm2,
VS Code...) verilir ve o uygulama tamamen kapatilip acilmalidir.

Bu testler dogruluk degil **calisirlik** kanitidir: gercek bir ekranda hangi
pencerelerin acik oldugu makineye gore degisir, bu yuzden iddialar kasitli
olarak gevsektir ve degismezlere odaklanir (koordinat uzayi, olcek, sinirlar).
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.macos, pytest.mark.gui]


@pytest.fixture(scope="module")
def extractor():
    from agent.perception.macos_ax import MacAXExtractor
    return MacAXExtractor()


def test_pencere_listesi_izin_olmadan_da_calisir(extractor):
    """list_windows CGWindowList kullanir; erisilebilirlik izni istemez."""
    windows = extractor.list_windows()
    assert windows, "hic ust duzey pencere bulunamadi"
    assert all(w.handle > 0 for w in windows)
    assert sum(1 for w in windows if w.is_active) <= 1


def test_erisilebilirlik_izni_gerekli():
    from agent.perception.macos_ax import accessibility_status
    ok, reason = accessibility_status()
    if not ok:
        pytest.skip(f"erisilebilirlik izni yok: {reason}")


def test_aktif_pencere_agaci_cikarilir(extractor):
    from agent.perception.macos_ax import accessibility_status
    if not accessibility_status()[0]:
        pytest.skip("erisilebilirlik izni yok")

    result = extractor.extract()
    assert result.nodes, f"agac bos geldi: {result.warning}"
    assert result.window_rect.width > 0

    # Koordinat uzayi degismezi: dugumler pencereyle kesismeli. Bir flip veya
    # olcek hatasi bu iddiayi bozar.
    inside = [n for n in result.nodes if not n.rect.is_empty and not n.offscreen]
    assert inside, "hicbir dugum pencere icinde degil — koordinat uzayi bozuk"


def test_budama_sonrasi_dugumler_kalir(extractor):
    from agent.perception.macos_ax import accessibility_status
    from agent.perception.pruner import PruneConfig, TreePruner
    if not accessibility_status()[0]:
        pytest.skip("erisilebilirlik izni yok")

    snapshot = TreePruner(PruneConfig(max_nodes=150)).prune(extractor.extract())
    # Asil regresyon: AX rolleri normalize edilmezse budayici HER SEYI duserdi.
    assert snapshot.nodes, "budama sonrasi hicbir dugum kalmadi (rol eslemesi bozuk?)"


def test_vision_yakalama_ve_ocr(extractor):
    """Ekran Kaydi izni ister. Olcek ve flip matematigini gercek pikselle dogrular."""
    from agent.vision.capture_mac import screen_capture_status
    from agent.vision.fallback import extract_via_vision

    ok, reason = screen_capture_status()
    if not ok:
        pytest.skip(reason)

    windows = [w for w in extractor.list_windows() if w.rect.width > 400]
    if not windows:
        pytest.skip("yeterince buyuk bir pencere yok")
    target = windows[0]

    result = extract_via_vision(target.handle, "tr")
    assert not result.error or result.nodes, result.error
    if not result.nodes:
        pytest.skip("pencerede taninan metin yok")

    # Her OCR dikdortgeni pencerenin icinde kalmali. Sol-alt flip veya Retina
    # olcegi yanlissa bu iddia coker — bu testin asil amaci budur.
    rect = target.rect
    disarida = [n for n in result.nodes
                if n.rect.left < rect.left - 5 or n.rect.right > rect.right + 5
                or n.rect.top < rect.top - 5 or n.rect.bottom > rect.bottom + 5]
    assert not disarida, (
        f"{len(disarida)}/{len(result.nodes)} OCR dikdortgeni pencere disinda — "
        "olcek veya dikey flip yanlis"
    )
