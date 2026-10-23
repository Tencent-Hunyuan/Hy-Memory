# Hermes 一键接入

更新日期：2026-10-22

## 接入组成

Hermes 接入同时安装两条互补路径：

| 能力 | 实现 |
|---|---|
| 每轮自动召回 | 原生 Python `MemoryProvider` 的 `queue_prefetch/prefetch` 生命周期 |
| 显式查询与诊断 | MCP Server + `memhub-recall` Skill |
| 会话写入 MemHub | Core 扫描 Hermes `state.db` / Session 文件 |
| 原生 Provider 自动写入 | 不启用，避免召回内容回流形成套娃 |

Python Provider 源码位于仓库
[`plugins/hermes/hy_memory`](../../../plugins/hermes/hy_memory)，发行构建将其作为
Host Bridge 受管资源打包。

## 打开“一键接入”

用户点击接入即确认 MemHub 可以切换 Hermes 的唯一
`memory.provider` slot。安装事务：

1. 创建 Hermes Installation、Credential 与 Recall Grant；
2. 安装受管 Python Provider；
3. 将 `memory.provider` 设为 `hy_memory`；
4. 写入 MCP Server 与 `memhub-recall` Skill；
5. 写安装 Journal，并执行连接 Probe。

```text
~/.hermes/plugins/hy_memory/
  __init__.py
  plugin.yaml
  provider.py
  connection.json

~/.hermes/config.yaml
  memory.provider: hy_memory
  mcp_servers.one-memory: ...

~/.hermes/skills/memhub-recall/SKILL.md
~/.hermes/.one-memory/recall-install.json

<MemHub dataDir>/host-permissions/...
  Hermes Credential、Installation、Grant
```

`connection.json` 只保存 Broker discovery 和 credential file 的路径，不保存
Core token 或 memory 内容。Provider 每次请求重新读取 discovery/credential，经
MemHub Broker `/context` 完成授权查询；不直连裸 Hy Memory HTTP server。

Hermes 必须完全退出并重新启动，原生 Provider 才会可靠生效。

## 自动 Recall 与 Session 去重

Hermes 在 turn N 结束后后台执行 `queue_prefetch`，turn N+1 开始时由
`prefetch` 消费已完成结果。前后 query 不要求完全相同；cache miss 不在当前轮次
重复发起第二次搜索。

Hermes 不会清除历史轮次中已注入的 memory。Provider 因此按 Hermes
`session_id` 保存已注入的 opaque `memory_id` 集合：

- 本 session 已出现的 ID 不再注入；
- 一次响应中的重复 ID 只保留首项；
- 相同正文、不同 ID 仍视为不同 memory；
- 切换 Session 使用各自的集合；
- reset / rewind 清理目标 Session 的集合；
- 搜索完成但没有实际注入的 ID 不进入集合。

集合以 session hash → opaque memory ID 的形式保存在
`~/.hermes/.one-memory/hy-memory-seen.json`，不保存 memory 正文，因此 Hermes
重启并恢复同一 Session 后仍可去重。

召回正文始终标记为不可信参考数据，不是指令。
注入块使用受控的 `memhub_memory_context` 边界，并对正文中的保留 tag 做转义；
即使宿主把注入内容写回历史，Source Adapter 也会在入库前剥离该块。

## 防止 memory 套娃

MemHub-managed Provider 是只读的：

- 自动 capture 关闭；
- 不暴露 `hy_memory_add`；
- Hermes 会话写入继续由 MemHub Source Adapter 扫描完成；
- Adapter 排除 system 注入内容，Provider 也不会把 assistant 对召回内容的复述
  作为另一条写入路径提交。

这样同一份 memory 不会因为被 prefetch 到 Hermes 后再次进入 memory 生成链路。

## 多 Profile 隔离

Hermes 支持多个 profile（默认 profile 在 `~/.hermes`，其他在
`~/.hermes/profiles/<name>/`），每个 profile 各有自己的 `state.db`。
Hermes 来源的默认扫描根是 `~/.hermes`，会递归覆盖所有 profile；只想导入
其中一个 profile 时，两种做法：

- 在「选择目录」里指定该 profile 目录（如 `~/.hermes/profiles/luo-bo`），
  授权时按 `user_selected` 根落盘；
- 或用 `HERMES_HOME` 指向 profile 目录，此时来源按
  `environment_override` 处理，接入时需要 `--confirm-overrides`；宿主接入
  （Provider / MCP / Skill 的安装位置）也跟随 `HERMES_HOME`，不会写回默认 profile。

两种方式互斥：一旦设置了 `HERMES_HOME`，picker 不再允许改选（与其它
环境覆盖来源一致）。

## 关闭与卸载

通过 MemHub 关闭接入时：

1. 先撤销 Hermes Credential；
2. 如果 `memory.provider` 仍是 MemHub 安装的值，恢复接入前的 provider；
3. 如果 Provider 文件与安装 hash 一致，删除或恢复受管目录；
4. 恢复/删除 MCP、Skill 和 Journal。

如果用户在安装后修改了 Provider 文件或配置，MemHub 停止自动清理并要求人工核对，
不会覆盖用户的新值。

用户直接从 `/Applications` 删除 MemHub 时，系统不保证有卸载回调，因此无法保证
恢复 Hermes 配置。遗留 Provider 会失败安全：Broker 不可用时只是不召回，不影响
Hermes 正常启动和对话。

## 同步开关

同步开关与一键接入相互独立：

- 打开：Core 校验 `.hermes` Root，扫描历史和新增 Session，并调度 SDK 写入；
- 关闭：停止发现和 claim 新 Job，已运行 Job 可以结束；
- 关闭同步不会卸载 Provider、MCP 或 Skill；
- 关闭一键接入不会删除已经进入 SDK 的 memory。

## 安全边界

- Provider 使用 host credential 和 Recall Grant，不持有 Core bearer。
- Broker 只接受 loopback、拒绝 proxy/redirect，并在释放结果前重新校验授权。
- `memory_id` 是去重身份，不是授权凭据。
- 用户原文、memory 内容、token、路径和后端错误正文不写入诊断错误。
- Python Provider 不安装 SDK、不启动 sidecar、不直接操作 memory store。
