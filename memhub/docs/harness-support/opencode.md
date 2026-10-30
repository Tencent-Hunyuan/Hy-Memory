# OpenCode 一键插入支持

更新日期：2026-10-14

## 结论

OpenCode 已开放一键插入。它安装 **OpenCode MCP 配置 + 实时 Capture Plugin**，不安装 Skill。

## 接入方式和产品状态

| 项目 | OpenCode 当前实现 |
|---|---|
| 一键插入产品状态 | 开放 |
| OpenCode 查询 MemHub | MCP Server |
| 告诉模型何时查询 | 无 Skill；依赖 MCP Tool Description |
| Prompt 前自动 Prefetch | 没有 |
| OpenCode 新会话实时送入 MemHub | `one-memory-capture.js` Plugin |
| 传统 Plugin | Capture 是本地 JS Plugin；Recall 本身仍是 MCP |

## 两个开关分别做什么

### 打开“一键插入应用”

写入 `mcp.one-memory`、安装 Capture Plugin、创建 Credential 并授权。OpenCode 模型需要主动调用 MCP 才会 Recall；Capture Plugin 不会向 Prompt 注入记忆。

### 关闭“一键插入应用”

先撤权，再恢复或删除 MCP、Capture Plugin 和 Journal；不修改同步开关。

### 打开“同步开关”

Core 扫描 OpenCode 数据库、恢复 history Job，并允许 Capture Plugin 实时事件进入 Evidence。

### 关闭“同步开关”

Core 停止数据库扫描和 pending Job claim，Capture Plugin 后续调用被 Core 拒绝；running Job可完成，已有 Memory 保留。OpenCode 的 MCP 若仍安装，可以继续查询已有 Memory。

### 组合效果

两个开关都开时，OpenCode 同时拥有主动 MCP Recall 和两条会话入库路径（数据库扫描、Capture Plugin）。当前没有自动 Prefetch。

## 实际安装

```text
~/.config/opencode/opencode.json
  └── mcp.one-memory

~/.config/opencode/plugin/one-memory-capture.js

~/.config/opencode/.one-memory/recall-install.json
```

MCP 配置形态：

```json
{
  "mcp": {
    "one-memory": {
      "type": "local",
      "command": ["/path/to/MemHub", "/path/to/mcp.cjs"],
      "environment": {
        "ONE_MEMORY_HOST_CREDENTIAL_FILE": "/path/to/credential"
      },
      "enabled": true
    }
  }
}
```

OpenCode 没有安装器认可的 Skill 机制，`skillPath=null`；使用指导依赖 MCP Tool Description。

## Capture Plugin

`one-memory-capture.js` 监听消息 part 更新与 session idle，将文本通过：

```text
one-memory capture opencode
```

送给 Core。Plugin 超时和异常不会阻断 OpenCode。

这意味着 OpenCode 有两条会话进入 MemHub 的路径：

1. Core Adapter 扫描 OpenCode 数据库；
2. Capture Plugin 实时推送。

同步开关仍应是最终接收和处理门禁，但两条路径存在职责重叠。

## 权限、重复打开与关闭

- 默认 Tools：`om_recall`、`om_conversations`、`om_diagnostic_trace`。
- MCP 只暴露三个工具：`MemHub_recall`、`MemHub_write`、`MemHub_diagnostic_trace`。`om_conversations` 不再是 MCP 工具，只授权原文通道（`/context` 与召回的原文回退）；`MemHub_write` 不受授权控制，只要求已认证的安装。
- 默认 Scopes：`all-apps`、`source:opencode`。
- 已安装且 `approved`：不重装，只修复命令并 Probe。
- 关闭：先撤权，再恢复或删除 `mcp.one-memory`、Capture Plugin 和 Journal。
- 配置或 Plugin 被用户修改时不强删，撤权后提示人工处理。

## 生效与重启

MemHub 的 Probe 不代表正在运行的 OpenCode 已热加载配置和 Plugin。建议完全退出 OpenCode、重新打开并新建会话。
