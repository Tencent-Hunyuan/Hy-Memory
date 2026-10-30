# Codex 一键插入支持

更新日期：2026-10-14

## 结论

Codex 已开放一键插入。它安装的是 **TOML MCP 配置 + Skill + Capture Hooks + Credential Grant**。

## 接入方式和产品状态

| 项目 | Codex 当前实现 |
|---|---|
| 一键插入产品状态 | 开放 |
| Codex 查询 MemHub | MCP Server |
| 告诉模型何时查询 | Skill |
| Prompt 前自动 Prefetch | 没有 |
| Codex 新会话实时送入 MemHub | `UserPromptSubmit`、`Stop` Capture Hooks |
| Plugin | 没有 |

## 两个开关分别做什么

### 打开“一键插入应用”

写入 TOML MCP 表、安装 Skill 和 Capture Hooks，并创建 Codex Credential。Codex 模型需要主动调用 MCP 才会查询记忆。

### 关闭“一键插入应用”

撤销 Credential，恢复或删除 TOML、Skill、Hooks 和 Journal；不影响同步开关。

### 打开“同步开关”

Core 补扫 `.codex/sessions`，恢复冻结 Job，并允许 Hook Capture 进入 Evidence。

### 关闭“同步开关”

停止扫描和 pending Job claim，Hook Capture 被 Core 拒绝；running Job允许结束，已有 Memory 保持可查询。

### 组合效果

两个开关分别控制 Recall 出口和会话入口；都打开才是双向接通，但仍没有自动 Prefetch。

## 实际安装

```text
~/.codex/config.toml
  └── [mcp_servers.one-memory]
      └── [mcp_servers.one-memory.env]

~/.codex/skills/memhub-recall/SKILL.md

~/.codex/hooks.json
  ├── UserPromptSubmit → one-memory capture codex
  └── Stop             → one-memory capture codex

~/.codex/.one-memory/recall-install.json
```

TOML 编辑器只维护 `mcp_servers.one-memory` 和对应 `.env` 表，尽量保留其他配置、注释和顺序。实现见 [toml-mcp.ts](../../packages/host-bridge/src/toml-mcp.ts)。

## 权限

- 默认 Tools：`om_recall`、`om_conversations`、`om_diagnostic_trace`。
- MCP 只暴露三个工具：`MemHub_recall`、`MemHub_write`、`MemHub_diagnostic_trace`。`om_conversations` 不再是 MCP 工具，只授权原文通道（`/context` 与召回的原文回退）；`MemHub_write` 不受授权控制，只要求已认证的安装。
- 默认 Scopes：`all-apps`、`source:codex`。
- Credential Secret 位于 MemHub 私有目录；`config.toml` 只写文件路径。

## 重复打开与关闭

- 已安装且 `approved`：不重装，只修复 Runtime/MCP 路径并 Probe。
- 权限不完整：重新安装、创建新 Installation 和 Credential，再授权。
- 关闭：先撤权，再恢复 TOML、Skill、Hooks 和 Journal。
- 用户修改过 MCP、Skill 或 Hook 时不强删，撤权后提示人工处理。

## 生效与重启

当前代码没有保证运行中的 Codex 热加载 `config.toml`、Skill 和 Hooks。建议完全退出 Codex、重新打开并开始新会话。
