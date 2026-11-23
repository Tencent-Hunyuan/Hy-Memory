"""Keep the package importable on the Python floor declared in pyproject.toml.

CI only runs 3.12, so a PEP 604 union in a function annotation (`str | None`)
passes every job while `pip install hy-memory` on 3.9 dies with
`TypeError: unsupported operand type(s) for |`.  Annotations are evaluated at
definition time unless the module opts into postponed evaluation, so the
import failure happens before any user code runs.
"""

import ast
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "hy_memory"
PYPROJECT = ROOT / "pyproject.toml"


def _declared_floor():
    """Return the minor version in requires-python (e.g. 9 for >=3.9)."""
    text = PYPROJECT.read_text(encoding="utf-8")
    match = re.search(r'^requires-python\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert match, "pyproject.toml has no requires-python"
    version = re.search(r">=\s*3\.(\d+)", match.group(1))
    assert version, f"unexpected requires-python: {match.group(1)}"
    return int(version.group(1))


def _postpones_annotations(tree):
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            if any(alias.name == "annotations" for alias in node.names):
                return True
    return False


def _annotations(tree):
    """Yield every annotation expression that is evaluated when the module loads."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            arguments = [
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
                node.args.vararg,
                node.args.kwarg,
            ]
            for argument in arguments:
                if argument is not None and argument.annotation is not None:
                    yield argument.annotation
            if node.returns is not None:
                yield node.returns
        elif isinstance(node, ast.AnnAssign) and node.annotation is not None:
            yield node.annotation


def _unions(annotation):
    for node in ast.walk(annotation):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            yield node


def test_annotations_are_importable_on_the_declared_floor():
    if _declared_floor() >= 10:
        return
    offenders = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        if _postpones_annotations(tree):
            continue
        for annotation in _annotations(tree):
            for union in _unions(annotation):
                line = source.splitlines()[union.lineno - 1].strip()
                offenders.append(f"{path.relative_to(ROOT)}:{union.lineno}: {line}")
    assert not offenders, (
        "PEP 604 unions are evaluated at import time and break the declared "
        "Python 3.9 floor; use typing.Optional or add "
        "`from __future__ import annotations`:\n" + "\n".join(offenders)
    )
