"""AX rol eşlemesinin budayıcının sözlüğüyle tutarlılığı.

Bu dosyadaki testler **hiçbir işletim sistemi API'si kullanmaz**; ne pyobjc ne
Mac gerekir. Yakaladıkları hata sınıfı sessizdir: eşleme tablosuna ``"Buton"``
ya da ``"TextField"`` gibi budayıcının tanımadığı bir değer yazılırsa o rolün
tüm düğümleri eleme aşamasında düşer ve belirti "boş anlık görüntü" olur.
"""

from __future__ import annotations

import pytest

from agent.core.types import ACTIONABLE_PATTERNS
from agent.perception.ax_roles import (
    AX_ACTION_PATTERNS,
    AX_ROLE_MAP,
    AX_SUBROLE_MAP,
    FOCUSABLE_ROLES,
    ROLE_DEFAULT_PATTERNS,
    UNKNOWN_ROLE,
    actions_to_patterns,
    normalize_role,
    role_patterns,
)
from agent.perception.pruner import (
    INTERACTIVE_ROLES,
    LAYOUT_ROLES,
    TEXT_ROLES,
    PruneConfig,
    TreePruner,
)
from helpers import extract, node

VOCABULARY = INTERACTIVE_ROLES | LAYOUT_ROLES | TEXT_ROLES


# ------------------------------------------------------------- sözlük bütünlüğü

@pytest.mark.parametrize("ax_role,mapped", sorted(AX_ROLE_MAP.items()))
def test_her_rol_degeri_budayici_sozlugunde(ax_role, mapped):
    assert mapped in VOCABULARY, (
        f"{ax_role} -> '{mapped}' budayıcının tanıdığı bir rol değil; "
        f"bu roldeki tüm düğümler sessizce elenir."
    )


@pytest.mark.parametrize("subrole,mapped", sorted(AX_SUBROLE_MAP.items()))
def test_her_subrole_degeri_budayici_sozlugunde(subrole, mapped):
    assert mapped in VOCABULARY


def test_budayici_rol_kumeleri_ayrik_kalir():
    """Bir rol yalnızca tek kümede olmalı; aksi halde hangi kuralın önce
    çalıştığı tesadüfe kalır (pruner.py bunu docstring'de söyler ama
    hiçbir şey doğrulamıyordu)."""
    assert not (INTERACTIVE_ROLES & LAYOUT_ROLES)
    assert not (INTERACTIVE_ROLES & TEXT_ROLES)
    assert not (LAYOUT_ROLES & TEXT_ROLES)


def test_varsayilan_pattern_tablosu_gecerli_rolleri_kullanir():
    for role in ROLE_DEFAULT_PATTERNS:
        assert role in VOCABULARY
    for role in FOCUSABLE_ROLES:
        assert role in VOCABULARY


def test_pattern_degerleri_uia_sozlugunde():
    bilinen = ACTIONABLE_PATTERNS | {"ScrollItem"}
    for ax_action, pattern in AX_ACTION_PATTERNS.items():
        assert pattern in bilinen, f"{ax_action} -> '{pattern}' tanınmayan pattern"


# ---------------------------------------------------------------- normalize_role

def test_bilinmeyen_rol_custom_olur():
    assert normalize_role("AXKaplumbaga", "") == UNKNOWN_ROLE
    assert normalize_role("", "") == UNKNOWN_ROLE


@pytest.mark.parametrize("role,subrole,beklenen", [
    # Subrole role'ü yener — Cocoa sekmeleri AXRadioButton rolüyle gelir.
    ("AXRadioButton", "AXTabButton", "TabItem"),
    ("AXTextField", "AXSearchField", "Edit"),
    ("AXTextField", "AXSecureTextField", "Edit"),
    ("AXCheckBox", "AXSwitch", "CheckBox"),
    ("AXRow", "AXOutlineRow", "TreeItem"),
    ("AXRow", "AXTableRow", "DataItem"),
    ("AXWindow", "AXDialog", "Window"),
    # Subrole tanınmıyorsa role'e düşülür.
    ("AXButton", "AXBilinmeyen", "Button"),
    ("AXButton", "", "Button"),
])
def test_subrole_roleden_once_bakilir(role, subrole, beklenen):
    assert normalize_role(role, subrole) == beklenen


# ------------------------------------------------------------ pattern sentezi

def test_ax_eylemleri_pattern_olur():
    assert actions_to_patterns(("AXPress", "AXShowMenu"), role="Button") == {
        "Invoke", "ExpandCollapse"
    }


def test_ayarlanabilir_deger_value_ekler():
    assert "Value" in actions_to_patterns((), role="Edit", value_settable=True)


def test_onay_kutusu_ayarlanabilirse_toggle_alir():
    result = actions_to_patterns((), role="CheckBox", value_settable=True)
    assert "Toggle" in result and "Value" in result


def test_isaretli_menu_ogesi_toggle_alir():
    assert "Toggle" in actions_to_patterns(("AXPress",), role="MenuItem", marked=True)


def test_raise_ve_cancel_pattern_uretmez():
    """Yoksa her pencere ve her iletişim kutusu 'eylenebilir' görünürdü."""
    assert actions_to_patterns(("AXRaise", "AXCancel"), role="Window") == frozenset()


def test_kaydirilabilir_alan_scroll_alir():
    assert "Scroll" in actions_to_patterns((), role="Pane", scrollable=True)


# ------------------------------------------- uçtan uca: budayıcıdan sağ çıkma

def _snapshot(nodes):
    """Düğümleri budayıcıdan geçirir ve hayatta kalan rolleri döndürür."""
    pruner = TreePruner(PruneConfig(max_nodes=50))
    snap = pruner.prune(extract(nodes))
    return [n.role for n in snap.nodes]


def test_ax_button_budamadan_sag_cikar():
    role = normalize_role("AXButton", "")
    n = node(role=role, name="Kaydet", patterns=role_patterns(role),
             rect=(0, 0, 80, 30))
    assert role in _snapshot([n])


def test_ax_scroll_area_yalnizca_scroll_pattern_ile_sag_cikar():
    """Bu testin tamamı ax_roles'un tasarım gerekçesidir.

    AXScrollArea -> Pane bir LAYOUT_ROLE'dür; budayıcı onu ancak eylem
    pattern'i varsa tutar. `scroll --node-id` kaydırma konteynerini
    adresleyebilmek zorunda olduğu için "Scroll" sentezi olmazsa düğüme göre
    kaydırma sessizce imkânsız hale gelir.
    """
    role = normalize_role("AXScrollArea", "")
    assert role in LAYOUT_ROLES

    rect = (0, 0, 400, 300)
    with_scroll = node(role=role, name="", rect=rect,
                       patterns=actions_to_patterns((), role=role, scrollable=True))
    without = node(role=role, name="", rect=rect, patterns=frozenset())

    assert role in _snapshot([with_scroll])
    assert role not in _snapshot([without])


def test_pattern_siz_grup_elenir():
    """Adsız, eylemsiz düzen konteynerleri duruma girmemeli."""
    role = normalize_role("AXGroup", "")
    n = node(role=role, name="", patterns=frozenset(),
             rect=(0, 0, 500, 400))
    assert role not in _snapshot([n])
