"""TreePruner — ham agaci LLM'in okuyabilecegi kompakt duruma indirger.

Bu, sistemin en cok kazanc saglayan bileseni. Tipik bir pencere binlerce dugum
uretir; bunlarin ezici cogunlugu gorunmez layout konteyneridir. Yerel bir modelde
her token gecikmedir (bu makinede ~12 token/s olculdu), dolayisiyla budama
"guzel olurdu" degil, kullanilabilirligin kosuludur.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from ..core.types import ACTIONABLE_PATTERNS, Rect, Snapshot, UINode, assign_ids
from .base import ExtractResult

# Uc rol kumesi ayrik tutulur: bir rol yalnizca tek kumede olmali, aksi halde
# hangi kuralin once calistigi tesadufe kalir.

# Adi olmasa bile kendi basina anlamli olan roller (ikon butonlar boyledir).
INTERACTIVE_ROLES = frozenset({
    "Button", "Edit", "ComboBox", "CheckBox", "RadioButton", "MenuItem",
    "ListItem", "TreeItem", "TabItem", "Hyperlink", "Slider", "SplitButton",
    "Spinner", "Menu", "Tab", "List", "Tree", "Table", "DataItem", "Thumb",
    "ScrollBar", "MenuBar", "Calendar", "DataGrid",
})

# Saf duzen konteynerleri: gercek bir eylem pattern'i yoksa deger tasimazlar.
# Adlarinin olmasi yetmez — "Ana icerik" adli bir Group'a tiklanmaz, cocuklari
# zaten ayrica listelenir.
LAYOUT_ROLES = frozenset({
    "Pane", "Group", "Custom", "Separator", "ToolBar", "TitleBar",
    "Header", "HeaderItem", "ToolTip", "Window",
})

# Sadece metin tasiyan roller: yalnizca gercekten metni varsa tutulur.
TEXT_ROLES = frozenset({"Text", "Document", "Image", "StatusBar", "ProgressBar"})


@dataclass(slots=True)
class PruneConfig:
    """Budama davranisi. Testler sabit degerlerle calisabilsin diye ayri tutuldu."""

    max_nodes: int = 150
    max_text_len: int = 80
    min_area: int = 4           # 2x2 pikselden kucuk = gorunmez sayilir
    keep_disabled: bool = False
    drop_offscreen: bool = True


class TreePruner:
    """Ham ``UINode`` listesini budanmis ``Snapshot``a cevirir."""

    def __init__(self, config: PruneConfig | None = None) -> None:
        self.config = config or PruneConfig()
        self._snapshot_counter = 0

    # ------------------------------------------------------------------ #

    def prune(self, result: ExtractResult) -> Snapshot:
        started = time.perf_counter()
        cfg = self.config

        # Pencere disi filtresi yalnizca gecerli bir dikdortgen varsa uygulanir.
        # Simge durumundaki pencere -32000,-32000'e parklandigi icin bu filtre
        # calistirilirsa tum icerik elenir (olculdu: Not Defteri 48 -> 2).
        window_rect = self.effective_window_rect(result)

        kept: list[UINode] = []
        seen: set[tuple] = set()

        for node in result.nodes:
            if not self._is_visible(node, cfg, window_rect):
                continue
            if not self._is_useful(node):
                continue

            node.name = self._clip(node.name, cfg.max_text_len)
            node.value = self._clip(node.value, cfg.max_text_len)

            key = (node.role, node.name, node.value, node.rect.as_tuple())
            if key in seen:
                continue
            seen.add(key)
            kept.append(node)

        if len(kept) > cfg.max_nodes:
            kept = self._apply_budget(kept, cfg.max_nodes, window_rect)

        assign_ids(kept)

        self._snapshot_counter += 1
        prune_ms = (time.perf_counter() - started) * 1000.0

        return Snapshot(
            active_window=result.window_title,
            nodes=kept,
            snapshot_id=self._snapshot_counter,
            source="uia",
            window_handle=result.window_handle,
            process_name=result.process_name,
            raw_node_count=len(result.nodes),
            extract_ms=result.extract_ms,
            prune_ms=prune_ms,
            warning=result.warning,
        )

    # ------------------------------------------------------------------ #
    #  Filtre kurallari
    # ------------------------------------------------------------------ #

    def _is_visible(self, node: UINode, cfg: PruneConfig, window: Rect | None) -> bool:
        if cfg.drop_offscreen and node.offscreen:
            return False
        if node.rect.area < cfg.min_area:
            return False
        if not cfg.keep_disabled and not node.enabled:
            return False
        # Pencere disina tasan dugumler (sanal listelerde sik) elenir.
        if window is not None and not window.is_empty:
            if (node.rect.right < window.left or node.rect.left > window.right
                    or node.rect.bottom < window.top or node.rect.top > window.bottom):
                return False
        return True

    def _is_useful(self, node: UINode) -> bool:
        """Bu dugum LLM'e gosterilmeye deger mi?

        Sira onemlidir: layout kontrolu, ``focusable`` kontrolunden ONCE gelir.
        Aksi halde Chrome ve Not Defteri'ndeki odaklanabilir ama adsiz ve
        pattern'siz ``Pane``/``Group`` konteynerleri gurultu olarak listeye
        girer (olculdu: Not Defteri'nde 3, Chrome'da 1 adet).
        """
        has_action_pattern = bool(node.patterns & ACTIONABLE_PATTERNS)

        # 1. Saf layout: SADECE gercek bir eylem pattern'i varsa kalir.
        #    Odaklanabilir olmasi veya adinin bulunmasi yeterli degildir.
        if node.role in LAYOUT_ROLES:
            return has_action_pattern

        # 2. Eyleme girilebilen her sey kalir — asil hedef bunlar.
        if has_action_pattern or node.focusable:
            return True

        # 3. Salt metin rolleri: yalnizca gercek metin tasiyorsa.
        if node.role in TEXT_ROLES:
            return bool(node.name.strip() or node.value.strip())

        # 4. Etkilesimli roller adsiz olsa bile kalir (ikon butonlar boyledir;
        #    automation_id LLM'e ipucu verir).
        if node.role in INTERACTIVE_ROLES:
            return True

        # 5. Geri kalan: sadece metni varsa.
        return node.has_text

    # ------------------------------------------------------------------ #
    #  Token butcesi
    # ------------------------------------------------------------------ #

    def _apply_budget(self, nodes: list[UINode], limit: int, window: Rect | None) -> list[UINode]:
        """Siniri asinca en degerli ``limit`` dugumu secer.

        Onceligi eyleme girilebilirlik belirler; esitlikte ekran merkezine
        yakinlik kullanilir, cunku kullanicinin ilgilendigi kontroller
        genellikle merkeze yakindir. Secim sonrasi **belge sirasi** korunur —
        LLM icin okuma duzeni bilgi tasir.
        """
        if window is None or window.is_empty:
            center = (0, 0)
        else:
            center = window.center

        def score(item: tuple[int, UINode]) -> tuple:
            _, node = item
            actionable = 0 if node.is_actionable else 1
            focused = 0 if node.focused else 1
            named = 0 if node.name.strip() else 1
            cx, cy = node.rect.center
            distance = abs(cx - center[0]) + abs(cy - center[1])
            return (actionable, focused, named, distance)

        indexed = list(enumerate(nodes))
        indexed.sort(key=score)
        chosen = sorted(indexed[:limit], key=lambda item: item[0])
        return [node for _, node in chosen]

    # ------------------------------------------------------------------ #

    @staticmethod
    def _clip(text: str, limit: int) -> str:
        text = " ".join(text.split())      # cok satirli etiketleri tek satira indir
        if len(text) <= limit:
            return text
        return text[: limit - 1] + "…"

    @staticmethod
    def effective_window_rect(result: ExtractResult) -> Rect | None:
        """Filtrede kullanilacak pencere dikdortgeni; yoksa ``None``.

        Ayri bir metot olarak durur cunku teshis betikleri de ayni karari
        vermek zorunda — iki yerde farkli mantik olursa teshis yaniltir.
        """
        if result.is_minimized:
            return None
        rect = result.window_rect
        return None if rect.is_empty else rect
