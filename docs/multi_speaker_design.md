# 多说话人对话支持

Hy Memory 支持在一段对话中区分当前用户、其他具名真人和 AI assistant。该能力使用标准 messages 输入中的 `role` 与 `name`，不增加 participant 表或新的 Memory 字段。

## 1. 输入约定

调用方负责确定“当前用户”的视角：

- `role="user"` 表示当前记忆所属的用户；可以通过 `name` 提供用户姓名。
- `role="assistant"` 且带 `name` 表示另一位具名真人。
- `role="assistant"` 且不带 `name` 表示 AI assistant。
- `role="tool"` 的 `name` 是工具名，不是说话人姓名。

例如，以 Tim 为当前用户、John 为对话中的另一位真人：

```python
from hy_memory import HyMemoryClient

client = HyMemoryClient(mode="pro")
result = client.add(
    [
        {"role": "user", "name": "Tim", "content": "I work at Stripe."},
        {"role": "assistant", "name": "John", "content": "I am a nurse in Berlin."},
    ],
    user_id="tim",
)
```

OpenAI 风格字符串消息与 Anthropic 风格 content blocks 都会保留非 tool 角色的 `name`。OpenAI 的独立 tool message 仍将 `name` 解析为工具名。

## 2. 内部渲染

普通 user-AI 对话保持原格式：

```text
[user]: I like coffee.
[assistant]: Noted.
```

具名多说话人对话会渲染为：

```text
The user's name is Tim
[user]: I work at Stripe.
[John]: I am a nurse in Berlin.
```

中文消息对应：

```text
用户名叫Tim
[user]: 我在 Stripe 工作。
[John]: 我是住在柏林的护士。
```

| 输入 | 渲染标签 | 语义 |
|---|---|---|
| `role=user` | `[user]` | 当前用户；即使有 `name` 也不改成姓名 |
| `role=assistant, name=John` | `[John]` | 其他具名真人 |
| `role=assistant`，无 `name` | `[assistant]` | AI assistant |
| `role=system/tool` | 原 role | 系统或工具消息 |

当前用户的姓名单独放在文首，整段只出现一次。名字行的语言由消息内容自动判断。

## 3. 记忆归属

抽取链按以下规则生成事实：

- 当前用户的事实以 `The user ...` / `用户...` 为主体，`owner="user"`。
- 其他具名真人的事实以姓名为主体，例如 `John is a nurse in Berlin.`，`owner="user"`。
- AI assistant 提供或产出的内容以 assistant 为主体，`owner="agent"`。

具名真人虽然在输入协议中可能使用 `role="assistant"`，但不会因此变成 `owner="agent"`。

L0 basic profile 只描述当前用户。`role=user` 的 `name` 是 `basic_info.name` 的可信输入；其他具名真人的职业、位置等信息应进入普通事实，而不是当前用户的 L0。

Reconcile prompt 明确禁止把不同主体的事实互相 UPDATE 或 SUPERSEDE。例如 `The user likes coffee` 与 `John likes coffee` 应保留为两条独立事实。

## 4. 历史上下文

L1_RAW 保存的内容使用上述渲染格式。历史回读时：

- 文首姓名行会被识别并去重，不会被误当成一条 user message。
- `[John]:` 这类具名标签会被保留。
- 多行消息按说话人前缀拆分，不会把正文续行误拆成新消息。
- 历史中的 assistant 与其他具名真人消息会按历史上下文上限截断；user 消息完整保留。

## 5. 当前边界

- 调用方必须用 `role` 指定当前用户视角；SDK 不根据姓名猜测谁是当前用户。
- 目前没有具名 AI agent 的独立身份模型。`role=assistant + name` 被解释为具名真人。
- 说话人主体主要编码在事实文本中，不是独立的检索过滤字段。
- 不会为其他具名真人创建独立 L0 profile。若需要分别维护多个人的用户画像，应使用不同的 `user_id` 写入各自视角。

## 6. 实现与验证

主要实现位置：

- `hy_memory/pipelines/base.py`：`ChatMessage.name`、说话人标签和对话渲染。
- `hy_memory/client.py`：OpenAI/Anthropic messages 规范化。
- `hy_memory/agent/extractor.py` 与 `hy_memory/agent/prompts_zh.py`：主体、owner 和 basic profile 规则。
- `hy_memory/agent/reconciler.py`：跨主体合并约束。
- `hy_memory/pipelines/writer.py`：L1_RAW 历史解析。

定向测试：

```bash
python -m pytest -q \
  tests/test_multi_speaker_format.py \
  tests/test_reconcile_failure.py
```
