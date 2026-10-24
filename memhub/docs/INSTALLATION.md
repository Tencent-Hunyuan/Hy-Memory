# MemHub 安装与发行

MemHub `v1.0.5`（build 14）的发行目标如下：

| 平台 | 发行物 | 构建与验收 |
| --- | --- | --- |
| Linux x64 | Runtime tar.gz | GitHub Actions 原生 x64 runner |
| Linux arm64 | Runtime tar.gz | GitHub Actions 原生 arm64 runner |
| macOS arm64 | DMG 与 Runtime tar.gz | Developer ID 签名、公证和原生 arm64 验收 |
| macOS x64 | DMG 与 Runtime tar.gz | Developer ID 签名、公证和原生 Intel 验收 |
| Windows | 无 | 当前不支持 |

正式支持以对应 GitHub Release 的 `release-manifest.json` 为准。Manifest 记录版本、
build、Git commit、协议范围、文件大小和 SHA-256；缺少条目的平台不属于该版本支持范围。
在 `v*` tag 上手动运行仓库的 `Linux lifecycle` GitHub Actions 后，Linux Runtime
和 SHA-256 文件会自动上传到对应的 Draft Release；普通分支运行只保留 Actions
Artifact。macOS 制品由发行流水线完成签名与公证后上传到同一个 Draft Release；
GitHub 不保存 Apple 私钥。所有平台制品核对完成后再发布 Release。

最低支持 macOS 13。当前更新方式是从 GitHub Releases 手动下载新版本，不包含
auto-updater。

## 公开安装方式

npm 包与同版本 GitHub Release 上线后，标准入口是：

```bash
npm install -g memhub-cli
memhub install
memhub init
memhub status
```

`npm install` 只安装 Bootstrap CLI。第一次运行 `memhub install` 时才会：

1. 获取与 CLI 精确同版本、同 channel 的 `release-manifest.json`；
2. 选择当前操作系统和 CPU 架构的 Runtime；
3. 校验 Git commit、Runtime manifest schema，以及 CLI/Runtime 双向协议兼容范围；
4. 校验 HTTPS URL、重定向域名、文件大小与 SHA-256；
5. 安全解压并交叉验证内部 manifest 的版本、build、commit、协议和逐文件哈希；
6. 调用现有安装事务，注册用户级服务和受管 CLI 链接；
7. 失败时回滚，并保留当前 Runtime 和 Memory 数据。

不带 `--yes` 的 `memhub install` 只下载较小的 Release manifest 并展示计划；显式确认后才
下载完整 Runtime。npm `postinstall` 不下载 Runtime、不注册服务、不修改 shell profile。

## 离线安装

已经取得 Runtime distribution 的开发者可以离线安装；仓库本身不包含该 distribution：

```bash
/path/to/memhub-runtime/bin/one-memory install \
  --source /path/to/memhub-runtime \
  --yes
```

提供 `--source` 后安装器完全离线，不请求 Release manifest 或 artifact。默认会先生成安装
计划；上例中的 `--yes` 表示应用计划，但不会跳过 manifest、commit、协议、平台、架构或
文件哈希校验。缺少协议声明的历史离线包不会作为新安装来源被接受。

需要隔离测试时，可显式传入：

```text
--install-root <path>
--home <path>
--bin-dir <path>
--data-dir <path>
--xdg-config-home <path>
--no-shell-path
```

Linux 的用户级服务要求当前用户具有可用的 systemd user session。仅需前台运行时可使用
完整 Runtime 内的 `memhub serve`，但这不等同于完成服务安装。

## macOS App 与后台 Runtime

当前 macOS App 和 npm CLI 复用同一 Runtime、默认数据目录、安装根、收据、端口、协议和
唯一 `com.tencent.onememory.runtime` LaunchAgent：

- 正式 App 首次从 `/Applications` 启动时安装稳定 Runtime 和统一 LaunchAgent；
- 登录后由 LaunchAgent 直接启动 Runtime/Core/Broker，不启动 Electron 或打开窗口；
- 关闭窗口、退出或强退 Electron 只断开 UI，后台 Runtime 继续工作；
- “登录后启动后台服务”控制 launchd `KeepAlive.PathState` 使用的私有 marker，关闭时不
  中断当前 Runtime，只影响未来登录/重启；
- App-owned 服务持续核对 App bundle。用户删除 `/Applications/MemHub.app` 后，marker 被
  撤销，Runtime/Core/Broker 停止且不会被 KeepAlive 拉起；Memory 数据默认保留；
- 完整 `memhub install --yes` 或成功升级可以事务性接管为 CLI-owned。此后删除 GUI App
  不影响服务，需用 `memhub uninstall` 管理；
- App-first/CLI-second 和 CLI-first/App-second 都使用同一个服务声明与单写者 Runtime。

完整进程、owner 与卸载矩阵见 [`ARCHITECTURE.md`](ARCHITECTURE.md)。真实注销/登录和整机
重启属于发行验收，源码测试或隔离 launchctl 验收不能替代该系统级验证。

Downloader 使用标准 `HTTPS_PROXY`/`https_proxy` 与 `NO_PROXY`/`no_proxy` 环境变量。代理只
承载 HTTPS CONNECT 隧道；Release URL、重定向域名、大小、SHA-256、commit 和协议校验不会
因代理而跳过。若 Release 主机列入 `NO_PROXY`，请求会直连。

## 升级、卸载与数据

- `npm uninstall -g memhub-cli`：只删除 npm CLI；不停止或删除受管 Runtime，不删除 Memory。
  完整 fake Release 验收会在 Runtime 运行中执行真实 npm uninstall，并再次验证
  systemd、`current`、受管 `memhub`、doctor 和 SQLite 均仍可用。
- `memhub upgrade`：下载并校验新 Runtime；旧版本写过的数据会先迁移到 SDK 数据面（先做完整备份，终端显示进度），再启动新 Runtime。迁移失败时恢复旧版本；迁移完成后旧版本无法打开数据，健康检查失败时保留新版本并提示 `memhub restart` / `memhub doctor`。
- `memhub uninstall`：停止并移除受管服务、Runtime versions/current 和受管 CLI 链接；
  默认保留数据。
- `memhub delete-data`：唯一删除 Memory 的命令；Runtime 必须已停止，并要求 `--yes` 与
  `--confirm <精确数据路径>` 两次确认。

默认数据目录是 `~/.one-memory`。Python Core 是 MemHub 管理库的唯一写入者；
memory/raw 数据平面只通过 Hy Memory SDK 写入。SQLite 数据不位于 npm package、
App 临时目录或受管 Runtime 的版本目录中。Python Core 及其依赖由 Runtime 携带，
不会向用户的系统 Python 安装 package。

## 代理、镜像和离线来源

- `--source <path>`：完全离线，优先级最高。
- `--release-manifest-url <https-url>`：显式 Release manifest。
- `MEMHUB_RELEASE_MANIFEST_URL`：固定 manifest URL。
- `MEMHUB_RELEASE_MANIFEST_URL_TEMPLATE`：用 `{version}` 构造同版本 URL。
- `MEMHUB_DEFAULT_RELEASE_MANIFEST_URL_TEMPLATE`：仅供发行构建时注入默认模板；它不是用户
  运行时配置。源码默认值为空，不硬编码私有仓库地址。
- `MEMHUB_RELEASE_ALLOWED_HOSTS`：逗号分隔的额外重定向 hostname allow-list，适用于 Release
  平台跳转到对象存储的场景。

manifest 与 artifact 均只接受 HTTPS。跨 hostname 重定向默认拒绝，必须显式加入 allow-list。
网络中断、大小/哈希不符、安全解压失败或磁盘空间不足时，安装器在进入本地安装事务前停止。
