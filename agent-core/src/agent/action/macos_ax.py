"""macOS eylem yürütücüsü — native AX eylemi önce, piksel en son.

Windows tarafıyla aynı ilke: bir düğmeye basmanın doğru yolu ``AXPress``
göndermektir, sınırlayıcı kutunun merkezine fare tıklamak değil. ``AXPress``
de UIA ``Invoke`` gibi arka plandaki bir uygulamada çalışır, pencere önde
olmasa, imleç başka yerde olsa, eleman kısmen örtülü olsa bile. Dolayısıyla
"piksel tahmini yok" iddiası macOS'ta da ayakta kalır.

``ActionResult.method`` hangi yolun kullanıldığını kaydeder; ``pixel_fallback``
değeri mimarinin o eylemde işe yaramadığını gösteren ölçülebilir sinyaldir.

macOS'a özgü iki tuzak, ikisi de sessiz yanlış sonuç üretir:

* ``AXUIElementSetAttributeValue``, UIA'nın aksine, değeri kabul etmediğinde
  **hata vermeyebilir**. Bu yüzden yazma işleminden sonra değer geri okunup
  karşılaştırılır; uyuşmazsa sahte başarı yerine klavye yoluna düşülür.
* Değiştiriciler platforma göre farklıdır: ``clear_first`` için ``ctrl+a``
  göndermek macOS'ta metni seçmez, imleci satır başına götürür. Bu yüzden düz
  kombinasyon yerine ``keynames.INTENTS`` kullanılır.
"""

from __future__ import annotations

import time
from typing import Any

from ..core.errors import NodeNotFound, StaleNodeError
from ..core.types import ActionResult, Snapshot, UINode
from ..perception.macos_ax import _import_frameworks, accessibility_error
from . import keys_mac as K
from .common import BaseExecutor
from .keynames import KeyParseError, intent_combo, translate

_ERR_SUCCESS = 0
_ERR_API_DISABLED = -25211
_ERR_INVALID_ELEMENT = -25202
_ERR_CANNOT_COMPLETE = -25204

#: Kimlik doğrulamada okunan öznitelikler (bkz. _check_identity).
_IDENTITY_ATTRS = ("AXRole", "AXSubrole", "AXIdentifier", "AXTitle")


class MacAXExecutor(BaseExecutor):
    """AXUIElement eylem tabanlı yürütücü."""

    def __init__(self, extractor: Any = None, verify: bool = True,
                 pruner: Any = None, timeout: float = 0.75) -> None:
        super().__init__(extractor=extractor, verify=verify, pruner=pruner)
        self._AS, self._Quartz, self._NSWorkspace = _import_frameworks()
        self._timeout = timeout

    # ------------------------------------------------------------------ #
    #  AX yardımcıları
    # ------------------------------------------------------------------ #

    def _attr(self, element: Any, name: str) -> Any:
        err, value = self._AS.AXUIElementCopyAttributeValue(element, name, None)
        return value if err == _ERR_SUCCESS else None

    def _actions(self, element: Any) -> tuple[str, ...]:
        err, names = self._AS.AXUIElementCopyActionNames(element, None)
        if err != _ERR_SUCCESS or not names:
            return ()
        return tuple(str(n) for n in names)

    def _perform(self, element: Any, action: str) -> bool:
        return self._AS.AXUIElementPerformAction(element, action) == _ERR_SUCCESS

    def _settable(self, element: Any, attr: str = "AXValue") -> bool:
        err, settable = self._AS.AXUIElementIsAttributeSettable(element, attr, None)
        return bool(err == _ERR_SUCCESS and settable)

    # ------------------------------------------------------------------ #
    #  Kimlik doğrulama
    # ------------------------------------------------------------------ #

    def _check_identity(self, node: UINode) -> None:
        """Düğümün hâlâ aynı elemana işaret ettiğini doğrular.

        AX'te ``RuntimeId`` karşılığı yoktur, bu yüzden **karşılaştırma yerine
        doğrulama** yapılır: öznitelikler yeniden okunur ve kimlik alanlarının
        değişmediği kontrol edilir.

        Hata kodlarının ayrımı önemlidir:

        * ``-25202`` (InvalidUIElement) — eleman gerçekten yok olmuş, bayat.
        * ``-25204`` (CannotComplete) — uygulama meşgul veya zaman aşımı;
          bayat DEĞİL. Bunu bayat saymak, ağır yük altındaki uygulamalarda
          gereksiz hata üretirdi.

        Başlık değişimi hata sayılmaz: başlıklar oynaktır ("Belge — Düzenlendi").
        """
        err, _ = self._AS.AXUIElementCopyAttributeValue(node.element, "AXRole", None)

        if err == _ERR_API_DISABLED:
            raise accessibility_error()
        if err == _ERR_INVALID_ELEMENT:
            raise StaleNodeError(
                f"[@{node.node_id}] ({node.role} '{node.name}') artik mevcut degil — "
                "UI degismis olmali. Yeni bir anlik goruntu alin."
            )
        if err == _ERR_CANNOT_COMPLETE:
            time.sleep(0.15)                     # bir kez daha dene
            err, _ = self._AS.AXUIElementCopyAttributeValue(
                node.element, "AXRole", None
            )
            if err == _ERR_CANNOT_COMPLETE:
                raise StaleNodeError(
                    f"[@{node.node_id}] okunamadi: uygulama yanit vermiyor "
                    "(mesguliyet veya zaman asimi). Biraz bekleyip tekrar deneyin."
                )
        if err != _ERR_SUCCESS:
            raise StaleNodeError(
                f"[@{node.node_id}] dogrulanamadi (AX hata {err})."
            )

        # Rol/kimlik değiştiyse aynı yolda başka bir elemana bakıyoruz.
        current_role = str(self._attr(node.element, "AXRole") or "")
        current_sub = str(self._attr(node.element, "AXSubrole") or "")
        from ..perception.ax_roles import normalize_role  # noqa: PLC0415
        if current_role and normalize_role(current_role, current_sub) != node.role:
            raise StaleNodeError(
                f"[@{node.node_id}] baska bir elemana isaret ediyor "
                f"(beklenen {node.role}, bulunan "
                f"{normalize_role(current_role, current_sub)}). "
                "UI degismis; yeni bir anlik goruntu alin."
            )

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

        actions = self._actions(node.element)
        last_error = ""

        # 1. AXPress — düğmeler, menü öğeleri, bağlantılar
        if "AXPress" in actions:
            if self._perform(node.element, "AXPress"):
                return self._done("click", "press", node, started, before)
            last_error = "AXPress basarisiz"

        # 2. AXPick — menü/liste öğesi seçimi
        if "AXPick" in actions:
            if self._perform(node.element, "AXPick"):
                return self._done("click", "pick", node, started, before)
            last_error = "AXPick basarisiz"

        # 3. Aç/kapa — AXPress'i olmayan onay kutuları
        if node.role in ("CheckBox", "RadioButton") and self._settable(node.element):
            current = self._attr(node.element, "AXValue")
            new = 0 if current else 1
            if self._AS.AXUIElementSetAttributeValue(
                node.element, "AXValue", new
            ) == _ERR_SUCCESS:
                return self._done("click", "set_value", node, started, before)
            last_error = "AXValue ayarlanamadi"

        # 4. AXShowMenu — açılır düğmeler
        if "AXShowMenu" in actions:
            if self._perform(node.element, "AXShowMenu"):
                return self._done("click", "show_menu", node, started, before)
            last_error = "AXShowMenu basarisiz"

        # 5. AXConfirm
        if "AXConfirm" in actions:
            if self._perform(node.element, "AXConfirm"):
                return self._done("click", "confirm", node, started, before)
            last_error = "AXConfirm basarisiz"

        # 6. Son çare: sınırlayıcı kutunun merkezine fiziksel tıklama.
        if node.rect.is_empty:
            return ActionResult.failure(
                "click",
                f"{node.describe()} icin ne native AX eylemi ne de gecerli konum var. "
                f"{last_error}",
                self._ms(started),
            )
        try:
            self._bring_to_front(snapshot)
            x, y = node.rect.center
            K.click_at(x, y)
            result = self._done("click", "pixel_fallback", node, started, before)
            result.detail = f"({x},{y}) — native AX eylemi yoktu"
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

        # 1. AXValue ile doğrudan yazma — anlık, klavye olayı üretmez.
        if self._settable(node.element):
            new_value = text if clear_first else (node.value + text)
            err = self._AS.AXUIElementSetAttributeValue(
                node.element, "AXValue", new_value
            )
            if err == _ERR_SUCCESS:
                # GERİ OKU. AX, UIA'nın aksine, kabul etmediği değerde hata
                # vermeyebilir: web içerik alanları (Chrome/Safari) set'i
                # yutup hiçbir şey yapmaz. Doğrulamadan başarı raporlamak
                # LLM'e yalan söylemek olurdu.
                written = self._attr(node.element, "AXValue")
                if written is not None and str(written) == new_value:
                    return self._done("type_text", "set_value", node, started, before)

        # 2. Klavye yolu: pencere gerçekten ön planda olmalı, yoksa yazı
        #    kullanıcının o an çalıştığı başka bir uygulamaya gider.
        if snapshot.window_handle:
            ok, detail = self._ensure_foreground(snapshot.window_handle)
            if not ok:
                return ActionResult.failure(
                    "type_text",
                    f"{node.describe()} AXValue ile yazilamadi ve pencere one "
                    f"getirilemedi ({detail}). Yazi baska bir uygulamaya gitmesin "
                    "diye islem iptal edildi.",
                    self._ms(started),
                )

        if self._AS.AXUIElementSetAttributeValue(
            node.element, "AXFocused", True
        ) != _ERR_SUCCESS:
            return ActionResult.failure(
                "type_text",
                f"{node.describe()} odaklanamadi ve AXValue ile yazilamadi.",
                self._ms(started),
            )

        try:
            time.sleep(0.05)                      # odagin yerlesmesi icin
            if clear_first:
                K.press_combo(intent_combo("select_all", "darwin"))
                K.press_combo(intent_combo("clear_field", "darwin"))
            K.type_unicode(text)
            return self._done("type_text", "keyboard", node, started, before)
        except Exception as exc:
            return ActionResult.failure("type_text", f"Klavye girisi: {exc}",
                                        self._ms(started))

    def press_key(self, keys: str, snapshot: Snapshot | None = None,
                  translate_combo: bool = False) -> ActionResult:
        """Tuş kombinasyonu gönderir.

        Args:
            translate_combo: Windows alışkanlığıyla yazılmış ``ctrl+<harf>``
                kombinasyonlarını ``cmd+<harf>``ya çevirir. **Varsayılan
                kapalıdır ve öyle kalmalıdır**: macOS'ta ``ctrl+a``/``ctrl+e``/
                ``ctrl+k`` gerçek Cocoa kısayollarıdır (satır başı, satır sonu,
                satır sil) ve sessiz çeviri bunları erişilemez kılar.
                Çeviri devreye girdiğinde ``detail``de görünür.
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

        detail = keys
        try:
            combo = translate(keys, "darwin") if translate_combo else keys
            if translate_combo and str(combo) != str(keys).lower():
                detail = f"{keys} -> {combo} (platform cevirisi)"
            K.press_combo(combo)
            return ActionResult(ok=True, action="press_key", method="cgevent",
                                detail=detail, elapsed_ms=self._ms(started))
        except KeyParseError as exc:
            return ActionResult.failure("press_key", str(exc), self._ms(started))
        except Exception as exc:
            return ActionResult.failure("press_key", f"CGEvent: {exc}",
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

        # 1. Native: kaydırma çubuğunun AXValue'su (0.0–1.0).
        #    AX'te ScrollPattern karşılığı yoktur; en yakın yol budur.
        if node is not None:
            bar_attr = ("AXVerticalScrollBar" if direction in ("up", "down")
                        else "AXHorizontalScrollBar")
            bar = self._attr(node.element, bar_attr)
            if bar is not None and self._settable(bar):
                current = self._attr(bar, "AXValue")
                if current is not None:
                    step = 0.1 * max(1, amount)
                    delta = step if direction in ("down", "right") else -step
                    target = min(1.0, max(0.0, float(current) + delta))
                    if self._AS.AXUIElementSetAttributeValue(
                        bar, "AXValue", target
                    ) == _ERR_SUCCESS:
                        written = self._attr(bar, "AXValue")
                        if written is not None and abs(float(written) - target) < 0.01:
                            return self._done("scroll", "scrollbar_value", node,
                                              started, before)

        # 2. Fare tekerleği. CGEvent tekerlek olayı İMLECİN ALTINDAKİ pencereye
        #    gider, odaklanmış pencereye değil — bu yüzden imleci taşımak şart.
        try:
            self._bring_to_front(snapshot)
            target_rect = node.rect if (node and not node.rect.is_empty) else None
            old = K.cursor_position()
            if target_rect is not None:
                K.move_cursor(*target_rect.center)
            elif not snapshot.nodes:
                pass

            if direction in ("up", "down"):
                K.scroll_wheel(amount if direction == "up" else -amount)
            else:
                K.scroll_wheel(amount if direction == "right" else -amount,
                               horizontal=True)

            if target_rect is not None:
                K.move_cursor(*old)
            return self._done("scroll", "wheel", node, started, before)
        except Exception as exc:
            return ActionResult.failure("scroll", f"Tekerlek: {exc}", self._ms(started))

    def focus(self, snapshot: Snapshot, node_id: int) -> ActionResult:
        started = time.perf_counter()
        before = self._fingerprint(snapshot)
        try:
            node = self._resolve(snapshot, node_id)
        except (NodeNotFound, StaleNodeError) as exc:
            return ActionResult.failure("focus", str(exc), self._ms(started))

        err = self._AS.AXUIElementSetAttributeValue(node.element, "AXFocused", True)
        if err == _ERR_SUCCESS and self._attr(node.element, "AXFocused"):
            return self._done("focus", "set_focus", node, started, before)

        # Dürüst hata: düğmeler ve onay kutuları yalnızca Tam Klavye Erişimi
        # açıkken klavye odağı kabul eder. Yeşil "OK" dönmek yanlış olurdu.
        return ActionResult.failure(
            "focus",
            f"{node.describe()} odaklanamadi (AX hata {err}). Butonlar ve onay "
            "kutulari yalnizca Sistem Ayarlari > Klavye > Klavye ile gezinme "
            "acikken odak alabilir.",
            self._ms(started),
        )

    # ------------------------------------------------------------------ #
    #  Ön plan
    # ------------------------------------------------------------------ #

    def _ensure_foreground(self, window_handle: int) -> tuple[bool, str]:
        """Pencereyi öne getirir ve **geldiğini doğrular**.

        Windows'taki politika birebir korunur: doğrulanamazsa çağıran tuş
        göndermez. İki aşama gerekir çünkü bir uygulama ön planda olup yanlış
        penceresi önde olabilir — ``AXRaise`` belirli pencereyi kaldırır.
        """
        Q = self._Quartz
        info = Q.CGWindowListCopyWindowInfo(
            Q.kCGWindowListOptionIncludingWindow, window_handle
        ) or []
        if not info:
            return False, f"CGWindowID {window_handle} bulunamadi"
        pid = int(info[0].get("kCGWindowOwnerPID", 0))

        workspace = self._NSWorkspace.sharedWorkspace()
        front = workspace.frontmostApplication()
        if front is not None and int(front.processIdentifier()) == pid:
            already = True
        else:
            already = False

        app = self._AS.AXUIElementCreateApplication(pid)
        self._AS.AXUIElementSetMessagingTimeout(app, self._timeout)

        if not already:
            # 1. Saf AX yolu — paketlenmemis bir Python sureci icin genellikle
            #    AppKit'ten daha guvenilir.
            self._AS.AXUIElementSetAttributeValue(app, "AXFrontmost", True)

        # 2. Dogru PENCEREYI one al (uygulama zaten onde olabilir).
        for window in (self._attr(app, "AXWindows") or []):
            self._AS.AXUIElementPerformAction(window, "AXRaise")
            break

        # 3. AppKit geri dusumu.
        if not already:
            for running in workspace.runningApplications():
                if int(running.processIdentifier()) == pid:
                    try:
                        running.activateWithOptions_(1 << 1)   # IgnoringOtherApps
                    except Exception:
                        try:
                            running.activate()
                        except Exception:
                            pass
                    break

        # 4. DOGRULA — ~0.5 sn icinde.
        deadline = time.perf_counter() + 0.5
        while time.perf_counter() < deadline:
            current = workspace.frontmostApplication()
            if current is not None and int(current.processIdentifier()) == pid:
                return True, "zaten on planda" if already else "one getirildi"
            time.sleep(0.05)

        current = workspace.frontmostApplication()
        name = current.localizedName() if current is not None else "bilinmiyor"
        return False, f"on planda hala '{name}' var"
