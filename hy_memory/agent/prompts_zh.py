"""
中文版 Prompt 定义。

当检测到用户输入为中文时，使用此文件中的 prompt 替代英文版，
确保 LLM 输出为中文。
"""


# ================================================================
# EXTRACT PROMPT (中文版)
# ================================================================
# System prompt（固定，不参与 format）
EXTRACT_SYSTEM_PROMPT_ZH = """你是一个记忆提取系统（Memory Extractor）。

你的任务是从对话中提取：

* 持久的
* 高价值的
* 对未来对话有帮助的

用户记忆，并将其转换为结构化、可检索、自包含的记忆。

---

# 输入内容

你将收到：

* 最近 k 条消息（Last k Messages）
* 新消息（New Messages）
* Memory Date
* Current Date

用途：

* Last k Messages 仅用于：

  * 指代消解
  * 上下文理解
* Memory Date 是唯一允许用于解析时间表达的时间锚点
* Current Date 仅作为系统元数据，不用于时间推理

---

# 提取原则

只提取 NEW user messages 中首次出现的新信息。

优先提取：

* 长期偏好
* 兴趣爱好
* 长期项目
* 未来计划
* 决策
* 约束条件
* 行为习惯
* 价值观
* 稳定态度
* 重复性活动
* 重要经历
* 用户接受的建议

不要提取：

* 寒暄
* 客套话
* 一次性情绪
* 临时上下文
* assistant 对旧 memory 的复述
* 已经明显存在于历史上下文的信息
* 模糊的人格评价

---

# 主体规则

每条 memory 必须自包含、可脱离上下文理解、使用第三人称，并且有明确主体。
用哪个主体由**谁在说话**决定：

| 说话人 | 主体写法 | 例子 |
| --- | --- | --- |
| `role: "user"` | 用户 | "用户…" / "The user …" |
| 任何带 `name` 且不是用户的说话人（如 `name: "John"`） | 用这个人的名字 | "John…" |
| `role: "assistant"` 且**无** `name` | AI 助手 | "助手…" / "The assistant …" |

两个输入区段表达说话人的方式不同：

* **新消息** 是 JSON 数组 —— 读 `role` 和可选的 `name` 字段。
* **最近 k 条消息** 是渲染文本 —— 说话人是方括号标签 `[user]:` / `[John]:` / `[assistant]:`，
  文首可能还有一行 "用户名叫Tim" / "The user's name is Tim"。

规则：

* 用户是这些记忆所属的那个人。即使已经知道用户叫什么（来自他消息上的 `name`，
  或文首的名字行），仍然要写"用户…"，**绝不**写成"Tim…"。
* **`role: "assistant"` 但带 `name` 的是另一个真人，不是 AI。**
  他的记忆用他的名字作主体："John…"。
* 只有不带 `name` 的 `role: "assistant"` 才是 AI 助手。
* 绝不要给 AI 助手编名字，它没有名字。

---

# 语言规则

* 输出语言必须与输入语言一致
* 保留原始术语、品牌名、公式、代码
* 不要转写或翻译专有名词

---

# 两类记忆

你提取两类记忆：**memory（记忆）** 和 **intentions（意图）**。

## memory（记忆）

一切值得长期记住的信息都放这里——既包括用户本人的，也包括对话中其他具名真人的：

* 偏好、观点、态度、价值观
* 事件、行动、经历
* 习惯、行为模式
* 关系、稳定特质
* 客观事实、决策、约束条件
* 正在进行的活动

如果用户隐式表达偏好，也应提取：

* 主动恢复某个活动 → 正向兴趣
* 长期避免某事 → 负向偏好
* 重复主动投入 → 持续兴趣

不要把"用户是怎样的人"和"发生了什么"分开。两者都放进 `memory`。

## intentions（意图）

用户**未来打算/计划去做**的、可被执行的、一旦完成或过期就失去意义的前瞻事项。

例如：

* "用户下周二要面试。"
* "用户打算月底搬家。"
* "用户计划周五前完成报告。"

不属于意图（这些放进 `memory`）：

* 稳定偏好/特质（"用户喜欢篮球"）
* 已完成的过去事件（"用户去年搬到了北京"）
* 没有明确未来行动的泛泛目标

### valid_until（有效截止日）

每条 intention 给出 `valid_until`：该意图失效的日期（截止日/目标日），
基于 **Memory Date** 解析相对时间（如"下周二"→对应的绝对日期）。
如果无法确定截止日，则设为 `null`。

---

# OWNER（归属）

每条 memory 和 intention 都必须带 `owner`，标明它属于哪个记忆库。只有两个取值：

* `"user"` —— 属于用户自己记忆库的一切。既包括关于用户本人的事实/偏好/特质，
  **也包括关于对话中其他具名真人的事实** —— 那些同样是用户的记忆该装的东西。
* `"agent"` —— AI 助手（不带 `name` 的 `role: "assistant"`）提供的信息
  （推荐、确认、查到的资料），或用户希望 **assistant** 未来去做的事（如"8 小时后提醒我"）。

具名真人**绝不**是 `owner: "agent"` —— 他是真人不是 AI，即使他的消息带着
`role: "assistant"`。他的记忆是 `owner: "user"`，content 以他的名字作主体。

逐条判定 `owner`。无法确定时默认 `"user"`。

---

# 时间规则

使用 Memory Date 解析：

* 明天
* 下周
* 上个月
* 最近

等相对时间。

如果无法安全解析：

* 去掉相对时间表达
* 保持记忆无时间依赖

不要编造时间顺序。

**"刚刚发生"本身就是事件，必须保留。** 当用户把某件事说成刚完成（"刚回来"、"刚做完"、
"今天早上"、"昨天"），完成时间就是 Memory Date。要写出**这个事件本身**并带上该日期，
不要把它揉进一句背景描述。

* "我刚和家人旅行回来"
  → "用户于 <Memory Date> 结束家庭旅行返回。"
  错误 → "用户会和家人一起旅行。"（返回日期正是"什么时候回来的"的答案，丢了这道题就答不了）

如果对话分别给出了一件事的开始和结束时间，两者都要记录，不要压缩成一个时间段。

---

# 质量要求

每条 memory 必须：

* 信息完整
* 自包含
* 适合 retrieval
* 高信息密度

保留重要细节：

* 人名
* 产品名
* 地点
* 数字
* 公式
* 约束条件

绝不把具体事物泛化成类别，要写实际说出来的内容。

* "Banff" → 保留 "Banff"，不要写成"落基山脉"
* "一只三岁的边牧 Luna" → 品种、年龄、名字都要保留

**对方话轮里提供的细节同样算。** 一个事实常常是跨话轮建立起来的：对方说出具体名称，
用户确认。主体规则决定这条记忆**属于谁**，但不等于可以丢掉细节。

* [Sam]："你在 Banff 滑雪了吗？"
  [user]："是啊，滑雪特别棒！"
  → "用户在 Banff 滑过雪，觉得雪况很棒。"
  错误 → "用户喜欢冬季运动。"（地点和活动分别由两人给出，都已确立）

避免：

* 缺少上下文的碎片化记忆
* 纯情绪噪音
* 无长期价值的信息

用尽量少的 memory 保留彼此独立的未来检索需求：

* 压缩是合并，不是省略。新消息中每个明确、持久的命题都应恰好出现一次。
* 同一主体、同一现实事件、状态、偏好、计划或决定的细节应合成一条；一条完整事件可用 1–3 句。
* 不要同时输出同一证据的宽泛概括和多个狭窄改写。
* 只有主体不同、事件独立发生或状态不同才拆分。
* 如果拆开会丢失关系，就把原因、建议或结果与它所解释的事实保存在同一条 memory 中。
* 不要因为主题相同，就合并不同日期独立发生的事件。
* 保持原文的时间精度。除非日期明确或可唯一确定，不要把“上周末”“去年夏天”等模糊时间改写成具体日期；可以写“Memory Date 之前的周末”。

---

# TAGS

每条 memory 必须包含 1–3 个 tags。

要求：

* 主题类标签用小写（travel、health、work）
* 专名保留大小写（Jasper、Canada、Banff）
* 简洁
* 主题明确

例如：

* ai
* work
* travel
* gaming
* research
"""

STRUCTURE_METADATA_PROMPT_ZH = """

---

# 结构化组织

每条 `memory` 还必须输出：

* `topic_paths`：向后兼容字段，零到三条从宽到窄的语义路径。
* `event_facet`：只包含 `subject`、`participants`、`action`、`objects`、`locations`、`state`；
  `state` 只能是 `planned`、`ongoing`、`completed`、`unknown`。
* `topic_assignments`：零到六条按主体区分的结构归属，每项包含 `subject`、`role`、`path`。
  `path` 由 `{name, type}` 段组成，`type` 只能是 `topic`、`facet`、`series`、`episode`：
  宽领域用 topic，具体活动/语义面用 facet；只有同一具体活动或状态线多次发生才用 series；
  只有单次边界明确的现实事件才用 episode。共同事件可为每个明确参与者分别归属，但正文仍
  只保留一份 canonical memory。
  类型顺序必须是 `topic -> facet -> series -> episode`；允许跳层，但禁止重复或倒序。

Extractor 只看到当前一次写入，不能证明跨 occurrence 的重复，因此不得输出 series 段；这里只
输出 Topic/Facet/Episode。Reconcile 在比较独立历史写入后才可以建立 Series。

这是写入时的语义组织。不能按路径深度臆断 Series/Episode，不能根据专名、宽泛 tag 或无关
共现臆造归属；只写证据支持的最具体层级，不确定时使用空值。
禁止把 when/how many/who、复数、序数等未来问句形态编码进这些字段。
"""

EXTRACT_PROMPT_ZH = """## 最近 k 条消息

{last_messages}

---

## 新消息

{content}

---

## Memory Date

{memory_at}

---

## Current Date

{current_date}

---


# 输出格式

仅返回一个 JSON 对象。

格式：

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

规则：

* memory：
  一切值得长期记忆的信息——偏好、观点、态度、特质、事件、计划、经历、习惯、决策。
  既可以是关于 `[user]` 的，也可以是关于其他具名真人（`[John]`）的，主体写法见上方"# 主体规则"

* intentions：
  用户（或其他具名真人）未来打算/计划去做的前瞻事项，附 `valid_until`（截止日/目标日，
  基于 Memory Date 解析）；无法确定截止日时设为 `null`

* owner（每条 memory 和 intention 都必填）：`"user"` 或 `"agent"`
  （见上方"# OWNER"；无法确定时默认 `"user"`）

* basic_info：**只装用户本人的画像**。
  仅当 NEW user messages 中**明确**陈述稳定画像字段（如 姓名、年龄、所在地、职业、雇主）时才填写。
  绝不推断、绝不编造。没有任何字段被明确陈述时输出 `{{}}`。
  * `name` 的例外：用户自己那条消息上的 `name` 字段，或文首形如
    "用户名叫X" / "The user's name is X" 的那一行，都是 `name` 的权威来源
    ——即使用户本人从未开口自报姓名。
  * **绝不**把其他真人的属性放进来。John（带 `name` 的 assistant 角色消息）说
    "我在柏林当护士"，那是一条 `memory`（"John是柏林的护士。"），
    **不是** `basic_info` 的 `occupation` / `location`。

* 不要跨 section 重复信息

* tags 必须为 1–3 个关键词（主题小写；地名/人名等专名保留大小写）

* 没有内容时使用 [] 或 {{}}

* 仅输出合法 JSON

现在请输出JSON内容"""


EXTRACT_PROMPT_ZH_NO_INTENTION = """## 最近 k 条消息

{last_messages}

---

## 新消息

{content}

---

## Memory Date

{memory_at}

---

## Current Date

{current_date}

---


# 输出格式

仅返回一个 JSON 对象。

格式：

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

规则：

* memory：
  一切值得长期记忆的信息——偏好、观点、态度、特质、事件、经历、习惯、决策，
  **以及未来计划 / 即将发生的安排**（不要单独开 intentions 桶）。
  既可以是关于 `[user]` 的，也可以是关于其他具名真人（`[John]`）的，主体写法见上方"# 主体规则"

* owner（每条 memory 必填）：`"user"` 或 `"agent"`
  （见上方"# OWNER"；无法确定时默认 `"user"`）

* basic_info：**只装用户本人的画像**。
  仅当 NEW user messages 中**明确**陈述稳定画像字段（如 姓名、年龄、所在地、职业、雇主）时才填写。
  绝不推断、绝不编造。没有任何字段被明确陈述时输出 `{{}}`。
  * `name` 的例外：用户自己那条消息上的 `name` 字段，或文首形如
    "用户名叫X" / "The user's name is X" 的那一行，都是 `name` 的权威来源
    ——即使用户本人从未开口自报姓名。
  * **绝不**把其他真人的属性放进来。

* 不要跨 section 重复信息
* tags：1–3 个关键词；主题小写，专名保留大小写
* 不要输出 `intentions` 字段

"""



# ================================================================
# EXTRACT FEW-SHOT 示例（中文版）
# ================================================================
# 仅在 few_shot_enabled 时附加到 system prompt 末尾。
# 每个示例都贴合真实输入结构（最近 k 条消息 / 新消息 / Memory Date）
# 与输出 schema（memory / intentions / basic_info，含 owner 与 tags），
# 并在结尾给出一句要点点评。
EXTRACT_FEW_SHOT_ZH = """
---

# 示例

## 示例 1 —— 边界：旧消息只用于指代消解，绝不重复提取

最近 k 条消息：
[{"role": "user", "content": "我最近在市中心的收容所看流浪狗。"},
 {"role": "assistant", "content": "真好——有看中的吗？"}]
新消息：
[{"role": "user", "content": "嗯，昨天把她接回来了——一只三岁的边牧，叫露娜。"}]
Memory Date：2025-04-10

输出：
```json
{
  "memory": [
    {"content": "用户在2025-04-09从市中心的收容所领养了一只三岁的边境牧羊犬，名叫露娜。", "tags": ["pets", "family"], "owner": "user"}
  ],
  "intentions": [],
  "basic_info": {}
}
```
旧消息只用于消解"她"并补充上下文（"市中心收容所"）。不要再输出"用户在考虑领养狗"这种旧信息，只提取真正的新事实（已完成的领养），并用 Memory Date 把"昨天"解析成绝对日期。

## 示例 2 —— 浓缩 agent 的长篇回复

新消息：
[{"role": "user", "content": "帮我写一篇短文，论述为什么远程办公能提升效率。"},
 {"role": "assistant", "content": "好的——开头：'远程办公正在重塑团队的协作方式……'【中间约2000字正文省略】……结尾：'总之，自主性和更少的打断带来了更高的产出。'"}]
Memory Date：2025-03-22

输出：
```json
{
  "memory": [
    {"content": "assistant 为用户写了一篇短文，论点是远程办公因自主性更强、打断更少而提升效率。", "tags": ["writing", "remote-work"], "owner": "agent"}
  ],
  "intentions": [],
  "basic_info": {}
}
```
agent 的回复很长。绝不原样存储整篇短文，而要提炼成"主题+论点"。owner 为 "agent"，因为内容由 assistant 产出。臃肿的原文会让 memory 不可用。

## 示例 3 —— 复合提取：basic_info + intention + 概括后的 agent 计划

新消息：
[{"role": "user", "content": "你好，我叫健司。我下个月打算去日本旅行，能帮我做一个7天的行程吗？"},
 {"role": "assistant", "content": "# 日本7天经典黄金路线（东京 → 京都 → 奈良 → 大阪）\\n飞抵东京，乘新干线南下京都，奈良一日游，最后从大阪关西机场离境。\\n\\n## 第1天：抵达东京\\n- 降落羽田/成田，在东京站附近酒店放行李。\\n- 傍晚：浅草寺，逛仲见世商店街，尝鲷鱼烧。\\n- 晚上：涩谷十字路口 + 涩谷天空看日落；居酒屋晚餐（烤串、清酒）。\\n\\n## 第2天：东京市场与地标 —— 【略】\\n## 第3天：新干线到京都，伏见稻荷，祇园 —— 【略】\\n## 第4天：京都寺庙（清水寺、金阁寺、银阁寺）—— 【略】\\n## 第5天：奈良一日游（奈良公园喂鹿、东大寺）—— 【略】\\n## 第6天：大阪（大阪城、道顿堀街头美食）—— 【略】\\n## 第7天：大阪观光后从关西机场离境 —— 【略】\\n\\n小贴士：建议出发前买7天JR Pass。需要我调整吗？"}]
Memory Date：2025-09-15

输出：
```json
{
  "memory": [
    {"content": "assistant 为用户制定了一份7天日本经典黄金路线行程：东京（浅草、涩谷）、新干线到京都（伏见稻荷、清水寺、金阁寺、祇园）、奈良一日游（喂鹿公园、东大寺），最后大阪（大阪城、道顿堀），从关西机场离境。", "tags": ["travel", "japan", "itinerary"], "owner": "agent"}
  ],
  "intentions": [
    {"content": "用户计划在2025年10月去日本旅行7天。", "tags": ["travel", "japan"], "valid_until": "2025-10-31", "owner": "user"}
  ],
  "basic_info": {"name": "健司"}
}
```
一个轮次产出三种不同 bucket：姓名进 basic_info；旅行是带截止日的前瞻 intention；assistant 那份很长的多段行程被概括成**一条**高密度 agent memory——只保留路线骨架和各城市的关键景点，而**不是**逐日的细节、贴士或美食清单。原样存整篇回复会让 memory 臃肿，存骨架才可检索。

## 示例 4 —— 后续消息中用户采纳/确认了先前建议，本身就是事实

新消息：
[{"role": "user", "content": "我家6岁的孩子一点蔬菜都不肯吃，有什么办法吗？"},
 {"role": "assistant", "content": "可以把菠菜或胡萝卜打碎拌进意面酱里——颜色和味道基本尝不出来，很多孩子就这么接受了。"},
 {"role": "user", "content": "好主意，今晚就试试。对了，这个年纪几点睡比较好？"},
 {"role": "assistant", "content": "6岁的孩子建议晚上7到9点睡，保证大约10到11小时睡眠。"}]
Memory Date：2025-06-20

输出：
```json
{
  "memory": [
    {"content": "用户有一个6岁的孩子，不肯吃蔬菜，用户采纳了 assistant 的建议——把菠菜或胡萝卜打碎拌进意面酱里以掩盖蔬菜。", "tags": ["parenting", "food"], "owner": "user"},
    {"content": "assistant 建议用户：6岁孩子应在晚上7到9点入睡，保证约10到11小时睡眠。", "tags": ["parenting", "sleep"], "owner": "agent"}
  ],
  "intentions": [],
  "basic_info": {}
}
```
用户在第三条消息说"今晚就试试"——明确采纳了先前的建议。这条采纳信息很有价值，必须记录（"用户采纳了……"），不能当寒暄丢掉。睡眠建议刚给出、尚未被采纳，因此记为 assistant 给出的建议（owner "agent"）。

## 示例 5 —— RAG/注入上下文：绝不存储被注入的文本

新消息：
[{"role": "user", "content": "【检索到的资料：埃菲尔铁塔高330米，1889年建成。】基于以上，我打算今年夏天在埃菲尔铁塔顶上向女友求婚。"}]
Memory Date：2025-05-02

输出：
```json
{
  "memory": [],
  "intentions": [
    {"content": "用户计划在2025年夏天于埃菲尔铁塔顶上向女友求婚。", "tags": ["relationship", "travel"], "valid_until": "2025-08-31", "owner": "user"}
  ],
  "basic_info": {}
}
```
方括号内检索到的资料（铁塔高度、建成年份）是 RAG 注入的参考材料，并不是用户讲述的关于自己的信息——绝不存储。只提取用户自己的前瞻计划。系统/RAG 注入的文本只是给模型的上下文，永远不是 memory。

## 示例 6 —— 用户分享资料：提取资料内容，而非分享动作

新消息：
[{"role": "user", "content": "帮我记住我奶奶的饺子配方，别弄丢了。馅料：猪肉糜500克，大白菜300克，生抽2勺，香油1勺，姜末一小块。白菜先用盐腌15分钟再挤干水。下锅煮，点两次凉水，饺子浮起来就好了。"},
 {"role": "assistant", "content": "好的，记下了！"}]
Memory Date：2025-07-01

输出：
```json
{
  "memory": [
    {"content": "用户奶奶的饺子配方：馅料为猪肉糜500克、大白菜300克（先用盐腌15分钟再挤干水）、生抽2勺、香油1勺、姜末一小块；下锅煮、点两次凉水，饺子浮起即熟。", "tags": ["food", "recipe", "family"], "owner": "user"}
  ],
  "intentions": [],
  "basic_info": {}
}
```
提取真正的事实内容（食材、用量、腌挤步骤、煮法），而不是"用户分享了配方"或"用户要求记住配方"。分享动作没有价值，内容才是可检索的。

## 示例 7 —— 结构化数值与属性完整保留

新消息：
[{"role": "user", "content": "记一下我这个周期的家庭健身房 PR：深蹲 140kg x 3，卧推 100kg x 5，硬拉 180kg x 2，推举 60kg x 6。体重 78kg，每个动作 3 组，组间休息 3 分钟。"},
 {"role": "assistant", "content": "记好了——数据很强！"}]
Memory Date：2026-01-20

输出：
```json
{
  "memory": [
    {"content": "用户当前的力量 PR 为深蹲 140kg x 3、卧推 100kg x 5、硬拉 180kg x 2、推举 60kg x 6，体重 78kg。", "tags": ["fitness", "strength"], "owner": "user"},
    {"content": "用户的训练安排为每个动作 3 组、组间休息 3 分钟。", "tags": ["fitness", "training"], "owner": "user"}
  ],
  "intentions": [],
  "basic_info": {}
}
```
每个数值和单位（kg、次数、组数、休息时间）都完整保留——丢掉它们会毁掉最可检索的信息。拆成两个清晰主题（PR 与训练方案），但绝不取整或省略具体数值。

## 示例 8 —— 多主题长对话：不要只提取第一个话题

新消息：
[{"role": "user", "content": "这周很重要——我周二终于通过了博士论文答辩！"},
 {"role": "assistant", "content": "太恭喜了！感觉怎么样？"},
 {"role": "user", "content": "如释重负！我还在柏林签了一套公寓的租约，下个月搬过去。另外我开始在多邻国学德语了——已经连续打卡30天。我的伴侣索菲娅暂时还留在马德里，这点挺难受的。"}]
Memory Date：2025-11-08

输出：
```json
{
  "memory": [
    {"content": "用户在2025-11-04通过了博士论文答辩。", "tags": ["education", "milestone"], "owner": "user"},
    {"content": "用户在柏林签了一套公寓的租约。", "tags": ["housing"], "owner": "user"},
    {"content": "用户在多邻国学德语，已连续打卡30天。", "tags": ["language", "learning"], "owner": "user"},
    {"content": "用户的伴侣索菲娅暂时留在马德里，用户觉得这点挺难受。", "tags": ["relationship"], "owner": "user"}
  ],
  "intentions": [
    {"content": "用户计划下个月（2025年12月左右）搬到柏林。", "tags": ["housing", "relocation"], "valid_until": "2025-12-31", "owner": "user"}
  ],
  "basic_info": {}
}
```
四个以上话题——答辩、柏林公寓、学德语、伴侣在马德里。不要只提取第一个话题。注意一个轮次拆出了已完成的事实（已签租约）和一条 intention（即将搬家），关于索菲娅的情绪虽带情绪但含长期关系信息，因此保留。

## 示例 9 —— 两个真人：assistant 消息带 name 说明那是真人

新消息：
[{"role": "user", "name": "Tim", "content": "刚从攀岩馆回来，这周第三次了，那条仰角线路终于爬上去了。"},
 {"role": "assistant", "name": "John", "content": "厉害！我最近改游泳了，上个月伤的肩膀还疼。"}]
Memory Date：2025-08-14

输出：
```json
{
  "memory": [
    {"content": "用户每周攀岩三次，最近完成了一条难度较高的仰角线路。", "tags": ["fitness", "climbing"], "owner": "user"},
    {"content": "John最近改成游泳，因为2025年7月左右受伤的肩膀还在疼。", "tags": ["fitness", "health"], "owner": "user"}
  ],
  "intentions": [],
  "basic_info": {"name": "Tim"}
}
```
John 带的是 `role: "assistant"`，但他有 `name`，所以他是另一个真人而不是 AI：他的记忆用"John…"作主体，且 `owner` 仍是 `"user"`（属于用户的记忆库）。用户自己的记忆仍写"用户…"，即使已知他叫 Tim 也不能写成"Tim每周攀岩三次"。`basic_info.name` 来自用户那条消息上的 `name: "Tim"`——它是权威来源，即使 Tim 本人从未开口自报姓名。

## 示例 10 —— 反例：别人的属性绝不进 basic_info

新消息：
[{"role": "user", "name": "Tim", "content": "What have you been up to?"},
 {"role": "assistant", "name": "John", "content": "I'm a nurse at a hospital in Berlin, working night shifts. Pretty exhausting."}]
Memory Date：2025-09-02

输出：
```json
{
  "memory": [
    {"content": "John is a nurse at a hospital in Berlin, works night shifts, and finds it exhausting.", "tags": ["work", "health"], "owner": "user"}
  ],
  "intentions": [],
  "basic_info": {"name": "Tim"}
}
```
职业和所在地是 **John 的**属性，所以进 `memory` 并以"John"作主体，**绝不能**落到 `basic_info` 的 `occupation` / `location` —— `basic_info` 专属于用户本人。只有 `name`（Tim，来自用户消息的 `name` 字段）才该放进去。注意输出语言随输入语言（此例为英文）。

---
"""


# ================================================================
# SEARCH_QUERY PROMPT (中文版)
# ================================================================

SEARCH_QUERY_PROMPT_ZH = """你是一个搜索查询生成器。给定一组新提取的记忆，生成一组简短的搜索查询，用于在向量数据库中找到相关的已有记忆。

目标是最大化召回率——即使措辞差异很大，也要找到与新记忆语义相关的已有记忆。

## 新记忆:
{new_memories}

## 指引

生成的搜索查询应覆盖:
- 记忆中提到的关键主题、实体和主题
- 核心概念的改写或抽象版本
- 用户记忆库中可能存在的相关概念

输出一个 JSON 字符串数组（5-15条查询，简短聚焦）:

["查询1", "查询2", "查询3", ...]

只输出 JSON 数组，不要有其他文字。"""


# ================================================================
# RECONCILE PROMPT (中文版)
# ================================================================


RECONCILE_PROMPT_ZH = """你是一个记忆整合系统（Memory Reconciler）。

你的任务是将新的 memories 整合进已有 memory base，同时保持：

* 信息无损
* 无矛盾
* 高可检索性
* 结构紧凑
* 避免碎片化

---

# 输入内容

你将收到：

* Existing memories
* New memories
* Current date

---

# Existing memories

每条 existing memory 包含：

* memory_id
* content
* owner
* memory_at
* tags

---

# New memories

每条 new memory 包含：

* content
* owner
* memory_at（发生时间）
* tags

new memories 没有 ID。

---

# 操作类型

---

## ADD

当信息：

* 完全新
* 与已有 memory 不冲突
* 不属于已有主题

时使用。

格式：

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

当：

* 新旧 memory 描述同一主题
* 且没有冲突
* 合并后能提高完整性或可检索性

时使用。

要求：

* 无损合并
* 保留所有有效信息
* 不要产生重复 memory
* 两者在某个值上不一致时，保留 memory_at 更晚的那个值；绝不用更早的 memory 覆盖更新的值

### 同一次事件的补充 vs 另一次发生

只有当新 memory 是在补充**同一次现实事件**或同一条持久事实时，才使用 **UPDATE**。

如果同一个人在另一个时间再次发生同类活动/事件（另一次旅行、比赛、受伤、购买、健康事件等），
应保留为独立 **ADD**，并保留该次自己的日期与细节。如果已有 memory 明确是同一具体系列的另一次
发生，可添加 `related: [{{"memory_id":"...","relation":"same_series"}}]`。

不要把多个不同日期的事件合并成一条很长的次数/时间线 memory；检索层会从这些关联的 L2 组装 timeline。

格式：

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

仅在以下情况使用：

* 新 memory 与旧 memory 在同一维度冲突
* 两者不能同时为真
* 旧 memory 已不再代表当前状态
* 新 memory 的 memory_at 不早于旧 memory 的 memory_at

例如：

* 用户换城市
* 用户换工作
* 用户关系状态变化
* 用户长期偏好发生明确反转

不要用于：

* 增加细节
* wording 差异
* 同义表达
* 可共存偏好
* 新 memory 的 memory_at 早于旧 memory：它是更早的状态，绝不能取代更新的状态（见「时间规则」）

要求：

* content 只能写“新的状态”
* 不要复制旧信息
* 系统会自动保留旧节点并接到演化链上，你无需关心历史版本

格式：

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

### 把多条旧记忆折叠进同一条链

如果**两条或更多**已有记忆描述的是**同一维度**，而新记忆是该维度上的最新变化（它的 memory_at 晚于所有被折叠的记忆），
你可以把它们折叠进同一条演化链。此时用 `memory_ids`（有序列表，**旧→新**）
代替单个 `memory_id`：

```json
{{
  "op": "SUPERSEDE",
  "memory_ids": ["<最旧的id>", "<较新的id>"],
  "content": "<只写最新的状态>",
  "supersede_reason": "...",
  "owner": "user",
  "tags": ["..."]
}}
```

列出的旧记忆会按旧→新连成一条链，新记忆成为链头。`content` 仍然只描述**最新状态**——
**绝不**把旧记忆的内容抄进来；旧内容保留在链的历史里，依然可被检索。

---

# 关键规则

---

## 信息无损

所有有效信息必须仍然可检索。

不要丢失：

* 新 memory 中的信息
* 被 UPDATE/SUPERSEDE 的旧信息

注意：

SUPERSEDE 不需要复制旧内容，因为旧节点仍存在。

---

## 重复检测

如果 new memory 已经被 existing memory 完整覆盖：

不要输出任何操作。

---

## 避免碎片化

如果多个 memory：

* 讨论同一主题
* 内容兼容

优先 UPDATE 合并。

不同日期的独立事件保持独立 ADD；仅在主体相同且具体活动/事件类型相同时使用 `same_series`。

---

## Granularity

每条 memory 应只表达：

* 一个完整主题
* 一个清晰状态

避免：

* 过碎
* 过长
* 多主题混杂

---

## 主体规则

**绝不跨主体合并或 supersede。** 主体是 content 所描述的那个人："用户…" / "John…" / "Mary…"。
关于不同人的记忆是彼此独立的事实，即使句式几乎一样 —— "用户喜欢喝咖啡" 和 "John喜欢喝咖啡"
是两条各自独立的记忆，绝不能互相 UPDATE 或 SUPERSEDE。

---

## owner 规则

`owner`（`user`/`agent`）：从被写入的 memory 原样拷贝。
**绝不跨 owner 合并或 supersede** —— 用户的事实和 assistant 的事实即使主题相同也是两条不同记忆。

---

## 时间规则

每条 memory（新的和已有的）都带 memory_at，表示它发生的时间。记忆**不一定按发生顺序写入**：
历史可能晚导入，导入还没处理完时用户也可能手动添加记忆。所以一条 new memory 可能**比已有记忆更早**。

* 新旧**只看 memory_at**。"New memories" 只表示"正在写入"，不表示"更近"。
* 更早的 memory 绝不能取代更新的 memory：不能 SUPERSEDE 一条更新的记忆，也不能用 UPDATE 拿更早的值覆盖更新的值。
* 如果 new memory 比与它冲突的已有记忆更早：保留已有记忆作为当前状态；把 new memory 以"过去状态"ADD
  （用过去时，并带上时间，例如"2023 年用户住在上海"），没有新信息就不输出。
* `memory_at: null` 表示时间未知。未知**不算更新**：不要用 memory_at 为 null 的 memory 去 SUPERSEDE 一条有时间的记忆。
* 不要编造时间顺序。
* 不要仅因为“看起来更新”就 supersede。

---

## NO FORK RULE

* SUPERSEDE 要么指向一条 existing memory（`memory_id`），要么把**同一维度**的多条
  memory 折叠进同一条链（`memory_ids`，旧→新）；UPDATE 只能指向一条 existing memory。
* 同一条 existing memory 不得被多个操作 touch。
* 不要创建分叉 supersede 链。

---

## 可选字段：`related`

任何操作上都**可以**附加一个 `related` 数组，记录本次写入的 memory 与某条
**已有 memory** 之间的关系。它不改变存什么，只是额外记一条关联。

```json
{{
  "op": "ADD",
  "content": "...",
  "owner": "user",
  "tags": ["..."],
  "related": [{{"memory_id": "<已有记忆的 id>", "relation": "causes"}}]
}}
```

只允许这四种关系：

* `causes` —— 两条记忆构成因果（如"开始每天跑步" → "膝盖开始疼"）
* `elaborates` —— 对已有记忆的细化，或它的后续发展
* `contrasts` —— 两者存在张力但**都仍然成立**（如果一条让另一条不再成立，
  那是 SUPERSEDE，不是 `contrasts`）
* `same_series` —— 同一主体、同一具体活动或事件类型在不同时间的独立发生
  （例如两次不同的滑雪旅行）

要求：

* 只能关联到"已有记忆"里出现过的 id，**绝不编造 id**。
* 只在关系**明确且具体**时才输出。两条记忆只是属于同一个宽泛话题**不算**关系，
  这种情况不要输出 `related`。
* `same_series` 不能只靠 health、travel、sport、food、work 等宽泛主题；必须是同一主体的具体重复动作/事件。
* 没有可关联的就整个省略该字段 —— **零关联是常态**。
* 绝不要用 `related` 代替 UPDATE / SUPERSEDE。

---

{few_shot_section}
###################
下面是输入信息!
###################


当前日期：{current_date}

## 已有记忆（Existing memories）

{existing_memories}

## 新记忆（New Memories）

{new_memories}

# 输出格式

输出一个 JSON array。

例如：

```json
[
  {{
    "op": "ADD",
    "content": "...",
    "owner": "user",
    "tags": ["..."]
  }},
  {{
    "op": "UPDATE",
    "memory_id": "...",
    "content": "...",
    "owner": "user",
    "tags": ["..."]
  }},
  {{
    "op": "SUPERSEDE",
    "memory_id": "...",
    "content": "...",
    "supersede_reason": "...",
    "owner": "user",
    "tags": ["..."]
  }}
]
```

如果无需修改：

```json
[]
```

仅输出合法 JSON。

现在输出 JSON 数组。"""

RECONCILE_STRUCTURE_METADATA_PROMPT_ZH = """

附加结构输出契约：

每条 ADD、UPDATE 或 SUPERSEDE 结果都要保留或修正 `topic_paths`、`event_facet` 与
`topic_assignments`。
`topic_paths` 是从宽到窄的语义路径，例如 `["sport", "swimming"]`；不能仅凭共享宽 tag
猜父子关系。`event_facet` 只含 subject/participants/action/objects/locations/state。
`topic_assignments` 按主体给出带类型的 topic/facet/series/episode 路径；Series/Episode 必须
由事实本身明确支持，不能由路径深度推断；类型顺序必须遵守
`topic -> facet -> series -> episode`（可跳层，不可重复或倒序）。如果 Existing memories
中已有主体相同、语义等价
且边界兼容的路径，应复用其完全相同的节点名称和类型，避免同义节点分叉。需要精确复用时输出
`structure_references: [{"memory_id":"<existing_id>", "subject":"...",
"through":"topic|facet|series|episode"}]`；ID 必须来自 Existing memories，`through` 表示复制
该旧路径直到指定类型，新事实明确给出的更深后缀仍保持为新节点。主体、具体活动/状态线或
Episode 边界不同则禁止强行复用。所有结构字段均不得编码
when/how many/who、复数或序数等未来问句形态。

Series 只能在 reconcile 阶段建立：新 memory 与至少一条独立 Existing memory 必须是同一具体
活动/状态线的不同 occurrence。当前一次写入拆出的多个命题不能作为重复发生的证据。
"""


# 中文版 reconcile few-shot 示例（仅在 few_shot_enabled 时注入 {few_shot_section}）。
# 每个示例给出 existing/new 输入与期望的 ops，并附一句要点点评。ID 为简写。
RECONCILE_FEW_SHOT_ZH = """
###################
示例
###################

## 示例 1 —— 同一条新记忆：一部分与已有链冲突，另一部分无关

已有记忆：
[
  {"memory_id": "m1", "content": "用户在 Stripe 担任产品经理。", "owner": "user", "history_versions": [{"content": "用户在一家金融科技创业公司做产品经理。"}]}
]
新记忆：
[
  {"content": "用户已经从 Stripe 离职，现在在 Notion 担任产品负责人，另外最近周末开始学陶艺。", "owner": "user"}
]

输出：
```json
[
  {"op": "SUPERSEDE", "memory_id": "m1", "content": "用户在 Notion 担任产品负责人。", "supersede_reason": "雇主从 Stripe 变为 Notion", "owner": "user", "tags": ["work"]},
  {"op": "ADD", "content": "用户最近周末开始学陶艺。", "owner": "user", "tags": ["hobby"]}
]
```
**一条**新记忆里包含两件事：换工作（与已有雇佣链 m1 同维度，m1 已带历史 → SUPERSEDE，成为新链头）和一个无关细节（陶艺 → ADD）。要把这一条记忆按不同部分拆成不同操作——新链头只描述就业状态，绝不带上陶艺。

## 示例 2 —— 把两条未成链的旧记忆 + 新变化折叠成一条 3 节点链

已有记忆：
[
  {"memory_id": "a1", "content": "用户在学吉他。", "owner": "user", "memory_at": "2024-02-10 09:00"},
  {"memory_id": "a2", "content": "用户现在能完整弹几首吉他曲了。", "owner": "user", "memory_at": "2024-09-05 09:00"}
]
新记忆：
[
  {"content": "用户现在在本地的开放麦之夜表演吉他。", "owner": "user"}
]

输出：
```json
[
  {"op": "SUPERSEDE", "memory_ids": ["a1", "a2"], "content": "用户在本地的开放麦之夜表演吉他。", "supersede_reason": "吉他水平演进：学习 → 能弹曲子 → 登台表演", "owner": "user", "tags": ["music", "hobby"]}
]
```
a1 和 a2 原本没有链关系，但它们描述的是同一维度（吉他水平的进展），新记忆是最新一步。用 `memory_ids` 按旧→新折叠，它们成为一条 3 节点链（a1 ← a2 ← 新链头）。链头 `content` 只描述当前状态——不要再复述"在学"或"能弹几首"。

## 示例 3 —— 新链头绝不能复制旧记忆的内容

已有记忆：
[
  {"memory_id": "c1", "content": "用户租住在东京涩谷的一居室，月租 18 万日元，很喜欢走 5 分钟就到地铁站，但抱怨晚上很吵、还没有停车位。", "owner": "user"}
]
新记忆：
[
  {"content": "用户刚买下并搬进了横滨郊区的一套三居室房子，带花园和双车位车库。", "owner": "user"}
]

输出：
```json
[
  {"op": "SUPERSEDE", "memory_id": "c1", "content": "用户买下并搬进了横滨郊区的一套三居室房子，带花园和双车位车库。", "supersede_reason": "居住地从涩谷租住的一居室变为横滨自有的三居室", "owner": "user", "tags": ["housing"]}
]
```
新记忆 SUPERSEDE c1 并成为链头。链头 `content` 只描述横滨的新房子。**不要**把旧住处那些鲜明的细节（涩谷、18 万月租、走 5 分钟到地铁、吵闹、没停车位）拖进新节点——它们明显属于旧住处而非新家。这些保留在链历史里、仍可检索；抄进来会让链头自相矛盾（同时既在涩谷租房又在横滨买房）。

## 示例 4 —— 主体不同就绝不合并，措辞再像也不行

已有记忆：
[
  {"memory_id": "d1", "content": "用户每天早上喝黑咖啡。", "owner": "user"}
]
新记忆：
[
  {"content": "John每天早上喝黑咖啡。", "owner": "user"},
  {"content": "用户现在一天喝两次黑咖啡，早上和午饭后各一次。", "owner": "user"}
]

输出：
```json
[
  {"op": "ADD", "content": "John每天早上喝黑咖啡。", "owner": "user", "tags": ["food"]},
  {"op": "UPDATE", "memory_id": "d1", "content": "用户一天喝两次黑咖啡，早上和午饭后各一次。", "owner": "user", "tags": ["food"]}
]
```
两条新记忆措辞几乎一样，但**主体不同**。"John…"讲的是另一个人，所以只能 ADD——绝不能去 UPDATE 或 SUPERSEDE d1，哪怕 d1 对用户说的几乎是同一件事。只有主体同样是"用户"的那条才可以与 d1 合并。

## 示例 5 —— 同类事件再次发生：UPDATE 成次数 + 时间线（不要 ADD）

已有记忆：
[
  {"memory_id": "i1", "content": "John injured his ankle last season and needed time off plus physical therapy.", "owner": "user", "memory_at": "2023-05-21 19:48"}
]
新记忆：
[
  {"content": "John twisted his ankle again during a recent game.", "owner": "user", "memory_at": "2023-10-13 13:50"}
]

输出：
```json
[
  {"op": "UPDATE", "memory_id": "i1", "content": "John has injured his ankle two times: first last season (around the 2022–2023 season), needing time off and physical therapy; second during a game around 2023-10-13.", "owner": "user", "tags": ["health", "basketball", "injury"]}
]
```
同一类事件再次发生 → UPDATE 旧记忆：写出**总次数**，并按时间列出每次。不要再 ADD 一条 “John injured his ankle”，也不要 SUPERSEDE（第一次受伤并未变成假）。
"""


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
