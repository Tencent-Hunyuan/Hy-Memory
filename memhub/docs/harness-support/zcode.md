# ZCode 一键插入支持

更新日期：2026-10-18

## 结论

ZCode 已开放一键插入。它安装 **JSON MCP 配置 + Skill + Credential Grant + Hooks**（recall 注入与 capture），Hooks 与 MCP 条目同在 `cli/config.json`。

## 接入方式和产品状态

| 项目 | ZCode 当前实现 |
|---|---|
| 一键插入产品状态 | 开放 |
| ZCode 查询 MemHub | MCP Server |
| 告诉模型何时查询 | Skill |
| Prompt 前自动 Prefetch | 有——UserPromptSubmit 与 SessionStart 钩子跑 recall 注入入口，`additionalContext` 注入对话（运行时未分发 recall 命令时缺省不装） |
| ZCode 新会话实时送入 MemHub | 注入入口顺手移交 prompt 侧采集 + Stop 钩子采集助手侧 |
| Plugin | 没有（ZCode 有插件系统，但本安装走配置文件形态） |

## 两个开关分别做什么

### 打开“一键插入应用”

向 `cli/config.json` 的 `mcp.servers` 安装 MCP、写 Skill、创建 Credential 并授权，并在同一文件的 `hooks.events` 合并三个钩子：UserPromptSubmit 与 SessionStart 跑 recall 注入（模型回答前召回并注入 `additionalContext`，同时把 prompt 移交给 capture 不等待）、Stop 采集助手侧回复。运行时未分发 recall 命令时降级为仅 Stop 采集。

### 关闭“一键插入应用”

先撤权，再一次性恢复或删除 MCP、Skill、Journal 与钩子（同文件的两处删除合并为一次写入）。

### 打开“同步开关”

ZCode 尚无 Core 同步适配器；同步开关对其不可用。一键插入（Recall 方向）不受影响。

### 组合效果

一键插入控制 ZCode 能不能 Recall 与实时送入；钩子失败一律静默（`exit 0`、无 stdout），不影响会话。

## 实际安装

```text
~/.zcode/cli/config.json
  ├── mcp.servers.one-memory
  └── hooks.events.{SessionStart,UserPromptSubmit,Stop} + hooks.enabled

~/.zcode/skills/memhub-recall/SKILL.md

~/.zcode/.one-memory/recall-install.json
```

安装器会保留 `hooks`、插件状态和其他 MCP Server，但 JSON 可能被重新格式化。已有非 MemHub 管理的 `mcp.servers.one-memory` 时默认拒绝覆盖。

## ZCode 特有约束

- 服务器表嵌套在 `mcp.servers`（比 Cursor 的顶层 `mcpServers` 深一层），条目 schema 严格：**含未知键的 server 会在加载时被静默丢弃**。安装器只写 `{command, args, env}` 三键（`type` 由 `command` 推断为 stdio），测试对此有专门断言。
- 钩子嵌套在 `hooks.events.<Event>`（比 Claude Code 深一层），**配置文件钩子必须 `hooks.enabled: true`**。用户原本为 false/缺失时安装会置 true——副作用是用户自己的配置文件钩子也会开始运行（不启用则我们的钩子永不执行、静默采集为零，两害取其轻）。
- 配置文件里的 server 不展开 `${...}` 模板变量，命令与路径须为绝对路径；钩子命令会展开模板。

## 权限、重复打开与关闭

- 默认 Tools：`om_recall`、`om_conversations`、`om_diagnostic_trace`。
- 默认 Scopes：`all-apps`、`source:zcode`。
- 已安装且 `approved`：只修复命令并 Probe。
- 权限不完整：重新安装、签发新 Credential 并授权。
- 关闭：先撤权，再恢复或删除 MCP、Skill 和 Journal。
- 用户修改过配置或 Skill 时不强删。

## 生效与重启

编辑 `cli/config.json` 后建议重启 ZCode（或重新打开会话），在 **Settings → MCP** 确认 `one-memory` 已连接。
