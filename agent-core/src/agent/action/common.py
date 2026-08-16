"""Yürütücülerin paylaştığı platformdan bağımsız iskelet.

``WindowsUIAExecutor`` içindeki zamanlama, sonuç kurma, parmak izi karşılaştırma
ve düğüm çözümleme mantığının hiçbiri Windows'a özgü değildi; macOS yürütücüsü
yazılırken kopyalanacaktı. Buraya çıkarıldı.

Platforma bırakılan yalnızca iki kanca var:

* ``_check_identity`` — düğümün hâlâ aynı eleman olduğunu doğrular. Windows
  ``RuntimeId`` karşılaştırır; macOS'ta böyle bir kimlik yoktur, öznitelikler
  yeniden okunur.
* ``_ensure_foreground`` — pencereyi öne getirir ve **geldiğini doğrular**.
"""

from __future__ import annotations

import time
from typing import Any

from ..core.errors import NodeNotFound, StaleNodeError
from ..core.types import ActionResult, Snapshot, UINode
from .base import ActionExecutor


class BaseExecutor(ActionExecutor):
    """Zamanlama, doğrulama ve düğüm çözümleme ortak davranışı."""

    #: Eylemden sonra UI'nin tepki vermesi için beklenen süre.
    SETTLE_DELAY = 0.12

    def __init__(self, extractor: Any = None, verify: bool = True,
                 pruner: Any = None) -> None:
        """
        Args:
            extractor: Eylem sonrası doğrulama için kullanılacak çıkarıcı.
                ``None`` ise ``ui_changed`` hesaplanmaz.
            verify: Eylem sonrası yeniden anlık görüntü alınıp değişim
                kontrol edilsin mi.
            pruner: Doğrulamada kullanılacak budayıcı. Verilmezse
                ``attach_pruner`` ile sonradan bağlanabilir.
        """
        self._extractor = extractor
        self._verify = verify and extractor is not None
        self._pruner = pruner

    def attach_pruner(self, pruner: Any) -> None:
        """Doğrulama adımının kullanacağı budayıcıyı bağlar.

        Bunu yapmak zorunludur, kozmetik değil: ``_changed`` eskiden her
        çağrıda varsayılan ``TreePruner()`` kuruyordu, oysa "önce" parmak izi
        CLI'nin ``--max-nodes`` ile kurduğu budayıcıdan geliyordu. ``--max-nodes
        40`` ile önce ≤40, sonra ≤150 düğüm budanıyor, parmak izleri kaçınılmaz
        olarak farklı çıkıyor ve ``ui_changed`` **her zaman True** dönüyordu —
        yani "eylem işe yaradı mı" sinyali sessizce anlamsızdı.
        """
        self._pruner = pruner

    def set_verify(self, enabled: bool) -> None:
        """Eylem sonrası 'UI değişti mi' kontrolünü açar/kapatır.

        Yürütücünün kendi kontrolü bir **ek** anlık görüntü demektir (bu
        makinede Chrome'da ~114 ms). ``execution.verifier.Verifier``
        devredeyken aynı işi daha ayrıntılı yapar; ikisi birden açık kalırsa
        her eylemde iki kez ağaç çıkarılır. Router bu yüzden yürütücünün
        kontrolünü kapatır.
        """
        self._verify = bool(enabled) and self._extractor is not None

    # ------------------------------------------------------------------ #
    #  Düğüm çözümleme
    # ------------------------------------------------------------------ #

    def _resolve(self, snapshot: Snapshot, node_id: int) -> UINode:
        """node_id'yi düğüme çevirir ve hâlâ geçerli olduğunu doğrular.

        Bayat referans, bu sistemdeki en tehlikeli hata sınıfıdır: UI
        değiştikten sonra ``[@7]`` bambaşka bir düğüm olabilir ve "Kaydet"
        yerine "Sil"e tıklanır. Bu yüzden kimlik doğrulanır ve uyuşmazlıkta
        işlem yapılmaz — yanlış tıklamaktansa hata vermek doğrudur.
        """
        node = snapshot.by_id(node_id)
        if node is None:
            available = [n.node_id for n in snapshot.nodes]
            raise NodeNotFound(
                f"[@{node_id}] bu anlik goruntude yok. "
                f"Gecerli id araligi: 1..{max(available) if available else 0}"
            )
        if node.element is None:
            raise StaleNodeError(f"[@{node_id}] icin canli eleman referansi yok.")

        self._check_identity(node)
        return node

    def _check_identity(self, node: UINode) -> None:
        """Düğümün hâlâ aynı elemana işaret ettiğini doğrular (platforma özgü).

        Raises:
            StaleNodeError: Eleman yok olduysa veya başka bir elemana
                dönüştüyse.
        """
        raise NotImplementedError

    # ------------------------------------------------------------------ #
    #  Ön plan
    # ------------------------------------------------------------------ #

    def _ensure_foreground(self, window_handle: int) -> tuple[bool, str]:
        """Pencereyi öne getirir ve **gerçekten geldiğini doğrular**.

        Dönüş değeri, çağıranın klavye girişi gibi odağa bağımlı işlemleri
        iptal edebilmesi içindir: hedef pencere önde değilse tuşlar
        kullanıcının o an yazdığı başka bir uygulamaya gider.

        Returns:
            ``(on_planda_mi, aciklama)``
        """
        raise NotImplementedError

    def _bring_to_front(self, snapshot: Snapshot) -> None:
        """Piksel/tekerlek yolu için pencereyi öne getirmeye çalışır (zorunlu değil)."""
        if snapshot.window_handle:
            self._ensure_foreground(snapshot.window_handle)

    # ------------------------------------------------------------------ #
    #  Sonuç kurma
    # ------------------------------------------------------------------ #

    @staticmethod
    def _ms(started: float) -> float:
        return (time.perf_counter() - started) * 1000.0

    def _done(self, action: str, method: str, node: UINode | None,
              started: float, before: tuple | None) -> ActionResult:
        result = ActionResult(
            ok=True, action=action, method=method,
            detail=node.describe() if node else "",
            elapsed_ms=self._ms(started),
        )
        result.ui_changed = self._changed(before)
        return result

    def _fingerprint(self, snapshot: Snapshot) -> tuple | None:
        return snapshot.fingerprint() if self._verify else None

    def _changed(self, before: tuple | None) -> bool | None:
        """Eylem gerçekten bir şey değiştirdi mi?

        'Başarılı ama hiçbir şey olmadı' durumu, LLM'in aynı eylemi tekrar
        denemesinin başlıca sebebidir; bunu ölçüp geri bildirmek döngünün
        tıkanmasını önler.

        Karşılaştırma yalnızca **aynı budama yapılandırmasıyla** anlamlıdır;
        bkz. ``attach_pruner``.
        """
        if before is None or self._extractor is None:
            return None
        try:
            from ..perception.pruner import TreePruner  # noqa: PLC0415
            pruner = self._pruner or TreePruner()
            time.sleep(self.SETTLE_DELAY)      # UI'nin tepki vermesi icin
            after = pruner.prune(self._extractor.extract()).fingerprint()
            return after != before
        except Exception:
            return None
