# Hy Memory Plugins

各 Agent / 编辑器平台的集成代码。记忆能力由仓库根目录的 Python SDK（`hy_memory/`，PyPI: `hy-memory`）提供；插件层负责对接宿主框架（HTTP、hooks、MCP 等）。

## 布局

| 目录 | 说明 | 发布 |
|------|------|------|
| `hermes/` | Hermes 原生 memory provider（标准库 HTTP 客户端，独立服务） | [安装说明](hermes/README.md) |
| `openclaw/` | OpenClaw 记忆插件（TypeScript，拉起 Python `hy_memory.server`） | npm: `openclaw-hy-memory` |
| `hook/` | Claude Code 等 hook 集成（预留） | — |
| `mcp/` | MCP Server 集成（预留） | — |

与 [Mem0](https://github.com/mem0ai/mem0) 的差异：Mem0 的 OpenClaw 插件依赖 **TypeScript SDK**（`mem0ai` npm）；本仓库插件依赖 **Python SDK**，通过本地 HTTP 服务通信。

## 开发

```bash
# OpenClaw 插件
cd plugins/openclaw
npm install
npm run build
```

发布到 npmjs：

```bash
bash scripts/publish_npmjs.sh <version>
```
