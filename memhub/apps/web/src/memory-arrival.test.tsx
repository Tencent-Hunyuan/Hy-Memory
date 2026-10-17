// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { MemoryArrivalPanel } from "./memory-arrival";
import type { MemoryArrivalPhase } from "./memory-arrival-state";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const base = {
  importing: false,
  importedMessages: 12,
  importedConversations: 2,
};
const noop = () => {};

it("import-only feedback does not imply HY generation", () => {
  const { container } = render(
    <MemoryArrivalPanel
      arrival={{ ...base, importing: true, phase: "importing" }}
      onConfigureModel={noop}
      onConnect={noop}
    />,
  );
  expect(screen.getByRole("status").textContent).toBe("正在导入对话");
  expect(container.textContent).not.toContain("更多记忆会陆续出现");
  expect(container.textContent).not.toContain("正在沉淀记忆");
});

it("confirmed running generation retains partial errors without looking stopped", () => {
  const { container } = render(
    <MemoryArrivalPanel
      arrival={{
        ...base,
        phase: "processing",
        blockedStages: [
          {
            kind: "extract",
            reason: "hy_memory_model_authentication_failed",
            count: 1,
          },
          {
            kind: "extract",
            reason: "hy_memory_input_exceeds_window",
            count: 5,
          },
        ],
      }}
      onConfigureModel={noop}
      onConnect={noop}
    />,
  );
  expect(screen.getByRole("status").textContent).toBe("正在沉淀记忆");
  expect(container.querySelector(".memory-arrival-spinner")).toBeTruthy();
  expect(container.querySelector("details")?.textContent).toContain(
    "模型认证失败。更新 key 后",
  );
  expect(container.querySelector("details")?.textContent).toContain("5 项");
});

it.each([
  ["checking", "进度待更新", false],
  ["importing", "正在导入对话", true],
  ["model-required", "尚未设置整理模型", false],
  ["processing", "正在沉淀记忆", true],
  ["failed", "待继续", false],
  ["idle", "待继续", false],
  ["stopped", "待继续", false],
  ["unavailable", "进度待更新", false],
] satisfies [MemoryArrivalPhase, string, boolean][])(
  "renders honest %s without invented percentage",
  (phase, title, spinner) => {
    const { container } = render(
      <MemoryArrivalPanel
        arrival={{ ...base, phase }}
        onConfigureModel={noop}
        onConnect={noop}
      />,
    );
    expect(screen.getByRole("status").textContent).toBe(title);
    expect(!!container.querySelector(".memory-arrival-spinner")).toBe(spinner);
    expect(container.textContent).not.toContain("%");
    expect(screen.queryByRole("progressbar")).toBeNull();
  },
);

it("offers explicit connection only for the truly empty idle state", async () => {
  const connect = vi.fn();
  const view = render(
    <MemoryArrivalPanel
      arrival={{
        importing: false,
        importedMessages: 0,
        importedConversations: 0,
        phase: "idle",
      }}
      onConfigureModel={noop}
      onConnect={connect}
    />,
  );
  expect(screen.getByText("还没有保存的对话")).toBeDefined();
  expect(screen.queryByText(/已保存/)).toBeNull();
  expect(connect).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "关联应用" }));
  expect(connect).toHaveBeenCalledOnce();
  view.rerender(
    <MemoryArrivalPanel
      arrival={{ ...base, phase: "idle" }}
      onConfigureModel={noop}
      onConnect={connect}
    />,
  );
  expect(screen.queryByRole("button", { name: "关联应用" })).toBeNull();
});

it.each(["suspended", "reduced-motion"])(
  "stops the processing indicator for %s",
  (mode) => {
    vi.stubGlobal("matchMedia", () => ({
      matches: mode === "reduced-motion",
      addEventListener() {},
      removeEventListener() {},
    }));
    const { container } = render(
      <MemoryArrivalPanel
        arrival={{ ...base, phase: "processing" }}
        suspended={mode === "suspended"}
        onConfigureModel={noop}
        onConnect={noop}
      />,
    );
    expect(
      container
        .querySelector(".memory-arrival-spinner")
        ?.getAttribute("data-moving"),
    ).toBe("false");
  },
);

it("keeps idle existing memories free of an empty-state message", () => {
  const view = render(
    <MemoryArrivalPanel
      arrival={{ ...base, phase: "idle" }}
      hasMemories
      onConfigureModel={noop}
      onConnect={noop}
    />,
  );
  expect(screen.queryByRole("region")).toBeNull();
  view.rerender(
    <MemoryArrivalPanel
      arrival={{ ...base, importing: true, phase: "idle" }}
      hasMemories
      onConfigureModel={noop}
      onConnect={noop}
    />,
  );
  expect(screen.getByText("原文仍在导入")).toBeDefined();
  expect(screen.queryByText("对话已保存，尚无生成的记忆")).toBeNull();
});

it("keeps background progress compact and exact failures collapsed", () => {
  const { container } = render(
    <MemoryArrivalPanel
      arrival={{
        ...base,
        phase: "processing",
        backlogPending: 20,
        blockedStages: [
          {
            kind: "extract",
            reason: "hy_memory_input_exceeds_window",
            count: 3,
          },
        ],
      }}
      onConfigureModel={noop}
      onConnect={noop}
    />,
  );
  expect(screen.getByRole("status").textContent).toBe("正在沉淀记忆");
  expect(screen.getByText("先整理近期对话，更多记忆会陆续出现")).toBeTruthy();
  const details = container.querySelector("details");
  expect(details?.open).toBe(false);
  expect(details?.textContent).toContain(
    "这段对话含过长原文，暂未整理；原文已保留",
  );
  expect(details?.textContent).toContain("3 项");
  // The code stays on the task row in Core. This panel is read by whoever
  // hit the stall, and `hy_memory_input_exceeds_window` is not an answer to
  // anything they can do.
  expect(details?.textContent).not.toContain("hy_memory_input_exceeds_window");
  expect(details?.textContent).toContain(
    "存量数据较大，为保证质量，正在后台分批整理",
  );
  expect(screen.queryByRole("alert")).toBeNull();
});

it("does not spin or say checking without a live refresh", () => {
  const view = render(
    <MemoryArrivalPanel
      arrival={{ ...base, phase: "unavailable" }}
      onConfigureModel={noop}
      onConnect={noop}
    />,
  );
  expect(screen.getByRole("status").textContent).toBe("进度待更新");
  expect(view.container.querySelector(".memory-arrival-spinner")).toBeNull();
  view.rerender(
    <MemoryArrivalPanel
      arrival={{ ...base, phase: "unavailable", refreshing: true }}
      onConfigureModel={noop}
      onConnect={noop}
    />,
  );
  expect(screen.getByRole("status").textContent).toBe(
    "已保存内容保留，正在核对进度",
  );
  view.rerender(
    <MemoryArrivalPanel
      arrival={{ ...base, phase: "unavailable", refreshing: false }}
      onConfigureModel={noop}
      onConnect={noop}
    />,
  );
  expect(screen.getByRole("status").textContent).toBe("进度待更新");
});

it("keeps a stopped authentication failure neutral without claiming processing never began", () => {
  const { container } = render(
    <MemoryArrivalPanel
      arrival={{
        ...base,
        phase: "failed",
        blockedStages: [
          {
            kind: "extract",
            reason: "hy_memory_model_authentication_failed",
            count: 1,
          },
        ],
      }}
      hasMemories
      onConfigureModel={noop}
      onConnect={noop}
    />,
  );
  expect(screen.getByRole("status").textContent).toBe("待继续");
  expect(container.querySelector(".memory-arrival-spinner")).toBeNull();
  expect(container.textContent).not.toContain("还没有开始");
  expect(container.querySelector("details")?.textContent).toContain(
    "模型认证失败。更新 key 后",
  );
  expect(container.textContent).not.toContain("hy_memory_");
  expect(screen.getByRole("button", { name: "设置记忆整理" })).toBeTruthy();
});
