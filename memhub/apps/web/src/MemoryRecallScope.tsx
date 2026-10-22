import type { MemoryDetail } from "@one-memory/contracts";

/** Human-readable provenance only; per-memory recall ACLs are not a product feature. */
export function memoryProvenanceLabel(detail: MemoryDetail): string {
  const evidence = detail.evidence.filter((item) => item.available);
  if (
    evidence.length &&
    evidence.every((item) => item.source_id === "memory-content-export")
  ) {
    const authors = new Set(
      evidence.map((item) => item.written_by ?? "unknown"),
    );
    const author =
      authors.size !== 1
        ? "多位作者"
        : authors.has("person")
          ? "用户原文"
          : authors.has("agent")
            ? "Agent 原文"
            : "作者未知";
    return `导入的已有记忆 · ${author}`;
  }
  return detail.memory.origin === "manual"
    ? "手动添加"
    : detail.memory.origin === "answered"
      ? "问答生成"
      : detail.memory.origin === "agent"
        ? "由 AI 回复整理"
        : "由原始对话整理";
}
