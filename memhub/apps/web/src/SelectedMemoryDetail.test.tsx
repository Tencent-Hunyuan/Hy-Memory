// @vitest-environment jsdom

import type { MemoryDetail, SourceResponse } from "@one-memory/contracts";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "./gui-language";
import { SelectedMemoryDetail } from "./SelectedMemoryDetail";

it("localizes memory details without exposing the retired evidence surface", async () => {
  delete window.oneMemory;
  localStorage.setItem(LANGUAGE_STORAGE_KEY, "es");
  render(
    <LanguageProvider>
      <SelectedMemoryDetail
        detail={detail}
        sources={[]}
        onChanged={vi.fn()}
        hideActions
      />
    </LanguageProvider>,
  );
  expect(await screen.findByText("Guardada · Versión 2")).toBeTruthy();
  expect(
    screen.queryByRole("heading", { name: "Evidencia original" }),
  ).toBeNull();
  expect(screen.queryByText("这次先确认范围。")).toBeNull();
  expect(correct).not.toHaveBeenCalled();
  expect(remove).not.toHaveBeenCalled();
  localStorage.clear();
});

const { correct, read, remove, errorCode } = vi.hoisted(() => ({
  correct: vi.fn(),
  read: vi.fn(),
  remove: vi.fn(),
  errorCode: vi.fn(),
}));
const trace = vi.hoisted(() => ({
  done: vi.fn(),
  traceId: "trace-selected-memory",
}));
vi.mock("./api", () => ({
  correctMemory: correct,
  getMemory: read,
  mutateMemory: remove,
  apiErrorCode: errorCode,
}));
vi.mock("./ui-trace", () => ({
  traceUiAction: () => trace,
}));
vi.mock("./MemoryRecallScope", () => ({ MemoryRecallScope: () => null }));
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
beforeEach(() => {
  correct.mockReset().mockResolvedValue(detail.memory);
  read.mockReset();
  remove.mockReset().mockResolvedValue(detail.memory);
  errorCode.mockReset();
  trace.done.mockReset();
});
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
      onChanged={vi.fn()}
      hideActions
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
      onChanged={vi.fn()}
      hideActions
    />,
  );
  expect(
    screen.getByRole("heading", { name: "项目发布前先确认范围。" }),
  ).toBeTruthy();
  for (const label of ["编辑", "查看原文", "版本记录", "更多管理"])
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
      onChanged={vi.fn()}
      hideActions
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
      onChanged={vi.fn()}
      hideActions
    />,
  );
  expect(screen.getByText("已隐藏")).toBeTruthy();
  expect(screen.queryByText(/第 0 版/)).toBeNull();
  expect(screen.getByText(/^来源 Cursor · /)).toBeTruthy();
});
it("opens full editor and saves content without rewriting scope", async () => {
  const changed = vi.fn();
  render(
    <SelectedMemoryDetail detail={detail} sources={[]} onChanged={changed} />,
  );
  fireEvent.click(screen.getByRole("button", { name: "编辑" }));
  fireEvent.change(screen.getByLabelText("记忆内容"), {
    target: { value: "新正文" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存修改" }));
  await waitFor(() =>
    expect(correct).toHaveBeenCalledWith(
      "m1",
      2,
      "新正文",
      undefined,
      expect.any(String),
      expect.any(String),
    ),
  );
  expect(changed).toHaveBeenCalledWith("m1");
});
it("keeps draft after conflict until latest revision is explicitly acknowledged", async () => {
  correct.mockRejectedValueOnce(Error("conflict"));
  errorCode.mockReturnValue("memory_revision_conflict");
  read.mockResolvedValue({
    ...detail,
    memory: { ...detail.memory, revision: 3, content: "新的远端版本" },
  });
  render(
    <SelectedMemoryDetail detail={detail} sources={[]} onChanged={() => {}} />,
  );
  fireEvent.click(screen.getByRole("button", { name: "编辑" }));
  fireEvent.change(screen.getByLabelText("记忆内容"), {
    target: { value: "我的草稿" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存修改" }));
  await screen.findByText("已有新版本");
  expect(screen.getByLabelText("记忆内容")).toHaveProperty("value", "我的草稿");
  expect(screen.getByRole("button", { name: "保存修改" })).toHaveProperty(
    "disabled",
    true,
  );
  fireEvent.click(screen.getByRole("button", { name: "查看新版本" }));
  await screen.findByText("新的远端版本");
  fireEvent.click(screen.getByRole("button", { name: "基于此版本继续编辑" }));
  correct.mockResolvedValue(detail.memory);
  fireEvent.click(screen.getByRole("button", { name: "保存修改" }));
  await waitFor(() =>
    expect(correct).toHaveBeenLastCalledWith(
      "m1",
      3,
      "我的草稿",
      undefined,
      expect.any(String),
      expect.any(String),
    ),
  );
});
it.each(["编辑", "删除记忆"])(
  "returns focus to %s without scrolling the background",
  (name) => {
    render(
      <StrictMode>
        <div className="r6-shell">
          <SelectedMemoryDetail
            detail={detail}
            sources={[]}
            onChanged={vi.fn()}
          />
        </div>
      </StrictMode>,
    );
    if (name === "删除记忆") fireEvent.click(screen.getByText("更多管理"));
    const trigger = screen.getByRole("button", { name });
    trigger.focus();
    const restore = vi.spyOn(trigger, "focus");
    fireEvent.click(trigger);
    const title = screen.getByRole("heading", { level: 1 });
    expect(document.activeElement).toBe(title);
    fireEvent.keyDown(title, { key: "Escape" });
    expect(document.activeElement).toBe(trigger);
    expect(restore).toHaveBeenCalledWith({ preventScroll: true });
    expect(document.querySelector<HTMLElement>(".r6-shell")?.inert).toBe(false);
  },
);

it.each(["escape", "cancel"])(
  "retains dirty draft when %s confirmation is declined and only discards after acceptance",
  (mode) => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(
      <SelectedMemoryDetail detail={detail} sources={[]} onChanged={vi.fn()} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    fireEvent.change(screen.getByLabelText("记忆内容"), {
      target: { value: "尚未保存的草稿" },
    });
    const close = () =>
      mode === "escape"
        ? fireEvent.keyDown(screen.getByLabelText("记忆内容"), {
            key: "Escape",
          })
        : fireEvent.click(screen.getByRole("button", { name: "取消" }));
    close();
    expect(confirm).toHaveBeenCalledWith("放弃尚未保存的修改？");
    expect(screen.getByLabelText("记忆内容")).toHaveProperty(
      "value",
      "尚未保存的草稿",
    );
    expect(correct).not.toHaveBeenCalled();
    confirm.mockReturnValue(true);
    close();
    expect(screen.queryByLabelText("记忆内容")).toBeNull();
  },
);

it("keeps draft and request identity after a failed save; pending escape cannot discard", async () => {
  let reject!: (reason: Error) => void;
  correct.mockReturnValueOnce(
    new Promise((_, r) => {
      reject = r;
    }),
  );
  const confirm = vi.spyOn(window, "confirm");
  render(
    <SelectedMemoryDetail detail={detail} sources={[]} onChanged={vi.fn()} />,
  );
  fireEvent.click(screen.getByRole("button", { name: "编辑" }));
  fireEvent.change(screen.getByLabelText("记忆内容"), {
    target: { value: "保留草稿" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存修改" }));
  fireEvent.keyDown(screen.getByLabelText("记忆内容"), { key: "Escape" });
  expect(confirm).not.toHaveBeenCalled();
  await act(async () => reject(Error("offline")));
  expect(screen.getByRole("alert").textContent).toContain("草稿已保留");
  expect(screen.getByLabelText("记忆内容")).toHaveProperty("value", "保留草稿");
  fireEvent.click(screen.getByRole("button", { name: "保存修改" }));
  await waitFor(() => expect(correct).toHaveBeenCalledTimes(2));
  // The durable request id must be reused across a retry; the trace id is a
  // fresh id per user attempt so each try forms its own four-line chain.
  expect(correct.mock.calls[1][0]).toBe(correct.mock.calls[0][0]);
  expect(correct.mock.calls[1][1]).toBe(correct.mock.calls[0][1]);
  expect(correct.mock.calls[1][2]).toBe(correct.mock.calls[0][2]);
  expect(correct.mock.calls[1][4]).toBe(correct.mock.calls[0][4]);
});

it("does not apply a late conflict read after its editor has closed", async () => {
  let resolve!: (value: MemoryDetail) => void;
  read.mockReturnValue(
    new Promise((r) => {
      resolve = r;
    }),
  );
  correct.mockRejectedValue(Error("conflict"));
  errorCode.mockReturnValue("memory_revision_conflict");
  const view = render(
    <SelectedMemoryDetail detail={detail} sources={[]} onChanged={vi.fn()} />,
  );
  fireEvent.click(screen.getByRole("button", { name: "编辑" }));
  fireEvent.click(screen.getByRole("button", { name: "保存修改" }));
  await screen.findByText("已有新版本");
  fireEvent.click(screen.getByRole("button", { name: "查看新版本" }));
  view.unmount();
  await act(async () =>
    resolve({
      ...detail,
      memory: { ...detail.memory, revision: 3, content: "late" },
    }),
  );
  expect(screen.queryByText("late")).toBeNull();
});

it("keeps failed deletion on its confirmation page and retries only the same version and request", async () => {
  remove.mockRejectedValue(Error("offline"));
  const changed = vi.fn();
  render(
    <SelectedMemoryDetail detail={detail} sources={[]} onChanged={changed} />,
  );
  fireEvent.click(screen.getByText("更多管理"));
  fireEvent.click(screen.getByRole("button", { name: "删除记忆" }));
  const buttons = screen.getAllByRole("button", { name: "删除记忆" });
  fireEvent.click(buttons[buttons.length - 1]);
  await screen.findByRole("alert");
  expect(screen.getByText(/来源 App 中的原始对话不会删除/)).toBeTruthy();
  fireEvent.click(buttons[buttons.length - 1]);
  await waitFor(() => expect(remove).toHaveBeenCalledTimes(2));
  expect(remove.mock.calls[1][0]).toBe(remove.mock.calls[0][0]);
  expect(remove.mock.calls[1][1]).toBe(remove.mock.calls[0][1]);
  expect(remove.mock.calls[1][2]).toBe(remove.mock.calls[0][2]);
  expect(remove.mock.calls[1][3]).toBe(remove.mock.calls[0][3]);
  expect(remove.mock.calls[0]).toEqual([
    "m1",
    "delete",
    2,
    expect.any(String),
    expect.any(String),
  ]);
  expect(changed).not.toHaveBeenCalled();
});

it("reports a definite save refusal as not submitted, not as an unknown write", async () => {
  correct.mockRejectedValueOnce({
    response: { status: 422, data: { detail: "request_validation_failed" } },
  });
  errorCode.mockReturnValue("request_validation_failed");
  render(
    <SelectedMemoryDetail detail={detail} sources={[]} onChanged={vi.fn()} />,
  );
  fireEvent.click(screen.getByRole("button", { name: "编辑" }));
  fireEvent.change(screen.getByLabelText("记忆内容"), {
    target: { value: "被拒绝的草稿" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存修改" }));

  expect((await screen.findByRole("alert")).textContent).toContain(
    "修改未提交",
  );
  expect(screen.queryByText(/修改尚未确认/)).toBeNull();
  expect(trace.done).toHaveBeenLastCalledWith("failed", {
    reason: "memory_correct_failed",
  });
});

it("reports a definite delete refusal as not submitted and keeps the same request", async () => {
  remove.mockRejectedValueOnce({
    response: { status: 404, data: { detail: "memory_not_found" } },
  });
  errorCode.mockReturnValue("memory_not_found");
  render(
    <SelectedMemoryDetail detail={detail} sources={[]} onChanged={vi.fn()} />,
  );
  fireEvent.click(screen.getByText("更多管理"));
  fireEvent.click(screen.getByRole("button", { name: "删除记忆" }));
  const buttons = screen.getAllByRole("button", { name: "删除记忆" });
  fireEvent.click(buttons[buttons.length - 1]);

  expect((await screen.findByRole("alert")).textContent).toContain(
    "删除未提交",
  );
  expect(screen.queryByText(/删除尚未确认/)).toBeNull();
  expect(trace.done).toHaveBeenLastCalledWith("failed", {
    reason: "memory_delete_failed",
  });

  fireEvent.click(buttons[buttons.length - 1]);
  await waitFor(() => expect(remove).toHaveBeenCalledTimes(2));
  expect(remove.mock.calls[1][3]).toBe(remove.mock.calls[0][3]);
});

it("reports a definite undo refusal as not submitted, not as an unknown write", async () => {
  remove.mockRejectedValueOnce({
    response: { status: 409, data: { detail: "memory_revision_conflict" } },
  });
  errorCode.mockReturnValue("memory_revision_conflict");
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
      onChanged={vi.fn()}
    />,
  );
  fireEvent.click(screen.getByText("更多管理"));
  fireEvent.click(screen.getByRole("button", { name: "撤销上次修改" }));

  expect((await screen.findByRole("alert")).textContent).toContain(
    "撤销未提交",
  );
  expect(screen.queryByText(/操作尚未确认/)).toBeNull();
  expect(trace.done).toHaveBeenLastCalledWith("failed", {
    reason: "memory_undo_failed",
  });
});

it("keeps a timeout save as unknown and does not replace its request identity", async () => {
  let reject!: (reason: unknown) => void;
  correct.mockReturnValueOnce(
    new Promise((_, fail) => {
      reject = fail;
    }),
  );
  errorCode.mockReturnValue(null);
  render(
    <SelectedMemoryDetail detail={detail} sources={[]} onChanged={vi.fn()} />,
  );
  fireEvent.click(screen.getByRole("button", { name: "编辑" }));
  fireEvent.change(screen.getByLabelText("记忆内容"), {
    target: { value: "超时草稿" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存修改" }));
  await act(async () => reject(new Error("offline")));

  expect(screen.getByRole("alert").textContent).toContain("修改尚未确认");
  expect(trace.done).toHaveBeenLastCalledWith("unknown", {
    reason: "memory_correct_result_unknown",
  });
  correct.mockResolvedValue(detail.memory);
  fireEvent.click(screen.getByRole("button", { name: "保存修改" }));
  await waitFor(() => expect(correct).toHaveBeenCalledTimes(2));
  expect(correct.mock.calls[1][4]).toBe(correct.mock.calls[0][4]);
});

it("keeps the original CAS and draft when reading a conflict fails", async () => {
  correct.mockRejectedValue(Error("conflict"));
  errorCode.mockReturnValue("memory_revision_conflict");
  read.mockRejectedValue(Error("offline"));
  render(
    <SelectedMemoryDetail detail={detail} sources={[]} onChanged={vi.fn()} />,
  );
  fireEvent.click(screen.getByRole("button", { name: "编辑" }));
  fireEvent.change(screen.getByLabelText("记忆内容"), {
    target: { value: "冲突草稿" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存修改" }));
  await screen.findByText("已有新版本");
  fireEvent.click(screen.getByRole("button", { name: "查看新版本" }));
  expect((await screen.findByRole("alert")).textContent).toContain(
    "草稿已保留",
  );
  expect(screen.getByLabelText("记忆内容")).toHaveProperty("value", "冲突草稿");
  expect(screen.getByRole("button", { name: "保存修改" })).toHaveProperty(
    "disabled",
    true,
  );
  expect(
    screen.queryByRole("button", { name: "基于此版本继续编辑" }),
  ).toBeNull();
  expect(correct.mock.calls[0][1]).toBe(2);
});

it("suspends the editor without losing its draft or leaving the background inert", () => {
  const p = { detail, sources: [], onChanged: vi.fn() };
  const view = render(
    <div className="r6-shell">
      <SelectedMemoryDetail {...p} />
    </div>,
  );
  const trigger = screen.getByRole("button", { name: "编辑" });
  trigger.focus();
  fireEvent.click(trigger);
  fireEvent.change(screen.getByLabelText("记忆内容"), {
    target: { value: "暂存草稿" },
  });
  view.rerender(
    <div className="r6-shell">
      <SelectedMemoryDetail {...p} suspended />
    </div>,
  );
  expect(document.querySelector<HTMLElement>(".r6-shell")?.inert).toBe(false);
  expect(document.activeElement).toBe(trigger);
  view.rerender(
    <div className="r6-shell">
      <SelectedMemoryDetail {...p} />
    </div>,
  );
  expect(screen.getByLabelText("记忆内容")).toHaveProperty("value", "暂存草稿");
  expect(document.querySelector<HTMLElement>(".r6-shell")?.inert).toBe(true);
  expect(document.activeElement).toBe(
    screen.getByRole("heading", { level: 1 }),
  );
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
      onChanged={vi.fn()}
    />,
  );
  expect(screen.getByText(/来源 workbuddy · 今天 /)).toBeTruthy();
  expect(screen.getByText(/对话时间 今天 /)).toBeTruthy();
  expect(screen.queryByRole("button", { name: "查看原文" })).toBeNull();
});
