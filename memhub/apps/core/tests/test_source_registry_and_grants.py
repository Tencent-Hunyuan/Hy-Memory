import os
import re
from pathlib import Path
from typing import get_args

from one_memory.imports.adapters import (
    ScanEvent,
    SourceCapabilities,
    SyncCompleteness,
)
from one_memory.imports.grants import SourceGrantStore
from one_memory.imports.host_packages import (
    CapabilityState,
    VerificationLevel,
)
from one_memory.imports.sources.registry import (
    RootCandidateKind,
    build_source_catalog,
    workbuddy_default_homes,
)


def test_catalog_resolves_defaults_and_marks_environment_overrides(tmp_path) -> None:
    catalog = build_source_catalog(
        home=tmp_path / "home",
        environment={"CODEX_HOME": str(tmp_path / "custom-codex")},
        platform="darwin",
    )

    assert set(catalog) == {
        "workbuddy",
        "cursor",
        "claude-code",
        "codex",
        "pi",
        "qwenwork",
        # Eleventh, not a rename of the tenth: the plan's list names Qwen Work,
        # and Qwen Code is a different product that happened to be the one
        # installed here. Both are read.
        "qwen-code",
        "hermes",
        "deepseek-harness",
        "opencode",
        "openclaw",
        # Twelfth: the ZCode install is its home directory, so the candidate
        # is the root rather than a sessions folder inside it.
        "zcode",
    }
    assert catalog["qwenwork"].candidate.path == tmp_path / "home/.qwenworkcn/projects"
    assert catalog["qwen-code"].candidate.path == tmp_path / "home/.qwen/projects"
    assert catalog["claude-code"].candidate.path == tmp_path / "home/.claude/projects"
    assert catalog["codex"].candidate.path == tmp_path / "custom-codex/sessions"
    assert catalog["codex"].candidate.kind is RootCandidateKind.ENVIRONMENT_OVERRIDE
    assert catalog["cursor"].capabilities.verification is VerificationLevel.REAL_REPLAYED
    assert catalog["openclaw"].capabilities.verification is VerificationLevel.FIXTURE_ONLY
    assert catalog["cursor"].capabilities.live_sync is CapabilityState.IMPLEMENTED


def test_recall_hosts_that_can_append_join_the_live_sync_poller() -> None:
    """Hermes, DeepSeek Harness, OpenCode, OpenClaw and ZCode already append.

    The poller is the same one the first-tier hosts use. Leaving live sync off
    was a declaration, not a missing reader. Qwen Work stays off: it has no
    recall installer, so it is not in this tier. ZCode joined when its rollout
    parser landed: the scanner reads the transcripts the capture hooks were
    already writing beside.
    """

    catalog = build_source_catalog(home=Path("/nonexistent-home"))
    expected = {
        "hermes": VerificationLevel.REAL_REPLAYED,
        "deepseek-harness": VerificationLevel.REAL_REPLAYED,
        "opencode": VerificationLevel.FIXTURE_ONLY,
        "openclaw": VerificationLevel.FIXTURE_ONLY,
        "zcode": VerificationLevel.FIXTURE_ONLY,
    }
    for source_id, verification in expected.items():
        capabilities = catalog[source_id].capabilities
        assert capabilities.historical_import is CapabilityState.IMPLEMENTED
        assert capabilities.incremental_import is CapabilityState.IMPLEMENTED
        assert capabilities.live_sync is CapabilityState.IMPLEMENTED
        assert capabilities.recall is CapabilityState.IMPLEMENTED
        assert capabilities.installer is CapabilityState.IMPLEMENTED
        assert capabilities.verification is verification
    assert catalog["qwenwork"].capabilities.live_sync is CapabilityState.NOT_IMPLEMENTED
    assert catalog["qwenwork"].capabilities.recall is CapabilityState.NOT_IMPLEMENTED


def test_declarations_with_no_reader_are_named_rather_than_mistaken_for_behaviour() -> None:
    """Pin the flags and members that exist but nothing acts on.

    This codebase has twice shipped a claim no code read: every source once
    reported "recall not connected" while three installers worked, and the UI
    called an additions-only feed real-time sync. The pattern is a declaration
    with no reader, which reads as a working feature to everyone except the
    machine. These four are today's remaining instances; each assertion fails
    the moment someone wires one up, which is when the docs and the UI have to
    change too.
    """
    root = Path(__file__).resolve().parents[1] / "src/one_memory"
    modules = {path.relative_to(root).as_posix(): path.read_text() for path in root.rglob("*.py")}

    def readers(name: str, *, defined_in: tuple[str, ...] = ()) -> set[str]:
        """Files mentioning `name` other than where it is defined or assigned."""
        found = set()
        for module, text in modules.items():
            if module in defined_in:
                continue
            uses = [
                line
                for line in text.splitlines()
                if name in line
                # Storing a value into a descriptor is not reading it back.
                and not re.match(rf"\s*{name}\s*[:=]", line.strip())
            ]
            if uses:
                found.add(module)
        return found

    # Deletion resolution exists as a function exercised only by its own test.
    # No adapter can report a deletion, so wiring it would not do anything yet —
    # but its presence reads as though deletion sync ships.
    assert readers("resolve_source_deletions", defined_in=("imports/lifecycle.py",)) == set()

    # Tool calls are stored clipped and redacted by the adapters that know the
    # format; protocol role is authoritative when a write job is assembled.
    assert readers("WrittenBy.TOOL") == {
        "imports/models.py",
        "memory_server/routes.py",
        # The row a record's tool calls become when it also said something.
        "imports/sources/common.py",
    } | {
        f"imports/sources/{name}.py"
        for name in (
            "claude_code",
            "codex",
            "cursor_unified",
            "deepseek_harness",
            "hermes",
            "openclaw",
            "opencode",
            "pi",
            "qwen_code",
            "workbuddy",
        )
    }

    # Five sources declare they can observe edits. Nothing consults the flag,
    # and memories derived from superseded evidence are not retracted (D-16).
    assert readers("can_observe_edits", defined_in=("imports/adapters.py",)) == set()

    # The scan protocol names four phases; file sources emit discover, read and
    # done. EMIT is now a real heartbeat: reading one long file used to report
    # nothing until the file ended, which left the job's visible counts frozen
    # for seconds. It is emitted by the reader, not by a second writer.
    assert readers("ScanPhase.READ") == {
        "imports/sources/common.py",
        "imports/sources/cursor_unified.py",
    }
    assert readers("ScanPhase.EMIT") == {"imports/sources/common.py"}


def test_workbuddy_uses_the_home_that_actually_holds_its_transcripts(tmp_path) -> None:
    """BUG-01: WorkBuddy's import root is the WorkBuddy home, not the CLI's.

    `~/.codebuddy` is the CLI's own config directory. Choosing between the two
    by newest mtime let a stale CLI test session outrank the app's 12 GiB
    corpus and put a folder choice in front of the user that has one right
    answer, so it is no longer a candidate.
    """
    home = tmp_path / "home"
    codebuddy = home / ".codebuddy" / "projects"
    legacy = home / ".workbuddy" / "projects"

    def resolve(environment: dict[str, str] | None = None) -> tuple[Path, RootCandidateKind]:
        candidate = build_source_catalog(home=home, environment=environment or {})[
            "workbuddy"
        ].candidate
        return candidate.path, candidate.kind

    codebuddy.mkdir(parents=True)
    (codebuddy / "session.jsonl").write_text("{}\n", encoding="utf-8")
    legacy.mkdir(parents=True)
    (legacy / "session.jsonl").write_text("{}\n", encoding="utf-8")

    # Transcripts in the CLI's own directory are not an import root, whether or
    # not the WorkBuddy home has any yet.
    assert resolve() == (legacy, RootCandidateKind.DEFAULT)

    # A newer CLI session does not outvote it: mtime says nothing about where
    # the app writes today.
    stale = codebuddy / "old-cli-test.jsonl"
    stale.write_text("{}\n", encoding="utf-8")
    os.utime(stale, (1_900_000_000, 1_900_000_000))
    os.utime(legacy / "session.jsonl", (1_600_000_000, 1_600_000_000))
    assert resolve() == (legacy, RootCandidateKind.DEFAULT)
    assert resolve() == (legacy, RootCandidateKind.DEFAULT)

    configured = tmp_path / "configured"
    assert resolve({"CODEBUDDY_CONFIG_DIR": str(configured)}) == (
        configured / "projects",
        RootCandidateKind.ENVIRONMENT_OVERRIDE,
    )


def test_workbuddy_catalog_selection_does_not_recursively_walk_every_transcript(
    tmp_path, monkeypatch
) -> None:
    """Root choice may inspect the project level, not every historical transcript."""
    home = tmp_path / "home"
    codebuddy, workbuddy = workbuddy_default_homes(home)
    codebuddy.mkdir(parents=True)
    project = workbuddy / "project"
    project.mkdir(parents=True)
    (project / "session.jsonl").write_text("{}\n", encoding="utf-8")

    walked: list[Path] = []
    original_walk = os.walk

    def observed_walk(root, *args, **kwargs):
        walked.append(Path(root))
        return original_walk(root, *args, **kwargs)

    monkeypatch.setattr("one_memory.imports.sources.registry.os.walk", observed_walk)
    candidate = build_source_catalog(home=home, environment={})["workbuddy"].candidate

    assert candidate.path == workbuddy
    assert walked == [], "catalog construction must not walk transcript trees"


def test_pi_is_found_in_whichever_home_actually_holds_its_sessions(tmp_path) -> None:
    """Pi's canonical home is often the empty one, and that failure is silent.

    An embedding host gives Pi a private config home seeded from the standalone
    `~/.pi/agent`, which then keeps only auth and skills. Resolving to the
    canonical path regardless means the scan reads an absent directory, imports
    nothing, and reports no error — the source just looks like it has no history.
    """
    home = tmp_path / "home"
    embedded = home / ".screenpipe/pi-config/sessions"
    embedded.mkdir(parents=True)
    # The standalone home exists but holds no conversations, exactly as it does
    # after an embedder seeds itself from it.
    (home / ".pi/agent/skills").mkdir(parents=True)

    def resolve(environment: dict[str, str]) -> tuple[Path, RootCandidateKind]:
        candidate = build_source_catalog(home=home, environment=environment)["pi"].candidate
        return candidate.path, candidate.kind

    assert resolve({}) == (embedded, RootCandidateKind.DEFAULT)

    # Once the standalone home has sessions of its own it wins: it is where Pi
    # writes when nothing embeds it.
    standalone = home / ".pi/agent/sessions"
    standalone.mkdir()
    assert resolve({}) == (standalone, RootCandidateKind.DEFAULT)

    # An explicit override is a statement of fact and must not be probed away.
    elsewhere = tmp_path / "elsewhere"
    assert resolve({"PI_CODING_AGENT_DIR": str(elsewhere)}) == (
        elsewhere / "sessions",
        RootCandidateKind.ENVIRONMENT_OVERRIDE,
    )


def test_declared_recall_hosts_match_the_installer_that_ships() -> None:
    """Pin the Python declaration to the TypeScript installer's host list.

    The two live in different languages and were maintained by hand, so they
    drifted: every source claimed recall was unavailable while three installers
    worked. Nothing failed, because nothing compared them.
    """
    cli = Path(__file__).resolve().parents[3] / "packages/host-bridge/src/installer-cli.ts"
    declaration = re.search(
        r"const HOSTS = \[(?P<hosts>.*?)\] as const;", cli.read_text(), re.DOTALL
    )
    assert declaration, "installer-cli.ts no longer declares HOSTS; update this pin"
    shipped = set(re.findall(r'"([^"]+)"', declaration.group("hosts")))

    # A host can offer more than one way in without being more than one
    # product. WorkBuddy takes a real `.codebuddy-plugin`, which installs and
    # uninstalls as one unit; `workbuddy-scattered` is the older way of writing
    # the same settings key by key, kept for a machine whose WorkBuddy predates
    # plugin support. Both reach one source, so the alternate spelling is
    # folded away here rather than being invented as an eleventh product.
    alternate_dialects = {"workbuddy-scattered": "workbuddy"}
    for alternate, canonical in alternate_dialects.items():
        if alternate not in shipped:
            continue
        assert canonical in shipped, (
            f"{alternate} is the fallback spelling of {canonical}, "
            "so it cannot be the only one shipped"
        )
        shipped.discard(alternate)

    catalog = build_source_catalog(home=Path("/nonexistent-home"))
    declared = {
        source_id
        for source_id, registration in catalog.items()
        if registration.capabilities.recall is CapabilityState.IMPLEMENTED
    }

    # Every host we can install recall into is now also a source we can read.
    # Qwen Code used to be the exception — recall worked, nothing read its
    # conversations — because it was mistaken for the Qwen Work in the plan.
    # They are separate products (`~/.qwen` from npm `@qwen-code/qwen-code`
    # versus `~/.qwenworkcn` from qwenwork.cn), so it got its own adapter
    # rather than being folded into that one.
    #
    # The reverse asymmetry is real and deliberate: Qwen Work is a source with
    # no recall target, because the host has never been seen on a machine here.
    # It therefore appears in the catalog and not in `HOSTS`, which is why this
    # compares in one direction.
    assert shipped == declared
    assert declared == {
        source_id
        for source_id, registration in catalog.items()
        if registration.capabilities.installer is CapabilityState.IMPLEMENTED
    }


def test_grant_receipt_hides_absolute_path_and_revocation_disables_read(
    tmp_path,
) -> None:
    home = tmp_path / "home"
    source = home / ".claude" / "projects"
    source.mkdir(parents=True)
    store = SourceGrantStore(tmp_path / "one-memory.sqlite3", home=home)

    receipt = store.grant(
        user_id="local",
        source_id="claude-code",
        root=source,
        candidate_kind=RootCandidateKind.DEFAULT,
    )

    assert receipt.root_label == "projects"
    assert str(home) not in receipt.root_id
    assert store.authorized_root(user_id="local", source_id="claude-code")

    revoked = store.revoke(user_id="local", source_id="claude-code")

    assert revoked.revoked_at is not None
    assert store.authorized_root(user_id="local", source_id="claude-code") is None


def test_sync_completeness_is_derived_not_declared() -> None:
    """The label has to follow the booleans, or it becomes a second thing to maintain.

    A source is followable in full if we can either see its deletions or check
    our copy against a complete set. Neither means additions only.
    """

    def capabilities(*, deletes: bool, reconcile: bool) -> SourceCapabilities:
        return SourceCapabilities(
            can_append=True,
            can_observe_edits=False,
            can_observe_deletes=deletes,
            can_reconcile_complete_set=reconcile,
        )

    assert (
        capabilities(deletes=False, reconcile=False).sync_completeness
        is SyncCompleteness.ADDITIONS_ONLY
    )
    assert (
        capabilities(deletes=True, reconcile=False).sync_completeness
        is SyncCompleteness.FULL_DYNAMIC
    )
    assert (
        capabilities(deletes=False, reconcile=True).sync_completeness
        is SyncCompleteness.FULL_DYNAMIC
    )


def test_no_source_claims_deletion_tracking_the_scan_protocol_cannot_report() -> None:
    """A capability claim is only meaningful if the plumbing could carry it.

    `ScanEvent` has no deletion member, so today no adapter could report an
    upstream delete even if its descriptor said otherwise. Claiming it anyway
    would make the product promise the source is being followed in full while
    deleted sentences quietly stay in memory.
    """
    kinds = {
        get_args(member.model_fields["kind"].annotation)[0]
        for member in get_args(ScanEvent.__value__)
    }
    protocol_reports_deletions = any("delet" in kind for kind in kinds)

    catalog = build_source_catalog(home=Path("/nonexistent-home"))
    claiming = {
        source_id
        for source_id, registration in catalog.items()
        if registration.descriptor.capabilities.can_observe_deletes
    }

    assert not claiming or protocol_reports_deletions, (
        f"{sorted(claiming)} 声称能观察源头删除，但 ScanEvent 只有 {sorted(kinds)}，"
        "没有承载删除的事件类型；先实现删除事件再改声明。"
    )


def test_every_source_states_its_live_sync_is_additions_only() -> None:
    """Pin today's honest answer so a future change has to restate it.

    All ten read forward from files or databases and none reconciles, so none
    can follow an upstream deletion. When one gains that ability this test
    fails, which is the reminder to also change what the UI tells the user.
    """
    catalog = build_source_catalog(home=Path("/nonexistent-home"))

    assert {
        source_id: registration.descriptor.capabilities.sync_completeness
        for source_id, registration in catalog.items()
    } == {source_id: SyncCompleteness.ADDITIONS_ONLY for source_id in catalog}


def test_exports_are_snapshots_without_changing_native_source_capabilities(tmp_path):
    catalog = build_source_catalog(home=tmp_path, import_root=tmp_path / "imports")
    exports = {key: value for key, value in catalog.items() if key.endswith("-export")}
    assert set(exports) == {
        "chatgpt-export",
        "claude-export",
        "generic-export",
        "memory-content-export",
    }
    for source in exports.values():
        assert source.descriptor.capabilities.sync_completeness.value == "one_time_snapshot"
        assert not source.descriptor.capabilities.can_append
    native = {key: value for key, value in catalog.items() if key not in exports}
    assert len(native) == 12
    assert all(
        source.descriptor.capabilities.sync_completeness.value == "additions_only"
        for source in native.values()
    )


def _grant(
    store: SourceGrantStore,
    source_id: str,
    root: Path,
    kind: RootCandidateKind,
):
    return store.grant(user_id="local", source_id=source_id, root=root, candidate_kind=kind)


def test_a_workbuddy_grant_follows_the_app_to_its_other_default_home(tmp_path) -> None:
    home = tmp_path / "home"
    codebuddy, workbuddy = workbuddy_default_homes(home)
    codebuddy.mkdir(parents=True)
    workbuddy.mkdir(parents=True)
    store = SourceGrantStore(tmp_path / "one-memory.sqlite3", home=home)
    _grant(store, "workbuddy", codebuddy, RootCandidateKind.DEFAULT)

    moved = store.follow_default_home(
        user_id="local",
        source_id="workbuddy",
        root=workbuddy,
        default_homes=(codebuddy, workbuddy),
    )

    assert moved is not None
    assert store.authorized_root(user_id="local", source_id="workbuddy").root == workbuddy.resolve()


def test_a_folder_the_user_picked_is_never_moved(tmp_path) -> None:
    home = tmp_path / "home"
    codebuddy, workbuddy = workbuddy_default_homes(home)
    picked = tmp_path / "picked"
    for folder in (codebuddy, workbuddy, picked):
        folder.mkdir(parents=True)
    store = SourceGrantStore(tmp_path / "one-memory.sqlite3", home=home)
    _grant(store, "workbuddy", picked, RootCandidateKind.USER_SELECTED)

    assert (
        store.follow_default_home(
            user_id="local",
            source_id="workbuddy",
            root=workbuddy,
            default_homes=(codebuddy, workbuddy),
        )
        is None
    )
    assert store.authorized_root(user_id="local", source_id="workbuddy").root == picked.resolve()


def test_a_paused_source_with_a_grant_does_not_break_active_adapter_assembly(
    tmp_path,
) -> None:
    """C2 BLOCK c2-to-c0-openclaw-paused-startup: a paused source that already
    has a real grant must be skipped during startup, not raise out of
    `_active_adapters` and take the whole Core down.

    The observed H89 failure is
    `api.py lifespan -> _active_adapters -> create_openclaw_adapter ->
    SourceAccessError(grant_required)`: the loop guarded only `authorized_root`,
    so `create_adapter`'s own refusal escaped.
    """
    from one_memory.api import _active_adapters

    home = tmp_path / "home"
    cursor_home = home / ".cursor"
    openclaw_home = home / ".openclaw"
    cursor_home.mkdir(parents=True)
    openclaw_home.mkdir(parents=True)

    catalog = build_source_catalog(home=home, environment={}, platform="darwin")
    # Use the product's real registrations: openclaw's own factory is the thing
    # that raises, and cursor is a real source that must still be assembled, so
    # a blanket `except: continue` (which would swallow everything) is caught.
    selected = {
        "cursor": catalog["cursor"],
        "openclaw": catalog["openclaw"],
    }

    store = SourceGrantStore(tmp_path / "one-memory.sqlite3", home=home)
    _grant(store, "cursor", cursor_home, RootCandidateKind.DEFAULT)
    _grant(store, "openclaw", openclaw_home, RootCandidateKind.DEFAULT)

    # Precondition: the paused source really does hold a live grant, so the
    # failure path is `create_adapter`, not a missing grant.
    assert store.authorized_root(user_id="local", source_id="openclaw") is not None

    adapters = _active_adapters(selected, store)

    assert "openclaw" not in adapters
    assert "cursor" in adapters
