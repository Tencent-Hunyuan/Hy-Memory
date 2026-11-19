# -*- coding: utf-8 -*-
"""Regression: mget() must keep keys whose stored value is None.

get()/exists() distinguish "missing key" from "present with value None", but
mget() filters with `if value is not None`, silently dropping legitimately
stored None values. Falsy values 0, "", [] are correctly kept because the
check is `is not None` — only None itself is lost.
"""

from hy_memory.data.kv_store import KVStore, KVStoreConfig


def test_mget_keeps_none_valued_key():
    store = KVStore(KVStoreConfig(backend="memory", prefix="t:"))
    store.set("a_none", None)
    store.set("b_zero", 0)
    result = store.mget(["a_none", "b_zero"])
    assert "a_none" in result, f"None-valued key was dropped: {result}"
    assert result["a_none"] is None
    assert result["b_zero"] == 0


def test_mget_skips_missing_key():
    store = KVStore(KVStoreConfig(backend="memory", prefix="t:"))
    store.set("present", 1)
    result = store.mget(["present", "absent"])
    assert "present" in result
    assert "absent" not in result
