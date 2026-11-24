"""同一个瞬间必须渲染成同一个字符串，不管传进来的是 datetime 还是 epoch。

调用方两种形式都用：`_episode_timeline` 存 `int(node.memory_at.timestamp())`
（session_episode.py:284、:318），而同一条记忆在 Memories 里以 datetime 传入。
epoch 分支按 UTC 渲染时，UTC+8 的 2026-01-01 00:30 会变成 "2025-12-31 16:30:00
UTC" —— 同一块输出里同一条记忆出现两个日期，跨年时差一天。
"""

from datetime import datetime, timezone

from hy_memory.pipelines._retrieval.memory_format import (
    format_final_content,
    format_memory_time,
)


def test_epoch_and_datetime_render_the_same_instant():
    moment = datetime(2026, 1, 1, 0, 30)
    assert format_memory_time(moment) == format_memory_time(int(moment.timestamp()))


def test_epoch_with_fractional_seconds_renders_the_same_instant():
    moment = datetime(2026, 6, 15, 13, 45, 30, 500000)
    assert format_memory_time(moment) == format_memory_time(moment.timestamp())


def test_utc_datetime_still_says_utc():
    """aware 的 UTC 时间仍要标出 UTC —— 这条不能被上面的修改带走。"""
    utc_moment = datetime(2026, 1, 1, 0, 30, tzinfo=timezone.utc)
    assert format_memory_time(utc_moment) == "2026-01-01 00:30:00 UTC"


def test_capsule_timeline_and_members_agree_on_the_date():
    """同一个 capsule 里，Timeline 与 Memories 的时间必须是同一天。"""
    moment = datetime(2026, 1, 1, 0, 30)
    item = {
        "source": "episode_capsule",
        "_episode_render_mode": "l2_visible",
        "_episode_title": "year boundary",
        "_episode_timeline": [{"memory_at": int(moment.timestamp())}],
        "_capsule_memories": [{"memory_at": moment, "content": "the same moment"}],
        "content": "episode body",
    }

    rendered = format_final_content(item)
    timeline = [line for line in rendered.splitlines() if line.startswith("- 20")]
    when = [line.strip() for line in rendered.splitlines() if line.strip().startswith("When:")]

    assert len(timeline) == 1
    assert len(when) == 1
    assert timeline[0].lstrip("- ").startswith(when[0].removeprefix("When: ").strip())
