# -*- coding: utf-8 -*-
"""Regression: _guess_branch must be deterministic regardless of PYTHONHASHSEED.

max(set(f), key=f.count) iterates a set, whose order depends on string hash
randomization. On a tie between two equally-frequent branches the winner varies
across processes, so the same input resolves to different branches and a
precise branch lookup randomly misses.
"""

import os
import subprocess
import sys

from hy_memory.coding.chain_store import HyCodeMem

TIE_TEXT = "branch feat/alpha and branch feat/beta"


def _guess_in_process(seed: str) -> str:
    code = (
        "from hy_memory.coding.chain_store import HyCodeMem; "
        f"print(HyCodeMem._guess_branch(None, {TIE_TEXT!r}))"
    )
    env = os.environ.copy()
    env["PYTHONHASHSEED"] = seed
    env["PYTHONPATH"] = os.getcwd()
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
    )
    return out.stdout.strip()


def test_guess_branch_is_stable_across_hash_seeds():
    results = {_guess_in_process(str(seed)) for seed in range(8)}
    assert len(results) == 1, f"branch guess varied across seeds: {results}"


def test_guess_branch_picks_first_on_tie():
    assert HyCodeMem._guess_branch(None, TIE_TEXT) == "feat/alpha"
