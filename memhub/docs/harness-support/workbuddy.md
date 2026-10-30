# WorkBuddy 一键插入支持

更新日期：2026-10-14

## 结论

WorkBuddy 是当前唯一真正安装成 `.codebuddy-plugin` 目录的 Harness。它会同时写 Marketplace 副本、Runtime Cache 副本、三个 Registry、Skill、Hooks、Credential 和 Grant。

## 接入方式和产品状态

| 项目 | WorkBuddy 当前实现 |
|---|---|
| 一键插入产品状态 | 开放 |
| WorkBuddy 查询 MemHub | Plugin 内 MCP Server |
| 告诉模型何时查询 | Plugin 内 Skill |
| 自动 Prefetch/注入 | 有，Plugin Hook 会运行 Recall Injection |
| WorkBuddy 新会话实时送入 MemHub | Plugin Capture Hooks |
| 安装形态 | 真正的 `.codebuddy-plugin` |

WorkBuddy 与其他 Harness 最大的不同是：它不仅允许模型主动调用 MCP，还能在 `SessionStart` 和 `UserPromptSubmit` Hook 中运行 Recall Injection，把 MemHub 返回的上下文直接交给当前会话。`Stop` Hook 则只负责 Capture。

## 两个开关分别做什么

### 打开“一键插入应用”

安装完整 Plugin、MCP、Skill、自动 Recall Injection Hook、Capture Hook，并创建 WorkBuddy Credential。此后 WorkBuddy 既能主动调用 MCP，也能在 Hook 触发时自动获取上下文。

### 关闭“一键插入应用”

先撤销 Credential，再恢复 Registry、删除 Plugin 树、Skill、Injection/Capture Hooks 和 Journal。它不改变 Core 的同步设置。

### 打开“同步开关”

Core 补扫 WorkBuddy 历史会话，恢复冻结 Job，并允许 Plugin Capture Hook 发来的新会话进入 Evidence。

### 关闭“同步开关”

Core 停止扫描和 pending Job claim，并拒绝 Capture 入库；running Job可结束，已有 Memory 保留。由于一键插入 Credential 仍有效，WorkBuddy 仍可主动 Recall，Injection Hook 也仍可能查询已有 Memory。

### 组合效果

- 一键开、同步关：WorkBuddy 仍可主动查询或自动注入已有 Memory，但新对话不再进入 MemHub。
- 一键关、同步开：MemHub 继续读取 WorkBuddy，会话侧没有 MCP、Skill 或自动注入。
- 两者都开：双向接通，并具备自动 Recall Injection。

## Plugin 身份

```text
marketplaceName = one-memory
pluginName      = one-memory-recall
skillName       = memhub-recall
version         = 1.0.0
pluginId        = one-memory-recall@one-memory
```

## 实际安装目录

Marketplace 副本：

```text
~/.workbuddy/plugins/marketplaces/one-memory/
├── .codebuddy-plugin/marketplace.json
└── plugins/one-memory-recall/
    ├── .codebuddy-plugin/plugin.json
    ├── hooks/hooks.json
    └── skills/memhub-recall/SKILL.md
```

Runtime Cache 副本：

```text
~/.workbuddy/plugins/cache/one-memory/one-memory-recall/1.0.0/
├── .codebuddy-plugin/plugin.json
├── hooks/hooks.json
└── skills/memhub-recall/SKILL.md
```

WorkBuddy 从 Marketplace 副本加载 Skill，从 Cache 副本加载 MCP，因此两个位置都是真实运行依赖。实现见 [codebuddy-plugin.ts](../../packages/host-bridge/src/codebuddy-plugin.ts)。

## Registry 修改

| 文件 | MemHub 写入内容 |
|---|---|
| `~/.workbuddy/plugins/known_marketplaces.json` | 注册 `one-memory` Marketplace |
| `~/.workbuddy/plugins/installed_plugins.json` | 注册 Cache 中的 `one-memory-recall@one-memory` |
| `~/.workbuddy/settings.json` | `enabledPlugins["one-memory-recall@one-memory"] = true` |

安装 Journal：

```text
~/.workbuddy/.one-memory/recall-plugin-install.json
```

## Plugin 内容与 Capture

Plugin 内包含：

- MCP Server 配置；
- `memhub-recall` Skill；
- `SessionStart`、`UserPromptSubmit`、`Stop` Hooks。

Hooks 的精确职责：

- `SessionStart`：运行 Recall Injection，并返回 MemHub 上下文和跨会话提示；
- `UserPromptSubmit`：运行 Recall Injection，同时异步触发 Capture；
- `Stop`：只运行 Capture，不再启动一次 Recall。

失败采用 fail-open，不阻断 WorkBuddy。

## 旧安装迁移

旧 scattered 安装位于：

```text
~/.workbuddy/mcp.json
~/.workbuddy/skills/one-memory-recall/SKILL.md
~/.workbuddy/.one-memory/recall-install.json
```

迁移顺序是先完整安装新 Plugin，再删除旧安装，避免新安装失败后 WorkBuddy 完全失去 Recall。

## 权限、重复打开与关闭

- 默认 Tools：`om_recall`、`om_conversations`、`om_diagnostic_trace`。
- MCP 只暴露三个工具：`MemHub_recall`、`MemHub_write`、`MemHub_diagnostic_trace`。`om_conversations` 不再是 MCP 工具，只授权原文通道（`/context` 与召回的原文回退）；`MemHub_write` 不受授权控制，只要求已认证的安装。
- 默认 Scopes：`all-apps`、`source:workbuddy`。
- 已安装且 `approved`：不重装，只修复命令并 Probe。
- 关闭：先撤权，再恢复三个 Registry，并删除 Marketplace/Cache 插件树和 Journal。
- Plugin 或 Registry 被修改时不强删，尽量撤权并要求人工核对。

安装器对插件树使用 staging + rename，避免 WorkBuddy 看到半写入插件。Registry 文件采用非常保守的 Hash 校验，WorkBuddy 或其他插件修改同一 Registry 后可能导致自动卸载拒绝。

## 生效与重启

Probe 不证明正在运行的 WorkBuddy 已重新读取 Marketplace、Cache、Registry、Skill 和 Hooks。建议重启 WorkBuddy，至少新开会话。
