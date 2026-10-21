# 开发验收闭环


> **架构变更（2026-10-21）：** 现行权威见 [`ARCHITECTURE.md`](ARCHITECTURE.md) **Top Rules**。~~MemHub Evidence / `governed_*` 作原文或 memory 权威~~ 已废弃为目标；memory 读写与 backend 只经 SDK；MemHub 可存 `memory_id` 引用。下文未逐段删线的历史表述，凡与 Top Rules 冲突处以架构文档为准。

每个步骤只有经过以下状态才算完成：

`Ready → In progress → Verify → Done`

任何检查失败：

`Verify → Rework → Verify`

## 开始前

1. 读取 `AGENTS.md`、项目规则和实现计划。
2. 确认本步输入、产物、边界用例和硬指标。
3. 先写会失败的测试或 Gate，并确认它能捕获目标回归。

## 实现与验证

1. 做最小范围实现。
2. 运行受影响测试。
3. 运行 `pnpm check`。
4. 使用临时数据目录跑本步端到端流程。
5. 保存命令、退出码、样本数和失败原因。

## 完成定义

- 代码、测试和文档一致。
- 自动测试全绿。
- 目标回归注入后 Gate 确实变红。
- 端到端流程无需手工改数据库或配置补救。
- 已知问题分级并进入计划。

任何一项不满足都不能标 Done。
既有失败与本轮失败必须分开记录，不能弱化断言换取绿色。
