"""The library search window store and the unified record/keyword parsing."""

from one_memory.hy_sdk_types import HySdkMemoryPage, HySdkMemoryRecord, library_keyword_hits
from one_memory.library_search_window import LibrarySearchWindows, window_key


def test_window_is_bound_to_its_filters_and_expires_when_idle():
    now = [0.0]
    windows = LibrarySearchWindows(ttl_seconds=10, clock=lambda: now[0])
    key = window_key("deploy", None, ["active"])
    window = windows.put(key, (), None)

    assert windows.get(window.window_id, key) is window
    assert windows.get(window.window_id, window_key("deploy", "cursor", ["active"])) is None
    now[0] = 9.0
    assert windows.get(window.window_id, key) is window  # reading keeps it alive
    now[0] = 18.0
    assert windows.get(window.window_id, key) is window
    now[0] = 28.5
    assert windows.get(window.window_id, key) is None
    assert len(windows) == 0


def test_window_store_is_bounded_least_recently_read_first():
    windows = LibrarySearchWindows(max_windows=2)
    key = window_key("q", None, ["active"])
    first = windows.put(key, (), None)
    second = windows.put(key, (), None)
    assert windows.get(first.window_id, key) is first
    third = windows.put(key, (), None)
    assert len(windows) == 2
    assert windows.get(second.window_id, key) is None
    assert windows.get(first.window_id, key) is first
    assert windows.get(third.window_id, key) is third


def test_keyword_hits_read_unified_items_in_fused_order():
    raw = {
        "scene": "all",
        "memories": [
            {"memory_id": "c1", "scene": "chat", "kind": "chat", "content": "Deploy on Fridays"},
            {
                "memory_id": "e1",
                "experience_id": "e1",
                "scene": "working",
                "kind": "experience",
                "content": "Deploy service\nUse blue/green",
            },
            {
                "memory_id": "f1",
                "fact_id": "f1",
                "scene": "coding",
                "kind": "fact",
                "content": "Nothing relevant",
                "statement": "Nothing relevant",
            },
            {"memory_id": "c1", "scene": "chat", "kind": "chat", "content": "Deploy on Fridays"},
        ],
    }
    hits, held = library_keyword_hits(raw, "deploy")
    assert held == 3
    assert [(hit.memory_id, hit.kind, hit.variant) for hit in hits] == [
        ("c1", "chat", None),
        ("e1", "experience", "working"),
    ]


def test_productivity_get_becomes_a_unified_record():
    record = HySdkMemoryRecord.from_sdk(
        {
            "memory_id": "e1",
            "kind": "experience",
            "variant": "coding",
            "scene": "productivity",
            "content": "solution only",
            "task": "the task",
            "solution": "solution only",
            "user_id": "local",
            "agent_id": "codebuddy",
            "source": "codebuddy",
            "created_at": "2026-10-01T00:00:00",
            "updated_at": None,
        }
    )
    assert (record.scene, record.kind, record.variant) == ("coding", "experience", "coding")
    assert record.content == "the task\nsolution only"
    assert record.status == "active"
    assert record.created_at == record.updated_at == 1_790_812_800


def test_all_scene_list_page_keeps_totals_and_skips_invalid_rows():
    page, skipped = HySdkMemoryPage.from_sdk_skipping_invalid(
        {
            "scene": "all",
            "memories": [
                {"memory_id": "c1", "content": "chat", "scene": "chat", "kind": "chat"},
                {"memory_id": "bad", "content": "", "scene": "chat", "kind": "chat"},
                {
                    "memory_id": "f1",
                    "kind": "fact",
                    "scene": "working",
                    "content": "fact",
                    "status": "active",
                },
            ],
            "total": 3,
            "totals": {"chat": 2, "working": 1, "coding": 0},
        },
        limit=20,
        offset=0,
    )
    assert skipped == 1
    assert page.total == 3
    assert page.totals == {"chat": 2, "working": 1, "coding": 0}
    assert [(m.memory_id, m.scene, m.kind) for m in page.memories] == [
        ("c1", "chat", "chat"),
        ("f1", "working", "fact"),
    ]
