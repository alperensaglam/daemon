"""TreePruner kuralları."""

from __future__ import annotations

import json

from agent.perception.pruner import PruneConfig, TreePruner
from helpers import extract, node


def prune(nodes, **kw):
    return TreePruner(PruneConfig(**kw.pop("config", {}))).prune(extract(nodes, **kw))


# --------------------------------------------------------------------- görünürlük

def test_ekran_disi_dugum_elenir():
    snap = prune([node(name="Görünür"), node(name="Gizli", offscreen=True)])
    assert [n.name for n in snap.nodes] == ["Görünür"]


def test_sifir_alanli_dugum_elenir():
    snap = prune([node(name="Normal"), node(name="Nokta", rect=(5, 5, 5, 5))])
    assert [n.name for n in snap.nodes] == ["Normal"]


def test_devre_disi_dugum_elenir():
    snap = prune([node(name="Aktif"), node(name="Pasif", enabled=False)])
    assert [n.name for n in snap.nodes] == ["Aktif"]


def test_pencere_disindaki_dugum_elenir():
    snap = prune([node(name="İçeride", rect=(10, 10, 100, 40)),
                  node(name="Dışarıda", rect=(5000, 5000, 5100, 5040))])
    assert [n.name for n in snap.nodes] == ["İçeride"]


def test_simge_durumunda_pencere_disi_filtresi_uygulanmaz():
    """Simge durumundaki pencere -32000'e park edilir; filtre uygulanırsa
    tüm içerik elenir (gerçek hata: Not Defteri 48 -> 2 düğüm)."""
    nodes = [node(name=f"Düğme{i}", rect=(10 * i, 10, 10 * i + 50, 40)) for i in range(5)]
    snap = TreePruner().prune(
        extract(nodes, window=(-32000, -32000, -31000, -31000), minimized=True)
    )
    assert len(snap.nodes) == 5


# --------------------------------------------------------------------- faydalılık

def test_adsiz_layout_elenir():
    snap = prune([node(role="Pane", name=""), node(role="Group", name=""),
                  node(role="Custom", name=""), node(name="Gerçek düğme")])
    assert [n.name for n in snap.nodes] == ["Gerçek düğme"]


def test_adli_layout_da_elenir():
    """Adı olması layout'u faydalı yapmaz — çocukları zaten ayrıca listelenir."""
    snap = prune([node(role="Group", name="Ana içerik"), node(name="Kaydet")])
    assert [n.name for n in snap.nodes] == ["Kaydet"]


def test_odaklanabilir_layout_elenir():
    """Gerçek hata: odaklanabilir ama adsız Pane'ler gürültü olarak kalıyordu."""
    snap = prune([node(role="Pane", name="", focusable=True), node(name="Tamam")])
    assert [n.name for n in snap.nodes] == ["Tamam"]


def test_eylem_patternli_layout_kalir():
    """Açılıp kapanan bir Group gerçek bir kontroldür."""
    snap = prune([node(role="Group", name="", patterns=["ExpandCollapse"])])
    assert len(snap.nodes) == 1


def test_metin_rolu_sadece_metni_varsa_kalir():
    snap = prune([node(role="Text", name="Merhaba"), node(role="Text", name="")])
    assert [n.name for n in snap.nodes] == ["Merhaba"]


def test_adsiz_buton_kalir():
    """İkon butonların adı olmayabilir; yine de tıklanabilir."""
    snap = prune([node(role="Button", name="", automation_id="save_btn")])
    assert len(snap.nodes) == 1


# --------------------------------------------------------------------- temizlik

def test_ayni_dugum_bir_kez_gecer():
    dup = dict(role="Button", name="Kaydet", rect=(0, 0, 80, 30))
    snap = prune([node(**dup), node(**dup), node(**dup)])
    assert len(snap.nodes) == 1


def test_uzun_metin_kirpilir():
    snap = prune([node(name="x" * 200)])
    assert len(snap.nodes[0].name) <= 80
    assert snap.nodes[0].name.endswith("…")


def test_cok_satirli_metin_tek_satira_indirilir():
    snap = prune([node(name="birinci\n\n  ikinci\tüçüncü")])
    assert snap.nodes[0].name == "birinci ikinci üçüncü"


# --------------------------------------------------------------------- bütçe

def test_dugum_butcesi_asilmaz():
    nodes = [node(name=f"Düğme{i}", rect=(0, i * 10, 60, i * 10 + 9)) for i in range(400)]
    snap = prune(nodes, config={"max_nodes": 25})
    assert len(snap.nodes) == 25


def test_butce_eyleme_girilebilenleri_onceler():
    """Sınır aşıldığında tıklanabilir olanlar salt metinden önce gelir."""
    nodes = [node(role="Text", name=f"Etiket{i}", rect=(0, i * 10, 60, i * 10 + 9))
             for i in range(30)]
    nodes += [node(role="Button", name=f"Düğme{i}", patterns=["Invoke"],
                   rect=(200, i * 10, 260, i * 10 + 9)) for i in range(5)]
    snap = prune(nodes, config={"max_nodes": 5})
    assert all(n.role == "Button" for n in snap.nodes), [n.name for n in snap.nodes]


def test_butce_sonrasi_belge_sirasi_korunur():
    nodes = [node(role="Button", name=f"D{i}", patterns=["Invoke"],
                  rect=(0, i * 10, 60, i * 10 + 9)) for i in range(20)]
    snap = prune(nodes, config={"max_nodes": 6})
    ids = [int(n.name[1:]) for n in snap.nodes]
    assert ids == sorted(ids)


# --------------------------------------------------------------------- kimlik / şema

def test_idler_birden_baslar_ve_ardisiktir():
    snap = prune([node(name=f"D{i}") for i in range(5)])
    assert [n.node_id for n in snap.nodes] == [1, 2, 3, 4, 5]


def test_durum_semasi_spec_ile_uyumlu():
    snap = prune([node(role="Edit", name="Ara", value="merhaba")])
    state = snap.to_state_dict()
    assert set(state) == {"active_window", "nodes"}
    assert state["nodes"][0] == {"id": 1, "role": "Edit", "name": "Ara", "value": "merhaba"}


def test_bos_deger_json_e_yazilmaz():
    """Yerel modelde her token gecikme; boş alan taşımanın bilgi değeri yok."""
    snap = prune([node(role="Button", name="Tamam")])
    assert "value" not in snap.to_state_dict()["nodes"][0]


def test_uyari_varsa_duruma_eklenir():
    snap = TreePruner().prune(extract([], warning="pencere askıda"))
    assert snap.to_state_dict()["warning"] == "pencere askıda"


def test_uyari_yoksa_alan_hic_olmaz():
    snap = prune([node(name="A")])
    assert "warning" not in snap.to_state_dict()


def test_durum_json_serilesebilir():
    snap = prune([node(role="Edit", name="Ünïcödé ığşç", value="değer")])
    assert json.loads(json.dumps(snap.to_state_dict(), ensure_ascii=False))


def test_parmak_izi_degisimi_yakalar():
    a = prune([node(name="Kaydet")])
    b = prune([node(name="Kaydet")])
    c = prune([node(name="Sil")])
    assert a.fingerprint() == b.fingerprint()
    assert a.fingerprint() != c.fingerprint()
