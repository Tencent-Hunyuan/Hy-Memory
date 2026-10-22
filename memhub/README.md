# MemHub

MemHub是一个本地优先的共享记忆产品。它把用户明确授权的AI对话和编码记录汇总到一份本地记忆中，再由用户分别授权给支持的AI工具调用。你可以继续使用熟悉的客户端，同时保留跨工具延续的上下文。Hy Memory是MemHub使用的Python SDK和记忆引擎。

# 💡❤️ MemHub 刚刚开源，欢迎一起优化共建！ ❤️💡 

**# 💭 MemHub处于preview阶段，当前公开版本可以下载和体验，部分平台制品、安装路径与宿主体验仍在持续优化验证。安装前请先查看Release里的实际制品和版本说明。**

> 🙋 如果你遇到问题/有建议：欢迎提交Issue，并附上MemHub版本、操作系统与芯片架构、复现步骤、预期结果、实际结果和经过脱敏的日志。请不要上传APIKey、真实对话、个人路径或其他敏感信息。

> 我们会公开跟进可复现的问题，持续发布修复，并根据真实反馈完善安装、同步和召回体验。

**# 感谢每一条反馈，期待你和我们一起打磨MemHub的体验 ：）**

> 💗 MemHub has just been open-sourced and is under active development. The current public release is available for download andevaluation. Some platform packages, installation paths, and host integrations are still being completed and verified. Check the assets
and release notes before installing.
If you run into a problem, please open an Issue with the version, platform,reproduction steps, and sanitized logs, We wilreviewreports, publish fixes, and keep the support matrix up to date.

## 发行与平台合同

源码当前固定使用公开 PyPI 开发预览 `hy-memory==2.0.5.dev3`。MemHub 源码版本为 `1.0.4`
（build 13），GitHub 仓库为
[`Tencent-Hunyuan/Hy-Memory`](https://github.com/Tencent-Hunyuan/Hy-Memory)，npm 包名为
`memhub-cli`。当前代码架构见
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。

`v1.0.4` 的发行目标是 Linux x64/arm64 Runtime 与 macOS arm64/x64 App 和 Runtime。
Linux 制品由 GitHub Actions 在原生架构 runner 上构建并完成生命周期验收；macOS
制品由发行流水线完成 Developer ID 签名、公证和验收后手动上传。Windows 不支持。
只有同时出现在 GitHub Release 与 `release-manifest.json` 中、且通过公开校验的制品才属于
该版本的正式支持范围。

安装流程、未来 npm Quick Start、已记录体积和数据生命周期见
[`docs/INSTALLATION.md`](docs/INSTALLATION.md)。本地 HTTPS fake Release 的可重复验收入口为
[`scripts/platform/linux/verify-fake-release-bootstrap.mjs`](scripts/platform/linux/verify-fake-release-bootstrap.mjs)。

## 核心原则

- 一次安装、一个 Runtime、一份数据、多种客户端。
- Hy Memory SDK 是 memory/raw/vector/graph/coding 数据面的唯一权威；Python Core
  管理授权、模型、异步队列和 Runtime 状态。
- 来源读取、模型处理和宿主读取是相互独立的授权边界。
- 正式写链路为 `memory_write_jobs -> SDK add() -> raw/memory ID receipt`。
- 产品不包含屏幕、Accessibility / AXTree、OCR、键鼠、剪贴板或后台录音采集。

## 仓库结构

- `apps/core`：Python / FastAPI Kernel，负责授权、模型、队列、SDK bridge 与 API。
- `apps/cli`：安装、控制和诊断共享 Runtime 的 `one-memory` CLI。
- `apps/web`：React / TypeScript 管理界面。
- `apps/desktop`：Electron 桌面壳与图形安装入口。
- `packages/contracts`：公共 API 类型。
- `packages/host-bridge`：AI App 的 Skill、Hook、插件和 MCP 接入。
- `packages/runtime-control`：Core、Host Gateway 和 Runtime 生命周期控制。

架构详情见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) 和
[`docs/contracts/runtime-unification.md`](docs/contracts/runtime-unification.md)。
产品、测试和一线支持排查导入与记忆沉淀时，使用
[`docs/PRODUCT_LOG_TROUBLESHOOTING.md`](docs/PRODUCT_LOG_TROUBLESHOOTING.md)。

## 开发环境

要求 Node.js 24、pnpm 11、Python 3.12 和 uv。

```bash
pnpm install
uv sync --directory apps/core --all-groups --locked
pnpm check
```

常用开发命令：

```bash
pnpm dev:web
pnpm dev:desktop
pnpm typecheck
pnpm test
pnpm lint
```

## CLI 与 Runtime

```text
memhub serve
memhub start
memhub stop
memhub restart
memhub status
memhub doctor
memhub install
memhub upgrade
memhub uninstall
memhub delete-data
```

`memhub serve` 在前台启动完整 Runtime。`memhub` 是主发行命令；npm 包 `memhub-cli`
同时暴露同名命令，`one-memory` 作为历史兼容命令保留。Desktop 和 CLI 使用相同 Runtime、数据目录和
权限合同；关闭窗口或退出 Desktop UI 不停止由系统服务管理的 Runtime。macOS 正式 App 与
CLI 共用唯一 LaunchAgent；删除 App 时，只有 App-owned 服务会自动停止，CLI-owned 服务继续
运行并由 `memhub uninstall` 管理。

npm 包只承载约 384 KB 的 Bootstrap CLI，不包含 Python Core、SQLite 数据、Web、内置
Node、Host Bridge 或模型。用户显式执行 `memhub install` 后，CLI 才下载并校验匹配版本
的完整 Runtime；npm `postinstall` 不下载 Runtime、不注册服务，也不修改 shell profile。

Python Core 和运行所需的 Python 依赖位于 MemHub 受管 Runtime，不写入系统 Python。
MemHub 的 SQLite 数据默认位于 `~/.one-memory`，不在 npm 包或 Runtime 版本目录中。
`npm uninstall` 只移除 npm CLI；`memhub uninstall` 移除服务、受管 Runtime 和 CLI 链接，
默认保留 Memory。只有独立的 `memhub delete-data` 在 Runtime 停止且二次确认精确路径后
才会删除数据。

当前平台能力和安装边界以源码、CI 与发布说明为准。未完成真机验收的平台不会仅凭跨平台
测试宣称可用。

## Hy Memory

SDK 依赖固定为公开 PyPI 开发预览 `hy-memory==2.0.5.dev3`。SDK 配置、权限边界及 search
返回格式见 [`MEMORY.md`](MEMORY.md)。HY search 返回的 `content` 已包含 SDK 统一生成的
逻辑时间和演化上下文；调用方不应再次转换时间戳或重复拼接 `When`。

## 隐私与安全

- 隐私说明：[`PRIVACY.md`](PRIVACY.md)
- 安全报告：[`SECURITY.md`](SECURITY.md)
- 贡献指南：[`CONTRIBUTING.md`](CONTRIBUTING.md)
- 行为准则：[`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md)

## 许可证

MemHub 使用 Apache License 2.0。Tencent 著作权声明见 [`NOTICE`](NOTICE)，第三方
归属和许可证见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) 与
[`SBOM.spdx.json`](SBOM.spdx.json)。
