"""Windows eylem yurutucusu — native UIA pattern'i once, piksel en son.

Tasarim ilkesi: bir butona basmanin dogru yolu ``InvokePattern.Invoke()``
cagirmaktir, bounding box merkezine fare tiklamak degil. Native yol
deterministiktir; pencere onde olmasa, imlec baska yerde olsa, eleman kismen
ortulu olsa bile calisir. Piksel tiklamasi bunlarin hicbirini garanti etmez.

``ActionResult.method`` alani hangi yolun kullanildigini kaydeder; ``pixel_fallback``
degeri mimarinin o eylemde ise yaramadigini gosteren olculebilir sinyaldir.
"""

from __future__ import annotations

import time
from typing import Any

from ..core.errors import NodeNotFound, StaleNodeError
from ..core.types import ActionResult, Snapshot, UINode
from . import keys_win as K
from .common import BaseExecutor
from .keynames import intent_combo

# UIA pattern id'leri (UIAutomationClient sabitleriyle ayni).
_PATTERN_IDS = {
    "Invoke": 10000,
    "Selection": 10001,
    "Value": 10002,
    "Scroll": 10004,
    "ExpandCollapse": 10005,
    "SelectionItem": 10010,
    "Toggle": 10015,
    "ScrollItem": 10017,
    "LegacyIAccessible": 10018,
}


class WindowsUIAExecutor(BaseExecutor):
    """UIA pattern tabanli eylem yurutucusu."""

    def __init__(self, extractor: Any = None, verify: bool = True,
                 pruner: Any = None) -> None:
        """
        Args:
            extractor: Eylem sonrasi dogrulama icin kullanilacak cikarici.
                ``None`` ise ``ui_changed`` hesaplanmaz.
            verify: Eylem sonrasi yeniden snapshot alinip degisim kontrol edilsin mi.
            pruner: Dogrulamada kullanilacak budayici (bkz. BaseExecutor).
        """
        super().__init__(extractor=extractor, verify=verify, pruner=pruner)

        import comtypes.client  # noqa: PLC0415
        self._mod = comtypes.client.GetModule("UIAutomationCore.dll")

    # ------------------------------------------------------------------ #
    #  Dugum cozumleme — bayat id koruma
    # ------------------------------------------------------------------ #

    def _check_identity(self, node: UINode) -> None:
        """RuntimeId hala ayni mi? Eleman yok olduysa COM hatasi verir."""
        try:
            current = tuple(int(x) for x in (node.element.GetRuntimeId() or ()))
        except Exception as exc:
            raise StaleNodeError(
                f"[@{node.node_id}] ({node.role} '{node.name}') artik mevcut degil — "
                f"UI degismis olmali. Yeni bir anlik goruntu alin. Detay: {exc}"
            ) from exc

        if node.runtime_id and current and current != node.runtime_id:
            raise StaleNodeError(
                f"[@{node.node_id}] baska bir elemana isaret ediyor "
                f"(beklenen {node.runtime_id}, bulunan {current}). "
                "UI degismis; yeni bir anlik goruntu alin."
            )

    def _pattern(self, node: UINode, name: str) -> Any | None:
        pid = _PATTERN_IDS.get(name)
        if pid is None:
            return None
        try:
            pattern = node.element.GetCurrentPattern(pid)
        except Exception:
            return None
        if not pattern:
            return None
        iface = getattr(self._mod, f"IUIAutomation{name}Pattern", None)
        if iface is None:
            return pattern
        try:
            return pattern.QueryInterface(iface)
        except Exception:
            return None

    # ------------------------------------------------------------------ #
    #  Eylemler
    # ------------------------------------------------------------------ #

    def click(self, snapshot: Snapshot, node_id: int) -> ActionResult:
        started = time.perf_counter()
        before = self._fingerprint(snapshot)
        try:
            node = self._resolve(snapshot, node_id)
        except (NodeNotFound, StaleNodeError) as exc:
            return ActionResult.failure("click", str(exc), self._ms(started))

        # 1. Invoke — butonlar, menu ogeleri, baglantilar
        pattern = self._pattern(node, "Invoke")
        if pattern is not None:
            try:
                pattern.Invoke()
                return self._done("click", "invoke", node, started, before)
            except Exception as exc:
                last_error = f"Invoke: {exc}"
        else:
            last_error = ""

        # 2. SelectionItem — liste/sekme ogeleri
        pattern = self._pattern(node, "SelectionItem")
        if pattern is not None:
            try:
                pattern.Select()
                return self._done("click", "select", node, started, before)
            except Exception as exc:
                last_error = f"Select: {exc}"

        # 3. Toggle — onay kutulari, acma/kapama
        pattern = self._pattern(node, "Toggle")
        if pattern is not None:
            try:
                pattern.Toggle()
                return self._done("click", "toggle", node, started, before)
            except Exception as exc:
                last_error = f"Toggle: {exc}"

        # 4. ExpandCollapse — agac dugumleri, acilir menuler
        pattern = self._pattern(node, "ExpandCollapse")
        if pattern is not None:
            try:
                pattern.Expand()
                return self._done("click", "expand", node, started, before)
            except Exception as exc:
                last_error = f"Expand: {exc}"

        # 5. Son care: bounding box merkezine fiziksel tiklama.
        if node.rect.is_empty:
            return ActionResult.failure(
                "click",
                f"{node.describe()} icin ne native pattern ne de gecerli konum var. "
                f"{last_error}",
                self._ms(started),
            )
        try:
            self._bring_to_front(snapshot)
            x, y = node.rect.center
            K.click_at(x, y)
            result = self._done("click", "pixel_fallback", node, started, before)
            result.detail = f"({x},{y}) — native pattern yoktu"
            return result
        except Exception as exc:
            return ActionResult.failure("click", f"Piksel tiklamasi: {exc}",
                                        self._ms(started))

    def type_text(self, snapshot: Snapshot, node_id: int, text: str,
                  clear_first: bool = True) -> ActionResult:
        started = time.perf_counter()
        before = self._fingerprint(snapshot)
        try:
            node = self._resolve(snapshot, node_id)
        except (NodeNotFound, StaleNodeError) as exc:
            return ActionResult.failure("type_text", str(exc), self._ms(started))

        # 1. ValuePattern.SetValue — anlik, klavye olayi uretmez, en guvenilir yol
        pattern = self._pattern(node, "Value")
        if pattern is not None:
            try:
                new_value = text if clear_first else (node.value + text)
                pattern.SetValue(new_value)
                return self._done("type_text", "set_value", node, started, before)
            except Exception:
                pass   # salt-okunur veya desteklemiyor; klavyeye dus

        # 2. Klavye yolu: pencere gercekten on planda olmali, yoksa yazi
        #    kullanicinin o an calistigi baska bir uygulamaya gider.
        if snapshot.window_handle:
            ok, detail = self._ensure_foreground(snapshot.window_handle)
            if not ok:
                return ActionResult.failure(
                    "type_text",
                    f"{node.describe()} ValuePattern desteklemiyor ve pencere one "
                    f"getirilemedi ({detail}). Yazi baska bir uygulamaya gitmesin "
                    "diye islem iptal edildi.",
                    self._ms(started),
                )
        try:
            node.element.SetFocus()
        except Exception as exc:
            return ActionResult.failure(
                "type_text",
                f"{node.describe()} odaklanamadi ve ValuePattern yok: {exc}",
                self._ms(started),
            )
        try:
            time.sleep(0.05)                      # odagin yerlesmesi icin
            if clear_first:
                # Duz kombinasyon yerine niyet: ayni kod macOS'ta meta+a uretir
                # (bkz. keynames.INTENTS).
                K.press_combo(intent_combo("select_all", "win32"))
                K.press_combo(intent_combo("clear_field", "win32"))
            K.type_unicode(text)
            return self._done("type_text", "keyboard", node, started, before)
        except Exception as exc:
            return ActionResult.failure("type_text", f"Klavye girisi: {exc}",
                                        self._ms(started))

    def press_key(self, keys: str, snapshot: Snapshot | None = None) -> ActionResult:
        """Tus kombinasyonu gonderir.

        Klavye girisi, ``click``/``type_text``ten temelde farklidir: bir dugume
        degil, **o an klavye odagi olan pencereye** gider. Hedef pencere on
        planda degilse tuslar kullanicinin o sirada yazdigi yere gider —
        hem islem basarisiz olur hem de baska bir uygulamada istenmeyen bir sey
        tetiklenebilir (olculdu: Not Defteri arka plandayken ctrl+s dosyayi
        kaydetmedi, tus baska pencereye gitti).

        Bu yuzden ``snapshot`` verildiginde pencere one getirilir ve **gercekten
        one geldigi dogrulanir**; gelmezse tus HIC gonderilmez.
        """
        started = time.perf_counter()

        if snapshot is not None and snapshot.window_handle:
            ok, detail = self._ensure_foreground(snapshot.window_handle)
            if not ok:
                return ActionResult.failure(
                    "press_key",
                    f"Hedef pencere one getirilemedi ({detail}); tus gonderilmedi. "
                    "Baska bir uygulamaya tus gondermek yerine islem iptal edildi.",
                    self._ms(started),
                )

        try:
            K.press_combo(keys)
            return ActionResult(ok=True, action="press_key", method="sendinput",
                                detail=keys, elapsed_ms=self._ms(started))
        except K.KeyParseError as exc:
            return ActionResult.failure("press_key", str(exc), self._ms(started))
        except Exception as exc:
            return ActionResult.failure("press_key", f"SendInput: {exc}",
                                        self._ms(started))

    def scroll(self, snapshot: Snapshot, direction: str, amount: int = 3,
               node_id: int | None = None) -> ActionResult:
        started = time.perf_counter()
        before = self._fingerprint(snapshot)
        direction = str(direction).lower()
        if direction not in ("up", "down", "left", "right"):
            return ActionResult.failure(
                "scroll", f"Gecersiz yon: '{direction}'. up/down/left/right bekleniyor.",
                self._ms(started))

        node: UINode | None = None
        if node_id is not None:
            try:
                node = self._resolve(snapshot, node_id)
            except (NodeNotFound, StaleNodeError) as exc:
                return ActionResult.failure("scroll", str(exc), self._ms(started))

        # 1. ScrollPattern — hedef konteyneri dogrudan kaydirir
        if node is not None:
            pattern = self._pattern(node, "Scroll")
            if pattern is not None:
                try:
                    # SmallIncrement=0, SmallDecrement=1, NoAmount=2 (UIA enum)
                    h, v = 2, 2
                    step = 0 if direction in ("down", "right") else 1
                    if direction in ("up", "down"):
                        v = step
                    else:
                        h = step
                    for _ in range(max(1, amount)):
                        pattern.Scroll(h, v)
                    return self._done("scroll", "scroll_pattern", node, started, before)
                except Exception:
                    pass

        # 2. Fare tekerlegi
        try:
            self._bring_to_front(snapshot)
            if direction in ("up", "down"):
                K.scroll_wheel(amount if direction == "up" else -amount)
            else:
                K.scroll_wheel(amount if direction == "right" else -amount,
                               horizontal=True)
            return self._done("scroll", "wheel", node, started, before)
        except Exception as exc:
            return ActionResult.failure("scroll", f"Tekerlek: {exc}", self._ms(started))

    def focus(self, snapshot: Snapshot, node_id: int) -> ActionResult:
        started = time.perf_counter()
        before = self._fingerprint(snapshot)
        try:
            node = self._resolve(snapshot, node_id)
            node.element.SetFocus()
            return self._done("focus", "set_focus", node, started, before)
        except (NodeNotFound, StaleNodeError) as exc:
            return ActionResult.failure("focus", str(exc), self._ms(started))
        except Exception as exc:
            return ActionResult.failure("focus", f"SetFocus: {exc}", self._ms(started))

    # ------------------------------------------------------------------ #
    #  Yardimcilar
    # ------------------------------------------------------------------ #
    #
    # _ms, _done, _fingerprint, _changed ve _bring_to_front artik
    # action/common.py:BaseExecutor icinde — hicbiri Windows'a ozgu degildi.

    @staticmethod
    def _ensure_foreground(hwnd: int) -> tuple[bool, str]:
        """Pencereyi on plana getirir ve **gercekten geldigini dogrular**.

        ``SetForegroundWindow`` Windows'un on plan kilidi nedeniyle sessizce
        veya ``Erisim engellendi`` ile basarisiz olabilir (olculdu: baska bir
        uygulama etkinken cagrildiginda hata 5). Donus degeri, cagiranin
        klavye girisi gibi odaga bagimli islemleri iptal edebilmesi icindir.

        Returns:
            ``(on_plandami, aciklama)``
        """
        try:
            import win32api  # noqa: PLC0415
            import win32con  # noqa: PLC0415
            import win32gui  # noqa: PLC0415
            import win32process  # noqa: PLC0415
            import ctypes  # noqa: PLC0415
        except Exception as exc:
            return False, f"win32 modulleri yuklenemedi: {exc}"

        try:
            if win32gui.GetForegroundWindow() == hwnd:
                return True, "zaten on planda"
        except Exception:
            pass

        user32 = ctypes.windll.user32
        try:
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                time.sleep(0.15)
        except Exception:
            pass

        # On plan kilidini asmanin standart yolu: hedef ve mevcut on plan
        # thread'lerinin girdi kuyruklarina baglanmak.
        attached: list[int] = []
        try:
            current = win32api.GetCurrentThreadId()
            foreground = win32gui.GetForegroundWindow()
            threads = set()
            if foreground:
                threads.add(win32process.GetWindowThreadProcessId(foreground)[0])
            threads.add(win32process.GetWindowThreadProcessId(hwnd)[0])
            for tid in threads:
                if tid and tid != current:
                    if user32.AttachThreadInput(current, tid, True):
                        attached.append(tid)
            try:
                win32gui.SetForegroundWindow(hwnd)
                user32.BringWindowToTop(hwnd)
            except Exception:
                pass
        except Exception as exc:
            return False, f"thread baglama: {exc}"
        finally:
            for tid in attached:
                try:
                    user32.AttachThreadInput(win32api.GetCurrentThreadId(), tid, False)
                except Exception:
                    pass

        time.sleep(0.12)
        try:
            actual = win32gui.GetForegroundWindow()
        except Exception:
            return False, "on plan penceresi okunamadi"
        if actual == hwnd:
            return True, "one getirildi"
        return False, f"on planda hala {actual} var (Windows on plan kilidi)"
