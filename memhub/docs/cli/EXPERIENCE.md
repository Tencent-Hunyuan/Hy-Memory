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
