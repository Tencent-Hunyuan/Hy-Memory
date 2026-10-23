"""An instrument must never be able to break the thing it measures.

`diagnostic_event` validates its payload: a malformed `details` key, a missing
`level`, or an unsafe detail value is rejected. A diagnostic payload must never
be able to break the request path or loop heartbeat it is intended to measure.

So this checks the payloads at their source, by walking every literal call.
"""

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent / "src" / "one_memory"
DETAIL_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
REFUSED_DETAIL_KEYS = {"__proto__", "prototype", "constructor"}
EXPORTED_STRING_DETAIL_KEYS = {
    "action",
    "method",
    "mode",
    "phase",
    "platform",
    "reason",
    "source_id",
    "status",
    "version",
    "parent_session_id",
    "runtime_state",
    "core_state",
    "broker_state",
    "instance_id",
    "operation_kind",
    "file_kind",
    "last_probe",
    "signal",
}
PRIVATE_DETAIL_KEYS = {
    "api_key",
    "authorization",
    "content",
    "credential",
    "memory",
    "memory_text",
    "message",
    "messages",
    "password",
    "prompt",
    "query",
    "raw_text",
    "secret",
    "token",
}

#: Every module, not a hand-picked list. The two payloads that caused real
#: outages were in files nobody thought to add here, so the only shape of this
#: check that can actually hold the line is "every module, always".
SOURCES = tuple(
    sorted(str(path.relative_to(ROOT)) for path in ROOT.rglob("*.py") if path.name != "__init__.py")
)


def _literal_calls(path: Path):
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "diagnostic_event":
            yield node


def _contains_free_text_conversion(node: ast.AST) -> bool:
    for item in ast.walk(node):
        if (
            isinstance(item, ast.Call)
            and isinstance(item.func, ast.Name)
            and item.func.id in {"str", "repr", "_safe_message"}
        ):
            return True
        if isinstance(item, ast.Attribute) and item.attr in {"args", "message"}:
            return True
    return False


@pytest.mark.parametrize("relative", SOURCES)
def test_every_diagnostic_payload_is_valid(relative: str) -> None:
    path = ROOT / relative
    assert path.is_file(), path
    problems: list[str] = []
    for node in _literal_calls(path):
        keys = {keyword.arg for keyword in node.keywords}
        if "level" not in keys:
            problems.append(f"{relative}:{node.lineno}: missing level")
        for keyword in node.keywords:
            if keyword.arg != "details" or not isinstance(keyword.value, ast.Dict):
                continue
            for key, expression in zip(keyword.value.keys, keyword.value.values, strict=True):
                if not isinstance(key, ast.Constant):
                    continue
                value = key.value
                if (
                    not isinstance(value, str)
                    or not DETAIL_KEY.fullmatch(value)
                    or value in REFUSED_DETAIL_KEYS
                ):
                    problems.append(f"{relative}:{node.lineno}: invalid details key {value!r}")
                elif value.lower() in PRIVATE_DETAIL_KEYS:
                    problems.append(f"{relative}:{node.lineno}: private details key {value!r}")
                elif value in EXPORTED_STRING_DETAIL_KEYS and _contains_free_text_conversion(
                    expression
                ):
                    problems.append(
                        f"{relative}:{node.lineno}: exported detail {value!r} uses free text"
                    )
    assert not problems, "\n".join(problems)
