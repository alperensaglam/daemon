"""pytest kurulumu: src/ yolunu import edilebilir yapar ve platform testlerini eler.

Yazarlar testleri yalnizca ``@pytest.mark.windows`` / ``macos`` / ``gui`` ile
isaretler; elle ``skipif`` yazilmaz. Boylece atlama olcutu tek yerdedir.
"""

import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

_PLATFORM_MARKS = {"windows": "win32", "macos": "darwin"}


def pytest_addoption(parser):
    parser.addoption(
        "--run-gui", action="store_true", default=False,
        help="Gercek pencere ve isletim sistemi izni gerektiren testleri de calistir",
    )


def pytest_collection_modifyitems(config, items):
    run_gui = config.getoption("--run-gui")
    for item in items:
        for mark, platform in _PLATFORM_MARKS.items():
            if mark in item.keywords and sys.platform != platform:
                item.add_marker(pytest.mark.skip(
                    reason=f"yalnizca {platform} uzerinde calisir"
                ))
        if "gui" in item.keywords and not run_gui:
            item.add_marker(pytest.mark.skip(
                reason="gercek pencere/izin ister; --run-gui ile calistirin"
            ))
