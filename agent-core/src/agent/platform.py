"""Platform fabrikası — hangi arka ucun kullanılacağına karar veren tek yer.

Tasarım kuralı: **fabrika, arka ucu import etmeden "hangi arka uç?" sorusunu
cevaplayabilmelidir.** Bu yüzden iki katman var:

* ``resolve_backend`` — saf. Yalnızca modül *adreslerini* döndürür, hiçbir şey
  import etmez. Ne ``comtypes`` ne ``pyobjc`` kurulu olmayan bir makinede bile
  çalışır, dolayısıyla dağıtım mantığı test edilebilir.
* ``load`` / ``create_*`` — asıl importu yapar ve ``ImportError``ı kurulum
  ipucu taşıyan ``BackendUnavailable``a çevirir.

Eskiden ``cli.py`` iki backend sınıfını modül seviyesinde import ediyordu; bu,
paketin macOS'ta hiç import edilememesi demekti. Tek yapı yeri artık burası.
"""

from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .core.errors import BackendUnavailable

if TYPE_CHECKING:                     # çalışma anında HİÇBİR backend importu yok
    from .action.base import ActionExecutor
    from .perception.base import UITreeExtractor


@dataclass(frozen=True, slots=True)
class BackendSpec:
    """Bir platformun bileşen adresleri. Saf veri — hiçbir şey import etmez."""

    platform: str
    extractor: str          # "agent.perception.macos_ax:MacAXExtractor"
    executor: str           # "agent.action.macos_ax:MacAXExecutor"
    keys: str               # "agent.action.keys_mac"
    capture: str            # "agent.vision.capture_mac"
    ocr: str                # "agent.vision.ocr_mac"
    install_hint: str       # BackendUnavailable mesajında gösterilir
    handle_label: str       # "hwnd" | "CGWindowID" — CLI yardım metni için
    api_name: str           # "UIAutomation" | "AXUIElement"


_SPECS: dict[str, BackendSpec] = {
    "win32": BackendSpec(
        platform="win32",
        extractor="agent.perception.windows_uia:WindowsUIAExtractor",
        executor="agent.action.windows_uia:WindowsUIAExecutor",
        keys="agent.action.keys_win",
        capture="agent.vision.capture_win",
        ocr="agent.vision.ocr_win",
        install_hint="pip install -r requirements.txt  (comtypes, pywin32)",
        handle_label="hwnd",
        api_name="UIAutomation",
    ),
    "darwin": BackendSpec(
        platform="darwin",
        extractor="agent.perception.macos_ax:MacAXExtractor",
        executor="agent.action.macos_ax:MacAXExecutor",
        keys="agent.action.keys_mac",
        capture="agent.vision.capture_mac",
        ocr="agent.vision.ocr_mac",
        install_hint="pip install -r requirements.txt  (pyobjc-framework-Quartz, "
                     "pyobjc-framework-ApplicationServices, pyobjc-framework-Cocoa)",
        handle_label="CGWindowID",
        api_name="AXUIElement",
    ),
}

SUPPORTED = tuple(_SPECS)


def current_platform() -> str:
    """``sys.platform`` — testlerin yamalayabilmesi için ayrı bir fonksiyon."""
    return sys.platform


def resolve_backend(platform: str | None = None) -> BackendSpec:
    """Platform adına karşılık gelen arka uç tanımını döndürür. Import etmez.

    Raises:
        BackendUnavailable: Platform desteklenmiyorsa.
    """
    name = platform or current_platform()
    spec = _SPECS.get(name)
    if spec is None:
        supported = ", ".join(f"{p} ({_SPECS[p].api_name})" for p in SUPPORTED)
        raise BackendUnavailable(
            f"Desteklenmeyen platform '{name}'. Desteklenenler: {supported}."
        )
    return spec


def load(target: str) -> Any:
    """``"paket.modul"`` veya ``"paket.modul:Sinif"`` adresini yükler.

    Raises:
        BackendUnavailable: Modül veya öznitelik yoksa. Orijinal hata
            ``__cause__`` olarak korunur.
    """
    module_name, _, attr = target.partition(":")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        hint = ""
        for spec in _SPECS.values():
            if module_name.startswith(spec.keys.rsplit(".", 1)[0]) or module_name in (
                spec.extractor.split(":")[0], spec.executor.split(":")[0],
                spec.keys, spec.capture, spec.ocr,
            ):
                hint = f"\nKurulum: {spec.install_hint}"
                break
        raise BackendUnavailable(
            f"'{module_name}' yüklenemedi: {exc}{hint}"
        ) from exc

    if not attr:
        return module
    try:
        return getattr(module, attr)
    except AttributeError as exc:
        raise BackendUnavailable(
            f"'{module_name}' içinde '{attr}' bulunamadı."
        ) from exc


def create_extractor(platform: str | None = None, **kwargs) -> "UITreeExtractor":
    """Bu platformun ağaç çıkarıcısını kurar.

    Yapıcı da ``BackendUnavailable`` fırlatabilir (COM başlatılamadı, macOS'ta
    Erişilebilirlik izni yok); bu hata olduğu gibi yukarı geçer çünkü mesajı
    zaten kullanıcıya ne yapması gerektiğini söyler.
    """
    return load(resolve_backend(platform).extractor)(**kwargs)


def create_executor(extractor: Any = None, verify: bool = True,
                    platform: str | None = None, **kwargs) -> "ActionExecutor":
    """Bu platformun eylem yürütücüsünü kurar."""
    cls = load(resolve_backend(platform).executor)
    return cls(extractor=extractor, verify=verify, **kwargs)


def create_backend(verify: bool = True, platform: str | None = None,
                   **kwargs) -> tuple["UITreeExtractor", "ActionExecutor"]:
    """Çıkarıcı + yürütücü çiftini birlikte kurar (yürütücü çıkarıcıya bağlanır)."""
    extractor = create_extractor(platform, **kwargs)
    executor = create_executor(extractor=extractor, verify=verify, platform=platform)
    return extractor, executor
