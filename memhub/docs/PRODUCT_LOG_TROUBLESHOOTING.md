# MemHub 产品日志排查手册

本文给产品、测试和一线支持同学使用。目标不是解释底层实现，而是回答三个问题：

1. 数据有没有成功导入；
2. 记忆沉淀有没有持续运行、产出了多少条；
3. 如果停住，停在导入、模型调用还是最终写入。

默认 `info` 下的官方结构化事件不应写入会话正文、模型输入输出或 API Key；但本地原始日志允许
新增受限标量标签，仍应按私人数据处理，不能直接对外发送。`debug` 会额外启用 HY pipeline
trace，其中可能包含完整 prompt/response 和获授权的对话正文。

排查时只通过 App 的“导出日志”提供文件：导出过程会按安全白名单重新生成诊断事件，不会打包
原始日志。不要直接发送 `diagnostic-*.jsonl`、整个 `~/.one-memory` 数据目录或 debug pipeline
日志；后者如确需分享，必须由开发人员另行确认并脱敏。

## 1. Log level 设什么

### 日常观察：`info`

`info` 是默认级别，已经包含导入数量、任务排队、模型沉淀结果、待处理数量和失败终态。
持续观察产品是否正常工作时，不需要打开 debug。

### 复现具体问题：`debug`

只有开发人员明确要求跟踪一次点击、一次召回或一个具体模型任务时，才打开：

> 设置 → 运行诊断 → 详细日志（排查用）

开关打开后等同于 `debug`。复现完成后：

1. 点击“记录问题时间点”；
2. 点击“导出日志（发给开发排查）”；
3. 关闭“详细日志（排查用）”。

debug 可能把完整模型 prompt/response 写入本机调试日志，不得长期开启。切回 `info`
只会停止后续正文记录，不会自动删除此前已经产生的 debug 文件。

不要把日志级别设置成仅 `warning` 或 `error` 来判断系统是否健康，因为正常进度事件是
`info`。

## 2. 日志在哪里

App 内推荐直接使用“运行诊断 → 导出日志”。开发人员远程指导时，优先使用
`memhub logs`，它会再次过滤不安全字段。原始结构化日志只用于本机现场查看，不得作为附件发送。
Core 日志默认位于：

```text
~/.one-memory/logs/diagnostic-core.jsonl
```

每行都是一条 JSON。macOS 终端中可先设置：

```bash
LOG="$HOME/.one-memory/logs/diagnostic-core.jsonl"
```

## 3. 用什么 key 过滤

产品排查只需要认识以下字段：

- `timestamp`：发生时间；
- `level`：`info`、`warn`、`error`；
- `event`：事件名称，是最重要的过滤 key；
- `outcome`：`started`、`succeeded`、`completed`、`failed` 等结果；
- `error_code`：稳定错误码，可直接发给开发；
- `job_id`：一次导入任务；
- `task_id`：一次记忆沉淀任务；
- `request_id` / `trace_id`：串联一次模型或界面请求；
- `details.result_count`：本次沉淀最终关联了多少条记忆；
- `details.pending_count`：完成本次任务后还有多少条待处理；
- `details.stored_count`：本次导入新增了多少条原始消息；
- `duration_ms`：耗时。

不要按用户原话、会话内容或 API Key 搜索；这些内容按设计不会进入诊断日志。

## 4. 系统正常工作时应该看到什么

### A. Core 已启动

```text
event = core.ready
outcome = succeeded
```

没有 `core.ready` 时，后面的导入和沉淀都不会运行。

### B. 原始消息正在导入

```text
event = import.finished
outcome = completed
details.stored_count >= 0
details.error_count = 0
```

`stored_count=0` 可能只是没有新消息；只要状态不是 failed，就不能单凭 0 判故障。

### C. 记忆任务进入队列

```text
event = memory_scheduler.tasks_queued
outcome = succeeded
details.prepared_count > 0
```

这表示导入的数据已经进入记忆沉淀队列。

Write Job 不是按 Harness 各自抢跑。Drain 以全局最老的 `event_at`
建立固定十分钟窗口：

- 窗口内不同 Harness 可以并发；
- 同一 Harness 仍按 `queue_seq` 串行；
- `time_quality=exact` 来自真实 turn 时间；
- `session/approximate` 表示缺少 turn 时间，使用 session/file/import
  回退；
- 首次多来源历史导入完成前，Write Job 可以积压，但模型调用应为 0；
- 沉淀开关关闭时 pending 可以增加，`hy_invoked` 不应增加。

如果出现明显的新对话先于十分钟窗口之外的旧对话完成，检查
`memory_write_jobs.event_at/time_quality/queue_seq` 和
`memory_write_schedule_state`；完整合同见
`contracts/memory-event-time-scheduling-v1.md`。

### D. SDK 正在沉淀

```text
event = hy.ingest_started
outcome = started
```

随后应出现：

```text
event = hy.ingest_completed
outcome = succeeded
details.result_count = 本次 SDK 返回的记忆数
```

如果有 reconcile，还会出现：

```text
event = hy.reconcile.completed
details.created_count
details.updated_count
details.superseded_count
details.deleted_count
```

### E. MemHub 已完成本次沉淀

最适合产品观察的是：

```text
event = memory_task.finished
outcome = completed
details.result_count = 本任务最终写入或关联的记忆数
details.pending_count = 剩余待处理任务数
details.attempt = 第几次尝试
```

健康表现是：

- `memory_task.finished` 持续出现；
- `pending_count` 总体下降，或在持续导入时保持动态变化；
- `result_count` 间歇性大于 0；
- 没有持续重复的同一 `error_code`。

`result_count=0` 并不一定是故障。某段对话没有值得长期保存的信息时，0 是正常结果。只有大量
任务连续为 0，同时存在 `hy.ingest_failed`、`memory_task.failed` 或积压只增不减，才需要升级
排查。

## 5. 可复制的过滤命令

优先使用以下安全读取命令：

```bash
memhub logs --since 2h --event memory_task.finished --json
memhub logs --since 2h --event hy.reconcile.completed --json
memhub logs --since 24h --errors-only --tail 500 --json
memhub status --json
```

以下 `jq` 命令仅用于 `diagnostic-core.jsonl`，不要对 `core.log`、`service.log` 或 debug
pipeline 日志直接运行后把完整输出发给他人。

### 查看最近的失败

```bash
jq -r '
  select(.level == "error" or .outcome == "failed") |
  [.timestamp, .service, .event, (.error_code // "-"), (.task_id // .job_id // "-")] |
  @tsv
' "$LOG"
```

### 查看每次沉淀数量和剩余积压

```bash
jq -r '
  select(.event == "memory_task.finished") |
  [.timestamp, .outcome, (.details.result_count // 0),
   (.details.pending_count // 0), (.details.attempt // 0), (.error_code // "-")] |
  @tsv
' "$LOG"
```

输出列依次是：

```text
时间  结果  本次记忆数  剩余待处理数  尝试次数  错误码
```

### 统计当前日志里累计完成和累计记忆数

```bash
jq -s '
  [.[] | select(.event == "memory_task.finished" and .outcome == "completed")] |
  {
    completed_tasks: length,
    memory_results: (map(.details.result_count // 0) | add // 0),
    latest_pending: (last.details.pending_count // 0)
  }
' "$LOG"
```

这里的 `memory_results` 是任务结果数量之和，用于判断流水线是否持续产出；它不是当前记忆库
去重后的总行数。

### 查看导入数量

```bash
jq -r '
  select(.event == "import.finished") |
  [.timestamp, .details.source_id, .outcome,
   (.details.stored_count // 0), (.details.revised_count // 0),
   (.details.skipped_count // 0), (.details.error_count // 0)] |
  @tsv
' "$LOG"
```

### 跟踪一个具体任务

先从失败或完成日志复制 `task_id`：

```bash
TASK_ID="粘贴-task_id"
jq --arg id "$TASK_ID" 'select(.task_id == $id)' "$LOG"
```

## 6. 常见停点怎么判断

- 有 `core.ready`，没有 `import.finished`：来源未授权、未开启同步，或来源读取失败。
- `import.finished` 正常，没有 `memory_scheduler.tasks_queued`：导入内容尚未形成完整会话，或未进入处理窗口。
- 有 `memory_task.started`，没有 `hy.ingest_completed`：重点查模型配置、余额、网络、超时和 `hy.ingest_failed`。
- 有 `hy.ingest_completed`，但 `memory_task.finished` 失败：重点查 SDK 结果确认、写入队列终态和对应 `error_code`。
- `pending_count` 长时间完全不下降：查看是否反复出现同一个错误码，或任务都在
  `waiting_for_model`。
- 导入数量持续增加、沉淀任务完全没有出现：这是异常，不应解释为“对话没有记忆价值”。

## 7. 产品反馈给开发时提供什么

请提供：

1. 问题发生的大致时间；
2. App 版本和平台；
3. 是否开启了详细日志；
4. 点击“记录问题时间点”后显示的结果；
5. 通过“导出日志”得到的诊断文件；
6. 最近重复出现的 `error_code`；
7. `memory_task.finished` 最近几行中的 `result_count` 和 `pending_count`。

不要提供 API Key，也不要打包发送整个记忆数据库。
