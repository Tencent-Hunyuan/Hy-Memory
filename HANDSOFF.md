# HANDSOFF — MemHub / Hy Memory 架构改造

更新：2026-10-21  
权威细则：[`memhub/docs/ARCHITECTURE.md`](memhub/docs/ARCHITECTURE.md)（~~旧删线~~ + 新并列）  
配套：[`memhub/MEMORY.md`](memhub/MEMORY.md)；其余 docs 已打「架构变更」条，冲突以 ARCHITECTURE Top Rules 为准。
旧用户迁移：[`memhub/docs/contracts/sdk-data-plane-migration-v1.md`](memhub/docs/contracts/sdk-data-plane-migration-v1.md)。

> 根目录原先无有效 `HANDSOFF` 文件；本文件为清空后重写，只保留本次架构改造交接。

---

## IMPORTANT：测试执行规则

1. 先完整运行目标测试套件一次，收集**全部失败 case**。
2. 按根因分组并批量修复，不得用 `-x` 反复重跑已经通过的前置测试。
3. 修复后只重跑上次失败的 case。
4. 失败 case 全部通过后，才再完整运行一次目标套件作为最终 gate。

## Top Rules（现行根本）

1. **所有对 memory 的读写（含存储 backend）都交给 SDK**，经 SDK config / env 配置。
2. **MemHub 内对 memory 的原子操作也一律经 SDK**；缺接口 → **先补 SDK client**，禁止 Core 直改 VDB/Graph/SDK 私库或再维护正文权威。
3. **MemHub 不存 memory 实体 / 引擎侧关系**（正文、supersede 权威、向量图副本等）。主责是管理面：harness、LLM model、异步写入队列、config、Core/Runtime 状态。  
   - **可以且应当存 `memory_id`**（SDK 给外层的唯一身份）及组织元数据。  
   - Matter 占位功能未发布，删除其表与入口；未来若重做，必须另立合同。

**推论：**

| 旧 | 新 |
|---|---|
| MemHub `evidence*` 原文权威 | 原文 → **SDK raw**；Evidence 权威拆除 |
| `governed_*` 列表/版本/supersede 权威 | 拆除方向；UI/纠正/删除 → **授权 + SDK API**；组织层只挂 `memory_id` |
| SDK success 后再镜像进 Governance 才算产品写入 | SDK 合同为准；MemHub 至多记 `memory_id` / 任务回执 |
| 拦 CRUD / parse ops 双写 supersede | 禁止作为目标架构 |

---

## 已改文档（合同先于代码）

- 重写：`memhub/docs/ARCHITECTURE.md`、`memhub/MEMORY.md`
- 条幅 + 关键段新旧对照：`contracts/*`（含 runtime-unification / unified-context-read / source-adapter 等）、`INSTALLATION.md`、`DEVELOPMENT_LOOP.md`、`MEMORY_EVENT_TIME_SCHEDULING_PLAN.md`、`PRODUCT_LOG_TROUBLESHOOTING.md`、`harness-support/README.md`
- 各 harness 专页未逐段改写 → 冲突时以 ARCHITECTURE 为准

**代码尚未按此拆除。** 文档是目标架构，不是已完成迁移。

---

## 建议实现顺序

1. 冻结新功能写入 `governed_*` / MemHub `evidence*` 权威路径。  
2. 记忆库 UI / recall：授权 + SDK list/get/search/update/delete；组织层只挂 `memory_id`。  
3. 导入/同步：写入 SDK（含 raw）；退役 Evidence 权威。  
4. 队列降级为「调度 SDK 调用」，完成态以 SDK 可验证结果为准。  
5. 清空/删除镜像台账；缺的能力开 **SDK PR**，不在 Core 补影子表。

旧用户的数据迁移不是独立的手工升级步骤：由 App 在可见的启动 loading
阶段、Core 开始提供产品读写之前，完成 bundle、可续跑 backfill、reconciliation
与最终 cutover。失败或缺少前置配置时 marker 保持未提交，不允许静默进入双写。

Fresh install 直接创建最新管理面 schema，禁止创建 `evidence*`、`governed_*`
及旧 retrieval/Matter/session-shadow 表。Legacy upgrade 在 bundle 与 SDK
迁移校验完成后物理 DROP 这些旧表；备份 bundle 才是审计/回滚载体，不在 live DB
保留一套“兼容权威”。

---

## 管理面仍属 MemHub（勿误拆）

- harness：`source_grants`、sync desired-state（`config.json`）、适配器扫描（产出进 SDK，不是 Evidence 权威库）
- model profiles / 密钥边界 / 模型内容许可
- `memory_write_jobs` 单层异步队列、FIFO / event-time 调度与租约（始终属于 MemHub；调度，不是 memory 实体）。旧 `memory_tasks` 二级队列与 task receipt 删除。
  - pending job 尚未调用 SDK，因此没有 raw/memory ID；worker 对 SDK 只调用一次
    `add()`，SDK 内部完成 raw + extraction/reconciliation，成功后 MemHub 仅保存
    返回的 raw ID / memory IDs 与 outcome。禁止“先 import_raw 再排 extraction”。
- Core / Runtime 收据与健康
- Matter 尚未形成已发布产品合同；删除全部 `matter*` 占位表与运行时入口。未来若重新开发，
  以新合同重新设计，不为当前占位 schema 保留兼容层。
- call_events 类审计元数据（不存记忆正文）
- ~~session inbox / change_outbox 围绕 Evidence 外发~~ → 需按新规则重定义或标旧

---

## 接手时注意

- Pin 的公开 `hy-memory` 与「先补 SDK 接口」可能不同步：合源码 ≠ 用户包已带能力；用 `Refs` / pin PR 闭环。  
- Top Rule 与旧 `ARCHITECTURE`「SQLite 含 Governance 为业务权威」冲突处，**以本 HANDSOFF + 新 ARCHITECTURE 文首为准**。  
- 本地 `pr_principle.md` / `pr_review.md` 是 PR 流程台账，不替代本架构 HANDSOFF。
