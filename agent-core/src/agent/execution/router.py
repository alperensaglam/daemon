"""Hibrit yürütme motoru: aynı işi kabuktan mı, arayüzden mi yapmalı?

Saf bir UI agent'ı, "şu klasördeki 300 png'yi jpg yap" görevini 300 kez tıklayıp
bekleyerek çözmeye çalışır. Saf bir CLI agent'ı ise Photoshop'ta bir filtre
uygulayamaz. Sektör seviyesindeki fark, **iki şeridi birden** kullanıp doğru işi
doğru şeride vermekten çıkar.

Bu modülün iki sorumluluğu var:

1. **Yönlendirme (``classify``).** Bir hedef metnini okuyup hangi şeridin
   uygun olduğunu söyler. Kararı LLM'e *dayatmaz*, ipucu olarak verir: model
   ekranı görebilir, sınıflandırıcı göremez. Dayatma, yanlış tahminde görevi
   tamamen bloke ederdi.

2. **Dağıtım (``ToolRouter.dispatch``).** Modelin araç çağrısını ilgili şeride
   yollar, onay kapısından geçirir, UI eylemlerini ``Verifier`` ile doğrular ve
   sonucu modelin okuyabileceği tek bir sözlüğe indirger.

Dağıtımdaki en önemli kural bir **kilit**: bir eylem UI'yı değiştirdikten sonra
elde kalan ``node_id``'ler geçersizdir; router yeni bir ``get_state`` alınana
kadar UI eylemi kabul etmez. Bu kontrol olmadan model, eski bir listedeki
``[@7]``'ye "Kaydet" diye tıklar ve o id artık "Sil" olabilir.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from ..core.errors import (ActionVerificationError, AgentError,
                           ShellCommandBlocked)
from ..core.types import ActionResult, Snapshot
from ..llm import schemas
from ..safety import ApprovalGate
from .shell import ShellRunner
from .verifier import Expectation, Verifier, expectation_for


# --------------------------------------------------------------------------- #
#  Şeritler
# --------------------------------------------------------------------------- #

class Lane(str, Enum):
    """İşin yapılacağı yol."""

    UI = "ui"            # UIA / AXUIElement — native arayüz
    SHELL = "shell"      # PowerShell / bash — arka plan
    BROWSER = "browser"  # CDP köprüsü (integrations/browser_cdp.py) — planlandı


# (regex, ağırlık, gerekçe). Ağırlıklar kabaca "bu sinyal ne kadar ayırt edici"
# sorusuna göre: bir uygulama adı görmek, "aç" fiilinden çok daha bilgilendirici.
# Türkçe EKLEMELİ bir dildir ve bu, kelime tabanlı her sınıflandırıcının sessiz
# hatasıdır: "\bklasör\b" kalıbı "klasörünü", "\bsüreç\b" ise "süreci"
# kelimesini KAÇIRIR. safety.py'de aynı ders zaten ödendi (bkz. oradaki
# "Ödeme Yap" notu); bu yüzden kökler ``\w*`` ile biter. Bedeli ara sıra fazla
# eşleşmedir ("portföy" → port) ve çıktı bir ipucu olduğu için bu bedel ucuz.
_SHELL_SIGNALS: tuple[tuple[str, int, str], ...] = (
    (r"\bgit\b|\b(commit|branch|merge|rebase|repo|depo|pull request)\w*", 3,
     "git işlemi"),
    (r"\b(npm|pnpm|yarn|pip|brew|apt|winget|docker|make|gradle|maven)\b", 3,
     "paket/derleme aracı"),
    (r"\b(süreç|surec|process|pid|port|servis|service|daemon|işlem|islem)\w*", 3,
     "süreç yönetimi"),
    (r"\b(grep|regex|filtrele|ayıkla|ayikla|sayısı|sayisi|satır|satir)\w*", 2,
     "metin filtreleme"),
    (r"\b(dosya|klasör|klasor|dizin|file|folder|directory|path|yol)\w*", 2,
     "dosya sistemi"),
    (r"\b(taşı|tasi|kopyala|adlandır|adlandir|move|copy|rename|mkdir|temizle|"
     r"arşivle|arsivle|zip|unzip|tar)\w*", 2, "dosya işlemi"),
    (r"\b(log|json|csv|yaml|env|ortam değişkeni)\w*", 2,
     "yapılandırma/veri dosyası"),
    (r"\b(toplu|hepsi|batch|özyinelemeli|ozyinelemeli|recursive)\w*|"
     r"\btüm dosya|\btum dosya", 2, "toplu iş"),
    (r"\b(disk|bellek|ram|cpu|ip adresi|ağ|network|ping|curl)\w*", 2,
     "sistem/ağ sorgusu"),
    (r"\b(test|pytest|jest|lint|build|derle|script|betik)\w*", 2,
     "geliştirme komutu"),
    (r"\b(terminal|konsol|komut|kabuk|shell|bash|powershell|cli)\w*", 3,
     "kullanıcı açıkça kabuk istedi"),
)

_UI_SIGNALS: tuple[tuple[str, int, str], ...] = (
    (r"\b(tıkla|tikla|click|buton|düğme|dugme|button)\w*", 3,
     "tıklama gerektiren iş"),
    (r"\b(menü|menu|sekme|pencere|window|dialog|form|onay kutusu)\w*|"
     r"\biletişim kutusu|\bacilir liste|\baçılır liste", 3, "arayüz elemanı"),
    (r"\b(ekran|görsel|gorsel|arayüz|arayuz|gui|görünüm|gorunum)\w*", 2,
     "görsel iş"),
    (r"\b(chrome|edge|safari|firefox|brave|word|excel|powerpoint|outlook|slack|"
     r"discord|whatsapp|telegram|spotify|finder|explorer|notepad|photoshop|"
     r"figma|zoom|teams|calculator)\w*|\bnot defteri|\bhesap makinesi", 3,
     "belirli bir uygulama"),
    (r"\b(giriş yap|giris yap|oturum aç|login|şifre|sifre|doldur)\w*", 3,
     "arayüzde form/oturum"),
    (r"\b(kaydır|kaydir|scroll|seç|sec|işaretle|isaretle|sürükle|surukle)\w*", 2,
     "arayüz etkileşimi"),
    (r"\b(yazdır|yazdir|print|önizleme|onizleme|ayar|tercih|preferences)\w*", 2,
     "uygulama içi işlem"),
)

_BROWSER_SIGNALS: tuple[tuple[str, int, str], ...] = (
    (r"https?://|\bwww\.|\.com\b|\.org\b|\.net\b|\.io\b", 3, "URL"),
    (r"\b(web sitesi|websitesi|site|sayfa|link|bağlantı|baglanti|tarayıcı|"
     r"tarayici|browser)\w*", 2, "web içeriği"),
)


@dataclass(slots=True)
class RouteDecision:
    """Bir hedef metninin hangi şeride ait olduğuna dair *tahmin*."""

    lane: Lane
    confidence: float                       # 0.0 – 1.0
    reasons: list[str] = field(default_factory=list)
    scores: dict[str, int] = field(default_factory=dict)

    @property
    def is_confident(self) -> bool:
        return self.confidence >= 0.6

    def hint(self) -> str:
        """LLM'e verilecek tek paragraflık yönlendirme ipucu.

        Emir kipi bilinçli olarak yumuşak: sınıflandırıcı ekranı görmüyor.
        Modelin ipucunu göz ardı edebilmesi gerekir, yoksa yanlış bir tahmin
        görevi tamamen bloke eder.
        """
        if self.lane is Lane.SHELL and self.is_confident:
            return ("Yönlendirme ipucu: bu iş büyük olasılıkla run_shell ile "
                    f"tek komutta yapılabilir ({', '.join(self.reasons[:2])}). "
                    "Arayüzde tıklamakla uğraşmadan önce kabuğu dene.")
        if self.lane is Lane.BROWSER and self.is_confident:
            return ("Yönlendirme ipucu: hedef bir web sayfası. Tarayıcı "
                    "penceresinde çalışacaksan get_state çıktısı kalabalık "
                    "olacaktır; önce sayfayı daralt (arama kutusu, doğrudan URL).")
        if self.lane is Lane.UI and self.is_confident:
            return ("Yönlendirme ipucu: bu iş arayüz etkileşimi gerektiriyor "
                    f"({', '.join(self.reasons[:2])}). get_state ile başla.")
        return ("Yönlendirme ipucu: hangi yolun uygun olduğu belirsiz. Önce "
                "get_state ile ekrana bak; iş dosya/süreç/metin işlemeye "
                "dönüşürse run_shell'e geç.")


def classify(text: str) -> RouteDecision:
    """Hedef metninden şerit tahmini üretir.

    Anahtar kelime tabanlıdır ve bilinçli olarak öyle: bu karar için ikinci bir
    LLM çağrısı yapmak, kazandıracağı süreden fazlasını harcar (yerel modelde
    ölçülen ~12 token/s). Yanılma maliyeti de düşüktür — çıktı bir *ipucudur*,
    araçların hepsi her durumda açık kalır.
    """
    lowered = (text or "").casefold()
    scores: dict[Lane, int] = {Lane.UI: 0, Lane.SHELL: 0, Lane.BROWSER: 0}
    reasons: dict[Lane, list[str]] = {Lane.UI: [], Lane.SHELL: [], Lane.BROWSER: []}

    for lane, table in ((Lane.SHELL, _SHELL_SIGNALS), (Lane.UI, _UI_SIGNALS),
                        (Lane.BROWSER, _BROWSER_SIGNALS)):
        for pattern, weight, why in table:
            if re.search(pattern, lowered):
                scores[lane] += weight
                reasons[lane].append(why)

    total = sum(scores.values())
    if total == 0:
        return RouteDecision(Lane.UI, 0.0, ["belirgin sinyal yok"],
                             {l.value: 0 for l in scores})

    lane = max(scores, key=lambda k: scores[k])
    # Tarayıcı sinyali tek başına yeterli değil: "chrome'da youtube aç" hem UI
    # hem browser'dır ve CDP köprüsü yoksa UI şeridi doğru cevaptır.
    if lane is Lane.BROWSER and scores[Lane.UI] >= scores[Lane.BROWSER]:
        lane = Lane.UI

    return RouteDecision(
        lane=lane,
        confidence=round(scores[lane] / total, 2),
        reasons=reasons[lane] or ["belirgin sinyal yok"],
        scores={l.value: s for l, s in scores.items()},
    )


# --------------------------------------------------------------------------- #
#  Anlık görüntü kaynağı
# --------------------------------------------------------------------------- #

#: Bu kalıplara uyan kabuk komutları pencere açabilir; sonrasında UI ağacı
#: geçersiz sayılır.
_LAUNCHER = re.compile(
    r"\b(open|start|explorer|xdg-open|osascript|Start-Process|code|subl)\b",
    re.IGNORECASE,
)


class SnapshotSource:
    """Anlık görüntüyü üretir, önbellekler ve **tazeliğini** takip eder.

    Üç durumu ayırt eder ve bu ayrım sistemin doğruluğunu taşır:

    * ``_last``  — en son alınan ağaç (doğrulayıcının aldığı da buraya girer),
    * ``_seen``  — modelin ``get_state`` ile *gördüğü* ağacın id'si,
    * ``dirty``  — ikisi farklıysa: elde taze bir ağaç var ama model onu
      görmedi, dolayısıyla elindeki ``node_id``'ler bir önceki dünyaya ait.

    Doğrulayıcının aldığı görüntüyü önbelleğe koymak (``adopt``) ölçülebilir
    bir kazanç: eylemden sonraki ``get_state`` çağrısı ikinci bir ağaç çıkarımı
    yapmaz (Chrome'da ~114 ms).
    """

    def __init__(self, extractor: Any, pruner: Any, hwnd: int | None = None,
                 max_age_ms: float = 400.0,
                 vision_merge: Callable[[Snapshot], None] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._extractor = extractor
        self._pruner = pruner
        self._hwnd = hwnd
        self._max_age_ms = max_age_ms
        self._vision_merge = vision_merge
        self._clock = clock
        self._last: Snapshot | None = None
        self._seen: int | None = None

    # ------------------------------------------------------------------ #

    def take(self) -> Snapshot:
        """Koşulsuz yeni ağaç çıkarır."""
        snapshot = self._pruner.prune(self._extractor.extract(self._hwnd))
        if self._vision_merge is not None:
            self._vision_merge(snapshot)
        self._last = snapshot
        return snapshot

    def state(self, force: bool = False) -> Snapshot:
        """Modele gösterilecek görüntü. Yeterince tazeyse önbellekten verir."""
        snapshot = self._last
        if force or snapshot is None or self._age_ms(snapshot) > self._max_age_ms:
            snapshot = self.take()
        self._seen = snapshot.snapshot_id
        return snapshot

    def adopt(self, snapshot: Snapshot | None) -> None:
        """Başka bir bileşenin (doğrulayıcı) aldığı görüntüyü önbelleğe koyar."""
        if snapshot is not None:
            self._last = snapshot

    def peek(self) -> Snapshot | None:
        """Son bilinen görüntü — tazelik iddiası olmadan."""
        return self._last

    def invalidate(self) -> None:
        """Modelin gördüğü görüntüyü geçersiz ilan eder."""
        self._seen = None

    @property
    def dirty(self) -> bool:
        return self._last is None or self._last.snapshot_id != self._seen

    def for_action(self) -> Snapshot:
        """UI eyleminde kullanılacak görüntü.

        Raises:
            AgentError: Model güncel durumu görmeden eylem yapmaya çalışıyorsa.
        """
        if self._last is None:
            raise AgentError(
                "Henüz hiçbir durum okunmadı. Önce get_state çağır: node_id "
                "değerleri yalnızca en son get_state çıktısında geçerlidir."
            )
        if self.dirty:
            raise AgentError(
                "Arayüz son eylemden sonra değişti ve yeni durumu henüz "
                "okumadın. Önce get_state çağır — elindeki node_id'ler artık "
                "başka elemanlara işaret ediyor olabilir."
            )
        return self._last

    def _age_ms(self, snapshot: Snapshot) -> float:
        return (self._clock() - snapshot.created_at) * 1000.0


# --------------------------------------------------------------------------- #
#  Dağıtıcı
# --------------------------------------------------------------------------- #

@dataclass(slots=True)
class RouterConfig:
    max_wait_seconds: float = 10.0
    verify_ui_actions: bool = True
    shell_enabled: bool = True


class ToolRouter:
    """LLM araç çağrılarını doğru şeride dağıtır ve sonucu doğrular.

    Döngü (``orchestrator.loop``) bu sınıfın yalnızca iki üyesini bilir:
    ``tools`` ve ``dispatch``. Şerit seçimi, onay, doğrulama ve hata biçimleme
    burada kapalı kalır; böylece yeni bir şerit (CDP tarayıcı köprüsü)
    eklemek döngüyü değiştirmez.
    """

    def __init__(self, snapshots: SnapshotSource, executor: Any,
                 verifier: Verifier | None = None,
                 shell: ShellRunner | None = None,
                 gate: ApprovalGate | None = None,
                 config: RouterConfig | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.snapshots = snapshots
        self.executor = executor
        self.shell = shell
        self.gate = gate or ApprovalGate("ask")
        self.config = config or RouterConfig()
        self._sleep = sleep

        self.verifier = verifier
        if self.verifier is None and self.config.verify_ui_actions:
            self.verifier = Verifier(snapshots.take)
        # Yürütücünün kendi "UI değişti mi" kontrolü ikinci bir ağaç çıkarımı
        # demektir; doğrulayıcı aynı işi daha ayrıntılı yapıyorsa kapatılır.
        if self.verifier is not None and hasattr(executor, "set_verify"):
            executor.set_verify(False)

        self._handlers: dict[str, Callable[[dict], dict]] = {
            "get_state": self._get_state,
            "click": self._click,
            "type_text": self._type_text,
            "press_key": self._press_key,
            "scroll": self._scroll,
            "focus": self._focus,
            "wait": self._wait,
            "done": self._done,
        }
        if self.shell is not None and self.config.shell_enabled:
            self._handlers["run_shell"] = self._run_shell

    # ------------------------------------------------------------------ #
    #  Dışa bakan yüz
    # ------------------------------------------------------------------ #

    @property
    def tool_names(self) -> list[str]:
        return list(self._handlers)

    def tools(self, flavor: str = "openai") -> list[dict]:
        """Bu router'ın gerçekten çalıştırabildiği araçların şeması.

        Kabuk yoksa ``run_shell`` modele **hiç gösterilmez**. Gösterilip
        reddedilmesi daha kötüdür: model onu planına koyar, ret yer, döngüde
        bir tur kaybeder.
        """
        include_shell = "run_shell" in self._handlers
        if flavor == "anthropic":
            return schemas.anthropic_tools(include_shell=include_shell)
        return schemas.openai_tools(include_shell=include_shell)

    def dispatch(self, name: str, arguments: dict | None = None) -> dict:
        """Tek bir araç çağrısını çalıştırır ve modele dönecek sözlüğü verir.

        Hiçbir zaman istisna sızdırmaz: döngünün gördüğü her sonuç, modele geri
        beslenebilir bir sözlüktür. Bir hatanın döngüyü kırması değil, modelin
        onu okuyup düzeltmesi istenir.
        """
        handler = self._handlers.get(name)
        if handler is None:
            return {
                "ok": False,
                "error": f"Bilinmeyen araç: {name!r}. "
                         f"Kullanılabilir araçlar: {', '.join(self.tool_names)}.",
            }
        try:
            return handler(arguments or {})
        except ActionVerificationError as exc:
            return _verification_payload(name, exc)
        except ShellCommandBlocked as exc:
            return {"ok": False, "blocked": True, "error": str(exc)}
        except AgentError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:                       # noqa: BLE001
            # Beklenmeyen hata da modele geri bildirilir; süreci öldürmek,
            # kullanıcıya yarım kalmış bir görev bırakmaktan daha kötüdür.
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def route_hint(self, goal: str) -> str:
        """Hedef metni için yönlendirme ipucu (sistem promptuna eklenir)."""
        decision = classify(goal)
        if "run_shell" not in self._handlers and decision.lane is Lane.SHELL:
            return ("Yönlendirme ipucu: bu iş kabuğa uygun görünüyor ama kabuk "
                    "erişimi kapalı; arayüz üzerinden ilerlemen gerekiyor.")
        return decision.hint()

    # ------------------------------------------------------------------ #
    #  UI şeridi
    # ------------------------------------------------------------------ #

    def _get_state(self, args: dict) -> dict:
        snapshot = self.snapshots.state(force=bool(args.get("force")))
        return snapshot.to_state_dict()

    def _click(self, args: dict) -> dict:
        snapshot, node = self._target(args)
        allowed, reason = self.gate.check("click", node)
        if not allowed:
            return _denied("click", reason)
        expectation = expectation_for("click", node,
                                      expect_appears=args.get("expect_appears"))
        return self._perform("click", snapshot, expectation,
                             lambda: self.executor.click(snapshot, node.node_id))

    def _type_text(self, args: dict) -> dict:
        snapshot, node = self._target(args)
        text = str(args.get("text", ""))
        allowed, reason = self.gate.check("type_text", node, text)
        if not allowed:
            return _denied("type_text", reason)
        append = bool(args.get("append"))
        expectation = self._type_expectation(node, text, append,
                                             args.get("expect_appears"))
        return self._perform(
            "type_text", snapshot, expectation,
            lambda: self.executor.type_text(snapshot, node.node_id, text,
                                            clear_first=not append),
        )

    @staticmethod
    def _type_expectation(node, text: str, append: bool,
                          expect_appears: str | None) -> Expectation:
        """``type_text`` sonrası beklenen değer.

        ``append`` durumunda beklenen değer "eski + yeni"dir; ama eski değer
        budayıcı tarafından kırpılmışsa (80 karakter sınırı) doğru bir hedef
        metin kurulamaz. O durumda kesin karşılaştırma yerine "bir şey değişsin"
        beklentisine düşülür — yanlış bir beklenti, beklentisizlikten kötüdür.
        """
        if expect_appears:
            return Expectation.appears(expect_appears)
        if not append:
            return Expectation.value_equals(node, text)
        if node.value.endswith("…"):
            return Expectation.any_change(
                f"{node.describe()} içeriğine metin eklenmiş olmalı")
        return Expectation.value_equals(node, node.value + text)

    def _focus(self, args: dict) -> dict:
        snapshot, node = self._target(args)
        allowed, reason = self.gate.check("focus", node)
        if not allowed:
            return _denied("focus", reason)
        expectation = expectation_for("focus", node)
        return self._perform("focus", snapshot, expectation,
                             lambda: self.executor.focus(snapshot, node.node_id))

    def _scroll(self, args: dict) -> dict:
        snapshot = self.snapshots.for_action()
        direction = str(args.get("direction", "down"))
        amount = int(args.get("amount", 3) or 3)
        node_id = args.get("node_id")
        allowed, reason = self.gate.check("scroll", None, direction)
        if not allowed:
            return _denied("scroll", reason)
        expectation = expectation_for("scroll")
        return self._perform(
            "scroll", snapshot, expectation,
            lambda: self.executor.scroll(snapshot, direction, amount,
                                         int(node_id) if node_id is not None else None),
        )

    def _press_key(self, args: dict) -> dict:
        keys = str(args.get("keys", "")).strip()
        if not keys:
            return {"ok": False, "error": "keys parametresi boş olamaz."}
        allowed, reason = self.gate.check("press_key", None, keys)
        if not allowed:
            return _denied("press_key", reason)

        # Tuşlar düğüme değil odağa gider; snapshot yalnızca hedef pencerenin
        # önde olduğunu doğrulamak için verilir, node_id kullanılmaz.
        snapshot = self.snapshots.peek()
        expectation = expectation_for("press_key", keys=keys,
                                      expect_appears=args.get("expect_appears"))
        return self._perform("press_key", snapshot, expectation,
                             lambda: self.executor.press_key(keys, snapshot))

    def _wait(self, args: dict) -> dict:
        seconds = float(args.get("seconds", 1.0) or 0.0)
        seconds = max(0.0, min(seconds, self.config.max_wait_seconds))
        self._sleep(seconds)
        # Bekleme sırasında arayüz değişmiş olabilir; eldeki id'ler artık
        # garanti değil.
        self.snapshots.invalidate()
        return {"ok": True, "action": "wait", "waited_seconds": seconds,
                "note": "Bekleme bitti. Devam etmeden önce get_state al."}

    def _done(self, args: dict) -> dict:
        return {
            "ok": True,
            "done": True,
            "success": bool(args.get("success", False)),
            "summary": str(args.get("summary", "")),
        }

    # ------------------------------------------------------------------ #
    #  Kabuk şeridi
    # ------------------------------------------------------------------ #

    def _run_shell(self, args: dict) -> dict:
        command = str(args.get("command", "")).strip()
        if not command:
            return {"ok": False, "error": "command parametresi boş olamaz."}

        # Politika denetimi onaydan ÖNCE: hiçbir koşulda çalışmayacak bir komut
        # için kullanıcıyı rahatsız etmenin anlamı yok.
        blocked = self.shell.blocked(command)
        if blocked:
            raise ShellCommandBlocked(
                f"Komut politika gereği çalıştırılmadı ({blocked}): {command}. "
                "Aynı sonucu daha dar kapsamlı bir komutla elde etmeyi dene."
            )

        allowed, reason = self.gate.check("run_shell", None, command)
        if not allowed:
            return _denied("run_shell", reason)

        timeout = args.get("timeout")
        result = self.shell.run(
            command,
            timeout=float(timeout) if timeout else None,
            cwd=args.get("cwd") or None,
        )
        if _LAUNCHER.search(command):
            # Komut bir pencere açmış olabilir; UI id'leri artık güvenilmez.
            self.snapshots.invalidate()

        payload = result.to_dict()
        payload["lane"] = Lane.SHELL.value
        payload["action"] = "run_shell"
        if not result.ok and not payload.get("stderr") and not payload.get("error"):
            payload["hint"] = ("Komut sıfırdan farklı bir kod döndürdü ama çıktı "
                               "vermedi. Komutu doğrulamak için önce salt okuma "
                               "bir varyantını çalıştır (ör. ls / git status).")
        return payload

    # ------------------------------------------------------------------ #
    #  Ortak yürütme yolu
    # ------------------------------------------------------------------ #

    def _target(self, args: dict):
        """``node_id`` argümanını güncel anlık görüntüdeki düğüme çevirir."""
        snapshot = self.snapshots.for_action()
        raw = args.get("node_id")
        if raw is None:
            raise AgentError("node_id parametresi zorunlu.")
        try:
            node_id = int(raw)
        except (TypeError, ValueError):
            raise AgentError(f"node_id bir tam sayı olmalı, gelen: {raw!r}") from None

        node = snapshot.by_id(node_id)
        if node is None:
            valid = [n.node_id for n in snapshot.nodes]
            raise AgentError(
                f"[@{node_id}] bu durumda yok. Geçerli aralık: "
                f"1..{max(valid) if valid else 0}. Yeni bir get_state al."
            )
        return snapshot, node

    def _perform(self, action: str, before: Snapshot | None,
                 expectation: Expectation,
                 run: Callable[[], ActionResult]) -> dict:
        """Eylemi çalıştırır, doğrular ve modele dönecek sözlüğü kurar.

        Doğrulama başarısızsa sonuç ``ok: False`` döner ama ``executed: True``
        kalır. Bu ayrım önemli: model "başarısız" gördüğünde ilk içgüdüsü
        tekrar denemektir, oysa eylem gönderilmiştir — geri alınamaz bir işlemi
        ikinci kez tetiklemek gerçek bir hasar üretebilir.
        """
        result = run()
        payload: dict[str, Any] = result.to_dict()
        payload["lane"] = Lane.UI.value

        if not result.ok:
            payload.setdefault("hint",
                               "Eylem yürütülemedi. Yeni bir get_state alıp "
                               "başka bir eleman veya yol dene.")
            self.snapshots.invalidate()
            return payload

        if self.verifier is None or before is None:
            self.snapshots.invalidate()
            return payload

        report = self.verifier.verify(action, before, expectation)
        # Doğrulayıcının aldığı taze ağacı önbelleğe koy: sonraki get_state
        # ikinci bir çıkarım yapmasın.
        self.snapshots.adopt(report.after)
        self.snapshots.invalidate()

        payload["verification"] = report.to_dict()
        try:
            report.raise_for_status()
        except ActionVerificationError as exc:
            payload["ok"] = False
            payload["executed"] = True
            payload["error"] = str(exc)
        return payload


def build_router(mode: str = "ask", max_nodes: int = 150,
                 hwnd: int | None = None, shell: bool = True,
                 verify: bool = True, platform: str | None = None,
                 shell_config: Any = None,
                 gate: ApprovalGate | None = None) -> ToolRouter:
    """Platformun arka ucuyla birlikte tam bir router kurar.

    Tek montaj noktası: CLI, döngü ve testler aynı bileşen grafiğini kurmalı.
    Ayrı ayrı kurulsalardı biri ``attach_pruner`` çağırmayı unuturdu ve
    doğrulama sessizce anlamsızlaşırdı (bkz. ``action/common.py``).
    """
    # Yerel import: modül seviyesinde yapılırsa ``execution`` paketini import
    # etmek platform arka uçlarını da çeker ve paket, bağımlılıkları kurulu
    # olmayan bir makinede import edilemez hâle gelir.
    from ..perception.pruner import PruneConfig, TreePruner    # noqa: PLC0415
    from ..platform import create_backend                      # noqa: PLC0415

    pruner = TreePruner(PruneConfig(max_nodes=max_nodes))
    # verify=False: "UI değişti mi" kontrolünü Verifier üstlenir; yürütücüde de
    # açık kalırsa her eylemde iki kez ağaç çıkarılır.
    extractor, executor = create_backend(verify=False, platform=platform)
    executor.attach_pruner(pruner)

    source = SnapshotSource(extractor, pruner, hwnd=hwnd)
    return ToolRouter(
        snapshots=source,
        executor=executor,
        verifier=Verifier(source.take) if verify else None,
        shell=ShellRunner(shell_config) if shell else None,
        gate=gate or ApprovalGate(mode),
        config=RouterConfig(verify_ui_actions=verify, shell_enabled=shell),
    )


def _denied(action: str, reason: str) -> dict:
    return {"ok": False, "action": action, "denied": True, "executed": False,
            "error": f"Eylem uygulanmadı: {reason}",
            "hint": "Kullanıcı bu eyleme izin vermedi. Israr etme; alternatif "
                    "bir yol öner veya done ile durumu bildir."}


def _verification_payload(action: str, exc: ActionVerificationError) -> dict:
    report = exc.report
    payload: dict[str, Any] = {
        "ok": False,
        "action": action,
        "executed": True,
        "error": str(exc),
    }
    if report is not None:
        payload["verification"] = report.to_dict()
    return payload
