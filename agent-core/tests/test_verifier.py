"""Durum farkı ve eylem doğrulama.

Testlerin tamamı sahte anlık görüntülerle çalışır: doğrulamanın doğruluğu
"hangi uygulama açıktı"ya bağlı olmamalı. Zaman da sahtedir — gerçek
``time.sleep`` ile yoklama testleri saniyeler sürerdi ve zamanlamaya bağlı
olarak kırılgan olurdu.
"""

from __future__ import annotations

import pytest

from agent.core.errors import ActionVerificationError, NoActiveWindow
from agent.execution.verifier import (Expectation, ExpectationKind, StateDiff,
                                      Verifier, expectation_for, node_key)
from helpers import node, snapshot


class Clock:
    """Elle ilerletilen saat + sleep. Yoklama davranışını ölçmek için."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


# --------------------------------------------------------------- node_key

def test_node_key_deger_degisiminden_etkilenmez():
    """Metin kutusuna yazmak düğümün kimliğini değiştirmemeli.

    ``value`` kimliğe girseydi, her ``type_text`` çağrısı farkta "eski düğüm
    kayboldu + yeni düğüm geldi" olarak görünür ve 'değer güncellendi mi'
    sorusu hiç sorulamazdı.
    """
    before = node(role="Edit", name="Ara", value="")
    after = node(role="Edit", name="Ara", value="merhaba")
    assert node_key(before) == node_key(after)


def test_node_key_automation_id_onceliklidir():
    a = node(role="Button", name="Tamam", automation_id="okBtn")
    b = node(role="Button", name="OK", automation_id="okBtn")
    assert node_key(a) == node_key(b)


# --------------------------------------------------------------- StateDiff

def test_ayni_agac_farksizdir():
    nodes = [node(name="Kaydet"), node(name="İptal", rect=(0, 40, 100, 70))]
    diff = StateDiff.between(snapshot(nodes), snapshot(nodes))
    assert diff.is_empty
    assert diff.summary() == "arayüzde hiçbir değişiklik yok"


def test_deger_degisimi_yakalanir():
    before = snapshot([node(role="Edit", name="Ad", value="")])
    after = snapshot([node(role="Edit", name="Ad", value="Alperen")])
    diff = StateDiff.between(before, after)

    assert not diff.is_empty
    assert len(diff.changed) == 1
    assert "value" in diff.changed[0].fields
    assert not diff.added and not diff.removed


def test_beliren_ve_kaybolan_dugumler():
    before = snapshot([node(name="Dosya")])
    after = snapshot([node(name="Dosya"),
                      node(name="Kaydet", rect=(0, 40, 100, 70))])
    diff = StateDiff.between(before, after)
    assert [n.name for n in diff.added] == ["Kaydet"]

    reverse = StateDiff.between(after, before)
    assert [n.name for n in reverse.removed] == ["Kaydet"]


def test_pencere_degisimi_farkta_gorunur():
    before = snapshot([node(name="Dosya")], title="Not Defteri")
    after = snapshot([node(name="Dosya")], title="Farklı Kaydet")
    diff = StateDiff.between(before, after)
    assert diff.window_changed
    assert not diff.is_empty


def test_toplu_kayma_scroll_olarak_okunur():
    """Aynı yönde kayan çok sayıda düğüm kaydırmadır.

    Tek tek bakıldığında bunlar 'yer değiştirmiş düğümler'dir; kaydırmayı
    ayırt eden şey yönün ortak olmasıdır.
    """
    before = snapshot([node(name=f"Satır {i}", rect=(0, i * 40, 200, i * 40 + 30))
                       for i in range(5)])
    after = snapshot([node(name=f"Satır {i}",
                           rect=(0, i * 40 - 100, 200, i * 40 - 70))
                      for i in range(5)])
    assert StateDiff.between(before, after).looks_scrolled


# -------------------------------------------------------------- Expectation

def test_deger_beklentisi_kirpilmis_metni_kabul_eder():
    """Budayıcı 80 karakterden uzun metni kırpar; karşılaştırma bunu bilmeli.

    Bilmeseydi uzun bir metin yazan her ``type_text`` çağrısı 'doğrulanamadı'
    diye işaretlenir ve model var olmayan bir hatayı düzeltmeye çalışırdı.
    """
    uzun = "x" * 200
    target = node(role="Edit", name="Not", value="")
    after = snapshot([node(role="Edit", name="Not", value=uzun)])
    diff = StateDiff.between(snapshot([target]), after)

    ok, reason = Expectation.value_equals(target, uzun).check(diff, after)
    assert ok, reason


def test_deger_beklentisi_yanlis_metni_reddeder():
    target = node(role="Edit", name="Not", value="")
    after = snapshot([node(role="Edit", name="Not", value="yanlış")])
    diff = StateDiff.between(snapshot([target]), after)

    ok, reason = Expectation.value_equals(target, "doğru").check(diff, after)
    assert not ok
    assert "'yanlış'" in reason


def test_appears_beklentisi_yeni_dugumde_arar():
    before = snapshot([node(name="Dosya")])
    after = snapshot([node(name="Dosya"),
                      node(name="Farklı Kaydet", rect=(0, 40, 200, 70))])
    diff = StateDiff.between(before, after)

    assert Expectation.appears("Farklı Kaydet").check(diff, after)[0]
    assert not Expectation.appears("Yazdır").check(diff, after)[0]


@pytest.mark.parametrize("action,keys,kind", [
    ("click", None, ExpectationKind.ANY_CHANGE),
    ("scroll", None, ExpectationKind.SCROLLED),
    ("wait", None, ExpectationKind.NONE),
    ("run_shell", None, ExpectationKind.NONE),
    ("press_key", "ctrl+s", ExpectationKind.ANY_CHANGE),
    ("press_key", "alt+f4", ExpectationKind.WINDOW_CHANGED),
])
def test_varsayilan_beklentiler(action, keys, kind):
    assert expectation_for(action, keys=keys).kind is kind


def test_modelin_bildirdigi_beklenti_varsayilani_ezer():
    """Modelin beklentisi göreve özgüdür; tablo yalnızca tahmindir."""
    expectation = expectation_for("click", expect_appears="Farklı Kaydet")
    assert expectation.kind is ExpectationKind.APPEARS


# ---------------------------------------------------------------- Verifier

def _verifier(snapshots, clock):
    """Sırayla verilen görüntüleri döndüren doğrulayıcı."""
    queue = list(snapshots)

    def take():
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return Verifier(take, settle_ms=60, poll_ms=80, timeout_ms=400,
                    sleep=clock.sleep, clock=clock)


def test_degisiklik_gorulunce_hemen_doner():
    clock = Clock()
    before = snapshot([node(name="Dosya")])
    after = snapshot([node(name="Dosya"), node(name="Kaydet", rect=(0, 40, 90, 70))])

    report = _verifier([after], clock).verify("click", before)

    assert report.satisfied
    assert report.attempts == 1
    assert report.after is after
    assert clock.slept == [0.06]        # yalnızca oturma süresi
    assert report.hint == ""


def test_gec_gelen_degisiklik_yoklamayla_yakalanir():
    """Bazı arayüzler 60 ms'de değil, birkaç yüz ms'de tepki verir.

    Tek gözlem yapılsaydı bu durum 'eylem etkisiz' sayılır, model çalışan bir
    eylemi geri almaya veya tekrarlamaya çalışırdı.
    """
    clock = Clock()
    before = snapshot([node(name="Dosya")])
    gec = snapshot([node(name="Dosya"), node(name="Kaydet", rect=(0, 40, 90, 70))])

    report = _verifier([snapshot([node(name="Dosya")]), gec], clock).verify(
        "click", before)

    assert report.satisfied
    assert report.attempts == 2
    assert clock.slept == [0.06, 0.08]


def test_hic_degismezse_dogrulama_basarisiz_ve_ipucu_verir():
    clock = Clock()
    before = snapshot([node(name="Dosya")])
    report = _verifier([snapshot([node(name="Dosya")])], clock).verify("click", before)

    assert not report.satisfied
    assert report.attempts > 1                     # zaman aşımına kadar bakıldı
    assert "AYNI eylemi tekrarlama" in report.hint
    assert report.to_dict()["verified"] is False


def test_okunamayan_durum_kapanma_beklentisinde_basaridir():
    """Pencereyi kapatan eylemden sonra ağaç okunamaz — bu başarının kanıtıdır.

    Hata sayılsaydı model kapattığı pencereyi tekrar kapatmaya çalışırdı.
    """
    clock = Clock()

    def take():
        raise NoActiveWindow("aktif pencere yok")

    verifier = Verifier(take, sleep=clock.sleep, clock=clock)
    report = verifier.verify("press_key", snapshot([node(name="Dosya")]),
                             Expectation.window_changed())

    assert report.satisfied
    assert report.error


def test_okunamayan_durum_deger_beklentisinde_basarisizdir():
    clock = Clock()

    def take():
        raise NoActiveWindow("aktif pencere yok")

    target = node(role="Edit", name="Ad")
    report = Verifier(take, sleep=clock.sleep, clock=clock).verify(
        "type_text", snapshot([target]), Expectation.value_equals(target, "x"))

    assert not report.satisfied
    assert "okunamadı" in report.hint


def test_none_beklentisi_hic_gozlem_yapmaz():
    """``wait`` ve ``run_shell`` için ağaç çıkarmak boşa gecikmedir."""
    clock = Clock()
    calls = []

    def take():
        calls.append(1)
        raise AssertionError("gözlem yapılmamalıydı")

    report = Verifier(take, sleep=clock.sleep, clock=clock).verify(
        "wait", snapshot([node(name="Dosya")]), Expectation.none())

    assert report.satisfied and not calls and not clock.slept


def test_raise_for_status_raporu_tasir():
    clock = Clock()
    before = snapshot([node(name="Dosya")])
    report = _verifier([snapshot([node(name="Dosya")])], clock).verify("click", before)

    with pytest.raises(ActionVerificationError) as excinfo:
        report.raise_for_status()

    assert excinfo.value.action == "click"
    assert excinfo.value.report is report
    assert "gerçekleşmedi" in str(excinfo.value)
