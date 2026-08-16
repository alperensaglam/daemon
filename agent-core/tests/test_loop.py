"""Agent döngüsü: bütçe, tıkanma tespiti ve bağlam hijyeni.

Model sahtedir ve önceden yazılmış bir senaryo oynar. Amaç modelin zekâsını
değil, **döngünün kötü davranışa verdiği tepkiyi** ölçmek: aynı çağrıyı
tekrarlayan, hiç araç çağırmayan veya hiç bitirmeyen bir modelde döngü ne
yapıyor?
"""

from __future__ import annotations

import json

from agent.llm.base import LLMController, LLMResponse, ToolCall
from agent.orchestrator.loop import AgentLoop, LoopConfig


class ScriptedLLM(LLMController):
    """Verilen yanıtları sırayla, tükenince baştan döndürür.

    Döngüsel olması bilinçli: gerçek bir model de tıkandığında aynı örüntüyü
    tekrarlar. Tek elemanlı bir senaryo "aynı çağrıyı üst üste üreten model"
    demektir.
    """

    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.turn = 0
        self.seen: list[list[dict]] = []
        self.tools: list[dict] = []

    def propose(self, messages, tools) -> LLMResponse:
        # Geçmişin kopyası: döngü listeyi yerinde değiştirir, sonradan
        # bakıldığında hangi turda ne görüldüğü kaybolurdu.
        self.seen.append([dict(m) for m in messages])
        self.tools = tools
        response = self.responses[self.turn % len(self.responses)]
        self.turn += 1
        return response

    def name(self) -> str:
        return "scripted"


class FakeRouter:
    """Araç çağrılarını kaydeder ve sabit sonuç döndürür."""

    def __init__(self, results=None, hint="") -> None:
        self.results = dict(results or {})
        self.hint = hint
        self.calls: list[tuple[str, dict]] = []

    def tools(self):
        return [{"type": "function", "function": {"name": "get_state"}}]

    def route_hint(self, goal):
        return self.hint

    def dispatch(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "done":
            return {"ok": True, "done": True, "success": True,
                    "summary": arguments.get("summary", "")}
        return self.results.get(name, {"ok": True})


def call(name, **arguments) -> LLMResponse:
    return LLMResponse(tool_calls=[ToolCall(name=name, arguments=arguments)])


# --------------------------------------------------------------------------- #

def test_done_dongusu_bitirir():
    llm = ScriptedLLM([
        call("get_state"),
        call("click", node_id=3),
        call("done", success=True, summary="Dosya kaydedildi"),
    ])
    router = FakeRouter()

    result = AgentLoop(llm, router).run("dosyayi kaydet")

    assert result.success and result.stopped == "done"
    assert result.summary == "Dosya kaydedildi"
    assert [name for name, _ in router.calls] == ["get_state", "click", "done"]
    assert result.tool_calls == 3


def test_tekrarlanan_ayni_cagri_donguyu_durdurur():
    """Doğrulama başarısız olduğunda model sık sık aynı çağrıyı üretir.

    Router her seferinde onu dürüstçe çalıştırır; durduran bir şey olmazsa
    kullanıcı 25 adım boyunca hiçbir şey olmadan bekler.
    """
    llm = ScriptedLLM([call("click", node_id=3)])
    router = FakeRouter({"click": {"ok": False, "error": "arayüz değişmedi"}})

    result = AgentLoop(llm, router, LoopConfig(max_repeats=2)).run("tikla")

    assert not result.success
    assert result.stopped == "repeat_guard"
    assert len(router.calls) == 3            # 1 + max_repeats

    # Durmadan önce modele uyarı verildi: körlemesine kesilmiyor.
    uyarilar = [m for m in result.messages
                if m["role"] == "user" and "tekrarlıyorsun" in m["content"]]
    assert len(uyarilar) == 1


def test_farkli_argumanlar_tekrar_sayilmaz():
    llm = ScriptedLLM([
        call("click", node_id=1),
        call("click", node_id=2),
        call("click", node_id=3),
        call("done", success=True, summary="bitti"),
    ])
    router = FakeRouter()
    result = AgentLoop(llm, router, LoopConfig(max_repeats=1)).run("tikla")
    assert result.stopped == "done"


def test_arac_cagirmayan_model_once_uyarilir_sonra_durur():
    llm = ScriptedLLM([LLMResponse(text="Bunu nasıl yapacağımı bilmiyorum.")])
    router = FakeRouter()

    result = AgentLoop(llm, router, LoopConfig(max_no_tool_turns=1)).run("bir sey yap")

    assert result.stopped == "no_tool"
    assert not router.calls
    nudges = [m for m in result.messages
              if m["role"] == "user" and "Araç çağırmadın" in m["content"]]
    assert len(nudges) == 1


def test_adim_butcesi_dolunca_durur():
    """Hiç bitirmeyen bir model, kullanıcıyı süresiz bekletmemeli."""
    llm = ScriptedLLM([call("get_state"), call("click", node_id=1)])
    router = FakeRouter()
    result = AgentLoop(llm, router, LoopConfig(max_steps=4)).run("sonsuz")

    assert result.stopped == "step_limit"
    assert len(router.calls) == 4


def test_eski_durum_ciktilari_baglamdan_dusurulur():
    """Bağlamda dört ağaç JSON'u varsa model eski durumu okumaya başlar.

    Ölçülen büyüklük bunu zorunlu kılıyor: Chrome'da tek bir durum ~2300 token.
    Eylem sonuçları (küçük ve *neden* başarısız olunduğunu taşıyan) korunur.
    """
    llm = ScriptedLLM([call("get_state"), call("click", node_id=1)])
    router = FakeRouter({"get_state": {"active_window": "Not Defteri",
                                       "nodes": [{"id": 1, "role": "Edit"}]}})

    result = AgentLoop(llm, router, LoopConfig(max_steps=8, keep_states=2)).run("oku")

    durumlar = [m for m in result.messages if m.get("name") == "get_state"]
    tam = [m for m in durumlar if "active_window" in m["content"]]
    dusurulen = [m for m in durumlar if "bağlamdan çıkarıldı" in m["content"]]

    assert len(durumlar) == 4
    assert len(tam) == 2 and len(dusurulen) == 2
    # Eylem sonuçları korunur: 'neden' bilgisi bağlamdan atılmaz.
    assert sum(1 for m in result.messages if m.get("name") == "click") == 4
    # En yeni ikisi tam kalmalı — sıra korunuyor.
    assert "active_window" in durumlar[-1]["content"]


def test_arac_sonuclari_openai_bicimiyle_yazilir():
    """Geçmiş, sağlayıcıların beklediği biçimde olmalı.

    ``tool`` rolündeki mesaj bir ``tool_call_id`` taşımazsa çoğu uç isteği
    reddeder; yerel modeller ise sessizce bağlamı bozar.
    """
    llm = ScriptedLLM([call("click", node_id=7),
                       call("done", success=True, summary="ok")])
    router = FakeRouter()
    result = AgentLoop(llm, router).run("tikla")

    assistant = next(m for m in result.messages if m.get("tool_calls"))
    tool_message = next(m for m in result.messages if m["role"] == "tool")

    assert assistant["tool_calls"][0]["function"]["name"] == "click"
    assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == {
        "node_id": 7}
    assert tool_message["tool_call_id"] == assistant["tool_calls"][0]["id"]
    assert json.loads(tool_message["content"])["ok"] is True


def test_yonlendirme_ipucu_sistem_promptuna_eklenir():
    llm = ScriptedLLM([call("done", success=True, summary="ok")])
    router = FakeRouter(hint="Yönlendirme ipucu: run_shell dene.")

    AgentLoop(llm, router).run("indirilenleri temizle")

    system = llm.seen[0][0]
    assert system["role"] == "system"
    assert "run_shell dene" in system["content"]


def test_ipucu_kapatilabilir():
    llm = ScriptedLLM([call("done", success=True, summary="ok")])
    router = FakeRouter(hint="Yönlendirme ipucu: run_shell dene.")

    AgentLoop(llm, router, LoopConfig(include_route_hint=False)).run("hedef")

    assert "run_shell dene" not in llm.seen[0][0]["content"]


def test_olay_geri_cagrisinin_hatasi_gorevi_dusurmez():
    """Arayüz kodundaki bir hata, çalışan bir görevi öldürmemeli."""
    def patlayan(kind, data):
        raise RuntimeError("arayüz çöktü")

    llm = ScriptedLLM([call("done", success=True, summary="tamam")])
    result = AgentLoop(llm, FakeRouter(), on_event=patlayan).run("hedef")

    assert result.success and result.summary == "tamam"
