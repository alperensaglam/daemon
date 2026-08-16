"""Test fabrikaları — UIA'sız, sabit girdiler.

Birim testler bilinçli olarak gerçek pencere kullanmaz: filtreleme kuralları
sabit girdilerle doğrulanabilir olmalı, aksi halde sonuç makinede hangi
uygulamanın açık olduğuna göre değişir ve test bir şey kanıtlamaz.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from agent.core.types import Rect, Snapshot, UINode     # noqa: E402
from agent.perception.base import ExtractResult         # noqa: E402
from agent.perception.pruner import TreePruner          # noqa: E402


def node(role="Button", name="", value="", patterns=(), rect=(0, 0, 100, 30),
         enabled=True, offscreen=False, focusable=False, focused=False,
         automation_id="", depth=1) -> UINode:
    """Test düğümü. Varsayılanı 'görünür, etkin, normal' bir düğümdür."""
    return UINode(
        role=role, name=name, value=value,
        automation_id=automation_id,
        rect=Rect.from_ltrb(*rect),
        enabled=enabled, offscreen=offscreen,
        focusable=focusable, focused=focused,
        patterns=frozenset(patterns),
        depth=depth,
        runtime_id=(1, depth, abs(hash((role, name))) % 10000),
    )


def extract(nodes, title="Test Penceresi", window=(0, 0, 800, 600),
            minimized=False, warning="", handle=1234) -> ExtractResult:
    return ExtractResult(
        nodes=list(nodes),
        window_title=title,
        window_handle=handle,
        process_name="test.exe",
        extract_ms=1.0,
        window_rect=Rect.from_ltrb(*window),
        is_minimized=minimized,
        warning=warning,
    )


def snapshot(nodes, pruner=None, **kwargs) -> Snapshot:
    """Budanmış anlık görüntü — doğrulama testlerinin girdisi.

    Gerçek ``TreePruner`` kullanılır: doğrulayıcı, budanmış ağaçları
    karşılaştırır, dolayısıyla testin de aynı kırpma ve id atama kurallarını
    görmesi gerekir.
    """
    return (pruner or TreePruner()).prune(extract(nodes, **kwargs))


class FakeExtractor:
    """Sabit bir düğüm listesi döndüren çıkarıcı.

    Her çağrıda **kopya** verir. Aynı nesneleri paylaştırmak, "önce" ve "sonra"
    anlık görüntülerinin aynı düğümlere işaret etmesine ve her farkın boş
    çıkmasına yol açardı — testler o durumda hiçbir şey kanıtlamaz.
    """

    def __init__(self, nodes, title="Test Penceresi", handle=1234) -> None:
        self.nodes = list(nodes)
        self.title = title
        self.handle = handle
        self.calls = 0
        self.error: Exception | None = None

    def extract(self, hwnd=None) -> ExtractResult:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return extract(copy.deepcopy(self.nodes), title=self.title,
                       handle=self.handle)

    def active_window_handle(self) -> int:
        return self.handle

    def list_windows(self):
        return []
