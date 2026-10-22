# DeepSeek Harness 一键插入支持

更新日期：2026-10-18

## 结论

DeepSeek Harness 的 Profile 级 MCP 安装器已经实现，Profile Scoped Host ID（`deepseek-harness:<profile>`）在权限层按不透明标签处理，可正常走完 Preview 与安装。

## 接入方式和产品状态

| 项目 | DeepSeek Harness 当前实现 |
|---|---|
| 应用详情页一键插入 | 有意排除，因为无法选择 Profile |
| 来源管理页 Profile 安装 | 可用；每个 Profile 独立 Patch、Credential 与 Grant |
| Harness 查询 MemHub | 通过 Profile 级 Cordis MCP Client |
| Skill | 没有 |
| Prompt 前自动 Prefetch | 没有 |
| Capture Hook/Plugin | 没有 |
| Harness 新会话进入 MemHub | Core 扫描 `$DSH_HOME/sessions` |

## 两个开关分别做什么

### “一键插入应用”

应用详情页不可用。来源管理页对每个 Profile 写 `cordis.patch.yml`、创建独立 Credential 并授权；Host ID 形如 `deepseek-harness:<profile>`，冒号只作为标签存在，权限层的安装目录按 Host ID 摘要生成，不参与路径拼接。

### 打开“同步开关”

同步开关针对整个 `deepseek-harness` 来源，而不是某个 Profile。Core 扫描 `$DSH_HOME/sessions`，补处理历史会话并持续轮询新 Session。

### 关闭“同步开关”

Core 停止扫描和 pending Job claim，running Job可结束，已有 Memory 保留。它不会修改任何 Profile 的 `cordis.patch.yml` 或 Credential。

### 修复一键安装后的组合语义

未来修复 Profile Host ID 后：

- 某个 Profile 的一键插入只决定该 Profile 是否能查询 MemHub；
- 全局同步开关决定 MemHub 是否继续读取整个 DeepSeek Harness 的 Session；
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

理论上每个 Profile 拥有独立 Patch、Journal、Credential 和 Grant；Core 会话同步仍按统一的 `deepseek-harness` 来源扫描 `$DSH_HOME/sessions`。

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

- 应用详情页排除 DeepSeek Harness，因为单一来源页无法选择 Profile。
- 首次导入批量连接也排除它。
- 来源管理页会列出每个 Profile，并显示独立安装按钮。

## 已修复：Profile Host ID 校验

此前来源管理页安装会在写入 `cordis.patch.yml` 和创建 Credential 之前失败，原因是：

```text
系统生成 hostId = deepseek-harness:<profile>
HostPermissions 只接受 [a-z][a-z0-9-]*
HostGrantStore 也不接受冒号
```

Host ID 是不可解释的标签：安装目录对它取摘要，不会把它拼进路径。现在权限层与 Grant 存储都接受一个可选的 `:<profile>` 段，因此 Profile 安装可以正常走完。这不是 DeepSeek Harness 不支持 MCP 的问题，而是 Profile Host ID 格式与权限校验不一致，现已对齐。

## 修复后的权限语义风险

来源管理页当前默认授权选择可能包含：

- `om_recall`、`om_ask`、`om_conversations`、`om_diagnostic_trace`；
- `all-apps`；
- 所有已授权 Source 和 Workspace。

这比应用详情页通用授权更宽。Host ID 修复后仍应确认是否希望每个 Profile 默认获得如此宽的权限。

## 重复安装与卸载

底层 Patch 安装是幂等的：已有受管块时替换，不追加第二份。相同 Row ID 出现在 MemHub 哨兵块外时默认拒绝接管。

正常卸载设计是先撤销该 Profile Credential，再删除受管块并保留其他 Patch；如果删完没有其他 Row，恢复 `[]`。用户修改过受管块时不强删。
