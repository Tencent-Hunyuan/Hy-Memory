"""English prompts (``en``).

The source of truth for every other language. Moved verbatim from the
modules that use them; those modules re-export the old names.
"""


# ================================================================
# Chat extraction (agent/extractor.py)
# ================================================================

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

STRUCTURE_METADATA_PROMPT = """

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

EXTRACT_FEW_SHOT = """
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

## Example 11 — Counter-example: small talk has no basic_info

New Messages:
[{"role": "user", "content": "Hi"},
 {"role": "assistant", "content": "Hello! The user's name is The user. How can I help?"}]
Memory Date: 2025-09-03

Output:
```json
{
  "memory": [],
  "intentions": [],
  "basic_info": {}
}
```
The user stated nothing about themselves. The assistant's line is not a user turn, and "The user" is a placeholder, not a name. Output `{}` — never `{"name": "The user"}` or `{"name": "user"}`.

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
  * HARD RULE: fill a field ONLY if the user stated it in a `user` turn.
    Anything said only by the assistant (including the assistant quoting an
    earlier memory such as "The user's name is X") does NOT count. If nothing
    qualifies, output `"basic_info": {{}}`.
  * NEVER write the field name itself or a placeholder as a value ("user",
    "The user", "用户", "name", "N/A", "unknown", "none", "-"). Omit the field.
  * Negative example: small talk such as `[user]: Hi` / `[assistant]: How can
    I help?`, or `[user]: Reply with exactly: PONG` → `"basic_info": {{}}`.
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
  * HARD RULE: fill a field ONLY if the user stated it in a `user` turn.
    Anything said only by the assistant (including the assistant quoting an
    earlier memory such as "The user's name is X") does NOT count. If nothing
    qualifies, output `"basic_info": {{}}`.
  * NEVER write the field name itself or a placeholder as a value ("user",
    "The user", "用户", "name", "N/A", "unknown", "none", "-"). Omit the field.
  * Negative example: small talk such as `[user]: Hi` / `[assistant]: How can
    I help?`, or `[user]: Reply with exactly: PONG` → `"basic_info": {{}}`.
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
# Chat reconcile (agent/reconciler.py)
# ================================================================

SEARCH_QUERY_PROMPT = """You are a search query generator. Given a list of newly extracted memories, generate a set of short search queries that can be used to find related existing memories in a vector database.

The goal is to maximize recall — find existing memories that are semantically related to the new memories, even if the wording is very different.

## New memories:
{new_memories}

## Instructions

Generate search queries that cover:
- Key topics, entities, and themes mentioned in the memories
- Rephrased or abstracted versions of the core concepts
- Related concepts that might exist in the user's memory store

Output a JSON array of query strings (5-15 queries, short and focused):

["query1", "query2", "query3", ...]

Output JSON array only, no other text."""

RECONCILE_PROMPT = """You are a memory management system.

Your task is to merge new memories into an existing memory base while keeping it:

* lossless
* non-contradictory
* compact
* highly retrievable

---

# Operations

## ADD

Use when the information is entirely new.

```json
{{
  "op": "ADD",
  "content": "...",
  "owner": "user",
  "tags": ["..."]
}}
```

---

## UPDATE

Use when:

* the new memory is compatible with an existing one
* they describe the same topic
* merging improves completeness or retrievability

Rules:

* merge losslessly
* preserve all useful information
* do NOT create duplicates
* if the two disagree on a value, keep the value from the memory with the later
  `memory_at`; never let an older memory overwrite a newer value

### Same occurrence vs. another occurrence

Use **UPDATE** only when the new memory adds detail to the same real-world
occurrence or durable fact already recorded.

If the same person does the same kind of activity/event again at another time
(another trip, game, injury, purchase, health scare, etc.), keep the occurrence
as a separate **ADD**. Preserve its own date and details. When an existing memory
is clearly another occurrence in that same concrete series, link it with
`related: [{{"memory_id":"...","relation":"same_series"}}]`.

Do not merge separate dated occurrences into one long count/timeline memory.
The retrieval layer builds timelines from the linked L2 memories.

```json
{{
  "op": "UPDATE",
  "memory_id": "...",
  "content": "...",
  "owner": "user",
  "tags": ["..."]
}}
```

---

## SUPERSEDE

Use ONLY when:

* the new memory contradicts an existing memory
* both cannot be true at the same time
* the old memory would now give the wrong current-state answer
* the new memory's `memory_at` is the same as or LATER than the existing memory's `memory_at`

Examples:

* city changed
* employer changed
* relationship status changed

Do NOT use for:

* added detail
* compatible preferences
* wording differences
* semantic equivalence
* a new memory whose `memory_at` is EARLIER than the existing memory's — it is an
  older state and must never replace a newer one (see "Time order")

Rules:

* content must contain ONLY the new state
* do NOT copy unchanged information
* the system keeps the old node and links it onto the evolution chain automatically; you don't need to handle history

```json
{{
  "op": "SUPERSEDE",
  "memory_id": "...",
  "content": "...",
  "supersede_reason": "...",
  "owner": "user",
  "tags": ["..."]
}}
```

### Folding several existing memories into one chain

If TWO OR MORE existing memories track the SAME dimension and the new memory is
the latest change on that dimension — its `memory_at` is later than every folded
memory's `memory_at` — you may fold them into a single evolution
chain. Use `memory_ids` (an ordered list, OLDEST → NEWEST) instead of a single
`memory_id`:

```json
{{
  "op": "SUPERSEDE",
  "memory_ids": ["<oldest_id>", "<newer_id>"],
  "content": "<only the newest state>",
  "supersede_reason": "...",
  "owner": "user",
  "tags": ["..."]
}}
```

The listed memories are chained oldest → newest and the new memory becomes the
chain head. `content` still describes ONLY the newest state — never copy the
older memories' text into it; they remain queryable in the chain history.

---

# Rules

* Preserve all meaningful information.
* Skip exact duplicates.
* Prefer merging related fragments.
* Separate dated occurrences stay separate ADDs; use `same_series` only for the
  same subject and same concrete activity/event type.
* Keep each memory focused on one coherent topic.
* Follow "Time order" below.
* **NEVER merge or supersede across different subjects.** The subject is whoever
  the content is about: "The user ..." vs "John ..." vs "Mary ...". Memories about
  different people are independent facts even when the sentence is otherwise
  identical — "The user likes coffee" and "John likes coffee" are two separate
  memories, never an UPDATE or a SUPERSEDE of each other.
* `owner` (`user`/`agent`): copy it from the memory being written. NEVER merge or supersede across different owners — a user's fact and the assistant's fact are distinct even on the same topic.
* A SUPERSEDE either targets ONE memory (`memory_id`) or folds several memories on the same dimension into one chain (`memory_ids`, oldest→newest). An UPDATE targets exactly ONE memory.
* Never touch the same existing memory from more than one op.
* Output language matches input memory language.

---

# Time order

Every memory, new or existing, has `memory_at`: when it happened. Memories are
NOT always written in the order they happened — history can be imported late,
and a memory can be added while older history is still being processed. A new
memory can therefore be OLDER than an existing one.

* Decide which memory is newer ONLY by `memory_at`. "New memories" means "being
  written now", not "more recent".
* An older memory must never replace a newer one: no SUPERSEDE of a newer
  memory, and no UPDATE that overwrites a newer value with an older one.
* If a new memory is older than an existing memory it conflicts with, keep the
  existing memory as the current state. ADD the new one as a past state, worded
  in the past with its time (for example "In 2023 the user lived in Shanghai"),
  or output nothing if it adds no information.
* `memory_at: null` means unknown time. Unknown is NOT newer: never SUPERSEDE a
  memory that has a known `memory_at` with one whose `memory_at` is null.
* Do not invent chronology.

---

# Optional: `related`

On any op you MAY add a `related` array recording how the memory being written
relates to an EXISTING memory. This does not change what gets stored — it only
records a link.

```json
{{
  "op": "ADD",
  "content": "...",
  "owner": "user",
  "tags": ["..."],
  "related": [{{"memory_id": "<existing_id>", "relation": "causes"}}]
}}
```

Only these four relations are allowed:

* `causes` — the two memories are in a cause/effect relationship
  (e.g. "started running daily" → "knee started hurting")
* `elaborates` — adds detail to, or is a later development of, the existing memory
* `contrasts` — they are in tension but BOTH still true (if one makes the other
  false, that is a SUPERSEDE, not a `contrasts` link)
* `same_series` — separate dated occurrences of the same concrete activity or
  event type for the same subject (for example, two different skiing trips)

Rules:

* Only link to memories that appear in "Existing memories" — never invent an id.
* Only emit a link when the relationship is **explicit and specific**. Two memories
  merely being about the same broad topic is NOT a relation — leave `related` out.
* `same_series` requires the same subject and a concrete repeated action/event;
  shared broad tags such as health, travel, sport, food, or work are insufficient.
* Omit the field entirely when there is nothing to link. Zero links is the normal case.
* Never use `related` as a substitute for UPDATE / SUPERSEDE.

---
{few_shot_section}
###################
Input Data
###################

Current date: {current_date}

## Existing memories

{existing_memories}

## New memories

{new_memories}

---

# Output

Return ONLY a JSON array inside a fenced ```json code block.

If no changes are needed:

```json
[]
```

Now produce the JSON array."""

RECONCILE_STRUCTURE_METADATA_PROMPT = """

Additional structural output contract:

For every ADD, UPDATE, or SUPERSEDE result, preserve or refine `topic_paths`,
`event_facet`, and `topic_assignments`. `event_facet` contains only `subject`,
`participants`, `action`, `objects`, `locations`, and `state`.
`topic_assignments` contains subject-specific `{subject, role, path}` entries;
each path segment is `{name, type}` and type is `topic|facet|series|episode`.
Types follow `topic -> facet -> series -> episode`; levels may be skipped but
types may not repeat or move backward.
Use Series only for repeated occurrences of one concrete activity/state line and
Episode only for one bounded real-world occurrence. Never infer these types from
depth, a broad shared tag, or unrelated co-occurrence. When an Existing memory
already has a semantically equivalent subject-specific path, reuse its exact node
names and types instead of creating a synonym fork. To request exact reuse, emit
`structure_references: [{"memory_id":"<existing_id>", "subject":"...",
"through":"topic|facet|series|episode"}]`. The referenced ID must appear in
Existing memories. `through` means copy that existing path prefix through the
named structural type; an explicitly authored deeper suffix remains new. Reuse
is forbidden when the subject, concrete activity/state line, or Episode boundary
differs. These fields describe the memory, never a future query form such as
"when", "how many", or "who".

Series is a reconcile-time relation: establish it only when the new memory and
at least one independent Existing memory are distinct occurrences of the same
concrete activity/state line. Multiple propositions from the current write do
not prove repetition.
"""

RECONCILE_FEW_SHOT = """
###################
Examples
###################

## Example 1 — One new memory: part conflicts with an existing chain, part is unrelated

Existing memories:
[
  {"memory_id": "m1", "content": "The user works as a product manager at Stripe.", "owner": "user", "history_versions": [{"content": "The user works as a product manager at a fintech startup."}]}
]
New memories:
[
  {"content": "The user has left Stripe and is now a product lead at Notion, and has recently taken up pottery on weekends.", "owner": "user"}
]

Output:
```json
[
  {"op": "SUPERSEDE", "memory_id": "m1", "content": "The user works as a product lead at Notion.", "supersede_reason": "employer changed from Stripe to Notion", "owner": "user", "tags": ["work"]},
  {"op": "ADD", "content": "The user has recently taken up pottery on weekends.", "owner": "user", "tags": ["hobby"]}
]
```
A SINGLE new memory carries two things: the job change (same dimension as the existing employment chain m1, which already has history → SUPERSEDE, becomes the new chain head) and an unrelated detail (pottery → ADD). Split the one memory into the right op per part — the new head describes ONLY the employment state, never the pottery.

## Example 2 — Fold two unlinked existing memories + the new change into one 3-node chain

Existing memories:
[
  {"memory_id": "a1", "content": "The user is learning to play the guitar.", "owner": "user", "memory_at": "2024-02-10 09:00"},
  {"memory_id": "a2", "content": "The user can now play a few full songs on the guitar.", "owner": "user", "memory_at": "2024-09-05 09:00"}
]
New memories:
[
  {"content": "The user now performs guitar at local open-mic nights.", "owner": "user"}
]

Output:
```json
[
  {"op": "SUPERSEDE", "memory_ids": ["a1", "a2"], "content": "The user performs guitar at local open-mic nights.", "supersede_reason": "guitar skill progressed: learning → playing songs → performing live", "owner": "user", "tags": ["music", "hobby"]}
]
```
a1 and a2 were never linked, but they track the same dimension (guitar progress) and the new memory is the latest step. Fold them with `memory_ids` ordered oldest→newest; they become a 3-node chain (a1 ← a2 ← new head). The head `content` describes ONLY the current state — it does not restate "learning" or "a few songs".

## Example 3 — New head must NOT copy the old memory's content

Existing memories:
[
  {"memory_id": "c1", "content": "The user lives in a rented one-bedroom apartment in Shibuya, Tokyo, pays ¥180,000/month, loves the 5-minute walk to the station, but complains the place is noisy at night and has no parking.", "owner": "user"}
]
New memories:
[
  {"content": "The user just bought and moved into a three-bedroom house in the suburbs of Yokohama with a garden and a two-car garage.", "owner": "user"}
]

Output:
```json
[
  {"op": "SUPERSEDE", "memory_id": "c1", "content": "The user bought and moved into a three-bedroom house in suburban Yokohama, with a garden and a two-car garage.", "supersede_reason": "residence changed from a rented Shibuya apartment to an owned Yokohama house", "owner": "user", "tags": ["housing"]}
]
```
The new memory SUPERSEDEs c1 and becomes the chain head. The head `content` describes ONLY the new Yokohama house. Do NOT drag the old apartment's distinctive details (Shibuya, ¥180,000 rent, the station walk, the noise/parking complaints) into the new node — those clearly belong to the old residence, not the new one. They stay in chain history and remain retrievable; copying them would make the head self-contradictory (renting in Shibuya AND owning in Yokohama at once).

## Example 4 — Different subjects are never merged, however similar the wording

Existing memories:
[
  {"memory_id": "d1", "content": "The user drinks black coffee every morning.", "owner": "user"}
]
New memories:
[
  {"content": "John drinks black coffee every morning.", "owner": "user"},
  {"content": "The user now drinks black coffee twice a day, morning and after lunch.", "owner": "user"}
]

Output:
```json
[
  {"op": "ADD", "content": "John drinks black coffee every morning.", "owner": "user", "tags": ["food"]},
  {"op": "UPDATE", "memory_id": "d1", "content": "The user drinks black coffee twice a day, in the morning and after lunch.", "owner": "user", "tags": ["food"]}
]
```
The two new memories are near-identical in wording but have DIFFERENT subjects. "John ..." is about another person, so it is a plain ADD — never an UPDATE or SUPERSEDE of d1, even though d1 says almost the same thing about the user. Only the memory whose subject is also "The user" may merge with d1.

## Example 5 — Recurring event: bump count and keep a timeline (UPDATE, not ADD)

Existing memories:
[
  {"memory_id": "i1", "content": "John injured his ankle last season and needed time off plus physical therapy.", "owner": "user", "memory_at": "2023-05-21 19:48"}
]
New memories:
[
  {"content": "John twisted his ankle again during a recent game.", "owner": "user", "memory_at": "2023-10-13 13:50"}
]

Output:
```json
[
  {"op": "UPDATE", "memory_id": "i1", "content": "John has injured his ankle two times: first last season (around the 2022–2023 season), needing time off and physical therapy; second during a game around 2023-10-13.", "owner": "user", "tags": ["health", "basketball", "injury"]}
]
```
Same event type happening again → UPDATE the old memory. State the **count** and list occurrences with times when known. Do NOT ADD a second "John injured his ankle" fact, and do NOT SUPERSEDE (the first injury did not stop being true).
"""


# ================================================================
# Working / coding memory (coding/)
# ================================================================

CODING_EXTRACT_PROMPT = """\
You extract durable WORKING memories from a task-execution session segment.
A "working" session is any hands-on task where the user operates a computer /
system through tools — this covers BOTH software/code work (writing, debugging,
refactoring, building, deploying, API/library usage) AND system & operations
work (running shell/PowerShell commands, disk/storage analysis or cleanup, file
& folder operations, installing/configuring software, environment/device setup,
hardware/disk health inspection, backup, scheduling/automation, data processing).

You produce TWO layers from the SAME segment:

  EXPERIENCES (procedural, "how to do it") — reusable method, extracted only
  when it clears the VALUE BAR below. A (task, search_keys, solution,
  boundary_envs) bundle.

  FACT (episodic, "what happened this time") — EXACTLY ONE record of THIS
  session: what the user wanted, how it was gone about, how it came out. No
  value bar, but there is never more than one.

The two layers answer different questions and are stored separately. A method
that generalizes goes in EXPERIENCES; the story of this particular session —
only true for this system / this project / right now — goes in the FACT.

═══════════════════════════════════════════════════════════════════
VALUE BAR (EXPERIENCES ONLY) — DO NOT cross this lightly.
═══════════════════════════════════════════════════════════════════
A memory MUST satisfy ALL of:
  (a) Solves a problem likely to recur for the same user
  (b) Solution is non-trivial: combines multiple pieces of info / steps, OR
      carries a decision rationale, OR captures a hard-won workaround, OR is a
      reusable procedure/recipe for an operational task (e.g. how to analyze &
      clean up disk usage, how to batch-organize files, how to inspect hardware
      health, how to set up a scheduled job)
  (c) A future agent / user would prefer looking it up over re-discovering it

IMPORTANT — analysis / diagnostic / investigation tasks COUNT:
A task does NOT have to make a change or "fix" something to be worth remembering.
Read-only / analytical / troubleshooting work is valuable when its PROCEDURE or
CONCLUSION is reusable. Explicitly extract:
  - Analysis / diagnostic recipes: the reusable procedure + what it revealed
    (e.g. "how to analyze which folders eat C-drive space" — the scan commands,
    the gotchas hit, and the finding). "It only produced a report, no change"
    is NOT a reason to skip — the reusable METHOD is the memory.
  - Investigation outcomes, INCLUDING negative / not-found results
    (e.g. "searched folder X across C: and D: with rg / fs_search / SQL index —
    not found anywhere"). A recorded dead-end saves the next agent from repeating
    the same fruitless search, so it clears the bar.
Do NOT reject a memory merely because "the assistant could regenerate the
commands" or "nothing was actually modified". If the procedure or the conclusion
would save future effort, keep it.

DO NOT extract:
  - Truly trivial single-step ops with no reusable procedure ("rename one file",
    "git status", "run ls once")
  - One-off issues already resolved with no transferable lesson
  - Information that is already in the repo/system (don't duplicate package.json)
  - Per-PR / per-session throwaway tasks unless they encode a reusable pattern
  - The act of ASKING THE USER for missing information. Requesting a parameter,
    listing what is unknown, or eliciting required fields is conversational
    behaviour, not a method: every agent already knows how to ask a question, so
    there is nothing to look up later. Never emit an experience whose task or
    search_keys are shaped like "how to ask for X", "gather the missing Y",
    "eliciting required fields", "what to clarify before starting". Note this
    stays out of EXPERIENCES only — that the session ended waiting on the user
    is real state and belongs in the FACT.

If the segment made no substantive progress because it stalled waiting on the
user — the assistant only asked for parameters, or was blocked before doing any
real work — emit "experiences": [] and write the FACT alone. Nothing was
learned yet, so there is no method to store. This matters more than it looks:
an experience distilled from a stall reads as a legitimate skill on retrieval,
and a later segment of the same task will match it and ask the user again
instead of proceeding, so the stall reproduces itself for the rest of the
session. Judge substance by what the tools actually did, not by whether the
turn produced fluent text.

Heuristic: if the user (or a future agent doing the same kind of task) would
want this written down instead of figuring it out again, keep it. A multi-step
operational procedure (even a system/ops or read-only analysis/search one, not
just code) usually qualifies. Only drop it if it is a single command any user
would find via `--help` in 30 seconds AND carries no decision/gotcha worth
remembering.

═══════════════════════════════════════════════════════════════════
EXPERIENCE FIELDS
═══════════════════════════════════════════════════════════════════

task          — Query-shaped name; phrase as how a user would later ASK.
                "publish a release to the xx platform" not
                "publish_release_internal"
                Self-contained (no "this", "the file", "we").

search_keys   — 0 to 5 alternative phrasings capturing important aspects of
                solution that `task` alone wouldn't match. Phrase as questions
                or topics future users would ASK ABOUT. Distinct from task.
                Omit if task already covers everything.

solution      — Complete, self-contained content the user actually needs:
                commands, credential locations (NOT credential VALUES),
                file paths, ordered steps, gotchas, decision rationale.
                Aggregate multi-turn end-to-end work into ONE solution if it
                forms a coherent task; split into multiple memories only when
                the user did unrelated tasks.

boundary_envs — Concrete pinning that solution depends on, multi-line "key: value":
                  runtime / SDK / library + version
                  API endpoint or method signature
                  external service / middleware version
                  env var NAME (not value)
                  config file path
                  host / cluster / region identifier
                If solution is environment-agnostic, leave as "" (empty).
                Purpose: when the underlying SDK/middleware upgrades, future
                consumers can inspect this to decide if solution still applies.

confidence  — 0.0 to 1.0
  1.0   user explicitly stated this as a rule / instruction
  0.7   inferred with good evidence from tool actions
  0.5   weakly inferred; one observation; uncertain

═══════════════════════════════════════════════════════════════════
FACT FIELD — EXACTLY ONE per segment, no value bar
═══════════════════════════════════════════════════════════════════

You write ONE fact for the whole segment — a recoverable state record for this
session, not a polished retrospective and never a list of separate findings. A
future agent must be able to resume from it without mistaking a suggestion,
inference, or partial search for something verified.

subject    — WHO or WHAT this session is about: the single entity the work was
             performed on or for. One short noun phrase, no sentence, no verb.
             Pick the most specific entity that the session is *about*, not the
             tool it used:
               a person's name when the session serves one named person
                 ("Dylan", "Matthew")
               the file / module / symbol when the work targets code
                 ("auth/middleware.ts", "parse_plan_text")
               the service / host / device / drive for ops work
                 ("D:\\WXWork", "redis-cache-prod", "ci.yml")
             This is a HARD KEY, not prose: two sessions about different people
             or different files MUST get different subjects, and two sessions
             about the same one MUST get the same string, spelled identically.
             Prefer the exact identifier as it appears in the trace over a
             description of it. If the segment genuinely has no single subject,
             output "".

             Why it matters: subject is the gate that decides whether a later
             session is allowed to overwrite this one as "the same task,
             continued". Get it wrong and one person's state silently replaces
             another's.

statement  — A compact record of THIS session. Two parts, and the second is the
             one that gets lost.
             Preserve task-scoped givens and concrete constraints instead of
             replacing them with a generic evaluation of the outcome. State
             each rule once: repeated instruction text competes with the data
             the memory must retain.

             PART 1 — what happened, about 300 latin characters (~150 CJK).
             Start with the specific goal and object ("reclaim disk space taken
             by D:\\WXWork", "locate the new_file_search folder"), not a generic
             "fix a bug" / "find a file". Then roughly what was inspected, tried,
             or changed. Then how it ended, with the semantics of exactly one of:
             completed, partially completed, failed, blocked, or
             still awaiting verification. Include negative results and dead ends
             without overstating them.

             PART 2 — the TASK-SCOPED GIVENS: everything the WHOLE task runs on,
             even when this segment did not establish it and even when it feels
             too elementary to state. Bindings (what each name/symbol/label
             stands for), fixed values the task supplies rather than derives,
             conventions adopted (indexing base, units, timezone, defaults), and
             this segment's own result WITH its value ("x = 7", not "the value
             was computed").

             Write PART 2 as data, one item per line, values as given:
               retry budget = 5; timeout 30s; index starts at 0
               step 3 input: n=64, tolerance 1e-6
               slot B: type=private, cost 688.7-721.3 per unit
             A line should be almost entirely values. Keep the trace's notation —
             symbols, units, numbers, spellings — and drop its sentences: strip
             the phrases that announce a given rather than being one ("I would
             prefer", "it should be somewhere between … and …"). They repeat once
             per item, so a 15-item list pays for them 15 times.
             Carry them on EVERY fact; never write "as above" or "same as the
             previous session" — retrieval returns a handful of facts by
             similarity and the referent may not be among them.

             Aim under 1200 characters total. If PART 2 does not fit, fix the
             notation, never the coverage: statement is the retrieval vector AND
             the only field a later segment can read (details is neither embedded
             nor injected), so anything cut from it is gone. Compress the wording
             around a given; never the given.

             The test: if the next segment cannot see the original material, can
             it continue from this record alone? Only a yes counts. A missing
             binding does not fail loudly — the name still looks meaningful, so
             it gets read with its general-usage meaning and the answer that
             follows is fluent and about a different question.

             LANGUAGE — same language as the session trace: statement is the
             retrieval vector and queries arrive in the user's working language,
             so another language pays a cross-lingual penalty on every lookup.

             EVIDENCE FIDELITY — record only actions actually executed and
             evidence explicitly present in the trace. Preserve material
             commands, flags, arguments, errors, and numbers verbatim.
             NEVER complete a command with customary flags, and never turn
             advice or plans into actions:
               "ran Get-ChildItem -Force" → "ran Get-ChildItem"
                 (the trace did not contain `-Force`; do not invent it)
             Scope every negative finding to what was actually searched, and
             enumerate EVERY explicit coverage limit that could invalidate it,
             especially permission failures and depth limits: keep the roots,
             filters, and depth that were in effect ("not found under C:\\ and
             D:\\ at depth 3; some directories were not covered due to Access
             denied"), never widen it to "does not exist". Do not omit one limit
             to make the record prettier.
             Never promote a partial search to global absence.
             A blocked attempt keeps its blocker.
             Preserve the distinction among attempted, observed, verified,
             inferred, and unresolved — express it naturally ("tried … but was
             blocked by Access denied", "suspected …, not yet verified"), not as
             labels. Without tool output or explicit confirmation, NEVER claim a
             fix succeeded, a cause was established, or a path does not exist.

             Do NOT record greetings or pure chatter — that is the only case
             getting "fact": null. A segment that merely stated requirements, or
             stalled before doing work, still established something.

details    — The session's concrete OUTPUT, copied over verbatim. `statement` is
             a summary and a summary necessarily drops the individual values; a
             later lookup almost always wants one specific value, not the gist.
             So whatever addressable result this session produced, reproduce it
             here in its original form and structure:
               a plan / schedule / itinerary → keep every line, every day, every
                 slot, and every price exactly as written
               a config or code block → keep the block
               a command's material output → keep the lines that carry the
                 findings (sizes, counts, versions, error text)
               a structured result (table, list of records) → keep the rows
             Preserve the original layout — line breaks, `Day 1:` / key: value
             prefixes, units, currency symbols, ids. Do not re-summarize, do not
             reorder, do not round numbers, do not translate.

             Copy ONLY what is present in the trace. Never fill a gap: if a slot
             was left unfilled, leave it unfilled or say so — an invented value
             is worse than a missing one, because it will be retrieved and used.
             Cap around 4000 characters; if the output is longer, keep the parts
             carrying concrete values and drop decorative prose, noting what was
             dropped.

             "" when the session produced no such artifact (pure investigation
             with no findings yet, or a stall waiting on the user).

             This field is NOT part of the retrieval vector — `statement` and
             `subject` do the finding, `details` supplies the value once found.
             So do not write retrieval bait here, and do not duplicate the
             summary; write the raw thing.

files      — 0..N absolute or repo-relative FILE OR DIRECTORY paths actually
             operated on or investigated during this session. Keep investigated
             roots such as `C:\\` and `D:\\`. Only include paths that were
             actually accessed/searched, not paths merely suggested or planned;
             [] if none apply.
═══════════════════════════════════════════════════════════════════
HOST CONTEXT (given by SDK)
═══════════════════════════════════════════════════════════════════
- workspace_id present: {workspace_present}
- branch present: {branch_present}

When no workspace_id is present, the solution cannot rely on unstated
project-local context — spell out any repo / path / service it needs inside
`solution` or `boundary_envs` so the memory stays usable on its own.

═══════════════════════════════════════════════════════════════════
CONVERSATION
═══════════════════════════════════════════════════════════════════
{conversation_block}

═══════════════════════════════════════════════════════════════════
OUTPUT (strict JSON only, no markdown)
═══════════════════════════════════════════════════════════════════
{{
  "experiences": [
    {{
      "task": "...",
      "search_keys": ["...", "..."],
      "solution": "...",
      "boundary_envs": "...",
      "confidence": 0.9
    }}
  ],
  "fact": {{
    "subject": "...",
    "statement": "...",
    "files": ["..."],
    "details": "..."
  }}
}}

DEFAULT TO EXTRACTING. When the segment did real work, you MUST output at least
one experience — even if the task looks simple or was a one-shot success, still
capture the reusable essence of what was done (the method / command / how-to).
When the session is thin, the single most valuable experience is enough.

The ONE exception is the stall case above: if the segment did no real work
because it was blocked asking the user for missing information, output
"experiences": [] and write only the fact. Do not manufacture an experience to
satisfy this instruction — a fabricated one ("how to ask for the departure
city") is worse than none, because it is later retrieved and followed. Empty is
allowed ONLY for that reason, never as a shortcut when work was actually done.

"fact" is a SINGLE object, never a list — one record per session.

ALWAYS write the fact. The bar for the fact is not "was this valuable" but "did
anything at all happen", and a segment reaching you has already cleared that.
This includes segments that look like setup rather than work: a single
user message stating requirements, parameters, or a starting state, with no
tools run, is exactly the material a later session cannot reconstruct on its own
— 把需求、参数、起始状态连同情形一起写进 `statement`（紧凑记法，不要照抄对话原句），
不要推给 `details`（`details` 不进检索向量，读取侧也不再注入它）。Use
"fact": null ONLY for greetings and pure chatter with no content whatsoever.
When in doubt, write the fact: an unneeded record costs one row, while a dropped
one costs every later session that needed it and has no way to know it existed.
"""

CODING_EXTRACT_PROMPT_CODING = """\
╔═══════════════════════════════════════════════════════════════════════════╗
║ HARD CONSTRAINT — read this before anything else, it overrides any         ║
║ phrasing later in this prompt that sounds like it wants a narrative.       ║
║                                                                           ║
║ You are NOT writing a report of what a person did. You are writing what a  ║
║ FUTURE AGENT needs in order to act. That agent already has its own task;   ║
║ it does not care what this user wanted. It needs to know: what was decided ║
║ here, at which code coordinates, what was tried and rejected, and whether  ║
║ it was verified.                                                          ║
║                                                                           ║
║ Concretely, in every `statement` and every `solution`:                     ║
║   · The grammatical subject is the CODE or THE DECISION — never "the user" ║
║     and never "the agent". Do not write "The user attempted to…",          ║
║     "The user wanted…", "The agent then checked…".                        ║
║   · Never close with a bookkeeping phrase like "the session was completed", ║
║     "the error was noted", "no solution was found yet". Close with the     ║
║     decision and its verification status. If nothing was decided, close    ║
║     with what was RULED OUT and the next unexplored lead — that is the     ║
║     reusable part.                                                        ║
║   · Keep the concrete symptom (error string, failing command, wrong        ║
║     output) — that is what a future search will match on.                 ║
║                                                                           ║
║ bad:  "The user attempted to write a QTable to RST with header_rows and    ║
║        hit a TypeError. The session was completed with the error noted."   ║
║ good: "ascii.rst's RST writer rejects header_rows: `tbl.write(..., \\        ║
║        format='ascii.rst', header_rows=['name','unit'])` raises TypeError   ║
║        because RST.__init__ does not forward it, unlike ascii.fixed_width   ║
║        which accepts it. No fix written — the writer needs the same         ║
║        header_rows plumbing as fixed_width."                              ║
╚═══════════════════════════════════════════════════════════════════════════╝

You extract durable WORKING memories from a task-execution session segment.
A "working" session is any hands-on task where the user operates a computer /
system through tools — this covers BOTH software/code work (writing, debugging,
refactoring, building, deploying, API/library usage) AND system & operations
work (running shell/PowerShell commands, disk/storage analysis or cleanup, file
& folder operations, installing/configuring software, environment/device setup,
hardware/disk health inspection, backup, scheduling/automation, data processing).

You produce TWO layers from the SAME segment:

  EXPERIENCES (procedural, "how to do it") — reusable method, extracted only
  when it clears the VALUE BAR below. A (task, search_keys, solution,
  boundary_envs) bundle.

  FACT (episodic, "what was established this time") — EXACTLY ONE record of THIS
  session. It is NOT a narrative of what the user did. It records, in this order:
  the task and its concrete symptom (this is what makes it findable later), the
  code coordinates, WHAT WAS SETTLED ON and what was rejected, and how far it was
  verified. No value bar, but there is never more than one.

The two layers answer different questions and are stored separately. A method
that generalizes goes in EXPERIENCES; the story of this particular session —
only true for this system / this project / right now — goes in the FACT.

═══════════════════════════════════════════════════════════════════
VALUE BAR (EXPERIENCES ONLY) — DO NOT cross this lightly.
═══════════════════════════════════════════════════════════════════
A memory MUST satisfy ALL of:
  (a) Solves a problem likely to recur for the same user
  (b) Solution is non-trivial: combines multiple pieces of info / steps, OR
      carries a decision rationale, OR captures a hard-won workaround, OR is a
      reusable procedure/recipe for an operational task (e.g. how to analyze &
      clean up disk usage, how to batch-organize files, how to inspect hardware
      health, how to set up a scheduled job)
  (c) A future agent / user would prefer looking it up over re-discovering it

IMPORTANT — analysis / diagnostic / investigation tasks COUNT:
A task does NOT have to make a change or "fix" something to be worth remembering.
Read-only / analytical / troubleshooting work is valuable when its PROCEDURE or
CONCLUSION is reusable. Explicitly extract:
  - Analysis / diagnostic recipes: the reusable procedure + what it revealed
    (e.g. "how to analyze which folders eat C-drive space" — the scan commands,
    the gotchas hit, and the finding). "It only produced a report, no change"
    is NOT a reason to skip — the reusable METHOD is the memory.
  - Investigation outcomes, INCLUDING negative / not-found results
    (e.g. "searched folder X across C: and D: with rg / fs_search / SQL index —
    not found anywhere"). A recorded dead-end saves the next agent from repeating
    the same fruitless search, so it clears the bar.
Do NOT reject a memory merely because "the assistant could regenerate the
commands" or "nothing was actually modified". If the procedure or the conclusion
would save future effort, keep it.

DO NOT extract:
  - Truly trivial single-step ops with no reusable procedure ("rename one file",
    "git status", "run ls once")
  - One-off issues already resolved with no transferable lesson
  - Information that is already in the repo/system (don't duplicate package.json)
  - Per-PR / per-session throwaway tasks unless they encode a reusable pattern

Heuristic: if the user (or a future agent doing the same kind of task) would
want this written down instead of figuring it out again, keep it. A multi-step
operational procedure (even a system/ops or read-only analysis/search one, not
just code) usually qualifies. Only drop it if it is a single command any user
would find via `--help` in 30 seconds AND carries no decision/gotcha worth
remembering.

═══════════════════════════════════════════════════════════════════
CODE WORK — what a future agent cannot re-derive, so it MUST be captured
═══════════════════════════════════════════════════════════════════
When the segment is software work on a repository, the reusable method is only
half of it. The other half is WHERE the work lives and WHICH VERSION won. Those
cannot be recovered by reading the code later — the code shows the current state,
not which branch it sits on, nor what was already rejected.

This section does NOT ask for a longer record. It asks you to SPEND THE SAME
BUDGET on higher-value tokens: drop restated goals, narration of what the
assistant "then decided to", and pasted code you are only echoing; spend those
characters on the four items below instead. A record that omits the branch but
quotes 30 lines of diff is a bad trade.

1. BRANCH / WORKING LOCATION — verbatim, in the fact `statement`. If the segment
   mentions or checks out `feat/x` / `fix/y`, that token MUST appear. An agent
   that resumes on the wrong branch produces changes that are silently thrown
   away, so this one token outranks every other detail.

2. WHICH VERSION WON — when the user changed their mind, name the winner and mark
   the loser in one clause: "settled on A; the interim B was dropped". Not both in
   full — one clause. Keeping only the latest is wrong: an agent that meets the
   rejected code must recognize it, or it will "fix" things back to the discarded
   variant. Phrases like `scratch that / go back to / actually, let's / no, not that`
   mark this: what follows is of record, what precedes is superseded.

3. EXACT COORDINATES, NOT PASTED CODE — repo-relative path
   (`qutebrowser/utils/debug.py`, never bare `debug.py`) plus the function /
   class touched. If a signature changed, give the new signature. If a constant,
   regex or literal was decided, quote THAT verbatim — but quote only it, not the
   surrounding block. One accurate line beats a paragraph of approximation.

4. VERIFIED vs MERELY WRITTEN — four words is enough ("tests run and passing" /
   "confirmed by git diff only, tests not run"). "the edit is in" and "the edit is in
   and tested" lead a future agent to do very different things next.

Anti-pattern — summarizing the goal and dropping the coordinates:
  bad:  "refactored the colour parsing logic to be cleaner."
  good: "on feat/q4, in qutebrowser/config/configtypes.py QtColor._parse_value:
         settled on `mult = 360.0 if kind == 'h' else 255.0`; the interim version that
         used 255.0 for everything was dropped. Tests not run."
  (the good one is SHORTER than most bad ones — it just spends every character
   on something that cannot be re-derived)

═══════════════════════════════════════════════════════════════════
EXPERIENCE FIELDS
═══════════════════════════════════════════════════════════════════

task          — Query-shaped name; phrase as how a user would later ASK.
                "publish a release to the xx platform", not "publish_release_internal"
                Self-contained (no "this", "the file", "we").

search_keys   — 0 to 5 alternative phrasings capturing important aspects of
                solution that `task` alone wouldn't match. Phrase as questions
                or topics future users would ASK ABOUT. Distinct from task.
                Omit if task already covers everything.

solution      — Complete, self-contained content the user actually needs:
                commands, credential locations (NOT credential VALUES),
                file paths, ordered steps, gotchas, decision rationale.
                Aggregate multi-turn end-to-end work into ONE solution if it
                forms a coherent task; split into multiple memories only when
                the user did unrelated tasks.

boundary_envs — Concrete pinning that solution depends on, multi-line "key: value":
                  runtime / SDK / library + version
                  API endpoint or method signature
                  external service / middleware version
                  env var NAME (not value)
                  config file path
                  host / cluster / region identifier
                If solution is environment-agnostic, leave as "" (empty).
                Purpose: when the underlying SDK/middleware upgrades, future
                consumers can inspect this to decide if solution still applies.

confidence  — 0.0 to 1.0
  1.0   user explicitly stated this as a rule / instruction
  0.7   inferred with good evidence from tool actions
  0.5   weakly inferred; one observation; uncertain

═══════════════════════════════════════════════════════════════════
FACT FIELD — EXACTLY ONE per segment, no value bar
═══════════════════════════════════════════════════════════════════

You write ONE fact for the whole segment — a recoverable state record for this
session, not a polished retrospective and never a list of separate findings. A
future agent must be able to resume from it without mistaking a suggestion,
inference, or partial search for something verified.

statement  — A compact summary of THIS session. It has two jobs at once, and it
             must do BOTH: it is the retrieval vector (a future agent finds it by
             searching with a fresh issue report, phrased the way a user phrases a
             problem), and it is the payload that agent then acts on. So:

             OPEN with the task and the symptom, in the words a user would use.
                This is what makes it findable — keep the surface form close to how
                the problem would be reported. Name the specific goal and object
                ("reclaim disk space under D:\\WXWork", "adjust the hue multiplier
                in QtColor._parse_value on feat/q4", "locate the new_file_search
                folder"), never a generic "fix a bug" / "look for a file". Include
                the concrete symptom: the error string, the failing command, the
                wrong output.

             THEN give the coordinates and the decision.
                repo-relative path, symbol, and branch for code work. State what was
                SETTLED ON, and in the same clause mark what was dropped
                ("settled on X; the interim Y was dropped"). This is the part that
                cannot be re-derived by reading the code — the code only shows the
                winner, never that Y was tried and rejected. An agent that meets the
                rejected variant later must be able to recognize it, or it will
                "fix" the code back to the discarded version.

             CLOSE with the evidence status.
                Exactly one of: completed, partially completed, failed, blocked, or
                still awaiting verification — plus how it was checked ("tests run and
                passing" / "confirmed by git diff only, tests not run"). Include
                negative results and dead ends without overstating them.

             ⚠️ Never stop at the narrative. "The user tried X and hit error Y; no
             solution found yet" is a wasted record: it retrieves fine but gives a
             future agent nothing to act on. If the session genuinely reached no
             decision, say what was ruled out and what the next unexplored lead is —
             that is the reusable part.

             The subject of the sentences should be the code and the decision, not
             "the user". 2 to 4 sentences, flowing prose, not labelled sections.
             Hard ceiling ~400 latin characters (~200 CJK characters).

             EVIDENCE FIDELITY — mandatory:
             - Record only actions actually executed and evidence explicitly
               present in the trace. Preserve material commands, flags,
               arguments, errors, and numbers verbatim. NEVER complete a
               command with customary flags or turn advice/plans into actions.
             - Preserve the distinction among attempted, observed, verified,
               inferred, and unresolved. Do not add those labels to the prose;
               express their meaning naturally, e.g. "tried … but was blocked by
               Access denied" or "suspect … , not yet verified".
             - Without tool output or explicit confirmation, NEVER claim a fix
               succeeded, a cause was established, a file/folder does not
               exist, or tests passed.
             - Scope every negative finding to what was actually searched and
               enumerate EVERY explicit coverage limit that could invalidate
               it, especially permission failures and maximum search depth:
               "not found within the searched parts of C:\\ and D:\\; some
               directories were skipped on Access denied, and the search was also
               depth-limited". Do not omit one limit to make the
               summary prettier. Never promote a partial search to global
               absence.
             - State causality as certain only when the trace directly verifies
               it. Otherwise retain the hedge ("suspected" / "inferred" / "not yet
               verified"); do not rewrite an
               incorrect test assumption as the system's root cause.

             CRITICAL — this text IS the retrieval vector, and it is the only
             thing a future query gets matched against. A generic summary is
             worthless, because EVERY session looks like a generic summary: it
             would match every query and answer none of them. So you MUST name
             the concrete anchors that make THIS session findable — real file
             paths, function / symbol names, branch names, commands, error
             strings, versions, config keys and values, host / service names.
             If someone later searches for any one of those specifics, this
             summary has to be the thing that comes back.
               good: "fixing a failing `npm run build` in CI. Read
                      .github/workflows/ci.yml and package.json; traced it to webpack
                      5.74 hitting ERR_OSSL_EVP_UNSUPPORTED under node 18. Bumping the
                      workflow's node to 20 made the build pass; node 18 still
                      reproduces locally."
               good: "on branch feat/q0, in qutebrowser/utils/debug.py signal_name:
                      PyQt<5.11 only exposes the name via repr(sig), and repr comes in
                      both a bracketed and a parenthesised form. Settled on two patterns
                      tried in order with fullmatch, breaking on the first hit; the
                      single-regex version was dropped. Confirmed by git diff, tests not
                      run."
               bad:  "user wanted a build bug fixed, the agent looked at some config
                      files, changed some code, and fixed it."
                      (no path, no command, no error string, no version — nothing
                       to retrieve on; every session reads exactly like this)
               bad:  "fixed the signal-name parsing compatibility issue on a feature
                      branch."
                      (which branch? which file? which function? which of the two
                       competing versions won? nothing here survives being
                       retrieved a week later)
             Fidelity examples (bad → good):
               "ran Get-ChildItem -Force" → "ran Get-ChildItem"
                 (the trace did not contain `-Force`; do not invent it)
               "confirmed new_file_search does not exist" →
                 "new_file_search was not found within the searched parts of C:\\ and
                  D:\\; some directories were skipped on Access denied or by the depth
                  limit, so the investigation is only partly done"
               "identical payloads cause a spurious lock error" →
                 "the verification script assumed the last writer is always a fixed
                  rank; that assumption does not hold, so whether there is a lock bug
                  is still unverified"

             Do NOT record greetings or pure chatter. If the segment established
             nothing at all, output "fact": null.

files      — 0..N absolute or repo-relative FILE OR DIRECTORY paths actually
             operated on or investigated during this session. Keep investigated
             roots such as `C:\\` and `D:\\`. Only include paths that were
             actually accessed/searched, not paths merely suggested or planned;
             [] if none apply.

═══════════════════════════════════════════════════════════════════
HOST CONTEXT (given by SDK)
═══════════════════════════════════════════════════════════════════
- workspace_id present: {workspace_present}
- branch present: {branch_present}

When no workspace_id is present, the solution cannot rely on unstated
project-local context — spell out any repo / path / service it needs inside
`solution` or `boundary_envs` so the memory stays usable on its own.

═══════════════════════════════════════════════════════════════════
CONVERSATION
═══════════════════════════════════════════════════════════════════
{conversation_block}

═══════════════════════════════════════════════════════════════════
OUTPUT (strict JSON only, no markdown)
═══════════════════════════════════════════════════════════════════
{{
  "experiences": [
    {{
      "task": "...",
      "search_keys": ["...", "..."],
      "solution": "...",
      "boundary_envs": "...",
      "confidence": 0.9
    }}
  ],
  "fact": {{
    "statement": "...",
    "files": ["..."]
  }}
}}

MANDATORY: You MUST output at least ONE experience. Do NOT return an empty list.
Even if the task looks simple or is a one-shot success, still capture the
reusable essence of what was done (the method / command / how-to). Only the
single most valuable experience is required when the session is thin — but never
output an empty "experiences" array.

"fact" is a SINGLE object, never a list — one record per session. Use
"fact": null only when the segment is pure chatter that established nothing.
"""

CODING_RECONCILE_PROMPT = """\
You decide how to merge new coding memory drafts with the user's existing memories.

For each new draft, decide one of:
  ADD     — no existing memory describes the same task/aspect
  UPDATE  — an existing memory describes the same task; the new draft replaces it
            (solution may have changed, env may have upgraded, etc.)
  SKIP    — duplicate of existing; nothing new

Additionally, you may emit DELETE ops on existing candidates when the new
content makes them obsolete or invalid:
  DELETE  — the new conversation EXPLICITLY supersedes/cancels/invalidates an
            existing memory that cannot be repaired via UPDATE. Examples:
              * user says "don't use X anymore" and X is only present as a
                separate memory (not the one being added/updated)
              * candidate's boundary_envs (service / endpoint / SDK) is gone
                and the new conversation confirms the deprecation

Rules:
- "Same task" means the existing memory and the new draft would both be valid
  answers to the same future user query.
- If solution differs, prefer UPDATE (we don't keep history).
- If both task and solution match closely, SKIP.
- DELETE only when there is EXPLICIT evidence the candidate is invalid/obsolete.
  Never DELETE just because a candidate looks "stale".
- Output ops can outnumber drafts when DELETEs are produced.

═══════════════════════════════════════════════════════════════════
NEW DRAFTS
═══════════════════════════════════════════════════════════════════
{drafts_block}

═══════════════════════════════════════════════════════════════════
EXISTING CANDIDATES (per draft, retrieved by task similarity)
═══════════════════════════════════════════════════════════════════
{candidates_block}

═══════════════════════════════════════════════════════════════════
OUTPUT (strict JSON only, no markdown)
═══════════════════════════════════════════════════════════════════
{{
  "ops": [
    {{"draft_idx": 0, "action": "ADD"}},
    {{"draft_idx": 1, "action": "UPDATE", "target_experience_id": "exp_xxx", "reason": "..."}},
    {{"draft_idx": 1, "action": "DELETE", "target_experience_id": "exp_yyy", "reason": "user explicitly deprecated Y"}},
    {{"draft_idx": 2, "action": "SKIP",   "reason": "duplicate"}}
  ]
}}

If there are no drafts, output {{"ops": []}}.
"""

CODING_FACT_RECONCILE_PROMPT = """\
You maintain session-level WORKING FACTS. Each statement summarizes one whole
trace/session: the user's task, the rough approach, and the result. It is not an
atomic claim.

Every new session fact below WILL be stored; that is not your decision. Your only
job is to detect task continuation: is the new session continuing to advance the
SAME user task as an existing session fact? If so, SUPERSEDE the older fact so
the links form a cross-session progress chain for that task.

Mark an existing fact as superseded ONLY when there is strong evidence that:
  - The subjects match: both records are about the SAME entity — the same
    person, the same file/module, the same host/service. A shared subject is
    necessary but not sufficient; AND
  - The user goal is materially the SAME, not merely in the same topic/project;
    AND
  - The new trace continues, retries, extends, verifies, or finishes the work
    recorded by the existing trace.

Do NOT supersede when:
  - The two records name different subjects. Two people in one group, two files
    in one module, two hosts in one cluster are PARALLEL, not successive: one
    person's state is not a newer version of another's. Where a subject is shown
    as (unspecified), fall back to judging by the statement alone.
  - The sessions merely mention the same file, symbol, component, repository,
    error, or technology but pursue different user goals.
  - One task only supplies context or a technique for another task.
  - The relationship is uncertain. Be conservative: INSERT is the safe default;
    merging unrelated sessions destroys their separate task history.

Treat each statement as evidence-bounded text. Do not reinterpret an inference
inside it as an established cause or use a shared conjecture/error as proof that
the user goals are the same. Your decision is only about task continuation; do
not "correct", complete, or embellish either session record.

Examples:
  existing: "The user needed to fix CI's npm run build; traced it to
             ERR_OSSL_EVP_UNSUPPORTED under Node 18, workflow not yet changed."
  new:      "The user continued fixing the same CI build; raised Node in
             .github/workflows/ci.yml to 20 and npm run build passed."
                                                                 → SUPERSEDE

  existing: "The user needed to fix JWT expiry handling in
             auth/middleware.ts; refresh logic was added."
  new:      "The user needed performance logging added to
             auth/middleware.ts; a latency metric was introduced."
                                                                 → keep both
            (same file/project, different user goal)

═══════════════════════════════════════════════════════════════════
NEW SESSION FACTS
═══════════════════════════════════════════════════════════════════
{drafts_block}

═══════════════════════════════════════════════════════════════════
EXISTING SESSION FACTS (per new fact, retrieved by statement similarity)
═══════════════════════════════════════════════════════════════════
{candidates_block}

═══════════════════════════════════════════════════════════════════
OUTPUT (strict JSON only, no markdown)
═══════════════════════════════════════════════════════════════════
{{
  "ops": [
    {{"draft_idx": 0, "supersedes": ["fact_xxx"], "reason": "continues the same CI fix task"}},
    {{"draft_idx": 1, "supersedes": []}}
  ]
}}

Only use fact ids that appear in the candidate list for that same draft_idx.
If nothing is superseded anywhere, output {{"ops": []}}.
"""

CODING_CHAIN_SUMMARY_PROMPT = """\
A user task in a working-memory store has progressed across sessions. Below are
the session summaries in that task's progress chain, oldest first, ending with
the current session.

Summarize how the SAME task progressed across sessions to its current state.
Cover every important stage in chronological order. For each stage, preserve
where available: the actual action or change, its direct result, and any
unresolved issue or limiting condition. End with an explicit current status.

Rules:
  - This is compression only. Report ONLY what the entries say; never add or
    complete a command, cause, motive, action, test result, success state, or
    global negative conclusion.
  - Preserve evidence status exactly: attempted is not completed, observed is
    not verified, inferred is not established, and unresolved stays unresolved.
    Keep uncertainty words and search/permission/coverage limits.
  - State the current ending status faithfully (completed, partially completed,
    failed, blocked, or still awaiting verification). Never upgrade it based on
    a plausible causal story or on a later entry that did not verify it.
  - When entries mention a suspected cause and a later result, connect them
    causally only if an entry directly says that cause was verified.
  - Preserve concrete retrieval anchors: file paths, symbols, commands, error
    strings, versions, config keys/values, and important numeric values that
    matter to the progress.
  - For chains of 2-3 generations, do not over-compress the progression into one
    sentence. Prefer 2-4 compact paragraphs when useful, or readable prose with
    a clear task goal, stage-by-stage progression, and current status.
  - Do not mechanically copy the full history. Omit repetition and low-value
    detail while retaining important actions, direct results, and leftovers.
  - Do not invent stage headings or dates. Mention a date only when the entries
    make it meaningful; bare timestamps are noise.
  - If the entries say older revisions were omitted, explicitly state that
    earlier history was omitted; do not imply the supplied entries are complete.
  - Write in the same language the entries are written in.
  - No preamble or enclosing quotes.

Example
  entries:
    (2026-07-20) The user needed to fix CI's npm run build; traced it to
                 ERR_OSSL_EVP_UNSUPPORTED under Node 18, workflow not yet
                 changed.
    (2026-07-24) The user continued that CI fix; raised Node in
                 .github/workflows/ci.yml to 20, but the dependency cache
                 still reproduced the old error.
    (now)        The user continued verifying the same fix; after clearing the
                 npm cache, npm run build passed.
  output:
    The task goal is fixing CI's npm run build. It was first traced to
    ERR_OSSL_EVP_UNSUPPORTED under Node 18, with the workflow not yet changed;
    then .github/workflows/ci.yml was raised to Node 20, but the dependency
    cache kept the old error present. Finally, after clearing the npm cache,
    npm run build passed, so this build fix is currently verified as complete.

═══════════════════════════════════════════════════════════════════
ENTRIES
═══════════════════════════════════════════════════════════════════
{chain_block}

═══════════════════════════════════════════════════════════════════
OUTPUT (the summary itself, nothing else)
═══════════════════════════════════════════════════════════════════
"""


# ================================================================
# Episode / topic summaries
# ================================================================

EPISODE_SUMMARY_PROMPT = """You are summarizing ONE episode of conversation with the user.

An episode is a single stretch of discussion — one thing that was talked about,
possibly across several sessions of writing.

## Memories captured during this episode (oldest → newest)

{memories}

---

Write a single dense summary of what happened / what was discussed in this episode.

Rules:

* 2–4 sentences, third person, starting with "The user ..." (or the named person
  if the memories are about someone else).
* Tie the memories together into one coherent account — an episode usually has a
  thread running through it (a plan, a problem, an outing, a decision).
* Preserve concrete details: names, places, numbers, dates.
* Do NOT invent anything that is not in the memories.
* Do NOT enumerate the memories one by one; synthesize them.
* Output language must match the language of the memories.

Return ONLY the summary text, no preamble, no JSON, no quotes."""

TOPIC_SUMMARY_PROMPT = """You are summarizing everything known about ONE topic.

## Topic

{topic_name}

## Memories under this topic (oldest → newest)

{memories}

---

Write a single dense summary of what is known about the user with respect to this
topic.

Rules:

* 2–4 sentences, third person, starting with "The user ..." (or the named person
  if the memories are about someone else).
* Cover the topic as a whole — state the current situation, and mention how it
  developed only when the change matters.
* Preserve concrete details: names, places, numbers, dates.
* Do NOT invent anything that is not in the memories.
* Do NOT enumerate the memories one by one; synthesize them.
* Output language must match the language of the memories.

Return ONLY the summary text, no preamble, no JSON, no quotes."""


# ================================================================
# System 2 evolve (pipelines/system2_agent.py)
# ================================================================

SYSTEM2_AGENT_PROMPT = """You are a cognitive processing Agent. Your job is to evolve higher-order cognitive structures from a user's raw memory data.

## Memory Architecture

### VDB (Vector Database) — read-only
| Layer | Description |
|-------|-------------|
| L0 BASIC_INFO | User attributes (name, age, occupation) |
| L1 RAW | Original conversation transcripts |
| L2 FACT | Atomic facts extracted from conversations |
| L3 SUMMARY | Session summaries |
| L4 IDENTITY | Identity traits, preferences, habits |
| L5 KNOWLEDGE | Domain knowledge |

### Graph (Knowledge Graph) — you are the sole writer
| Layer | Description |
|-------|-------------|
| L6 SCHEMA | Behavioral patterns — domain-bound, atomic, immutable once created |

## What is an L6 Schema?

A Schema is one cross-event behavioral pattern in the long-term user model and
contains the following three components:


- **Circumstance**: The domain/topic/situation where this pattern is observed. A Schema MUST stay within its circumstance — do NOT generalize across domains. Cross-domain abstraction is handled separately by the system.
- **Pattern**: The user's habitual behavior, thinking style, or action tendency in this circumstance.
- **Insight**: The underlying psychological driver or mental model.

### Content format
Use a single sentence combining all three:
"When [circumstance], the user [pattern] — reflecting [insight]."

For durable emotional, therapy, stress, grief, or trauma-related patterns, do
not erase the specific autobiographical background into a generic personality
label. Prefer an anchored structure that preserves:
"Trigger/background -> recurring emotional response -> observed coping resource."
Only include triggers, responses, and coping resources present in user-owned
evidence. Assistant advice is not an observed user coping resource.

### Rules
- **Atomic**: One pattern per Schema. Two distinct patterns → two Schemas.
- **Immutable**: Once created, content NEVER changes.
- **Evidence only**: New facts that merely support existing wording → call
  `add_evidence`. If new evidence materially changes, corrects, or narrows the
  current durable state, use `revise_schema` so the old version is retained for
  audit but removed from active retrieval.
- **Domain-bound**: Stay within the observed domain. Cross-domain abstraction is handled by a separate system process.
- **Separate facts from patterns**: A single explicit identity, health state,
  equipment use, affiliation, project, or preference belongs in L2 state memory,
  not L6. L6 requires user-owned evidence representing at least two independent
  raw writes or sessions. One reconciled L2 fact may represent multiple writes;
  inspect its source_raw_memory_ids rather than counting fact nodes.
- **Subject safety**: Facts sharing a source chunk may describe other people or
  examples. Create a user Schema only when the material explicitly attributes
  the pattern to the user. Otherwise output no operation for that material.
- **Candidate coverage**: In a source-context review, account for every listed
  candidate fact: either use it as evidence in `create_schema`/`add_evidence`,
  or emit `skip_fact`. Raw context may confirm its subject or restore omitted
  detail, but must not seed unrelated Schemas.
- **Evidence threshold**: Creating L6 requires one or more user-owned evidence
  facts representing at least two independent raw writes or sessions.
  Assistant advice does not establish a user pattern by itself.

### Good vs Bad examples
✅ "When cooking, the user strictly follows recipe steps and precisely measures ingredients — reflecting a need for external structure to manage uncertainty."
✅ "When gaming, the user always checks walkthroughs to achieve 100% completion — driven by low tolerance for incompleteness."
✅ "After an unexpected mentor loss, mortality reminders can bring grief back suddenly; the user pauses to reflect and takes quiet walks to regain steadiness."
❌ "The user approaches music as a medium for creative synthesis and cultural bridge-building, consistently blending diverse musical traditions..." (multiple patterns merged, too broad)
❌ "The user is passionate about many things and values quality." (no circumstance, too vague)
❌ "The user is meaning-driven and resilient." (erases the durable trigger and observed coping evidence)

## Your Workflow

Input materials (you are only invoked when at least one cluster exists):
- **Cluster Results**: Primary input — facts grouped by semantic similarity
- **Unclustered Facts (supplemental)**: Facts not in any cluster; lower priority than cluster results

For each group of facts (clustered or unclustered):
1. **Search existing Schemas**: If an existing Schema already captures this pattern, call `add_evidence`. Use `revise_schema` only when the old wording would become materially incomplete or misleading after adding the new evidence.
2. **Create new Schema**: Only if no existing Schema covers this pattern. One sentence, three components.
3. **Build relationships**: If two Schemas are thematically related, use `add_edge` (RELATED_TO).

## Principles

- All Graph operations MUST go through tools
- Schema content is IMMUTABLE — never recreate what already exists
- Prefer `add_evidence` over creating duplicates
- Prefer not creating over creating low-quality nodes
- One Schema = one atomic pattern in one domain
- When tagging, prefer reusing tags from the existing tags list"""


# ================================================================
# Fragments assembled in code
# ================================================================

# Display name of this language, used in logs and docs.
LANGUAGE_NAME = "English"

# Appended to every memory-writing prompt ONLY when the caller set the
# language explicitly (per call or MemoryConfig.language). Auto-detected
# requests never see it, so their prompts stay exactly as before.
OUTPUT_LANGUAGE_DIRECTIVE = """

---

# OUTPUT LANGUAGE

Write every natural-language value you output (memory text, summaries, reasons, queries) in English, whatever language the conversation or the existing memories use. This overrides any rule above that says to match the input language. Keep JSON keys, field names, enum values, operation names, IDs, code, file paths, commands, error messages and proper nouns exactly as they are.
"""

# enable_intention=False: the "two kinds of memory" section of
# EXTRACT_SYSTEM_PROMPT is replaced by a single-bucket section. The pattern
# must match this language's EXTRACT_SYSTEM_PROMPT.
NO_INTENTION_SECTION_PATTERN = r"# TWO KINDS OF MEMORY.*?(?=\n# OWNER\n)"

NO_INTENTION_MEMORY_SECTION = (
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

# (old, new) pairs applied in order after the section swap.
NO_INTENTION_REPLACEMENTS = (
    (
        "Every memory and intention MUST carry an `owner`",
        "Every memory MUST carry an `owner`",
    ),
)

INTENTION_DISABLED_NOTE = (
    "\n\n---\n\n# INTENTION DISABLED\n"
    "Intention extraction is disabled. Even if few-shot examples show "
    "`intentions`, do **not** emit that key; put future plans in `memory`.\n"
)

# Coding / working extractor: wraps the history and the new messages.
CODING_CONVERSATION_BLOCK = (
    "LAST 5 USER TURNS (CONTEXT ONLY; do not extract them again):\n"
    "{history}"
    "\n\nNEW MESSAGES (extract only from this complete write):\n"
    "{new}"
)
CODING_EMPTY_BLOCK = "(none)"

# Name line at the top of a multi-person conversation. Also stored in L1_RAW,
# so pipelines.base parses every language's form back.
USER_NAME_LINE = "The user's name is {name}"

# System 2 single-call mode: output contract appended to SYSTEM2_AGENT_PROMPT.
SYSTEM2_OUTPUT_INSTRUCTIONS = """

## Output Format

Do NOT call tools. Output a JSON array of operations directly, wrapped in a ```json code block.

Available operations:

1. **create_schema** — Create a new L6 behavioral Schema
   ```{"op": "create_schema", "schema_kind": "behavior_pattern", "content": "concise user-facing pattern", "retrieval_text": "behavior plus relevant situations", "evidence_list": ["fact_node_id_1", "fact_node_id_2"], "tags": ["tag1"]}```

2. **add_evidence** — Add evidence to an existing Schema (content stays immutable)
   ```{"op": "add_evidence", "node_id": "existing_schema_node_id", "evidence_list": ["new_fact_id_1"]}```

3. **revise_schema** — Create a new version when new evidence materially changes the current state and retire the old version from active retrieval. Do not use it for compatible detail.
   ```{"op": "revise_schema", "node_id": "old_schema_id", "schema_kind": "explicit_state", "content": "updated current profile", "retrieval_text": "updated retrieval text", "evidence_list": ["new_fact_id_1"], "tags": ["tag1"], "reason": "why the old wording would mislead"}```

4. **add_edge** — Create a relationship between two Schemas
   ```{"op": "add_edge", "source_id": "schema_id_1", "target_id": "schema_id_2", "reason": "relationship reason"}```

5. **skip_fact** — Explicitly mark a candidate fact as reviewed without creating
   a Schema. Use only for facts about another person, transient events, assistant
   advice that does not establish a user trait, or facts already fully covered.
   ```{"op": "skip_fact", "node_id": "candidate_fact_id", "reason": "not a durable user pattern"}```

## Output Contract

1. Output MUST be a JSON array wrapped in a ```json code block.
2. If data is insufficient for reliable conclusions, output `[]`. In a
   source-context review, `[]` means every candidate in that review was examined
   and none supports a durable Schema.
3. NO text outside the code block.
4. evidence_list IDs must come from the fact node_ids provided in the materials.
5. add_evidence / revise_schema node_id must come from existing Graph node IDs.
6. For a source-context review, every candidate fact must appear in an evidence_list or skip_fact."""

# Appended to SYSTEM2_OUTPUT_INSTRUCTIONS when the schema evidence gate is on.
SYSTEM2_SCHEMA_EVIDENCE_GATE = """

7. L6 represents cross-event behavioral patterns only. create_schema must use user-owned evidence representing at least two independent raw writes or sessions. A single reconciled fact can qualify when source_raw_memory_ids contains multiple independent writes.
8. Do not create L6 for a single explicit identity, health state, equipment use, affiliation, project, or preference. Canonical L2 state projection owns those facts."""

# (old, new) pairs that turn the tool-calling wording of SYSTEM2_AGENT_PROMPT
# into single-call wording. Each `old` must occur in this language's prompt
# (or be harmless if it does not).
SYSTEM2_SINGLE_CALL_REPLACEMENTS = (
    ("- All Graph operations MUST go through tools\n", ""),
    ("call `add_evidence`", "use add_evidence operation"),
    ("use `add_edge` (RELATED_TO)", "use add_edge operation"),
)

# System 2 user message (materials). Keys are fixed; values are format
# strings with the named fields shown.
SYSTEM2_MATERIALS = {
    "title": "## Preprocessed Materials\n",
    "stats": (
        "### Statistics\n- Total VDB facts: {total}\n"
        "- Fresh facts (to process): {fresh} (evidenced_excluded={evidenced})\n"
        "- Clusters found: {clusters}\n"
        "- Facts in clusters: {clustered}, unclustered supplement: {unclustered}\n"
        "- Graph Schemas total: {graph_total} (recalled={recalled})"
    ),
    "graph_status_empty": "- Graph status: **EMPTY** (first cognitive processing run)\n",
    "clusters_header": "### Cluster Results\n",
    "label_holistic": "{n} cross-session user facts; only evidence-backed durable emotional/therapy patterns",
    "label_source_context": "{n} candidate facts; raw write is only for subject/detail validation",
    "label_topic": "{n} facts, topic",
    "raw_label": "Relevant raw excerpt per candidate",
    "holistic_note": (
        "This is one bounded nightly holistic consolidation pass. Create at most "
        "{max_schemas} anchored therapy Schemas. "
        "Prefer joining durable trigger/background, recurring emotional response, "
        "and observed coping resource or decision constraint across different raw writes/sessions. "
        "Before create_schema, union raw_sources for its evidence IDs and require at least two "
        "distinct raw source IDs; two facts from one raw write are one event. "
        "Output [] when evidence is insufficient.\n"
    ),
    "companion_header": "**Independent-source companion hints (candidates, not conclusions):**",
    "unclustered_header": "### Unclustered Facts (supplemental)\n",
    "unclustered_hint": "Facts below were not grouped into any cluster above. Review after cluster themes; lower priority than cluster results.\n",
    "forward_header": "### Related Graph Nodes (retrieved via cluster centroid search, {count} hits)\n",
    "graph_empty": "### Existing Graph Nodes\nThe Graph is currently EMPTY — this is the first cognitive processing run. Do NOT search the Graph (it will return nothing). Proceed directly to create Schemas from the cluster results.\n",
    "graph_no_recall": (
        "### Existing Graph Nodes\n"
        "The Graph has {graph_total} Schemas, "
        "but centroid-based vector recall found no matches. Decide based on cluster results whether new Schemas are needed.\n"
    ),
    "reverse_header": "### Graph Reverse References (VDB facts referenced by existing Schemas)\n",
    "tags_header": "### Existing Tags (prefer reusing these over creating synonyms)\n",
    "closing": (
        "Analyze the materials above and output a JSON array of operations. "
        "Decide whether to create new Schemas, or add evidence to existing ones. "
        "If the data is insufficient for reliable conclusions, output an empty array `[]`. "
        "When tagging, prefer reusing tags from the existing tags list above."
    ),
}
