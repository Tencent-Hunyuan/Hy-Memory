# SourceAdapter V1

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
mtime。Adapter 只能在对应扫描记录已提交，或该记录可安全跳过后推进该文件
cursor。旧文件后续追加也必须能被再次发现，不能只有一个“最后文件”游标。

用户授权是读取前置条件。环境变量只用于发现候选根，不能代替授权。每个候选
文件必须在打开时再次验证 realpath 归属、symlink、普通文件类型和大小预算。

## Imported source record

Adapter 输出的 `ScannedEvidence` 是兼容命名的导入中间表示。原文持久化与 raw
权威归 Hy Memory SDK；该类型不是 MemHub 业务表合同。

必填：

- 用户、来源、会话、原始 role、真正作者、原话、至少一个 origin。
- `message_id` 有则使用；没有稳定消息 ID 时必须提供来源内确定性的
  `source_ordinal`。ordinal 只用于区分同一会话内位置，不用于跨通道关联。
- Hook / 插件不知道 `message_id` 但有同一稳定标识时写入 `correlation_id`；
  后续文件扫描提供相同 `message_id` 后合并为同一导入记录。
- Adapter 必须确认 `correlation_id` 与来源 `message_id` 属于同一命名空间；
  不能把局部序号或自行生成的 UUID 填入该字段。

约束：

- 原始 role 不能直接推导真正作者。
- tool call 参数、tool result 正文、reasoning、文件内容和命令输出默认不进入
  raw 输入；若只为维持对话结构需要，可输出不含内容的最小元数据或 `ScanSkip`。
- 缺失来源时间保持 `null`，不能用导入时间冒充。
- 同一来源消息经文件、Hook 或插件提供相同 `message_id` / `correlation_id`
  时合并 origins；没有可靠相关键时不凭相同文本强行合并。
- 跨来源相同文本不能合并。
- 每个 origin 独立保存 `raw_meta` 与 `source_version`，不能用后来的 Hook
  元数据覆盖文件定位信息；单个 origin 的 `raw_meta` 最多 64 KiB。
- 当前只接受文本记录，不声明附件、图片或音频支持。

## 来源能力

- `can_append`
- `can_observe_edits`
- `can_observe_deletes`
- `can_reconcile_complete_set`

只有 `can_observe_deletes` 的明确删除事件，或
`can_reconcile_complete_set` 的健康完整对账，才能驱动源删除。
来源暂时不可读、文件锁定或 schema 未知不能解释为删除。

## 事件、错误与恢复

- `ScannedEvidence`：产生一条文本导入记录。
- `ScanSkip`：合法排除，如 tool content、reasoning、无文本或预算限制；不计错误。
- `ScanFailure`：单条损坏或来源忙；Runner 记录后继续或稍后恢复。
- `ScanProgressEvent`：只上报真实 phase 与发现目标增量；总量未知时不得估算百分比。
- skip / failure 只允许枚举机器码。持久化错误不得包含正文、绝对路径或自由文本异常。
- conversation 错误标识进入任务库前必须不可逆哈希。
- Adapter 整体异常使 job 进入 `failed`；正常 Core 退出使运行任务进入
  `interrupted`，不是失败。
- `paused`、`interrupted` 或 `failed` job 从多文件 cursor set 恢复。
- 扫描记录必须先原子提交到 SDK 写入路径，再推进对应 cursor；若中间崩溃，
  重跑必须沿用确定性身份，不能产生重复 raw 记录。

## 版本策略

V1 字段只做向后兼容增加；删除、改名和改变既有含义必须新建合同版本。
