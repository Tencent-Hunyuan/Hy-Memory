# OpenClaw 一键插入支持

更新日期：2026-10-14

## 结论

OpenClaw 当前是“底层 MCP installer 已实现，但正式产品全链路 Freeze”。用户不能通过当前 App 正式启用一键插入。

## 接入方式和产品状态

| 项目 | OpenClaw 当前真实状态 |
|---|---|
| 一键插入产品状态 | 未开放 |
| OpenClaw 查询 MemHub | 当前没有；只有未开放的 MCP 配置写入器 |
| Skill | 没有安装 |
| Plugin | 没有安装 |
| Prompt Hook | 没有安装 |
| 自动 Prefetch/上下文注入 | 没有实现 |
| OpenClaw 新会话实时送入 MemHub | 没有 Hook；Core Adapter 也被冻结 |
| Credential/Grant | 正式流程不会创建 |

当前用户无法通过 MemHub App 把任何插件、Hook 或 Prefetch Provider 安装进 OpenClaw。

## 如果只放开底层 MCP，会怎样

理论调用链是：

```text
OpenClaw 读取 openclaw.json
→ 启动 mcp.servers.one-memory
→ OpenClaw 模型在需要时主动调用 MemHub_recall / MemHub_write / MemHub_diagnostic_trace
→ MemHub mcp.cjs 通过本机 Broker 调用 Core
```

这只是主动工具调用，不是：

```text
用户发送 Prompt
→ MemHub 自动 Prefetch
→ 记忆自动注入 OpenClaw 上下文
```

当前没有任何实现能自动完成后者。

## 两个产品开关当前实际行为

### “一键插入应用”

当前不可用。Web、Desktop、Runtime、Connection Controller 和权限层都拒绝 OpenClaw，因此不会写 MCP、不会创建 Credential、不会安装 Skill/Hook/Plugin。

### “同步开关”

当前也不可用。OpenClaw 来源在 Core 中被全局暂停：不 `stat` 目录、不探测、不创建 Adapter、不扫描、不创建可执行 Job。

### 所以当前组合结果

无论用户如何尝试，正式产品中两个方向都没有接通：

```text
OpenClaw → MemHub：关闭
MemHub → OpenClaw：关闭
自动 Prefetch：不存在
```

## 真正支持 OpenClaw 需要什么

至少有两种产品方案：

1. **只支持 MCP 主动 Recall**：开放 MCP 安装、Credential 和授权；不碰 memory-provider，模型是否查询依赖 Tool Description。
2. **支持自动 Prefetch**：还需实现 OpenClaw memory-provider 集成，明确如何处理已有 Provider、如何组合或拒绝、如何回滚、超时如何 fail-open、注入内容如何避免再次入库。

当前第二套完全没有实现。

## 当前产品限制

- Web 一键映射中没有 OpenClaw。
- Desktop Host 列表主动过滤 OpenClaw。
- Runtime Host Control、Connection Controller、Connection Client 和 `HostPermissions` 都拒绝 OpenClaw。
- Core 导入层把 OpenClaw 放入暂停列表，不探测、不启动、不读取。

## 底层 installer 理论行为

如果直接调用底层安装器，会写：

```text
~/.openclaw/openclaw.json
  └── mcp.servers.one-memory

~/.openclaw/.one-memory/recall-install.json
```

它不安装 Skill，也不安装 Capture Hook：

- OpenClaw 的个人 Skill 位于共享 `~/.agents/skills`，直接写入会影响其他 Agent；
- `skills.load.extraDirs` 需要额外安装和回滚状态，尚未完成；
- 实时采集需要占用单一 memory-provider slot，可能覆盖用户已有 Provider。

## 为什么 Freeze

- 导入仅做过 fixture 级验证，没有真实 OpenClaw 会话回放验收；
- 用户角色归因尚未解决，可能导致内容不能进入记忆提取；
- Skill、Capture 和权限边界尚未形成完整产品流程。

## 底层 CLI 风险

开发 installer CLI 仍可直接写 OpenClaw MCP 配置，但它不会创建正式 Credential 或 Grant；同一 CLI 的 rollback 还被禁用。

因此可能产生：

```text
openclaw.json 中存在 one-memory
但没有合法 Credential/Grant
且不能通过正式产品流程管理
```

这是开发工具残留，不代表产品支持。

## 卸载

底层库函数可以按 Journal 恢复或删除 `mcp.servers.one-memory`，但正式 `HostPermissions.uninstall()` 因 OpenClaw 被排除而不可用。直接使用底层安装器的调用方必须自己负责 Credential 和卸载安全。
