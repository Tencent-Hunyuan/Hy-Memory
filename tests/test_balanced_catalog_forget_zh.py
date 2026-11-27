"""A Chinese forget marker has to suppress the fact it names.

``is_explicit_forget_marker`` already accepts the Chinese wording (``用户要求忘记…``)
and ``extract_forget_targets`` already knows the ``请忘记`` directive, but the
shared full-anchor matcher only tokenizes ASCII words.  A Chinese target
therefore produced no anchors, the loop treated it as "cannot match", and
``suppress_forget_targets`` kept everything: the user asked for a fact to be
forgotten and the catalog still carried it.
"""

from hy_memory.models.memory import MemoryLayer, MemoryNode, MemoryStatus
from hy_memory.pipelines.balanced_catalog import (
    collect_forget_targets,
    extract_forget_targets,
    is_explicit_forget_marker,
    suppress_forget_targets,
    text_matches_forget_targets,
)


def fact(node_id, content):
    return MemoryNode(
        node_id=node_id,
        user_id="u1",
        agent_id="a1",
        layer=MemoryLayer.L2_FACT,
        content=content,
        owner="user",
        status=MemoryStatus.ACTIVE,
        is_latest=True,
        tags=["tag"],
    )


def test_a_chinese_marker_is_detected_and_its_directive_is_stripped():
    marker = "用户要求忘记自己住在杭州。"

    assert is_explicit_forget_marker(marker)
    assert extract_forget_targets(marker) == ["住在杭州"]


def test_the_chinese_target_suppresses_the_named_fact():
    targets = collect_forget_targets([fact("marker", "用户要求忘记自己住在杭州。")])
    entries = [
        {"node_id": "drop", "domain": "identity_relationships",
         "content": "用户住在杭州，喜欢早起。"},
        {"node_id": "keep", "domain": "preferences_collections",
         "content": "用户喜欢喝茶。"},
    ]

    kept, suppressed = suppress_forget_targets(entries, targets)

    assert [row["node_id"] for row in kept] == ["keep"]
    assert [row["node_id"] for row in suppressed] == ["drop"]


def test_a_chinese_target_does_not_match_an_english_text():
    assert not text_matches_forget_targets("The user lives in Hangzhou.", ["住在杭州"])


def test_the_english_path_still_needs_the_full_anchor():
    targets = ["they enjoy iced coffee"]

    assert text_matches_forget_targets("The user enjoys iced coffee.", targets)
    assert not text_matches_forget_targets(
        "The user works in a coffee shop supply company.", targets
    )
