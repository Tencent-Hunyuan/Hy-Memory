# Qwen Code 一键插入支持

更新日期：2026-10-14

## 结论

Qwen Code 已开放一键插入。它安装 **JSON MCP 配置 + Skill + Credential Grant**，不安装 Capture Hook。

## 接入方式和产品状态

| 项目 | Qwen Code 当前实现 |
|---|---|
| 一键插入产品状态 | 开放 |
| Qwen Code 查询 MemHub | MCP Server |
| 告诉模型何时查询 | Skill |
| Prompt 前自动 Prefetch | 没有 |
| Qwen Code 新会话实时送入 MemHub | 无 Hook；依赖 Core 扫描 |
| Plugin | 没有 |

## 两个开关分别做什么

### 打开“一键插入应用”

向 `settings.json` 安装 MCP、写 Skill、创建 Credential 并授权。模型在需要时主动调用 MCP；不会自动 Prefetch。

### 关闭“一键插入应用”

先撤权，再恢复或删除 MCP、Skill 和 Journal；不影响 Core 同步。

### 打开“同步开关”

Core 扫描 `$QWEN_HOME/projects`（默认 `~/.qwen/projects`）中尚未处理的会话，恢复冻结 Job，并持续轮询新内容。

### 关闭“同步开关”

Core Freeze：停止扫描和 pending Job claim，running Job可结束，已有 Memory 保留。Qwen Code MCP 若仍安装，可以继续查询已有 Memory。

### 组合效果

一键插入控制 Qwen Code 能不能 Recall；同步控制 MemHub 是否继续学习 Qwen Code。两者都开才是双向接通，但仍是模型主动调用而非自动 Prefetch。

## 实际安装

```text
~/.qwen/settings.json
  └── mcpServers.one-memory

~/.qwen/skills/memhub-recall/SKILL.md

~/.qwen/.one-memory/recall-install.json
```

安装器会保留 `$version`、模型设置和其他 MCP Server，但 JSON 可能被重新格式化。已有非 MemHub 管理的 `mcpServers.one-memory` 时默认拒绝覆盖。

## 权限、重复打开与关闭

- 默认 Tools：`om_recall`、`om_conversations`、`om_diagnostic_trace`。
- MCP 只暴露三个工具：`MemHub_recall`、`MemHub_write`、`MemHub_diagnostic_trace`。`om_conversations` 不再是 MCP 工具，只授权原文通道（`/context` 与召回的原文回退）；`MemHub_write` 不受授权控制，只要求已认证的安装。
- 默认 Scopes：`all-apps`、`source:qwen-code`。
- 已安装且 `approved`：只修复命令并 Probe。
- 权限不完整：重新安装、签发新 Credential 并授权。
- 关闭：先撤权，再恢复或删除 MCP、Skill 和 Journal。
- 用户修改过配置或 Skill 时不强删。

## 已知路径问题

Core 导入支持 Qwen 自己的 `QWEN_HOME`，但 Desktop Host Layout 默认固定使用 `~/.qwen`，只识别 MemHub 的 `ONE_MEMORY_HOST_HOME` 隔离变量。

自定义 `QWEN_HOME` 时可能出现：

```text
Core 导入：$QWEN_HOME
Recall 安装：~/.qwen
```

即同步和一键插入指向不同 Qwen Home。

## 生效与重启

当前代码不保证运行中的 Qwen Code 热加载 `settings.json` 和 Skill。建议完全退出重开并开始新会话。
