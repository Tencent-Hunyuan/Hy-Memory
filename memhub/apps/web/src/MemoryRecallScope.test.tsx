// @vitest-environment jsdom
import type { MemoryDetail } from "@one-memory/contracts";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRecallScope, memoryProvenanceLabel } from "./MemoryRecallScope";

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

it("traduce la procedencia y conserva el chino original por defecto", () => {
  expect(memoryProvenanceLabel(detail, "es")).toBe(
    "Memoria existente importada · Autor desconocido",
  );
  expect(memoryProvenanceLabel(detail)).toBe("导入的已有记忆 · 作者未知");
});

describe("MemoryRecallScope", () => {
  afterEach(cleanup);
  it("requires Save and submits only scopes", async () => {
    const save = vi.fn().mockResolvedValue(undefined);
    render(<MemoryRecallScope detail={detail} busy={false} onSave={save} />);
    await userEvent.click(screen.getByRole("button", { name: "调整召回范围" }));
    await userEvent.click(screen.getByRole("radio", { name: "所有关联工具" }));
    expect(save).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "保存召回范围" }));
    await waitFor(() =>
      expect(save).toHaveBeenCalledWith(["all-apps", "workspace:limited"]),
    );
  });
  it("cancel keeps the saved scope and restores focus without a mutation", async () => {
    const save = vi.fn();
    render(<MemoryRecallScope detail={detail} busy={false} onSave={save} />);
    await userEvent.click(screen.getByRole("button", { name: "调整召回范围" }));
    await userEvent.click(
      screen.getByRole("radio", { name: "停止外部记忆召回" }),
    );
    await userEvent.click(screen.getByRole("button", { name: "取消范围修改" }));
    expect(save).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(
      screen.getByRole("button", { name: "调整召回范围" }),
    );
  });
  it("does not invent original limits for an all-apps manual memory", async () => {
    render(
      <MemoryRecallScope
        detail={{
          ...detail,
          memory: { ...detail.memory, scopes: ["all-apps"] },
          history: [],
        }}
        busy={false}
        onSave={vi.fn()}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "调整召回范围" }));
    expect(screen.queryByRole("radio", { name: "原有限定范围" })).toBeNull();
    expect(screen.getByText(/原文检索仍按来源授权/)).toBeTruthy();
  });
  it("retains the draft after failure without reporting success", async () => {
    render(
      <MemoryRecallScope
        detail={detail}
        busy={false}
        onSave={vi.fn().mockRejectedValue(new Error("failed"))}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "调整召回范围" }));
    await userEvent.click(
      screen.getByRole("radio", { name: "停止外部记忆召回" }),
    );
    await userEvent.click(screen.getByRole("button", { name: "保存召回范围" }));
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(
      (
        screen.getByRole("radio", {
          name: "停止外部记忆召回",
        }) as HTMLInputElement
      ).checked,
    ).toBe(true);
  });
  it("does not call a definite refusal 'unconfirmed'", async () => {
    // The trace logs a definite 4xx as `failed`; if the inline sentence says
    // the result is unconfirmed, the screen contradicts the log for the same
    // write. A refusal is a fact, an unknown outcome is not.
    const refused = Object.assign(new Error("refused"), {
      response: { status: 400, data: { detail: "memory_scope_rejected" } },
    });
    render(
      <MemoryRecallScope
        detail={detail}
        busy={false}
        onSave={vi.fn().mockRejectedValue(refused)}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "调整召回范围" }));
    await userEvent.click(
      screen.getByRole("radio", { name: "停止外部记忆召回" }),
    );
    await userEvent.click(screen.getByRole("button", { name: "保存召回范围" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("范围修改未提交");
    expect(alert.textContent).not.toContain("尚未确认");
  });
  it("still says an unknown outcome is unconfirmed", async () => {
    // A timeout / transport error may have landed; the copy must not claim a
    // refusal either.
    const timeout = Object.assign(new Error("timeout"), {
      code: "ECONNABORTED",
    });
    render(
      <MemoryRecallScope
        detail={detail}
        busy={false}
        onSave={vi.fn().mockRejectedValue(timeout)}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "调整召回范围" }));
    await userEvent.click(
      screen.getByRole("radio", { name: "停止外部记忆召回" }),
    );
    await userEvent.click(screen.getByRole("button", { name: "保存召回范围" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("尚未确认");
  });
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
