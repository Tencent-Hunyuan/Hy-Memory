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

`details` 的本地写入合同只约束结构，不维护业务字段白名单：最多 32 个 key；key 必须是
1–64 字符的字母、数字、点、下划线或连字符并以字母开头；value 只接受 `null`、布尔值、
跨 Python/JavaScript 可无损表示的有限数字（整数限于 ±(2^53-1)）或不超过 256 字符且不含
控制换行的短字符串，不接受对象和数组。这样新增诊断标签
不需要先修改中央白名单。诊断日志的校验、序列化、排队或落盘发生任何异常时都只能丢弃该条
日志，不能阻断产品主链。

本地写入放宽不代表允许官方代码记录用户内容。对话原文、查询、回答、模型输入输出、请求体、
响应体、token、API Key、secret ref、完整用户路径和异常 message/cause 仍禁止进入诊断事件，
由类型、静态检查和隐私测试共同防止误用；确需保存完整用户输入的调试能力必须使用独立的本地
trace/session 文件，不得混入可导出的诊断日志。

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

读取器只枚举上述固定文件名的 regular file，不跟随符号链接，不接受任意路径。日常本地查询
遇到损坏行时跳过并计数，不让一行损坏遮蔽其余证据。

导出是独立的安全边界：它重新读取并校验全部候选事件，只把明确列入导出白名单的字段投影为
新的 `diagnostics/events.jsonl`；details 不只检查 key，还按字段约束 value 类型，并拒绝自由文本、
路径和凭证形态的字符串。绝不复制原始 `diagnostic-*.jsonl`、普通运行日志或崩溃报告。
导出包还可以包含固定 schema 的系统摘要和问题时间点，但不接受调用方传入的任意对象展开。
任何事件、marker、安全投影或日志目录读取失败时，导出必须 fail-closed：删除临时文件、不发布
诊断包，更不能回退为打包原始日志。发布使用已设好权限的临时文件原子创建最终目标，已存在的
目标不得覆盖，发布后也不存在再 chmod 失败留下伪成功文件的窗口。

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

隐私测试必须先向本地日志植入带未知 key 的可检测秘密作为正向对照，再证明本地读取仍保留该
证据、导出安全投影必定剔除它；另需证明损坏行会阻止导出且不会留下伪成功文件。至少用隔离
数据制造 Core 启动失败、HY 失败和宿主连接失败，并仅凭允许导出的字段还原失败组件、时间、
关联 ID、稳定错误码和代码位置。自动测试通过只能标记 verified，正式安装 App 的真实体验仍需
单独验收。
