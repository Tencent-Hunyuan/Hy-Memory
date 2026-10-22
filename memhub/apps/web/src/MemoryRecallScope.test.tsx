// @vitest-environment jsdom
import type { MemoryDetail } from "@one-memory/contracts";
import { describe, expect, it } from "vitest";
import { memoryProvenanceLabel } from "./MemoryRecallScope";

const detail = {
  memory: {
    memory_id: "m1",
    status: "confirmed",
    revision: 1,
    scopes: ["workspace:limited"],
    content: "Keep the original",
    origin: "agent",
  },
  history: [{ revision: 1, scopes: ["workspace:limited"] }],
  evidence: [
    {
      available: true,
      source_id: "memory-content-export",
      written_by: "unknown",
    },
  ],
} as MemoryDetail;

describe("memory provenance label", () => {
  it("reports the unknown original author, not the internal agent lane", () => {
    expect(memoryProvenanceLabel(detail)).toBe("导入的已有记忆 · 作者未知");
    expect(
      memoryProvenanceLabel({
        ...detail,
        evidence: [{ ...detail.evidence[0], written_by: "agent" }],
      }),
    ).toBe("导入的已有记忆 · Agent 原文");
  });
  it("MEM-MAPD-01: does not describe manual memories as extracted conversations", () => {
    expect(
      memoryProvenanceLabel({
        ...detail,
        memory: { ...detail.memory, origin: "manual" },
        evidence: [
          {
            ...detail.evidence[0],
            source_id: "one-memory-manual",
            written_by: "person",
          },
        ],
      }),
    ).toBe("手动添加");
  });
  it("names a memory taken from the assistant's reply as the AI's, not the person's", () => {
    const fromConversation = {
      ...detail,
      evidence: [
        { ...detail.evidence[0], source_id: "codex", written_by: "agent" },
      ],
    } as MemoryDetail;
    expect(memoryProvenanceLabel(fromConversation)).toBe("由 AI 回复整理");
    expect(
      memoryProvenanceLabel({
        ...fromConversation,
        memory: { ...fromConversation.memory, origin: "extracted" },
      }),
    ).toBe("由原始对话整理");
  });
});
