# Pi 一键插入支持

更新日期：2026-10-14

## 结论

Pi 没有 MCP 配置入口，但当前生产权限层支持 CLI 通道，因此 Pi 可以使用和撤销 Credential。它安装 **CLI Recall Skill + Capture Extension**。

## 接入方式和产品状态

| 项目 | Pi 当前实现 |
|---|---|
| 一键插入产品状态 | 生产代码开放 |
| Pi 查询 MemHub | Skill 执行 Recall CLI，不使用 MCP |
| 告诉模型何时查询 | CLI 版 Skill |
| Prompt 前自动 Prefetch | 没有自动注入；模型按 Skill 主动执行命令 |
| Pi 新会话实时送入 MemHub | `one-memory-capture.js` Extension |
| Credential | 支持，CLI 从私有文件读取，可撤销 |

## 两个开关分别做什么

### 打开“一键插入应用”

安装 CLI Recall Skill 和 Capture Extension，创建 Credential 并授权。Pi 需要根据 Skill 主动执行 Recall CLI；这不是 MCP，也不是自动 Prefetch。

### 关闭“一键插入应用”

先撤销 Pi Credential，再恢复或删除 Skill、Capture Extension 和 Journal；同步开关不变。

### 打开“同步开关”

Core 补扫 Pi Session，并允许 Extension 的 `before_agent_start`、`turn_end` Capture 事件进入 Evidence。

### 关闭“同步开关”

Core 停止扫描和 pending Job claim，并拒绝 Extension Capture 入库；running Job可结束，已有 Memory 保留。只要一键插入仍开，Pi 的 Recall CLI 仍可查询已有 Memory。

### 组合效果

Pi 的 Recall 出口是 CLI Skill，入库实时入口是 Extension；两者都不是 MCP。同步关闭只冻结入库，不撤销 Pi 查询已有 Memory 的权限。

## 实际安装

```text
~/.pi/agent/skills/memhub-recall/SKILL.md
~/.pi/agent/extensions/one-memory-capture.js
~/.pi/agent/.one-memory/recall-install.json
```

不会创建 MCP 配置，因为 Pi 没有读取 `mcp.json` 或 `mcpServers` 的机制。

## Recall 通道

Skill 通过 Shell 命令执行 `recall-cli.cjs`，并设置：

```text
ONE_MEMORY_BROKER_DISCOVERY
ONE_MEMORY_HOST_CREDENTIAL_FILE
```

Recall CLI 从私有 Credential 文件读取凭证，再访问本机 Broker。Broker 每次调用都检查当前 Grant，所以 Credential 可以撤销。

## Capture Extension

`one-memory-capture.js` 监听：

```text
before_agent_start
turn_end
```

并调用 `one-memory capture pi`。采集失败不会阻断 Pi。

## 权限、重复打开与关闭

- 默认 Tools：`om_recall`、`om_conversations`、`om_diagnostic_trace`。
- MCP 只暴露三个工具：`MemHub_recall`、`MemHub_write`、`MemHub_diagnostic_trace`。`om_conversations` 不再是 MCP 工具，只授权原文通道（`/context` 与召回的原文回退）；`MemHub_write` 不受授权控制，只要求已认证的安装。
- 默认 Scopes：`all-apps`、`source:pi`。
- 已安装且 `approved`：不重装，只修复 Skill 中的命令路径并 Probe。
- 权限 revoked：重新签发 Installation 和 Credential，再授权。
- 关闭：先撤权，再恢复或删除 Skill、Extension 和 Journal。
- 用户修改过 Skill 或 Extension 时不强删。

## 过时 UI 文案

[host-state.ts](../../apps/web/src/host-state.ts) 中仍有“Pi 没有 MCP，所以不能发放可撤销 Credential”的旧注释和不支持文案，部分测试也手工构造 Pi 为 `unsupported_transport`。

这与当前生产实现矛盾：`HostPermissions` 支持 `channel="cli"`，Pi Skill 也携带 Credential 文件路径。这些文案和测试属于过时残留。

## 生效与重启

Probe 只能证明 Recall CLI 可运行，不能证明 Pi 已加载新 Skill 和 Extension。建议完全退出 Pi、重新打开并开始新会话。
