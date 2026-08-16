"""Agent'a ozel hata tipleri."""

from __future__ import annotations


class AgentError(Exception):
    """Tum agent hatalarinin tabani."""


class BackendUnavailable(AgentError):
    """Platform erisilebilirlik arayuzu baslatilamadi."""


class NoActiveWindow(AgentError):
    """Aktif (foreground) pencere bulunamadi."""


class StaleNodeError(AgentError):
    """Verilen node_id artik gecerli degil.

    Bu hata **bilincli olarak** firlatilir: UI degistikten sonra eski bir id ile
    islem yapmak, tahmin edilemeyen bir elemana tiklamak demektir. Yanlis
    dugume tiklamaktansa hata vermek dogrudur.
    """


class NodeNotFound(AgentError):
    """Istenen node_id mevcut anlik goruntude yok."""


class ActionFailed(AgentError):
    """Eylem tum yollar denenmesine ragmen basarisiz oldu."""


class ActionVerificationError(AgentError):
    """Eylem calisti ama beklenen degisiklik UI'da gozlenmedi.

    Bu hata bir cokme degil, bir **geri bildirim kanalidir**: mesaji LLM
    baglamina girer ve modelin ayni eylemi tekrar denemek yerine baska bir yol
    aramasini saglar (self-healing). ``report`` alani ne beklendigini, ne
    gozlendigini ve onerilen bir sonraki adimi tasir.

    Sessiz basarisizlik bu sistemdeki en pahali hata sinifidir: eylem "ok"
    doner, model dogru dugmeye bastigini sanir ve sonraki tum adimlari yanlis
    bir varsayimin uzerine kurar.
    """

    def __init__(self, message: str, action: str = "", report: object = None) -> None:
        super().__init__(message)
        self.action = action
        self.report = report


class ShellCommandBlocked(AgentError):
    """Kabuk komutu politika geregi calistirilmadi.

    Onay reddinden farklidir: engellenen kalip onay verilse bile calismaz
    (ornegin ``rm -rf /``). Kullaniciya sorulan sey ile hic sorulmayan sey
    ayri tutulur, cunku ikincisi bir tercih degil bir sinirdir.
    """


class ElevationRequired(AgentError):
    """Hedef pencere yukseltilmis (admin) bir surece ait.

    Windows UIPI nedeniyle yukseltilmemis bir sureç, yonetici olarak calisan
    pencereleri suremez. Cozum: agent'i yonetici olarak baslatmak.
    """
