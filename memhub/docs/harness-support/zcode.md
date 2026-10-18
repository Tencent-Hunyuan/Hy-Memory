# ZCode 一键插入支持

更新日期：2026-10-18

## 结论

ZCode 已开放一键插入。它安装 **JSON MCP 配置 + Skill + Credential Grant**，不安装 Capture Hook。

## 接入方式和产品状态

| 项目 | ZCode 当前实现 |
|---|---|
| 一键插入产品状态 | 开放 |
| ZCode 查询 MemHub | MCP Server |
| 告诉模型何时查询 | Skill |
| Prompt 前自动 Prefetch | 没有 |
| ZCode 新会话实时送入 MemHub | 无 Hook；同步适配器未接入 |
| Plugin | 没有 |

## 两个开关分别做什么

### 打开“一键插入应用”

向 `cli/config.json` 的 `mcp.servers` 安装 MCP、写 Skill、创建 Credential 并授权。模型在需要时主动调用 MCP；不会自动 Prefetch。

### 关闭“一键插入应用”

先撤权，再恢复或删除 MCP、Skill 和 Journal。

### 打开“同步开关”

ZCode 尚无 Core 同步适配器；同步开关对其不可用。一键插入（Recall 方向）不受影响。

### 组合效果

一键插入控制 ZCode 能不能 Recall。当前没有反向学习通道，后续接入 Capture Hook 或同步适配器时再补全双向语义。

## 实际安装

```text
~/.zcode/cli/config.json
  └── mcp.servers.one-memory

~/.zcode/skills/memhub-recall/SKILL.md

~/.zcode/.one-memory/recall-install.json
```

安装器会保留 `hooks`、插件状态和其他 MCP Server，但 JSON 可能被重新格式化。已有非 MemHub 管理的 `mcp.servers.one-memory` 时默认拒绝覆盖。

## ZCode 特有约束

- 服务器表嵌套在 `mcp.servers`（比 Cursor 的顶层 `mcpServers` 深一层），条目 schema 严格：**含未知键的 server 会在加载时被静默丢弃**。安装器只写 `{command, args, env}` 三键（`type` 由 `command` 推断为 stdio），测试对此有专门断言。
- 配置文件里的 server 不展开 `${...}` 模板变量，命令与路径须为绝对路径。
- 配置文件级 Hook 需要 `hooks.enabled: true`；本安装不写 Hook，不影响用户既有 Hook 配置。

## 权限、重复打开与关闭

- 默认 Tools：`om_recall`、`om_conversations`、`om_diagnostic_trace`。
- 默认 Scopes：`all-apps`、`source:zcode`。
- 已安装且 `approved`：只修复命令并 Probe。
- 权限不完整：重新安装、签发新 Credential 并授权。
- 关闭：先撤权，再恢复或删除 MCP、Skill 和 Journal。
- 用户修改过配置或 Skill 时不强删。

## 生效与重启

编辑 `cli/config.json` 后建议重启 ZCode（或重新打开会话），在 **Settings → MCP** 确认 `one-memory` 已连接。
