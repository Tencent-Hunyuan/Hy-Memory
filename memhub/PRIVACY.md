# Privacy

MemHub 是本地优先的共享记忆应用，只处理用户明确授权的 AI 对话来源。文中的
One Memory Home 指历史兼容数据目录，默认路径为 `~/.one-memory`。

## 本地数据

- 来源原文以 Evidence 形式保存在用户本地数据目录。
- 来源读取、模型处理和宿主读取是相互独立的授权边界。
- 暂停、撤权、更正和删除不会因为安装入口不同而改变语义。
- 卸载程序默认不删除用户记忆数据；删除数据必须由用户单独确认。

## 网络访问

当用户选择第三方模型服务时，MemHub 会把完成所选任务所需的获授权内容发送给该
服务。模型提供方、发送范围和用途必须在启用前可见。产品不得把 API Key、任意请求头、
本地绝对路径或与任务无关的原始内容发送给模型。

## 不采集的内容

产品不包含屏幕、Accessibility / AXTree、OCR、键鼠、剪贴板或后台录音采集。

## 诊断与调试日志

默认诊断级别为 `info`，结构化诊断日志只接受脱敏字段，不记录对话正文、模型
prompt/response、密钥、访问令牌或完整本地路径。结构化日志位于 One Memory Home 下的
`logs/`，单文件 4 MiB、保留 3 份轮转，清理 14 天以前的文件，并将总量限制在 128 MiB。
诊断导出包含版本、平台、架构、非秘密状态和最多 5000 条已验证事件，不包含数据库或配置
文件。详细合同见
[`docs/contracts/diagnostic-logging.md`](docs/contracts/diagnostic-logging.md)。

HY pipeline 日志与 trace 在任何级别下都关闭。HY SDK 的文本日志只写入
`hy-memory/governed-v2/logs/hy_memory.log`，其中的 WARNING 及以上同时写入 `core.log`；
SDK 不在 INFO 及以上级别记录对话或记忆正文。用户显式把诊断级别切换为 `debug` 时，Core 将
`MEMORY_LOG_LLM_CONTENT` 设为 `true`。该模式会把完整模型 prompt/response 写入
`hy_memory.log` 的 DEBUG 行，其中可能包含获授权的对话正文和模型输出；它只应用于有意识的
故障排查，不应长期开启，也不应在未经检查和脱敏时分享相关日志。切回 `info` 会停止后续正文记录，但不会
自动删除此前已写入的调试文件。

具体行为以当前源码、网络请求实现和发布说明为准。发现实现与本说明不一致时，请按
[`SECURITY.md`](SECURITY.md) 的私下渠道报告。
