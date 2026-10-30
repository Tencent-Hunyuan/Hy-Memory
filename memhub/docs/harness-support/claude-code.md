# Claude Code 一键插入支持

更新日期：2026-10-14

## 结论

Claude Code 已开放一键插入。它安装的是 **MCP 配置 + Skill + 三组 Capture Hook**，不是传统插件。

## 接入方式和产品状态

| 项目 | Claude Code 当前实现 |
|---|---|
| 一键插入产品状态 | 开放 |
| Claude Code 查询 MemHub | MCP Server |
| 告诉模型何时查询 | Skill |
| Prompt 前自动 Prefetch | 一键插入本身没有；另有独立同步 Hook 机制 |
| 新会话实时送入 MemHub | `UserPromptSubmit`、`Stop`、`SubagentStop` Capture Hooks |
| 传统 Plugin | 没有 |

## 两个开关分别做什么

### 打开“一键插入应用”

写 `~/.claude.json` 的 MCP、安装 Skill、向 `settings.json` 合并三组 Capture Hook，并创建授权 Credential。

### 关闭“一键插入应用”

先撤权，再精确删除或恢复 MCP、Skill 和 Capture Hook。它不关闭 Core 同步。

### 打开“同步开关”

Core 扫描 `.claude/projects` 中未处理会话，恢复 Job，并允许 Capture Hook 事件进入 Evidence。

### 关闭“同步开关”

Core Freeze：停止扫描与 pending Job claim，Capture Hook 后续调用被 Core 拒绝，running Job可完成，已有 Memory 仍保留。

### 组合效果

- 一键开、同步关：Claude Code 能查旧 Memory，但新会话不再进入 MemHub。
- 一键关、同步开：MemHub 继续读取 Claude Code，但 Claude Code 无 Recall 工具。
- 两者都开：双向接通；一键插入的 MCP 是主动查询，不是自动 Prefetch。

## 实际安装

```text
~/.claude.json
  └── mcpServers.one-memory

~/.claude/skills/memhub-recall/SKILL.md

~/.claude/settings.json
  ├── UserPromptSubmit → one-memory capture claude-code
  ├── Stop             → one-memory capture claude-code
  └── SubagentStop     → one-memory capture claude-code

~/.claude/.one-memory/recall-install.json
```

Layout 和 MCP/Skill 写入位于 [recall-installer.ts](../../packages/host-bridge/src/recall-installer.ts)，Hook 生成位于 [capture-hooks.ts](../../packages/host-bridge/src/capture-hooks.ts)。

## 与独立同步 Hook 的区别

项目中还有一套 Claude Code 专用“同步 Hook”。它不是一键插入安装的三组 Capture Hook。当前权限层会检查两套机制的冲突，独立同步 Hook 存在时可能要求先处理再安装或卸载 Recall。

## 权限

- 默认 Tools：`om_recall`、`om_conversations`、`om_diagnostic_trace`。
- MCP 只暴露三个工具：`MemHub_recall`、`MemHub_write`、`MemHub_diagnostic_trace`。`om_conversations` 不再是 MCP 工具，只授权原文通道（`/context` 与召回的原文回退）；`MemHub_write` 不受授权控制，只要求已认证的安装。
- 默认 Scopes：`all-apps`、`source:claude-code`。

## 重复打开与关闭

- 已安装且 `approved`：只修复命令路径并 Probe。
- 权限不完整：重新 Preview/Install，签发新 Credential，再授权。
- 关闭：先撤权，再精确移除 MemHub MCP、Skill 和 Capture Hooks，保留其他 Hook。
- 文件被用户修改时不强删，撤权并要求人工核对。

## 生效与重启

MemHub 不会重启 Claude Code，也不能证明老进程热加载 `~/.claude.json`、Skill 和 Hook。建议完全退出后重新打开并新建会话。
