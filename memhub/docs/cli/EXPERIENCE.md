# CLI 体验流程（用户可以照着走一遍）

本文件只写**真机验证过**的步骤。凡标注「拒绝」的，是当前刻意未接线、会明确报错而非假装成功。

## 0. 准备

发行目录（九项齐全）：`dist-runtime6`（用 `node scripts/build_runtime_distribution.cjs dist-runtime6 darwin arm64 0.0.0 产出）。

体验时建议用**隔离数据目录**，不动你日常的 `~/.one-memory`：

```bash
export ONE_MEMORY_DATA_DIR=/tmp/my_try
```

CLI 可用两种方式：

```bash
# 方式 A：用仓库里的构建产物
node apps/cli/dist/one-memory.cjs <命令>

# 方式 B：npm tarball 安装（已在隔离目录验证）
npm install /path/to/one-memory-cli-0.0.0.tgz
npx one-memory <命令>
```

## 1. 启动与体检

```bash
node apps/cli/dist/one-memory.cjs status
node apps/cli/dist/one-memory.cjs doctor
```

`status` 显示 Runtime 是否运行；`doctor` 按诊断口径做只读检查（默认不修复）。

## 2. 首次接入（init / sources connect）—— 没有独立的「授权」步骤

```bash
# 在终端里：交互向导（选来源 → 是否开启处理 → 配置模型）
# 非终端或加 --json：只列出可接入的来源和下一步命令
node apps/cli/dist/one-memory.cjs init

# 只授权（处理保持冻结：不导入历史、不扫描新会话）
node apps/cli/dist/one-memory.cjs sources connect codex

# 接入并同时导入历史、开启持续同步；输出 request id
node apps/cli/dist/one-memory.cjs sources connect codex --import

# 一次接入所有已检测到的来源
node apps/cli/dist/one-memory.cjs sources connect --all-detected --import
```

**接入即授权**：没有单独的 authorize 步骤，也没有计划文件。目录来自环境变量覆盖
（如 `DSH_HOME`）的来源必须加 `--confirm-overrides`，否则被拒绝（或在
`--all-detected` 时跳过并列出）。用同一个 `--request-id <uuid>` 重试不会重复导入。

### 接入 Hermes 的另一个 profile（多用户）

一个 Runtime 可以给多个用户记忆。**用户由读的是哪个目录决定**，不看会话或平台账号：
`~/.hermes/state.db` 属于默认用户 `local`，`~/.hermes/profiles/<name>/state.db` 是另一个用户。

```bash
# 把 wife 这个 Hermes profile 接成用户 wife：授权 + 导入历史，一步完成
node apps/cli/dist/one-memory.cjs sources connect hermes --user-id wife --root ~/.hermes/profiles/wife
# 也可以显式加 --import，效果相同；可带 --request-id <uuid> 幂等重试
node apps/cli/dist/one-memory.cjs sources connect hermes --user-id wife --root ~/.hermes/profiles/wife --import --json

# 列出来源；其他用户的连接单独列在「其他用户的连接」下，--json 里在 grants 字段
node apps/cli/dist/one-memory.cjs sources list
```

- 只有 `hermes` 能带 `--user-id`（非 `local`）或 `--root`；其他来源带了会退 2，什么都不发。
- 用户 ID 不必等于 profile 名：profile 名里有 `:` 等字符时另起一个，例如
  `--user-id wife-work --root ~/.hermes/profiles/wife:work`。
- `--root` 里的 `~` 由 CLI 展开为主目录，相对路径按当前目录解析成绝对路径。
- 扫描时任何层级名为 `profiles` 的目录都会被排除，所以 `local` 不会读到 wife 的 `state.db`，
  即使授权的根在 `~/.hermes` 之上。
- `--root` 不能是 `profiles` 目录本身（`hermes_root_is_profiles_container`），也不能与另一个
  用户已接入的 Hermes 目录相同、包含它或在它之内（`hermes_root_overlaps_other_user`，解析
  symlink 后比较）；`~/.hermes` 给 `local`、`~/.hermes/profiles/<name>` 给另一个用户是允许的。
- App 不变：它只读写 `local`，其他来源也都固定是 `local`。

## 3. 看导入进度

```bash
node apps/cli/dist/one-memory.cjs sources receipt <request-id>   # 接入回执
node apps/cli/dist/one-memory.cjs progress                        # 导入与生成进度
```

真机观察：授权 codex 后任务运行中并持续落库（stored 从 14,437 涨到 62,634）。

## 4. 立即扫描一个来源

```bash
node apps/cli/dist/one-memory.cjs scan --source codex   # 直接执行，返回导入任务
```

来源必须已接入并开启持续同步，否则返回 `live_sync_unavailable`（exit 1）。

## 5. 登记扫描目录（scan-roots，旧名 path 仍可用）

```bash
mkdir -p /tmp/scanme
node apps/cli/dist/one-memory.cjs scan-roots list
node apps/cli/dist/one-memory.cjs scan-roots add --root /tmp/scanme --source codex
node apps/cli/dist/one-memory.cjs scan-roots list          # 显示刚登记的，revision 0→1
node apps/cli/dist/one-memory.cjs scan-roots remove --root /tmp/scanme
```

行为已验证：重复登记返回 **409**；删除不存在的返回 **404**（都是 exit 1，带 code）。

## 6. 看宿主适配器（adapter）

```bash
node apps/cli/dist/one-memory.cjs adapter list
node apps/cli/dist/one-memory.cjs adapter probe --source codex
```

输出每个 harness 的 available / authorized / live_sync / recall 能力。

## 7. 添加记忆与写入对话

```bash
# 新增一条结论式记忆（与 App 的「添加记忆」相同）
node apps/cli/dist/one-memory.cjs memories add "我更喜欢用 pnpm" --json
node apps/cli/dist/one-memory.cjs memories add-status <request_id> --json

# 提交一段 OpenAI messages 对话，异步加工成记忆
node apps/cli/dist/one-memory.cjs write submit --session <会话ID> --messages-file <文件> --json
node apps/cli/dist/one-memory.cjs write status <request_id> --json
```

两条都返回 request id；它是幂等键，结果未知时用**同一个** `--request-id` 重试不会重复写入。

### 按用户读写（`--user-id`）

记忆命令都接受全局 `--user-id <id>`（默认 `local`，即 App 看到的那份）：
`memories list/show/add/add-status/update/delete`、`recall`、`profile`、`write submit/status`。

```bash
node apps/cli/dist/one-memory.cjs memories list --user-id wife --json
node apps/cli/dist/one-memory.cjs memories add "我更喜欢喝绿茶" --user-id wife
node apps/cli/dist/one-memory.cjs recall "喝什么茶" --user-id wife
node apps/cli/dist/one-memory.cjs profile --user-id wife
node apps/cli/dist/one-memory.cjs write status <request_id> --user-id wife
```

- 用户 ID 只能是 1–64 位字母、数字、`.`、`_`、`-`；其他写法退 2，不连 Runtime。
- 不带 `--user-id`（或写 `--user-id local`）时请求里不出现 `user_id`，和以前完全一样。
- 用户之间互相隔离：读、改、删另一个用户的记忆 ID 返回「未找到」（HTTP 404 `memory_not_found`，
  与不存在的 ID 完全一样）；write 的 request id 也只属于提交它的用户。
- `memories delete <id> --yes` 遇到「未找到」时按已删除处理：提示「未找到记忆 <id>（可能已删除）」，
  退出 0；`--json` 为 `state: "committed"` 加 `already_gone: true`。App 里重复点删除也不再报错。
- 读接口（`memories list`、`profile`、`recall` 等背后的 HTTP 响应）都带 `user_id`，即实际读的是哪个用户。

### 画像版本（`profile --versions`）

```bash
node apps/cli/dist/one-memory.cjs profile --versions            # L0 画像版本链，最新在前，* 为当前版本
node apps/cli/dist/one-memory.cjs profile --versions --user-id wife --json
```

用于找出覆盖了正确值的错误版本（例如 `用户` / `The user`），再用
`memories delete <memory-id> --yes` 删除；对应 `GET /api/v2/user-profile/versions`。

### 记忆模式与进化（`settings mode` / `evolve`）

```bash
node apps/cli/dist/one-memory.cjs settings mode                 # 当前模式
node apps/cli/dist/one-memory.cjs settings mode ultra           # 只显示切换计划，不改动
node apps/cli/dist/one-memory.cjs settings mode ultra --yes     # 切换
node apps/cli/dist/one-memory.cjs evolve --user-id wife --wait  # 立即进化（仅 ultra）
```

- 切换不迁移数据：已处理的对话不会重做，排队中的和新的按新模式处理；正在写的一批按旧模式写完。
- 任何模式都保留全部原始对话。
- `status --json` 与 `progress --json` 输出 `pipeline.mode`、每晚进化时间和每个用户的上次进化。
- 契约见 `docs/contracts/memory-mode-evolve-v1.md`。

### SDK 可选功能（`settings features`）

```bash
node apps/cli/dist/one-memory.cjs settings features                                   # 列出全部功能、默认值、当前模式下是否生效
node apps/cli/dist/one-memory.cjs settings features set episode_recall=true session_episodes=true        # 只显示计划
node apps/cli/dist/one-memory.cjs settings features set episode_recall=true session_episodes=true --yes  # 生效
node apps/cli/dist/one-memory.cjs settings features reset episode_recall               # 恢复默认
node apps/cli/dist/one-memory.cjs settings features reset                              # 全部恢复默认
```

- 全局设置，不分用户；没设置的功能保持 SDK 默认值，什么都不设时与以前完全一样（App 不变）。
- 可设：`graph_rerank`（图谱重排，仅 ultra）、`same_series_timeline`（需 graph_rerank）、`episode_recall`（片段召回，pro 即可）、`session_episodes`（片段写入）、`episode_summary`（片段摘要，每个片段一次模型调用）、`intention`（默认开）、`intention_dedup`（意图判重，默认开；关闭后检索和写入都不判重、不删除意图）、`few_shot`；以及数值 `intention_dedup_threshold`（意图判重的余弦阈值，默认 0.95，可设 0.8–1.0，例如 `settings features set intention_dedup_threshold=0.9 --yes`）。
- 片段召回只对带片段的记忆有用：同时开 `session_episodes`，之后提取的记忆才按会话归入片段。
- 在当前模式下不生效的功能照样保存，并注明原因（如 `graph_rerank` 在 pro 下「需要 ultra」）。
- 修改会重建 SDK 客户端：正在写的一批按旧设置写完，之后的写入和检索用新设置；不迁移数据，不改集合名。
- `status --json` / `progress --json` 的 `pipeline.features` 列出与默认值不同的功能。
- 契约见 `docs/contracts/memory-features-v1.md`。

### 界面语言与 SDK 提示语言（`settings language`）

```bash
node apps/cli/dist/one-memory.cjs settings language          # 界面语言和 SDK 提示语言
node apps/cli/dist/one-memory.cjs settings language ja       # 设为日语，立即生效
node apps/cli/dist/one-memory.cjs settings language system   # 清除设置（不指定 SDK 语言）
```

- 可选 11 种：`en`、`zh-Hans`、`zh-Hant`、`ja`、`es`、`de`、`pt`、`fr`、`ko`、`ru`、`id`，以及 `system`；旧代码 `zh-CN` 按 `zh-Hans` 处理。
- App 切换语言时会自动设置；Desktop 每次启动也会同步一次界面实际显示的语言（App 选“跟随系统”时，发送它按系统语言解析出的语言，例如 `zh-Hans`）。全局设置，存于 `config.json` 的 `ui_language`；从未设置时不写入。
- 所有语言（包括 `zh-Hans` 和 `en`）都作为 `MemoryConfig.language` 传给 SDK，提取等提示使用该语言；只有未设置（`system`，或只用 CLI/API、从未打开 App 的安装）时不向 SDK 传语言，SDK 照旧自动识别中文或英文。
- 修改会像功能开关一样重建 SDK 客户端；不迁移数据，不改集合名（需要 hy-memory 2.0.11 的 `MemoryConfig.language`）。
- `status --json` 的 `pipeline.ui_language` / `pipeline.sdk_language`（`auto` 或语言代码；`zh-Hans` 用户显示 `zh-Hans`）。API：`GET`/`PUT /api/v1/settings/ui-language`。

## 8. 召回（recall）

`recall` 两条路径（raw / memory）此前已真机验证。文本输出会标明每条是「原始对话摘录」
还是「记忆」。

## 9. 备份

```bash
node apps/cli/dist/one-memory.cjs backup create --target <数据目录>/backups/<文件名> --json
node apps/cli/dist/one-memory.cjs backup restore --source <数据目录>/backups/<文件名>        # 预览
node apps/cli/dist/one-memory.cjs backup restore --source <数据目录>/backups/<文件名> --yes --confirm <数据目录>/backups/<文件名>  # 执行
```

备份只包含主库（来源、写入队列、配置），**不包含记忆本身**（`hy-memory/`）。目标必须在
数据目录内（否则拒绝），放在 `backups/` 下才能被 restore 使用；restore 需 Runtime 已停止。

## 10. 宿主接入（host）

```bash
node apps/cli/dist/one-memory.cjs host list
node apps/cli/dist/one-memory.cjs host status --host codex
node apps/cli/dist/one-memory.cjs host install --host codex          # 预览
node apps/cli/dist/one-memory.cjs host install --host codex --approve <fingerprint>   # 执行
node apps/cli/dist/one-memory.cjs host uninstall --host codex --approve <fingerprint>
```

两阶段确认：不加 `--approve` 只预览。

## 11. 持续同步控制

```bash
node apps/cli/dist/one-memory.cjs sync status
node apps/cli/dist/one-memory.cjs sync pause --source codex
node apps/cli/dist/one-memory.cjs sync resume --source codex
```

## 确认约定

只有不可逆操作需要 `--yes`：`memories delete`、`models remove`、`delete-data`、
`backup restore`、`graph-width --decision rebuild_with_new`。不加时只说明将要做什么，不做任何
改动（exit 0）。`delete-data` 和 `backup restore` 还要用 `--confirm <路径>` 再写一遍目标（数据目录 /
备份文件），只加 `--yes` 会被拒绝。install / upgrade / uninstall / service-install / service-uninstall 不加 `--yes`
时只出计划。其它写命令直接执行。

## 12. 卸载（保留数据）

```bash
node apps/cli/dist/one-memory.cjs uninstall --install-root <root> --home <home> --yes
```

已验证：删除 current 链接、版本目录、CLI 链接；**数据目录原样保留**。

## 已知会明确拒绝的（不是 bug）

- `service-install/service-uninstall --yes`（在隔离环境）：需真实用户域（launchctl），失败时会正确回滚、不留半成品。
- 某些命令不接受 `--json`（如 `start`、`install`、`uninstall`）——契约里该命令没有这个选项，带了会退 2。

## 与桌面 App 的关系

- 只读/控制类命令在 CLI-only 场景已验证：两个独立 CLI 进程连同一 daemon（同一 instance_id）、读同一份数据；`start` 不会重复启动。
- 涉及 App 的那半（App 与 CLI 共用同一 daemon 的真机验收）依赖桌面 App 运行，App 当前未运行，未验证。
- 注意：`~/.one-memory/runtime.json` 存在过期 receipt（记录的 pid/port 已不存在），会让 CLI 显示 stopped——receipt 维护在 App 侧，本轮未改。
