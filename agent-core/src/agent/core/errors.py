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


class ElevationRequired(AgentError):
    """Hedef pencere yukseltilmis (admin) bir surece ait.

    Windows UIPI nedeniyle yukseltilmemis bir sureç, yonetici olarak calisan
    pencereleri suremez. Cozum: agent'i yonetici olarak baslatmak.
    """
