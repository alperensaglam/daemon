"""Windows UIAutomation cikarici — tek sureçler-arasi round-trip.

Tasarimin tamami tek bir gozleme dayanir: UIA elemanlarindan **canli** ozellik
okumak sureçler-arasi bir COM cagrisidir. Bu makinede olculdu:

    dugum basina canli okuma : 0.178 ms
    dugum basina cached okuma: 0.003 ms   ->  55x fark

500 dugumlu bir pencerede 8 ozellik okumak canli yolda ~700 ms, cached yolda
~12 ms eder. Bu yuzden butun agac tek bir ``BuildUpdatedCache`` cagrisiyla
cekilir; sonrasindaki her okuma sureç-icidir.
"""

from __future__ import annotations

import time
from typing import Any

from ..core.dpi import ensure_dpi_aware
from ..core.errors import BackendUnavailable, NoActiveWindow
from ..core.types import Rect, UINode
from .base import ExtractResult, UITreeExtractor

# Guvenlik siniri: patolojik agaclarda (cok buyuk tablolar, sonsuz liste)
# bellegi ve sureyi sabitler.
MAX_RAW_NODES = 4000
MAX_DEPTH = 40

# Cache'e alinacak ozellikler. Her biri tek seferde gelir.
_CACHED_PROPERTIES = (
    "UIA_NamePropertyId",
    "UIA_ControlTypePropertyId",
    "UIA_AutomationIdPropertyId",
    "UIA_ClassNamePropertyId",
    "UIA_BoundingRectanglePropertyId",
    "UIA_IsEnabledPropertyId",
    "UIA_IsOffscreenPropertyId",
    "UIA_IsKeyboardFocusablePropertyId",
    "UIA_HasKeyboardFocusPropertyId",
    "UIA_ValueValuePropertyId",
    "UIA_ValueIsReadOnlyPropertyId",
    "UIA_RuntimeIdPropertyId",
    "UIA_IsInvokePatternAvailablePropertyId",
    "UIA_IsValuePatternAvailablePropertyId",
    "UIA_IsTogglePatternAvailablePropertyId",
    "UIA_IsExpandCollapsePatternAvailablePropertyId",
    "UIA_IsSelectionItemPatternAvailablePropertyId",
    "UIA_IsScrollPatternAvailablePropertyId",
    "UIA_IsTextPatternAvailablePropertyId",
    "UIA_IsScrollItemPatternAvailablePropertyId",
)

# Pattern adi -> hangi "IsXAvailable" ozelliginden okunacagi
_PATTERN_PROPS = {
    "Invoke": "UIA_IsInvokePatternAvailablePropertyId",
    "Value": "UIA_IsValuePatternAvailablePropertyId",
    "Toggle": "UIA_IsTogglePatternAvailablePropertyId",
    "ExpandCollapse": "UIA_IsExpandCollapsePatternAvailablePropertyId",
    "SelectionItem": "UIA_IsSelectionItemPatternAvailablePropertyId",
    "Scroll": "UIA_IsScrollPatternAvailablePropertyId",
    "Text": "UIA_IsTextPatternAvailablePropertyId",
    "ScrollItem": "UIA_IsScrollItemPatternAvailablePropertyId",
}


def _build_control_type_map(uia_mod: Any) -> dict[int, str]:
    """``UIA_ButtonControlTypeId`` -> ``"Button"`` haritasini modulden uretir.

    Sabitler elle yazilmaz: UIA sonraki Windows surumlerinde kontrol tipi
    ekleyebilir ve modulden okumak otomatik olarak guncel kalir.
    """
    out: dict[int, str] = {}
    for attr in dir(uia_mod):
        if attr.startswith("UIA_") and attr.endswith("ControlTypeId"):
            try:
                value = int(getattr(uia_mod, attr))
            except (TypeError, ValueError):
                continue
            out[value] = attr[len("UIA_"):-len("ControlTypeId")]
    return out


class WindowsUIAExtractor(UITreeExtractor):
    """UIAutomation tabanli cikarici.

    Ornek uzun omurludur: COM nesnesi ve CacheRequest bir kez kurulur, her
    ``extract()`` cagrisinda yeniden kullanilir.
    """

    def __init__(self) -> None:
        # DPI her seyden once: yanlis ayarlanirsa tum bounding box'lar kayar.
        self.dpi_mode = ensure_dpi_aware()

        try:
            import comtypes.client  # noqa: PLC0415 — COM icе aktarim bilincli
            self._uia_mod = comtypes.client.GetModule("UIAutomationCore.dll")
            self._iuia = comtypes.client.CreateObject(
                self._uia_mod.CUIAutomation, interface=self._uia_mod.IUIAutomation
            )
        except Exception as exc:  # pragma: no cover - ortama bagli
            raise BackendUnavailable(
                "UIAutomation baslatilamadi. comtypes kurulu mu ve "
                f"UIAutomationCore.dll erisilebilir mi? Detay: {exc}"
            ) from exc

        self._control_types = _build_control_type_map(self._uia_mod)
        self._cache_request = self._make_cache_request()

        import win32gui  # noqa: PLC0415
        self._win32gui = win32gui

    # ------------------------------------------------------------------ #
    #  Kurulum
    # ------------------------------------------------------------------ #

    def _make_cache_request(self) -> Any:
        mod = self._uia_mod
        cr = self._iuia.CreateCacheRequest()
        for prop_name in _CACHED_PROPERTIES:
            prop = getattr(mod, prop_name, None)
            if prop is not None:
                cr.AddProperty(prop)

        cr.TreeScope = mod.TreeScope_Subtree
        # ControlView, saf layout konteynerlerinin buyuk kismini UIA tarafinda
        # zaten eler — bu, Python'a hic gelmeyen dugum demektir.
        cr.TreeFilter = self._iuia.ControlViewCondition
        # Full: elemanlar canli kalir, sonradan Invoke/SetValue cagirabiliriz.
        # None modu daha hizli olurdu ama eylem yapilamazdi.
        if hasattr(mod, "AutomationElementMode_Full"):
            cr.AutomationElementMode = mod.AutomationElementMode_Full
        return cr

    # ------------------------------------------------------------------ #
    #  Genel API
    # ------------------------------------------------------------------ #

    def active_window_handle(self) -> int:
        hwnd = self._win32gui.GetForegroundWindow()
        if not hwnd:
            raise NoActiveWindow("Aktif pencere bulunamadi.")
        return int(hwnd)

    def resolve_content_window(self, hwnd: int) -> int:
        """UWP cerceve penceresini gercek icerik penceresine cevirir.

        Hesap Makinesi, Ayarlar, Takvim gibi UWP/WinUI uygulamalari
        ``ApplicationFrameHost.exe`` icindeki bir ``ApplicationFrameWindow``
        cercevesinde barinir. ``GetForegroundWindow()`` bu cerceveyi dondurur
        ama UI agaci cercevede degil, ``Windows.UI.Core.CoreWindow`` sinifli
        cocuk penceresindedir — cerceveden okunursa neredeyse bos agac gelir.

        Olculdu: Hesap Makinesi'nde cerceveden 0 kullanilabilir dugum,
        icerik penceresinden 33 dugum.
        """
        try:
            if self._win32gui.GetClassName(hwnd) != "ApplicationFrameWindow":
                return hwnd
        except Exception:
            return hwnd

        found: list[int] = []

        def _on_child(child: int, _: Any) -> bool:
            try:
                if self._win32gui.GetClassName(child) == "Windows.UI.Core.CoreWindow":
                    found.append(child)
                    return False        # aramayi durdur
            except Exception:
                pass
            return True

        try:
            self._win32gui.EnumChildWindows(hwnd, _on_child, None)
        except Exception:
            # EnumChildWindows, geri cagrim False dondurunce hata firlatabilir;
            # aradigimizi zaten bulmus oluruz.
            pass

        return found[0] if found else hwnd

    def extract(self, hwnd: int | None = None) -> ExtractResult:
        started = time.perf_counter()

        if hwnd is None:
            hwnd = self.active_window_handle()
        hwnd = self.resolve_content_window(hwnd)

        try:
            title = self._win32gui.GetWindowText(hwnd) or ""
        except Exception:
            title = ""

        try:
            root = self._iuia.ElementFromHandle(hwnd)
        except Exception as exc:
            raise NoActiveWindow(
                f"Pencere ({hwnd}) icin UIA elemani alinamadi: {exc}"
            ) from exc

        # >>> Tek sureçler-arasi cagri: butun alt agac cache'e cekilir.
        cached_root = root.BuildUpdatedCache(self._cache_request)

        nodes: list[UINode] = []
        truncated = self._walk(cached_root, depth=0, out=nodes)

        # Pencere sinirlari OS'tan: agactan tahmin etmek simge durumunda
        # (-32000,-32000'e parklanmis) pencerelerde tum cocuklari eler.
        window_rect, minimized = self._window_geometry(hwnd)

        elapsed = (time.perf_counter() - started) * 1000.0
        return ExtractResult(
            nodes=nodes,
            window_title=title or self._safe_name(cached_root),
            window_handle=hwnd,
            process_name=self._process_name(hwnd),
            extract_ms=elapsed,
            truncated=truncated,
            window_rect=window_rect,
            is_minimized=minimized,
            warning=self._diagnose(hwnd, nodes),
        )

    def _diagnose(self, hwnd: int, nodes: list[UINode]) -> str:
        """Supheli bos agaclarin nedenini aciklar.

        En sik neden: UWP/WinUI uygulamalari (Hesap Makinesi, Ayarlar, Takvim)
        arka plana dustuklerinde Windows tarafindan ASKIYA ALINIR ve UIA agaclari
        neredeyse tamamen bosalir. Olculdu — Hesap Makinesi:

            on planda    : 46 ham dugum
            askidayken   :  1 ham dugum (cerceve penceresinden 8, sadece baslik)

        Bu durum sessizce bos bir liste olarak donerse, ustteki katman "pencerede
        hicbir sey yok" diye yanlis karar verir. Bu yuzden acikca bildirilir.
        """
        if len(nodes) > 10:
            return ""
        try:
            cls = self._win32gui.GetClassName(hwnd)
            foreground = self._win32gui.GetForegroundWindow()
        except Exception:
            return ""

        if cls in ("ApplicationFrameWindow", "Windows.UI.Core.CoreWindow"):
            if foreground != hwnd:
                return (
                    "Bu bir UWP/WinUI penceresi ve on planda degil. Windows bu tur "
                    "uygulamalari arka planda askiya alir; UIA agaci bu yuzden bos "
                    "gorunuyor. Pencereyi one getirip tekrar deneyin."
                )
            return (
                "UWP penceresi on planda ama agac yine de bos. Uygulama henuz "
                "yuklenyor olabilir; kisa bir bekleyip tekrar deneyin."
            )
        return ""

    def _window_geometry(self, hwnd: int) -> tuple[Rect, bool]:
        """OS'a gore pencere dikdortgeni ve simge durumu."""
        try:
            minimized = bool(self._win32gui.IsIconic(hwnd))
        except Exception:
            minimized = False
        try:
            left, top, right, bottom = self._win32gui.GetWindowRect(hwnd)
            return Rect.from_ltrb(left, top, right, bottom), minimized
        except Exception:
            return Rect(0, 0, 0, 0), minimized

    # ------------------------------------------------------------------ #
    #  Ic yardimcilar — buradan sonrasi tamamen sureç-ici
    # ------------------------------------------------------------------ #

    def _walk(self, element: Any, depth: int, out: list[UINode]) -> bool:
        """Cache'lenmis agaci gezer. Sinir asilirsa True (truncated) doner."""
        if len(out) >= MAX_RAW_NODES or depth > MAX_DEPTH:
            return True

        node = self._to_node(element, depth)
        if node is not None:
            out.append(node)

        try:
            children = element.GetCachedChildren()
        except Exception:
            # Bazi elemanlar cache'de cocuk tasimaz; bu normaldir.
            return False
        if not children:
            return False

        truncated = False
        try:
            count = children.Length
        except Exception:
            return False

        for i in range(count):
            try:
                child = children.GetElement(i)
            except Exception:
                continue
            if self._walk(child, depth + 1, out):
                truncated = True
            if len(out) >= MAX_RAW_NODES:
                return True
        return truncated

    def _to_node(self, element: Any, depth: int) -> UINode | None:
        mod = self._uia_mod
        get = element.GetCachedPropertyValue

        try:
            control_type = int(get(mod.UIA_ControlTypePropertyId) or 0)
        except Exception:
            return None

        role = self._control_types.get(control_type, f"Unknown{control_type}")

        patterns = set()
        for pattern_name, prop_name in _PATTERN_PROPS.items():
            prop = getattr(mod, prop_name, None)
            if prop is None:
                continue
            try:
                if get(prop):
                    patterns.add(pattern_name)
            except Exception:
                pass

        # Salt-okunur Value pattern'i "yazilabilir" saymamak gerekir; aksi halde
        # pruner degistirilemeyen etiketleri duzenlenebilir alan sanar.
        if "Value" in patterns:
            try:
                if get(mod.UIA_ValueIsReadOnlyPropertyId):
                    patterns.discard("Value")
            except Exception:
                pass

        try:
            runtime_id = tuple(int(x) for x in (get(mod.UIA_RuntimeIdPropertyId) or ()))
        except Exception:
            runtime_id = ()

        return UINode(
            role=role,
            name=self._as_text(get, mod.UIA_NamePropertyId),
            value=self._as_text(get, mod.UIA_ValueValuePropertyId),
            automation_id=self._as_text(get, mod.UIA_AutomationIdPropertyId),
            class_name=self._as_text(get, mod.UIA_ClassNamePropertyId),
            rect=self._as_rect(get, mod.UIA_BoundingRectanglePropertyId),
            enabled=self._as_bool(get, mod.UIA_IsEnabledPropertyId, default=True),
            offscreen=self._as_bool(get, mod.UIA_IsOffscreenPropertyId, default=False),
            focusable=self._as_bool(get, mod.UIA_IsKeyboardFocusablePropertyId, default=False),
            focused=self._as_bool(get, mod.UIA_HasKeyboardFocusPropertyId, default=False),
            patterns=frozenset(patterns),
            depth=depth,
            runtime_id=runtime_id,
            element=element,
        )

    @staticmethod
    def _as_text(get: Any, prop: int) -> str:
        try:
            value = get(prop)
        except Exception:
            return ""
        if value is None:
            return ""
        return str(value).strip()

    @staticmethod
    def _as_bool(get: Any, prop: int, default: bool) -> bool:
        try:
            value = get(prop)
        except Exception:
            return default
        return default if value is None else bool(value)

    @staticmethod
    def _as_rect(get: Any, prop: int) -> Rect:
        try:
            return Rect.from_uia(get(prop))
        except Exception:
            return Rect(0, 0, 0, 0)

    @staticmethod
    def _safe_name(element: Any) -> str:
        try:
            return str(element.CachedName or "")
        except Exception:
            return ""

    @staticmethod
    def _process_name(hwnd: int) -> str:
        """Pencerenin sahibi surecin adi. Basarisiz olursa bos doner."""
        try:
            import win32process  # noqa: PLC0415
            import win32api  # noqa: PLC0415
            import win32con  # noqa: PLC0415

            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            handle = win32api.OpenProcess(
                win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            try:
                path = win32process.GetModuleFileNameEx(handle, 0)
            finally:
                win32api.CloseHandle(handle)
            return path.rsplit("\\", 1)[-1]
        except Exception:
            # Yukseltilmis sureçlerde bu cagri basarisiz olur — bilgi amacli
            # bir alan oldugu icin sessizce gecilir.
            return ""
