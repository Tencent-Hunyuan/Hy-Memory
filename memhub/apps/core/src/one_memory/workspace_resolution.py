"""Name a workspace from the directory a caller is sitting in.

Workspace-scoped memories were reachable only by a caller that could state
the scope string, and the design says exactly that: "调用方必须显式传入
workspace scope" (docs/mvp/2026-09-03-workspace-memory.md). But a scope is
`workspace:v1:<sha256>` over an identity a host chose, so nothing outside
Core could ever produce one. Meanwhile the HY-native routing change made
workspace memories the bulk of the corpus. Measured on this machine's real
database: 417 of 536 confirmed memories carry only a workspace scope, so the
default recall every installed host performs could never return any of them,
and the Skill we install says the opposite — "unless the user explicitly
requests a workspace scope".

This closes that by translating in the only direction that is well defined:
a real directory into the identities the sources would have recorded for it.
The isolation the design asks for is untouched, because a caller can only
ever name the directory it is in; there is no way to ask for another one.

Two families, because the sources genuinely differ:

* Most sources record the directory itself — `cwd` for Claude Code, Qwen
  Work, Hermes, Harness, Pi and WorkBuddy, `directory` for OpenCode,
  `workspace_path` for OpenClaw. Their identity is a hash of the path, so it
  can be computed outright.

* Cursor records a name of its own making: the directory under
  `~/.cursor/projects`, which is the absolute path with every run of
  non-alphanumeric characters folded to `-`. For long paths Cursor truncates
  that and appends a short hash of its own, which cannot be reproduced from
  outside. So Cursor is resolved by looking up the names already seen during
  import, comparing plaintext, rather than by reconstructing a hash. Its
  other store, `~/.cursor/chats`, is keyed by `md5` of the path and needs no
  lookup — 42 evidence rows on this machine come from there.
"""

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass

from one_memory.imports.access import opaque_workspace_id

#: Sources whose adapter stores the working directory verbatim, read off each
#: adapter rather than assumed. Codex's native session header supplies cwd.
PATH_WORKSPACE_SOURCES = (
    "claude-code",
    "codex",
    "deepseek-harness",
    "hermes",
    "openclaw",
    "opencode",
    "pi",
    "qwen-work",
    "qwen-code",
    "workbuddy",
)

#: Cursor's truncated project names end in a short hex hash it computes
#: itself. Only the visible part can be compared.
_TRUNCATED_LABEL = re.compile(r"^(?P<prefix>.+)-[0-9a-f]{7}$")

#: A prefix shorter than this is not specific enough to call a match.
_MINIMUM_PREFIX = 24


@dataclass(frozen=True)
class KnownWorkspace:
    """A workspace some import already recorded."""

    source_id: str
    workspace: str
    project: str | None


def cursor_project_label(workspace_path: str) -> str:
    """Cursor's own name for a directory.

    For example, `/Users/example/example-admin-app/api/reports` is stored as
    `Users-example-example-admin-app-api-reports`, and a path containing
    non-ASCII characters folds them away the same way every other separator
    is folded.
    """
    return re.sub(r"[^A-Za-z0-9]+", "-", workspace_path).strip("-")


def resolve_workspace_scopes(
    workspace_path: str | None,
    known: Iterable[KnownWorkspace] = (),
) -> tuple[str, ...]:
    """Every scope this directory could be known by.

    Returning more than one is expected and safe: they all name the same
    directory, seen by different apps. What is never returned is a scope for
    any other directory.
    """
    if not workspace_path:
        return ()
    normalized = _normalize(workspace_path)
    if normalized is None:
        return ()

    scopes = {
        scope
        for source_id in PATH_WORKSPACE_SOURCES
        if (scope := opaque_workspace_id(source_id, normalized)) is not None
    }
    chat_store = opaque_workspace_id(
        "cursor",
        hashlib.md5(normalized.encode(), usedforsecurity=False).hexdigest(),
    )
    if chat_store is not None:
        scopes.add(chat_store)
    scopes.update(_cursor_project_scopes(normalized, known))
    return tuple(sorted(scopes))


def _normalize(workspace_path: str) -> str | None:
    # This value is an opaque identity that is hashed below, never a filesystem
    # operation. Accept only an already-absolute POSIX path; do not expand `~`,
    # resolve symlinks, stat, open, or join caller-controlled path components.
    if (
        not workspace_path.startswith("/")
        or len(workspace_path) > 4_096
        or any(ord(character) < 32 for character in workspace_path)
    ):
        return None
    return workspace_path


def _cursor_project_scopes(
    normalized: str,
    known: Iterable[KnownWorkspace],
) -> set[str]:
    label = cursor_project_label(normalized)
    if not label:
        return set()

    exact: set[str] = set()
    truncated: dict[str, set[str]] = {}
    for entry in known:
        if entry.source_id != "cursor" or not entry.project:
            continue
        if entry.project == label:
            exact.add(entry.workspace)
            continue
        match = _TRUNCATED_LABEL.match(entry.project)
        if match is None:
            continue
        prefix = match.group("prefix")
        if len(prefix) >= _MINIMUM_PREFIX and label.startswith(prefix):
            truncated.setdefault(prefix, set()).add(entry.workspace)

    if exact:
        return exact
    # Cursor appends its hash precisely because two directories can share a
    # truncated name. Where that has happened the visible part cannot say
    # which one is ours, so nothing is returned — a caller seeing none of its
    # own memories is a smaller failure than one seeing another project's.
    unambiguous = {
        workspace
        for workspaces in truncated.values()
        if len(workspaces) == 1
        for workspace in workspaces
    }
    return unambiguous
