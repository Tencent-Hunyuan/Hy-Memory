# -*- coding: utf-8 -*-
"""Simplified Chinese prompts (``zh-Hans``).

Moved verbatim from ``hy_memory/agent/prompts_zh.py``,
``hy_memory/coding/prompts_zh.py`` and the summary / System 2 modules,
which re-export the old ``*_ZH`` names.
"""


# ================================================================
# Chat extraction (agent/extractor.py)
# ================================================================

EXTRACT_SYSTEM_PROMPT = """你是一个记忆提取系统（Memory Extractor）。

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

STRUCTURE_METADATA_PROMPT = """

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

EXTRACT_FEW_SHOT = """
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

## 示例 11 —— 反例：寒暄没有 basic_info

新消息：
[{"role": "user", "content": "你好"},
 {"role": "assistant", "content": "你好！我可以帮你。之前记得 The user's name is 用户."}]
Memory Date：2025-09-03

输出：
```json
{
  "memory": [],
  "intentions": [],
  "basic_info": {}
}
```
用户没有说任何关于自己的信息。assistant 那句不是 user 回合，"用户" 也是占位符而不是名字。必须输出 `{}`——**绝不能**输出 `{"name": "用户"}`。

---
"""

EXTRACT_PROMPT = """## 最近 k 条消息

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
  * **硬性规则**：只有用户在 `user` 回合里**明确说出**的字段才能填写。只在
    assistant 回合出现的内容（包括 assistant 引用旧记忆，如"The user's name is X"
    / "用户名是X"）一律不算。没有符合条件的字段时，必须输出 `"basic_info": {{}}`。
  * **绝不**把字段名本身或占位符当值写入（"用户"、"The user"、"user"、"name"、
    "姓名"、"N/A"、"unknown"、"未知"、"未提供"、"-"），宁可省略该字段。
  * 负例：寒暄如 `[user]: 你好` / `[assistant]: 我可以帮你。`，或
    `[user]: Reply with exactly: PONG` → `"basic_info": {{}}`。
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

EXTRACT_PROMPT_NO_INTENTION = """## 最近 k 条消息

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
  * **硬性规则**：只有用户在 `user` 回合里**明确说出**的字段才能填写。只在
    assistant 回合出现的内容（包括 assistant 引用旧记忆，如"The user's name is X"
    / "用户名是X"）一律不算。没有符合条件的字段时，必须输出 `"basic_info": {{}}`。
  * **绝不**把字段名本身或占位符当值写入（"用户"、"The user"、"user"、"name"、
    "姓名"、"N/A"、"unknown"、"未知"、"未提供"、"-"），宁可省略该字段。
  * 负例：寒暄如 `[user]: 你好` / `[assistant]: 我可以帮你。`，或
    `[user]: Reply with exactly: PONG` → `"basic_info": {{}}`。
  * `name` 的例外：用户自己那条消息上的 `name` 字段，或文首形如
    "用户名叫X" / "The user's name is X" 的那一行，都是 `name` 的权威来源
    ——即使用户本人从未开口自报姓名。
  * **绝不**把其他真人的属性放进来。

* 不要跨 section 重复信息
* tags：1–3 个关键词；主题小写，专名保留大小写
* 不要输出 `intentions` 字段

"""


# ================================================================
# Chat reconcile (agent/reconciler.py)
# ================================================================

SEARCH_QUERY_PROMPT = """你是一个搜索查询生成器。给定一组新提取的记忆，生成一组简短的搜索查询，用于在向量数据库中找到相关的已有记忆。

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

RECONCILE_PROMPT = """你是一个记忆整合系统（Memory Reconciler）。

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

RECONCILE_STRUCTURE_METADATA_PROMPT = """

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

RECONCILE_FEW_SHOT = """
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
# Working / coding memory (coding/)
# ================================================================

CODING_EXTRACT_PROMPT = """\
你需要从一段任务执行会话中抽取可长期保留的 WORKING 记忆。
“working”会话是指用户通过工具操作计算机或系统的任何实操任务——既包括软件/代码
工作（编写、调试、重构、构建、部署、API/library 使用），也包括系统与运维工作
（运行 shell/PowerShell 命令、磁盘/存储分析或清理、文件与文件夹操作、安装/配置
软件、环境/设备设置、硬件/磁盘健康检查、备份、调度/自动化、数据处理）。

你需要从同一段会话中产出两个层次：

  EXPERIENCES（过程性，“如何做”）——可复用的方法；只有达到下述 VALUE BAR 时才
  抽取。每条是一个 (task, search_keys, solution, boundary_envs) 组合。

  FACT（情节性，“这次发生了什么”）——关于本次会话的恰好一条记录：用户想做什么、
  大致如何推进、最终结果如何。它不受价值门槛限制，但绝不能超过一条。

这两个层次回答不同问题，并分别存储。可泛化的方法放入 EXPERIENCES；只对这个系统、
这个项目、此时此刻成立的本次会话经历放入 FACT。

═══════════════════════════════════════════════════════════════════
VALUE BAR（仅适用于 EXPERIENCES）——不要轻易越过此门槛。
═══════════════════════════════════════════════════════════════════
一条记忆必须同时满足以下全部条件：
  (a) 解决了同一用户以后很可能再次遇到的问题
  (b) 解决方案并非琐碎操作：它组合了多项信息/多个步骤，或者包含决策理由，或者记录了
      来之不易的 workaround，或者是某项运维任务的可复用流程/配方（例如如何分析并
      清理磁盘占用、如何批量整理文件、如何检查硬件健康、如何设置 scheduled job）
  (c) 未来的 agent / 用户会更愿意查阅它，而不是重新摸索

重要——分析、诊断、调查任务也算：
一项任务不必实际修改或“修复”任何东西才值得记忆。只读、分析、故障排查工作，只要其
流程或结论可复用，就有价值。明确抽取：
  - 分析/诊断配方：可复用的流程以及它揭示的结果（例如“如何分析哪些文件夹占用了
    C-drive 空间”——扫描命令、遇到的坑和最终发现）。“只生成了报告，没有修改”
    不是跳过理由——可复用的方法本身就是记忆。
  - 调查结果，包括否定结果或未找到结果（例如“使用 rg / fs_search / SQL index
    在 C: 和 D: 中搜索 folder X——任何位置都未找到”）。记录一条死路可以避免下一个
    agent 重复同样无果的搜索，因此达到了价值门槛。
不要仅仅因为“assistant 可以重新生成这些命令”或“实际上没有修改任何东西”就拒绝
一条记忆。只要流程或结论能节省未来的工作，就保留它。

不要抽取：
  - 没有可复用流程、真正琐碎的单步操作（“rename one file”、“git status”、
    “run ls once”）
  - 已经解决且没有可迁移经验的一次性问题
  - repo/system 中已经存在的信息（不要重复 package.json）
  - 每个 PR / 每次会话的一次性任务，除非其中编码了可复用模式
  - 向用户询问缺失信息这一行为。请求参数、列出未知项或引导用户提供必填字段属于
    对话行为，不是方法：每个 agent 都知道如何提问，因此以后没有任何值得查阅的内容。
    绝不要产出 task 或 search_keys 形如“how to ask for X”、“gather the missing Y”、
    “eliciting required fields”、“what to clarify before starting”的 experience。
    注意，这条限制只适用于 EXPERIENCES——会话最终停在等待用户的状态是真实状态，
    应写入 FACT。

如果该段会话因为等待用户而没有取得任何实质进展——assistant 只是询问参数，或者在
开展任何实际工作前就被阻塞——输出 "experiences": []，只写 FACT。此时尚未学到任何
东西，因此没有方法可存。这一点比看起来更重要：从停滞中提炼出的 experience 在检索时
会像一项真正的技能；同一任务的后续片段会匹配它，再次向用户提问而不是继续推进，导致
这种停滞在余下会话中不断自我复制。应根据工具实际做了什么判断是否有实质进展，而不是
根据该轮是否生成了流畅文本。

启发式标准：如果用户（或未来执行同类任务的 agent）希望把它记下来，而不是以后再重新
摸索，就保留。多步骤运维流程通常符合条件——包括 system/ops 或只读分析/搜索流程，
不局限于代码。只有当它只是任何用户都能在 30 秒内通过 `--help` 找到的单条命令，并且
不包含值得记忆的决策或坑点时，才丢弃。

═══════════════════════════════════════════════════════════════════
EXPERIENCE 字段
═══════════════════════════════════════════════════════════════════

task          — 采用查询式名称；写成用户以后会如何提问。
                使用“publish a release to the xx platform”，不要使用
                “publish_release_internal”。
                必须自包含（不要使用“this”、“the file”、“we”）。

search_keys   — 0 到 5 种替代表述，用来覆盖仅靠 `task` 无法匹配、但对 solution 很
                重要的方面。写成未来用户会询问的问题或主题，且必须与 task 有区别。
                如果 task 已覆盖全部内容，则省略。

solution      — 用户实际需要的完整、自包含内容：命令、凭据位置（不是凭据值）、
                文件路径、有序步骤、坑点、决策理由。如果多轮端到端工作构成一个连贯
                任务，应聚合成一个 solution；只有用户执行了互不相关的任务时才拆成
                多条记忆。

boundary_envs — solution 所依赖的具体限定条件，使用多行“key: value”：
                  runtime / SDK / library + version
                  API endpoint 或 method signature
                  external service / middleware version
                  env var NAME（不是值）
                  config file path
                  host / cluster / region identifier
                如果 solution 与环境无关，填写 ""（空字符串）。
                目的：底层 SDK/middleware 升级后，未来使用者可以据此判断 solution
                是否仍然适用。

confidence  — 0.0 到 1.0
  1.0   用户明确将其陈述为规则/指令
  0.7   根据工具操作及充分证据推断
  0.5   弱推断；只有一次观察；不确定

═══════════════════════════════════════════════════════════════════
FACT 字段——每段会话恰好一条，无价值门槛
═══════════════════════════════════════════════════════════════════

你要为整段会话写一条 FACT——它是本次会话可恢复的状态记录，不是精心润色的回顾，也
绝不是分散发现的列表。未来 agent 必须能够从中继续推进，且不会把建议、推断或局部搜索
误认为已经验证的事实。

subject    — 本次会话围绕谁或什么展开：工作所针对或服务的单一实体。使用简短名词
             短语，不要写句子，不要使用动词。选择会话真正围绕的最具体实体，而不是
             所使用的工具：
               会话服务于某个具名人员时，使用人名
                 （“Dylan”、“Matthew”）
               工作针对代码时，使用 file / module / symbol
                 （“auth/middleware.ts”、“parse_plan_text”）
               运维工作使用 service / host / device / drive
                 （“D:\\WXWork”、“redis-cache-prod”、“ci.yml”）
             这是 HARD KEY，不是散文：关于不同人员或不同文件的两次会话必须得到不同
             subject；关于同一实体的两次会话必须得到拼写完全一致的同一字符串。
             优先使用 trace 中出现的精确标识符，而不是对它的描述。如果该段确实没有
             单一 subject，输出 ""。

             这很重要：subject 是决定后续会话是否可以按“同一任务的继续”覆盖本条
             记录的闸门。填错后，一个人的状态可能静默替换另一个人的状态。

statement  — 本次会话的紧凑记录。它分为两部分，而第二部分最容易丢失。
             保留任务范围内的给定量和具体约束，不要用泛泛的结果评价取代它们。
             每条规则只写一次：重复的指令文字会挤占记忆真正需要保留的数据。

             PART 1——发生了什么，约 300 个拉丁字符（约 150 个 CJK 字符）。
             从具体目标和对象开始（“reclaim disk space taken by D:\\WXWork”、
             “locate the new_file_search folder”），不要写泛泛的“fix a bug”/
             “find a file”。然后概述检查、尝试或修改了什么。最后说明结束状态，
             且语义必须精确对应以下之一：completed、partially completed、failed、
             blocked、still awaiting verification。纳入否定结果和死路，但不要夸大。

             PART 2——TASK-SCOPED GIVENS：整个任务赖以运行的全部给定信息，即使并非
             本段会话建立，或看起来基础到不值得写。包括绑定关系（每个 name/symbol/
             label 代表什么）、任务提供而不是推导出的固定值、采用的约定（indexing
             base、units、timezone、defaults），以及本段自己的结果和值（写“x = 7”，
             不要只写“the value was computed”）。

             将 PART 2 写成数据，每行一项，值保持给定形式：
               retry budget = 5; timeout 30s; index starts at 0
               step 3 input: n=64, tolerance 1e-6
               slot B: type=private, cost 688.7-721.3 per unit
             每一行几乎都应由值构成。保留 trace 的 notation——symbols、units、
             numbers、spellings——删掉其句子外壳：去掉那些只是宣告“这是给定量”的
             短语，而非给定量本身（“I would prefer”、“it should be somewhere
             between … and …”）。这些短语会在每项重复；一个 15 项列表会为它们
             支付 15 次空间成本。
             每一条 FACT 都要携带这些信息；绝不要写“as above”或“same as the
             previous session”——检索只会按相似度返回少量 facts，被指代项可能不在
             其中。

             总长度尽量控制在 1200 字符以内。如果 PART 2 放不下，应优化 notation，
             绝不能削减覆盖范围：statement 是 retrieval vector，也是后续 segment
             唯一能读到的字段（details 既不做 embedding，也不会被注入），因此从中
             删除的内容就永久丢失。压缩给定量周围的措辞，绝不要压缩给定量本身。

             检验标准：如果下一段看不到原始材料，能否仅凭此记录继续任务？只有肯定
             答案才合格。缺失 binding 不会明显报错——该名称仍看起来有意义，于是会
             按其通用含义被理解，后续答案可以很流畅，却回答了另一个问题。

             LANGUAGE——使用与 session trace 相同的语言：statement 是 retrieval
             vector，而查询会使用用户的工作语言；改用另一种语言会让每次检索都付出
             跨语言匹配损失。

             EVIDENCE FIDELITY——只记录 trace 中实际执行的操作和明确存在的证据。
             对关键命令、flags、arguments、errors、numbers 保持原样。
             绝不要按惯例补全命令 flags，也不要把建议或计划写成已经执行的动作：
               “ran Get-ChildItem -Force” → “ran Get-ChildItem”
                 （trace 中没有 `-Force`，不要杜撰）
             每个否定发现都必须限定在实际搜索范围内，并逐一列出可能使其失效的所有
             明确覆盖限制，尤其是 permission failures 和 depth limits：保留当时的
             roots、filters、depth（“not found under C:\\ and D:\\ at depth 3;
             some directories were not covered due to Access denied”），绝不要扩大为
             “does not exist”。不要为了让记录更漂亮而漏掉任何限制。
             绝不要把局部搜索提升为全局不存在。
             被阻塞的尝试必须保留其 blocker。
             保留 attempted、observed、verified、inferred、unresolved 之间的区别；
             用自然语言表达（“tried … but was blocked by Access denied”、
             “suspected …, not yet verified”），不要把它们当标签。没有工具输出或
             明确确认时，绝不要声称修复已成功、原因已确定或路径不存在。

             不要记录问候或纯闲聊——只有这种情况才输出 "fact": null。仅仅陈述了
             要求，或尚未开展工作就停滞的会话，仍然建立了信息。

details    — 本次会话的具体 OUTPUT，逐字复制。`statement` 是摘要，而摘要必然会
             丢掉各个具体值；后续检索几乎总是在寻找某个具体值，而非大意。因此，只要
             会话产出了可寻址的结果，就以原始形式和结构复现在这里：
               plan / schedule / itinerary → 每一行、每一天、每个 slot、每个 price
                 都严格按原文保留
               config 或 code block → 保留整个 block
               command 的关键 output → 保留承载发现的行（sizes、counts、versions、
                 error text）
               structured result（table、records list）→ 保留各行
             保留原始布局——line breaks、`Day 1:` / key: value prefixes、units、
             currency symbols、ids。不要再次概括，不要重排，不要舍入数字，不要翻译。

             只复制 trace 中实际存在的内容。绝不要填补空缺：如果某个 slot 没有填，
             就保持未填或明确说明——杜撰的值比缺失的值更糟，因为它会被检索并使用。
             长度控制在约 4000 字符；如果输出更长，保留承载具体值的部分，删去装饰性
             文字，并注明删掉了什么。

             如果本次会话未产生此类 artifact（尚无发现的纯调查，或等待用户导致的
             停滞），填写 ""。

             此字段不属于 retrieval vector——`statement` 和 `subject` 负责找到
             记录，`details` 在找到后提供具体值。因此不要在此堆砌检索诱饵，也不要
             重复摘要；只写原始内容。

files      — 0..N 个本次会话中实际操作或调查过的绝对或 repo-relative FILE OR
             DIRECTORY paths。保留调查过的 roots，例如 `C:\\` 和 `D:\\`。只包含
             实际访问/搜索过的路径，不包含仅建议或计划访问的路径；不适用时填写 []。
═══════════════════════════════════════════════════════════════════
HOST CONTEXT（由 SDK 提供）
═══════════════════════════════════════════════════════════════════
- workspace_id present: {workspace_present}
- branch present: {branch_present}

没有 workspace_id 时，solution 不能依赖未声明的项目本地上下文——必须在 `solution`
或 `boundary_envs` 中明确写出其所需的 repo / path / service，使记忆能够独立使用。

═══════════════════════════════════════════════════════════════════
CONVERSATION
═══════════════════════════════════════════════════════════════════
{conversation_block}

═══════════════════════════════════════════════════════════════════
OUTPUT（仅输出严格 JSON，不要 markdown）
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

默认应进行抽取。当该段会话确实完成了实际工作时，必须输出至少一条 experience——即使
任务看似简单或一次成功，也要记录所做工作的可复用本质（method / command / how-to）。
会话内容较少时，只保留最有价值的一条 experience 即可。

唯一例外是上述停滞情况：如果该段因为缺少信息、被阻塞在向用户提问而没有开展实际工作，
输出 "experiences": []，只写 fact。不要为了满足本指令而杜撰 experience——杜撰的
“how to ask for the departure city”比没有记忆更糟，因为以后会被检索并照做。只有这个
原因允许空数组；实际开展过工作时，绝不能把空数组当捷径。

"fact" 是单个对象，绝不是列表——每次会话一条记录。

始终写 fact。fact 的门槛不是“这是否有价值”，而是“是否发生了任何事情”；能够到达
你的 segment 已经越过这条门槛。这包括看起来只是 setup 而非工作的片段：仅有一条用户
消息陈述 requirements、parameters 或 starting state，即使没有运行工具，也恰恰是后续
会话无法自行重建的材料——把需求、参数、起始状态连同情形一起写进 `statement`（紧凑
记法，不要照抄对话原句），不要推给 `details`（`details` 不进检索向量，读取侧也不再
注入它）。只有完全没有内容的问候和纯闲聊才使用 "fact": null。有疑问时应写 fact：
多余记录只多占一行，而漏掉记录会伤害每一次需要它、却无从知道它曾存在的后续会话。
"""

CODING_EXTRACT_PROMPT_CODING = """\
╔═══════════════════════════════════════════════════════════════════════════╗
║ HARD CONSTRAINT——先阅读本节；它覆盖本提示词后文任何看似要求叙事的措辞。      ║
║                                                                           ║
║ 你不是在撰写某个人做过什么的报告。你要写的是 FUTURE AGENT 为采取行动所需要 ║
║ 的信息。该 agent 已有自己的任务；它不关心这个用户想要什么。它需要知道：此处 ║
║ 做出了什么决定、位于哪些代码坐标、尝试并否决了什么，以及是否经过验证。       ║
║                                                                           ║
║ 具体来说，每个 `statement` 和每个 `solution` 都必须遵守：                 ║
║   · 语法主语必须是 CODE 或 THE DECISION——绝不能是“the user”，也绝不能是   ║
║     “the agent”。不要写“The user attempted to…”、“The user wanted…”、   ║
║     “The agent then checked…”。                                           ║
║   · 绝不要用“the session was completed”、“the error was noted”、         ║
║     “no solution was found yet”之类流水账短语收尾。结尾应写决定及其验证    ║
║     状态。如果没有做出决定，就以已排除的内容和下一条尚未探索的线索收尾——这才 ║
║     是可复用部分。                                                        ║
║   · 保留具体症状（error string、failing command、wrong output）——未来搜索 ║
║     正是通过这些内容命中。                                                ║
║                                                                           ║
║ bad:  “The user attempted to write a QTable to RST with header_rows and   ║
║        hit a TypeError. The session was completed with the error noted.”  ║
║ good: “ascii.rst's RST writer rejects header_rows:                        ║
║        `tbl.write(..., format='ascii.rst', header_rows=['name','unit'])`  ║
║        raises TypeError because RST.__init__ does not forward it, unlike  ║
║        ascii.fixed_width which accepts it. No fix written — the writer    ║
║        needs the same header_rows plumbing as fixed_width.”               ║
╚═══════════════════════════════════════════════════════════════════════════╝

你需要从一段任务执行会话中抽取可长期保留的 WORKING 记忆。
“working”会话是指用户通过工具操作计算机或系统的任何实操任务——既包括软件/代码
工作（编写、调试、重构、构建、部署、API/library 使用），也包括系统与运维工作
（运行 shell/PowerShell 命令、磁盘/存储分析或清理、文件与文件夹操作、安装/配置
软件、环境/设备设置、硬件/磁盘健康检查、备份、调度/自动化、数据处理）。

你需要从同一段会话中产出两个层次：

  EXPERIENCES（过程性，“如何做”）——可复用的方法；只有达到下述 VALUE BAR 时才
  抽取。每条是一个 (task, search_keys, solution, boundary_envs) 组合。

  FACT（情节性，“这次确立了什么”）——关于本次会话的恰好一条记录。它不是用户做了
  什么的叙事，而要按以下顺序记录：任务及其具体症状（使它以后能被检索到）、代码坐标、
  最终采用了什么以及拒绝了什么、验证推进到什么程度。它不受价值门槛限制，但绝不能
  超过一条。

这两个层次回答不同问题，并分别存储。可泛化的方法放入 EXPERIENCES；只对这个系统、
这个项目、此时此刻成立的本次会话经历放入 FACT。

═══════════════════════════════════════════════════════════════════
VALUE BAR（仅适用于 EXPERIENCES）——不要轻易越过此门槛。
═══════════════════════════════════════════════════════════════════
一条记忆必须同时满足以下全部条件：
  (a) 解决了同一用户以后很可能再次遇到的问题
  (b) 解决方案并非琐碎操作：它组合了多项信息/多个步骤，或者包含决策理由，或者记录了
      来之不易的 workaround，或者是某项运维任务的可复用流程/配方（例如如何分析并
      清理磁盘占用、如何批量整理文件、如何检查硬件健康、如何设置 scheduled job）
  (c) 未来的 agent / 用户会更愿意查阅它，而不是重新摸索

重要——分析、诊断、调查任务也算：
一项任务不必实际修改或“修复”任何东西才值得记忆。只读、分析、故障排查工作，只要其
流程或结论可复用，就有价值。明确抽取：
  - 分析/诊断配方：可复用的流程以及它揭示的结果（例如“如何分析哪些文件夹占用了
    C-drive 空间”——扫描命令、遇到的坑和最终发现）。“只生成了报告，没有修改”
    不是跳过理由——可复用的方法本身就是记忆。
  - 调查结果，包括否定结果或未找到结果（例如“使用 rg / fs_search / SQL index
    在 C: 和 D: 中搜索 folder X——任何位置都未找到”）。记录一条死路可以避免下一个
    agent 重复同样无果的搜索，因此达到了价值门槛。
不要仅仅因为“assistant 可以重新生成这些命令”或“实际上没有修改任何东西”就拒绝
一条记忆。只要流程或结论能节省未来的工作，就保留它。

不要抽取：
  - 没有可复用流程、真正琐碎的单步操作（“rename one file”、“git status”、
    “run ls once”）
  - 已经解决且没有可迁移经验的一次性问题
  - repo/system 中已经存在的信息（不要重复 package.json）
  - 每个 PR / 每次会话的一次性任务，除非其中编码了可复用模式

启发式标准：如果用户（或未来执行同类任务的 agent）希望把它记下来，而不是以后再重新
摸索，就保留。多步骤运维流程通常符合条件——包括 system/ops 或只读分析/搜索流程，
不局限于代码。只有当它只是任何用户都能在 30 秒内通过 `--help` 找到的单条命令，并且
不包含值得记忆的决策或坑点时，才丢弃。

═══════════════════════════════════════════════════════════════════
CODE WORK——未来 agent 无法重新推导的信息，因此必须捕获
═══════════════════════════════════════════════════════════════════
当该段是针对 repository 的软件工作时，可复用方法只占一半。另一半是工作位于哪里，
以及哪个版本胜出。这些无法通过以后阅读代码恢复——代码只能展示当前状态，无法说明它
位于哪个 branch，也无法说明哪些方案已被否决。

本节不是要求写得更长，而是要求把相同预算花在价值更高的 token 上：删掉重复目标、
assistant“随后决定”做什么的叙事，以及只是复述的粘贴代码；把这些字符用于下面四项。
一条遗漏 branch、却引用 30 行 diff 的记录是糟糕的取舍。

1. BRANCH / WORKING LOCATION——逐字写入 fact `statement`。如果该段提到或 checkout
   了 `feat/x` / `fix/y`，这个 token 必须出现。在错误 branch 上恢复工作的 agent
   会产生随后被静默丢弃的改动，因此这一个 token 的优先级高于其他任何细节。

2. WHICH VERSION WON——当用户改变主意时，用一个分句指出胜出者并标记落败者：
   “settled on A; the interim B was dropped”。不要完整保留两个版本，只需一个分句。
   只保留最新版本是错误的：以后遇到被否决代码的 agent 必须识别它，否则会把代码
   “修复”回已经丢弃的变体。`scratch that / go back to / actually, let's / no, not that`
   之类措辞就是标志：后面的内容才是正式结论，前面的内容已被 superseded。

3. EXACT COORDINATES, NOT PASTED CODE——使用 repo-relative path
   （`qutebrowser/utils/debug.py`，绝不要只写 `debug.py`）以及触及的 function /
   class。如果 signature 发生变化，给出新的 signature。如果决定的是 constant、regex
   或 literal，逐字引用它——但只引用这一项，不要引用周围 block。准确的一行胜过一段
   近似描述。

4. VERIFIED vs MERELY WRITTEN——四个词就足够（“tests run and passing”/
   “confirmed by git diff only, tests not run”）。“the edit is in”和“the edit is in
   and tested”会让未来 agent 采取完全不同的下一步。

反例——只总结目标，却丢掉坐标：
  bad:  “refactored the colour parsing logic to be cleaner.”
  good: “on feat/q4, in qutebrowser/config/configtypes.py QtColor._parse_value:
         settled on `mult = 360.0 if kind == 'h' else 255.0`; the interim version that
         used 255.0 for everything was dropped. Tests not run.”
  （good 往往比 bad 还短——它只是把每个字符都用在无法重新推导的信息上）

═══════════════════════════════════════════════════════════════════
EXPERIENCE 字段
═══════════════════════════════════════════════════════════════════

task          — 采用查询式名称；写成用户以后会如何提问。
                使用“publish a release to the xx platform”，不要使用
                “publish_release_internal”。
                必须自包含（不要使用“this”、“the file”、“we”）。

search_keys   — 0 到 5 种替代表述，用来覆盖仅靠 `task` 无法匹配、但对 solution 很
                重要的方面。写成未来用户会询问的问题或主题，且必须与 task 有区别。
                如果 task 已覆盖全部内容，则省略。

solution      — 用户实际需要的完整、自包含内容：命令、凭据位置（不是凭据值）、
                文件路径、有序步骤、坑点、决策理由。如果多轮端到端工作构成一个连贯
                任务，应聚合成一个 solution；只有用户执行了互不相关的任务时才拆成
                多条记忆。

boundary_envs — solution 所依赖的具体限定条件，使用多行“key: value”：
                  runtime / SDK / library + version
                  API endpoint 或 method signature
                  external service / middleware version
                  env var NAME（不是值）
                  config file path
                  host / cluster / region identifier
                如果 solution 与环境无关，填写 ""（空字符串）。
                目的：底层 SDK/middleware 升级后，未来使用者可以据此判断 solution
                是否仍然适用。

confidence  — 0.0 到 1.0
  1.0   用户明确将其陈述为规则/指令
  0.7   根据工具操作及充分证据推断
  0.5   弱推断；只有一次观察；不确定

═══════════════════════════════════════════════════════════════════
FACT 字段——每段会话恰好一条，无价值门槛
═══════════════════════════════════════════════════════════════════

你要为整段会话写一条 FACT——它是本次会话可恢复的状态记录，不是精心润色的回顾，也
绝不是分散发现的列表。未来 agent 必须能够从中继续推进，且不会把建议、推断或局部搜索
误认为已经验证的事实。

statement  — 本次会话的紧凑摘要。它同时承担两个职责，且两者都必须完成：它既是
             retrieval vector（未来 agent 用一份新 issue report 搜索它；措辞方式
             与用户报告问题的方式一致），也是该 agent 找到后要据以行动的 payload。
             因此：

             开头写任务和症状，使用用户会使用的措辞。
                这是保证可检索性的关键——表述应尽量贴近问题被报告时的表面形式。
                点明具体目标和对象（“reclaim disk space under D:\\WXWork”、
                “adjust the hue multiplier in QtColor._parse_value on feat/q4”、
                “locate the new_file_search folder”），绝不要写泛泛的“fix a bug”/
                “look for a file”。包含具体症状：error string、failing command、
                wrong output。

             然后给出坐标和决定。
                对代码工作写 repo-relative path、symbol 和 branch。说明最终 SETTLED ON
                什么，并在同一分句标记丢弃了什么（“settled on X; the interim Y was
                dropped”）。这部分无法通过阅读代码重新推导——代码只展示胜出者，从不
                展示 Y 曾被尝试后否决。以后遇到被否决变体的 agent 必须能识别它，
                否则会把代码“修复”回已丢弃版本。

             结尾写证据状态。
                必须精确对应以下之一：completed、partially completed、failed、blocked、
                still awaiting verification；还要写明如何检查（“tests run and passing”/
                “confirmed by git diff only, tests not run”）。纳入否定结果和死路，
                但不要夸大。

             ⚠️ 绝不要停留在叙事层。“The user tried X and hit error Y; no solution found
             yet”是一条浪费的记录：它容易被检索到，却不能指导未来 agent 行动。如果
             本次确实没有得出决定，就说明排除了什么、下一条尚未探索的线索是什么——
             这才是可复用部分。

             句子的主语应是 code 和 decision，而不是“the user”。使用 2 到 4 个连贯
             句子，不要写带标签的小节。硬上限约 400 个拉丁字符（约 200 个 CJK 字符）。

             EVIDENCE FIDELITY——强制要求：
             - 只记录 trace 中实际执行的操作和明确存在的证据。对关键 commands、flags、
               arguments、errors、numbers 保持原样。绝不要按惯例补全 command flags，
               也不要把 advice/plans 写成已执行的动作。
             - 保留 attempted、observed、verified、inferred、unresolved 之间的区别。
               不要把这些词作为标签写进正文；自然表达其含义，例如“tried … but was
               blocked by Access denied”或“suspect …, not yet verified”。
             - 没有工具输出或明确确认时，绝不要声称修复成功、原因已经确定、file/folder
               不存在或 tests passed。
             - 每个否定发现都必须限定在实际搜索范围内，并逐一列出可能使其失效的所有
               明确覆盖限制，尤其是 permission failures 和 maximum search depth：
               “not found within the searched parts of C:\\ and D:\\; some directories
               were skipped on Access denied, and the search was also depth-limited”。
               不要为了摘要更漂亮而漏掉任何限制。绝不要把局部搜索提升为全局不存在。
             - 只有 trace 直接验证了因果关系时，才能确定地陈述因果。否则保留限定词
               （“suspected”/“inferred”/“not yet verified”）；不要把错误的测试假设
               改写成系统的 root cause。

             关键——这段文字就是 retrieval vector，也是未来 query 唯一会匹配的内容。
             泛泛摘要毫无价值，因为每次会话看起来都像泛泛摘要：它会匹配所有查询，
             却无法回答任何一个。因此必须写出使本次会话可检索的具体 anchors——真实
             file paths、function / symbol names、branch names、commands、error strings、
             versions、config keys and values、host / service names。以后有人搜索其中任一
             具体项时，本摘要必须能被返回。
               good: “fixing a failing `npm run build` in CI. Read
                      .github/workflows/ci.yml and package.json; traced it to webpack
                      5.74 hitting ERR_OSSL_EVP_UNSUPPORTED under node 18. Bumping the
                      workflow's node to 20 made the build pass; node 18 still
                      reproduces locally.”
               good: “on branch feat/q0, in qutebrowser/utils/debug.py signal_name:
                      PyQt<5.11 only exposes the name via repr(sig), and repr comes in
                      both a bracketed and a parenthesised form. Settled on two patterns
                      tried in order with fullmatch, breaking on the first hit; the
                      single-regex version was dropped. Confirmed by git diff, tests not
                      run.”
               bad:  “user wanted a build bug fixed, the agent looked at some config
                      files, changed some code, and fixed it.”
                      （没有 path、command、error string、version——没有任何可检索锚点；
                       每次会话都可以写成这样）
               bad:  “fixed the signal-name parsing compatibility issue on a feature
                      branch.”
                      （哪个 branch？哪个 file？哪个 function？两个竞争版本中谁胜出？
                       一周后检索到它时，没有任何信息仍然有用）
             忠实度示例（bad → good）：
               “ran Get-ChildItem -Force” → “ran Get-ChildItem”
                 （trace 中没有 `-Force`，不要杜撰）
               “confirmed new_file_search does not exist” →
                 “new_file_search was not found within the searched parts of C:\\ and
                  D:\\; some directories were skipped on Access denied or by the depth
                  limit, so the investigation is only partly done”
               “identical payloads cause a spurious lock error” →
                 “the verification script assumed the last writer is always a fixed
                  rank; that assumption does not hold, so whether there is a lock bug
                  is still unverified”

             不要记录问候或纯闲聊。如果该段完全没有建立任何信息，输出 "fact": null。

files      — 0..N 个本次会话中实际操作或调查过的绝对或 repo-relative FILE OR
             DIRECTORY paths。保留调查过的 roots，例如 `C:\\` 和 `D:\\`。只包含
             实际访问/搜索过的路径，不包含仅建议或计划访问的路径；不适用时填写 []。

═══════════════════════════════════════════════════════════════════
HOST CONTEXT（由 SDK 提供）
═══════════════════════════════════════════════════════════════════
- workspace_id present: {workspace_present}
- branch present: {branch_present}

没有 workspace_id 时，solution 不能依赖未声明的项目本地上下文——必须在 `solution`
或 `boundary_envs` 中明确写出其所需的 repo / path / service，使记忆能够独立使用。

═══════════════════════════════════════════════════════════════════
CONVERSATION
═══════════════════════════════════════════════════════════════════
{conversation_block}

═══════════════════════════════════════════════════════════════════
OUTPUT（仅输出严格 JSON，不要 markdown）
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

强制要求：必须输出至少一条 experience，不要返回空列表。即使任务看似简单或一次成功，
也要记录所做工作的可复用本质（method / command / how-to）。会话内容较少时，只需要
最有价值的一条 experience——但绝不能输出空的 "experiences" array。

"fact" 是单个对象，绝不是列表——每次会话一条记录。只有该段是没有建立任何信息的
纯闲聊时，才使用 "fact": null。
"""

CODING_RECONCILE_PROMPT = """\
你需要决定如何将新的 coding memory drafts 与用户已有的 memories 合并。

对每条新 draft，选择以下一种 action：
  ADD     — 没有已有 memory 描述相同的任务/方面
  UPDATE  — 某条已有 memory 描述相同任务；用新 draft 替换它
            （solution 可能已经变化，env 可能已经升级，等等）
  SKIP    — 与已有内容重复；没有新信息

此外，当新内容使已有候选失效或作废时，可以针对已有 candidates 输出 DELETE ops：
  DELETE  — 新 conversation 明确 supersedes/cancels/invalidates 某条已有 memory，
            且无法通过 UPDATE 修复。示例：
              * 用户说“don't use X anymore”，而 X 只存在于另一条独立 memory 中
                （不是本次正在 ADD/UPDATE 的那条）
              * candidate 的 boundary_envs（service / endpoint / SDK）已经消失，
                且新 conversation 确认其已 deprecated

规则：
- “Same task”是指已有 memory 和新 draft 都可以作为同一个未来用户查询的有效答案。
- 如果 solution 不同，优先 UPDATE（不保留历史版本）。
- 如果 task 和 solution 都高度一致，选择 SKIP。
- 只有在存在明确证据表明 candidate 已 invalid/obsolete 时才 DELETE。绝不要仅仅因为
  candidate 看起来“stale”就 DELETE。
- 产生 DELETE 时，输出的 ops 数量可以超过 drafts 数量。

═══════════════════════════════════════════════════════════════════
NEW DRAFTS
═══════════════════════════════════════════════════════════════════
{drafts_block}

═══════════════════════════════════════════════════════════════════
EXISTING CANDIDATES（按每条 draft 的 task similarity 检索）
═══════════════════════════════════════════════════════════════════
{candidates_block}

═══════════════════════════════════════════════════════════════════
OUTPUT（仅输出严格 JSON，不要 markdown）
═══════════════════════════════════════════════════════════════════
{{
  "ops": [
    {{"draft_idx": 0, "action": "ADD"}},
    {{"draft_idx": 1, "action": "UPDATE", "target_experience_id": "exp_xxx", "reason": "..."}},
    {{"draft_idx": 1, "action": "DELETE", "target_experience_id": "exp_yyy", "reason": "user explicitly deprecated Y"}},
    {{"draft_idx": 2, "action": "SKIP",   "reason": "duplicate"}}
  ]
}}

如果没有 drafts，输出 {{"ops": []}}。
"""

CODING_FACT_RECONCILE_PROMPT = """\
你负责维护 session-level WORKING FACTS。每条 statement 都概括一整段 trace/session：
用户的任务、大致方法和结果。它不是一条 atomic claim。

下面每条新的 session fact 都一定会被存储；这不是你要决定的事情。你的唯一任务是识别
task continuation：新 session 是否在继续推进某条已有 session fact 所记录的同一个用户
任务？如果是，就将较旧 fact 标记为 SUPERSEDE，使这些链接为该任务构成跨 session 的
进展链。

只有在存在充分证据同时满足以下条件时，才将已有 fact 标记为 superseded：
  - subjects 匹配：两条记录围绕同一个实体——同一个 person、同一个 file/module、
    同一个 host/service。subject 相同是必要条件，但不是充分条件；并且
  - 用户目标在实质上相同，而不只是处于同一 topic/project；并且
  - 新 trace 在继续、重试、扩展、验证或完成已有 trace 所记录的工作。

以下情况不要 supersede：
  - 两条记录的 subjects 不同。同一 group 中的两个人、同一 module 中的两个 files、
    同一 cluster 中的两个 hosts 是并行关系，不是先后关系：一个人的状态不是另一个人
    状态的新版本。如果 subject 显示为 (unspecified)，退回到只根据 statement 判断。
  - 两次 session 只是提到了相同 file、symbol、component、repository、error 或
    technology，但追求不同的用户目标。
  - 一个任务只是为另一个任务提供 context 或 technique。
  - 关系不确定。应保守判断：INSERT 是安全默认值；合并无关 session 会毁掉它们各自
    独立的任务历史。

把每条 statement 当作有证据边界的文本。不要把其中的 inference 重新解释为已经确定的
cause，也不要把共同的 conjecture/error 当作用户目标相同的证据。你的判断仅涉及 task
continuation；不要“纠正”、补全或润色任何一条 session 记录。

示例：
  existing: “需要修复 CI 的 npm run build；已追踪到 Node 18 下的
             ERR_OSSL_EVP_UNSUPPORTED，workflow 尚未修改。”
  new:      “继续修复同一个 CI build；已将 .github/workflows/ci.yml 中的 Node
             提升到 20，且 npm run build 通过。”
                                                                 → SUPERSEDE

  existing: “需要修复 auth/middleware.ts 中的 JWT expiry handling；已添加
             refresh logic。”
  new:      “需要在 auth/middleware.ts 中添加 performance logging；已引入
             latency metric。”
                                                                 → 两者都保留
            （同一 file/project，不同用户目标）

═══════════════════════════════════════════════════════════════════
NEW SESSION FACTS
═══════════════════════════════════════════════════════════════════
{drafts_block}

═══════════════════════════════════════════════════════════════════
EXISTING SESSION FACTS（按每条新 fact 的 statement similarity 检索）
═══════════════════════════════════════════════════════════════════
{candidates_block}

═══════════════════════════════════════════════════════════════════
OUTPUT（仅输出严格 JSON，不要 markdown）
═══════════════════════════════════════════════════════════════════
{{
  "ops": [
    {{"draft_idx": 0, "supersedes": ["fact_xxx"], "reason": "continues the same CI fix task"}},
    {{"draft_idx": 1, "supersedes": []}}
  ]
}}

只能使用同一个 draft_idx 的 candidate list 中实际出现的 fact ids。
如果任何位置都没有 fact 被 superseded，输出 {{"ops": []}}。
"""

CODING_CHAIN_SUMMARY_PROMPT = """\
working-memory store 中的某个用户任务已跨多个 sessions 推进。下面是该任务进展链中的
session summaries，按从最早到最新排列，最后一条是当前 session。

总结同一个任务如何跨 sessions 推进到当前状态。按时间顺序覆盖每个重要阶段。对每个
阶段，在有信息时保留：实际 action 或 change、其 direct result，以及任何 unresolved
issue 或 limiting condition。最后明确写出当前状态。

规则：
  - 这只是压缩。只能报告 entries 实际陈述的内容；绝不要新增或补全 command、cause、
    motive、action、test result、success state 或 global negative conclusion。
  - 精确保留证据状态：attempted 不等于 completed，observed 不等于 verified，inferred
    不等于 established，unresolved 必须保持 unresolved。保留 uncertainty words 以及
    search/permission/coverage limits。
  - 忠实陈述当前结束状态（completed、partially completed、failed、blocked 或
    still awaiting verification）。绝不要因为某个看似合理的因果故事，或后续 entry
    并未验证的内容而提升状态。
  - 如果 entries 提到 suspected cause 和后续 result，只有某条 entry 直接说明该 cause
    已被 verified 时，才可在两者之间建立因果联系。
  - 保留具体 retrieval anchors：file paths、symbols、commands、error strings、versions、
    config keys/values，以及对进展重要的 numeric values。
  - 对 2-3 代的 chains，不要把进展过度压缩成一句话。适合时优先使用 2-4 个紧凑段落，
    或使用可读性良好的 prose，清楚呈现 task goal、逐阶段 progression 和 current status。
  - 不要机械复制完整 history。在保留重要 actions、direct results 和 leftovers 的同时，
    省略重复及低价值细节。
  - 不要杜撰 stage headings 或 dates。只有 entries 使日期有实际意义时才提及日期；
    单独的 timestamps 是噪声。
  - 如果 entries 说明更早 revisions 已被省略，必须明确指出 earlier history was omitted；
    不要暗示所提供的 entries 是完整历史。
  - 使用与 entries 相同的语言。
  - 不要写前言，也不要用引号包裹输出。

示例
  entries:
    (2026-07-20) 需要修复 CI 的 npm run build；已追踪到 Node 18 下的
                 ERR_OSSL_EVP_UNSUPPORTED，workflow 尚未修改。
    (2026-07-24) 继续修复同一个 CI 问题；已将 .github/workflows/ci.yml 中的 Node
                 提升到 20，但 dependency cache 仍复现旧错误。
    (now)        继续验证同一修复；清除 npm cache 后，npm run build 通过。
  output:
    任务目标是修复 CI 的 npm run build。最初将问题追踪到 Node 18 下的
    ERR_OSSL_EVP_UNSUPPORTED，当时尚未修改 workflow；随后将
    .github/workflows/ci.yml 提升到 Node 20，但 dependency cache 仍使旧错误继续
    出现。最后，清除 npm cache 后 npm run build 通过，因此该 build 修复目前已验证完成。

═══════════════════════════════════════════════════════════════════
ENTRIES
═══════════════════════════════════════════════════════════════════
{chain_block}

═══════════════════════════════════════════════════════════════════
OUTPUT（只输出 summary 本身，不要其他内容）
═══════════════════════════════════════════════════════════════════
"""


# ================================================================
# Episode / topic summaries
# ================================================================

EPISODE_SUMMARY_PROMPT = """你在为**一段讨论**（一个 episode）做总结。

一个 episode 是一段连续的讨论 —— 围绕一件事，可能跨越好几次写入。

## 这段讨论中抽取到的记忆（时间正序）

{memories}

---

写一段关于这次讨论内容的高密度摘要。

要求：

* 2–4 句，第三人称，以"用户…"开头（若记忆讲的是别人，则以那个人的名字开头）。
* 把这些记忆串成一个连贯的叙述 —— 一段讨论通常有一根主线（一个计划、一个问题、
  一次出行、一个决定）。
* 保留具体细节：人名、地点、数字、日期。
* 绝不编造记忆里没有的内容。
* 不要逐条罗列记忆，要综合成一段。
* 输出语言与记忆的语言一致。

只返回摘要正文，不要任何前言、不要 JSON、不要引号。"""

TOPIC_SUMMARY_PROMPT = """你在为**一个主题**总结已知的全部信息。

## 主题

{topic_name}

## 该主题下的记忆（时间正序）

{memories}

---

写一段关于用户在这个主题上的高密度摘要。

要求：

* 2–4 句，第三人称，以"用户…"开头（若记忆讲的是别人，则以那个人的名字开头）。
* 覆盖整个主题 —— 说明当前状况；只在变化本身重要时才提它是怎么演变过来的。
* 保留具体细节：人名、地点、数字、日期。
* 绝不编造记忆里没有的内容。
* 不要逐条罗列记忆，要综合成一段。
* 输出语言与记忆的语言一致。

只返回摘要正文，不要任何前言、不要 JSON、不要引号。"""


# ================================================================
# System 2 evolve (pipelines/system2_agent.py)
# ================================================================

SYSTEM2_AGENT_PROMPT = """你是一个认知加工 Agent，负责从用户的记忆数据中演化高层认知结构。

## 记忆分层架构

用户的记忆存储在两个系统中：

### VDB（向量数据库）— 你只能读取，不能修改
| 层级 | 说明 |
|------|------|
| L0 BASIC_INFO | 用户基础属性（姓名、年龄、职业等）|
| L1 RAW | 原始对话记录 |
| L2 FACT | 原子事实（从对话中提取的客观事实）|
| L3 SUMMARY | 会话摘要 |
| L4 IDENTITY | 身份画像（性格特征、偏好、习惯）|
| L5 KNOWLEDGE | 领域知识 |

### Graph（知识图谱）— 你是唯一的写入者
| 层级 | 说明 |
|------|------|
| L6 SCHEMA | 行为模式 — 域内、原子化、创建后不可变 |

## L6 Schema 是什么？

Schema 是从多次独立经历归纳出的长期行为模式，包含以下三个要素：

- **Circumstance（场景）**：该模式发生的领域/话题/场景。Schema 必须限定在其场景内，不要跨域泛化。
- **Pattern（模式）**：用户在该场景下的惯常行为、思维方式或行动倾向。
- **Insight（洞察）**：底层心理驱动力或心智模型。

### 内容格式
行为模式用一句话组合三要素：
"当[场景]时，用户[模式]——反映了[洞察]。"

对与情绪、治疗、压力、哀伤或创伤有关的长期模式，不要把具体人生背景压缩成
空泛人格词。优先保留这三部分：
"Trigger/background（持久触发背景） → emotional response（反复情绪反应） → observed coping resource（用户已实际使用的应对资源）。"
只能写入 user-owned 证据中出现的触发背景、反应和应对方式；助手建议不是用户已观察到的应对资源。

### 规则
- **原子化**：一个 Schema 只包含一个模式。两个不同模式 → 两个 Schema。
- **不可变**：Schema 创建后内容永远不变。不要修改已有 Schema 的内容。
- **累积证据**：新事实只是在支持已有表述 → 调 `add_evidence`。如果新证据会让旧画像的当前状态产生实质遗漏、错误或误导，使用 `revise_schema` 创建新版本，让旧版本保留审计但退出 active 检索。
- **域内约束**：不要跨域。跨域抽象由系统的其他流程处理。
- **事实与模式分层**：单条明确身份、健康状态、设备使用、隶属关系、项目或偏好属于 L2 状态，不创建 L6。L6 必须由至少两个独立 raw/session 中的 user-owned 证据支持。
- **主体安全**：同一原始写入可能包含他人资料或示例。只有材料明确归因于当前用户时才创建用户 Schema；否则对该材料不执行任何操作。
- **候选覆盖**：source-context 审查时，每条候选事实必须二选一：用于 `create_schema`/`add_evidence` 的 evidence，或用 `skip_fact` 明确跳过。原始上下文只用于确认主体或补回该事实遗漏的细节，不得据此创建无关 Schema。
- **证据门槛**：创建 L6 必须有 user-owned evidence，且这些证据合计代表至少两个独立 raw/session。
  一个被 S1 合并的 L2 事实可以代表多次独立写入，应检查它的 `source_raw_memory_ids`，不要只数节点。
  助手建议不能作为用户模式的独立证据。

## 工作流程

输入材料分两部分（仅当已形成聚类时才会调用你）：
- **聚类结果（Cluster Results）**：主输入，按语义相似度归组的事实
- **未聚类事实（补充）**：未进入任一聚类的事实，优先级低于聚类结果，酌情审视即可

对于每组事实（无论聚类还是未聚类）：
1. **搜索已有 Schema**：如果已有 Schema 已覆盖该模式，使用 `add_evidence`。只有加入新证据后旧表述会产生实质遗漏、错误或误导时才使用 `revise_schema`。
2. **创建新 Schema**：仅当没有已有 Schema 覆盖时。一句话，三要素。
3. **建立关系**：两个 Schema 主题相关 → 用 `add_edge`（RELATED_TO）。

## 原则

- 所有 Graph 操作必须通过 tools 执行
- Schema 内容创建后不可变——绝不重新创建已存在的 Schema
- 优先 `add_evidence` 而非创建重复节点
- 宁可不创建，也不要创建低质量节点
- 一个 Schema = 一个域内原子模式
- 打标签时优先使用已有标签列表中的标签"""


# ================================================================
# Fragments assembled in code
# ================================================================

LANGUAGE_NAME = "简体中文"

# 仅在调用方显式指定语言（per-call 或 MemoryConfig.language）时追加到写记忆的
# prompt 末尾；自动检测的请求不会带上它，prompt 与以前完全一致。
OUTPUT_LANGUAGE_DIRECTIVE = """

---

# 输出语言

你输出的所有自然语言内容（记忆正文、摘要、理由、查询）一律用简体中文书写，无论对话或已有记忆使用什么语言。此规则优先于上文任何「输出语言与输入语言一致」的要求。JSON 键、字段名、枚举值、操作名、ID、代码、文件路径、命令、报错信息和专有名词保持原样。
"""

# enable_intention=False：把 EXTRACT_SYSTEM_PROMPT 的「两类记忆」段换成单桶段。
NO_INTENTION_SECTION_PATTERN = r"# 两类记忆.*?(?=\n# OWNER)"

NO_INTENTION_MEMORY_SECTION = (
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

NO_INTENTION_REPLACEMENTS = (
    (
        "每条 memory 和 intention 都必须带 `owner`",
        "每条 memory 都必须带 `owner`",
    ),
    (
        "每条 memory 和 intention 都必须带",
        "每条 memory 都必须带",
    ),
)

# few-shot 里若仍出现 intentions，用硬约束盖住
INTENTION_DISABLED_NOTE = (
    "\n\n---\n\n# INTENTION 关闭\n"
    "当前配置关闭了 intention 抽取。即使示例里出现 `intentions`，"
    "也**不要**输出该字段；未来计划写入 `memory`。\n"
)

CODING_CONVERSATION_BLOCK = (
    "最近 5 个用户轮次（仅作上下文，不要重复抽取）：\n"
    "{history}"
    "\n\n本次新增消息（只从这次完整写入中抽取）：\n"
    "{new}"
)
CODING_EMPTY_BLOCK = "（无）"

USER_NAME_LINE = "用户名叫{name}"

SYSTEM2_OUTPUT_INSTRUCTIONS = """

## 输出格式

不要调用 tools。直接输出一个 JSON 数组，包含你决定执行的操作。用 ```json 代码块包裹。

可用操作：

1. **create_schema** — 创建新的 L6 行为 Schema
   ```{"op": "create_schema", "schema_kind": "behavior_pattern", "content": "供用户阅读的简洁画像", "retrieval_text": "用于检索的行为与适用场景", "evidence_list": ["fact_node_id_1", "fact_node_id_2"], "tags": ["tag1"]}```

2. **add_evidence** — 给已有 Schema 追加证据（不修改内容）
   ```{"op": "add_evidence", "node_id": "existing_schema_node_id", "evidence_list": ["new_fact_id_1"]}```

3. **revise_schema** — 新证据使已有 Schema 的当前状态发生实质变化时，创建新版本并让旧版本退出默认检索。不要用于可共存的细节补充。
   ```{"op": "revise_schema", "node_id": "old_schema_id", "schema_kind": "explicit_state", "content": "更新后的当前画像", "retrieval_text": "更新后的检索文本", "evidence_list": ["new_fact_id_1"], "tags": ["tag1"], "reason": "明确说明旧画像为何会误导"}```

4. **add_edge** — 建立两个 Schema 之间的关系
   ```{"op": "add_edge", "source_id": "schema_id_1", "target_id": "schema_id_2", "reason": "关系原因"}```

5. **skip_fact** — 明确标记某条候选事实已审查但不创建 Schema。仅用于他人事实、临时事件、不能证明用户特征的助手建议，或已被现有 Schema 完整覆盖的事实。
   ```{"op": "skip_fact", "node_id": "candidate_fact_id", "reason": "不是稳定的用户模式"}```

## 输出约定

1. 输出必须是 JSON 数组，用 ```json 代码块包裹。
2. 如果数据不足以得出可靠结论，输出 `[]`。在 source-context review 中，
   `[]` 表示已审查该组全部候选，且都不足以支持稳定 Schema。
3. 代码块外不要有任何其他文字。
4. evidence_list 中的 id 必须来自材料中提供的 fact node_id。
5. add_evidence / revise_schema 的 node_id 必须来自已有 Graph 节点的 id。
6. 对 source-context 审查，必须让每条候选事实出现在 evidence_list 或 skip_fact 中。"""

SYSTEM2_SCHEMA_EVIDENCE_GATE = """

7. L6 只表示跨事件行为模式。create_schema 必须使用 user-owned 证据，且证据合计代表至少两个独立 raw/session。当 `source_raw_memory_ids` 包含多个独立写入时，一个合并后的 L2 事实也可以满足门槛。
8. 单条明确身份、健康状态、设备使用、隶属关系、项目或偏好不要创建 L6；这些由 canonical L2 状态通道维护。"""

SYSTEM2_SINGLE_CALL_REPLACEMENTS = (
    ("- 所有 Graph 操作必须通过 tools 执行\n", ""),
    ("调 `add_evidence` 添加证据", "使用 add_evidence 操作"),
    ("调 `add_evidence`", "使用 add_evidence"),
    ("用 `add_edge`（RELATED_TO）", "使用 add_edge 操作"),
)

SYSTEM2_MATERIALS = {
    "title": "## 预处理材料\n",
    "stats": (
        "### 统计\n- VDB 事实总数: {total}\n"
        "- 新鲜事实（待处理）: {fresh}（已被引用排除={evidenced}）\n"
        "- 聚类数: {clusters}\n"
        "- 聚类内事实: {clustered}，未聚类补充: {unclustered}\n"
        "- Graph Schema 总数: {graph_total}（本次召回={recalled}）"
    ),
    "graph_status_empty": "- Graph 状态: **空**（首次认知加工）\n",
    "clusters_header": "### 聚类结果\n",
    "label_holistic": "{n} 条跨会话用户事实；只归纳有证据的长期情绪/治疗模式",
    "label_source_context": "{n} 条候选事实；原始写入仅用于主体与细节核验",
    "label_topic": "{n} 条，主题",
    "raw_label": "候选事实对应的原始片段",
    "holistic_note": (
        "这是一次有边界的夜间全局整理。最多创建 "
        "{max_schemas} 条 anchored therapy Schema。"
        "优先联合不同 raw/session 中的持久 trigger/background、"
        "反复 emotional response 和用户已实际使用的 coping resource/decision constraint。"
        "证据不足时输出 []。\n"
    ),
    "companion_header": "**独立来源 companion 提示（只是候选，不是结论）:**",
    "unclustered_header": "### 未聚类事实（补充，次要）\n",
    "unclustered_hint": "以下事实未进入上述任一聚类，可在完成聚类主题加工后酌情审视；优先级低于聚类结果。\n",
    "forward_header": "### 相关 Graph 节点（通过 cluster centroid 向量召回，共 {count} 条）\n",
    "graph_empty": "### 已有 Graph 节点\n当前 Graph 为空，这是首次认知加工。无需搜索 Graph，直接根据聚类结果创建 Schema。\n",
    "graph_no_recall": (
        "### 已有 Graph 节点\n"
        "Graph 中共有 {graph_total} 个 Schema，"
        "但 centroid 向量召回未命中相关节点。请根据聚类结果判断是否需要创建新 Schema。\n"
    ),
    "reverse_header": "### Graph 反向引用（已有 Schema 引用的 VDB 事实）\n",
    "tags_header": "### 已有标签（请优先从中选择，避免创建同义标签）\n",
    "closing": (
        "请分析以上材料，输出操作 JSON 数组。"
        "决定是否需要创建新的 Schema，或为已有 Schema 追加 evidence。"
        "如果数据不足以得出可靠结论，输出空数组 `[]`。"
        "打标签时请优先使用已有标签列表中的标签。"
    ),
}
