# ZCode 一键插入支持

更新日期：2026-10-31

## 结论

ZCode 已开放一键插入。它安装 **一个 ZCode 插件**（内含 MCP Server 声明、Skill、recall 注入与 capture 钩子）+ **Credential Grant**，对用户 `cli/config.json` 的唯一写入是插件自身的启用键——**不触碰 `hooks.enabled` 全局开关**。

## 接入方式和产品状态

| 项目 | ZCode 当前实现 |
|---|---|
| 一键插入产品状态 | 开放 |
| ZCode 查询 MemHub | 插件内联声明的 MCP Server（`mcpServers`，会话启动自动连接） |
| 告诉模型何时查询 | 插件携带的 Skill |
| Prompt 前自动 Prefetch | 有——插件钩子（UserPromptSubmit 与 SessionStart）跑 recall 注入入口，`additionalContext` 注入对话（运行时未分发 recall 命令时缺省不装） |
| ZCode 新会话实时送入 MemHub | 注入入口顺手移交 prompt 侧采集 + Stop 钩子采集助手侧 |
| 历史/增量导入 | 有——rollout 解析器读 `cli/rollout/model-io-*.jsonl`：每次模型调用一行，取"收尾于该 prompt 的请求"作用户轮、带正文的响应用助手轮；压缩记录与工具循环中间调用按名跳过；消息 ID 键于 `requestId`，压缩重写文件后的重扫不重复入库 |
| Plugin | **是——整个安装就是一个插件**（`.zcode-plugin` 形态） |

## 为什么是插件（而不是配置文件合并）

ZCode 的配置文件钩子必须 `hooks.enabled: true` 才运行，而这个开关管着用户**所有**配置文件钩子——翻开它会让用户自己休眠的钩子一起开始运行。插件钩子不经过这个开关（实测确认：插件存在也不会唤醒用户休眠的配置钩子），因此整套安装生来就是插件形态，该全局副作用不存在。（WorkBuddy 的插件形态携带 `supersedes` 迁移其已发布的配置文件旧形态；ZCode 从未发布过旧形态，无需迁移。）

## 两个开关分别做什么

### 打开"一键插入应用"

安装插件目录树（manifest + 钩子 + Skill）到 `cli/plugins/cache/one-memory/one-memory-recall/<version>/`，向 `installed_plugins.json` 追加注册行，向 `cli/config.json` 的 `plugins.enabledPlugins` 写入本插件唯一的启用键（无此键插件默认禁用——"默认启用"只对市场安装成立），并创建 Credential 与授权。

### 关闭"一键插入应用"

先撤权，再删除插件目录、剥除注册行与启用键、移除 Journal。用户 config 的 `hooks` 段与 MCP Server 全程不动。

### 打开"同步开关"

同步开关兼作 capture 写入闸门；关闭时实时采集不入库。一键插入（Recall 方向）不受影响。

### 组合效果

一键插入控制 ZCode 能不能 Recall 与实时送入；钩子失败一律静默（`exit 0`、无 stdout），不影响会话。

## 实际安装

```text
~/.zcode/cli/plugins/cache/one-memory/one-memory-recall/<version>/
  ├── .zcode-plugin/plugin.json      # name/version/skills/hooks/mcpServers（one-memory 内联）
  ├── hooks/hooks.json               # SessionStart + UserPromptSubmit（注入）+ Stop（capture）
  └── skills/memhub-recall/SKILL.md

~/.zcode/cli/plugins/installed_plugins.json   # 追加一行 our record（id=one-memory-recall@one-memory）

~/.zcode/cli/config.json                      # plugins.enabledPlugins 加一个键，其余不动

~/.zcode/.one-memory/recall-plugin-install.json
```

安装器保留 config 里用户自己的 MCP Server、钩子与插件状态；marketplace 无需注册（cache 副本 + 注册行 + 启用键即可被宿主完整加载，已实测）。`installed_plugins.json` 里已有同 id 指向别处的记录时拒绝安装。

## ZCode 特有约束

- MCP server schema 严格：**含未知键的 server 会在加载时被静默丢弃**。插件 manifest 内联的条目只写 `{command, args, env}` 三键，测试对此有专门断言。
- 插件钩子文件为 `{"hooks": {"<Event>": [{"hooks": [...]}]}}`（事件直接挂在 `hooks` 下，比配置文件形态浅一层），`timeoutMs` 毫秒；一台 3.16.2 客户端上三事件全部实测触发。
- manifest 必须显式声明 `"skills"` 字段，否则宿主加载钩子与 MCP 但不列出 Skill（实测）。
- 命令与路径须为绝对路径（插件 MCP 不展开 `${...}` 模板变量）。

## 权限、重复打开与关闭

- 默认 Tools（授权项，非 MCP 工具名）：`om_recall`、`om_conversations`、`om_diagnostic_trace`。
- 默认 Scopes：`all-apps`、`source:zcode`。
- 已安装且 `approved`：修复命令并 Probe；应用升级后的首次启动还会刷新仍是我们的 Skill、钩子和插件文件（用户改过的文件留待详情页确认）。
- 权限不完整：重新安装、签发新 Credential 并授权（本构建版本固定 1.0.0，重装落同一版本目录，整棵 `cache/one-memory/` 随卸载回收）。
- 关闭：先撤权，再删除插件树、剥除注册行与启用键、移除 Journal。
- 用户修改过插件文件（如编辑过的 Skill）时不强删。

## 生效与重启

插件安装后建议重启 ZCode（或新开会话），在 **Settings → Plugin Management** 确认 `one-memory-recall` 已启用，**Settings → MCP** 确认 `one-memory` 已连接。
