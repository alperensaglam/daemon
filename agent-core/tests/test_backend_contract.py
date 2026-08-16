"""Arka uçların sözleşmeye uyduğunu yapısal olarak doğrular.

Bu testler modülleri **import etmez**; kaynak dosyaları ``ast`` ile okur.
Böylece Windows arka ucunun eksiksizliği macOS'ta (ve tersi) doğrulanabilir —
comtypes veya pyobjc kurulu olmasa bile.

Yakaladığı hata sınıfı somut: ``list_windows`` ABC'ye eklendiğinde
``WindowsUIAExtractor`` onu uygulamayı unutmuştu. Soyut metodu eksik bir sınıf
import anında değil, **kurulmaya çalışıldığında** patlar — yani hata yalnızca
o platformda, çalışma anında görünürdü.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "agent"

#: (dosya, sinif) -> uygulanmasi gereken metotlar
EXTRACTORS = [
    ("perception/windows_uia.py", "WindowsUIAExtractor"),
    ("perception/macos_ax.py", "MacAXExtractor"),
]
EXECUTORS = [
    ("action/windows_uia.py", "WindowsUIAExecutor"),
    ("action/macos_ax.py", "MacAXExecutor"),
]
KEY_BACKENDS = ["action/keys_win.py", "action/keys_mac.py"]

#: keys arka uclarinin ortak protokolu.
KEY_PROTOCOL = [
    "press_combo", "type_unicode", "click_at", "scroll_wheel",
    "move_cursor", "cursor_position",
]


def _tree(relative: str) -> ast.Module:
    return ast.parse((SRC / relative).read_text(encoding="utf-8"))


def _methods(relative: str, class_name: str) -> set[str]:
    for node in ast.walk(_tree(relative)):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                child.name for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
    raise AssertionError(f"{class_name} sinifi {relative} icinde bulunamadi")


def _abstract_methods(relative: str, class_name: str) -> set[str]:
    """ABC'deki @abstractmethod ile isaretli metot adlari."""
    for node in ast.walk(_tree(relative)):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            found = set()
            for child in node.body:
                if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for decorator in child.decorator_list:
                    name = getattr(decorator, "id", getattr(decorator, "attr", ""))
                    if name == "abstractmethod":
                        found.add(child.name)
            return found
    raise AssertionError(f"{class_name} bulunamadi")


@pytest.mark.parametrize("relative,class_name", EXTRACTORS)
def test_cikaricilar_tum_soyut_metotlari_uygular(relative, class_name):
    required = _abstract_methods("perception/base.py", "UITreeExtractor")
    assert required, "UITreeExtractor'da hic abstractmethod bulunamadi"
    missing = required - _methods(relative, class_name)
    assert not missing, (
        f"{class_name} sunlari uygulamiyor: {sorted(missing)} — "
        "sinif kurulmaya calisildiginda TypeError verir."
    )


@pytest.mark.parametrize("relative,class_name", EXECUTORS)
def test_yurutuculer_tum_soyut_metotlari_uygular(relative, class_name):
    required = _abstract_methods("action/base.py", "ActionExecutor")
    implemented = _methods(relative, class_name) | _methods("action/common.py",
                                                           "BaseExecutor")
    missing = required - implemented
    assert not missing, f"{class_name} sunlari uygulamiyor: {sorted(missing)}"


@pytest.mark.parametrize("relative,class_name", EXECUTORS)
def test_yurutuculer_platform_kancalarini_doldurur(relative, class_name):
    """BaseExecutor'daki iki kanca her arka uctan uygulanmali."""
    methods = _methods(relative, class_name)
    for hook in ("_check_identity", "_ensure_foreground"):
        assert hook in methods, f"{class_name}.{hook} eksik"


@pytest.mark.parametrize("relative", KEY_BACKENDS)
def test_keys_arka_uclari_ayni_protokolu_sunar(relative):
    """Quartz veya windll import etmeden yapisal uygunluk kontrolu."""
    functions = {
        node.name for node in _tree(relative).body
        if isinstance(node, ast.FunctionDef)
    }
    missing = set(KEY_PROTOCOL) - functions
    assert not missing, f"{relative} sunlari sunmuyor: {sorted(missing)}"


@pytest.mark.parametrize("relative", ["vision/capture_win.py", "vision/capture_mac.py"])
def test_capture_arka_uclari_ayni_imzayi_sunar(relative):
    functions = {
        node.name for node in _tree(relative).body
        if isinstance(node, ast.FunctionDef)
    }
    assert "capture_window" in functions


@pytest.mark.parametrize("relative", ["vision/ocr_win.py", "vision/ocr_mac.py"])
def test_ocr_arka_uclari_ayni_imzayi_sunar(relative):
    functions = {
        node.name for node in _tree(relative).body
        if isinstance(node, ast.FunctionDef)
    }
    assert {"ocr_image", "supported_languages"} <= functions
