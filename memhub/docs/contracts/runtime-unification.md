# MemHub Runtime 统一架构与实施合同

更新：2026-10-12。关联 D02、D06、D07、D08、D26、D40、RT-D41、D56。

> 状态说明（2026-10-16）：本文保留历史 `One Memory` / `one-memory` 合同名称。
> 对外产品名是 MemHub，记忆 SDK/引擎是 Hy Memory。本文中的跨平台目标不是当前支持
> 矩阵；Windows 不支持，其他平台是否受支持以对应 GitHub Release manifest 和
> 原生平台验收回执为准。当前发行合同以
> [`../INSTALLATION.md`](../INSTALLATION.md) 为准。

## 目标

One Memory 对外提供两种主要安装入口：

- macOS 图形 App；
- 设计目标中的跨平台 `one-memory` 兼容 CLI；当前公开入口和平台范围以安装文档为准。

二者不是两套产品。无论从哪个入口安装，最终都必须使用同一个 MemHub
Runtime、同一份用户数据、同一套权限语义，以及由 Hy Memory SDK 负责的
memory 数据面。

产品不变量是“一次安装、一个 Runtime、一份数据、多种客户端”。

## 分层

### Kernel / Core

Python/FastAPI Core 是 MemHub 管理面，负责来源授权、模型配置、异步写入队列、
SDK bridge、检索/问答 API 与 Runtime 协作。Memory/raw/vector/graph/coding
数据面由 Hy Memory SDK 管理。Core 不负责安装桌面窗口、修改宿主配置或注册操作系统服务。

### Runtime

Runtime 是可常驻的完整本地服务，由以下部分组成：

- SQLite，作为 MemHub 管理面数据库，由 Core 独占写入；
- Core；
- Host Gateway，包括 Recall Broker、Hook 通知和宿主授权校验；
- Runtime discovery、本地认证、版本与健康状态；
- Web Console 静态资源；
- 日志、升级、回滚和服务生命周期控制。

Runtime 可以由图形 App 或 CLI 安装，但安装来源不得改变其数据合同和行为。

### CLI

`one-memory` 是 Runtime 的命令行安装器、控制器和客户端。最终公开命令至少覆盖
`install/start/stop/restart/status/doctor/ui/logs/upgrade/uninstall`，并复用同一套来源、
模型和宿主授权合同。内部 Kernel 入口后续明确为 `one-memory-core`；迁移期间无参数
`one-memory` 继续兼容直接启动 Core。

### Desktop

Electron 只提供图形安装、原生窗口、目录选择、系统展示偏好和 Runtime 客户端能力。
它不得拥有第二份数据库或第二套宿主授权语义。Desktop 启动时先发现并连接已有
Runtime；只有不存在可验证实例时才启动或安装 Runtime。关闭窗口默认不停止独立
Runtime。

### Web Console

React UI 保持一套。Electron 与浏览器分别通过受约束的 Control Client 使用同一
Runtime 合同。浏览器不得长期持有裸 Core token；正式独立 Web Console 采用一次性
本地登录换取 HttpOnly、SameSite=Strict 的 loopback session。

## 安装等效定义

“App 与 CLI 等效”表示：

1. 同平台、同架构、同版本消费同一个 Runtime artifact；
2. 使用同一 One Memory Home、数据库 schema 和迁移；
3. 使用同一 Core API、Host Gateway 和授权合同；
4. 同一数据目录最多一个写入 Runtime；
5. App 能连接 CLI 已启动的 Runtime，CLI 也能管理 App 安装的 Runtime；
6. 来源、模型、宿主三类授权仍然独立；
7. 升级、撤权、删除和回滚结果不因安装入口不同而变化。

等效不表示每个平台可用宿主完全相同。macOS、Linux、Windows 必须按真实支持矩阵
展示来源和宿主能力；fixture 或跨平台分支测试不能替代真机结论。

## One Memory Home

默认逻辑位置保持 `~/.one-memory`，可由 `ONE_MEMORY_DATA_DIR` 覆盖。它是数据实例，
不是某个 App 版本的私有目录。正式目录合同逐步收敛为：

```text
One Memory Home/
  data/                 # one-memory.sqlite3、HY 数据与派生索引
  migrations/           # SQLite schema 升级回执与备份索引
  runtime.json          # 无秘密的运行实例发现回执
  runtime-token         # 当前实例短生命周期本地凭据
  host-permissions/     # 宿主安装身份和授权
  integrations/         # Broker discovery
  logs/
```

MemHub SQLite 是管理面权威，保存任务调度、cursor、配置、权限、审计和必要的
`memory_id` 引用，不保存 memory 实体或原始对话正文。Memory、raw、向量、图和
coding stores 由 Hy Memory SDK 管理。Adapter 可只读宿主自身文件，写入目标是 SDK。
Runtime 负责数据目录单写、管理库 schema 升级、WAL、备份恢复、磁盘预算和卸载保留
数据。MemHub 管理库不并行维护 MySQL 双写；SDK 自身可选 backend 不受此限制。

`runtime.json` 不得包含 token。`runtime-token` 必须是 owner-only，实例退出时删除；
Windows 在正式支持前必须实现并真机验证用户 SID/DACL，不能靠忽略 POSIX mode 过关。

## Runtime discovery v1

当前第一批先把 Core 的 receipt 升级为可供可信本地客户端连接的最小合同。POSIX
平台同时发布 owner-only token；Windows 在 SID/DACL 实现和真机验证前不发布 attach
token，因此本批 Windows 仍只能使用启动方直接持有的凭据，不能声称 CLI/App attach：

```json
{
  "schema_version": 1,
  "service": "one-memory-core",
  "instance_id": "...",
  "pid": 1234,
  "parent_pid": null,
  "host": "127.0.0.1",
  "port": 8765,
  "started_at": "..."
}
```

连接方必须同时满足：receipt schema/service 正确、host 是精确 loopback、端口有效、
PID 仍存活、owner-only token 可读、health 的 service 与 instance ID 匹配。任一失败都
不得把任意 loopback 服务当作 One Memory。若进程仍活但身份无法验证，必须显式失败，
不能杀进程、接管数据目录或退回匿名访问。

## 第一批开发范围

真实链路：

```text
CLI/终端启动 Core
  -> Core 锁定 One Memory Home
  -> 写 runtime-token 和 runtime.json
  -> Desktop 读取并验证 receipt/token/health
  -> Desktop attach 同一 Core
  -> Desktop 退出
  -> 独立 Core 继续运行
```

本批改变：

- 记录统一 Runtime 分层和后续实施路线；
- Core 发布无秘密 discovery receipt 与 owner-only 短生命周期 token；
- Desktop 在 spawn 前尝试连接同一数据目录的可验证 Core；
- Desktop 只停止自己启动的 Core。

本批不改变：

- HY、Governance、Evidence、数据库 schema 和来源权限；
- Host Gateway 仍在 Electron 内的现状；
- 日常安装包、真实宿主配置和用户资料；
- Linux/systemd、macOS LaunchAgent、Windows 用户服务；
- Runtime 托管 Web Console 和浏览器 session；
- 发布、签名、自动更新和公开下载。

## 第一批失败测试与完成标准

1. Core receipt 不含 schema/service 或 token 文件权限不安全时测试失败。
2. Desktop 面对健康且实例匹配的独立 Core 时若仍 spawn，测试失败。
3. receipt 指向非 loopback、死 PID、缺 token 或 health 实例不匹配时不得 attach。
4. Desktop attach 后调用 stop 不得向独立 Core 发信号。
5. Desktop 自己启动的 Core 仍须在退出时优雅停止并保持原孤儿恢复行为。
6. Core runtime、Desktop supervisor 定向测试、类型和格式检查通过。

本批只能标记为 `implemented` / 自动合同 `verified`。没有实际 CLI 进程与打包 App 的
同库联测时不能标记 `accepted`，更不能称跨平台一键安装完成。

## 后续阶段

### R2：Runtime Supervisor

把 Recall Broker、HostPermissions、来源变化通知和 runtime discovery 从 Electron 移到
独立 Runtime Supervisor。App 退出后宿主召回和持续同步仍可用。

R2前两切片已经建立 `packages/runtime-control`：统一解析无秘密receipt、校验owner-only
token、验证独立进程和受保护identity，并承载Core启动、attach、孤儿恢复和停止语义。
Desktop只保留App资源路径与数据目录选择并消费该包；Host Gateway和宿主权限仍未迁出
Electron。

R2第三切片增加不依赖Electron的`RuntimeSupervisor`组合器，顺序启动或attach Core后再
启动sidecar，sidecar失败时释放本次Core所有权，停止时统一收口两者。Desktop已用它
组合现有Recall Broker。Broker进程目前仍和Electron同进程，故这一步是可迁移的生命周期
边界，不是后台常驻已完成。

R2第四切片已把HostPermissions、同步Hook资产校验与来源变化处理器迁入
`packages/runtime-control`，Desktop原路径只保留兼容re-export，以维持既有IPC测试和调用
边界。权限receipt、安装事务、确认指纹、撤权和Hook回滚语义不变；Windows仍因缺少
SID/DACL实现而明确拒绝宿主授权控制。

R2第五切片增加`RecallBrokerSupervisor`：若同一Runtime实例已有存活且health匹配的Broker，
客户端只attach且退出不停止它；缺失、stopped或死PID才创建新Broker；活着但属于其他
instance或无法验证时失败关闭，不能覆盖discovery或启动第二个Broker。Desktop已通过
`RuntimeSupervisor`组合该层。

R2第六切片增加内部`one-memory-runtime`前台入口和外层`runtime-service.json`/租约。该
进程取得One Memory Home单实例租约后，依次构造HostPermissions、启动Core与Broker并
发布外层身份；SIGINT/SIGTERM时清理Core receipt/token、外层receipt与租约，Broker留下
明确stopped回执。Desktop只在外层PID、Core parent PID、instance和port一致且两进程存活
时attach parent-owned Core；普通另一Desktop子进程仍不接管。该入口尚未注册系统服务。

R2第七切片把`packages/runtime-control/dist`加入Desktop正式资源清单，并让Desktop build
先构建Host Bridge与Runtime Control。安装包因此将同时携带同版本Core、Host Bridge、Web
和`runtime/runtime.cjs`；当前Desktop启动逻辑仍以内嵌模式运行，未在未定义安装/升级/
卸载事务前擅自注册或切换后台服务。

R2第八切片使打包Desktop默认执行共享launcher：先验证现有外层Runtime，缺失时以
Electron Node模式detached启动同包`runtime/runtime.cjs`，等待外层receipt、Core token和
identity全部成立后再进入原Desktop attach流程。退出Desktop只detach，不停止外部Runtime。
开发模式及显式`ONE_MEMORY_DESKTOP_RUNTIME_MODE=embedded`保留旧的进程内组合模式，作为
开发与故障回退；这仍不是登录自启或操作系统服务安装。

### R3：完整 CLI 与服务管理

实现统一命令，增加 macOS LaunchAgent、Linux systemd user service、无 systemd 的
前台模式和 Windows 当前用户登录启动方式。停止/卸载与删除数据必须分开确认。

R3第一切片已实现只读`service-plan`：根据明确的platform、home和install root生成macOS
LaunchAgent或Linux systemd user service的文件内容、目标路径和参数数组，不写文件也不
执行命令。macOS缺uid时blocked；Windows在SID/DACL和当前用户启动机制真机验证前始终
blocked。后续apply必须消费同一plan并进行原子写入、预览确认、失败回滚和当前状态复核。

R3第二切片已建立`apps/cli`，产出公开`one-memory`命令并直接复用Runtime Control的
serve/status/doctor/service-plan；Python包新增`one-memory-core`正式Kernel入口，旧Python
`one-memory`暂时兼容。发行时只应把Node CLI暴露为用户命令，避免两个实现争用命令名。

R3第三切片增加公开CLI的start/stop/restart。start只启动显式安装布局中的Runtime entry，
并等待外层receipt、Core token和identity全部验证；stop在相同验证完成后仅向外层Runtime
PID发送SIGTERM，并等待父进程及外层/Core receipt、token清理；身份不匹配时不发信号。
restart严格串行stop与start，避免旧Core尚在退出时抢同一数据目录。

R3第四切片把公开`cli/one-memory.cjs`加入Desktop的正式extraResources，并让Desktop build
先构建Host Bridge、Runtime Control和CLI。产物哈希清单必须同时覆盖CLI、Runtime、Core、
Host Bridge和Web；这保证Mac App携带的命令行入口与其后台Runtime来自同一源码构建，
但尚未自动把CLI链接到PATH。

R3第五切片增加CLI发行目录构建器：从已经构建的CLI、Node、Runtime、Core、Host Bridge
和Web资源组装固定目录，完整列出SHA-256 manifest，并使用stage目录后原子替换目标。
缺任一关键文件或存在越界符号链接时失败且保留旧发行目录。该构建器不下载artifact，
也不安装到用户目录。

R3第六切片增加共享的本地发行安装事务和公开CLI `install`。默认只输出经manifest、目标
平台/架构、完整文件集合和逐文件SHA-256核验后的plan；只有显式`--yes`才复制到
`versions/<version-platform-arch>`并原子切换`current`。版本/arch字符、源与安装根重叠、
越界链接、被调用方修改的plan、已有非托管current、同版本不同内容都会拒绝。现阶段不
下载artifact、不删除旧版本、不写PATH或服务文件。

R3第七切片增加`service-install`两阶段操作：默认只展示`planUserService`，显式`--yes`
后才要求当前平台一致、Runtime/Core/Host Bridge/Hook关键文件齐全、服务目录安全且目标
文件不存在，再以0600写入并按参数数组调用服务管理器。失败时只删除本次新建的文件，
不猜测并禁用可能早已存在的同名服务；潜在管理器残留由doctor后续报告。已有服务升级
与卸载另行实现。

R3第八切片增加`service-uninstall`两阶段操作：默认只预览，显式`--yes`后先逐字核对现有
服务文件与当前plan，完全一致才执行bootout/disable并删除；用户修改或其他工具创建的
同名文件一律拒绝。卸载服务不删除`versions/current`或One Memory Home，数据删除必须是
另一条明确操作。

R3第九切片将首次`install`组合成单一事务：同一预览同时返回发行目录和用户服务计划；
显式`--yes`后先安装版本并切换current，再首次注册服务。服务步骤失败时仅在current仍
指向本次版本的前提下恢复旧指针，并删除本次新建的版本目录。apply再次验证源manifest
与计划，拒绝调用方篡改、源/目标重叠、路径逃逸和非托管current。当前不支持覆盖已有
服务文件，也不自动清理旧版本或数据。

R3第十切片把受管CLI入口加入同一事务：POSIX默认`~/.local/bin/one-memory`指向
`<install-root>/current/bin/one-memory`，可用`--bin-dir`覆盖。普通文件、指向其他目标的
链接和不安全目录都拒绝覆盖；已正确链接时幂等。服务注册失败按逆序删除本次新建链接、
恢复旧current和本次版本。Windows PATH安装仍blocked。

R3第十一切片修正发行目录POSIX launcher：命令从PATH符号链接调用时逐层解析自身真实
位置，再执行同一发行目录`bin/node`与`bin/one-memory.cjs`。真实临时符号链接执行测试
必须产生bundled Node路径，不能只凭exit 0判断成功。

R3第十二切片使发行目录中的公开CLI可自定位安装源，并提供平台默认安装根：macOS为
`~/Library/Application Support/One Memory`，Linux为`~/.local/share/one-memory`，Windows
为`LOCALAPPDATA/One Memory`或用户目录回退。显式source/install-root/home仍可用于隔离和
企业部署；Windows apply继续受既有ACL门禁阻止。

R3第十三切片增加`upgrade`两阶段事务：要求现有CLI入口仍精确指向受管current、服务
文件与当前plan逐字一致，才安装并切换新版本后执行服务restart。restart失败时恢复旧
current、删除本次新版本并尽力重新启动旧版本；不重写服务文件、不删除数据或旧版本。

### R4：独立 Web Console

由 Runtime 提供同源 Web 静态资源和本地 session；补 Browser Control Client，使 CLI
安装后可通过 `one-memory ui` 使用完整界面。

### R5：Desktop 薄壳

Desktop 只做 discover -> verify -> attach；缺 Runtime 时走共享安装器。App 与 CLI 使用
相同 release manifest 和 Runtime artifact hash。

### R6：跨平台发行与真机验收

分别构建 macOS arm64/x64、Linux x64/arm64、Windows x64；完成权限存储、来源读取、
服务生命周期、升级回滚和宿主调用真机矩阵。Docker 是 Linux/NAS/CVM 的额外部署形态，
不能取代桌面用户级 CLI 安装验收。
