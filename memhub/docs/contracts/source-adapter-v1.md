# SourceAdapter V1


> **架构变更（2026-10-21）：** 现行权威见 [`../ARCHITECTURE.md`](../ARCHITECTURE.md) **Top Rules**。~~MemHub Evidence / `governed_*` 作原文或 memory 权威~~ 已废弃为目标；memory 读写与 backend 只经 SDK；MemHub 可存 `memory_id` 引用。下文未逐段删线的历史表述，凡与 Top Rules 冲突处以架构文档为准。

状态：9/2 冻结；9/3 仅做向后兼容补强。

## 输入与输出

每个来源实现：

- `descriptor`：来源 ID、展示名、存储形态和能力声明。
- `detect()`：只判断用户已授权根内是否有可读来源；不能扫描真实 Home
  来“猜”用户装了什么。
- `scan(options)`：异步流式输出 `ScannedEvidence | ScanFailure |
  ScanSkip | ScanProgressEvent`。

`ScanOptions.cursors` 是同一用户、同一来源的多文件游标集合。每个 cursor
包含 opaque `file_key`、文件 identity、已提交 checkpoint、观察到的大小和
mtime。Adapter 只能在对应 Evidence 已提交，或该记录可安全跳过后推进该文件
cursor。旧文件后续追加也必须能被再次发现，不能只有一个“最后文件”游标。

用户授权是读取前置条件。环境变量只用于发现候选根，不能代替授权。每个候选
文件必须在打开时再次验证 realpath 归属、symlink、普通文件类型和大小预算。

## Evidence

~~**旧目标：** Adapter 产出写入 MemHub Evidence 权威库，再进沉淀队列。~~  
**新目标：** Adapter 仍可描述「扫描到的原文记录」形状，但持久化与 raw 权威归 **SDK**；本节字段名若仍叫 Evidence，视为 **导入中间表示**，不是 MemHub 业务权威表合同。

必填：

- 用户、来源、会话、原始 role、真正作者、原话、至少一个 origin。
- `message_id` 有则使用；没有稳定消息 ID 时必须提供来源内确定性的
  `source_ordinal`。ordinal 只用于区分同一会话内位置，不用于跨通道关联。
- Hook / 插件不知道 `message_id` 但有同一稳定标识时写入 `correlation_id`；
  后续文件扫描提供相同 `message_id` 后合并为同一 Evidence。
- Adapter 必须确认 `correlation_id` 与来源 `message_id` 属于同一命名空间；
  不能把局部序号或自行生成的 UUID 填入该字段。

约束：

- 原始 role 不能直接推导真正作者。
- tool call 参数、tool result 正文、reasoning、文件内容和命令输出默认不进入
  Evidence；若只为维持对话结构需要，可输出不含内容的最小元数据或 `ScanSkip`。
- 缺失来源时间保持 `null`，不能用导入时间冒充。
- 同一来源消息经文件、Hook 或插件提供相同 `message_id` / `correlation_id`
  时合并 origins；没有可靠相关键时不凭相同文本强行合并。
- 跨来源相同文本不能合并。
- 每个 origin 独立保存 `raw_meta` 与 `source_version`，不能用后来的 Hook
  元数据覆盖文件定位信息；单个 origin 的 `raw_meta` 最多 64 KiB。
- 当前只接受文本 Evidence，不声明附件、图片或音频支持。

## 来源能力

- `can_append`
- `can_observe_edits`
- `can_observe_deletes`
- `can_reconcile_complete_set`

只有 `can_observe_deletes` 的明确删除事件，或
`can_reconcile_complete_set` 的健康完整对账，才能驱动源删除。
来源暂时不可读、文件锁定或 schema 未知不能解释为删除。

## 事件、错误与恢复

- `ScannedEvidence`：产生一条文本 Evidence。
- `ScanSkip`：合法排除，如 tool content、reasoning、无文本或预算限制；不计错误。
- `ScanFailure`：单条损坏或来源忙；Runner 记录后继续或稍后恢复。
- `ScanProgressEvent`：只上报真实 phase 与发现目标增量；总量未知时不得估算百分比。
- skip / failure 只允许枚举机器码。持久化错误不得包含正文、绝对路径或自由文本异常。
- conversation 错误标识进入任务库前必须不可逆哈希。
- Adapter 整体异常使 job 进入 `failed`；正常 Core 退出使运行任务进入
  `interrupted`，不是失败。
- `paused`、`interrupted` 或 `failed` job 从多文件 cursor set 恢复。
- Evidence 必须先原子提交，再推进对应 cursor；若中间崩溃，重跑只能 merge，
  不能产生新 Evidence 或正文 revision。

## 版本策略

V1 字段只做向后兼容增加；删除、改名和改变既有含义必须新建合同版本。
