"""事实层的词面通道：BM25 + 分数融合，给 store.search_facts 做重排。

为什么要加这一路：statement 是唯一进检索向量的字段（store._upsert_fact_vec 只嵌
`subject: statement`），而它记的大量内容是给定量——符号名、数值、单位、标签。稠密
向量对这类 token 很钝："step 3 input: n=64" 和 "step 4 input: n=32" 的余弦几乎一样，
但查询要的是其中一条。词面通道对精确 token 敏感，正好补这个洞。

只有候选数超过 top-k 时重排才会改变结果；候选不足时该通道自然是空操作。
词面权重应足以打破语义分接近的邻居，但不能盖过明显更相关的语义候选。

两个设计约束，都是被调用方逼出来的：

  1. 绝不能把融合分数返回给上层。client.py:851 用 `s >= fact_min_score`
     （默认 0.30）过闸门，那是余弦分的量纲；融合分是另一套量纲，一返回就等于
     把整个事实层静默清空。所以融合只用来定顺序，返回的分数仍是稠密余弦分。

  2. 重排只在"已经过了稠密下限"的候选里做。否则 BM25 可能把一条余弦 0.2 的事实顶进
     前 top，它随后被 client 的闸门丢掉，于是混检反而比纯稠密召回得更少。先按
     min_score 砍掉候选池，再重排，返回条数就恒等于纯稠密的条数，只是选得更准。
"""
from __future__ import annotations

import logging
import os
import re
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

ENV_DISABLE = "HY_FACT_LEXICAL_OFF"   # 置 1 关掉词面通道，退回纯稠密
ENV_WEIGHT = "HY_FACT_LEXICAL_WEIGHT"

# 词面相对稠密的权重，含义是"词面最多能翻过多大的余弦间距"。
_LEX_WEIGHT = 0.05

# 数值必须整体成词，包括科学计数法：给定量的辨识度全在完整数值上，"1e-6" 被切成
# 1/e/6 之后和 "6e-1" 无从区分。数值分支放在字母分支前面，否则 "1e-6" 的 e 会先被
# 字母分支吃掉。
_LATIN = re.compile(r"\d+(?:\.\d+)?(?:e[-+]?\d+)?|[a-z]+")
# Cover the whole CJK range used elsewhere in the retrieval stack
# (see pipelines/_retrieval/lemmatize.py::_CJK_PATTERN): basic block,
# Extension A, and compatibility ideographs. The old basic-only range silently
# dropped rare / traditional / variant characters from the lexical index.
_CJK = re.compile(r"[一-鿿㐀-䶿豈-﫿]+")

_warned = False


def _warn_once(msg: str) -> None:
    global _warned
    if not _warned:
        _warned = True
        logger.warning(f"[coding-lexical] {msg}")


def tokenize(text: str) -> List[str]:
    """中英混排分词。

    先用正则取拉丁词和数值（数值整体成词，见 _LATIN 上的注释），CJK 段落交给 jieba。
    jieba 首次调用要加载 0.4s 词典，所以在这里 lazy import，没有 CJK 的库不付这个钱。
    """
    low = text.lower()
    tokens = _LATIN.findall(low)
    cjk = _CJK.findall(low)
    if cjk:
        try:
            import jieba

            for run in cjk:
                tokens.extend(t for t in jieba.lcut(run) if t.strip())
        except Exception as e:  # noqa: BLE001
            _warn_once(f"jieba unavailable ({e}); CJK falls back to bigrams")
            for run in cjk:
                tokens.extend(run[i:i + 2] for i in range(max(len(run) - 1, 1)))
    return tokens


def _weight() -> float:
    """每次调用都读环境变量：便于在不改代码的前提下切换。"""
    raw = os.getenv(ENV_WEIGHT)
    if not raw:
        return _LEX_WEIGHT
    try:
        return float(raw)
    except ValueError:
        _warn_once(f"{ENV_WEIGHT}={raw!r} is not a number; using {_LEX_WEIGHT}")
        return _LEX_WEIGHT


def rerank(
    query_text: str,
    candidates: Sequence[Tuple[str, float, str]],
    *,
    lex_weight: Optional[float] = None,
) -> Optional[List[str]]:
    """返回融合后的 id 顺序；不可用或无意义时返回 None（调用方保持纯稠密）。

    candidates 是 (id, 稠密余弦分, 用于建词面索引的文本)。文本应当与嵌入侧一致
    （`subject: statement`），否则两路排的不是同一个东西。
    """
    if os.getenv(ENV_DISABLE, "").strip() in ("1", "true", "True"):
        return None
    if len(candidates) < 2:
        return None
    if lex_weight is None:
        lex_weight = _weight()
    try:
        # BM25Plus 而不是 BM25Okapi。Okapi 的 idf 对"出现在多数文档里"的词取负值，
        # 再由 epsilon 地板截平：候选池只有 2 条时每个词都出现在 >=50% 的文档里，
        # 两条得分相同，完全无法区分。而小候选池正是本路的主要工作区间，用
        # Okapi 等于在最常见的情形下把词面通道关掉。BM25Plus 的下界项保证正分。
        from rank_bm25 import BM25Plus
    except Exception as e:  # noqa: BLE001
        _warn_once(f"rank_bm25 unavailable ({e}); fact search stays dense-only")
        return None

    q = tokenize(query_text)
    if not q:
        return None
    corpus = [tokenize(text) for _, _, text in candidates]
    if not any(corpus):
        return None
    try:
        scores = BM25Plus(corpus).get_scores(q)
    except Exception as e:  # noqa: BLE001
        _warn_once(f"bm25 scoring failed ({e}); fact search stays dense-only")
        return None

    ids = [fid for fid, _, _ in candidates]
    # 全部候选得分相同 => 词面没有区分力（一个查询 token 都没命中，或命中得一样多）。
    # 此时它的"排名"是任意的，融合进去等于拿噪声扰动稠密顺序，直接不重排。
    lo, hi = min(scores), max(scores)
    if hi - lo <= 1e-9:
        return None

    # 融合用余弦原分 + 归一化的词面分，而不是两路各自的名次。
    #
    # 名次的问题是它抹掉间距：候选池只有个位数，把名次归一到 [0,1] 会把 0.0004 的
    # 余弦差和 0.05 的余弦差同等放大成一个名次身位，于是"稠密几乎没意见"和"稠密强烈
    # 反对"变得无从区分。余弦本身已经是可比的标度，直接用。
    #
    # 这样 lex_weight 就有了可解释的含义：词面最多能翻过 lex_weight 这么大的余弦
    # 间距。
    lex = {ids[i]: (scores[i] - lo) / (hi - lo) for i in range(len(ids))}
    dense = {fid: s for fid, s, _ in candidates}
    dense_pos = {fid: i for i, fid in enumerate(
        sorted(ids, key=lambda f: -dense[f]))}
    fused = {fid: dense[fid] + lex_weight * lex[fid] for fid in ids}
    # 并列时按稠密顺序稳定收敛：稠密是主索引，词面是补丁。
    return sorted(ids, key=lambda f: (-fused[f], dense_pos[f]))
