#!/usr/bin/env python
"""One-command targeted verification for the C2 write/DB slice.

This is the entry a Captain or C8 can run before merging the slice. It runs
only the targeted suites owned by this chain, never the whole Core suite, so
it stays fast and does not contend with a running soak.

    .venv/bin/python scripts/verify_c2_slice.py            # targeted tests
    .venv/bin/python scripts/verify_c2_slice.py --load     # + 430k load metrics
    .venv/bin/python scripts/verify_c2_slice.py --soak-summary <file.jsonl>

`--soak-summary` prints the trend of an existing soak sample file instead of
running one, so a long soak started elsewhere can be judged without restart.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TARGETED = (
    "tests/test_memory_server_durable_queue.py",
    "tests/test_memory_server_unified_entry.py",
    "tests/test_memory_write_routes.py",
    "tests/test_memory_write_registry.py",
    "tests/test_memory_tasks.py",
    "tests/test_memory_server_migrations.py",
    "tests/test_memory_server_queue.py",
    "tests/test_write_queue_lease_renewal.py",
    "tests/test_memory_server_job_state.py",
    "tests/test_fault_matrix.py",
    "tests/test_backup_destroy_restore_reconciliation.py",
    "tests/test_backup_endpoint.py",
    "tests/test_library_upgrade.py",
    "tests/test_main_database_access.py",
    "tests/test_database_contention.py",
    "tests/test_main_database_fairness.py",
    "tests/test_soak_probe_effectiveness.py",
    "tests/test_sqlite_semantics_gate.py",
    "tests/test_store_binding.py",
    "tests/test_store_binding_ratchet.py",
    "tests/test_main_database_entry.py",
    "tests/test_extraction_window.py",
    "tests/test_extraction_defer.py",
    "tests/test_active_memory_window.py",
    "tests/test_active_window_rotation.py",
    "tests/test_memory_queue_recency.py",
    "tests/test_continuous_outcome_unknown.py",
    "tests/test_conversation_turn_grouping.py",
    "tests/test_extraction_state.py",
)

LOAD = "tests/test_real_load_metrics.py"


def _run(args: list[str]) -> int:
    started = time.monotonic()
    completed = subprocess.run(args, cwd=ROOT, check=False)
    print(f"--- elapsed {time.monotonic() - started:.1f}s -> exit {completed.returncode}")
    return completed.returncode


def _soak_summary(
    path: Path,
    *,
    require_complete: bool = False,
    exit_code_path: Path | None = None,
) -> int:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        print(f"verdict=invalid (unreadable soak sample file: {error})")
        return 2
    try:
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    except json.JSONDecodeError as error:
        print(f"verdict=invalid (malformed JSONL soak sample: {error})")
        return 2
    if not rows:
        print("verdict=invalid (no soak samples)")
        return 2
    if any(not isinstance(row, dict) for row in rows):
        print("verdict=invalid (malformed soak sample: row is not an object)")
        return 2
    try:
        handles = [row["process"]["file_handles"] for row in rows]
        threads = [row["process"]["threads"] for row in rows]
        pending = [row["queue"].get("memory_write_jobs", {}).get("pending", 0) for row in rows]
        directory = [row["disk"]["directory_bytes"] for row in rows]
        connections = [row["database"].get("open_connections", 0) for row in rows]
        writer_wait_ms_max = max(row["database"]["writer_wait_ms_max"] for row in rows)
        reader_wait_ms_max = max(row["database"]["reader_wait_ms_max"] for row in rows)
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        print(f"verdict=invalid (malformed soak sample: {error})")
        return 2
    invalid = _first_invalid_sample(
        handles=handles,
        threads=threads,
        connections=connections,
    )
    if invalid is not None:
        print(f"verdict=invalid ({invalid})")
        return 2
    try:
        span = rows[-1]["epoch"] - rows[0]["epoch"]
        handle_spread = max(handles) - min(handles)
    except (KeyError, TypeError, ValueError) as error:
        print(f"verdict=invalid (malformed soak sample: {error})")
        return 2
    # Checkout counters prove the sampler observed load in its own process.
    # Older soak files predate these fields; `.get` keeps them readable while
    # making the new self-load runs show a non-zero, growing figure.
    writer_checkouts = [row["database"].get("writer_checkouts") for row in rows]
    print(f"samples={len(rows)} span_s={span:.0f}")
    print(f"file_handles min={min(handles)} max={max(handles)} last={handles[-1]}")
    thread_values = set(threads)
    unknown_threads = sum(1 for value in thread_values if value < 0)
    known_threads = sorted(value for value in thread_values if value >= 0)
    print(
        f"threads={known_threads}"
        + (f" (+{unknown_threads} unknown samples)" if unknown_threads else "")
    )
    if all(value is not None for value in writer_checkouts):
        print(
            "writer_checkouts "
            f"first={writer_checkouts[0]} last={writer_checkouts[-1]} "
            f"delta={writer_checkouts[-1] - writer_checkouts[0]}",
        )
    else:
        print("writer_checkouts absent (split-process soak; wait figures not load-backed)")
    print(f"writer_wait_ms_max={writer_wait_ms_max}")
    print(f"reader_wait_ms_max={reader_wait_ms_max}")
    print(f"pending first={pending[0]} last={pending[-1]} delta={pending[-1] - pending[0]}")
    delta_bytes = directory[-1] - directory[0]
    print(f"directory_bytes first={directory[0]} last={directory[-1]} delta={delta_bytes}")

    # A verdict line so a C8 reviewer reads the conclusion, not just numbers.
    # "No unbounded growth" is the objective's criterion: handles and threads
    # stay bounded, waits stay zero, and the queue/disk figures are allowed to
    # grow because that is the load itself.
    waits_zero = writer_wait_ms_max == 0 and reader_wait_ms_max == 0
    verdict = _soak_verdict(
        span_s=span,
        handle_spread=handle_spread,
        waits_zero=waits_zero,
    )
    if require_complete and exit_code_path is not None:
        if not exit_code_path.is_file():
            print(f"verdict=invalid (missing soak exit code {exit_code_path})")
            return 2
        try:
            soak_exit_code = int(exit_code_path.read_text(encoding="utf-8").strip())
        except ValueError:
            print(f"verdict=invalid (unreadable soak exit code {exit_code_path})")
            return 2
        if soak_exit_code != 0:
            print(f"verdict=invalid (soak process exited {soak_exit_code})")
            return 2
    print(verdict)
    if not require_complete:
        return 0
    if verdict.startswith("verdict=complete-pass"):
        return 0
    return 3


def _first_invalid_sample(
    *,
    handles: list[object],
    threads: list[object],
    connections: list[object],
) -> str | None:
    """The first failed probe value, named for the exit receipt.

    `soak_probe.py` writes `-1` when a process probe could not be read. That is
    an unknown sample, not a healthy zero; a verifier that exits 0 on it turns
    a failed measurement into apparent acceptance.
    """
    for name, values in (
        ("file_handles", handles),
        ("threads", threads),
        ("open_connections", connections),
    ):
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in values
        ):
            return f"{name} has a non-integer or negative sample"
    return None


def _soak_verdict(*, span_s: float, handle_spread: int, waits_zero: bool) -> str:
    """One-line conclusion so a reviewer is not left to read the numbers.

    `incomplete` until the window actually reaches 8h; `complete-pass` only
    when handles stayed bounded and no writer/reader wait occurred;
    `complete-attention` otherwise, naming the figure that needs a look.
    """
    if span_s < 28_800:
        return f"verdict=incomplete (span {span_s:.0f}s < 28800s; not a final 8h judgement)"
    if handle_spread <= 8 and waits_zero:
        return "verdict=complete-pass (8h reached; handles bounded; waits zero)"
    return (
        "verdict=complete-attention "
        f"(8h reached; handle_spread={handle_spread} waits_zero={waits_zero})"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--load", action="store_true", help="also run the 430k load metrics (slow)")
    parser.add_argument(
        "--soak-summary", type=Path, help="print the trend of an existing soak file"
    )
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help="fail unless the soak covers a full 8h and passes its trend checks",
    )
    parser.add_argument(
        "--exit-code-file",
        type=Path,
        help="with --require-complete, the soak process's recorded exit code must be 0",
    )
    parser.add_argument("--markers", default="", help="extra pytest -m expression, e.g. 'slow'")
    args = parser.parse_args(argv)

    if args.soak_summary is not None:
        if args.exit_code_file is not None and not args.require_complete:
            print("--exit-code-file requires --require-complete")
            return 2
        return _soak_summary(
            args.soak_summary,
            require_complete=args.require_complete,
            exit_code_path=args.exit_code_file,
        )

    command = [sys.executable, "-m", "pytest", "-q", *TARGETED]
    if args.markers:
        command += ["-m", args.markers]
    code = _run(command)
    if code != 0:
        return code

    if args.load:
        return _run([sys.executable, "-m", "pytest", "-q", "-m", "slow", "-s", LOAD])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
