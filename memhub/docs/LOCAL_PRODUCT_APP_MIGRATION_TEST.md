# MemHub App 数据迁移产品验收手册（本地）

适用范围：从旧版 MemHub App 升级到 SDK 数据面版本，以及 SDK 数据面版本的全新安装。

默认数据目录：`~/.one-memory`。以下操作只能在测试账号或测试机器执行，禁止拿真实用户唯一一份数据做破坏性测试。

## 一、验收结论怎么判

旧用户升级通过，必须同时满足：

1. 启动时显示 loading，迁移完成前不能进入可操作的主页。
2. 旧版中的有效记忆、原始对话、用户画像和配置在新版中仍可读取。
3. 修改过的记忆只显示最终内容；旧内容不会作为另一个有效记忆出现。
4. 用户已删除的记忆不会恢复。
5. 同一份旧数据重复启动、迁移中断后重启，不产生重复记忆。
6. 数据权威已切换到 SDK，旧的 Evidence/Governance/Matter 等表已从在线数据库物理删除。
7. 迁移前备份 bundle 存在；失败时不得出现“部分新数据 + 部分旧数据”的可用主页。

全新安装通过，必须同时满足：

1. 首次进入时记忆列表为空，用户画像为空或显示“尚未形成画像”。
2. 数据库从一开始就是 SDK authority。
3. 不创建 `evidence*`、`governed_*`、`matter*`、旧 retrieval、旧 session-shadow、`memory_tasks` 等废弃表。
4. 全新安装不应生成“旧用户迁移”备份 bundle。
5. 新写入一条测试记忆后，UI、CLI 和搜索都能读到，重启 App 后仍存在。

表的总数不是验收标准。部分管理面表会在对应功能首次使用时按需创建；应检查必需状态和禁止出现的旧表，而不是只核对一个固定数字。

## 二、场景 A：旧用户升级

### A1. 准备旧版样本

1. 在测试账号安装当前线上旧版 App，并完成模型配置。
2. 用真实产品入口产生一组容易核对的数据。建议至少包含：
   - 有效记忆 A：`升级验收-姓名是林澄-20261022`
   - 有效记忆 B：`升级验收-偏好无糖咖啡-20261022`
   - 记忆 C：先写“住在上海”，再修改为 `升级验收-住在深圳-20261022`
   - 记忆 D：写入 `升级验收-稍后删除-20261022`，然后在旧版中删除
   - 至少一段包含 user/assistant 角色的真实对话，供 raw 数据迁移验证
3. 在旧版 UI 截图或导出以下基线：
   - 有效记忆列表及总数
   - A、B、C 的完整正文
   - D 已经不存在
   - 已连接来源、模型配置和用户画像（如果已有）
4. 正常退出 App 后执行：

```bash
memhub stop
cp -a "$HOME/.one-memory" "$HOME/.one-memory.before-sdk-migration"
```

注意：关闭 App 窗口不等于后台 Runtime 已停止。必须确认 `memhub stop` 成功后再复制目录。

### A2. 安装并首次启动新版

1. 安装待验收的新 App，保留原来的 `~/.one-memory`，不要点清除数据。
2. 启动 App。
3. 观察启动 loading：
   - 迁移期间不应提前出现可操作主页。
   - 成功后自动进入主页。
   - 如果提示迁移失败或配置不足，记录错误码；此时旧数据必须仍完整，且不能进入一个可写的半迁移状态。
4. 不要手工运行 `sdk-migrate`、`sdk-cutover` 或 `sdk-cleanup`。产品验收的目标就是确认 App 启动流程能自动完成迁移。

### A3. 产品界面验证

进入主页后逐项检查：

- 记忆 A、B 均存在且正文完全一致。
- C 只保留“住在深圳”的最终内容；“住在上海”不能作为有效记忆出现。
- D 不存在，搜索 D 的唯一文本也不能命中。
- 原始对话的角色、顺序、来源和时间没有串行或丢失。
- 用户画像仍可读取；没有画像的旧用户应明确显示为空，不能报接口不存在。
- 来源授权、模型配置仍在。
- 用 A、B 的关键词搜索都能命中对应记忆。
- 重启 App 后再次检查，数量和内容不变。

新版 CLI 可作为第二条产品验证通道：

```bash
memhub status --json
memhub memories list --limit 500 --json > /tmp/memhub-memories-after.json
memhub profile --json > /tmp/memhub-profile-after.json
```

若 CLI 与 UI 结果不一致，验收失败；保留两个输出并导出 App 日志。

### A4. 工程辅助检查

以下检查由研发或熟悉终端的测试人员执行。

1. 数据权威必须为 `sdk`：

```bash
sqlite3 "$HOME/.one-memory/one-memory.sqlite3" \
  "SELECT authority, migration_id FROM memory_authority_state WHERE singleton=1;"
```

预期：第一列为 `sdk`。旧用户迁移的 `migration_id` 应非空。

2. 在线数据库中不能残留废弃表：

```bash
sqlite3 "$HOME/.one-memory/one-memory.sqlite3" "
SELECT name
FROM sqlite_master
WHERE type='table' AND (
  name GLOB 'evidence*'
  OR name GLOB 'governed_*'
  OR name GLOB 'matter*'
  OR name GLOB 'retrieval_*'
  OR name GLOB 'session_inbox_*'
  OR name IN (
    'session_snapshots',
    'session_subscriptions',
    'tail_handoff',
    'change_outbox',
    'memory_tasks',
    'memory_task_inputs',
    'memory_task_outputs',
    'memory_task_contexts',
    'memory_task_extractions',
    'memory_task_sdk_receipts',
    'memory_deletions',
    'memory_index_syncs',
    'extraction_settings'
  )
)
ORDER BY name;"
```

预期：无输出。`memory_write_jobs` 是新版仍保留的唯一写入队列，不应误判为旧表。

3. 迁移前备份 bundle 必须存在：

```bash
ls "$HOME/.one-memory/backups/"sdk-authority-v1-*.sdk-bundle/manifest.json
```

预期：至少一个 `manifest.json`，并且 bundle 内包含管理数据库和 SDK 数据快照。不要删除该 bundle。

4. 查看本次启动的迁移结果：

```bash
rg '"event":"sdk_migration.startup"' \
  "$HOME/.one-memory/logs/diagnostic-desktop.jsonl"
```

预期：最新一条结果为 `outcome: "succeeded"`、`state: "cutover"`。如果是 `blocked`，记录 `error_code` 并判定本轮未通过。

### A5. 中断恢复专项

使用 `~/.one-memory.before-sdk-migration` 恢复测试环境后，再做一轮：

1. 启动新版，在迁移 loading 期间强制退出 App。
2. 再次启动同一新版 App。
3. 迁移应继续完成，而不是从头重复写入。
4. 完成后重复 A3、A4。
5. 重点确认 A、B、C 各只有一份，且 bundle、记忆、raw 均没有重复。

此场景只在可随时恢复的数据副本上执行。

## 三、场景 B：全新安装

### B1. 准备真正的空环境

卸载 App 不会删除用户数据，因此“重新安装 App”不等于全新安装。请在测试账号执行：

```bash
memhub stop
mv "$HOME/.one-memory" "$HOME/.one-memory.saved-$(date +%Y%m%d-%H%M%S)"
```

确认 `~/.one-memory` 已不存在，再安装并启动新 App。

### B2. 首次启动产品验证

首次进入后检查：

- 正常进入 onboarding 或空主页。
- 记忆列表为 0。
- 用户画像为空或明确显示尚未形成。
- 没有旧来源授权、旧模型密钥、旧任务或旧历史。
- 启动过程不应声称“已迁移旧数据”。

然后配置测试模型并通过新版入口写入一条唯一记忆：

`全新安装验收-喜欢乌龙茶-20261022`

确认：

- 写入任务最终成功。
- 记忆列表和搜索都能读到它。
- `memhub profile --json` 可调用；画像是否立即形成取决于 SDK 提取结果，但接口不能缺失。
- 重启 App 后该记忆仍存在且不重复。

### B3. 工程辅助检查

1. 初始 authority：

```bash
sqlite3 "$HOME/.one-memory/one-memory.sqlite3" \
  "SELECT authority, migration_id FROM memory_authority_state WHERE singleton=1;"
```

预期：`sdk|`，即 authority 为 `sdk`，`migration_id` 为空。

2. 运行 A4 中的“废弃表检查”SQL。

预期：无输出。

3. 检查没有旧用户迁移 bundle：

```bash
ls "$HOME/.one-memory/backups/"sdk-authority-v1-*.sdk-bundle/manifest.json
```

预期：找不到匹配文件。全新安装不需要制作旧数据迁移备份。

4. CLI 空态：

```bash
memhub memories list --limit 100 --json
memhub profile --json
```

写入测试记忆前，memories 的 `total` 应为 `0`；profile 应返回合法空态，而不是 404、422 或 500。

## 四、失败时提交什么

发现问题时，请附：

1. 测试类型：旧用户升级 / 中断恢复 / 全新安装。
2. 旧版版本、旧版 build、新版版本、新版 build、macOS 版本和 CPU 架构。
3. loading 阶段的截图或录屏，以及是否提前进入主页。
4. `memhub status --json` 输出。
5. `memhub memories list --limit 500 --json` 输出；如含隐私内容，先脱敏正文，但保留 ID、状态和数量。
6. App 内“导出日志”生成的压缩包。
7. `memory_authority_state` 查询结果和废弃表检查结果。
8. 是否存在 migration bundle；不要上传真实用户的 bundle。

不要为了继续测试而手工删表、修改 authority 或删除 bundle。保留失败现场，复制整个测试数据目录后再恢复下一轮。
