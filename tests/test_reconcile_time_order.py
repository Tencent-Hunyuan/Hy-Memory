"""An older memory must never replace a newer one, whatever the model returns.

Memories are not written in the order they happened: history is imported late,
and a hand-added memory can be written while older history is still being
digested. A memory from 2023 arriving after one from 2025 must not supersede
or overwrite it.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from hy_memory.agent.reconciler import ReconcileOp, build_reconcile_prompt, enforce_time_order

NEWER = datetime(2025, 6, 1, 9, 0, tzinfo=timezone.utc)
OLDER = datetime(2023, 3, 1, 9, 0, tzinfo=timezone.utc)


def _existing(**times):
    return {memory_id: SimpleNamespace(memory_at=at) for memory_id, at in times.items()}


def test_an_older_memory_cannot_supersede_a_newer_one():
    ops = [ReconcileOp(op="SUPERSEDE", memory_id="m1", memory_ids=["m1"],
                       content="The user lives in Shanghai.", owner="user", tags=["home"])]

    kept = enforce_time_order(ops, OLDER.isoformat(), _existing(m1=NEWER))

    assert len(kept) == 1
    assert kept[0].op == "ADD"
    assert kept[0].memory_id is None and kept[0].memory_ids == []
    assert kept[0].content == "The user lives in Shanghai."
    assert kept[0].tags == ["home"]
    assert kept[0].reason.startswith("time_order:")


def test_an_older_memory_cannot_update_a_newer_value():
    ops = [ReconcileOp(op="UPDATE", memory_id="m1", content="merged")]

    kept = enforce_time_order(ops, OLDER, _existing(m1=NEWER))

    assert [op.op for op in kept] == ["ADD"]


def test_a_chain_fold_with_any_newer_member_is_blocked():
    ops = [ReconcileOp(op="SUPERSEDE", memory_id="m2", memory_ids=["m1", "m2"],
                       content="latest")]

    kept = enforce_time_order(
        ops, NEWER - timedelta(days=1), _existing(m1=OLDER, m2=NEWER)
    )

    assert [op.op for op in kept] == ["ADD"]


def test_a_newer_memory_still_supersedes_an_older_one():
    op = ReconcileOp(op="SUPERSEDE", memory_id="m1", memory_ids=["m1"], content="moved")

    assert enforce_time_order([op], NEWER, _existing(m1=OLDER)) == [op]


def test_unknown_times_are_not_compared():
    update = ReconcileOp(op="UPDATE", memory_id="m1", content="x")
    supersede = ReconcileOp(op="SUPERSEDE", memory_id="m2", memory_ids=["m2"], content="y")

    assert enforce_time_order([update], "", _existing(m1=NEWER)) == [update]
    assert enforce_time_order([supersede], OLDER, _existing(m2=None)) == [supersede]


def test_a_contentless_update_against_a_newer_memory_is_dropped():
    # The legacy DELETE mapping: nothing to keep as an ADD.
    ops = [ReconcileOp(op="UPDATE", memory_id="m1", content=None)]

    assert enforce_time_order(ops, OLDER, _existing(m1=NEWER)) == []


def test_stored_naive_times_are_read_as_local_time():
    # MemoryNode.from_dict rebuilds memory_at from epoch seconds as naive local time.
    stored = datetime.fromtimestamp(NEWER.timestamp())
    assert stored.tzinfo is None
    op = ReconcileOp(op="SUPERSEDE", memory_id="m1", memory_ids=["m1"], content="x")

    blocked = enforce_time_order([op], NEWER - timedelta(minutes=1), _existing(m1=stored))
    allowed = enforce_time_order([op], NEWER + timedelta(minutes=1), _existing(m1=stored))

    assert [item.op for item in blocked] == ["ADD"]
    assert allowed == [op]


def test_add_ops_and_unknown_targets_pass_through():
    add = ReconcileOp(op="ADD", content="new")
    stray = ReconcileOp(op="UPDATE", memory_id="not-a-candidate", content="x")

    assert enforce_time_order([add, stray], OLDER, {}) == [add, stray]


def test_both_prompts_state_the_time_order_rule():
    english = build_reconcile_prompt(
        new_memories=["The user lives in Shanghai."],
        new_memories_block="[]",
        existing_memories_block="[]",
        current_date="2026-10-27 10:00",
        few_shot_enabled=False,
        structure_metadata_enabled=False,
    )
    chinese = build_reconcile_prompt(
        new_memories=["用户住在上海。"],
        new_memories_block="[]",
        existing_memories_block="[]",
        current_date="2026-10-27 10:00",
        few_shot_enabled=False,
        structure_metadata_enabled=False,
    )

    assert "# Time order" in english
    assert "An older memory must never replace a newer one" in english
    assert "## 时间规则" in chinese
    assert "更早的 memory 绝不能取代更新的 memory" in chinese
    assert "* memory_at（发生时间）" in chinese
