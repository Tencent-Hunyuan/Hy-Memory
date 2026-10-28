// @vitest-environment jsdom

import type { MemoryDetail, SourceResponse } from "@one-memory/contracts";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "./gui-language";
import { SelectedMemoryDetail } from "./SelectedMemoryDetail";

it("localizes memory details without exposing the retired evidence surface", async () => {
  delete window.oneMemory;
  localStorage.setItem(LANGUAGE_STORAGE_KEY, "es");
  render(
    <LanguageProvider>
      <SelectedMemoryDetail detail={detail} sources={[]} />
    </LanguageProvider>,
  );
  expect(await screen.findByText("Guardada · Versión 2")).toBeTruthy();
  expect(
    screen.queryByRole("heading", { name: "Evidencia original" }),
  ).toBeNull();
  expect(screen.queryByText("这次先确认范围。")).toBeNull();
  localStorage.clear();
});

const detail = {
  memory: {
    memory_id: "m1",
    revision: 2,
    content: "项目发布前先确认范围。设计预览不代表产品验收。",
    status: "confirmed",
    scopes: ["private"],
  },
  evidence: [
    {
      evidence_id: "e1",
      source_id: "workbuddy",
      written_by: "person",
      created_at: "2026-09-19T00:00:00Z",
      available: true,
      verbatim: "这次先确认范围。",
    },
  ],
  history: [],
} as unknown as MemoryDetail;
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
it("OBS-21: names a manually added memory 手动添加, not the internal source id", () => {
  render(
    <SelectedMemoryDetail
      detail={{
        ...detail,
        memory: { ...detail.memory, origin: "manual" },
        evidence: [{ ...detail.evidence[0], source_id: "one-memory-manual" }],
      }}
      sources={[]}
    />,
  );
  expect(screen.getByText(/^来源 手动添加 · /)).toBeTruthy();
  expect(screen.queryByText(/one-memory-manual/)).toBeNull();
});

it("keeps the simplified memory inspector informational", () => {
  render(
    <SelectedMemoryDetail
      detail={{
        ...detail,
        history: [
          { ...detail.memory, revision: 1, action: "create" },
          { ...detail.memory, revision: 2, action: "correct" },
        ],
      }}
      sources={[]}
    />,
  );
  expect(
    screen.getByRole("heading", { name: "项目发布前先确认范围。" }),
  ).toBeTruthy();
  for (const label of ["编辑", "查看原文", "版本记录", "更多管理", "删除"])
    expect(screen.queryByText(label)).toBeNull();
});
it.each([
  ["confirmed", "已沉淀"],
  ["deleted", "已删除"],
  ["superseded", "已被取代"],
] as const)("labels a %s memory with its actual state", (status, label) => {
  render(
    <SelectedMemoryDetail
      detail={{
        ...detail,
        memory: { ...detail.memory, status },
      }}
      sources={[]}
    />,
  );
  expect(screen.getByText(`${label} · 第 2 版`)).toBeTruthy();
  expect(screen.queryByText("待核对 · 第 2 版")).toBeNull();
});
it("shows the native SDK state without inventing a revision", () => {
  render(
    <SelectedMemoryDetail
      detail={{
        ...detail,
        memory: { ...detail.memory, revision: 0 },
        evidence: [],
        sdk_status: "shadow",
        source_id: "cursor",
        sdk_memory_at: "2026-09-18T00:00:00Z",
        sdk_created_at: "2026-09-19T00:00:00Z",
      }}
      sources={[
        { source_id: "cursor", display_name: "Cursor" } as SourceResponse,
      ]}
    />,
  );
  expect(screen.getByText("已隐藏")).toBeTruthy();
  expect(screen.queryByText(/第 0 版/)).toBeNull();
  expect(screen.getByText(/^来源 Cursor · /)).toBeTruthy();
});
it("splits long memory text into a bounded heading and readable body", () => {
  const content = "长".repeat(100);
  render(
    <SelectedMemoryDetail
      detail={{
        ...detail,
        memory: { ...detail.memory, content },
        evidence: [],
        sdk_status: "active",
      }}
      sources={[]}
    />,
  );
  expect(screen.getByRole("heading", { name: "长".repeat(72) })).toBeTruthy();
  expect(screen.getByText("长".repeat(28)).className).toContain(
    "selected-memory-body",
  );
});
it.each([
  [
    "a late sentence end",
    `${"长".repeat(80)}。${"尾".repeat(10)}`,
    "长".repeat(72),
  ],
  ["words", `${"word ".repeat(20)}end`, "word ".repeat(14).trim()],
])("keeps the heading within the limit for %s", (_case, content, heading) => {
  render(
    <SelectedMemoryDetail
      detail={{
        ...detail,
        memory: { ...detail.memory, content },
        evidence: [],
        sdk_status: "active",
      }}
      sources={[]}
    />,
  );
  expect(screen.getByRole("heading", { name: heading })).toBeTruthy();
});
it("stamps today's source and conversation time without an evidence page", () => {
  render(
    <SelectedMemoryDetail
      detail={{
        ...detail,
        evidence: [
          {
            ...detail.evidence[0],
            created_at: new Date().toISOString(),
          },
        ],
      }}
      sources={[]}
    />,
  );
  expect(screen.getByText(/来源 workbuddy · 今天 /)).toBeTruthy();
  expect(screen.getByText(/对话时间 今天 /)).toBeTruthy();
  expect(screen.queryByRole("button", { name: "查看原文" })).toBeNull();
});
it("shows an experience's conversation time when the SDK stored one", () => {
  const today = new Date().toISOString();
  render(
    <SelectedMemoryDetail
      detail={{
        ...detail,
        evidence: [],
        sdk_status: "active",
        source_id: "codebuddy",
        sdk_memory_at: today,
        sdk_created_at: today,
        sdk_ref: { scene: "coding", kind: "experience", variant: "coding" },
      }}
      sources={[]}
    />,
  );
  expect(screen.getByText(/对话时间 今天 /)).toBeTruthy();
  expect(screen.queryByText(/时间未知/)).toBeNull();
});
it("labels an experience without a conversation time by its write time", () => {
  render(
    <SelectedMemoryDetail
      detail={{
        ...detail,
        evidence: [],
        sdk_status: "active",
        source_id: "codebuddy",
        sdk_memory_at: null,
        sdk_created_at: new Date().toISOString(),
        sdk_ref: { scene: "working", kind: "experience", variant: "working" },
      }}
      sources={[]}
    />,
  );
  expect(screen.getByText(/来源 codebuddy · 写入时间 今天 /)).toBeTruthy();
  expect(screen.queryByText(/对话时间/)).toBeNull();
  expect(screen.queryByText(/时间未知/)).toBeNull();
});
