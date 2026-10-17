import runpy
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


def evaluate_spec(monkeypatch, layout):
    if layout is None:
        monkeypatch.delenv("ONE_MEMORY_CORE_LAYOUT", raising=False)
    else:
        monkeypatch.setenv("ONE_MEMORY_CORE_LAYOUT", layout)
    hooks = ModuleType("PyInstaller.utils.hooks")
    collected = []

    def collect_all(name):
        collected.append(name)
        return [(f"{name}.data", name)], [(f"{name}.binary", name)], [name]

    hooks.collect_all = collect_all
    monkeypatch.setitem(sys.modules, "PyInstaller.utils.hooks", hooks)
    calls = {}
    analysis = SimpleNamespace(
        pure=["python-modules"],
        scripts=["cli.py"],
        binaries=["native-libraries"],
        datas=["embedding-model-and-package-data"],
    )

    def analyze(*args, **kwargs):
        calls["analysis"] = (args, kwargs)
        return analysis

    def executable(*args, **kwargs):
        calls["exe"] = (args, kwargs)
        return "owned-core-executable"

    def collect(*args, **kwargs):
        calls["collect"] = (args, kwargs)

    runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "one-memory.spec"),
        init_globals={
            "Analysis": analyze,
            "PYZ": lambda pure: pure,
            "EXE": executable,
            "COLLECT": collect,
        },
    )
    return calls, analysis, collected


def test_single_file_layout_remains_available_without_private_preset(monkeypatch):
    calls, analysis, collected = evaluate_spec(monkeypatch, "single-file")
    assert analysis.binaries in calls["exe"][0]
    assert analysis.datas in calls["exe"][0]
    assert "collect" not in calls
    assert collected == ["hy_memory", "chromadb", "kuzu", "onnxruntime", "tokenizers"]


def test_folder_layout_keeps_same_core_and_all_native_dependencies(monkeypatch):
    calls, analysis, collected = evaluate_spec(monkeypatch, "folder")
    assert calls["exe"][1]["exclude_binaries"] is True
    assert calls["exe"][1]["name"] == "one-memory"
    assert analysis.binaries not in calls["exe"][0]
    assert analysis.datas not in calls["exe"][0]
    assert calls["collect"][0] == ("owned-core-executable", analysis.binaries, analysis.datas)
    assert calls["collect"][1]["name"] == "one-memory-runtime"
    assert collected == ["hy_memory", "chromadb", "kuzu", "onnxruntime", "tokenizers"]


def test_unknown_layout_does_not_silently_build_another_package(monkeypatch):
    with pytest.raises(ValueError, match="ONE_MEMORY_CORE_LAYOUT"):
        evaluate_spec(monkeypatch, "unrecognized")


def test_default_distribution_avoids_per_launch_self_extraction(monkeypatch):
    calls, _, _ = evaluate_spec(monkeypatch, None)
    assert calls["exe"][1]["exclude_binaries"] is True
    assert calls["collect"][1]["name"] == "one-memory-runtime"
