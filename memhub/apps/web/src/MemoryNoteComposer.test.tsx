// @vitest-environment jsdom
import type { MemoryNoteReceipt } from "@one-memory/contracts";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "./gui-language";
import { MemoryNoteComposer } from "./MemoryNoteComposer";

it("opens a Spanish composer without saving, processing or enabling anything", async () => {
  delete window.oneMemory;
  localStorage.setItem(LANGUAGE_STORAGE_KEY, "es");
  render(
    <LanguageProvider>
      <MemoryNoteComposer
        onSaved={vi.fn()}
        onOpenMemory={vi.fn()}
        onConfigureModel={vi.fn()}
      />
    </LanguageProvider>,
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "Añadir memoria" }),
  );
  expect(screen.getByRole("dialog", { name: "Añadir memoria" })).toBeTruthy();
  expect(
    screen.getByRole("textbox", { name: "¿Qué quieres recordar?" }),
  ).toBeTruthy();
  expect(
    (screen.getByRole("button", { name: "Guardar texto" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  await userEvent.click(
    screen.getByRole("button", { name: "Cerrar nueva memoria" }),
  );
  expect(api.saveMemoryNote).not.toHaveBeenCalled();
  expect(api.processMemoryNote).not.toHaveBeenCalled();
  localStorage.clear();
});

const api = vi.hoisted(() => ({
  getMemoryNotes: vi.fn(),
  getMemoryNoteOperation: vi.fn(),
  getMemoryNoteEvidence: vi.fn(),
  getModelProfiles: vi.fn(),
  processMemoryNote: vi.fn(),
  saveMemoryNote: vi.fn(),
  apiErrorCode: vi.fn(),
  getSources: vi.fn(),
}));
vi.mock("./api", () => api);

const note: MemoryNoteReceipt = {
  evidence_id: "note-1",
  text: "项目周会不排在周五。",
  scope: "all-apps",
  created_at: "2026-09-12T00:00:00Z",
  state: "waiting_for_model",
  task_id: null,
  memory_ids: [],
  error: null,
};
beforeEach(() => {
  vi.resetAllMocks();
  api.getMemoryNotes.mockResolvedValue([]);
  api.getMemoryNoteOperation.mockRejectedValue(new Error("not found"));
  api.getModelProfiles.mockResolvedValue([]);
  api.saveMemoryNote.mockResolvedValue(note);
  api.apiErrorCode.mockImplementation(
    (reason) => reason?.response?.data?.detail ?? null,
  );
});
afterEach(cleanup);

it("OBS-18: reads the configured model before saying it is not configured", async () => {
  let release: (value: unknown) => void = () => {};
  api.getModelProfiles.mockReturnValue(
    new Promise((resolve) => {
      release = resolve;
    }),
  );
  page();
  await userEvent.click(screen.getByRole("button", { name: "添加记忆" }));
  expect(screen.getByText(/整理模型：/).textContent).toContain("正在读取");
  expect(screen.queryByRole("button", { name: "配置模型" })).toBeNull();
  release([
    {
      profile_id: "p1",
      name: "DeepSeek",
      is_active: true,
    },
  ]);
  await waitFor(() =>
    expect(screen.getByText(/整理模型：/).textContent).toContain("DeepSeek"),
  );
  expect(screen.queryByText(/尚未配置/)).toBeNull();
});

it("explains source verification failure without retrying or claiming success", async () => {
  api.getMemoryNotes.mockResolvedValue([
    {
      ...note,
      state: "failed",
      error: "hy_memory_write_verification_historical_support_only",
    },
  ]);
  page();
  await userEvent.click(screen.getByRole("button", { name: "添加记忆" }));
  expect(
    await screen.findByText(
      "历史引用尚未绑定到原始来源，当前批次未完成，原话已保留。",
    ),
  ).toBeTruthy();
  expect(screen.queryByText("整理已完成")).toBeNull();
  expect(api.processMemoryNote).not.toHaveBeenCalled();
});

function page() {
  const onSaved = vi.fn(),
    onOpenMemory = vi.fn(),
    onConfigureModel = vi.fn(),
    onPendingChange = vi.fn();
  const view = render(
    <MemoryNoteComposer
      {...{ onSaved, onOpenMemory, onConfigureModel, onPendingChange }}
    />,
  );
  return {
    ...view,
    onSaved,
    onOpenMemory,
    onConfigureModel,
    onPendingChange,
  };
}

it("reports a saved note that still needs processing when the panel is closed", async () => {
  api.getMemoryNotes.mockResolvedValue([note]);
  const callbacks = page();
  await waitFor(() =>
    expect(callbacks.onPendingChange).toHaveBeenCalledWith({
      evidenceId: note.evidence_id,
      text: note.text,
      state: "waiting_for_model",
    }),
  );
  await userEvent.click(screen.getByRole("button", { name: "添加记忆" }));
  await waitFor(() =>
    expect(callbacks.onPendingChange).toHaveBeenCalledWith({
      evidenceId: note.evidence_id,
      text: note.text,
      state: "waiting_for_model",
    }),
  );
  await userEvent.keyboard("{Escape}");
  expect(callbacks.onPendingChange).toHaveBeenLastCalledWith({
    evidenceId: note.evidence_id,
    text: note.text,
    state: "waiting_for_model",
  });
});

it("saves from an empty library with the linked-app scope by default", async () => {
  const callbacks = page();
  await userEvent.click(screen.getByRole("button", { name: "添加记忆" }));
  const input = screen.getByRole("textbox", { name: "记下这件事" });
  expect(document.activeElement).toBe(input);
  await userEvent.type(input, note.text);
  expect(
    screen.getByRole("button", { name: "保存原话" }).hasAttribute("disabled"),
  ).toBe(false);
  expect(
    screen.queryByRole("checkbox", { name: /允许已关联应用读取/ }),
  ).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "保存原话" }));
  await waitFor(() =>
    expect(api.saveMemoryNote).toHaveBeenCalledWith(
      {
        text: note.text,
        scope: "all-apps",
        request_id: expect.any(String),
      },
      // The trace id rides along so the ui-action line and Core's
      // kernel-call/kernel-return lines share one id.
      expect.any(String),
    ),
  );
  expect(screen.getByText("已保存，正在后台沉淀记忆")).toBeTruthy();
  expect(callbacks.onSaved).toHaveBeenCalledOnce();
  expect(screen.queryByRole("dialog", { name: "添加记忆" })).toBeNull();
  expect(screen.queryByRole("button", { name: "查看记忆" })).toBeNull();
});

it("preserves draft and request identity after an uncertain save", async () => {
  api.saveMemoryNote
    .mockRejectedValueOnce(new Error("timeout"))
    .mockResolvedValueOnce(note);
  const emitted: Array<{ phase: string; status?: string; reason?: string }> =
    [];
  window.oneMemory = {
    logUiTrace: (input: {
      phase: string;
      status?: string;
      reason?: string;
    }) => {
      emitted.push(input);
      return Promise.resolve({ state: "logged" });
    },
  } as unknown as NonNullable<typeof window.oneMemory>;
  page();
  await userEvent.click(screen.getByRole("button", { name: "添加记忆" }));
  await userEvent.type(screen.getByRole("textbox"), note.text);
  await userEvent.click(screen.getByRole("button", { name: "保存原话" }));
  expect(await screen.findByRole("alert")).toBeTruthy();
  expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe(
    note.text,
  );
  // A plain timeout error is not proof that Core refused the write: the copy
  // says the result is unconfirmed, so the log must not say "failed".
  const result = emitted.find((item) => item.phase === "result");
  expect(result?.status).toBe("unknown");
  expect(result?.reason).toBe("save_result_unconfirmed");
  expect(result?.status).not.toBe("failed");
  await userEvent.click(screen.getByRole("button", { name: "保存原话" }));
  expect(api.saveMemoryNote.mock.calls[0][0].request_id).toBe(
    api.saveMemoryNote.mock.calls[1][0].request_id,
  );
  delete (window as unknown as { oneMemory?: unknown }).oneMemory;
});

it("closes as saved when the POST response is lost but the operation exists", async () => {
  api.saveMemoryNote.mockRejectedValueOnce(new Error("timeout"));
  api.getMemoryNoteOperation.mockResolvedValueOnce({
    ...note,
    state: "pending",
  });
  const callbacks = page();
  await userEvent.click(screen.getByRole("button", { name: "添加记忆" }));
  await userEvent.type(screen.getByRole("textbox"), note.text);
  await userEvent.click(screen.getByRole("button", { name: "保存原话" }));
  await waitFor(() =>
    expect(api.getMemoryNoteOperation).toHaveBeenCalledOnce(),
  );
  const requestId = api.saveMemoryNote.mock.calls[0][0].request_id;
  expect(api.getMemoryNoteOperation).toHaveBeenCalledWith(requestId);
  expect(screen.getByText("已保存，正在后台沉淀记忆")).toBeTruthy();
  expect(screen.queryByRole("dialog", { name: "添加记忆" })).toBeNull();
  expect(callbacks.onSaved).toHaveBeenCalledOnce();
});

it("opens a persisted result in the existing memory editor", async () => {
  api.getMemoryNotes.mockResolvedValue([
    { ...note, state: "ready", memory_ids: ["memory-7"] },
  ]);
  const callbacks = page();
  await userEvent.click(screen.getByRole("button", { name: "添加记忆" }));
  await userEvent.click(
    await screen.findByRole("button", { name: "查看记忆" }),
  );
  expect(callbacks.onOpenMemory).toHaveBeenCalledWith("memory-7");
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("closes with Escape and restores the trigger focus", async () => {
  page();
  const trigger = screen.getByRole("button", { name: "添加记忆" });
  await userEvent.click(trigger);
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.activeElement).toBe(trigger);
});

it("refreshes the workbench once asynchronous extraction becomes ready", async () => {
  api.getMemoryNotes
    .mockResolvedValueOnce([{ ...note, state: "running" }])
    .mockResolvedValueOnce([{ ...note, state: "running" }]);
  api.getMemoryNotes.mockResolvedValue([
    { ...note, state: "ready", memory_ids: ["new-memory"] },
  ]);
  const callbacks = page();
  await userEvent.click(screen.getByRole("button", { name: "添加记忆" }));
  expect(callbacks.onSaved).not.toHaveBeenCalled();
  await waitFor(() => expect(callbacks.onSaved).toHaveBeenCalledOnce(), {
    timeout: 2500,
  });
  expect(await screen.findByRole("button", { name: "查看记忆" })).toBeTruthy();
});

it("does not ask for separate consent when an old note is waiting for permission", async () => {
  api.getMemoryNotes.mockResolvedValue([
    { ...note, state: "waiting_for_permission" },
  ]);
  api.processMemoryNote.mockResolvedValue({ ...note, state: "pending" });
  page();
  await userEvent.click(screen.getByRole("button", { name: "添加记忆" }));

  expect(await screen.findByText("原话已保存，等待整理")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "模型授权" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "重新整理" }));
  expect(api.processMemoryNote).toHaveBeenCalledExactlyOnceWith(
    note.evidence_id,
    expect.any(String),
  );
});

it("preserves ordinary processing-failure retry without opening model consent", async () => {
  api.getMemoryNotes.mockResolvedValue([
    { ...note, state: "failed", error: "hy_memory_model_payment_required" },
  ]);
  api.processMemoryNote.mockResolvedValue({ ...note, state: "pending" });
  page();
  await userEvent.click(screen.getByRole("button", { name: "添加记忆" }));
  await userEvent.click(
    await screen.findByRole("button", { name: "重新整理" }),
  );
  expect(api.processMemoryNote).toHaveBeenCalledExactlyOnceWith(
    note.evidence_id,
    expect.any(String),
  );
  expect(api.getMemoryNoteEvidence).not.toHaveBeenCalled();
});
