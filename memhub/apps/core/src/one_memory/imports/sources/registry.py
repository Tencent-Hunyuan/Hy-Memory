import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from one_memory.imports.access import AuthorizedSourceRoot
from one_memory.imports.adapters import SourceAdapter, SourceDescriptor
from one_memory.imports.host_packages import (
    HostPackageCapabilities,
    VerificationLevel,
    historical_only_capabilities,
    polling_live_sync_capabilities,
    with_recall_install_target,
)
from one_memory.imports.sources.claude_code import (
    CLAUDE_CODE_DESCRIPTOR,
    create_claude_code_adapter,
)
from one_memory.imports.sources.codex import CODEX_DESCRIPTOR, create_codex_adapter
from one_memory.imports.sources.cursor_unified import (
    CURSOR_UNIFIED_DESCRIPTOR,
    CursorUnifiedSourceAdapter,
)
from one_memory.imports.sources.deepseek_harness import (
    DEEPSEEK_HARNESS_DESCRIPTOR,
    create_deepseek_harness_adapter,
)
from one_memory.imports.sources.exported import EXPORT_DESCRIPTORS, create_export_adapter
from one_memory.imports.sources.hermes import (
    HERMES_DESCRIPTOR,
    create_hermes_adapter,
)
from one_memory.imports.sources.openclaw import (
    OPENCLAW_DESCRIPTOR,
    create_openclaw_adapter,
)
from one_memory.imports.sources.opencode import (
    OPENCODE_DESCRIPTOR,
    create_opencode_adapter,
)
from one_memory.imports.sources.pi import PI_DESCRIPTOR, create_pi_adapter
from one_memory.imports.sources.qwen_code import (
    QWEN_CODE_DESCRIPTOR,
    create_qwen_code_adapter,
)
from one_memory.imports.sources.qwenwork import (
    QWENWORK_DESCRIPTOR,
    create_qwenwork_adapter,
)
from one_memory.imports.sources.workbuddy import (
    WORKBUDDY_DESCRIPTOR,
    create_workbuddy_adapter,
)
from one_memory.imports.sources.zcode import (
    ZCODE_DESCRIPTOR,
    create_zcode_adapter,
)


class RootCandidateKind(StrEnum):
    DEFAULT = "default"
    ENVIRONMENT_OVERRIDE = "environment_override"
    USER_SELECTED = "user_selected"


@dataclass(frozen=True)
class SourceRootCandidate:
    path: Path
    kind: RootCandidateKind
    revision: int = 0


@dataclass(frozen=True)
class SourceRegistration:
    descriptor: SourceDescriptor
    candidate: SourceRootCandidate
    create_adapter: Callable[[AuthorizedSourceRoot], SourceAdapter]
    capabilities: HostPackageCapabilities = field(
        default_factory=lambda: historical_only_capabilities(
            verification=VerificationLevel.FIXTURE_ONLY
        )
    )


def build_source_catalog(
    *,
    home: Path,
    environment: Mapping[str, str] | None = None,
    platform: str | None = None,
    import_root: Path | None = None,
) -> dict[str, SourceRegistration]:
    current_environment = environment if environment is not None else os.environ
    current_platform = platform or sys.platform
    registrations: tuple[SourceRegistration, ...] = (
        SourceRegistration(
            descriptor=WORKBUDDY_DESCRIPTOR,
            candidate=_workbuddy_projects_root(
                home=home,
                environment=current_environment,
            ),
            create_adapter=create_workbuddy_adapter,
            capabilities=with_recall_install_target(
                polling_live_sync_capabilities(verification=VerificationLevel.REAL_REPLAYED)
            ),
        ),
        SourceRegistration(
            descriptor=CURSOR_UNIFIED_DESCRIPTOR,
            candidate=SourceRootCandidate(
                path=_cursor_data_root(
                    home=home,
                    environment=current_environment,
                    platform=current_platform,
                ),
                kind=RootCandidateKind.DEFAULT,
            ),
            create_adapter=lambda grant: CursorUnifiedSourceAdapter(grant=grant),
            capabilities=with_recall_install_target(
                polling_live_sync_capabilities(verification=VerificationLevel.REAL_REPLAYED)
            ),
        ),
        SourceRegistration(
            descriptor=CLAUDE_CODE_DESCRIPTOR,
            candidate=_configured_child(
                home=home,
                environment=current_environment,
                variables=("CLAUDE_CONFIG_DIR",),
                default_home=".claude",
                child="projects",
                translated_homes=(".tclaude", ".tcodex"),
            ),
            create_adapter=create_claude_code_adapter,
            capabilities=with_recall_install_target(
                polling_live_sync_capabilities(verification=VerificationLevel.REAL_REPLAYED)
            ),
        ),
        SourceRegistration(
            descriptor=CODEX_DESCRIPTOR,
            candidate=_configured_child(
                home=home,
                environment=current_environment,
                variables=("CODEX_HOME",),
                default_home=".codex",
                child="sessions",
                translated_homes=(".tcodex", ".tclaude"),
            ),
            create_adapter=create_codex_adapter,
            capabilities=with_recall_install_target(
                polling_live_sync_capabilities(verification=VerificationLevel.REAL_REPLAYED)
            ),
        ),
        SourceRegistration(
            descriptor=PI_DESCRIPTOR,
            candidate=_pi_sessions_root(
                home=home,
                environment=current_environment,
            ),
            create_adapter=create_pi_adapter,
            # Recall reaches Pi through a skill that runs a command, not an MCP
            # server: it has no MCP config to install into. Declared the same
            # way regardless, because what a source card answers is whether
            # recall can be installed here, not by which mechanism.
            capabilities=with_recall_install_target(
                polling_live_sync_capabilities(verification=VerificationLevel.REAL_REPLAYED)
            ),
        ),
        SourceRegistration(
            descriptor=QWENWORK_DESCRIPTOR,
            candidate=_configured_child(
                home=home,
                environment=current_environment,
                variables=("QWENWORK_CONFIG_DIR",),
                default_home=".qwenworkcn",
                child="projects",
            ),
            create_adapter=create_qwenwork_adapter,
            # No recall target yet, and this is not an oversight to be filled in
            # by analogy. Qwen Work is a desktop application behind a DingTalk
            # login: it is not on this machine, so its extension point has never
            # been seen. WorkBuddy's and Pi's layouts were each read off the
            # installed host, and guessing `~/.qwenworkcn/skills` from the
            # pattern would be the one thing this file is not allowed to do.
        ),
        SourceRegistration(
            descriptor=QWEN_CODE_DESCRIPTOR,
            candidate=_configured_child(
                home=home,
                environment=current_environment,
                # `QWEN_HOME` is the host's own override for `~/.qwen`, read
                # from `Storage.getGlobalQwenDir` in the installed bundle.
                variables=("QWEN_HOME",),
                default_home=".qwen",
                child="projects",
            ),
            create_adapter=create_qwen_code_adapter,
            # A separate product from Qwen Work, not another entry point to it:
            # different vendor directory (`~/.qwen` vs `~/.qwenworkcn`),
            # different distribution (npm `@qwen-code/qwen-code` vs
            # qwenwork.cn), different transcript layout. Recall has been
            # verified against it since 0.23.0, which reported our server
            # Connected. Import is fixture-verified only: this machine has run
            # `qwen mcp` but never a session, so `projects/` does not exist.
            capabilities=with_recall_install_target(
                polling_live_sync_capabilities(verification=VerificationLevel.FIXTURE_ONLY)
            ),
        ),
        SourceRegistration(
            descriptor=HERMES_DESCRIPTOR,
            candidate=_configured_root(
                home=home,
                environment=current_environment,
                variables=("HERMES_HOME",),
                default_home=".hermes",
            ),
            create_adapter=create_hermes_adapter,
            # Hermes is a recall target: v0.21.0 verified our server itself at
            # install time — it refuses to save a server it cannot reach, and
            # reported `om_recall` discovered over a live handshake.
            # New transcripts use the same append cursor and the shared poller.
            capabilities=with_recall_install_target(
                polling_live_sync_capabilities(verification=VerificationLevel.REAL_REPLAYED)
            ),
        ),
        SourceRegistration(
            descriptor=DEEPSEEK_HARNESS_DESCRIPTOR,
            candidate=_configured_child(
                home=home,
                environment=current_environment,
                variables=("DSH_HOME",),
                default_home=".dsh",
                child="sessions",
            ),
            create_adapter=create_deepseek_harness_adapter,
            # dsh 0.1.2-rc.1 installed on this machine, session produced by it
            # and replayed through this adapter: one person turn, verbatim
            # intact, workspace resolved. Its two machine-written `user/message`
            # records (a runtime-context snapshot and a skill catalogue) were
            # excluded, which is what the `source.kind` gate was guessing at
            # before a real session existed to check it against.
            #
            # Recall target since the patch-list installer landed: dsh's own
            # loader composed the row we write into its plugin tree
            # (`dsh --dump-config`, exit 0, no diagnostics), and uninstalling
            # returned the profile's patch file byte for byte. Whether the
            # bridged tool answers in a live session is still unverified — dsh
            # validates a model credential before it reaches MCP startup, and
            # this machine has none that works.
            # New transcripts use the same append cursor and the shared poller.
            capabilities=with_recall_install_target(
                polling_live_sync_capabilities(verification=VerificationLevel.REAL_REPLAYED)
            ),
        ),
        SourceRegistration(
            descriptor=OPENCODE_DESCRIPTOR,
            candidate=_opencode_data_root(
                home=home,
                environment=current_environment,
            ),
            create_adapter=create_opencode_adapter,
            # OpenCode is a recall target: verified on 1.18.29, which completed
            # an MCP handshake with our server. Import stays fixture-verified —
            # no real OpenCode conversation has been replayed through it yet.
            # New rows use the same append cursor and the shared poller.
            capabilities=with_recall_install_target(
                polling_live_sync_capabilities(verification=VerificationLevel.FIXTURE_ONLY)
            ),
        ),
        SourceRegistration(
            descriptor=OPENCLAW_DESCRIPTOR,
            # Recall verified on openclaw 2026.9.1, which wrote this dialect
            # itself and reported our server as stdio. Import stays
            # fixture-verified: no real OpenClaw session has been replayed.
            # New rows use the same append cursor and the shared poller.
            candidate=_configured_root(
                home=home,
                environment=current_environment,
                variables=("OPENCLAW_STATE_DIR",),
                default_home=".openclaw",
            ),
            create_adapter=create_openclaw_adapter,
            capabilities=with_recall_install_target(
                polling_live_sync_capabilities(verification=VerificationLevel.FIXTURE_ONLY)
            ),
        ),
        SourceRegistration(
            descriptor=ZCODE_DESCRIPTOR,
            # ZCode's home directory is the whole install — config, skills,
            # and the rollout transcripts all live under ~/.zcode — so its
            # existence is what "installed here" means.
            candidate=SourceRootCandidate(
                path=home / ".zcode",
                kind=RootCandidateKind.DEFAULT,
            ),
            create_adapter=create_zcode_adapter,
            # Same shape as the other scanner-backed hosts: history and
            # incremental import read the rollout transcripts, live sync is
            # the capture hooks writing each turn as it happens (the sync
            # flag also gates those writes, which is why it exists), and the
            # recall installer ships in this build.
            capabilities=with_recall_install_target(
                polling_live_sync_capabilities(verification=VerificationLevel.FIXTURE_ONLY)
            ),
        ),
    )
    if import_root is not None:
        registrations += tuple(
            SourceRegistration(
                descriptor=descriptor,
                candidate=SourceRootCandidate(
                    path=import_root / descriptor.source_id, kind=RootCandidateKind.DEFAULT
                ),
                create_adapter=create_export_adapter,
                capabilities=historical_only_capabilities(
                    verification=VerificationLevel.FIXTURE_ONLY
                ),
            )
            for descriptor in EXPORT_DESCRIPTORS
        )
    return {registration.descriptor.source_id: registration for registration in registrations}


def _workbuddy_projects_root(
    *,
    home: Path,
    environment: Mapping[str, str],
) -> SourceRootCandidate:
    configured = next(
        (
            environment[variable].strip()
            for variable in ("WORKBUDDY_CONFIG_DIR", "CODEBUDDY_CONFIG_DIR")
            if environment.get(variable, "").strip()
        ),
        None,
    )
    if configured:
        return SourceRootCandidate(
            path=Path(configured).expanduser() / "projects",
            kind=RootCandidateKind.ENVIRONMENT_OVERRIDE,
        )
    # BUG-01: WorkBuddy's import root is `~/.workbuddy/projects`, full stop.
    # `~/.codebuddy` is the CLI's own config directory and is not a candidate:
    # picking between the two by newest mtime let a leftover CLI test session
    # outrank the app's daily writes, and made the source card offer users a
    # folder choice that has one right answer.
    return SourceRootCandidate(
        path=workbuddy_default_homes(home)[-1], kind=RootCandidateKind.DEFAULT
    )


def workbuddy_default_homes(home: Path) -> tuple[Path, Path]:
    return (home / ".codebuddy" / "projects", home / ".workbuddy" / "projects")


def _newest_suffix_mtime(root: Path, suffix: str) -> float | None:
    """Latest mtime at the transcript level, without walking every historical file.

    WorkBuddy writes one project directory per working directory, with its
    transcripts directly inside. A full recursive walk over the real 12 GiB
    tree takes long enough to freeze the loop while the catalog is built, so
    inspect only the root and its immediate project directories. That is also
    the layout the importer uses after a root is selected.
    """
    if not root.is_dir():
        return None
    newest: float | None = None
    for directory in (root, *sorted(child for child in root.iterdir() if child.is_dir())):
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if entry.name.lower().endswith(suffix) and entry.is_file(follow_symlinks=False):
                        modified = entry.stat(follow_symlinks=False).st_mtime
                        newest = modified if newest is None else max(newest, modified)
        except OSError:
            continue
    return newest


def _configured_child(
    *,
    home: Path,
    environment: Mapping[str, str],
    variables: tuple[str, ...],
    default_home: str,
    child: str,
    translated_homes: tuple[str, ...] = (),
) -> SourceRootCandidate:
    configured = next(
        (
            environment[variable].strip()
            for variable in variables
            if environment.get(variable, "").strip()
        ),
        None,
    )
    if configured:
        return SourceRootCandidate(
            path=Path(configured).expanduser() / child,
            kind=RootCandidateKind.ENVIRONMENT_OVERRIDE,
        )
    return SourceRootCandidate(
        path=_default_or_translated_child(home, default_home, child, translated_homes),
        kind=RootCandidateKind.DEFAULT,
    )


def _default_or_translated_child(
    home: Path,
    default_home: str,
    child: str,
    translated_homes: tuple[str, ...],
) -> Path:
    """The agreed root, unless it is empty and a translated home has sessions.

    A host installed under a translated home directory keeps its history there
    while `~/.codex` / `~/.claude` exist and hold nothing. Pointing at the
    empty agreed root imports zero rows and reports no error at all, which
    reads as "this source has no history" rather than "we looked in the wrong
    folder".

    The order is the declared one, never mtime: which directory was touched
    last says nothing about where the transcripts are, and on a machine where
    both hold history the agreed root must keep winning.
    """
    agreed = home / default_home / child
    if agreed.is_dir() and any(agreed.iterdir()):
        return agreed
    for name in translated_homes:
        candidate = home / name / child
        if candidate.is_dir() and any(candidate.iterdir()):
            return candidate
    return agreed


def _configured_root(
    *,
    home: Path,
    environment: Mapping[str, str],
    variables: tuple[str, ...],
    default_home: str,
) -> SourceRootCandidate:
    configured = next(
        (
            environment[variable].strip()
            for variable in variables
            if environment.get(variable, "").strip()
        ),
        None,
    )
    return SourceRootCandidate(
        path=Path(configured).expanduser() if configured else home / default_home,
        kind=(RootCandidateKind.ENVIRONMENT_OVERRIDE if configured else RootCandidateKind.DEFAULT),
    )


def _pi_sessions_root(
    *,
    home: Path,
    environment: Mapping[str, str],
) -> SourceRootCandidate:
    """Find the Pi home that actually holds sessions, not just the canonical one.

    Pi runs both standalone out of `~/.pi/agent` and embedded in a host that gives
    it a private config home seeded from the standalone one — screenpipe's
    `~/.screenpipe/pi-config` records exactly that in `.migrated-from-global`.
    Only one of them usually has conversations, and pointing at the empty one
    imports nothing while reporting no error at all, so probe for the directory
    that exists instead of assuming the standalone layout.
    """
    configured = environment.get("PI_CODING_AGENT_DIR", "").strip()
    if configured:
        return SourceRootCandidate(
            path=Path(configured).expanduser() / "sessions",
            kind=RootCandidateKind.ENVIRONMENT_OVERRIDE,
        )
    candidates = (
        home / ".pi" / "agent" / "sessions",
        home / ".screenpipe" / "pi-config" / "sessions",
    )
    existing = next((path for path in candidates if path.is_dir()), None)
    return SourceRootCandidate(
        path=existing if existing is not None else candidates[0],
        kind=RootCandidateKind.DEFAULT,
    )


def _opencode_data_root(
    *,
    home: Path,
    environment: Mapping[str, str],
) -> SourceRootCandidate:
    configured = environment.get("OPENCODE_DATA_DIR", "").strip()
    if configured:
        return SourceRootCandidate(
            path=Path(configured).expanduser(),
            kind=RootCandidateKind.ENVIRONMENT_OVERRIDE,
        )
    xdg_data = environment.get("XDG_DATA_HOME", "").strip()
    return SourceRootCandidate(
        path=(Path(xdg_data).expanduser() if xdg_data else home / ".local/share") / "opencode",
        kind=(RootCandidateKind.ENVIRONMENT_OVERRIDE if xdg_data else RootCandidateKind.DEFAULT),
    )


def _cursor_data_root(
    *,
    home: Path,
    environment: Mapping[str, str],
    platform: str,
) -> Path:
    modern_root = home / ".cursor"
    if modern_root.exists():
        return modern_root
    # Legacy layout only. The platform branches live in one place —
    # `cursor_default_root` — so Windows cannot be fixed here and forgotten there.
    from one_memory.imports.sources.cursor import cursor_default_root

    return cursor_default_root(home, platform=platform, environment=environment)
