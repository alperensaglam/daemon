"""macOS ağaç çıkarıcısı — AXUIElement (Accessibility API).

Windows'taki UIA karşılığından **mimari olarak farklıdır** ve fark performansta
görünür: UIA tüm alt ağaç için tek bir ``BuildUpdatedCache`` çağrısı yapar, AX
ise **düğüm başına, öznitelik başına** bir süreçler arası tur atar. Bunun
karşılığı yoktur; bu bir eksik optimizasyon değil, API biçimi farkıdır.
Azaltma sırası:

1. ``AXUIElementCopyMultipleAttributeValues`` — 13 özniteliği tek turda okur.
2. ``AXUIElementGetAttributeValueCount`` + sınırlı ``CopyAttributeValues`` —
   10 000 satırlık bir tabloda 10 000 eleman referansı üretmeden sayıyı öğrenir.
3. Konteynerlerde ``AXVisibleChildren`` / ``AXVisibleRows`` — ekran dışı
   satırlar zaten budanacak, ama önce onları getirme bedeli ödeniyordu.
4. Eylem ve ayarlanabilirlik sorguları yalnızca rol olarak anlamlı düğümlerde.

Ayrıca AX'te ``MAX_EXTRACT_MS`` gibi yumuşak bir süre bütçesi gerekir:
``BuildUpdatedCache`` ya döner ya hata verir, AX yürüyüşü ise sessizce 30
saniyelik bir donmaya dejenere olabilir.
"""

from __future__ import annotations

import os
import time
import zlib
from typing import Any

from ..core.errors import BackendUnavailable, NoActiveWindow
from ..core.types import Rect, UINode
from .ax_roles import (
    FOCUSABLE_ROLES,
    NO_ACTION_ROLES,
    actions_to_patterns,
    normalize_role,
    role_patterns,
)
from .base import ExtractResult, UITreeExtractor, WindowInfo

# Güvenlik sınırları — Windows tarafıyla aynı, artı macOS'a özgü ikisi.
MAX_RAW_NODES = 4000
MAX_DEPTH = 40
MAX_CHILDREN = 200            # konteyner başına
MAX_EXTRACT_MS = 1500.0       # yumuşak süre bütçesi

#: Tek turda okunan öznitelikler.
BATCH_ATTRS = (
    "AXRole", "AXSubrole", "AXTitle", "AXValue", "AXDescription", "AXHelp",
    "AXIdentifier", "AXEnabled", "AXFocused", "AXPosition", "AXSize",
    "AXPlaceholderValue", "AXSelected",
)

#: Ayarlanabilirlik sorgusu yapmaya değen roller (her biri fazladan bir tur).
_SETTABLE_CHECK_ROLES = frozenset({
    "Edit", "ComboBox", "Slider", "CheckBox", "RadioButton", "Spinner", "ScrollBar",
})

#: Gerçek eylem sorgusu yapılacak belirsiz roller.
_ACTION_CHECK_ROLES = frozenset({"Custom", "Group", "Pane", "Image", "Window"})

_ERR_SUCCESS = 0
_ERR_API_DISABLED = -25211
_ERR_INVALID_ELEMENT = -25202
_ERR_CANNOT_COMPLETE = -25204


def _import_frameworks():
    """pyobjc modüllerini yükler; yoksa kurulum ipucu veren hata fırlatır."""
    try:
        import ApplicationServices as AS  # noqa: PLC0415
        import Quartz  # noqa: PLC0415
        from AppKit import NSWorkspace  # noqa: PLC0415
    except ImportError as exc:                       # pragma: no cover
        raise BackendUnavailable(
            "macOS erişilebilirlik arayüzü yüklenemedi. Kurulum: "
            "pip install pyobjc-framework-ApplicationServices "
            "pyobjc-framework-Quartz pyobjc-framework-Cocoa"
        ) from exc
    return AS, Quartz, NSWorkspace


def _responsible_app() -> str:
    """İzni almak zorunda olan uygulamanın adını tahmin eder."""
    return (
        os.environ.get("TERM_PROGRAM")
        or os.environ.get("__CFBundleIdentifier", "").split(".")[-1]
        or "çalıştırdığınız terminal/IDE"
    )


def accessibility_error() -> BackendUnavailable:
    """Erişilebilirlik izni yokken verilecek **eyleme dönüştürülebilir** hata.

    Bu mesajın ayrıntısı gereksiz değil: iznin verilme biçimi macOS'ta
    sezgisel olmayan iki tuzak içerir ve ikisi de "izni verdim ama olmadı"
    şikâyetine yol açar.
    """
    return BackendUnavailable(
        "Erişilebilirlik izni yok — macOS, UI ağacını okumayı engelliyor.\n"
        "  1. Sistem Ayarları > Gizlilik ve Güvenlik > Erişilebilirlik\n"
        f"  2. Listeye '{_responsible_app()}' ekleyip işaretleyin.\n"
        "     İzin python binary'sine değil, onu ÇALIŞTIRAN uygulamaya verilir.\n"
        "  3. O uygulamayı tamamen kapatıp yeniden açın; izin çalışan sürece\n"
        "     geriye dönük uygulanmaz."
    )


def accessibility_status() -> tuple[bool, str]:
    """``(izin_var_mi, aciklama)`` — sessiz kontrol, sistem penceresi açmaz."""
    AS, _, _ = _import_frameworks()
    if AS.AXIsProcessTrusted():
        return True, "erişilebilirlik izni var"
    return False, f"izin yok ({_responsible_app()} için verilmeli)"


class MacAXExtractor(UITreeExtractor):
    """AXUIElement tabanlı ağaç çıkarıcısı."""

    def __init__(self, timeout: float = 0.75, prompt: bool = False) -> None:
        """
        Args:
            timeout: AX çağrısı başına azami süre. **Varsayılanı değiştirmeyin**
                — sistem varsayılanı çağrı başına ~6 saniyedir ve tek donmuş
                uygulama 400 düğümlük bir yürüyüşü dakikalara çevirir.
            prompt: İzin yoksa sistem iletişim kutusu gösterilsin mi. Kütüphane
                yapıcısında varsayılan olarak asla gösterilmez.
        """
        self._AS, self._Quartz, self._NSWorkspace = _import_frameworks()
        self._timeout = timeout
        self._app_cache: dict[int, Any] = {}
        self._manual_ax_done: set[int] = set()

        # İzin YOKSA da nesne kurulur. Sebep pratik: list_windows() CGWindowList
        # üzerinden çalışır ve erişilebilirlik izni istemez. Yapıcıda hata
        # fırlatmak, kullanıcının sorunu teşhis etmek için kullanacağı
        # `agent.cli windows` komutunu da çalışmaz hale getirirdi.
        # Ağaç okuyan yollar (extract, active_window_handle) izni ayrıca ister.
        if prompt and not self._AS.AXIsProcessTrusted():
            self._AS.AXIsProcessTrustedWithOptions(
                {self._AS.kAXTrustedCheckOptionPrompt: True}
            )

    def _require_accessibility(self) -> None:
        """Ağaç okumadan önce izni doğrular.

        Her çağrıda kontrol edilir, yalnızca kurulumda değil: izin oturum
        ortasında geri alınabilir ve o durumda AX çağrıları hata vermek yerine
        BOŞ ağaç döndürür — sessiz ve yanlış bir sonuç.
        """
        if not self._AS.AXIsProcessTrusted():
            raise accessibility_error()

    # ------------------------------------------------------------------ #
    #  Düşük seviye AX yardımcıları
    # ------------------------------------------------------------------ #

    def _attr(self, element: Any, name: str) -> Any:
        """Tek öznitelik okur; hata durumunda ``None``."""
        err, value = self._AS.AXUIElementCopyAttributeValue(element, name, None)
        if err == _ERR_API_DISABLED:
            raise accessibility_error()
        return value if err == _ERR_SUCCESS else None

    def _batch(self, element: Any) -> dict[str, Any]:
        """BATCH_ATTRS'i tek turda okur.

        ``options=0`` bilinçli: ``kAXCopyMultipleAttributeOptionStopOnError``
        verilseydi desteklenmeyen tek bir öznitelik tüm partiyi geçersiz kılardı.
        Eksik girdiler ``kAXValueAXErrorType`` olarak döner ve ``None``a çevrilir.
        """
        err, values = self._AS.AXUIElementCopyMultipleAttributeValues(
            element, BATCH_ATTRS, 0, None
        )
        if err == _ERR_API_DISABLED:
            raise accessibility_error()
        if err != _ERR_SUCCESS or values is None:
            # Toplu okuma desteklenmiyorsa tek tek oku (yaklaşık 13x pahalı).
            return {name: self._attr(element, name) for name in BATCH_ATTRS}

        out: dict[str, Any] = {}
        for name, value in zip(BATCH_ATTRS, values):
            out[name] = None if self._is_ax_error(value) else value
        return out

    def _is_ax_error(self, value: Any) -> bool:
        """Toplu okumada eksik özniteliğin yerine gelen hata sarmalayıcısı mı?"""
        if value is None:
            return True
        try:
            # kAXValueAXErrorType = 5
            return (self._AS.AXValueGetType(value) == 5)
        except Exception:
            return False

    def _point(self, value: Any) -> tuple[float, float] | None:
        if value is None:
            return None
        ok, point = self._AS.AXValueGetValue(
            value, self._AS.kAXValueCGPointType, None
        )
        return (point.x, point.y) if ok else None

    def _size(self, value: Any) -> tuple[float, float] | None:
        if value is None:
            return None
        ok, size = self._AS.AXValueGetValue(value, self._AS.kAXValueCGSizeType, None)
        return (size.width, size.height) if ok else None

    def _children(self, element: Any) -> list[Any]:
        """Çocukları sınırlı sayıda getirir.

        Önce sayıyı sormak, 10 000 satırlık bir tabloda 10 000 eleman referansı
        materyalize etmemeyi sağlar.
        """
        err, count = self._AS.AXUIElementGetAttributeValueCount(
            element, "AXChildren", None
        )
        if err != _ERR_SUCCESS or not count:
            return []
        err, children = self._AS.AXUIElementCopyAttributeValues(
            element, "AXChildren", 0, min(count, MAX_CHILDREN), None
        )
        if err != _ERR_SUCCESS or children is None:
            return []
        return list(children)

    def _visible_children(self, element: Any, role: str) -> list[Any] | None:
        """Kaydırılabilir konteynerlerde yalnızca görünür çocuklar.

        En büyük kazanç burada: belge ve tablo ağırlıklı uygulamalarda ekran
        dışı satırlar zaten budanıyordu, ama önce onları getirmenin bedeli
        ödeniyordu.
        """
        if role not in ("Pane", "Table", "Tree", "List", "DataGrid"):
            return None
        for attr in ("AXVisibleRows", "AXVisibleChildren"):
            err, values = self._AS.AXUIElementCopyAttributeValue(element, attr, None)
            if err == _ERR_SUCCESS and values:
                return list(values)[:MAX_CHILDREN]
        return None

    # ------------------------------------------------------------------ #
    #  Uygulama ve pencere çözümleme
    # ------------------------------------------------------------------ #

    def _app_element(self, pid: int) -> Any:
        app = self._app_cache.get(pid)
        if app is None:
            app = self._AS.AXUIElementCreateApplication(pid)
            self._AS.AXUIElementSetMessagingTimeout(app, self._timeout)
            self._app_cache[pid] = app

        # Chromium tabanlı uygulamalar (Chrome, VS Code, Slack, Electron) web
        # içerik ağacını bir AX istemcisi istemeden ÜRETMEZ. Bu bayrak
        # olmadan neredeyse boş bir ağaç döner ve suç çıkarıcıya atılır.
        if pid not in self._manual_ax_done:
            try:
                self._AS.AXUIElementSetAttributeValue(app, "AXManualAccessibility", True)
            except Exception:
                pass
            self._manual_ax_done.add(pid)
        return app

    def _frontmost_pid(self) -> int:
        app = self._NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            raise NoActiveWindow("On planda bir uygulama yok.")
        return int(app.processIdentifier())

    def _focused_window(self, app: Any) -> Any:
        for attr in ("AXFocusedWindow", "AXMainWindow"):
            window = self._attr(app, attr)
            if window is not None:
                return window
        windows = self._attr(app, "AXWindows")
        if windows:
            return windows[0]
        raise NoActiveWindow(
            "On plandaki uygulamanin erisilebilir bir penceresi yok "
            "(uygulama gizli veya simge durumunda olabilir)."
        )

    def _window_id(self, window: Any, pid: int, rect: Rect) -> int:
        """AX penceresini CGWindowID'ye çevirir.

        AX'te bunun genel bir erişimcisi yoktur. İki yol:

        1. ``_AXUIElementGetWindow`` — HIServices'te belgelenmemiş ama her
           macOS pencere yöneticisinin (yabai, Hammerspoon) kullandığı yol.
        2. Desteklenen geri düşüş: ``CGWindowListCopyWindowInfo`` içinde aynı
           pid'e ait ve sınırları eşleşen pencereyi bulmak.

        Başarısızlıkta 0 döner; çağıran bunu **uyarıya çevirmek zorundadır**,
        çünkü ``cli._merge_vision`` sıfır tutamaçta vision geri düşüşünü
        sessizce atlar.
        """
        try:
            import ctypes  # noqa: PLC0415
            import objc  # noqa: PLC0415

            lib = ctypes.CDLL(
                "/System/Library/Frameworks/ApplicationServices.framework/"
                "ApplicationServices"
            )
            lib._AXUIElementGetWindow.argtypes = [
                ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)
            ]
            lib._AXUIElementGetWindow.restype = ctypes.c_int
            out = ctypes.c_uint32(0)
            if lib._AXUIElementGetWindow(objc.pyobjc_id(window), ctypes.byref(out)) == 0:
                if out.value:
                    return int(out.value)
        except Exception:
            pass

        # Geri düşüş: pid + sınır eşleştirme.
        Q = self._Quartz
        info = Q.CGWindowListCopyWindowInfo(
            Q.kCGWindowListOptionOnScreenOnly | Q.kCGWindowListExcludeDesktopElements,
            Q.kCGNullWindowID,
        ) or []
        for entry in info:
            if int(entry.get("kCGWindowOwnerPID", -1)) != pid:
                continue
            bounds = entry.get("kCGWindowBounds") or {}
            if (abs(bounds.get("X", -1e9) - rect.left) <= 2
                    and abs(bounds.get("Y", -1e9) - rect.top) <= 2
                    and abs(bounds.get("Width", -1) - rect.width) <= 2
                    and abs(bounds.get("Height", -1) - rect.height) <= 2):
                return int(entry.get("kCGWindowNumber", 0))
        return 0

    def _window_for_handle(self, handle: int) -> tuple[Any, int]:
        """CGWindowID -> (AX pencere elemani, pid)."""
        Q = self._Quartz
        info = Q.CGWindowListCopyWindowInfo(
            Q.kCGWindowListOptionIncludingWindow, handle
        ) or []
        if not info:
            raise NoActiveWindow(f"CGWindowID {handle} bulunamadi.")
        entry = info[0]
        pid = int(entry.get("kCGWindowOwnerPID", 0))
        bounds = entry.get("kCGWindowBounds") or {}

        app = self._app_element(pid)
        for window in (self._attr(app, "AXWindows") or []):
            values = self._batch(window)
            pos = self._point(values.get("AXPosition"))
            size = self._size(values.get("AXSize"))
            if pos and size and (abs(pos[0] - bounds.get("X", -1e9)) <= 2
                                 and abs(pos[1] - bounds.get("Y", -1e9)) <= 2):
                return window, pid
        raise NoActiveWindow(
            f"CGWindowID {handle} icin erisilebilir bir AX penceresi bulunamadi."
        )

    # ------------------------------------------------------------------ #
    #  Kimlik
    # ------------------------------------------------------------------ #

    @staticmethod
    def _runtime_id(pid: int, path: tuple[int, ...], role: str, subrole: str,
                    identifier: str, title: str) -> tuple[int, ...]:
        """AX'te ``RuntimeId`` karşılığı olmadığı için sentezlenen kimlik.

        ``hash()`` **kullanılmaz**: PYTHONHASHSEED yüzünden süreçler arasında
        değişir ve kimlik yeniden başlatmalarda tutmaz. crc32 kararlıdır.

        Bu kimlik Windows'un ``RuntimeId``'sinden **zayıftır**: aynı ağaç
        yolunda görsel olarak özdeş bir elemanla değiştirilen bir düğüm ayırt
        edilemez (``NSTableView`` hücre geri dönüşümü kaydırırken tam olarak
        bunu yapar). Bu bir sınır, README'de belirtilmiştir.
        """
        def crc(text: str) -> int:
            return zlib.crc32(text.encode("utf-8")) & 0xFFFFFFFF

        return (
            pid,
            crc("/".join(str(i) for i in path)),
            crc(f"{role}|{subrole}|{identifier}"),
            crc(title),
        )

    # ------------------------------------------------------------------ #
    #  Düğüm kurma
    # ------------------------------------------------------------------ #

    def _to_node(self, element: Any, values: dict[str, Any], pid: int,
                 path: tuple[int, ...], depth: int,
                 window_rect: Rect) -> UINode | None:
        ax_role = str(values.get("AXRole") or "")
        ax_subrole = str(values.get("AXSubrole") or "")
        role = normalize_role(ax_role, ax_subrole)

        pos = self._point(values.get("AXPosition"))
        size = self._size(values.get("AXSize"))
        rect = (Rect.from_origin_size(pos[0], pos[1], size[0], size[1])
                if pos and size else Rect(0, 0, 0, 0))

        # İsim zinciri: UIA'nın Name'i etiket ilişkilendirmesini zaten toplar,
        # AX toplamaz. Bu zincir olmadan macOS metin alanları isimsiz gelir ve
        # LLM "E-posta" ile "Parola"yı ayırt edemez.
        name = (str(values.get("AXTitle") or "")
                or str(values.get("AXDescription") or "")
                or str(values.get("AXHelp") or "")
                or str(values.get("AXPlaceholderValue") or ""))
        if not name and role in ("Edit", "ComboBox", "Slider"):
            label = self._attr(element, "AXTitleUIElement")
            if label is not None:
                name = str(self._attr(label, "AXTitle") or "")

        # Parola alanının değeri ASLA okunmaz.
        if ax_subrole == "AXSecureTextField":
            value = ""
        else:
            raw = values.get("AXValue")
            value = "" if raw is None else str(raw)

        # Ayrılmış menü çizgileri: adsız, eylemsiz AXMenuItem'lar. MenuItem
        # INTERACTIVE_ROLES'da olduğu için budayıcı adsız olanları da tutar;
        # elenmezse her macOS menüsünün yarısı ayraçtan oluşur.
        if role == "MenuItem" and not name and not value:
            return None

        settable = False
        if role in _SETTABLE_CHECK_ROLES:
            err, is_settable = self._AS.AXUIElementIsAttributeSettable(
                element, "AXValue", None
            )
            settable = bool(err == _ERR_SUCCESS and is_settable)

        actions: tuple[str, ...] = ()
        force_actions = os.environ.get("AGENT_AX_ACTIONS") == "1"
        if role not in NO_ACTION_ROLES and (force_actions or role in _ACTION_CHECK_ROLES):
            err, names = self._AS.AXUIElementCopyActionNames(element, None)
            if err == _ERR_SUCCESS and names:
                actions = tuple(str(n) for n in names)

        scrollable = False
        if role == "Pane" and ax_role == "AXScrollArea":
            for bar in ("AXVerticalScrollBar", "AXHorizontalScrollBar"):
                if self._attr(element, bar) is not None:
                    scrollable = True
                    break

        patterns = actions_to_patterns(
            actions, role=role, value_settable=settable, scrollable=scrollable,
        )
        if not actions and not patterns:
            # Gerçek eylem sorgusu yapılmadıysa rol tablosuna düş (bedava).
            patterns = role_patterns(role)

        enabled = values.get("AXEnabled")
        focused = bool(values.get("AXFocused") or False)

        # AX'te 'offscreen' yoktur; pencere dikdörtgeniyle kesişimden hesaplanır.
        offscreen = bool(
            not rect.is_empty and not window_rect.is_empty
            and (rect.right <= window_rect.left or rect.left >= window_rect.right
                 or rect.bottom <= window_rect.top or rect.top >= window_rect.bottom)
        )

        return UINode(
            role=role,
            name=name[:200],
            value=value[:200],
            automation_id=str(values.get("AXIdentifier") or ""),
            class_name=ax_subrole,
            rect=rect,
            enabled=True if enabled is None else bool(enabled),
            offscreen=offscreen,
            focusable=(role in FOCUSABLE_ROLES) or settable,
            focused=focused,
            patterns=patterns,
            depth=depth,
            runtime_id=self._runtime_id(
                pid, path, ax_role, ax_subrole,
                str(values.get("AXIdentifier") or ""), name,
            ),
            element=element,
        )

    # ------------------------------------------------------------------ #
    #  Genel arayüz
    # ------------------------------------------------------------------ #

    def extract(self, hwnd: int | None = None) -> ExtractResult:
        self._require_accessibility()
        started = time.perf_counter()
        warnings: list[str] = []

        if hwnd:
            window, pid = self._window_for_handle(int(hwnd))
        else:
            pid = self._frontmost_pid()
            window = self._focused_window(self._app_element(pid))

        root_values = self._batch(window)
        pos = self._point(root_values.get("AXPosition"))
        size = self._size(root_values.get("AXSize"))
        window_rect = (Rect.from_origin_size(pos[0], pos[1], size[0], size[1])
                       if pos and size else Rect(0, 0, 0, 0))

        title = str(root_values.get("AXTitle") or "")
        minimized = bool(self._attr(window, "AXMinimized") or False)

        handle = int(hwnd) if hwnd else self._window_id(window, pid, window_rect)
        if not handle:
            warnings.append(
                "CGWindowID cozulemedi; vision geri dusumu bu pencerede kullanilamaz."
            )

        nodes: list[UINode] = []
        truncated = self._walk(
            window, pid, (), 0, window_rect, nodes, started,
        )
        if truncated:
            warnings.append(
                f"Agac sinirda kesildi (>{MAX_RAW_NODES} dugum veya "
                f">{MAX_EXTRACT_MS:.0f} ms); durum eksik olabilir."
            )

        if not nodes:
            warnings.append(self._diagnose(window, pid, minimized))

        return ExtractResult(
            nodes=nodes,
            window_title=title,
            window_handle=handle,
            process_name=self._process_name(pid),
            extract_ms=(time.perf_counter() - started) * 1000.0,
            truncated=truncated,
            window_rect=window_rect,
            is_minimized=minimized,
            warning=" | ".join(w for w in warnings if w),
        )

    def _walk(self, element: Any, pid: int, path: tuple[int, ...], depth: int,
              window_rect: Rect, out: list[UINode], started: float) -> bool:
        """Ağacı gezer. Sınır aşıldıysa ``True`` döner."""
        if depth > MAX_DEPTH or len(out) >= MAX_RAW_NODES:
            return True
        if (time.perf_counter() - started) * 1000.0 > MAX_EXTRACT_MS:
            return True

        values = self._batch(element)
        node = self._to_node(element, values, pid, path, depth, window_rect)
        if node is not None:
            out.append(node)

        role = normalize_role(
            str(values.get("AXRole") or ""), str(values.get("AXSubrole") or "")
        )
        children = self._visible_children(element, role)
        if children is None:
            children = self._children(element)

        truncated = False
        for index, child in enumerate(children):
            if self._walk(child, pid, (*path, index), depth + 1,
                          window_rect, out, started):
                truncated = True
                break
        return truncated

    def _process_name(self, pid: int) -> str:
        for app in self._NSWorkspace.sharedWorkspace().runningApplications():
            if int(app.processIdentifier()) == pid:
                return str(app.localizedName() or "")
        return ""

    def _diagnose(self, window: Any, pid: int, minimized: bool) -> str:
        """Ağaç boşsa nedenini açıklar.

        Boş bir anlık görüntüyü sessizce döndürmek LLM'in "sayfa boş" diye
        yanlış sonuca varmasına yol açar; Windows tarafındaki ``_diagnose``
        ile aynı gerekçe.
        """
        if minimized:
            return "Pencere simge durumunda; once geri yukleyin."
        app = self._app_element(pid)
        if self._attr(app, "AXHidden"):
            return "Uygulama gizli (Cmd+H); once gosterin."
        children = self._children(window)
        if len(children) == 1:
            role = str(self._attr(children[0], "AXRole") or "")
            if role in ("AXUnknown", "AXImage"):
                return ("Pencere tek bir cizim yuzeyinden olusuyor (oyun/canvas); "
                        "--vision ile OCR geri dusumunu deneyin.")
        if not children:
            return ("Pencere erisilebilirlik agaci sunmuyor. Chromium tabanli bir "
                    "uygulamaysa AXManualAccessibility ayarlandi ama etkisiz kaldi; "
                    "--vision deneyin.")
        return ""

    def active_window_handle(self) -> int:
        self._require_accessibility()
        pid = self._frontmost_pid()
        window = self._focused_window(self._app_element(pid))
        values = self._batch(window)
        pos = self._point(values.get("AXPosition"))
        size = self._size(values.get("AXSize"))
        rect = (Rect.from_origin_size(pos[0], pos[1], size[0], size[1])
                if pos and size else Rect(0, 0, 0, 0))
        return self._window_id(window, pid, rect)

    def list_windows(self) -> list[WindowInfo]:
        """Görünür üst düzey pencereler, ön plandan arkaya doğru.

        ``kCGWindowLayer == 0`` filtresi normal pencereleri bırakır; menü
        çubuğu öğeleri (layer 25) ve loginwindow katmanları (2001+) elenir.
        """
        Q = self._Quartz
        info = Q.CGWindowListCopyWindowInfo(
            Q.kCGWindowListOptionOnScreenOnly | Q.kCGWindowListExcludeDesktopElements,
            Q.kCGNullWindowID,
        ) or []

        front = self._NSWorkspace.sharedWorkspace().frontmostApplication()
        front_pid = int(front.processIdentifier()) if front else -1

        rows: list[WindowInfo] = []
        seen_front = False
        needs_title: list[int] = []

        for entry in info:
            if int(entry.get("kCGWindowLayer", -1)) != 0:
                continue
            pid = int(entry.get("kCGWindowOwnerPID", 0))
            bounds = entry.get("kCGWindowBounds") or {}
            # kCGWindowName Ekran Kaydi izni olmadan bostur; AX'ten doldurulur.
            title = str(entry.get("kCGWindowName") or "")
            is_active = (pid == front_pid and not seen_front)
            if is_active:
                seen_front = True
            if not title:
                needs_title.append(len(rows))
            rows.append(WindowInfo(
                handle=int(entry.get("kCGWindowNumber", 0)),
                title=title,
                process_name=str(entry.get("kCGWindowOwnerName") or ""),
                pid=pid,
                rect=Rect.from_origin_size(
                    bounds.get("X", 0), bounds.get("Y", 0),
                    bounds.get("Width", 0), bounds.get("Height", 0),
                ),
                is_active=is_active,
            ))

        # Baslik tamamlama AX ister; izin yoksa baslıksız listelemek,
        # hic listelememekten iyidir.
        if needs_title and self._AS.AXIsProcessTrusted():
            rows = self._backfill_titles(rows, needs_title)
        return rows

    def _backfill_titles(self, rows: list[WindowInfo],
                         indexes: list[int]) -> list[WindowInfo]:
        """Boş başlıkları AX'ten tamamlar — uygulama başına bir kez."""
        by_pid: dict[int, list[int]] = {}
        for index in indexes:
            by_pid.setdefault(rows[index].pid, []).append(index)

        for pid, targets in by_pid.items():
            try:
                windows = self._attr(self._app_element(pid), "AXWindows") or []
            except Exception:
                continue
            geometry = []
            for window in windows:
                values = self._batch(window)
                pos = self._point(values.get("AXPosition"))
                geometry.append((pos, str(values.get("AXTitle") or "")))
            for index in targets:
                row = rows[index]
                for pos, title in geometry:
                    if pos and title and (abs(pos[0] - row.rect.left) <= 2
                                          and abs(pos[1] - row.rect.top) <= 2):
                        rows[index] = WindowInfo(
                            handle=row.handle, title=title,
                            process_name=row.process_name, pid=row.pid,
                            rect=row.rect, is_active=row.is_active,
                            is_minimized=row.is_minimized,
                        )
                        break
        return rows

    def close(self) -> None:
        self._app_cache.clear()
        self._manual_ax_done.clear()
