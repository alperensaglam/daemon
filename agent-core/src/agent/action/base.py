"""ActionExecutor soyut arayuzu."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..core.types import ActionResult, Snapshot


class ActionExecutor(ABC):
    """Bir anlik goruntudeki dugumler uzerinde eylem yurutur.

    Executor daima bir ``Snapshot`` ile calisir: ``node_id`` degerleri o
    anlik goruntuye aittir ve baska bir snapshot'in id'leriyle karistirilamaz.
    """

    @abstractmethod
    def click(self, snapshot: Snapshot, node_id: int) -> ActionResult: ...

    @abstractmethod
    def type_text(self, snapshot: Snapshot, node_id: int, text: str,
                  clear_first: bool = True) -> ActionResult: ...

    @abstractmethod
    def press_key(self, keys: str, snapshot: Snapshot | None = None) -> ActionResult:
        """Tus gonderir.

        ``snapshot`` verilirse hedef pencerenin on planda oldugu dogrulanir;
        dogrulanamazsa tus gonderilmez. Klavye girisi dugume degil odaga gider,
        bu yuzden hedefsiz kullanim kullanicinin baska penceresine yazabilir.
        """

    @abstractmethod
    def scroll(self, snapshot: Snapshot, direction: str, amount: int = 3,
               node_id: int | None = None) -> ActionResult: ...

    @abstractmethod
    def focus(self, snapshot: Snapshot, node_id: int) -> ActionResult: ...
