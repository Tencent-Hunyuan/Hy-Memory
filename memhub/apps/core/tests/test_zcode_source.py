"""The ZCode source registration: a card and a recall target, no history yet.

What is worth pinning here: detection answers "installed on this machine"
from the home directory's existence; the transcript predicate names the
rollout streams a future parser would read and nothing else; and the parser
skips every record by name, so a sync run over ZCode files cannot file
bookkeeping rows as conversation.
"""

from pathlib import Path

from one_memory.imports.access import AuthorizedSourceFile
from one_memory.imports.adapters import ScanIssueCode
from one_memory.imports.host_packages import CapabilityState, VerificationLevel
from one_memory.imports.sources.registry import build_source_catalog
from one_memory.imports.sources.zcode import (
    is_zcode_transcript,
    parse_zcode_record,
)


def source_file(relative: str) -> AuthorizedSourceFile:
    return AuthorizedSourceFile(
        source_id="zcode",
        root=Path("/home/someone/.zcode"),
        path=Path("/home/someone/.zcode") / relative,
        relative_path=Path(relative),
        max_bytes=400_000_000,
        file_identity=f"identity:{relative}",
        size=10,
        modified_ns=1,
        changed_ns=1,
    )


def test_catalog_registers_zcode_as_recall_target() -> None:
    catalog = build_source_catalog(home=Path("/home/someone"))

    registration = catalog["zcode"]
    assert registration.descriptor.display_name == "ZCode"
    assert registration.candidate.path == Path("/home/someone/.zcode")
    capabilities = registration.capabilities
    assert capabilities.recall is CapabilityState.IMPLEMENTED
    assert capabilities.installer is CapabilityState.IMPLEMENTED
    # The sync switch has to be available because it also gates the capture
    # hooks' writes; the scanner side reads rollout files and skips every
    # record by name until the record shape is mapped.
    assert capabilities.live_sync is CapabilityState.IMPLEMENTED
    assert capabilities.historical_import is CapabilityState.IMPLEMENTED
    assert capabilities.verification is VerificationLevel.FIXTURE_ONLY


def test_transcript_predicate_names_only_rollout_streams() -> None:
    assert is_zcode_transcript(
        source_file("cli/rollout/model-io-sess_1.jsonl"),
    )
    # Config, logs, and side files live under the same root; none of them is
    # conversation data, and the future parser should never see them.
    assert not is_zcode_transcript(source_file("cli/config.json"))
    assert not is_zcode_transcript(source_file("cli/log/tail.jsonl"))
    assert not is_zcode_transcript(
        source_file("cli/rollout/ledger.jsonl"),
    )


def test_parser_skips_every_record_by_name() -> None:
    skip = parse_zcode_record({"anything": True}, None)  # type: ignore[arg-type]

    assert skip.reason_code is ScanIssueCode.UNKNOWN_RECORD
    assert skip.conversation_id is None
    assert not skip.report_issue
