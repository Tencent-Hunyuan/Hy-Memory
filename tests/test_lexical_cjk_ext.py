# -*- coding: utf-8 -*-
"""Regression: tokenize() must not silently drop CJK Extension-A / compatibility
ideographs. The old _CJK regex only covered the basic block (U+4E00-U+9FFF),
while lemmatize.py already uses a wider pattern; the two drifting ranges made
the lexical rerank channel blind to rare/traditional/variant characters.
"""

from hy_memory.coding.lexical import tokenize, _CJK
from hy_memory.pipelines._retrieval.lemmatize import _CJK_PATTERN


def test_ext_a_ideographs_are_kept():
    # 㐀 U+3400, 㐁 U+3401 (CJK Extension A)
    found = _CJK.findall("㐀㐁测试")
    joined = "".join(found)
    assert "㐀" in joined and "㐁" in joined, (
        f"CJK Extension A chars dropped: {found}"
    )


def test_compatibility_ideographs_are_kept():
    # 豈 U+8C48 is in the basic block; 﫿 U+FAFF is compatibility supplement
    found = "".join(_CJK.findall("﫿测试"))
    assert "﫿" in found, f"compatibility ideograph dropped: {found}"


def test_tokenize_keeps_ext_chars_in_bigram_fallback():
    toks = tokenize("内存不足㐀㐁")
    # With jieba absent the fallback is bigrams; the ext chars must still
    # appear somewhere in the output instead of vanishing entirely.
    assert any("㐀" in t or "㐁" in t for t in toks), (
        f"ext chars vanished from tokens: {toks}"
    )


def test_pattern_matches_lemmatize_coverage():
    sample = "㐀㐁䶿豈﫿测试abc123"
    assert "".join(_CJK.findall(sample)) == "".join(_CJK_PATTERN.findall(sample))
