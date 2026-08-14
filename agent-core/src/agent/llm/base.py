"""LLMController soyut arayüzü — sağlayıcıdan bağımsız agent döngüsü dikişi.

Döngünün kendisi bu aşamada yazılmadı (kullanıcı kararı: önce çekirdek).
Burada duran şey, Ollama ve Anthropic adaptörlerinin dolduracağı sözleşme.

Adaptörler yazılırken önceki projede **bu makinede ölçülen** iki davranışın
hesaba katılması gerekir:

1. Yerel modeller araç çağrısını bazen native ``tool_calls`` alanında değil,
   düz metin içine JSON olarak yazar. O durumda hiçbir araç çalışmaz ve
   kullanıcı ham JSON görür; metinden çağrı kurtaran bir katman şart.

2. qwen3 ailesi düşünme içeriğini ``reasoning`` alanında ayrı gönderir
   (``<think>`` etiketiyle değil). Bu alan okunmazsa modelin neden o kararı
   verdiği tamamen görünmez kalır.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ToolCall:
    """Modelin yapmak istediği tek eylem."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    call_id: str = ""
    raw_text: str = ""      # metinden kurtarıldıysa özgün gövde

    def __str__(self) -> str:
        args = ", ".join(f"{k}={v!r}" for k, v in self.arguments.items())
        return f"{self.name}({args})"


@dataclass(slots=True)
class LLMResponse:
    """Bir modelin tek turluk çıktısı."""

    text: str = ""
    thinking: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"
    salvaged: bool = False   # araç çağrısı düz metinden mi kurtarıldı


class LLMController(ABC):
    """Durumu alıp bir sonraki eylemi öneren bileşen."""

    @abstractmethod
    def propose(self, messages: list[dict], tools: list[dict]) -> LLMResponse:
        """Modeli çağırır ve bir sonraki adımı döndürür.

        Args:
            messages: OpenAI biçiminde konuşma geçmişi.
            tools: ``schemas.openai_tools()`` çıktısı.
        """

    @abstractmethod
    def name(self) -> str:
        """Kullanılan model/sağlayıcı adı — loglar ve hata mesajları için."""
