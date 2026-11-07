# Google Antigravity — 文件扫描接入

> 状态：**文件扫描接入**（无一键插入、无 Hook）。验证等级：`fixture_only`。
> 依据：<https://antigravity.google/docs/hooks?tab=ide>（Antigravity App / CLI / IDE 通用契约）。

本页描述 Antigravity 官方公开的存储布局，以及 MemHub 实际支持的那条路径。
**尚未提供**一键插入安装器、`recall` 安装目标，也**没有**在真机 Antigravity 会话上
replay 验证过；这些需要本机安装 Antigravity 并采样真实 transcript 后才能补齐，
不宜按其他 Harness 的形状类推（`registry.py` 已有同样约定）。

## 1. 为什么没有 Hook 通道

Antigravity 的 `hooks.json` 本身是可用的（见附录），但**当前架构下不应再加一条 hook**：

`api.file_polled()` 的判定是「来源的 catalog 声明了 `live_sync` 且运行时开了实时同步」。
只要 Antigravity 注册了 catalog（同步开关需要它，见 §4），hook 就会被判为冗余并直接
返回 `CaptureOutcome.FILE_POLLED`，不进入 staging。上游 `ebf16b939` 把两件事一起改了：

- hook 进程不再自己写库，改为 `POST /api/v1/live-capture/{source}` 交给运行中的 Core；
- **同一轮对话只允许一条 lane**——hook 与文件轮询给同一条消息算出不同的身份，
  两条都写就会入库两次。

所以 Antigravity 保持**单条文件 lane**：transcript 本来就在磁盘上，轮询能读到。

## 2. 数据位置

```text
~/.gemini/<app_data_dir>/brain/<conversationId>/.system_generated/logs/transcript.jsonl
# <app_data_dir> = antigravity（App）| antigravity-cli（CLI）| antigravity-ide（IDE）
# 无截断的兄弟文件：transcript_full.jsonl
```

三个变体按**声明顺序**探测，不按 mtime——残留目录不该压过正在使用的那个。
只扫 `transcript.jsonl`：`transcript_full.jsonl` 是同一批 step 的无截断副本，
两个都读会让每一轮入库两次。

## 3. step 与可见行

每行是一个 **step**，不是一条消息：

| 字段 / 取值 | 处理 |
| --- | --- |
| `type: USER_INPUT` | ✅ `user` / `PERSON`，`content` 剥掉 `<user_query>` 之类的信封标签 |
| `type: PLANNER_RESPONSE` | ✅ `assistant` / `AGENT`，只取 `content`；`thinking` 是内部推理，不入库 |
| `type: GENERIC` 等工具结果 | ❌ 工具输出不入库 |
| `status != DONE` | ❌ 仍在写入的 step 留给下一次扫描 |
| `truncated_fields` 含 `content` | ❌ 半句话不入库 |
| 含 `om-derived:` 行 | ❌ 注入的记忆前言不算本人说的话 |

`step_index` 是主机自己的顺序键，且在 `transcript.jsonl` 与 `transcript_full.jsonl`
之间一致，所以用它作 `message_id`：重复读取不会翻倍。缺失时退回记录序号。

## 4. 怎么启用

1. 在「我的应用」里打开 Antigravity 来源并授权根目录（`~/.gemini/<变体>/brain`）；
2. 打开该来源的同步开关——`capture_rejection` 与文件轮询都以 catalog 的
   `sync_enabled` + `source_grants` 为前提，只声明 adapter 不会让任何东西动起来；
3. 首次导入会补扫历史，之后由共享轮询器增量读取。

## 5. 覆盖与未覆盖

| 项目 | 状态 |
| --- | --- |
| Catalog 注册 / 同步开关 / 授权 | ✅ |
| `transcript.jsonl` 增量扫描与可见行解析 | ✅ fixture 测试 |
| 来源筛选与调用历史里的 `antigravity` 分类 | ✅ |
| 一键插入安装器 / `recall` 安装目标 | ❌ 未实现，需真机安装路径验证 |
| Hook 实时通道 | ❌ 刻意不做：会与文件 lane 重复（§1） |
| 真机 replay（`real_replayed`） | ❌ 未验证，能力声明保持 `fixture_only` |

将来若要补 hook 实时通道，必须**同时**去掉该来源 catalog 条目里的 `live_sync`，
否则 `file_polled` 会一直短路它；先用真机采样确认 payload 与 transcript 的
消息身份能对齐，再决定保留哪一条 lane，不能两条都开。

## 附录：Antigravity `hooks.json` 契约（供将来使用）

顶层**按 hook 名字分组**，不是 Claude Code 的 `{"hooks": {...}}`；写错形状会让
Language Server 静默丢弃**整个**配置文件，其它 hook 一起失效。

```json
{
  "my-hook": {
    "enabled": true,
    "Stop": [
      { "type": "command", "command": "./scripts/on-stop.sh", "timeout": 30 }
    ],
    "PostToolUse": [
      { "matcher": "run_command", "hooks": [ { "command": "./scripts/lint.sh" } ] }
    ]
  }
}
```

- `PreToolUse` / `PostToolUse`：带 `matcher`，handler 嵌在 `hooks: [...]` 里；
- `SessionStart` / `PreInvocation` / `PostInvocation` / `Stop`：**扁平 handler 数组**，
  不能包 `hooks` 层；
- 配置文件位置：工作区 `.agents/hooks.json`、全局 `~/.gemini/config/hooks.json`、
  插件包内 `hooks.json`；
- stdin 是 camelCase JSON，公共字段 `conversationId` / `workspacePaths` /
  `transcriptPath` / `artifactDirectoryPath` / `modelName` / `lastUserInput`，
  `Stop` 另带 `executionNum` / `terminationReason` / `error?` / `fullyIdle`；
- **`Stop` 的 stdout 绝不能含 `decision: "continue"`**——会让 Agent 无限循环；
  hook 必须 fail-open，失败也 `exit 0`。
