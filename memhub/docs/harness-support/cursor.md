# Cursor 一键插入支持

更新日期：2026-10-14

## 结论

Cursor 已开放一键插入。它不是 Cursor Extension，而是 **MCP 配置 + Skill + Capture Hooks + Credential Grant**。

## 接入方式和产品状态

| 项目 | Cursor 当前实现 |
|---|---|
| 一键插入产品状态 | 开放 |
| Cursor 查询 MemHub | MCP Server |
| 告诉模型何时查询 | Skill |
| Prompt 前自动 Prefetch | 没有 |
| Cursor 新会话实时送入 MemHub | `hooks.json` Capture Hooks |
| 传统 Cursor Extension | 没有 |

## 两个开关分别做什么

### 打开“一键插入应用”

安装 MCP、Skill、`beforeSubmitPrompt`/`afterAgentResponse` Capture Hooks，并创建和授权 Cursor Credential。MCP 用于 Cursor 主动查询；Hooks 只负责把可见会话事件实时送给 Core，不负责 Recall 注入。

### 关闭“一键插入应用”

先撤销 Cursor Credential，再移除或恢复 MCP、Skill、Hooks 和 Journal。同步开关状态不变。

### 打开“同步开关”

Core 补扫 Cursor 历史来源，恢复冻结 Job，启用轮询/Watcher；Capture Hook 发来的新事件也允许进入 Evidence。

### 关闭“同步开关”

Core 不再扫描、不 claim pending Job，也拒绝 Hook Capture 入库；已 running Job可结束，已有 Memory 保留。Cursor 的 MCP/Skill 仍存在且 Credential 未撤销，所以 Cursor 仍可查询以前已有的 Memory。

### 组合效果

- 一键开、同步关：Cursor 可 Recall，但新对话不再进入 MemHub。
- 一键关、同步开：MemHub 仍学习 Cursor，会话侧不能 Recall。
- 两者都开：双向接通；Recall 由模型主动调用，不是 Prompt 自动 Prefetch。

## 实际安装

```text
~/.cursor/mcp.json
  └── mcpServers.one-memory

~/.cursor/skills/memhub-recall/SKILL.md

~/.cursor/hooks.json
  ├── beforeSubmitPrompt → one-memory capture cursor
  └── afterAgentResponse → one-memory capture cursor

~/.cursor/.one-memory/recall-install.json
```

配置只保存 Credential 文件路径，不保存 Secret。安装前后状态和 Hash 记录在 Journal 中。

Layout 在 [recall-installer.ts](../../packages/host-bridge/src/recall-installer.ts)，Capture 合并在 [capture-hooks.ts](../../packages/host-bridge/src/capture-hooks.ts)。

## Capture 与同步开关

Capture Hook 使用 fail-open Shell 命令：失败不阻止 Cursor 发送 Prompt 或结束回答。Hook 只是实时事件入口；Core 的 capture 路径仍检查同步配置和 Root 授权。因此：

```text
一键插入 → 把 Capture 能力装进 Cursor
同步开关 → 决定事件是否真的进入并被处理
```

## 权限

- 默认 Tools：`om_recall`、`om_conversations`、`om_diagnostic_trace`。
- MCP 只暴露三个工具：`MemHub_recall`、`MemHub_write`、`MemHub_diagnostic_trace`。`om_conversations` 不再是 MCP 工具，只授权原文通道（`/context` 与召回的原文回退）；`MemHub_write` 不受授权控制，只要求已认证的安装。
- 默认 Scopes：`all-apps`、`source:cursor`。

## 重复打开与关闭

- 已安装且 `approved`：不重装，只修复命令路径并 Probe。
- 权限不完整：重新安装并签发新 Credential，不会重复追加 MCP 或 Hook。
- 关闭：先撤权，再恢复或删除 MCP、Skill、Hooks 和 Journal。
- 用户修改过的文件不会被强删；撤权后可能留下无权限残留。

## 生效与重启

Cursor 有专门的 Credential 更新重启提示。建议完全退出 Cursor 后重新打开，并开始新对话；只关闭聊天标签不保证后台进程重载 MCP、Skill 和 Hooks。
