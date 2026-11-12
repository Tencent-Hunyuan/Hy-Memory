# DeepSeek Harness 一键插入支持

更新日期：2026-11-02

## 结论

DeepSeek Harness 按 Profile 配置 MCP：每个 Profile 是一个独立 Host（`deepseek-harness:<profile>`），有自己的 `cordis.patch.yml`、Credential 与 Grant。应用详情页只有一个“一键插入应用”开关，它一次覆盖桌面检测到的全部 Profile：打开时逐个安装并授权，关闭时逐个移除并撤销。

## 接入方式和产品状态

| 项目 | DeepSeek Harness 当前实现 |
|---|---|
| 应用详情页一键插入 | 可用；一个开关覆盖全部已检测 Profile，并列出每个 Profile 的状态 |
| 首次导入批量连接 | 排除；在应用详情页打开开关 |
| Harness 查询 MemHub | 通过 Profile 级 Cordis MCP Client |
| Skill | 没有 |
| Prompt 前自动 Prefetch | 没有 |
| Capture Hook/Plugin | 没有 |
| Harness 新会话进入 MemHub | Core 扫描 `$DSH_HOME/sessions`，按 `session[.vN].jsonl[.zstd]` 识别会话文件 |

会话文件按写入时的格式版本命名：首版为 `session.jsonl` / `session.jsonl.zstd`，v3/v4 起为 `session.v3.jsonl.zstd` / `session.v4.jsonl.zstd`。来源扫描接受带版本号的名称；解析前再读文件头 `version` 字段，只导入已验证的 v0/v3/v4，未来未知版本不会被解析：整个文件以 `unsupported_schema` 报告，且不留下读取进度，将来支持该版本的发布会从头读取（错误导入比暂时漏导更难回滚）。`tool/result` 的输出无论写成 `tool-result` 块还是纯 `text` 块，都按同一条裁剪与凭据清洗路径导入。

## 两个开关分别做什么

### 打开“一键插入应用”

应用详情页对每个已检测、尚未获批的 Profile：

1. 预览并写入该 Profile 的 `cordis.patch.yml` 受管块，创建独立 Credential；
2. 授予 `om_recall`、`om_conversations`、`om_diagnostic_trace`，范围为 `all-apps` 与 `source:deepseek-harness`（与其他应用的一键插入相同）。

已获批的 Profile 不会重装。某个 Profile 失败时，其他 Profile 的结果保留，页面提示失败的 Profile 名称；开关只要有一个 Profile 获批就显示为开。Host ID 里的冒号只作为标签存在，权限层的安装目录按 Host ID 摘要生成，不参与路径拼接。

### 关闭“一键插入应用”

对每个已接入的 Profile 先撤销 Credential，再删除受管块（与其他应用的关闭语义相同）。受管块被改过、无法删除时仍会撤销授权，页面列出需要核对的 Profile；撤销也失败的 Profile 会单独列出，它仍可使用记忆。从未接入的 Profile 不受影响。

### 之后新建的 Profile

桌面每次刷新应用接入状态时都会重新枚举 Profile（`$DSH_HOME/profiles/<profile>/cordis.yml`），应用首页打开时每 3 秒刷新一次；常驻的 Runtime 收到未知的 Profile Host ID 时也会重新读盘。开关的选择会被记住：开关为开时，新出现的 Profile 由应用首页自动安装并授权，不需要重启 MemHub。

每个新 Profile 触发一次自动尝试；失败时应用首页会指明 Profile，不会每次轮询都重试，下次启动或点击重试时再试一次。开关为关时不会自动接入任何 Profile。

### 打开“同步开关”

同步开关针对整个 `deepseek-harness` 来源，而不是某个 Profile。Core 扫描 `$DSH_HOME/sessions`，补处理历史会话并持续轮询新 Session。

### 关闭“同步开关”

Core 停止扫描和 pending Job claim，running Job可结束，已有 Memory 保留。它不会修改任何 Profile 的 `cordis.patch.yml` 或 Credential。

### 组合语义

- 一键插入决定已检测 Profile 是否能查询 MemHub；
- 同步开关决定 MemHub 是否继续读取整个 DeepSeek Harness 的 Session；
- 没有自动 Prefetch，也没有 Capture Hook。

## Profile 粒度

默认 `DSH_HOME=~/.dsh`，每个 Profile 独立：

```text
$DSH_HOME/profiles/<profile>/cordis.yml
$DSH_HOME/profiles/<profile>/cordis.patch.yml
$DSH_HOME/.one-memory/recall-install-<profile>.json
```

桌面枚举出的 Host ID 为：

```text
deepseek-harness:web
deepseek-harness:headless
...
```

每个 Profile 拥有独立 Patch、Journal、Credential 和 Grant；Core 会话同步仍按统一的 `deepseek-harness` 来源扫描 `$DSH_HOME/sessions`。

## MCP Patch

安装器在 `cordis.patch.yml` 中维护受管文本块：

```yaml
# >>> one-memory recall — managed block, do not edit >>>
- insert:
    - id: one-memory-recall
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: onememory
        transport: stdio
        command: '/Applications/MemHub.app/Contents/MacOS/MemHub'
        args: ['/Applications/MemHub.app/Contents/Resources/mcp.cjs']
        env:
          ONE_MEMORY_HOST_CREDENTIAL_FILE: '.../credential'
# <<< one-memory recall — managed block <<<
```

实现见 [dsh-patch.ts](../../packages/host-bridge/src/dsh-patch.ts)。安装器不解析整份 YAML，因为 Cordis Patch 可能包含 `!!js` 等普通 YAML 库不能安全往返的内容。

DeepSeek Harness 不安装 Skill，也不安装 Capture Hook；新会话进入 MemHub 依赖 Core 扫描。

## UI 状态

- 应用详情页的“一键插入应用”下方列出每个已检测 Profile 及其“已接入 / 未接入”状态；没有检测到 Profile 时开关不可用，并提示创建 Profile 后回到此页。
- Host ID 只接受小写字母、数字和连字符（最长 32 个字符）。名称不符合的 Profile 仍会列出，但无法授权，页面会指明它和原因，不影响其他 Profile。
- 连接状态取各已接入 Profile 记录的自检结果：全部接通才显示“已接通”。
- 首次导入批量连接排除 DeepSeek Harness。
- CLI 的 `memhub host install deepseek-harness:<profile>` 只安装，不授权（状态停在 `install-only`）；授权入口只有应用详情页的开关，CLI 没有授权命令。

## 已修复：Profile Host ID 校验

此前 Profile 安装会在写入 `cordis.patch.yml` 和创建 Credential 之前失败，原因是：

```text
系统生成 hostId = deepseek-harness:<profile>
HostPermissions 只接受 [a-z][a-z0-9-]*
HostGrantStore 也不接受冒号
```

Host ID 是不可解释的标签：安装目录对它取摘要，不会把它拼进路径。现在权限层与 Grant 存储都接受一个可选的 `:<profile>` 段，因此 Profile 安装可以正常走完。这不是 DeepSeek Harness 不支持 MCP 的问题，而是 Profile Host ID 格式与权限校验不一致，现已对齐。

## 已修复：应用详情页返回 `unsupported_host`

此前应用详情页按“一个来源对应一个 Host”接入，而 DeepSeek Harness 没有不带 Profile 的 Host，开关因此置灰并返回 `unsupported_host`，安装后的 Profile 也只能停在 `install-only`（#230）。现在开关直接作用于全部 `deepseek-harness:<profile>` Host，复用与其他应用相同的安装、授权、撤销与卸载接口。

## 重复安装与卸载

底层 Patch 安装是幂等的：已有受管块时替换，不追加第二份。相同 Row ID 出现在 MemHub 哨兵块外时默认拒绝接管。

卸载先撤销该 Profile Credential，再删除受管块并保留其他 Patch；如果删完没有其他 Row，恢复 `[]`。用户修改过受管块时不强删。
