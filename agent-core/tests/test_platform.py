"""Platform fabrikası — hangi arka ucun seçildiği ve neyin import EDİLMEDİĞİ.

``resolve_backend`` saf olduğu için bu testler ne comtypes ne pyobjc gerektirir;
her iki işletim sisteminde de koşar.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from agent.core.errors import AgentError, BackendUnavailable
from agent.platform import SUPPORTED, load, resolve_backend


def test_windows_arka_ucu():
    spec = resolve_backend("win32")
    assert spec.extractor == "agent.perception.windows_uia:WindowsUIAExtractor"
    assert spec.executor == "agent.action.windows_uia:WindowsUIAExecutor"
    assert spec.keys == "agent.action.keys_win"
    assert spec.handle_label == "hwnd"


def test_macos_arka_ucu():
    spec = resolve_backend("darwin")
    assert spec.extractor == "agent.perception.macos_ax:MacAXExtractor"
    assert spec.executor == "agent.action.macos_ax:MacAXExecutor"
    assert spec.keys == "agent.action.keys_mac"
    assert spec.handle_label == "CGWindowID"


def test_desteklenmeyen_platform_iki_secenegi_de_adlandirir():
    with pytest.raises(BackendUnavailable) as info:
        resolve_backend("linux")
    message = str(info.value)
    assert "linux" in message
    for platform in SUPPORTED:
        assert platform in message


def test_backend_unavailable_agent_error_turevidir():
    """cli.main()'deki mevcut `except AgentError` bunu yakalamak zorunda."""
    assert issubclass(BackendUnavailable, AgentError)


def test_varsayilan_platform_sys_platform():
    if sys.platform in SUPPORTED:
        assert resolve_backend().platform == sys.platform
    else:
        with pytest.raises(BackendUnavailable):
            resolve_backend()


def test_eksik_modul_kurulum_ipucu_verir():
    with pytest.raises(BackendUnavailable) as info:
        load("agent.perception.olmayan_modul")
    assert "olmayan_modul" in str(info.value)


def test_eksik_oznitelik_yakalanir():
    with pytest.raises(BackendUnavailable):
        load("agent.platform:OlmayanSinif")


# --------------------------------------------------------- import temizligi

def _fresh(code: str) -> subprocess.CompletedProcess:
    """Kodu temiz bir alt süreçte çalıştırır."""
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True,
        env={"PYTHONPATH": _src(), "PATH": "/usr/bin:/bin"},
    )


def _src() -> str:
    from pathlib import Path
    return str(Path(__file__).resolve().parents[1] / "src")


def test_fabrika_backend_import_etmez():
    """'Fabrika arka ucu import etmeden karar verir' iddiasının asıl testi.

    Bu bozulursa paket, platform kütüphaneleri kurulu olmayan makinede yine
    import edilemez hale gelir — yani düzeltilen hatanın ta kendisi geri gelir.
    """
    result = _fresh(
        "import sys, agent.platform\n"
        "agent.platform.resolve_backend('win32')\n"
        "agent.platform.resolve_backend('darwin')\n"
        "kotu = [m for m in ('agent.perception.windows_uia',"
        " 'agent.perception.macos_ax', 'agent.action.windows_uia',"
        " 'agent.action.macos_ax') if m in sys.modules]\n"
        "assert not kotu, kotu\n"
        "print('temiz')\n"
    )
    assert result.returncode == 0, result.stderr
    assert "temiz" in result.stdout


@pytest.mark.parametrize("module", [
    "agent",
    "agent.cli",
    "agent.platform",
    "agent.safety",
    "agent.core.types",
    "agent.core.dpi",
    "agent.core.errors",
    "agent.action.base",
    "agent.action.common",
    "agent.action.keys",
    "agent.action.keynames",
    "agent.action.keycodes_win",
    "agent.action.keycodes_mac",
    "agent.action.keys_win",
    "agent.action.keys_mac",
    "agent.perception.base",
    "agent.perception.pruner",
    "agent.perception.ax_roles",
    "agent.vision.base",
    "agent.vision.fallback",
    "agent.vision.geometry",
    "agent.llm.base",
    "agent.llm.schemas",
    "agent.execution.verifier",
    "agent.execution.shell",
    "agent.execution.router",
    "agent.orchestrator.loop",
])
def test_modul_her_platformda_import_edilebilir(module):
    """Her modül temiz bir süreçte import edilebilmeli.

    Regresyon testi: ``keys.py`` modül seviyesinde ``ctypes.windll``
    kullandığı için macOS'ta TÜM test paketi toplama aşamasında çöküyordu.
    """
    result = _fresh(f"import {module}")
    assert result.returncode == 0, f"{module} import edilemedi:\n{result.stderr}"
