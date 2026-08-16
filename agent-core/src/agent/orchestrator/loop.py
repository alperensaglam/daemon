"""Agent döngüsü: model → araç → gözlem → model.

Döngünün kendisi bilinçli olarak ince: karar modelde, yürütme ve doğrulama
``execution.router``dadır. Buradaki kod yalnızca üç şeyi yönetir — konuşma
geçmişi, bütçe ve **tıkanma tespiti**.

Tıkanma tespiti neden burada: yerel bir model doğrulama başarısız olduğunda
sıklıkla aynı çağrıyı tekrar üretir. Router her seferinde onu dürüstçe
çalıştırır, döngü bütçesi biter ve kullanıcı hiçbir şey olmadan 25 adım
beklemiş olur. Aynı imzanın tekrarını saymak, bu davranışı ölçülebilir bir
durdurma koşuluna çevirir.

Bağlam hijyeni de burada: her ``get_state`` sonucu bir ağaç JSON'udur (Chrome'da
~2300 token). Beşinci adımda geçmişteki dört ağaç hâlâ bağlamdaysa yerel bir
modelin penceresi dolar ve model **eski** durumu okumaya başlar. Bu yüzden eski
durum çıktıları yer tutucuyla değiştirilir; sonuncusu tam kalır.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from ..llm.base import LLMController, ToolCall
from ..llm.schemas import SYSTEM_PROMPT

#: Bağlamdan çıkarılmış eski durum çıktısının yerine konan metin.
_DROPPED = ('{"note": "Bu eski get_state çıktısı bağlamdan çıkarıldı. '
            'Güncel durum için yeniden get_state çağır."}')

#: Bağlamda tam hâliyle tutulacak durum çıktısı sayısı.
_KEEP_STATES = 2


@dataclass(slots=True)
class LoopConfig:
    max_steps: int = 25
    max_repeats: int = 2            # aynı çağrının üst üste tekrarı
    max_no_tool_turns: int = 2      # araç çağırmadan geçen tur
    keep_states: int = _KEEP_STATES
    include_route_hint: bool = True


@dataclass(slots=True)
class Step:
    """Tek bir araç çağrısı ve sonucu."""

    index: int
    tool: str
    arguments: dict
    result: dict
    thinking: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.result.get("ok", True))


@dataclass(slots=True)
class LoopResult:
    success: bool
    summary: str
    stopped: str                    # "done" | "step_limit" | "repeat_guard" | ...
    steps: list[Step] = field(default_factory=list)
    messages: list[dict] = field(default_factory=list)

    @property
    def tool_calls(self) -> int:
        return len(self.steps)


class AgentLoop:
    """Bir hedefi araç çağrılarıyla yürüten döngü."""

    def __init__(self, llm: LLMController, router: Any,
                 config: LoopConfig | None = None,
                 on_event: Callable[[str, dict], None] | None = None) -> None:
        """
        Args:
            llm: ``LLMController`` uygulaması (Ollama, Anthropic, sahte model).
            router: ``execution.router.ToolRouter`` — ``tools()`` ve
                ``dispatch()`` yeter, tip zorunlu değil (testler sahte
                router verebilsin diye).
            config: Bütçe ve tıkanma eşikleri.
            on_event: Arayüz/log için olay geri çağrısı.
        """
        self.llm = llm
        self.router = router
        self.config = config or LoopConfig()
        self._on_event = on_event or (lambda kind, data: None)

    # ------------------------------------------------------------------ #

    def run(self, goal: str, messages: list[dict] | None = None) -> LoopResult:
        """Hedefi yürütür.

        ``messages`` verilirse konuşma oradan devam eder; hiyerarşik döngü bir
        alt hedefi çalıştırırken üst bağlamı böyle taşıyacak.
        """
        history = messages if messages is not None else self._start(goal)
        steps: list[Step] = []
        tools = self.router.tools()

        repeats = 0
        last_signature: str | None = None
        idle_turns = 0

        for index in range(1, self.config.max_steps + 1):
            self._compact(history)
            response = self.llm.propose(history, tools)

            if response.thinking:
                self._emit("thinking", {"text": response.thinking})

            if not response.tool_calls:
                idle_turns += 1
                self._emit("message", {"text": response.text})
                history.append({"role": "assistant", "content": response.text})
                if idle_turns > self.config.max_no_tool_turns:
                    return self._finish(False, response.text or
                                        "Model araç çağırmadı.",
                                        "no_tool", steps, history)
                history.append({
                    "role": "user",
                    "content": ("Araç çağırmadın. Görev bitmediyse bir araç "
                                "çağır; bittiyse done ile bildir."),
                })
                continue

            idle_turns = 0
            for call in response.tool_calls:
                signature = _signature(call)
                repeats = repeats + 1 if signature == last_signature else 0
                last_signature = signature

                if repeats > self.config.max_repeats:
                    return self._finish(
                        False,
                        f"Aynı eylem {repeats + 1} kez tekrarlandı: {call}. "
                        "Döngü tıkandı.",
                        "repeat_guard", steps, history)

                step = self._execute(index, call, response, history)
                steps.append(step)

                if step.result.get("done"):
                    return self._finish(
                        bool(step.result.get("success")),
                        str(step.result.get("summary", "")),
                        "done", steps, history)

                if repeats == self.config.max_repeats:
                    history.append({
                        "role": "user",
                        "content": ("Aynı çağrıyı tekrarlıyorsun ve sonuç "
                                    "değişmiyor. Bu yolu bırak: farklı bir "
                                    "eleman dene, işi run_shell ile yapmayı "
                                    "değerlendir ya da done ile durumu bildir."),
                    })

        return self._finish(False, "Adım bütçesi doldu.", "step_limit",
                            steps, history)

    # ------------------------------------------------------------------ #
    #  Yürütme
    # ------------------------------------------------------------------ #

    def _execute(self, index: int, call: ToolCall, response: Any,
                 history: list[dict]) -> Step:
        call_id = call.call_id or f"call_{index}_{call.name}"
        self._emit("tool_call", {"tool": call.name, "arguments": call.arguments})

        result = self.router.dispatch(call.name, call.arguments)

        history.append({
            "role": "assistant",
            "content": response.text or "",
            "tool_calls": [{
                "id": call_id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, ensure_ascii=False),
                },
            }],
        })
        history.append({
            "role": "tool",
            "tool_call_id": call_id,
            "name": call.name,
            "content": json.dumps(result, ensure_ascii=False),
        })

        self._emit("tool_result", {"tool": call.name, "result": result})
        return Step(index=index, tool=call.name, arguments=dict(call.arguments),
                    result=result, thinking=response.thinking)

    # ------------------------------------------------------------------ #
    #  Bağlam
    # ------------------------------------------------------------------ #

    def _start(self, goal: str) -> list[dict]:
        system = SYSTEM_PROMPT
        if self.config.include_route_hint and hasattr(self.router, "route_hint"):
            hint = self.router.route_hint(goal)
            if hint:
                system = f"{system}\n\n{hint}"
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": goal},
        ]

    def _compact(self, history: list[dict]) -> None:
        """Eski ``get_state`` çıktılarını yer tutucuyla değiştirir.

        Yalnızca durum çıktıları hedeflenir: eylem sonuçları küçüktür ve
        *neden* başarısız olunduğunu taşır; onları atmak modelin aynı hataya
        geri dönmesine yol açar.
        """
        keep = max(0, self.config.keep_states)
        seen = 0
        for message in reversed(history):
            if message.get("role") != "tool" or message.get("name") != "get_state":
                continue
            if message.get("content") == _DROPPED:
                continue
            seen += 1
            if seen > keep:
                message["content"] = _DROPPED

    # ------------------------------------------------------------------ #

    def _finish(self, success: bool, summary: str, stopped: str,
                steps: list[Step], history: list[dict]) -> LoopResult:
        self._emit("stop", {"success": success, "reason": stopped,
                            "summary": summary, "steps": len(steps)})
        return LoopResult(success=success, summary=summary, stopped=stopped,
                          steps=steps, messages=history)

    def _emit(self, kind: str, data: dict) -> None:
        try:
            self._on_event(kind, data)
        except Exception:                              # noqa: BLE001
            # Bir arayüz geri çağrısının hatası görevi düşürmemeli.
            pass


def _signature(call: ToolCall) -> str:
    """Tekrar tespiti için çağrı imzası (argüman sırası önemsiz)."""
    return f"{call.name}:{json.dumps(call.arguments, sort_keys=True, ensure_ascii=False)}"
