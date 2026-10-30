// @vitest-environment jsdom
import type { MemoryAddReceipt } from "@one-memory/contracts";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "./gui-language";
import { MemoryAddComposer, RECENT_ADDITIONS_KEY } from "./MemoryAddComposer";

it("opens a Spanish composer without saving, processing or enabling anything", async () => {
  delete window.oneMemory;
  localStorage.setItem(LANGUAGE_STORAGE_KEY, "es");
  render(
    <LanguageProvider>
      <MemoryAddComposer
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
    (screen.getByRole("button", { name: "Guardar" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  await userEvent.click(
    screen.getByRole("button", { name: "Cerrar nueva memoria" }),
  );
  expect(api.addMemory).not.toHaveBeenCalled();
  localStorage.clear();
});

const api = vi.hoisted(() => ({
  getMemoryAddStatus: vi.fn(),
  getModelProfiles: vi.fn(),
  addMemory: vi.fn(),
  apiErrorCode: vi.fn(),
  getSources: vi.fn(),
}));
vi.mock("./api", () => api);

const REQUEST_ID = "11111111-2222-4333-8444-555555555555";
const addition: MemoryAddReceipt = {
  evidence_id: "evidence-1",
  text: "项目周会不排在周五。",
  scope: "all-apps",
  created_at: "2026-09-12T00:00:00Z",
  state: "waiting_for_model",
  task_id: null,
  memory_ids: [],
  error: null,
};
/** This device remembers one earlier addition whose status reads as `receipt`. */
function remembered(...receipts: MemoryAddReceipt[]) {
  localStorage.setItem(RECENT_ADDITIONS_KEY, JSON.stringify([REQUEST_ID]));
  for (const receipt of receipts)
    api.getMemoryAddStatus.mockResolvedValueOnce(receipt);
  if (receipts.length)
    api.getMemoryAddStatus.mockResolvedValue(receipts.at(-1));
}

beforeEach(() => {
  vi.resetAllMocks();
  localStorage.removeItem(RECENT_ADDITIONS_KEY);
  api.getMemoryAddStatus.mockRejectedValue(new Error("not found"));
  api.getModelProfiles.mockResolvedValue([]);
  api.addMemory.mockResolvedValue(addition);
  api.apiErrorCode.mockImplementation(
    (reason) => reason?.response?.data?.detail ?? null,
  );
});
afterEach(() => {
  cleanup();
  localStorage.removeItem(RECENT_ADDITIONS_KEY);
});

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
  remembered({
    ...addition,
    state: "failed",
    error: "hy_memory_write_verification_historical_support_only",
  });
  page();
  await userEvent.click(screen.getByRole("button", { name: "添加记忆" }));
  expect(
    await screen.findByText(
      "历史引用尚未绑定到原始来源，当前批次未完成，原话已保留。",
    ),
  ).toBeTruthy();
  expect(screen.queryByText("整理已完成")).toBeNull();
  expect(api.getMemoryAddStatus).toHaveBeenCalledWith(REQUEST_ID);
  expect(screen.queryByRole("button", { name: "重新整理" })).toBeNull();
});

function page() {
  const onSaved = vi.fn(),
    onOpenMemory = vi.fn(),
    onConfigureModel = vi.fn(),
    onPendingChange = vi.fn();
  const view = render(
    <MemoryAddComposer
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

it("reports a saved addition that still needs processing when the panel is closed", async () => {
  remembered(addition);
  const callbacks = page();
  await waitFor(() =>
    expect(callbacks.onPendingChange).toHaveBeenCalledWith({
      evidenceId: addition.evidence_id,
      text: addition.text,
      state: "waiting_for_model",
    }),
  );
  await userEvent.click(screen.getByRole("button", { name: "添加记忆" }));
  await waitFor(() =>
    expect(callbacks.onPendingChange).toHaveBeenCalledWith({
      evidenceId: addition.evidence_id,
      text: addition.text,
      state: "waiting_for_model",
    }),
  );
  await userEvent.keyboard("{Escape}");
  expect(callbacks.onPendingChange).toHaveBeenLastCalledWith({
    evidenceId: addition.evidence_id,
    text: addition.text,
    state: "waiting_for_model",
  });
});

it("saves from an empty library with the linked-app scope by default", async () => {
  const callbacks = page();
  await userEvent.click(screen.getByRole("button", { name: "添加记忆" }));
  const input = screen.getByRole("textbox", { name: "记下这件事" });
  expect(document.activeElement).toBe(input);
  await userEvent.type(input, addition.text);
  expect(
    screen.getByRole("button", { name: "保存" }).hasAttribute("disabled"),
  ).toBe(false);
  expect(
    screen.queryByRole("checkbox", { name: /允许已关联应用读取/ }),
  ).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "保存" }));
  await waitFor(() =>
    expect(api.addMemory).toHaveBeenCalledWith(
      {
        text: addition.text,
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
  api.addMemory
    .mockRejectedValueOnce(new Error("timeout"))
    .mockResolvedValueOnce(addition);
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
  await userEvent.type(screen.getByRole("textbox"), addition.text);
  await userEvent.click(screen.getByRole("button", { name: "保存" }));
  expect(await screen.findByRole("alert")).toBeTruthy();
  expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe(
    addition.text,
  );
  // A plain timeout error is not proof that Core refused the write: the copy
  // says the result is unconfirmed, so the log must not say "failed".
  const result = emitted.find((item) => item.phase === "result");
  expect(result?.status).toBe("unknown");
  expect(result?.reason).toBe("save_result_unconfirmed");
  expect(result?.status).not.toBe("failed");
  await userEvent.click(screen.getByRole("button", { name: "保存" }));
  expect(api.addMemory.mock.calls[0][0].request_id).toBe(
    api.addMemory.mock.calls[1][0].request_id,
  );
  delete (window as unknown as { oneMemory?: unknown }).oneMemory;
});

it("closes as saved when the POST response is lost but the addition exists", async () => {
  api.addMemory.mockRejectedValueOnce(new Error("timeout"));
  api.getMemoryAddStatus.mockResolvedValueOnce({
    ...addition,
    state: "pending",
  });
  const callbacks = page();
  await userEvent.click(screen.getByRole("button", { name: "添加记忆" }));
  await userEvent.type(screen.getByRole("textbox"), addition.text);
  await userEvent.click(screen.getByRole("button", { name: "保存" }));
  await waitFor(() => expect(api.getMemoryAddStatus).toHaveBeenCalled());
  const requestId = api.addMemory.mock.calls[0][0].request_id;
  // The recovery read asks for exactly this request's addition.
  expect(api.getMemoryAddStatus.mock.calls[0]).toEqual([requestId]);
  // The recovered addition is remembered for the recent list.
  expect(
    JSON.parse(localStorage.getItem(RECENT_ADDITIONS_KEY) ?? "[]"),
  ).toEqual([requestId]);
  expect(screen.getByText("已保存，正在后台沉淀记忆")).toBeTruthy();
  expect(screen.queryByRole("dialog", { name: "添加记忆" })).toBeNull();
  expect(callbacks.onSaved).toHaveBeenCalledOnce();
});

it("opens a persisted result in the existing memory editor", async () => {
  remembered({ ...addition, state: "ready", memory_ids: ["memory-7"] });
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
  remembered(
    { ...addition, state: "running" },
    { ...addition, state: "running" },
    { ...addition, state: "ready", memory_ids: ["new-memory"] },
  );
  const callbacks = page();
  await userEvent.click(screen.getByRole("button", { name: "添加记忆" }));
  expect(callbacks.onSaved).not.toHaveBeenCalled();
  await waitFor(() => expect(callbacks.onSaved).toHaveBeenCalledOnce(), {
    timeout: 2500,
  });
  expect(await screen.findByRole("button", { name: "查看记忆" })).toBeTruthy();
});

it("shows an old addition waiting for permission without consent or a manual retry", async () => {
  remembered({ ...addition, state: "waiting_for_permission" });
  page();
  await userEvent.click(screen.getByRole("button", { name: "添加记忆" }));

  expect(await screen.findByText("正在等待整理")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "模型授权" })).toBeNull();
  // Processing is not a separate write: activating a model readmits parked
  // work, so there is no "process again" control on an addition.
  expect(screen.queryByRole("button", { name: "重新整理" })).toBeNull();
});

it("forgets a remembered addition Core no longer knows", async () => {
  localStorage.setItem(RECENT_ADDITIONS_KEY, JSON.stringify([REQUEST_ID]));
  api.getMemoryAddStatus.mockRejectedValue({ response: { status: 404 } });
  page();
  await userEvent.click(screen.getByRole("button", { name: "添加记忆" }));
  await waitFor(() =>
    expect(localStorage.getItem(RECENT_ADDITIONS_KEY)).toBe("[]"),
  );
  expect(screen.queryByRole("region", { name: "最近添加" })).toBeNull();
  expect(screen.queryByRole("alert")).toBeNull();
});

it("keeps remembered additions when a status read fails for another reason", async () => {
  localStorage.setItem(RECENT_ADDITIONS_KEY, JSON.stringify([REQUEST_ID]));
  api.getMemoryAddStatus.mockRejectedValue({ response: { status: 503 } });
  page();
  await userEvent.click(screen.getByRole("button", { name: "添加记忆" }));
  expect(await screen.findByRole("alert")).toBeTruthy();
  expect(
    JSON.parse(localStorage.getItem(RECENT_ADDITIONS_KEY) ?? "[]"),
  ).toEqual([REQUEST_ID]);
});
