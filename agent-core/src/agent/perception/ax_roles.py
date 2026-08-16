"""AX rolleri -> budayıcının sözlüğü, ve AX eylemleri -> UIA pattern'leri.

``TreePruner`` platformdan bağımsızdır ama **sözlüğü UIA'dır**: ``Button``,
``Edit``, ``Pane``, ``Group``… ve karar verirken ``node.patterns &
ACTIONABLE_PATTERNS`` kümesine bakar. macOS çıkarıcısı ham AX rollerini
(``AXButton``, ``AXStaticText``…) olduğu gibi verirse ``_is_useful`` hiçbirini
tanımaz ve **her şeyi sessizce düşürür** — belirti, "sayfa boş" diyen bir LLM
olur, hata mesajı değil.

Bu modül **hiçbir şey import etmez** (pyobjc dahil), böylece eşleme tabloları
Mac olmayan bir makinede de test edilebilir. ``tests/test_ax_roles.py`` her
değerin budayıcının sözlüğünde bulunduğunu mekanik olarak doğrular.
"""

from __future__ import annotations

#: Eşlenmemiş roller buraya düşer. ``Custom`` bilinçli bir seçimdir: budayıcıda
#: LAYOUT_ROLES içindedir, yani gerçek bir eylem pattern'i taşımıyorsa elenir.
#: Alternatif (metin kuralına düşürmek) SwiftUI ve web içeriğinden gelen adsız
#: yığınla durumu doldururdu.
UNKNOWN_ROLE = "Custom"


# --------------------------------------------------------------------------- #
#  Subrole -> rol  (role'den ÖNCE bakılır)
# --------------------------------------------------------------------------- #

AX_SUBROLE_MAP: dict[str, str] = {
    "AXSearchField": "Edit",
    # Parola alanı. Çıkarıcı bu rolde `value`yu ASLA okumaz, boş yazar.
    "AXSecureTextField": "Edit",
    "AXTextAttachment": "Image",

    # Pencere düğmeleri ve araç çubuğu düğmeleri AXButton rolüyle gelir ama
    # subrole daha bilgilendiricidir; hepsi Button'a düşer.
    "AXCloseButton": "Button",
    "AXMinimizeButton": "Button",
    "AXZoomButton": "Button",
    "AXFullScreenButton": "Button",
    "AXToolbarButton": "Button",
    "AXSortButton": "Button",
    "AXIncrementArrow": "Button",
    "AXDecrementArrow": "Button",
    "AXApplicationDockItem": "Button",
    "AXTrashDockItem": "Button",
    "AXFolderDockItem": "Button",
    "AXDocumentDockItem": "Button",

    # KRİTİK: Cocoa sekmeleri AXRadioButton ROLÜYLE gelir. Subrole'e bakılmazsa
    # her sekme "radyo düğmesi" olarak listelenir ve LLM sekme değiştiremez.
    "AXTabButton": "TabItem",

    "AXOutlineRow": "TreeItem",
    "AXTableRow": "DataItem",
    "AXSwitch": "CheckBox",
    "AXToggle": "CheckBox",
    "AXRatingIndicator": "Slider",
    "AXTimeline": "Slider",

    "AXContentList": "List",
    "AXDefinitionList": "List",
    "AXDescriptionList": "List",
    "AXSectionList": "List",
    "AXCollectionList": "List",

    "AXStandardWindow": "Window",
    "AXDialog": "Window",
    "AXSystemDialog": "Window",
    "AXFloatingWindow": "Window",
    "AXSystemFloatingWindow": "Window",

    "AXTextLink": "Hyperlink",
    "AXUnknown": UNKNOWN_ROLE,
}


# --------------------------------------------------------------------------- #
#  Rol -> budayıcı sözlüğü
# --------------------------------------------------------------------------- #

AX_ROLE_MAP: dict[str, str] = {
    # --- etkileşimli ---
    "AXButton": "Button",
    "AXColorWell": "Button",
    "AXDisclosureTriangle": "Button",
    "AXPopUpButton": "ComboBox",
    "AXComboBox": "ComboBox",
    "AXMenuButton": "SplitButton",
    "AXTextField": "Edit",
    "AXTextArea": "Edit",
    "AXSearchField": "Edit",
    "AXDateField": "Edit",
    "AXTimeField": "Edit",
    "AXCheckBox": "CheckBox",
    "AXRadioButton": "RadioButton",
    "AXSlider": "Slider",
    "AXIncrementor": "Spinner",
    "AXStepper": "Spinner",
    "AXMenu": "Menu",
    "AXMenuItem": "MenuItem",
    "AXMenuBar": "MenuBar",
    "AXMenuBarItem": "MenuItem",
    "AXList": "List",
    "AXTable": "Table",
    "AXOutline": "Tree",
    "AXBrowser": "Tree",
    "AXGrid": "DataGrid",
    "AXRow": "DataItem",
    "AXCell": "DataItem",
    "AXTabGroup": "Tab",
    "AXLink": "Hyperlink",
    "AXScrollBar": "ScrollBar",
    "AXValueIndicator": "Thumb",
    "AXHandle": "Thumb",

    # --- metin ---
    "AXStaticText": "Text",
    "AXHeading": "Text",
    "AXWebArea": "Document",
    "AXImage": "Image",
    "AXProgressIndicator": "ProgressBar",
    "AXLevelIndicator": "ProgressBar",
    "AXBusyIndicator": "ProgressBar",
    "AXRelevanceIndicator": "ProgressBar",

    # --- düzen ---
    "AXWindow": "Window",
    "AXSheet": "Window",
    "AXApplication": "Pane",
    "AXSystemWide": "Pane",
    "AXDrawer": "Pane",
    "AXPopover": "Pane",
    # AXScrollArea -> Pane bu tasarımın tutarlılık sınavıdır: Pane bir
    # LAYOUT_ROLE'dür ve budayıcı onu yalnızca eylem pattern'i varsa tutar.
    # `scroll --node-id` için kaydırma konteynerinin hayatta kalması şart,
    # dolayısıyla aşağıdaki "Scroll" sentezi olmazsa düğüme göre kaydırma
    # imkânsız hale gelir.
    "AXScrollArea": "Pane",
    "AXSplitGroup": "Pane",
    "AXLayoutArea": "Pane",
    "AXMatte": "Pane",
    "AXGroup": "Group",
    "AXRadioGroup": "Group",
    "AXColumn": "Group",
    "AXSplitter": "Separator",
    "AXToolbar": "ToolBar",
    "AXHelpTag": "ToolTip",
    "AXLayoutItem": UNKNOWN_ROLE,
    "AXRuler": UNKNOWN_ROLE,
    "AXRulerMarker": UNKNOWN_ROLE,
    "AXGrowArea": UNKNOWN_ROLE,
    "AXUnknown": UNKNOWN_ROLE,
}


def normalize_role(ax_role: str, ax_subrole: str = "") -> str:
    """AX rol/alt-rol çiftini budayıcının sözlüğündeki bir role çevirir.

    Subrole role'den **önce** değerlendirilir: macOS'ta asıl ayırt edici bilgi
    çoğu zaman subrole'dedir (``AXRadioButton`` + ``AXTabButton`` = sekme).
    """
    if ax_subrole:
        mapped = AX_SUBROLE_MAP.get(ax_subrole)
        if mapped:
            return mapped
    return AX_ROLE_MAP.get(ax_role, UNKNOWN_ROLE)


# --------------------------------------------------------------------------- #
#  AX eylemleri -> UIA pattern'leri
# --------------------------------------------------------------------------- #

AX_ACTION_PATTERNS: dict[str, str] = {
    "AXPress": "Invoke",
    "AXConfirm": "Invoke",
    "AXPick": "SelectionItem",
    "AXShowMenu": "ExpandCollapse",
    "AXIncrement": "Value",
    "AXDecrement": "Value",
    "AXScrollToVisible": "ScrollItem",   # UIA ScrollItem paritesi; ACTIONABLE degil
    # AXRaise ve AXCancel bilinçli olarak eşlenmez: her pencereyi ve her
    # iletişim kutusunu "eylenebilir" gösterirlerdi.
}

#: Gerçek eylem sorgusu yapılmadığında kullanılan varsayılan tablo.
#:
#: ``AXUIElementCopyActionNames`` düğüm başına fazladan bir IPC turudur ve AX
#: zaten tur başına pahalıdır (bkz. macos_ax modül docstring'i). Bu tablo
#: bedava bir tahmindir; yanlış bir satır düğümleri LLM'den gizler, bu yüzden
#: ``AGENT_AX_ACTIONS=1`` ile gerçek sorgu zorlanabilir.
ROLE_DEFAULT_PATTERNS: dict[str, frozenset[str]] = {
    "Button": frozenset({"Invoke"}),
    "SplitButton": frozenset({"Invoke", "ExpandCollapse"}),
    "Hyperlink": frozenset({"Invoke"}),
    "MenuItem": frozenset({"Invoke"}),
    "CheckBox": frozenset({"Toggle"}),
    "RadioButton": frozenset({"SelectionItem"}),
    "TabItem": frozenset({"SelectionItem"}),
    "ListItem": frozenset({"SelectionItem"}),
    "TreeItem": frozenset({"SelectionItem", "ExpandCollapse"}),
    "DataItem": frozenset({"SelectionItem"}),
    "ComboBox": frozenset({"ExpandCollapse", "Value"}),
    "Edit": frozenset({"Value"}),
    "Slider": frozenset({"Value"}),
    "Spinner": frozenset({"Value"}),
    "ScrollBar": frozenset({"Value"}),
}

#: Klavye odağı alabilen roller. ``_is_useful`` kural 2 odaklanabilir her düğümü
#: tuttuğu için bu küme dar olmalı: hepsine ``True`` demek budamayı tamamen
#: etkisiz kılar, hepsine ``False`` demek eylemi olmayan metin alanlarını düşürür.
FOCUSABLE_ROLES: frozenset[str] = frozenset({
    "Edit", "ComboBox", "CheckBox", "RadioButton", "Button", "Slider",
    "Spinner", "ListItem", "TreeItem", "TabItem", "DataItem", "Hyperlink",
})

#: Gerçek eylem sorgusu yapmaya değmeyen roller (asla eylemleri olmaz).
NO_ACTION_ROLES: frozenset[str] = frozenset({"Text", "Separator", "ToolTip"})


def role_patterns(role: str) -> frozenset[str]:
    """Rolden türetilen varsayılan pattern kümesi (IPC turu harcamadan)."""
    return ROLE_DEFAULT_PATTERNS.get(role, frozenset())


def actions_to_patterns(
    actions: "tuple[str, ...] | list[str]",
    *,
    role: str,
    value_settable: bool = False,
    scrollable: bool = False,
    marked: bool = False,
) -> frozenset[str]:
    """AX eylem adlarını UIA pattern adlarına çevirir.

    Args:
        actions: ``AXUIElementCopyActionNames`` sonucu (boş olabilir).
        role: Normalize edilmiş rol — tabloya dayalı çıkarımlar için.
        value_settable: ``AXUIElementIsAttributeSettable(el, "AXValue")``.
            Windows çıkarıcısı bunun aynasını yapar: ``ValueIsReadOnly`` ise
            ``Value`` pattern'ini *atar*.
        scrollable: Elemanın settable ``AXValue``'lu bir kaydırma çubuğu var mı.
        marked: Menü öğesinde ``kAXMenuItemMarkChar`` dolu mu (işaretli menü).

    Returns:
        Budayıcının ``ACTIONABLE_PATTERNS`` kümesiyle karşılaştırılabilir küme.
    """
    patterns = set()

    for action in actions or ():
        mapped = AX_ACTION_PATTERNS.get(action)
        if mapped:
            patterns.add(mapped)

    if value_settable:
        patterns.add("Value")
        # Onay kutusu/radyo düğmesinde ayarlanabilir değer = aç/kapa.
        if role in ("CheckBox", "RadioButton"):
            patterns.add("Toggle")

    if marked:
        patterns.add("Toggle")

    if scrollable:
        patterns.add("Scroll")

    return frozenset(patterns)
