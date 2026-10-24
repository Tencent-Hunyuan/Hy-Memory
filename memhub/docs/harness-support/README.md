# Harness 一键插入支持索引

更新日期：2026-10-14

“一键插入应用”控制的是 **MemHub → Harness** 的 Recall 接入；“同步开关”控制的是 **Harness → MemHub** 的会话读取与处理。两者不是同一个功能。
同步后的原始对话和记忆由 Hy Memory SDK 数据面持久化。

打开一键插入通常会执行：

```text
后台 Preview 并计算 fingerprint
→ 创建 install-only Installation 和私有 Credential
→ 写 Harness 配置、Skill、Hook 或 Plugin
→ 写安装 Journal
→ 授予 Recall Tools 和 Scopes
→ 执行连接 Probe
```

默认详情页授权：

```text
Tools: om_recall, om_conversations, om_diagnostic_trace
Scopes: all-apps, source:<当前来源>
```

Preview 不会弹出给用户；开关点击本身被视为确认。关闭时通常先撤销 Credential，再恢复或删除受管文件。用户修改过的文件不会被强删。

## 支持矩阵

| Harness | 一键插入产品状态 | Harness 如何查询 MemHub | Harness 如何把新会话送入 MemHub | 同步开关的数据来源 |
|---|---|---|---|---|
| [Hermes](hermes.md) | 开放 | 原生 MemoryProvider Prefetch + MCP + Skill | Provider 只读；Core 扫描写入 | `.hermes` SQLite/文件扫描 |
| [Cursor](cursor.md) | 开放 | MCP + Skill | Capture Hooks | Cursor Adapter 扫描 + Hook 实时入口 |
| [Claude Code](claude-code.md) | 开放 | MCP + Skill | Capture Hooks | `.claude/projects` 扫描 + Hook 实时入口 |
| [Codex](codex.md) | 开放 | MCP + Skill | Capture Hooks | `.codex/sessions` 扫描 + Hook 实时入口 |
| [WorkBuddy](workbuddy.md) | 开放 | `.codebuddy-plugin` 内 MCP + Skill | Plugin Hooks | WorkBuddy Adapter 扫描 + Plugin Hook |
| [OpenCode](opencode.md) | 开放 | MCP，无 Skill | JS Capture Plugin | OpenCode DB 扫描 + Plugin 实时入口 |
| [Qwen Code](qwen-code.md) | 开放 | MCP + Skill | 无自动 Hook | `$QWEN_HOME/projects` 扫描 |
| [ZCode](zcode.md) | 开放 | Plugin 内联 MCP + Skill | Plugin Hooks（注入入口移交 + Stop 钩子） | `cli/rollout` 转录扫描 + Plugin Hook 实时入口 |
| [Pi](pi.md) | 开放 | CLI Skill，不使用 MCP | JS Capture Extension | Pi session 扫描 + Extension 实时入口 |
| [OpenClaw](openclaw.md) | 正式产品冻结 | 当前没有；仅有未开放的底层 MCP 写入器 | 没有 Hook、Plugin 或 Prefetch | Adapter 也被冻结，开关不可用 |
| [DeepSeek Harness](deepseek-harness.md) | 详情页排除，来源管理页按 Profile 接入 | Profile 级 MCP Patch | 无自动 Hook | `$DSH_HOME/sessions` 扫描 |

## 同步开关的统一精确语义

### 打开同步开关

Core 会：

1. 检查来源目录已经授权且 Root identity 有效；
2. 建立实时读取基线，避免把打开前的文件尾部误判成实时新增；
3. 如果没有现存 history Job，创建一个 history Job；
4. 把 `<data_dir>/config.json` 中该来源的 `sync_enabled` 写为 `true`；
5. 调度 pending Job，并恢复 paused、interrupted、failed Job；
6. 把该来源加入轮询/文件 Watcher；
7. 补扫历史尚未处理的 Session，并继续处理未来的新 Session；
8. 如果启用了自动记忆处理，恢复对应的 `memory_write_jobs`。

### 关闭同步开关

Core 会：

1. 把来源从实时 Watcher/轮询的可执行集合中移除；
2. 把 `config.json` 中该来源的 `sync_enabled` 写为 `false`；
3. 停止 claim Job 表中尚未开始的 Job；这些 Job 保持原状态，等待下次打开；
4. 不再通过 Adapter 扫描新 Session；
5. 已经 running 的少量 Job允许执行完，不强杀；
6. Harness Hook/Plugin 后续即使继续调用 Capture，Core 也会因为同步关闭而拒绝进入 SDK 写入路径；
7. 已经生成的 Memory 不删除，仍可被 Search 和 Recall 使用。

关闭同步开关不会卸载 MCP、Skill、Hook 或 Plugin，也不会撤销 Harness 查询记忆的 Credential。那些属于“一键插入应用”开关。

## 共同语义

- 已安装且权限为 `approved` 时再次打开，一般不重装，只修复 Runtime 路径并 Probe。
- `install-only`、`revoked` 或 `relink-required` 状态通常会重新安装、签发新 Credential 并授权。
- 关闭时先撤权，再卸载；若文件有用户修改，只撤权并要求人工核对。
- Probe 是 MemHub 自己启动落盘命令做检查，不证明正在运行的 Harness 已热加载。
- 最稳妥的生效方式是完全退出目标 Harness、重新打开并开始新会话。

统一生产入口：

- [Web 开关](../../apps/web/src/ApplicationDetail.tsx)
- [Web 安装与授权编排](../../apps/web/src/host-state.ts)
- [Desktop IPC](../../apps/desktop/src/main.ts)
- [权限和安装协调](../../packages/runtime-control/src/host-permissions.ts)
- [通用文件安装器](../../packages/host-bridge/src/recall-installer.ts)
- [Capture 安装器](../../packages/host-bridge/src/capture-hooks.ts)
