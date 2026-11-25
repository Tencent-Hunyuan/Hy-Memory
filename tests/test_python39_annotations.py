"""Keep the declared Python 3.9 floor honest about annotations.

``pyproject.toml`` declares ``requires-python = ">=3.9"``, and ``hy_memory`` is
importable on 3.9. A PEP 604 union in an annotation (``str | None``) is *evaluated*
when the module executes, so on 3.9 the import raises
``TypeError: unsupported operand type(s) for |`` before any user code runs.

A module that opts out with ``from __future__ import annotations`` is exempt:
annotations are then strings and never evaluated.
"""

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "hy_memory"


def _annotation_trees(tree: ast.AST):
    """Yield every annotation expression in a module."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            arguments = [
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
            ]
            for argument in arguments:
                if argument.annotation is not None:
                    yield argument.annotation
            if node.returns is not None:
                yield node.returns
        elif isinstance(node, ast.AnnAssign) and node.annotation is not None:
            yield node.annotation


def _pep604_union(annotation: ast.AST) -> bool:
    return any(
        isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr)
        for node in ast.walk(annotation)
    )


def test_no_evaluated_pep604_union_in_annotations():
    offenders = []
    for path in sorted(PACKAGE.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "from __future__ import annotations" in source:
            continue
        tree = ast.parse(source, filename=str(path))
        for annotation in _annotation_trees(tree):
            if _pep604_union(annotation):
                offenders.append(
                    f"{path.relative_to(PACKAGE.parent).as_posix()}:{annotation.lineno}"
                )

    assert not offenders, (
        "these annotations use `X | Y`, which raises TypeError when the module is "
        "imported on the declared Python 3.9 floor; use typing.Optional instead: "
        + ", ".join(offenders)
    )
