"""Eylem doğrulama: Action → Observation → Verification döngüsü.

Bir masaüstü agent'ının en pahalı hata sınıfı çökme değil, **sessiz
başarısızlıktır**: ``InvokePattern.Invoke()`` hatasız döner, ``ActionResult.ok``
``True`` olur, ama düğme devre dışıdır, bir katman onu örtmüştür veya
uygulama isteği yutmuştur. Model doğru düğmeye bastığını sanar ve sonraki tüm
adımlarını yanlış bir varsayımın üzerine kurar.

Bu modül her eylemden sonra şunu yapar:

1. ~60 ms bekler (UI'nin tepki vermesi için),
2. yeni bir anlık görüntü alır,
3. eylem **öncesi** ağaçla karşılaştırır (``StateDiff``),
4. beklenen değişikliğin gerçekleşip gerçekleşmediğine bakar (``Expectation``),
5. gerçekleşmediyse modele *ne beklendiğini, ne gözlendiğini ve ne
   denenebileceğini* söyleyen bir geri bildirim üretir.

Neden tek bir gözlem yetmez: bazı arayüzler 60 ms'de, bazıları 600 ms'de tepki
verir. Sabit uzun bir bekleme her adıma gecikme eklerdi; bu yüzden gözlem
**yoklamalıdır** — beklenen değişiklik görülür görülmez döner, görülmezse zaman
aşımına kadar tekrar bakar. Hızlı durumda maliyet tek bir snapshot'tır.

Düğüm kimliği (``node_key``) burada kritik bir tasarım kararıdır: ``node_id``
anlık görüntüye özeldir, iki snapshot arasında **karşılaştırılamaz**. Kimlik
sırasıyla ``automation_id`` → ``name`` → konum ızgarasından türetilir; ``value``
bilinçli olarak dışarıda bırakılır, aksi halde bir metin kutusuna yazmak o
düğümü "kayboldu + yeni geldi" gibi gösterirdi.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from ..core.errors import ActionVerificationError
from ..core.types import Snapshot, UINode

# Konumdan kimlik üretirken kullanılan ızgara. Bir düğüm birkaç piksel
# oynadığında kimliğinin değişmemesi için koordinatlar bu adıma yuvarlanır.
_POS_GRID = 8

# Bu kadar pikselden küçük yer değiştirmeler "hareket" sayılmaz; alt piksel
# yerleşim farkları her karşılaştırmayı gürültüye boğardı.
_MOVE_EPS = 3

# Kaydırma sezgisi: en az bu kadar düğüm aynı yönde kaymalı.
_SCROLL_MIN_NODES = 3

#: Anlık görüntüde uzun metinler ``TreePruner._clip`` tarafından kırpılır ve
#: sonuna bu karakter eklenir. Değer karşılaştırması bunu bilmek zorundadır.
_ELLIPSIS = "…"


def normalize(text: str) -> str:
    """Karşılaştırma için metni tekilleştirir (pruner ile aynı kural)."""
    return " ".join((text or "").split())


def node_key(node: UINode) -> tuple:
    """Anlık görüntüler arasında geçerli düğüm kimliği.

    ``node_id`` kullanılamaz: her budamada baştan atanır. Buradaki sıra
    bilinçlidir — ``automation_id`` kararlıdır, ad genellikle kararlıdır,
    konum ise son çaredir.
    """
    if node.automation_id:
        return (node.role, "aid", node.automation_id)
    name = normalize(node.name).casefold()
    if name:
        return (node.role, "name", name)
    cx, cy = node.rect.center
    return (node.role, "pos", cx // _POS_GRID, cy // _POS_GRID)


# --------------------------------------------------------------------------- #
#  Durum farkı
# --------------------------------------------------------------------------- #

@dataclass(slots=True)
class NodeChange:
    """Aynı düğümün iki anlık görüntü arasındaki değişimi."""

    before: UINode
    after: UINode
    fields: tuple[str, ...]      # "value" | "name" | "enabled" | "focused" | "moved"

    def describe(self) -> str:
        label = self.after.name or self.after.automation_id or self.after.role
        if "value" in self.fields:
            return (f"{self.after.role} '{label}' değeri "
                    f"{self.before.value!r} → {self.after.value!r}")
        return f"{self.after.role} '{label}' değişti: {', '.join(self.fields)}"


def _changed_fields(before: UINode, after: UINode) -> tuple[str, ...]:
    fields: list[str] = []
    if before.value != after.value:
        fields.append("value")
    if before.name != after.name:
        fields.append("name")
    if before.enabled != after.enabled:
        fields.append("enabled")
    if before.focused != after.focused:
        fields.append("focused")
    if (abs(before.rect.left - after.rect.left) > _MOVE_EPS
            or abs(before.rect.top - after.rect.top) > _MOVE_EPS):
        fields.append("moved")
    if (abs(before.rect.width - after.rect.width) > _MOVE_EPS
            or abs(before.rect.height - after.rect.height) > _MOVE_EPS):
        fields.append("resized")
    return tuple(fields)


@dataclass(slots=True)
class StateDiff:
    """İki anlık görüntü arasındaki fark — doğrulamanın ham malzemesi."""

    added: list[UINode] = field(default_factory=list)
    removed: list[UINode] = field(default_factory=list)
    changed: list[NodeChange] = field(default_factory=list)
    window_before: str = ""
    window_after: str = ""
    handle_before: int = 0
    handle_after: int = 0
    focus_before: UINode | None = None
    focus_after: UINode | None = None

    # ------------------------------------------------------------------ #

    @classmethod
    def between(cls, before: Snapshot, after: Snapshot) -> "StateDiff":
        """İki snapshot'ı karşılaştırır.

        Eşleştirme ``node_key`` üzerinden yapılır; aynı kimlikten birden fazla
        varsa (iki tane "Sil" düğmesi) belge sırasına göre eşlenirler. Kimliği
        konumdan gelen adsız bir düğüm yer değiştirirse "silinmiş + eklenmiş"
        görünür; bu, ucuz eşleştirmenin kabul edilen bedelidir ve doğrulamayı
        yalnızca daha *muhafazakâr* yapar (fark olduğunu söyler, olmadığını
        değil).
        """
        order: dict[int, int] = {}
        before_map: dict[tuple, list[UINode]] = defaultdict(list)
        after_map: dict[tuple, list[UINode]] = defaultdict(list)

        for index, node in enumerate(before.nodes):
            before_map[node_key(node)].append(node)
            order[id(node)] = index
        for index, node in enumerate(after.nodes):
            after_map[node_key(node)].append(node)
            order[id(node)] = index

        added: list[UINode] = []
        removed: list[UINode] = []
        changed: list[NodeChange] = []

        for key, after_nodes in after_map.items():
            before_nodes = before_map.get(key, ())
            for position, node in enumerate(after_nodes):
                if position >= len(before_nodes):
                    added.append(node)
                    continue
                fields = _changed_fields(before_nodes[position], node)
                if fields:
                    changed.append(NodeChange(before_nodes[position], node, fields))

        for key, before_nodes in before_map.items():
            surplus = len(before_nodes) - len(after_map.get(key, ()))
            if surplus > 0:
                removed.extend(before_nodes[-surplus:])

        added.sort(key=lambda n: order[id(n)])
        removed.sort(key=lambda n: order[id(n)])
        changed.sort(key=lambda c: order[id(c.after)])

        return cls(
            added=added,
            removed=removed,
            changed=changed,
            window_before=before.active_window,
            window_after=after.active_window,
            handle_before=before.window_handle,
            handle_after=after.window_handle,
            focus_before=_focused(before),
            focus_after=_focused(after),
        )

    # ------------------------------------------------------------------ #

    @property
    def window_changed(self) -> bool:
        """Aktif pencere değişti mi?

        İki ölçüt de gerekli: tutamaç, başlığı aynı olan iki pencereyi
        (iki "Adsız - Not Defteri") ayırır; başlık ise aynı pencerenin durum
        değişimini yakalar ("Adsız" → "notlar.txt"), ki bu kaydetme gibi
        eylemlerin tek görünür kanıtıdır.
        """
        if self.handle_before and self.handle_after \
                and self.handle_before != self.handle_after:
            return True
        return self.window_before != self.window_after

    @property
    def focus_moved(self) -> bool:
        before = node_key(self.focus_before) if self.focus_before else None
        after = node_key(self.focus_after) if self.focus_after else None
        return before != after

    @property
    def is_empty(self) -> bool:
        """Hiçbir şey değişmedi mi? 'Eylem etkisiz kaldı'nın en güçlü işareti."""
        return not (self.added or self.removed or self.changed
                    or self.window_changed)

    @property
    def looks_scrolled(self) -> bool:
        """Çok sayıda düğüm aynı yönde kaydıysa bu bir kaydırmadır."""
        deltas = [c.after.rect.top - c.before.rect.top
                  for c in self.changed if "moved" in c.fields]
        if len(deltas) < _SCROLL_MIN_NODES:
            return False
        return all(d > 0 for d in deltas) or all(d < 0 for d in deltas)

    def find_after(self, key: tuple) -> UINode | None:
        """Eylem sonrası ağaçta bu kimliğe sahip düğüm (varsa)."""
        for change in self.changed:
            if node_key(change.after) == key:
                return change.after
        return None

    # ------------------------------------------------------------------ #

    def summary(self, limit: int = 3) -> str:
        """Modelin okuyacağı tek satırlık özet.

        Uzunluk bilinçli olarak sınırlı: bu metin her adımda bağlama girer,
        20 düğümlük bir liste yazmak doğrulamanın maliyetini faydasının üstüne
        çıkarırdı.
        """
        if self.is_empty:
            return "arayüzde hiçbir değişiklik yok"

        parts: list[str] = []
        if self.window_changed:
            parts.append(f"aktif pencere değişti: {self.window_before!r} → "
                         f"{self.window_after!r}")
        if self.added:
            names = _labels(self.added, limit)
            parts.append(f"{len(self.added)} yeni düğüm ({names})")
        if self.removed:
            names = _labels(self.removed, limit)
            parts.append(f"{len(self.removed)} düğüm kayboldu ({names})")
        if self.changed:
            detail = "; ".join(c.describe() for c in self.changed[:limit])
            parts.append(f"{len(self.changed)} düğüm değişti ({detail})")
        if self.looks_scrolled:
            parts.append("içerik kaydırılmış görünüyor")
        return ", ".join(parts)

    def to_dict(self, limit: int = 3) -> dict:
        out: dict[str, Any] = {"changed": not self.is_empty}
        if self.window_changed:
            out["active_window"] = self.window_after
        if self.added:
            out["appeared"] = [n.describe() for n in self.added[:limit]]
        if self.removed:
            out["disappeared"] = [n.describe() for n in self.removed[:limit]]
        if self.changed:
            out["updated"] = [c.describe() for c in self.changed[:limit]]
        return out


def _focused(snapshot: Snapshot) -> UINode | None:
    for node in snapshot.nodes:
        if node.focused:
            return node
    return None


def _labels(nodes: list[UINode], limit: int) -> str:
    shown = [f"{n.role} '{n.name or n.value or n.automation_id}'"
             for n in nodes[:limit]]
    if len(nodes) > limit:
        shown.append("…")
    return ", ".join(shown)


# --------------------------------------------------------------------------- #
#  Beklenti
# --------------------------------------------------------------------------- #

class ExpectationKind(str, Enum):
    NONE = "none"                    # doğrulama gerekmez (wait, get_state)
    ANY_CHANGE = "any_change"        # bir şeyin değişmesi yeter
    VALUE_EQUALS = "value_equals"    # hedef alanın değeri şu olmalı
    APPEARS = "appears"              # şu metni taşıyan bir düğüm belirmeli
    DISAPPEARS = "disappears"        # hedef düğüm kaybolmalı
    WINDOW_CHANGED = "window_changed"
    FOCUS_ON = "focus_on"
    SCROLLED = "scrolled"


@dataclass(frozen=True, slots=True)
class Expectation:
    """Bir eylemden sonra gözlenmesi beklenen değişiklik.

    Beklenti eylemden ayrı bir nesnedir çünkü aynı ``click`` bazen "modal
    açılsın", bazen "bu satır seçilsin" demektir. Model isterse beklentiyi
    kendisi bildirir (``expect_appears``), bildirmezse ``expectation_for``
    eylem tipinden makul bir varsayılan üretir.
    """

    kind: ExpectationKind
    target: tuple | None = None      # node_key(...) — hedef düğümün kimliği
    text: str = ""
    label: str = ""                  # insan-okur açıklama (LLM'e de gider)

    # -------------------------------------------------------------- kurucular

    @classmethod
    def none(cls, label: str = "doğrulama gerekmiyor") -> "Expectation":
        return cls(ExpectationKind.NONE, label=label)

    @classmethod
    def any_change(cls, label: str = "arayüzde bir değişiklik") -> "Expectation":
        return cls(ExpectationKind.ANY_CHANGE, label=label)

    @classmethod
    def value_equals(cls, node: UINode, text: str) -> "Expectation":
        return cls(ExpectationKind.VALUE_EQUALS, target=node_key(node),
                   text=normalize(text),
                   label=f"{node.role} '{node.name}' değeri {normalize(text)!r} olmalı")

    @classmethod
    def appears(cls, text: str) -> "Expectation":
        return cls(ExpectationKind.APPEARS, text=normalize(text),
                   label=f"{normalize(text)!r} içeren bir eleman belirmeli")

    @classmethod
    def disappears(cls, node: UINode) -> "Expectation":
        return cls(ExpectationKind.DISAPPEARS, target=node_key(node),
                   label=f"{node.describe()} kaybolmalı")

    @classmethod
    def window_changed(cls) -> "Expectation":
        return cls(ExpectationKind.WINDOW_CHANGED,
                   label="aktif pencere değişmeli (kapanma/yeni pencere)")

    @classmethod
    def focus_on(cls, node: UINode) -> "Expectation":
        return cls(ExpectationKind.FOCUS_ON, target=node_key(node),
                   label=f"klavye odağı {node.describe()} üzerine geçmeli")

    @classmethod
    def scrolled(cls) -> "Expectation":
        return cls(ExpectationKind.SCROLLED, label="içerik kaymalı")

    # ---------------------------------------------------------------- denetim

    def check(self, diff: StateDiff, after: Snapshot) -> tuple[bool, str]:
        """Beklenti karşılandı mı? ``(sonuç, gerekçe)`` döner."""
        if self.kind is ExpectationKind.NONE:
            return True, "doğrulama istenmedi"

        if self.kind is ExpectationKind.ANY_CHANGE:
            if diff.is_empty:
                return False, "arayüzde ölçülebilir hiçbir değişiklik olmadı"
            return True, diff.summary()

        if self.kind is ExpectationKind.VALUE_EQUALS:
            return self._check_value(diff, after)

        if self.kind is ExpectationKind.APPEARS:
            return self._check_appears(diff)

        if self.kind is ExpectationKind.DISAPPEARS:
            if diff.window_changed:
                return True, "pencere değişti, hedef artık görünmüyor"
            if any(node_key(n) == self.target for n in diff.removed):
                return True, "hedef düğüm kayboldu"
            return False, "hedef düğüm hâlâ ekranda"

        if self.kind is ExpectationKind.WINDOW_CHANGED:
            if diff.window_changed:
                return True, f"aktif pencere artık {diff.window_after!r}"
            return False, "aktif pencere değişmedi"

        if self.kind is ExpectationKind.FOCUS_ON:
            focused = diff.focus_after
            if focused is not None and node_key(focused) == self.target:
                return True, "odak hedefe geçti"
            if focused is None:
                # Odak bilgisini vermeyen arayüzler var; yokluğu kanıt saymayız.
                return not diff.is_empty, "odak bilgisi okunamadı"
            return False, f"odak başka elemanda: {focused.describe()}"

        if self.kind is ExpectationKind.SCROLLED:
            if diff.looks_scrolled:
                return True, "içerik kaydı"
            if not diff.is_empty:
                # Sanal listeler kaydırmada düğümleri yeniden üretir; kaymış
                # gibi görünmez ama liste tamamen değişir. Bu da kaydırmadır.
                return True, diff.summary()
            return False, "kaydırma sonrası hiçbir şey değişmedi"

        return True, "bilinmeyen beklenti tipi — doğrulama atlandı"

    # ------------------------------------------------------------------ #

    def _check_value(self, diff: StateDiff, after: Snapshot) -> tuple[bool, str]:
        node = diff.find_after(self.target)
        if node is None:
            node = next((n for n in after.nodes if node_key(n) == self.target), None)
        if node is None:
            return False, "hedef alan yeni durumda bulunamadı (ağaç değişmiş olabilir)"

        observed = normalize(node.value)
        if _value_matches(observed, self.text):
            return True, f"alanın değeri {observed!r}"
        # Bazı alanlar metni ``value`` yerine ``name`` altında yayınlar.
        if _value_matches(normalize(node.name), self.text):
            return True, "metin elemanın adında görünüyor"
        return False, f"alanın değeri {observed!r}, beklenen {self.text!r}"

    def _check_appears(self, diff: StateDiff) -> tuple[bool, str]:
        needle = self.text.casefold()
        for node in diff.added:
            if needle in normalize(node.name).casefold() or \
                    needle in normalize(node.value).casefold():
                return True, f"beliren eleman: {node.describe()}"
        for change in diff.changed:
            if "enabled" in change.fields and \
                    needle in normalize(change.after.name).casefold():
                return True, f"eleman etkinleşti: {change.after.describe()}"
        if needle in diff.window_after.casefold() and diff.window_changed:
            return True, f"pencere başlığı eşleşti: {diff.window_after!r}"
        return False, f"{self.text!r} içeren yeni bir eleman görünmedi"


def _value_matches(observed: str, expected: str) -> bool:
    """Kırpılmış değerleri de doğru karşılaştırır.

    ``TreePruner`` 80 karakterden uzun metinleri kırpıp sonuna ``…`` koyar.
    Bunu bilmeyen bir karşılaştırma, uzun metin yazan her ``type_text``
    çağrısını "başarısız" ilan ederdi.
    """
    if observed == expected:
        return True
    if observed.endswith(_ELLIPSIS):
        return expected.startswith(observed[:-1])
    return False


#: ``press_key`` ile pencere kapatan/değiştiren kombinasyonlar.
_WINDOW_KEYS = frozenset({"alt+f4", "cmd+w", "cmd+q", "ctrl+w", "cmd+shift+w"})


def expectation_for(action: str, node: UINode | None = None, text: str | None = None,
                    keys: str | None = None,
                    expect_appears: str | None = None) -> Expectation:
    """Eylem tipinden makul bir varsayılan beklenti üretir.

    Modelin bildirdiği beklenti (``expect_appears``) daima kazanır: o, göreve
    özgü bilgidir; buradaki tablo yalnızca genel bir tahmindir.
    """
    if expect_appears:
        return Expectation.appears(expect_appears)

    if action in ("get_state", "snapshot", "wait", "done", "run_shell"):
        return Expectation.none()

    if action == "type_text" and node is not None and text is not None:
        return Expectation.value_equals(node, text)

    if action == "focus" and node is not None:
        return Expectation.focus_on(node)

    if action == "scroll":
        return Expectation.scrolled()

    if action == "press_key":
        if keys and keys.strip().lower().replace(" ", "") in _WINDOW_KEYS:
            return Expectation.window_changed()
        return Expectation.any_change()

    return Expectation.any_change()


# --------------------------------------------------------------------------- #
#  Rapor
# --------------------------------------------------------------------------- #

@dataclass(slots=True)
class VerificationReport:
    """Doğrulamanın sonucu — hem log hem LLM geri bildirimi."""

    action: str
    satisfied: bool
    expectation: Expectation
    reason: str = ""
    diff: StateDiff | None = None
    after: Snapshot | None = None      # taze durum; çağıran yeniden çıkarmasın
    attempts: int = 0
    waited_ms: float = 0.0
    error: str = ""                    # durum okunamadıysa

    @property
    def hint(self) -> str:
        """Modelin bir sonraki denemesi için somut öneri (self-healing).

        Genel "tekrar dene" tavsiyesi işe yaramaz; hangi *başka* yolun
        denenebileceğini söylemek gerekir. Aksi halde model aynı çağrıyı
        tekrarlayıp döngüye girer — ölçülen en sık başarısızlık biçimi budur.
        """
        if self.satisfied:
            return ""
        if self.error:
            return ("Eylem sonrası durum okunamadı. Yeni bir get_state al ve "
                    "gerçekten ne olduğunu gör.")
        if self.diff is not None and self.diff.is_empty:
            return ("Arayüz hiç tepki vermedi. AYNI eylemi tekrarlama. Sırayla "
                    "dene: (1) hedefe focus verip press_key ile 'enter'/'space', "
                    "(2) elemanı açan üst menü/sekmeyi önce aç, (3) get_state alıp "
                    "farklı bir eleman seç, (4) bu iş kabuktan yapılabiliyorsa "
                    "run_shell kullan.")
        if self.expectation.kind is ExpectationKind.VALUE_EQUALS:
            return ("Alan beklenen metni almadı. Alan maskeli veya salt-okunur "
                    "olabilir: önce focus + press_key ile içeriği temizleyip "
                    "yeniden yazmayı dene, ya da başka bir alan seç.")
        if self.diff is not None and self.diff.window_changed:
            return ("Aktif pencere değişti; eldeki tüm node_id'ler geçersiz. "
                    "Önce get_state al.")
        return ("Beklenen değişiklik olmadı ama arayüz değişti. Yeni durumu "
                "get_state ile oku ve planı buna göre güncelle.")

    def to_dict(self) -> dict:
        out: dict[str, Any] = {
            "verified": self.satisfied,
            "expected": self.expectation.label,
            "observed": self.reason,
        }
        if self.waited_ms:
            out["waited_ms"] = round(self.waited_ms, 1)
        if self.diff is not None and not self.satisfied:
            out["diff"] = self.diff.to_dict()
        if self.error:
            out["error"] = self.error
        if not self.satisfied:
            out["hint"] = self.hint
        return out

    def raise_for_status(self) -> None:
        """Doğrulanmadıysa ``ActionVerificationError`` fırlatır."""
        if self.satisfied:
            return
        raise ActionVerificationError(
            f"{self.action}: beklenen '{self.expectation.label}' gerçekleşmedi "
            f"({self.reason}). {self.hint}",
            action=self.action,
            report=self,
        )


# --------------------------------------------------------------------------- #
#  Doğrulayıcı
# --------------------------------------------------------------------------- #

class Verifier:
    """Eylem sonrası gözlem yapıp beklentiyi denetler.

    Zamanlama parametreleri ölçüme dayalı: yerel uygulamalarda tepki tipik
    olarak ilk 60 ms içinde görülür (bu makinede Not Defteri'nde ~27 ms'lik
    extract ile), Chromium tabanlı arayüzlerde birkaç yüz milisaniye sürebilir.
    Bu yüzden ilk bakış erken, zaman aşımı geniştir; başarılı durumda maliyet
    tek snapshot'tır.
    """

    def __init__(self, snapshot_fn: Callable[[], Snapshot],
                 settle_ms: float = 60.0, poll_ms: float = 80.0,
                 timeout_ms: float = 1200.0,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.perf_counter) -> None:
        """
        Args:
            snapshot_fn: Taze anlık görüntü üreten çağrılabilir. Doğrulayıcı
                çıkarıcıyı ve budayıcıyı bilmez; testler burada sabit bir
                liste döndürebilir.
            settle_ms: İlk gözlemden önceki bekleme.
            poll_ms: Beklenti karşılanmadıysa tekrar bakma aralığı.
            timeout_ms: Toplam bekleme üst sınırı.
            sleep / clock: Testlerin zamanı hızlandırabilmesi için dışarıdan
                verilebilir.
        """
        self._snapshot = snapshot_fn
        self.settle_ms = settle_ms
        self.poll_ms = poll_ms
        self.timeout_ms = timeout_ms
        self._sleep = sleep
        self._clock = clock

    # ------------------------------------------------------------------ #

    def verify(self, action: str, before: Snapshot,
               expectation: Expectation | None = None) -> VerificationReport:
        """Eylemi doğrular ve raporu döndürür (hata fırlatmaz).

        Fırlatma kararı çağırana bırakılır: döngü bu raporu LLM'e geri bildirim
        olarak vermek ister, CLI ise ``raise_for_status()`` ile hata almak
        isteyebilir.
        """
        expectation = expectation or Expectation.any_change()
        if expectation.kind is ExpectationKind.NONE:
            return VerificationReport(action=action, satisfied=True,
                                      expectation=expectation,
                                      reason="doğrulama istenmedi")

        started = self._clock()
        deadline = started + self.timeout_ms / 1000.0
        self._sleep(self.settle_ms / 1000.0)

        attempts = 0
        last_diff: StateDiff | None = None
        last_after: Snapshot | None = None
        last_reason = "gözlem yapılamadı"

        while True:
            attempts += 1
            try:
                after = self._snapshot()
            except Exception as exc:                     # noqa: BLE001
                return self._unreadable(action, expectation, exc, attempts,
                                        self._elapsed_ms(started))

            diff = StateDiff.between(before, after)
            ok, reason = expectation.check(diff, after)
            last_diff, last_after, last_reason = diff, after, reason

            if ok:
                return VerificationReport(
                    action=action, satisfied=True, expectation=expectation,
                    reason=reason, diff=diff, after=after, attempts=attempts,
                    waited_ms=self._elapsed_ms(started),
                )
            if self._clock() >= deadline:
                break
            self._sleep(self.poll_ms / 1000.0)

        return VerificationReport(
            action=action, satisfied=False, expectation=expectation,
            reason=last_reason, diff=last_diff, after=last_after,
            attempts=attempts, waited_ms=self._elapsed_ms(started),
        )

    # ------------------------------------------------------------------ #

    def _unreadable(self, action: str, expectation: Expectation, exc: Exception,
                    attempts: int, waited_ms: float) -> VerificationReport:
        """Anlık görüntü alınamadı — bu her zaman başarısızlık değildir.

        Pencereyi kapatan bir eylemden sonra ``extract()`` "aktif pencere yok"
        der. Beklenti zaten "kaybolsun/değişsin" ise bu, başarının **kanıtıdır**;
        hata sayılması modeli var olmayan bir sorunu düzeltmeye iterdi.
        """
        closing = expectation.kind in (
            ExpectationKind.DISAPPEARS, ExpectationKind.WINDOW_CHANGED,
            ExpectationKind.ANY_CHANGE,
        )
        return VerificationReport(
            action=action, satisfied=closing, expectation=expectation,
            reason=("hedef pencere artık okunamıyor (kapanmış olabilir)"
                    if closing else "eylem sonrası durum okunamadı"),
            attempts=attempts, waited_ms=waited_ms, error=str(exc),
        )

    def _elapsed_ms(self, started: float) -> float:
        return (self._clock() - started) * 1000.0
