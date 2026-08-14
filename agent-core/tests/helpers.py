"""Test fabrikaları — UIA'sız, sabit girdiler.

Birim testler bilinçli olarak gerçek pencere kullanmaz: filtreleme kuralları
sabit girdilerle doğrulanabilir olmalı, aksi halde sonuç makinede hangi
uygulamanın açık olduğuna göre değişir ve test bir şey kanıtlamaz.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from agent.core.types import Rect, UINode          # noqa: E402
from agent.perception.base import ExtractResult    # noqa: E402


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
            minimized=False, warning="") -> ExtractResult:
    return ExtractResult(
        nodes=list(nodes),
        window_title=title,
        window_handle=1234,
        process_name="test.exe",
        extract_ms=1.0,
        window_rect=Rect.from_ltrb(*window),
        is_minimized=minimized,
        warning=warning,
    )
