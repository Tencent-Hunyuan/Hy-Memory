# OpenCode 一键插入支持

更新日期：2026-11-08

## 结论

OpenCode 已开放一键插入，**V1 与 V2 均支持**。它安装 **OpenCode MCP 配置 + 实时 Capture Plugin**，不安装 Skill。

| 能力 | OpenCode 1（≥ 1.18.29） | OpenCode 2（2.2.6 实测） |
|---|---|---|
| 查询 MemHub | MCP | MCP |
| MCP 配置形态 | `mcp.one-memory` | `mcp.servers.one-memory`（原生）或旧式 `mcp.one-memory` |
| 新会话入库（扫描） | `message` / `part` / `session` | `session_message` / `session_v2` |
| 新会话入库（实时） | Plugin `server()` | Plugin `setup()` + `ctx.event.subscribe()` |
| 告诉模型何时查询 | 无 Skill；依赖 MCP Tool Description | 同左（V2 有 Skill 机制，MemHub 仍不安装） |
| Prompt 前自动 Prefetch | 没有 | 没有 |

数据库位置不变，都是 `$XDG_DATA_HOME/opencode/opencode.db`（默认 `~/.local/share/opencode/opencode.db`）。V2 改了表结构，文件名没变，因此 Adapter 按库内的表判断方言，不看应用版本号。

## 两个开关分别做什么

### 打开“一键插入应用”

写入 MCP 条目、安装 Capture Plugin、创建 Credential 并授权。OpenCode 模型需要主动调用 MCP 才会 Recall；Capture Plugin 不会向 Prompt 注入记忆。

### 关闭“一键插入应用”

先撤权，再恢复或删除 MCP、Capture Plugin 和 Journal；不修改同步开关。

### 打开“同步开关”

Core 扫描 OpenCode 数据库、恢复 history Job，并允许 Capture Plugin 实时事件进入 Evidence。

### 关闭“同步开关”

Core 停止数据库扫描和 pending Job claim，Capture Plugin 后续调用被 Core 拒绝；running Job 可完成，已有 Memory 保留。OpenCode 的 MCP 若仍安装，可以继续查询已有 Memory。

### 组合效果

同步开关打开且文件轮询可用时，Core 以数据库扫描为准，并丢弃 Capture Hook 送来的同一轮（`FILE_POLLED`），避免同一句话以两种身份入库。只在全局 live sync 关闭、而该来源同步开关仍打开时，Capture Plugin 才是实际入口。

## 实际安装

```text
~/.config/opencode/opencode.json（或用户已有的 opencode.jsonc）
  └── mcp.servers.one-memory   # V2 原生，若配置已使用 mcp.servers
      mcp.one-memory           # 旧式，V1 与 V2 都读取

~/.config/opencode/plugin/one-memory-capture.js

~/.config/opencode/.one-memory/recall-install.json
```

安装器不移动已有的 MCP 条目：配置里已经有 `mcp.servers` 时写入 V2 原生形态（`disabled: false`），否则写入旧式 `mcp` 形态（`enabled: true`）。两种形态 V2 都能读，写两份则会让同一个 Server 被读两次，所以不会同时写。

V2 原生形态：

```json
{
  "mcp": {
    "servers": {
      "one-memory": {
        "type": "local",
        "command": ["/path/to/MemHub", "/path/to/mcp.cjs"],
        "environment": {
          "ONE_MEMORY_HOST_CREDENTIAL_FILE": "/path/to/credential"
        },
        "disabled": false
      }
    }
  }
}
```

旧式形态：

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

## Capture Plugin

`one-memory-capture.js` 用一个默认导出同时提供两个入口：

```js
export default { id: "one-memory-capture", server, setup }
```

- `server()`：V1 入口（对象形态自 OpenCode 1.18.29 起支持），监听 `message.part.updated` / `session.idle`。
- `setup(ctx)`：V2 入口。用户文本来自 `ctx.session.hook("prompt")`，助手文本来自 `ctx.event.subscribe()` 的 `session.text.ended`（按块 `ordinal` 收集，不覆盖），`session.execution.succeeded` / `failed` / `interrupted` 与 `session.idle` / 空闲 `session.status` 触发 flush。
- 两个入口都把文本通过 `one-memory capture opencode` 送给 Core，payload 一致；Plugin 超时和异常不会阻断 OpenCode。
- 带 `message_id` 的条目会被 Core 用于去重。同一条消息的多个文本块（V1 按 part id、V2 按 `ordinal`）合并为一个条目发送，避免同一 id 的两个条目被 Core 当作相互修订；同一进程内重复加载多个 Plugin 实例也不会重复发送。

因此 OpenCode 有两条会话进入 MemHub 的路径：

1. Core Adapter 扫描 OpenCode 数据库（生效路径）；
2. Capture Plugin 实时推送（全局 live sync 关闭时的备用路径）。

同步开关仍是最终接收和处理门禁。

## 权限、重复打开与关闭

- 默认 Tools：`om_recall`、`om_conversations`、`om_diagnostic_trace`。
- MCP 只暴露三个工具：`MemHub_recall`、`MemHub_write`、`MemHub_diagnostic_trace`。`om_conversations` 不再是 MCP 工具，只授权原文通道（`/context` 与召回的原文回退）；`MemHub_write` 不受授权控制，只要求已认证的安装。
- 默认 Scopes：`all-apps`、`source:opencode`。
- 已安装且 `approved`：不重装，只修复命令并 Probe。
- 关闭：先撤权，再恢复或删除 MCP、Capture Plugin 和 Journal。
- 配置或 Plugin 被用户修改时不强删，撤权后提示人工处理。

## 生效与重启

OpenCode V2 会监听配置与 Plugin 目录并热加载，但 MCP 连接与 Plugin 实例的清理仍以服务重载为准。MemHub 的 Probe 也不代表正在运行的 OpenCode 已经加载了新 Plugin。建议完全退出 OpenCode、重新打开并新建会话。

## 已知限制

- 安装器只写 `opencode.json`；配置仅存在于 `opencode.jsonc` 时，会新建一个 `opencode.json`。V2 同时读取两者，功能不受影响，但文件会分成两个。
- V2 表的 `session_part` 目前为空，文本与工具内容都在 `session_message.data`；Adapter 暂不读取该表。
- 流式中的助手消息会等到 `time.completed` 或 10 分钟静默窗口后再入库，避免保存半句回复。
- 只读 `user` / `assistant` 行；`synthetic`（OpenCode 代用户注入的文本，V1 的 synthetic text part 迁移后即此类型）、`compaction`、`idle` 等行不入库。
- 从 V1 升级到 V2：OpenCode 原地迁移并保留消息 id。V1 Adapter 留下的游标仍能在旧 `message` 表上验证，Adapter 据此跳过已导入的消息（V2 游标携带 `;v1=<rowid>` 水位，扫描中断后仍有效），不会重复入库或重复触发模型写入。
