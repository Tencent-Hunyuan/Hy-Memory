"""
中文版 Prompt 定义（兼容入口）。

Prompt 文本已统一迁到 ``hy_memory/prompts/zh_hans.py``（按语言码注册，见
``hy_memory.prompts.get_prompt``）。这里保留旧的 ``*_ZH`` 名字作为别名，
字节级不变，旧代码 / 测试照常 import。
"""

from ..prompts.zh_hans import (  # noqa: F401
    EXTRACT_FEW_SHOT as EXTRACT_FEW_SHOT_ZH,
    EXTRACT_PROMPT as EXTRACT_PROMPT_ZH,
    EXTRACT_PROMPT_NO_INTENTION as EXTRACT_PROMPT_ZH_NO_INTENTION,
    EXTRACT_SYSTEM_PROMPT as EXTRACT_SYSTEM_PROMPT_ZH,
    RECONCILE_FEW_SHOT as RECONCILE_FEW_SHOT_ZH,
    RECONCILE_PROMPT as RECONCILE_PROMPT_ZH,
    RECONCILE_STRUCTURE_METADATA_PROMPT as RECONCILE_STRUCTURE_METADATA_PROMPT_ZH,
    SEARCH_QUERY_PROMPT as SEARCH_QUERY_PROMPT_ZH,
    STRUCTURE_METADATA_PROMPT as STRUCTURE_METADATA_PROMPT_ZH,
)


# ================================================================
# SUMMARY PROMPT (中文版)
# ================================================================

SUMMARY_PROMPT_ZH = """为以下对话内容生成简洁的摘要。

内容:
---
{content}
---

记忆日期: {memory_date}
当前日期: {current_date}

要求:
1. **第三人称**: 以"用户..."描述用户——不要使用没有明确先行词的代词。
2. **长度**: 1-3句话，最多200字。
3. **优先级（内容超出长度限制时）**:
   a) 变化、决定、承诺（最高）
   b) 明确的偏好、态度、喜恶
   c) 关键事件和事实
   d) 背景信息（最低）
4. **保留偏好信号**: 保留任何直接或间接表达的喜好、厌恶、态度或观点——即使看起来不重要。
5. **自包含**: 读者应该在不看原始对话的情况下就能理解摘要。
6. **不要编造**: 不要添加原始内容中没有的信息。
7. **语言**: 输出语言必须与输入语言一致（中文输入→中文输出）。
8. **时间处理**:
   - **记忆日期**是对话实际发生的时间，是你解析对话中相对时间引用的唯一时间锚点。
   - **当前日期**是今天的系统日期（可能距记忆日期已过数年），不要用它来解释用户的陈述。
   - 如果提供了记忆日期，将相对时间表达式根据它转换为绝对引用（"上周" → 对应的日期）。
   - 如果记忆日期为空，重写句子使其不包含时间（避免在输出中留下"上周"/"昨天"等原始表达）。

## 输出约定

严格格式要求:
1. 只输出摘要文本——一段1-3句话的纯文本。
2. 不要用引号、反引号、代码块或任何 markdown 包裹输出。
3. 不要添加前缀或标签（不要"摘要："、"Summary："、"这是..."等）。
4. 不要添加尾部解释或元评论。
5. 如果内容太简单不值得总结，输出一句描述最显著元素的话——不要输出空字符串。

现在生成摘要。"""


ROLLING_SUMMARY_PROMPT_ZH = """你负责维护用户当天的对话摘要，按 topic（话题）组织。

给你的输入：
1. 上一条摘要（PREVIOUS SUMMARY）—— 该用户今天最近的一条摘要（可能为空）。
2. 新对话轮次（NEW TURNS）—— 最近的对话，每条以行号 [n] 开头。

你的任务：把 NEW TURNS 按 topic 分组，对每组判断：
- 若有价值且内容已足够 → 为它写一条摘要。如果该组延续了「上一条摘要」的同一 topic，
  则改写一条「合并了上一条摘要 + 新对话」的更新摘要（"is_continuation": true）。
- 若有价值但还不够写摘要 → 把这些行号放进 "keep_line_ids"（留待下次再总结）。
- 若无价值 / 不值得记 → 直接省略这些行号（丢弃）。

上一条摘要（PREVIOUS SUMMARY）：
---
{prev_summary}
---

新对话轮次（NEW TURNS）：
---
{turns}
---

记忆日期: {memory_date}
当前日期: {current_date}

摘要撰写规则（与标准摘要一致）：
1. 第三人称「用户……」。2. 每条 1-3 句话，最多 200 字。3. 保留偏好/决定/变化。
4. 自包含。5. 不编造。6. 输出语言必须与对话语言一致。7. 相对时间按记忆日期解析。

## 输出约定 —— 严格 JSON，不要 markdown / 代码块：
{{
  "summary_list": [
    {{"content": "<摘要文本>", "topic": "<简短话题标签>", "line_ids": [<int>, ...], "is_continuation": <true|false>}}
  ],
  "keep_line_ids": [<int>, ...]
}}

输出规则：
- 每个行号最多出现在一个位置（某条摘要的 line_ids、或 keep_line_ids、或被丢弃而完全不出现），
  绝不在两个分组里重复出现。
- line_ids / keep_line_ids 只能引用 NEW TURNS 中出现过的行号。
- 如果当前还没有任何值得写摘要的内容，返回 {{"summary_list": [], "keep_line_ids": [...]}}。
只输出 JSON。"""
