"""
Agent Memory - Extractor 提取智能体

统一 Prompt 提取: 单次 LLM 调用提取 memory / intentions / basic_info。
  - memory: 一切值得长期记忆的信息（偏好/观点/事件/计划/经历/习惯/决定）
  - intentions: 用户未来要做的前瞻意图（带 valid_until 截止日）
  - basic_info: 结构化基础画像 kv

LLM 只需感知 memory 与 intention 两类，无需感知内部分层（layer）。

另保留 deep_extract (System 2 深度分析) 供后台精炼使用。
"""

from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from enum import Enum
import json
import logging
import re

from .llm_provider import LLMProvider, _content_fingerprint, _log_llm_content_enabled
from ..config import LLMConfig as GlobalLLMConfig

logger = logging.getLogger(__name__)


def _sentence_parts_for_dedup(text: str) -> List[str]:
    """Split complete sentences without breaking initials in personal names."""
    return [
        part.strip()
        for part in re.split(r"(?<=[.!?])\s+(?=[A-Z])", str(text or "").strip())
        if part.strip()
    ]


def _content_words_for_dedup(text: str) -> set:
    stop = {
        "the", "a", "an", "and", "or", "of", "to", "in", "on", "at", "for",
        "is", "was", "were", "has", "have", "had", "he", "she", "they", "it",
        "his", "her", "their", "user",
    }
    return {
        word for word in re.findall(r"[a-z0-9]+", str(text or "").casefold())
        if len(word) > 2 and word not in stop
    }


def dedup_internal_memory_sentences(text: str) -> str:
    """Drop only later complete sentences that are near-contained paraphrases."""
    parts = _sentence_parts_for_dedup(text)
    if len(parts) < 2:
        return text
    keep = [True] * len(parts)
    words = [_content_words_for_dedup(part) for part in parts]
    for later in range(1, len(parts)):
        short = words[later]
        if len(short) < 8:
            continue
        for earlier in range(later):
            long = words[earlier]
            if not long:
                continue
            coverage = len(short & long) / len(short)
            jaccard = len(short & long) / max(1, len(short | long))
            if coverage >= 0.75 and jaccard >= 0.45:
                keep[later] = False
                break
    return " ".join(part for part, include in zip(parts, keep) if include)


# ================================================================
# 数据结构
# ================================================================


class ExtractMode(str, Enum):
    """提取模式"""

    LIGHT = "light"  # System 1 轻量提取
    DEEP = "deep"  # System 2 深度提取


@dataclass
class ExtractResult:
    """提取结果 (V1 兼容)"""

    success: bool
    info: Dict[str, Any] = field(default_factory=dict)
    suggested_layer: Optional[str] = None
    confidence: float = 0.0
    tokens_used: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    error: Optional[str] = None
    error_code: Optional[str] = (
        None  # "EMPTY_RESPONSE" / "JSON_PARSE_FAILED" / "LLM_ERROR"
    )
    raw_response: Optional[str] = (
        None  # LLM 原始返回（失败时保留，用于写 pipeline_log）
    )
    llm_finish_reason: str = ""
    llm_model: str = ""
    llm_response_id: str = ""
    llm_choices_count: int = 0
    llm_content_len: int = 0
    llm_tool_calls_count: int = 0
    # 推理模型诊断：reasoning_tokens 接近/等于 max_tokens 且 content 为空，
    # 说明思维链吃光了输出配额（hy3 需传 reasoning_effort=no_think）。
    llm_reasoning_tokens: int = 0
    llm_reasoning_len: int = 0
    tool_calls_only: bool = False  # True 当 LLM 只返回了 tool_calls，content 为空
    attempts: List[Dict[str, Any]] = field(
        default_factory=list
    )  # LLM 重试各次失败明细（来自 LLMRetryError.attempts），写入 EXTRACT trace
    _actual_prompt: Optional[str] = (
        None  # 实际发送给 LLM 的 user prompt（用于 trace 日志）
    )
    _actual_system_prompt: Optional[str] = None  # 实际的 system prompt


@dataclass
class V2ExtractResult:
    """V2 提取结果 (丰富结构)"""

    success: bool
    mode: ExtractMode = ExtractMode.LIGHT

    # 事实提取
    facts: List[Dict[str, Any]] = field(default_factory=list)

    # 实体与关系
    entities: List[Dict[str, Any]] = field(default_factory=list)
    relations: List[Dict[str, Any]] = field(default_factory=list)

    # 情绪标注
    emotional_valence: float = 0.0
    emotional_arousal: float = 0.0

    # 时间信息
    temporal_expressions: List[Dict[str, Any]] = field(default_factory=list)

    # 意图检测
    intentions: List[Dict[str, Any]] = field(default_factory=list)

    # 画像信息
    profile_updates: List[Dict[str, Any]] = field(default_factory=list)

    # 更新类型判断 (深度模式)
    update_classifications: List[Dict[str, Any]] = field(default_factory=list)

    tokens_used: int = 0
    error: Optional[str] = None


# ================================================================
# Prompt 模板
# ================================================================

# 统一提取 Prompt
#
# 设计要点（v0.3.45_v0 post8）：
#   - 不再输出 basic_info 字段；稳定结构化属性（name/age/location/occupation/employer）
#     由 LLM 通过 function-calling tool `update_basic_user_profile` 表达。
#   - 不再输出 time_ref 字段。时间相关信息如果是时间敏感的（schedule / past event），
#     让 LLM 把已解析的时间自然嵌入 content 即可；稳定偏好类 content 保持不含时间。

# System prompt（固定，不参与 format）
EXTRACT_SYSTEM_PROMPT = """# ROLE

You are a Memory Extractor.

Extract durable, high-value memories from conversations and convert them into structured, self-contained statements.

Only extract information that is likely to matter in future conversations.

---

# INPUTS

You will receive:

* Last k Messages
* New Messages
* Memory Date
* Current Date

Use:

* Last k Messages only for coreference resolution
* Memory Date as the ONLY temporal anchor
* Current Date only as metadata, never for resolving relative time

---

# WHAT TO EXTRACT

Extract ONLY genuinely new information introduced in the NEW user messages.

Good memories include:

* preferences
* long-term projects
* habits
* goals
* recurring activities
* opinions
* relationships
* important experiences
* stable traits
* future plans
* decisions
* constraints
* accepted recommendations

Do NOT extract:

* small talk
* acknowledgements
* generic emotions
* temporary conversational context
* assistant reminders of old memories
* information already implied by prior context
* vague personality judgments

---

# SUBJECT

Every memory must be self-contained, written in third person, with an explicit
subject. Which subject to use is decided by **who spoke**:

| Speaker | Subject to write | Example |
| --- | --- | --- |
| `role: "user"` | the user | "The user ..." / "用户..." |
| any speaker with a `name` other than the user (e.g. `name: "John"`) | that person, by name | "John ..." |
| `role: "assistant"` with NO `name` | the AI assistant | "The assistant ..." |

The two input sections express the speaker differently:

* **New Messages** is a JSON array — read `role` and the optional `name` field.
* **Last k Messages** is rendered text — the speaker is the bracket label,
  `[user]:` / `[John]:` / `[assistant]:`, optionally preceded by one line
  "The user's name is Tim" / "用户名叫Tim".

Rules:

* The user is the person these memories belong to. Even when the user's own name
  is known (from `name` on their message, or from the leading name line), still
  write "The user ...", NEVER "Tim ...".
* **`role: "assistant"` WITH a `name` is another real person, not the AI.**
  Write their memories with their name as the subject: "John ...".
* Only a nameless `role: "assistant"` is the AI assistant.
* Never invent a name for the AI assistant, it has none.

---

# LANGUAGE RULES

* Output language must match input language.
* Preserve original scripts and technical terms exactly.
* Never transliterate names, brands, or equations.

---

# TWO KINDS OF MEMORY

You extract two kinds of memory: **memory** and **intentions**.

## memory

Everything durable and worth remembering about the user — and about any other
named person in the conversation — goes here:

* preferences, opinions, values, attitudes
* events, actions, experiences
* habits, behavioral patterns
* relationships, traits
* objective facts, decisions, constraints
* ongoing activities

Implicit preferences MUST be inferred when strongly suggested
(e.g. resuming an activity → positive interest; avoiding something →
negative preference; repeated voluntary effort → sustained interest).

Do NOT separate "what kind of person the user is" from "what happened".
Both go into `memory`.

## intentions

A forward-looking thing the user **plans or intends to do in the future**,
which can be acted on and which becomes irrelevant once done or past.

Examples:

* "The user has a job interview next Tuesday."
* "The user plans to move apartments at the end of the month."
* "The user intends to finish the report before Friday."

NOT intentions (these belong in `memory`):

* stable preferences / traits ("The user likes basketball")
* completed past events ("The user moved to Beijing last year")
* general goals with no actionable future step

### valid_until

For each intention, provide `valid_until`: the date after which the
intention no longer matters (the deadline / target date), resolved
against **Memory Date** (e.g. "next Tuesday" → that absolute date).
If no deadline can be determined, set `valid_until` to `null`.

---

# OWNER

Every memory and intention MUST carry an `owner` saying which memory store it
belongs to. There are exactly two values:

* `"user"` — anything belonging to the user's own memory store. This includes
  facts/preferences/traits about the user themselves, AND facts about other
  named real people in the conversation, because those are still things the
  user's memory should hold.
* `"agent"` — information the AI assistant (a nameless `role: "assistant"`)
  provides (recommendations, confirmations, things it researched), or a future
  action the user wants the **assistant** to perform ("remind me in 8 hours").

A named person is NEVER `owner: "agent"` — they are a real person, not the AI,
even though their messages carry `role: "assistant"`. Their memories are
`owner: "user"` with their name as the content subject.

Decide `owner` per item. When unsure, default to `"user"`.

---

# TEMPORAL RULES

Resolve relative time using Memory Date.

Examples:

* "next week"
* "last month"
* "tomorrow"

If time cannot be resolved safely:

* remove the relative wording
* keep the memory atemporal

Do not invent chronology.

**Just-happened events are events — keep them.** When the user reports something as
just completed ("just got back", "just finished", "this morning", "yesterday"), the
completion date is the Memory Date. Emit the **event itself** with that date; do not
fold it into a background statement.

* "I just got back from a trip with my family"
  → "The user returned from a family trip on <Memory Date>."
  NOT → "The user takes family trips." (the return date is the answer to "when did
  they get back", and dropping it makes the question unanswerable)

Where an event has both a start and an end that the conversation gives separately,
record both rather than collapsing them into one span.

---

# QUALITY RULES

Each memory must be:

* self-contained
* contextually complete
* retrieval-friendly
* information-dense

Retain important details:

* names
* products
* locations
* numbers
* equations
* constraints

Never generalise a specific into a category. Write what was actually said.

* "Banff" → keep "Banff", NOT "the Rocky Mountains"
* "a 3-year-old border collie named Luna" → keep the breed, age and name

**Specifics supplied by the other speaker still count.** A fact is often built across
turns: the other speaker names the thing, the user confirms it. The subject rules
decide *whose* memory it is — they do not license dropping detail.

* [Sam]: "Did you take advantage of the skiing opportunities in Banff?"
  [user]: "Yeah, it was great for skiing!"
  → "The user went skiing in Banff and found the snow amazing."
  NOT → "The user enjoys winter sports." (both the place and the activity were
  established, one by each speaker)

Avoid fragmentary memories lacking context.

Choose the smallest number of memories that still preserves distinct future
retrieval needs.

* Compression means combining, not omitting. Every explicit durable proposition
  in the new messages must appear exactly once in the output.
* Combine details when they share the same subject and the same real-world event,
  state, preference, plan, or decision. A coherent event may use 1–3 sentences.
* Do not emit both a broad statement and narrower restatements of the same evidence.
* Split only when the subject differs, the events happened independently, or the
  memories describe different states.
* Keep a cause, recommendation, or consequence with the fact it explains when
  separating them would lose the relationship.
* Never merge separate dated occurrences merely because they share a topic.
* Preserve the source's time precision. Do not turn vague phrases such as "last
  weekend" or "last summer" into exact calendar dates unless the date is explicit
  or uniquely determined; "the weekend before <Memory Date>" is acceptable.

---

# TAGS

Each memory must include 1–3 concise tags.
- Topical keywords stay lowercase (travel, health, work).
- Proper nouns keep capitalization (Jasper, Canada, Banff, Prius).

Examples:

* work
* travel
* Jasper
* Canada
* health

---
"""

STRUCTURE_METADATA_PROMPT_EN = """

---

# STRUCTURAL ORGANIZATION

For every `memory`, also output:

* `topic_paths`: a backward-compatible list of zero to three broad-to-specific
  semantic paths.
* `event_facet`: an object with only `subject`, `participants`, `action`,
  `objects`, `locations`, and `state`; state is `planned`, `ongoing`,
  `completed`, or `unknown`.
* `topic_assignments`: zero to six subject-specific memberships. Each item is
  `{subject, role, path}`, where `path` is a list of `{name, type}` segments and
  `type` is one of `topic`, `facet`, `series`, or `episode`. Use `topic` for a
  broad domain, `facet` for a concrete activity/semantic aspect, `series` only
  for repeated occurrences of the same concrete activity/state line, and
  `episode` only for one bounded real-world occurrence. A shared event may have
  one assignment per explicit participant while keeping one canonical memory.
  Types follow `topic -> facet -> series -> episode`; levels may be skipped but
  types may not repeat or move backward.

This extractor sees only the current write and therefore must not emit a
`series` segment. Emit Topic/Facet/Episode here. Reconcile may later establish a
Series after comparing independent stored occurrences.

This is write-time organization. Do not infer Series/Episode from path depth,
manufacture hierarchy from a proper noun or broad tag, or assign a person only
because they co-occur in unrelated context. Stop at the most specific supported
level and use empty values when uncertain. Never encode query forms such as
"when", "how many", "who", plurality, or ordinal wording.
"""

# Few-shot examples (appended to the system prompt only when few_shot_enabled).
# Each example mirrors the real input shape (Last k Messages / New Messages /
# Memory Date) and our output schema (memory / intentions / basic_info with
# owner & tags), and ends with a one-line takeaway.
EXTRACT_FEW_SHOT_EN = """
---

# EXAMPLES

## Example 1 — Boundary: old context is for resolution only, never re-extracted

Last k Messages:
[{"role": "user", "content": "I've been looking at rescue dogs at the shelter downtown."},
 {"role": "assistant", "content": "That's lovely — found one you like?"}]
New Messages:
[{"role": "user", "content": "Yeah, picked her up yesterday — a 3-year-old border collie named Luna."}]
Memory Date: 2025-04-10

Output:
```json
{
  "memory": [
    {"content": "The user adopted a 3-year-old border collie named Luna from a downtown shelter on 2025-04-09.", "tags": ["pets", "family"], "owner": "user"}
  ],
  "intentions": [],
  "basic_info": {}
}
```
The old messages only resolve "her" and supply context ("downtown shelter"). Do NOT emit a stale memory like "the user is considering adopting a dog" — only the genuinely new fact (the completed adoption) is extracted, with "yesterday" resolved against Memory Date.

## Example 2 — Condense a long assistant reply

New Messages:
[{"role": "user", "content": "Write me a short essay on why remote work boosts productivity."},
 {"role": "assistant", "content": "Sure — opening: 'Remote work has reshaped how teams operate...' [~2000 words of body omitted] ...closing: 'In short, autonomy and fewer interruptions drive higher output.'"}]
Memory Date: 2025-03-22

Output:
```json
{
  "memory": [
    {"content": "The assistant drafted a short essay for the user arguing that remote work boosts productivity because autonomy and fewer interruptions drive higher output.", "tags": ["writing", "remote-work"], "owner": "agent"}
  ],
  "intentions": [],
  "basic_info": {}
}
```
The assistant's reply is long. Never store the verbatim essay — distill it to topic + thesis. owner is "agent" because the assistant produced it. Bloated verbatim content makes memory unusable.

## Example 3 — Composite extraction: basic_info + intention + condensed agent plan

New Messages:
[{"role": "user", "content": "Hi, I'm Kenji. I'm planning a trip to Japan next month — could you put together a 7-day itinerary?"},
 {"role": "assistant", "content": "# 7-Day Classic Japan Golden Route (Tokyo → Kyoto → Nara → Osaka)\\nFly into Tokyo, Shinkansen south to Kyoto, day-trip Nara, depart from Osaka (Kansai).\\n\\n## Day 1: Arrival in Tokyo\\n- Land at Haneda/Narita, drop bags at hotel near Tokyo Station.\\n- Late afternoon: Sensoji Temple (Asakusa), wander Nakamise Street, try taiyaki.\\n- Evening: Shibuya Crossing + Shibuya Sky rooftop for sunset; izakaya dinner (yakitori, sake).\\n\\n## Day 2: Tokyo markets & landmarks — [omitted]\\n## Day 3: Shinkansen to Kyoto, Fushimi Inari, Gion — [omitted]\\n## Day 4: Kyoto temples (Kiyomizu-dera, Kinkaku-ji, Ginkaku-ji) — [omitted]\\n## Day 5: Nara day trip (Nara Park deer, Todai-ji) — [omitted]\\n## Day 6: Osaka (Osaka Castle, Dotonbori street food) — [omitted]\\n## Day 7: Osaka sightseeing + depart from Kansai Airport — [omitted]\\n\\nTip: buy a 7-day JR Pass before arriving. Want me to adjust anything?"}]
Memory Date: 2025-09-15

Output:
```json
{
  "memory": [
    {"content": "The assistant created a 7-day Japan itinerary for the user along the classic golden route: Tokyo (Asakusa, Shibuya), Shinkansen to Kyoto (Fushimi Inari, Kiyomizu-dera, Kinkaku-ji, Gion), a Nara day trip (deer park, Todai-ji), then Osaka (Osaka Castle, Dotonbori), departing from Kansai Airport.", "tags": ["travel", "japan", "itinerary"], "owner": "agent"}
  ],
  "intentions": [
    {"content": "The user plans to take a 7-day trip to Japan in October 2025.", "tags": ["travel", "japan"], "valid_until": "2025-10-31", "owner": "user"}
  ],
  "basic_info": {"name": "Kenji"}
}
```
One turn produces three outputs in different buckets: the name goes to basic_info, the trip is a forward-looking intention with a deadline, and the assistant's long multi-section itinerary is condensed into ONE dense agent memory — the route and key stops per city, NOT every day's bullets, tips, or food lists. Storing the whole reply verbatim would bloat memory; storing the skeleton keeps it queryable.

## Example 4 — A later message confirming/adopting an earlier recommendation is itself a fact

New Messages:
[{"role": "user", "content": "My 6-year-old won't eat any vegetables. Any ideas?"},
 {"role": "assistant", "content": "Try blending spinach or carrots into pasta sauce — the color and flavor mostly disappear, and many kids accept it without noticing."},
 {"role": "user", "content": "Oh nice, we'll try that tonight. By the way, what's a good bedtime for that age?"},
 {"role": "assistant", "content": "For a 6-year-old, aim for 7–9 PM with about 10–11 hours of sleep."}]
Memory Date: 2025-06-20

Output:
```json
{
  "memory": [
    {"content": "The user has a 6-year-old child who refuses to eat vegetables, and accepted the assistant's suggestion to blend spinach or carrots into pasta sauce to hide them.", "tags": ["parenting", "food"], "owner": "user"},
    {"content": "The assistant advised the user that a 6-year-old should aim for a 7–9 PM bedtime with about 10–11 hours of sleep.", "tags": ["parenting", "sleep"], "owner": "agent"}
  ],
  "intentions": [],
  "basic_info": {}
}
```
In the third message the user said "we'll try that tonight" — explicitly adopting the earlier suggestion. That acceptance is valuable and must be recorded ("accepted the assistant's suggestion to ..."), not discarded as small talk. The bedtime advice was just given and not yet adopted, so it is recorded as advice (owner "agent").

## Example 5 — RAG/injected context: never store the injected text

New Messages:
[{"role": "user", "content": "[Retrieved context: The Eiffel Tower is 330m tall and was completed in 1889.] Given the above, I'm going to propose to my girlfriend at the top of the Eiffel Tower this summer."}]
Memory Date: 2025-05-02

Output:
```json
{
  "memory": [],
  "intentions": [
    {"content": "The user plans to propose to his girlfriend at the top of the Eiffel Tower in summer 2025.", "tags": ["relationship", "travel"], "valid_until": "2025-08-31", "owner": "user"}
  ],
  "basic_info": {}
}
```
The bracketed retrieved context (the tower's height and year) is RAG-injected reference material, NOT something the user told us about themselves — do NOT store it. Only the user's own forward-looking plan is extracted. System/RAG-injected text is context for the model, never a memory.

## Example 6 — Shared reference material: extract the content, not the sharing action

New Messages:
[{"role": "user", "content": "Remember my grandma's dumpling recipe so I don't lose it. Filling: 500g ground pork, 300g napa cabbage, 2 tbsp soy sauce, 1 tbsp sesame oil, a thumb of ginger minced. Cabbage salted 15 min then squeezed dry. Boil dumplings, add cold water twice, ready when they float."},
 {"role": "assistant", "content": "Got it, saved!"}]
Memory Date: 2025-07-01

Output:
```json
{
  "memory": [
    {"content": "The user's grandmother's dumpling recipe: filling of 500g ground pork, 300g napa cabbage (salted 15 minutes then squeezed dry), 2 tbsp soy sauce, 1 tbsp sesame oil, and a thumb of minced ginger; boil the dumplings, add cold water twice, and they are done when they float.", "tags": ["food", "recipe", "family"], "owner": "user"}
  ],
  "intentions": [],
  "basic_info": {}
}
```
Extract the actual factual content (ingredients, quantities, the salt-and-squeeze step, the boiling method) — NOT "the user shared a recipe" or "the user asked to remember the recipe". The sharing action is worthless; the content is what's queryable.

## Example 7 — Structured numbers and attributes preserved exactly

New Messages:
[{"role": "user", "content": "Logging my home-gym PRs this cycle: Squat 140kg x 3, Bench 100kg x 5, Deadlift 180kg x 2, Overhead Press 60kg x 6. Bodyweight 78kg, 3 sets per lift, 3 min rest."},
 {"role": "assistant", "content": "Logged — strong numbers!"}]
Memory Date: 2026-01-20

Output:
```json
{
  "memory": [
    {"content": "The user's current lifting PRs are Squat 140kg x 3, Bench 100kg x 5, Deadlift 180kg x 2, and Overhead Press 60kg x 6, at a bodyweight of 78kg.", "tags": ["fitness", "strength"], "owner": "user"},
    {"content": "The user trains with 3 sets per lift and 3 minutes of rest between sets.", "tags": ["fitness", "training"], "owner": "user"}
  ],
  "intentions": [],
  "basic_info": {}
}
```
Every number and unit (kg, reps, sets, rest time) is preserved exactly — dropping them would destroy the most queryable information. Split into two clean topics (PRs vs. training protocol), but never round or omit the specifics.

## Example 8 — Long multi-topic conversation: do not stop after the first topic

New Messages:
[{"role": "user", "content": "Big week — I finally defended my PhD thesis on Tuesday!"},
 {"role": "assistant", "content": "Huge congrats! How do you feel?"},
 {"role": "user", "content": "Relieved! I also signed a lease on an apartment in Berlin and move next month. And I've started learning German on Duolingo — 30-day streak. My partner Sofia is staying in Madrid for now, which is hard."}]
Memory Date: 2025-11-08

Output:
```json
{
  "memory": [
    {"content": "The user defended their PhD thesis on 2025-11-04.", "tags": ["education", "milestone"], "owner": "user"},
    {"content": "The user signed a lease on an apartment in Berlin.", "tags": ["housing"], "owner": "user"},
    {"content": "The user has been learning German on Duolingo and has a 30-day streak.", "tags": ["language", "learning"], "owner": "user"},
    {"content": "The user's partner Sofia is staying in Madrid for now, which the user finds hard.", "tags": ["relationship"], "owner": "user"}
  ],
  "intentions": [
    {"content": "The user plans to move to Berlin next month (around December 2025).", "tags": ["housing", "relocation"], "valid_until": "2025-12-31", "owner": "user"}
  ],
  "basic_info": {}
}
```
Four+ topics — thesis defense, the Berlin apartment, learning German, and the partner in Madrid. Do not stop after the first topic. Note one turn split into a completed fact (lease signed) AND an intention (the upcoming move), and the emotional-but-substantive detail about Sofia is kept because it carries lasting relationship context.

## Example 9 — Two real people: `name` on an assistant message means a real person

New Messages:
[{"role": "user", "name": "Tim", "content": "Just got back from the climbing gym, third time this week. Finally sent that overhang route."},
 {"role": "assistant", "name": "John", "content": "Nice! I've been swimming instead — my shoulder still hurts from last month."}]
Memory Date: 2025-08-14

Output:
```json
{
  "memory": [
    {"content": "The user has been climbing three times a week and recently completed a difficult overhang route.", "tags": ["fitness", "climbing"], "owner": "user"},
    {"content": "John has been swimming instead of other exercise because his shoulder still hurt after an injury around July 2025.", "tags": ["fitness", "health"], "owner": "user"}
  ],
  "intentions": [],
  "basic_info": {"name": "Tim"}
}
```
John carries `role: "assistant"` but he has a `name`, so he is another real person, NOT the AI: his memory uses "John ..." as the subject and is still `owner: "user"` (it belongs to the user's memory store). The user's own memory stays "The user ..." even though we know the name is Tim — never "Tim has been climbing". `basic_info.name` comes from `name: "Tim"` on the user message, which is authoritative even though Tim never said his own name out loud.

## Example 10 — Counter-example: another person's attributes never go into basic_info

New Messages:
[{"role": "user", "name": "Tim", "content": "你最近在忙什么？"},
 {"role": "assistant", "name": "John", "content": "我在柏林一家医院当护士，上的是夜班，挺累的。"}]
Memory Date: 2025-09-02

Output:
```json
{
  "memory": [
    {"content": "John是柏林一家医院的护士，上夜班，觉得很累。", "tags": ["work", "health"], "owner": "user"}
  ],
  "intentions": [],
  "basic_info": {"name": "Tim"}
}
```
John's occupation and location are HIS attributes, so they go into `memory` with "John" as the subject. They must NEVER land in `basic_info.occupation` / `basic_info.location` — `basic_info` is exclusively the user's own profile. Only `name` (Tim, from the user message's `name` field) belongs there.

---
"""


EXTRACT_PROMPT = """## Last k Messages

{last_messages}

---

## New Messages

{content}

---

## Memory Date

{memory_at}

---

## Current Date

{current_date}

---

# Output Format

Return ONLY a JSON object inside a ```json fenced block.

```json
{{
  "memory": [
    {{
      "content": "...",
      "tags": ["..."],
      "owner": "user"
    }}
  ],
  "intentions": [
    {{
      "content": "...",
      "tags": ["..."],
      "valid_until": "YYYY-MM-DD",
      "owner": "user"
    }}
  ],
  "basic_info": {{}}
}}
```

Rules:

* `memory` = durable info worth remembering: preferences, opinions, attitudes,
  traits, events, plans, experiences, habits, decisions — about the user or
  about any other named real person, with the subject written per "# SUBJECT"
* `intentions` = forward-looking things the user (or another named person) plans /
  intends to do in the future, with `valid_until` = the deadline/target date
  (resolved against Memory Date) or `null` if none
* `owner` (required on every memory & intention) = `"user"` or `"agent"`
  (see "# OWNER" above; default `"user"` when unsure)
* `basic_info` = the **user's own profile only**. Include stable profile
  fields (e.g. name, age, location, occupation, employer) **explicitly stated**
  in the NEW user messages. Never infer, never invent. If no field is explicitly
  stated, output `{{}}`.
  * Exception for `name`: the `name` field on the user's own message, or a leading
    line "The user's name is X" / "用户名叫X", is an authoritative source for
    `name` — even though the user never said it out loud.
  * NEVER put another real person's attributes here. If John (an assistant-role
    message carrying `name`) says "I'm a nurse in Berlin", that is a `memory`
    ("John is a nurse in Berlin."), NOT `basic_info.occupation` / `.location`.
* No duplicate information across sections
* Tags must contain 1–3 concise lowercase keywords
* Use empty arrays `[]` or `{{}}` when nothing is extracted
* Return valid JSON only

Output the JSON now."""


# User prompt when MEMORY_ENABLE_INTENTION=false（无 intentions 桶）
EXTRACT_PROMPT_NO_INTENTION = """## Last k Messages

{last_messages}

---

## New Messages

{content}

---

## Memory Date

{memory_at}

---

## Current Date

{current_date}

---

# Output Format

Return ONLY a JSON object inside a ```json fenced block.

```json
{{
  "memory": [
    {{
      "content": "...",
      "tags": ["..."],
      "owner": "user"
    }}
  ],
  "basic_info": {{}}
}}
```

Rules:

* `memory` = durable info worth remembering: preferences, opinions, attitudes,
  traits, events, experiences, habits, decisions, **and future plans / upcoming
  actions** — about the user or about any other named real person, with the
  subject written per "# SUBJECT". Do **not** invent a separate intentions bucket.
* `owner` (required on every memory) = `"user"` or `"agent"`
  (see "# OWNER" above; default `"user"` when unsure)
* `basic_info` = the **user's own profile only**. Include stable profile
  fields (e.g. name, age, location, occupation, employer) **explicitly stated**
  in the NEW user messages. Never infer, never invent. If no field is explicitly
  stated, output `{{}}`.
  * Exception for `name`: the `name` field on the user's own message, or a leading
    line "The user's name is X" / "用户名叫X", is an authoritative source for
    `name` — even though the user never said it out loud.
  * NEVER put another real person's attributes here. If John (an assistant-role
    message carrying `name`) says "I'm a nurse in Berlin", that is a `memory`
    ("John is a nurse in Berlin."), NOT `basic_info.occupation` / `.location`.
* No duplicate information across sections
* Tags: 1–3 concise keywords; topical tags lowercase; proper nouns keep capitalization
* Use empty arrays `[]` or `{{}}` when nothing is extracted
* Return valid JSON only — **never** include an `intentions` key

Output the JSON now."""


# ================================================================
# 核心实现
# ================================================================


def _system_prompt_without_intention(system_prompt: str, *, lang: str) -> str:
    """从 system prompt 去掉 intentions 分桶，改为单一 memory（含未来计划）。"""
    import re

    if lang == "zh":
        repl = (
            "# 记忆\n"
            "\n"
            "你只提取一类记忆：**memory（记忆）**。\n"
            "\n"
            "## memory（记忆）\n"
            "\n"
            "一切值得长期记住的信息都放这里——既包括用户本人的，也包括对话中其他具名真人的：\n"
            "\n"
            "* 偏好、观点、态度、价值观\n"
            "* 事件、行动、经历\n"
            "* 习惯、行为模式\n"
            "* 关系、稳定特质\n"
            "* 客观事实、决策、约束条件\n"
            "* 正在进行的活动\n"
            "* **未来计划 / 即将发生的安排**（若值得记住，直接写进 `memory`；"
            "不要单独开 intentions 桶）\n"
            "\n"
            "如果用户隐式表达偏好，也应提取。\n"
            "\n"
            "不要把\"用户是怎样的人\"和\"发生了什么\"分开。两者都放进 `memory`。\n"
            "\n"
            "---\n"
            "\n"
        )
        system_prompt = re.sub(
            r"# 两类记忆.*?(?=\n# OWNER)",
            repl,
            system_prompt,
            count=1,
            flags=re.S,
        )
        system_prompt = system_prompt.replace(
            "每条 memory 和 intention 都必须带 `owner`",
            "每条 memory 都必须带 `owner`",
        )
        system_prompt = system_prompt.replace(
            "每条 memory 和 intention 都必须带",
            "每条 memory 都必须带",
        )
        # few-shot 里若仍出现 intentions，用硬约束盖住
        system_prompt += (
            "\n\n---\n\n# INTENTION 关闭\n"
            "当前配置关闭了 intention 抽取。即使示例里出现 `intentions`，"
            "也**不要**输出该字段；未来计划写入 `memory`。\n"
        )
    else:
        repl = (
            "# MEMORY\n"
            "\n"
            "You extract one kind of memory: **memory**.\n"
            "\n"
            "## memory\n"
            "\n"
            "Everything durable and worth remembering about the user — and about any other\n"
            "named person in the conversation — goes here:\n"
            "\n"
            "* preferences, opinions, values, attitudes\n"
            "* events, actions, experiences\n"
            "* habits, behavioral patterns\n"
            "* relationships, traits\n"
            "* objective facts, decisions, constraints\n"
            "* ongoing activities\n"
            "* **future plans / upcoming actions** that are worth remembering\n"
            "  (put them in `memory`; do NOT invent a separate intentions bucket)\n"
            "\n"
            "Implicit preferences MUST be inferred when strongly suggested.\n"
            "\n"
            "Do NOT separate \"what kind of person the user is\" from \"what happened\".\n"
            "Both go into `memory`.\n"
            "\n"
            "---\n"
            "\n"
        )
        system_prompt = re.sub(
            r"# TWO KINDS OF MEMORY.*?(?=\n# OWNER\n)",
            repl,
            system_prompt,
            count=1,
            flags=re.S,
        )
        system_prompt = system_prompt.replace(
            "Every memory and intention MUST carry an `owner`",
            "Every memory MUST carry an `owner`",
        )
        system_prompt += (
            "\n\n---\n\n# INTENTION DISABLED\n"
            "Intention extraction is disabled. Even if few-shot examples show "
            "`intentions`, do **not** emit that key; put future plans in `memory`.\n"
        )
    return system_prompt


def _message_list(content: str) -> Optional[List[Any]]:
    """Parse a JSON message blob into a list of messages, or None.

    The chat scene hands the extractor a JSON list of the new messages
    (`writer.py`: `_extract_content = _json_msgs.dumps(_msgs_dump)`).
    `detect_language()` reads the FIRST 10 alphanumeric characters of the
    string it is given, and for that blob they are the envelope keys —
    "roleuserc" — so it answers "en".  The Chinese extractor prompt
    (prompts_zh.EXTRACT_PROMPT_ZH + Chinese few-shot) was therefore
    unreachable, and a compliant model answered with ENGLISH facts, which then
    failed Chinese recall against a Chinese embedding model.

    Returning the parsed messages lets the caller route on the message TEXT via
    `is_chinese(messages=...)`, which already ignores role names and metadata.

    Returns None for anything that is not a JSON list (e.g. plain text), so the
    caller can fall back to detecting on the raw string.
    """
    text = (content or "").strip()
    if not text.startswith("["):
        return None
    try:
        items = json.loads(text)
    except (ValueError, TypeError):
        return None
    return items if isinstance(items, list) else None


class Extractor:
    """
    提取智能体 (V2)

    支持 V1 兼容接口 + V2 双模式接口。
    """

    def __init__(
        self,
        llm_provider: LLMProvider,
        llm_config: Optional[GlobalLLMConfig] = None,
    ):
        self.llm = llm_provider
        self._llm_config = llm_config or GlobalLLMConfig()
        self._call_count = 0
        self._total_tokens = 0
        logger.info("Extractor V2 initialized")

    # ================================================================
    # V1 兼容接口
    # ================================================================

    async def extract(
        self,
        content: str,
        context: Dict[str, Any] = None,
        extract_types: List[str] = None,
        current_time: str = "",
        existing_tags: Optional[List[str]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_registry: Optional[Any] = None,
        tool_context: Optional[Dict[str, Any]] = None,
        history_context: str = "",
        basic_profile_fields: Optional[Dict[str, str]] = None,
    ) -> ExtractResult:
        """
        统一提取：identity / facts / basic_info

        Args:
            content:       原始对话内容
            context:       业务上下文（目前透传，不使用）
            extract_types: 兼容参数（未使用）
            current_time:  当前时间字符串
            existing_tags: 可选，该用户已有的所有 tags（供 LLM 优先复用）
            tools:         **DEPRECATED**: 自 v0.1.5.13 起 extractor 不再调用 LLM
                           function-calling 工具——基础画像通过 prompt + JSON 输出
                           的 basic_info 字段表达。保留参数仅为向后兼容；传入也不
                           会被透传到 LLM API。
            tool_registry: **DEPRECATED**: 同上，不再使用。
            tool_context:  保留，作为 dispatch context 传给（未来可能扩展的）非 basic_info
                           tool；当前未使用。
            basic_profile_fields:
                **DEPRECATED**: 曾用于渲染 EXTRACT prompt 的基础画像 schema 段落，
                现已移除该段落（basic_info 由内置 few-shot / 指令引导）。保留参数
                仅为向后兼容，传入不再生效。
        """
        MAX_TOOL_ROUNDS = 5

        try:
            # 构建 last_messages（传入的 history_context 就是格式化好的历史对话文本）
            _last_messages = history_context or "(none)"

            # 构建日期字段
            from datetime import date as _date_cls

            _current_date = (
                _date_cls.today().isoformat()
            )  # 系统当前日期 e.g. "2026-05-23"
            # memory_at: 转为日精度
            if current_time:
                try:
                    from datetime import datetime as _dt_cls

                    _memory_at = _dt_cls.fromisoformat(current_time).strftime(
                        "%Y-%m-%d"
                    )
                except (ValueError, TypeError):
                    _memory_at = _current_date
            else:
                _memory_at = _current_date

            # Select prompt based on input language
            from ..utils.lang_detect import is_chinese

            _enable_intention = bool(
                getattr(self._llm_config, "enable_intention", True)
            )
            # Route on the message TEXT, not the JSON envelope. In the chat
            # scene `content` is a JSON list of the new messages, and
            # detect_language() reads its first 10 alphanumeric characters
            # ("roleuserc") -> "en" -> the Chinese prompt was never used and
            # the model returned English facts for Chinese input. The parsed
            # messages let is_chinese(messages=...) route on the text itself.
            _messages = _message_list(content)
            if _messages is not None:
                _is_zh = is_chinese(messages=_messages)
            else:
                _is_zh = is_chinese(content=content)
            if _is_zh:
                from .prompts_zh import (
                    EXTRACT_PROMPT_ZH,
                    EXTRACT_PROMPT_ZH_NO_INTENTION,
                    EXTRACT_SYSTEM_PROMPT_ZH,
                    EXTRACT_FEW_SHOT_ZH,
                )

                _custom_sys = getattr(
                    self._llm_config, "extract_system_prompt_zh", None
                )
                if _custom_sys:
                    # client 自定义 system prompt：直接用，不追加内置 few-shot
                    system_prompt = _custom_sys
                else:
                    system_prompt = EXTRACT_SYSTEM_PROMPT_ZH
                    if getattr(self._llm_config, "few_shot_enabled", False):
                        system_prompt = system_prompt + EXTRACT_FEW_SHOT_ZH
                if not _enable_intention and not _custom_sys:
                    system_prompt = _system_prompt_without_intention(
                        system_prompt, lang="zh"
                    )
                if getattr(self._llm_config, "structure_metadata_enabled", False):
                    from .prompts_zh import STRUCTURE_METADATA_PROMPT_ZH
                    system_prompt += STRUCTURE_METADATA_PROMPT_ZH
                _user_tmpl = (
                    EXTRACT_PROMPT_ZH
                    if _enable_intention
                    else EXTRACT_PROMPT_ZH_NO_INTENTION
                )
                prompt = _user_tmpl.format(
                    content=content,
                    current_date=_current_date,
                    memory_at=_memory_at,
                    last_messages=_last_messages,
                )
            else:
                _custom_sys = getattr(
                    self._llm_config, "extract_system_prompt_en", None
                )
                if _custom_sys:
                    system_prompt = _custom_sys
                else:
                    system_prompt = EXTRACT_SYSTEM_PROMPT
                    if getattr(self._llm_config, "few_shot_enabled", False):
                        system_prompt = system_prompt + EXTRACT_FEW_SHOT_EN
                if not _enable_intention and not _custom_sys:
                    system_prompt = _system_prompt_without_intention(
                        system_prompt, lang="en"
                    )
                if getattr(self._llm_config, "structure_metadata_enabled", False):
                    system_prompt += STRUCTURE_METADATA_PROMPT_EN
                _user_tmpl = (
                    EXTRACT_PROMPT if _enable_intention else EXTRACT_PROMPT_NO_INTENTION
                )
                prompt = _user_tmpl.format(
                    content=content,
                    current_date=_current_date,
                    memory_at=_memory_at,
                    last_messages=_last_messages,
                )

            # 累计 token 统计
            if _log_llm_content_enabled():
                logger.debug(f"[extractor] user prompt: {prompt!r}")
            else:
                logger.debug(
                    "[extractor] user prompt hidden; len=%d sha256=%s",
                    len(prompt), _content_fingerprint(prompt),
                )
            total_tokens = 0
            total_prompt_tokens = 0
            total_completion_tokens = 0
            llm_finish_reason = ""
            llm_model = ""
            llm_response_id = ""
            llm_choices_count = 0
            llm_content_len = 0
            llm_tool_calls_count = 0
            llm_reasoning_tokens = 0
            llm_reasoning_len = 0
            all_tool_calls: List[Dict[str, Any]] = []
            all_tool_results: List[Dict[str, Any]] = []

            # 构造 messages（system + user）
            messages: List[Dict[str, Any]] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ]
            # 保存实际 prompt 用于 trace 日志
            _actual_user_prompt = prompt
            _actual_sys_prompt = system_prompt

            # 第一轮调用
            # NOTE: tools / tool_choice 已废弃 — 基础画像走 prompt + JSON basic_info
            # 字段，而不是 LLM function-calling tool（弱模型乱填）。
            # 若调用方传入 tools/tool_registry，会被忽略。
            response = await self.llm.complete_messages(
                messages=messages,
                max_tokens=self._llm_config.agent_max_tokens,
                tools=None,
                tool_choice=None,
                audit_stage="extract",
            )
            self._call_count += 1
            total_tokens += response.tokens_used
            total_prompt_tokens += response.prompt_tokens
            total_completion_tokens += response.completion_tokens

            raw_text = response.content or ""
            llm_finish_reason = getattr(response, "finish_reason", "") or ""
            llm_model = getattr(response, "model", "") or ""
            llm_response_id = getattr(response, "response_id", "") or ""
            llm_choices_count = getattr(response, "choices_count", 0) or 0
            llm_content_len = len(raw_text)
            llm_tool_calls_count = len(response.tool_calls or [])
            llm_reasoning_tokens = getattr(response, "reasoning_tokens", 0) or 0
            llm_reasoning_len = len(getattr(response, "reasoning_content", "") or "")

            # 多轮 tool-use loop（仅当传入 tool_registry 时启用）
            if response.tool_calls and tool_registry:
                from .tools.base import parse_tool_calls_from_json

                round_count = 0
                while response.tool_calls and round_count < MAX_TOOL_ROUNDS:
                    round_count += 1
                    logger.info(
                        f"[extractor] tool-use round {round_count}: "
                        f"{len(response.tool_calls)} tool call(s)"
                    )

                    all_tool_calls.extend(response.tool_calls)

                    # 追加 assistant 消息（含 tool_calls）
                    messages.append(
                        {
                            "role": "assistant",
                            "content": response.content or "",
                            "tool_calls": response.tool_calls,
                        }
                    )

                    # 执行每个 tool call
                    parsed_calls = parse_tool_calls_from_json(response.tool_calls)
                    for tc_raw, tc_parsed in zip(
                        response.tool_calls, parsed_calls or []
                    ):
                        tc_id = tc_raw.get("id") or ""
                        logger.info(
                            f"[extractor] tool call: name={tc_parsed.name}, "
                            f"tool_call_id={tc_id!r}, "
                            f"raw_keys={list(tc_raw.keys())}"
                        )
                        if not tc_id:
                            logger.error(
                                f"[extractor] tool_call_id is empty! "
                                f"raw tc: {json.dumps(tc_raw, ensure_ascii=False, default=str)[:500]}"
                            )
                        try:
                            t_res = await tool_registry.dispatch(
                                tc_parsed, tool_context or {}
                            )
                            result_content = json.dumps(
                                {
                                    "success": t_res.success,
                                    "data": t_res.data,
                                    "error": t_res.error,
                                },
                                ensure_ascii=False,
                                default=str,
                            )
                            all_tool_results.append(
                                {
                                    "tool": tc_parsed.name,
                                    "round": round_count,
                                    "success": t_res.success,
                                    "data": t_res.data,
                                    "error": t_res.error,
                                }
                            )
                        except Exception as tool_err:
                            logger.error(
                                f"[extractor] tool '{tc_parsed.name}' raised: {tool_err}",
                                exc_info=True,
                            )
                            result_content = json.dumps(
                                {"success": False, "error": str(tool_err)},
                                ensure_ascii=False,
                            )
                            all_tool_results.append(
                                {
                                    "tool": tc_parsed.name,
                                    "round": round_count,
                                    "success": False,
                                    "error": str(tool_err),
                                }
                            )

                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc_id,
                                "content": result_content,
                            }
                        )

                    # 下一轮 LLM 调用（已废弃 tools；保留 dispatch 路径仅用于
                    # 极端情况下 LLM 通过其他途径返回 tool_calls 的兼容处理）
                    response = await self.llm.complete_messages(
                        messages=messages,
                        max_tokens=self._llm_config.agent_max_tokens,
                        tools=None,
                        tool_choice=None,
                        audit_stage="extract",
                    )
                    self._call_count += 1
                    total_tokens += response.tokens_used
                    total_prompt_tokens += response.prompt_tokens
                    total_completion_tokens += response.completion_tokens
                    raw_text = response.content or ""
                    llm_finish_reason = getattr(response, "finish_reason", "") or ""
                    llm_model = getattr(response, "model", "") or ""
                    llm_response_id = getattr(response, "response_id", "") or ""
                    llm_choices_count = getattr(response, "choices_count", 0) or 0
                    llm_content_len = len(raw_text)
                    llm_tool_calls_count = len(response.tool_calls or [])
                    llm_reasoning_tokens = getattr(response, "reasoning_tokens", 0) or 0
                    llm_reasoning_len = len(getattr(response, "reasoning_content", "") or "")

                if round_count >= MAX_TOOL_ROUNDS and response.tool_calls:
                    logger.warning(
                        f"[extractor] tool-use loop hit max rounds ({MAX_TOOL_ROUNDS})"
                    )

                logger.info(
                    f"[extractor] tool-use loop done: {round_count} round(s), "
                    f"{len(all_tool_calls)} tool call(s), content_len={len(raw_text)}"
                )

            elif response.tool_calls and not tool_registry:
                # 没有 tool_registry 时，透传 tool_calls 给上层（向后兼容）
                all_tool_calls = response.tool_calls

            self._total_tokens += total_tokens

            # 空响应判断
            if (not raw_text or not raw_text.strip()) and not all_tool_calls:
                logger.warning(
                    "[extractor] LLM returned empty response "
                    f"finish_reason={llm_finish_reason!r} model={llm_model!r} "
                    f"response_id={llm_response_id!r} choices={llm_choices_count} "
                    f"content_len={llm_content_len} prompt_tokens={total_prompt_tokens} "
                    f"completion_tokens={total_completion_tokens} total_tokens={total_tokens} "
                    f"reasoning_tokens={llm_reasoning_tokens} reasoning_len={llm_reasoning_len}"
                    + (
                        " | HINT: reasoning exhausted the output budget "
                        "(completion_tokens == max_tokens). Disable thinking "
                        "(hy3: reasoning_effort=no_think) or raise MEMORY_AGENT_MAX_TOKENS."
                        if llm_reasoning_tokens > 0
                        and llm_finish_reason == "length"
                        else ""
                    )
                )
                return ExtractResult(
                    success=False,
                    error="LLM returned empty response",
                    error_code="EMPTY_RESPONSE",
                    raw_response=raw_text,
                    tokens_used=total_tokens,
                    prompt_tokens=total_prompt_tokens,
                    completion_tokens=total_completion_tokens,
                    llm_finish_reason=llm_finish_reason,
                    llm_model=llm_model,
                    llm_response_id=llm_response_id,
                    llm_choices_count=llm_choices_count,
                    llm_content_len=llm_content_len,
                    llm_tool_calls_count=llm_tool_calls_count,
                    llm_reasoning_tokens=llm_reasoning_tokens,
                    llm_reasoning_len=llm_reasoning_len,
                    _actual_prompt=_actual_user_prompt,
                    _actual_system_prompt=_actual_sys_prompt,
                )

            # 解析 JSON
            info: Dict[str, Any]
            if raw_text and raw_text.strip():
                parsed = self._parse_json(raw_text)
                if parsed is None:
                    if not all_tool_calls:
                        logger.warning(
                            f"[extractor] JSON parse failed: {raw_text[:200]}"
                        )
                        return ExtractResult(
                            success=False,
                            error=f"JSON parse failed: {raw_text[:200]}",
                            error_code="JSON_PARSE_FAILED",
                            raw_response=raw_text,
                            tokens_used=total_tokens,
                            prompt_tokens=total_prompt_tokens,
                            completion_tokens=total_completion_tokens,
                            llm_finish_reason=llm_finish_reason,
                            llm_model=llm_model,
                            llm_response_id=llm_response_id,
                            llm_choices_count=llm_choices_count,
                            llm_content_len=llm_content_len,
                            llm_tool_calls_count=llm_tool_calls_count,
                            _actual_prompt=_actual_user_prompt,
                            _actual_system_prompt=_actual_sys_prompt,
                        )
                    info = {}
                else:
                    info = parsed
            else:
                info = {}

            # 记录 tool 调用信息供上层（pipeline log / writer）使用
            if all_tool_calls:
                info["tool_calls"] = all_tool_calls
            if all_tool_results:
                info["tool_results"] = all_tool_results

            # One extraction call covers one current write and cannot establish
            # repetition across independent occurrences. Keep any bounded
            # Episode, but defer Series creation to reconcile where historical
            # candidate writes are visible.
            if getattr(self._llm_config, "structure_metadata_enabled", False):
                from ..structure import strip_series_from_assignments

                for _key in ("memory", "facts", "identity"):
                    _items = info.get(_key)
                    if not isinstance(_items, list):
                        continue
                    for _item in _items:
                        if isinstance(_item, dict) and "topic_assignments" in _item:
                            _item["topic_assignments"] = strip_series_from_assignments(
                                _item.get("topic_assignments")
                            )

            # Intention 开关关闭：即使模型仍返回 intentions，也丢弃（写侧另有硬闸）
            if not _enable_intention:
                if info.get("intentions"):
                    logger.info(
                        "[extractor] enable_intention=false, dropping %d intention(s)",
                        len(info.get("intentions") or []),
                    )
                info["intentions"] = []

            # basic_info is the canonical home for the user's name. Models may
            # still echo the same fact into L2 despite the prompt; remove only an
            # exact name-only statement, never a sentence carrying other facts.
            _basic = info.get("basic_info") if isinstance(info, dict) else None
            _name = str((_basic or {}).get("name") or "").strip()
            if _name:
                _name_key = re.escape(_name)
                _name_only = re.compile(
                    rf"^(?:the\s+user(?:'s|’s)\s+name\s+is|"
                    rf"the\s+user\s+is\s+(?:named|called))\s+{_name_key}[.!。]?$|"
                    rf"^(?:用户(?:的名字|名)?(?:是|叫))\s*{_name_key}[.!。]?$",
                    flags=re.I,
                )
                for _key in ("memory", "facts"):
                    _items = info.get(_key)
                    if isinstance(_items, list):
                        info[_key] = [
                            item for item in _items
                            if not (
                                isinstance(item, dict)
                                and _name_only.fullmatch(
                                    str(item.get("content") or "").strip()
                                )
                            )
                        ]

            # A strong model can still repeat one durable proposition twice
            # inside the same aggregated L2. Remove only near-contained complete
            # sentences; keep short fragments, dates and causal/detail clauses.
            for _key in ("memory", "facts"):
                _items = info.get(_key)
                if not isinstance(_items, list):
                    continue
                for _item in _items:
                    if not isinstance(_item, dict):
                        continue
                    _content = str(_item.get("content") or "").strip()
                    if _content:
                        _item["content"] = dedup_internal_memory_sentences(_content)

            tool_calls_only = (
                bool(all_tool_calls)
                and not info.get("memory")
                and not info.get("facts")  # legacy compat
                and not info.get("intentions")
                and not info.get("identity")  # legacy compat
            )

            result = ExtractResult(
                success=True,
                info=info,
                confidence=0.8,
                raw_response=raw_text,
                tokens_used=total_tokens,
                prompt_tokens=total_prompt_tokens,
                completion_tokens=total_completion_tokens,
                llm_finish_reason=llm_finish_reason,
                llm_model=llm_model,
                llm_response_id=llm_response_id,
                llm_choices_count=llm_choices_count,
                llm_content_len=llm_content_len,
                llm_tool_calls_count=llm_tool_calls_count,
                tool_calls_only=tool_calls_only,
                _actual_prompt=_actual_user_prompt,
                _actual_system_prompt=_actual_sys_prompt,
            )
            return result
        except Exception as e:
            # 带异常类型 + 完整 traceback，避免只剩笼统 "LLM_ERROR" 无从排查。
            # 若为 LLM 重试耗尽，RuntimeError 的 __cause__ 里含真实根因（超时/401/连接等），
            # 且 LLMRetryError.attempts 带每一次 attempt 的失败明细。
            logger.error(f"Extractor.extract failed: {type(e).__name__}: {e}", exc_info=True)
            _root = e.__cause__ or e
            _err_msg = f"{type(e).__name__}: {e}"
            if _root is not e:
                _err_msg += f" | root cause: {type(_root).__name__}: {_root}"
            _attempts = list(getattr(e, "attempts", []) or [])
            return ExtractResult(
                success=False,
                error=_err_msg,
                error_code="LLM_ERROR",
                attempts=_attempts,
                _actual_prompt=(
                    _actual_user_prompt if "_actual_user_prompt" in dir() else None
                ),
                _actual_system_prompt=(
                    _actual_sys_prompt if "_actual_sys_prompt" in dir() else None
                ),
            )

    async def extract_profile(
        self,
        content: str,
        context: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """专门提取用户画像 (profile 已包含 preferences)"""
        result = await self.extract(content, context)
        if result.success:
            return result.info.get("profile", {})
        return {}

    async def extract_entities(
        self,
        content: str,
        context: Dict[str, Any] = None,
    ) -> List[Dict[str, Any]]:
        """提取实体"""
        result = await self.extract(content, context)
        if result.success:
            return result.info.get("entities", [])
        return []

    # ================================================================
    # 工具方法
    # ================================================================

    def _parse_json(self, text: str) -> Optional[Dict[str, Any]]:
        """从 LLM 输出中解析 JSON。成功返回 dict，解析失败返回 None。"""
        text = text.strip()
        # 处理 markdown 代码块
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # 尝试找到 JSON 块
            match = re.search(r"\{[\s\S]*\}", text)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
        # 最后兜底：json_repair 修复常见 LLM 残缺输出（缺逗号/引号/未闭合等）
        try:
            from json_repair import repair_json

            repaired = repair_json(text, return_objects=True)
            if isinstance(repaired, dict) and repaired:
                logger.info("[extractor] JSON recovered via json_repair")
                return repaired
        except Exception as _re:
            logger.debug(f"[extractor] json_repair failed: {_re}")
        return None

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "call_count": self._call_count,
            "total_tokens": self._total_tokens,
            "avg_tokens_per_call": (
                self._total_tokens / self._call_count if self._call_count > 0 else 0
            ),
        }
