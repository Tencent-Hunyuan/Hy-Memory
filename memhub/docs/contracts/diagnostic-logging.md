# One Memory 诊断日志合同

更新：2026-09-24。关联 D07、D26、RT-D41。

## 目标

诊断日志用于在用户体验出现问题后，按最近时间、运行会话、一次操作或问题编号还原
Desktop -> Runtime -> Core -> HY -> Host 的实际执行链。日志不是用户行为分析、调用记录或
记忆来源，不进入 `call_events` 和记忆数据库，也不上传网络。

## 事件格式

每行是一个 `DiagnosticEventV1` JSON 对象。必填字段为 `schema_version=1`、UTC
`timestamp`、`level`、`service`、`component`、`event`、`outcome` 和 `session_id`。
可选关联字段为 `trace_id`、`incident_id`、`instance_id`、`operation_id`、`request_id`、
`job_id`、`task_id`、`duration_ms`、状态、稳定错误码、错误类型和清理后的仓库相对栈帧。

`details` 只接受代码声明的标量白名单。禁止记录对话原文、查询、回答、模型输入输出、
请求体、响应体、token、API Key、secret ref、完整用户路径或异常 message/cause。未知字段和
不合规标识拒绝写入。日志写入失败不能阻断产品主链。

## 关联规则

- Desktop、Runtime、Core 各次启动有独立 `session_id`。
- 用户请求生成 UUID `trace_id`，经 `X-One-Memory-Trace-Id` 在 Web、Broker 和 Core 之间传播；
  无效外来值被替换，不原样记录。
- 已有 operation/request/job/task 身份沿用业务合同，不创建平行身份。
- “记录问题时间点”创建 `incident_id` 并记录当前 Runtime instance。按 incident 查询时返回
  同桌面会话或同 Runtime instance 在标记前 15 分钟至后 5 分钟的事件。

## 存储与读取

日志位于 One Memory Home 的 `logs/diagnostic-<service>.jsonl`，服务包括 runtime、core、
host-bridge、desktop 和 cli。单文件 4 MiB，保留 3 份轮转；额外清理 14 天以前的文件并将
总量限制在 128 MiB。POSIX 目录为 0700、文件为 0600。

读取器只枚举上述固定文件名的 regular file，不跟随符号链接，不接受任意路径。损坏行跳过
并计数，不让一行损坏遮蔽其余证据。诊断导出是单个脱敏 JSON，包含版本、平台、架构、
非秘密状态和最多 5000 条已验证事件，不包含数据库或配置文件。

## 覆盖与节流

必须记录启动/停止、attach/spawn、身份核验、导入和记忆任务状态、模型/HY 调用、宿主召回、
权限与安装操作、关键 IPC、失败分类、重试、耗时和终态。不记录无变化的高频轮询或逐条原文，
导入使用阶段和计数汇总，避免日志反过来造成性能问题。

产品进度判断使用以下稳定事件：

- `import.finished`：`stored_count`、`revised_count`、`skipped_count`、`error_count`；
- `hy.ingest.completed`：SDK 本次返回的 `result_count`；
- `hy.reconcile.completed`：新增、更新、取代和删除数量；
- `memory_task.finished`：最终 `result_count`、剩余 `pending_count` 和 `attempt`。

其中 `memory_task.finished` 是产品侧判断沉淀是否持续推进的最终事件；不能只用
`hy.ingest.completed` 代替，因为后者早于 SDK 结果确认和写入队列终态。

## 验收

隐私测试必须先植入一条可检测的秘密作为正向对照，再证明真实链路日志和导出均不含该秘密。
至少用隔离数据制造 Core 启动失败、HY 失败和宿主连接失败，并仅凭日志还原失败组件、时间、
关联 ID、稳定错误码和代码位置。自动测试通过只能标记 verified，正式安装 App 的真实体验仍需
单独验收。
