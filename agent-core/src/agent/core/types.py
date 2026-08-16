"""Çekirdek veri tipleri.

Bu modül bilinçli olarak platformdan ve COM'dan bağımsızdır: Windows/macOS/Linux
implementasyonlarının hepsi aynı ``UINode`` ve ``Snapshot`` tiplerini üretir,
böylece TreePruner, CLI ve LLM katmanı tek bir şekle bakar.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterable


# --------------------------------------------------------------------------- #
#  Geometri
# --------------------------------------------------------------------------- #

@dataclass(frozen=True, slots=True)
class Rect:
    """Ekran koordinatlarında sınırlayıcı kutu (fiziksel piksel)."""

    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)

    @property
    def area(self) -> int:
        return self.width * self.height

    @property
    def center(self) -> tuple[int, int]:
        return (self.left + self.width // 2, self.top + self.height // 2)

    @property
    def is_empty(self) -> bool:
        return self.area <= 0

    @classmethod
    def from_uia(cls, value: Any) -> "Rect":
        """UIA ``BoundingRectangle`` **property** değerinden Rect üretir.

        DİKKAT — UIA'nın en sinsi tuzağı: aynı bilginin iki biçimi vardır ve
        düzenleri farklıdır.

            UIA_BoundingRectanglePropertyId  ->  (left, top, WIDTH, HEIGHT)
            CurrentBoundingRectangle (struct) ->  (left, top, right, bottom)

        Cache'lenmiş property okuduğumuz için birinci biçim gelir. İkincisi
        sanılırsa genişlik ``right - left`` yerine ``width - left`` olarak
        hesaplanır, çoğu düğümde negatife düşer, alan sıfır çıkar ve eleman
        "görünmez" diye elenir. Hiçbir hata vermez; sadece ağacın büyük kısmı
        sessizce kaybolur. (Ölçüldü: Chrome'da 520 tıklanabilir düğüm.)
        """
        if not value or len(value) != 4:
            return cls(0, 0, 0, 0)
        left, top, width, height = (int(round(float(v))) for v in value)
        return cls(left, top, left + width, top + height)

    @classmethod
    def from_origin_size(cls, x: float, y: float, width: float, height: float) -> "Rect":
        """Köşe + boyut biçiminden kurar — platformdan bağımsız ad.

        macOS'ta ``AXPosition``/``AXSize`` ve ``kCGWindowBounds`` de bu biçimdedir,
        dolayısıyla sayısal olarak ``from_uia`` ile aynı işi yapar. Ayrı bir ad
        olmasının sebebi dürüstlük: mac kodundan ``from_uia`` çağırmak yanlış
        bilgi verirdi ve o metodun docstring'indeki UIA tuzağı orada geçerli
        değildir.

        Birim farkı vardır ve önemlidir: UIA fiziksel piksel, AX ise **point**
        döndürür (Retina'da 2x). Bu tutarlıdır çünkü CGEvent de point ile
        çalışır; yalnızca OCR yolu piksel verir ve ölçeklenmesi gerekir.
        """
        left, top = int(round(float(x))), int(round(float(y)))
        return cls(left, top, left + int(round(float(width))),
                   top + int(round(float(height))))

    @classmethod
    def from_ltrb(cls, left: int, top: int, right: int, bottom: int) -> "Rect":
        """Doğrudan sol/üst/sağ/alt ile kurar (testler ve struct biçimi için)."""
        return cls(left, top, right, bottom)

    def as_tuple(self) -> tuple[int, int, int, int]:
        return (self.left, self.top, self.right, self.bottom)


# --------------------------------------------------------------------------- #
#  UI düğümü
# --------------------------------------------------------------------------- #

# Eyleme girilebilir olduğunu gösteren UIA pattern adları.
ACTIONABLE_PATTERNS = frozenset({
    "Invoke", "Toggle", "ExpandCollapse", "SelectionItem", "Value", "Scroll",
})


@dataclass(slots=True)
class UINode:
    """Erişilebilirlik ağacındaki tek bir eleman.

    ``element`` alanı canlı platform tutamacını (Windows'ta
    ``IUIAutomationElement``) taşır ve **serileştirilmez** — LLM'e giden JSON'da
    yer almaz, yalnızca ActionExecutor kullanır.
    """

    role: str                       # normalize edilmiş kontrol tipi, ör. "Button"
    name: str = ""
    value: str = ""
    automation_id: str = ""
    class_name: str = ""
    rect: Rect = field(default_factory=lambda: Rect(0, 0, 0, 0))
    enabled: bool = True
    offscreen: bool = False
    focusable: bool = False
    focused: bool = False
    patterns: frozenset[str] = frozenset()
    depth: int = 0
    runtime_id: tuple[int, ...] = ()   # bayat referans tespiti için
    element: Any = field(default=None, repr=False, compare=False)

    # Pruner tarafından atanır; anlık görüntüye özeldir.
    node_id: int | None = None

    @property
    def is_actionable(self) -> bool:
        return bool(self.patterns & ACTIONABLE_PATTERNS) or self.focusable

    @property
    def has_text(self) -> bool:
        return bool(self.name.strip() or self.value.strip())

    def signature(self) -> tuple:
        """Snapshot'lar arası karşılaştırma için kimlik (id'den bağımsız)."""
        return (self.role, self.name, self.value, self.rect.as_tuple())

    def to_state_dict(self) -> dict:
        """LLM'e giden kompakt biçim.

        Boş alanlar bilinçli olarak atlanır: yerel modellerde her token
        gecikmedir, ``"value": ""`` taşımanın bilgi değeri yoktur.
        """
        out: dict[str, Any] = {
            "id": self.node_id,
            "role": self.role,
            "name": self.name,
        }
        if self.value:
            out["value"] = self.value
        return out

    def describe(self) -> str:
        """Onay istemi ve loglar için insan-okur tek satır."""
        label = self.name or self.value or self.automation_id or "(isimsiz)"
        return f"[@{self.node_id}] {self.role} '{label}'"


# --------------------------------------------------------------------------- #
#  Anlık görüntü
# --------------------------------------------------------------------------- #

@dataclass(slots=True)
class Snapshot:
    """Belirli bir andaki budanmış UI durumu.

    ``node_id`` değerleri **yalnızca bu snapshot içinde** geçerlidir. Bir eylem
    UI'yı değiştirdiği anda yeni bir snapshot alınmalıdır; ``snapshot_id`` bunu
    denetlemeye yarar.
    """

    active_window: str
    nodes: list[UINode]
    snapshot_id: int
    source: str = "uia"                 # "uia" | "ocr" | "uia+ocr"
    window_handle: int = 0
    process_name: str = ""
    raw_node_count: int = 0
    extract_ms: float = 0.0
    prune_ms: float = 0.0
    warning: str = ""
    created_at: float = field(default_factory=time.monotonic)

    def by_id(self, node_id: int) -> UINode | None:
        for n in self.nodes:
            if n.node_id == node_id:
                return n
        return None

    def to_state_dict(self) -> dict:
        """Spec'teki şema — LLM'in gördüğü tek şey budur.

        ``warning`` yalnızca doluysa eklenir: boş bir düğüm listesinin *neden*
        boş olduğunu bilmeden model "sayfada hiçbir şey yok" diye yanlış karar
        verir.
        """
        state: dict[str, Any] = {
            "active_window": self.active_window,
            "nodes": [n.to_state_dict() for n in self.nodes],
        }
        if self.warning:
            state["warning"] = self.warning
        return state

    def fingerprint(self) -> tuple:
        """Eylem sonrası 'bir şey değişti mi' karşılaştırması için."""
        return (self.active_window, tuple(n.signature() for n in self.nodes))

    def stats(self) -> dict:
        return {
            "active_window": self.active_window,
            "process": self.process_name,
            "source": self.source,
            "raw_nodes": self.raw_node_count,
            "pruned_nodes": len(self.nodes),
            "extract_ms": round(self.extract_ms, 1),
            "prune_ms": round(self.prune_ms, 1),
        }


# --------------------------------------------------------------------------- #
#  Eylem sonucu
# --------------------------------------------------------------------------- #

@dataclass(slots=True)
class ActionResult:
    """Bir eylemin sonucu.

    ``method`` alanı mimarinin sınavıdır: değeri ``pixel_fallback`` ise o eylem
    native UIA pattern'i ile yapılamamış demektir. Bunu ölçmeden "piksel tahmini
    yok" iddiası doğrulanamaz.
    """

    ok: bool
    action: str
    method: str = ""                  # "invoke" | "set_value" | "pixel_fallback" ...
    detail: str = ""
    error: str = ""
    elapsed_ms: float = 0.0
    ui_changed: bool | None = None    # eylem sonrası snapshot farklı mı

    def to_dict(self) -> dict:
        out = {
            "ok": self.ok,
            "action": self.action,
            "method": self.method,
            "elapsed_ms": round(self.elapsed_ms, 1),
        }
        if self.detail:
            out["detail"] = self.detail
        if self.error:
            out["error"] = self.error
        if self.ui_changed is not None:
            out["ui_changed"] = self.ui_changed
        return out

    @classmethod
    def failure(cls, action: str, error: str, elapsed_ms: float = 0.0) -> "ActionResult":
        return cls(ok=False, action=action, error=error, elapsed_ms=elapsed_ms)


def assign_ids(nodes: Iterable[UINode], start: int = 1) -> list[UINode]:
    """Düğümlere sıralı, snapshot'a özel id atar."""
    out = []
    for i, node in enumerate(nodes, start=start):
        node.node_id = i
        out.append(node)
    return out
