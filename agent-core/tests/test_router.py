"""Hibrit yürütme: şerit sınıflandırma, dağıtım, onay ve kabuk politikası."""

from __future__ import annotations

import subprocess

import pytest

from agent.core.errors import ShellCommandBlocked
from agent.core.types import ActionResult
from agent.execution.router import (Lane, RouterConfig, SnapshotSource,
                                    ToolRouter, classify)
from agent.execution.shell import ShellConfig, ShellRunner, clip_output
from agent.execution.verifier import Verifier
from agent.perception.pruner import TreePruner
from agent.safety import ApprovalGate, Risk, assess_shell
from helpers import FakeExtractor, node


# --------------------------------------------------------------------------- #
#  Sahteler
# --------------------------------------------------------------------------- #

class FakeExecutor:
    """Eylemi kaydeder ve ``effect`` ile sahte ağacı değiştirir."""

    def __init__(self, extractor, effect=None, ok=True) -> None:
        self.extractor = extractor
        self.effect = effect
        self.ok = ok
        self.calls: list[tuple] = []
        self.verify_flag: bool | None = None

    def set_verify(self, enabled: bool) -> None:
        self.verify_flag = enabled

    def attach_pruner(self, pruner) -> None:
        pass

    def _act(self, action, *args):
        self.calls.append((action, *args))
        if self.effect is not None:
            self.effect(self.extractor)
        if not self.ok:
            return ActionResult.failure(action, "native yol basarisiz")
        return ActionResult(ok=True, action=action, method="invoke")

    def click(self, snapshot, node_id):
        return self._act("click", node_id)

    def type_text(self, snapshot, node_id, text, clear_first=True):
        return self._act("type_text", node_id, text, clear_first)

    def press_key(self, keys, snapshot=None):
        return self._act("press_key", keys)

    def scroll(self, snapshot, direction, amount=3, node_id=None):
        return self._act("scroll", direction, amount, node_id)

    def focus(self, snapshot, node_id):
        return self._act("focus", node_id)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def build(effect=None, ok=True, mode="yes", shell=None, nodes=None):
    """Tam bir router grafiği kurar — gerçek SnapshotSource ve Verifier ile."""
    extractor = FakeExtractor(nodes or [node(name="Dosya"),
                                        node(name="Kaydet", rect=(0, 40, 90, 70))])
    pruner = TreePruner()
    executor = FakeExecutor(extractor, effect=effect, ok=ok)
    clock = Clock()
    source = SnapshotSource(extractor, pruner, clock=clock)
    verifier = Verifier(source.take, settle_ms=10, poll_ms=10, timeout_ms=40,
                        sleep=clock.sleep, clock=clock)
    router = ToolRouter(source, executor, verifier=verifier, shell=shell,
                        gate=ApprovalGate(mode), sleep=clock.sleep,
                        config=RouterConfig(shell_enabled=shell is not None))
    return router, executor, extractor


def add_dialog(extractor) -> None:
    """Her çağrıda **farklı** bir düğüm ekler.

    Aynı düğümü ikinci kez eklemek işe yaramaz: budayıcı
    (role, name, value, rect) dörtlüsüne göre tekilleştirir, ağaç değişmez ve
    test 'eylem etkisiz kaldı' sonucunu ölçmüş olur.
    """
    index = len(extractor.nodes)
    extractor.nodes.append(
        node(name=f"Farklı Kaydet {index}", rect=(0, 40 * index, 200, 40 * index + 30)))


# --------------------------------------------------------------------------- #
#  Sınıflandırma
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("text,lane", [
    ("indirilenler klasorundeki tum png dosyalarini bir klasore tasi", Lane.SHELL),
    ("git deposunda son 5 commit'i listele", Lane.SHELL),
    ("8080 portunu kullanan sureci bul", Lane.SHELL),
    ("log dosyasinda ERROR gecen satirlari filtrele", Lane.SHELL),
    ("chrome'da yeni sekme ac", Lane.UI),
    ("word belgesindeki basligi kalin yap", Lane.UI),
    ("ayarlar penceresinde bildirimleri kapat", Lane.UI),
])
def test_serit_siniflandirma(text, lane):
    decision = classify(text)
    assert decision.lane is lane, decision.scores


def test_belirsiz_hedefte_guven_sifir_ve_ipucu_yumusak():
    """Sınıflandırıcı ekranı görmüyor; emin olmadığında dayatmamalı."""
    decision = classify("bunu benim icin hallet")
    assert decision.confidence == 0.0
    assert not decision.is_confident
    assert "belirsiz" in decision.hint()


def test_url_iceren_hedef_browser_seridine_dusuyor():
    """URL görmek ayırt edicidir; CDP köprüsü eklenince şerit hazır olacak."""
    assert classify("chrome'da github.com sayfasini ac").lane is Lane.BROWSER


def test_browser_ipucu_kopru_yokken_de_yol_gosteriyor():
    """Şerit BROWSER olsa bile yürütme bugün UI üzerinden yapılır.

    Bu yüzden ipucu, var olmayan bir araca değil ``get_state``e yönlendirir.
    """
    router, *_ = build()
    assert "get_state" in router.route_hint("github.com adresini ac")


def test_uygulama_sinyali_agir_basinca_ui_secilir():
    """'chrome'da yeni sekme aç' bir web *içeriği* işi değil, arayüz işidir."""
    assert classify("chrome'da yeni sekme ac ve ayarlar menusune git").lane is Lane.UI


def test_kabuk_kapaliyken_ipucu_kullaniciyi_yaniltmaz():
    router, *_ = build(shell=None)
    hint = router.route_hint("indirilenler klasorunu temizle")
    assert "kabuk erişimi kapalı" in hint


# --------------------------------------------------------------------------- #
#  Bayat id koruması
# --------------------------------------------------------------------------- #

def test_get_state_almadan_eylem_reddedilir():
    """Model durumu görmeden tıklayamaz: [@7] bambaşka bir eleman olabilir."""
    router, executor, _ = build()
    result = router.dispatch("click", {"node_id": 1})

    assert result["ok"] is False
    assert "get_state" in result["error"]
    assert executor.calls == []          # eylem hiç gönderilmedi


def test_eylemden_sonra_yeni_get_state_zorunlu():
    router, executor, _ = build(effect=add_dialog)
    router.dispatch("get_state", {})
    assert router.dispatch("click", {"node_id": 1})["ok"] is True

    ikinci = router.dispatch("click", {"node_id": 1})
    assert ikinci["ok"] is False
    assert "get_state" in ikinci["error"]
    assert len(executor.calls) == 1

    router.dispatch("get_state", {})
    assert router.dispatch("click", {"node_id": 1})["ok"] is True


def test_gecersiz_node_id_acik_hata_verir():
    router, executor, _ = build()
    router.dispatch("get_state", {})
    result = router.dispatch("click", {"node_id": 99})

    assert result["ok"] is False
    assert "Geçerli aralık" in result["error"]
    assert executor.calls == []


def test_get_state_dogrulayicinin_aldigi_agaci_yeniden_kullanir():
    """Doğrulama zaten taze bir ağaç çıkardı; ikinci çıkarım israftır."""
    router, _, extractor = build(effect=add_dialog)
    router.dispatch("get_state", {})
    router.dispatch("click", {"node_id": 1})
    before_calls = extractor.calls

    router.dispatch("get_state", {})
    assert extractor.calls == before_calls


# --------------------------------------------------------------------------- #
#  Doğrulama entegrasyonu
# --------------------------------------------------------------------------- #

def test_etkili_tiklama_dogrulanir():
    router, _, _ = build(effect=add_dialog)
    router.dispatch("get_state", {})
    result = router.dispatch("click", {"node_id": 1})

    assert result["ok"] is True
    assert result["verification"]["verified"] is True
    assert result["lane"] == "ui"


def test_etkisiz_tiklama_hata_olarak_geri_bildirilir():
    """Sessiz başarısızlık, modele *hata* olarak dönmeli.

    ``ActionResult.ok`` True'dur (native çağrı hatasızdı) ama arayüz kımıldamadı.
    Bunu başarı diye raporlamak, modelin sonraki tüm adımlarını yanlış bir
    varsayımın üzerine kurmasına yol açar.
    """
    router, _, _ = build(effect=None)
    router.dispatch("get_state", {})
    result = router.dispatch("click", {"node_id": 1})

    assert result["ok"] is False
    assert result["executed"] is True          # tekrar denemesin diye
    assert result["verification"]["verified"] is False
    assert "AYNI eylemi tekrarlama" in result["verification"]["hint"]


def test_modelin_beklentisi_dogrulamada_kullanilir():
    router, _, _ = build(effect=add_dialog)
    router.dispatch("get_state", {})

    eslesen = router.dispatch("click", {"node_id": 1,
                                        "expect_appears": "Farklı Kaydet 2"})
    assert eslesen["ok"] is True

    router.dispatch("get_state", {})
    esleşmeyen = router.dispatch("click", {"node_id": 1,
                                           "expect_appears": "Yazdır"})
    assert esleşmeyen["ok"] is False


def test_type_text_degeri_dogrular():
    def yaz(extractor):
        extractor.nodes[0] = node(role="Edit", name="Ad", value="Alperen")

    router, executor, _ = build(
        effect=yaz, nodes=[node(role="Edit", name="Ad", value="")])
    router.dispatch("get_state", {})
    result = router.dispatch("type_text", {"node_id": 1, "text": "Alperen"})

    assert result["ok"] is True
    assert executor.calls[0] == ("type_text", 1, "Alperen", True)


def test_yurutucu_hatasi_ipucuyla_doner():
    router, _, _ = build(ok=False)
    router.dispatch("get_state", {})
    result = router.dispatch("click", {"node_id": 1})

    assert result["ok"] is False
    assert "hint" in result


def test_dogrulayici_varsa_yurutucunun_kendi_kontrolu_kapatilir():
    """İki katman da doğrularsa her eylemde iki kez ağaç çıkarılır."""
    _, executor, _ = build()
    assert executor.verify_flag is False


# --------------------------------------------------------------------------- #
#  Onay kapısı
# --------------------------------------------------------------------------- #

def test_reddedilen_eylem_calistirilmaz():
    router, executor, _ = build(mode="ask")
    router.gate = ApprovalGate("ask", prompt=lambda _: "h", output=lambda *_: None)
    router.dispatch("get_state", {})
    result = router.dispatch("click", {"node_id": 1})

    assert result["denied"] is True
    assert result["executed"] is False
    assert executor.calls == []


def test_bilinmeyen_arac_kullanilabilir_listesini_soyler():
    router, _, _ = build()
    result = router.dispatch("teleport", {})
    assert result["ok"] is False
    assert "get_state" in result["error"]


def test_wait_id_leri_gecersiz_kilar():
    """Bekleme sırasında arayüz değişmiş olabilir."""
    router, _, _ = build()
    router.dispatch("get_state", {})
    router.dispatch("wait", {"seconds": 99})       # üst sınıra kırpılır

    result = router.dispatch("click", {"node_id": 1})
    assert result["ok"] is False and "get_state" in result["error"]


# --------------------------------------------------------------------------- #
#  Kabuk şeridi
# --------------------------------------------------------------------------- #

def test_kabuk_kapaliysa_arac_listesinde_gorunmez():
    router, _, _ = build(shell=None)
    assert "run_shell" not in router.tool_names
    assert all(t["function"]["name"] != "run_shell" for t in router.tools())


def test_kabuk_aciksa_arac_sunulur():
    router, _, _ = build(shell=ShellRunner())
    assert "run_shell" in router.tool_names
    assert any(t["name"] == "run_shell" for t in router.tools("anthropic"))


def test_engellenen_komut_calistirilmaz():
    """Bu kalıplar onay verilse bile çalışmaz — tercih değil, sınır."""
    runner = ShellRunner(runner=_never_run)
    with pytest.raises(ShellCommandBlocked):
        runner.run("rm -rf /")


def test_engellenen_komut_kullaniciya_hic_sorulmaz():
    """Onay istemi yalnızca gerçekten çalışabilecek komutlar için anlamlıdır."""
    sorular: list[str] = []
    router, _, _ = build(shell=ShellRunner(runner=_never_run))
    router.gate = ApprovalGate("ask", prompt=lambda q: sorular.append(q) or "e",
                               output=lambda *_: None)

    result = router.dispatch("run_shell", {"command": "sudo rm -rf /"})

    assert result["ok"] is False and result["blocked"] is True
    assert sorular == []


def test_kabuk_komutu_calisir_ve_ciktisi_dondurulur():
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        assert kwargs["stdin"] is subprocess.DEVNULL
        assert kwargs["env"]["GIT_PAGER"] == "cat"
        return subprocess.CompletedProcess(argv, 0, "main\n", "")

    router, _, _ = build(shell=ShellRunner(runner=fake_run))
    result = router.dispatch("run_shell", {"command": "git branch --show-current"})

    assert result["ok"] is True
    assert result["stdout"] == "main"
    assert result["lane"] == "shell"
    assert calls[0][-1] == "git branch --show-current"


def test_zaman_asimi_asili_kalmayi_onler():
    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"], output=b"kismi")

    result = ShellRunner(ShellConfig(timeout=2.0), runner=fake_run).run("sleep 60")
    assert result.timed_out and not result.ok
    assert result.stdout == "kismi"
    assert "2 saniyede" in result.error


def test_uzun_cikti_ortasindan_kirpilir():
    clipped = clip_output("A" * 100 + "SON", 50)
    assert len(clipped) < 120
    assert clipped.startswith("A") and clipped.endswith("SON")
    assert "atlandı" in clipped


def test_pencere_acan_komut_ui_durumunu_gecersiz_kilar():
    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, "", "")

    router, _, _ = build(shell=ShellRunner(runner=fake_run))
    router.dispatch("get_state", {})
    router.dispatch("run_shell", {"command": "open -a Safari"})

    result = router.dispatch("click", {"node_id": 1})
    assert result["ok"] is False and "get_state" in result["error"]


def _never_run(*args, **kwargs):                    # pragma: no cover
    raise AssertionError("engellenen komut çalıştırıldı")


# --------------------------------------------------------------------------- #
#  Kabuk risk sınıflandırması (safety.py)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("command,level", [
    ("ls -la", Risk.LOW),
    ("git status", Risk.LOW),
    ("cat notlar.txt | grep TODO", Risk.LOW),
    ("echo merhaba > dosya.txt", Risk.MEDIUM),       # yönlendirme yazma yapar
    ("mkdir yedek", Risk.MEDIUM),
    ("git push origin main", Risk.HIGH),
    ("rm -rf build", Risk.HIGH),
    ("sudo systemctl restart nginx", Risk.HIGH),
    ("ls && rm -rf build", Risk.HIGH),               # ilk tokene bakmak yetmez
])
def test_kabuk_risk_seviyeleri(command, level):
    assert assess_shell(command).level is level


def test_yuksek_riskli_komut_yes_modunda_bile_sorulur():
    """``--yes`` sıradan işleri hızlandırmak içindir, korumayı kaldırmak için değil."""
    sorular: list[str] = []
    gate = ApprovalGate("yes", prompt=lambda q: sorular.append(q) or "h",
                        output=lambda *_: None)

    assert gate.check("run_shell", None, "ls")[0] is True
    assert sorular == []

    allowed, _ = gate.check("run_shell", None, "rm -rf build")
    assert allowed is False and len(sorular) == 1
