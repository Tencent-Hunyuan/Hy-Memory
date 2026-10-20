// @vitest-environment jsdom

import type {
  SourceDirectoryCandidate,
  SourceResponse,
} from "@one-memory/contracts";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  SourceDirectoryPicker,
  type SourceDirectoryPickerProps,
} from "./SourceDirectoryPicker";
import { setUiLanguage } from "./ui-language";

const api = vi.hoisted(() => ({
  preview: vi.fn(),
  save: vi.fn(),
  pick: vi.fn(),
}));
vi.mock("./api", () => ({
  previewSourceDirectory: api.preview,
  saveSourceDirectory: api.save,
}));

function source(overrides: Partial<SourceResponse> = {}): SourceResponse {
  return {
    source_id: "cursor",
    display_name: "Cursor",
    storage_kinds: ["jsonl"],
    available: false,
    authorized: false,
    detected: null,
    root_label: null,
    candidate_path: "~/.cursor",
    candidate_kind: "default",
    candidate_revision: 0,
    candidate_change_allowed: true,
    candidate_change_reason: null,
    imported_messages: 0,
    imported_conversations: 0,
    capabilities: {
      historical_import: "implemented",
      incremental_import: "implemented",
      live_sync: "implemented",
      recall: "implemented",
      installer: "implemented",
      verification: "fixture_only",
    },
    sync_enabled: false,
    sync_completeness: "additions_only",
    location_state: "not_found",
    candidate_root_id: null,
    ...overrides,
  };
}
const preview: SourceDirectoryCandidate = {
  source_id: "cursor",
  path: "/isolated/custom/cursor",
  root_id: "chosen-root",
  candidate_revision: 0,
};
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
function setup(overrides: Partial<SourceDirectoryPickerProps> = {}) {
  let props: SourceDirectoryPickerProps = {
    source: source(),
    onSaved: vi.fn().mockResolvedValue(undefined),
    onBusyChange: vi.fn(),
    ...overrides,
  };
  const view = render(<SourceDirectoryPicker {...props} />);
  return {
    ...view,
    props,
    update(change: Partial<SourceDirectoryPickerProps>) {
      props = { ...props, ...change };
      view.rerender(<SourceDirectoryPicker {...props} />);
    },
  };
}
const choose = () =>
  screen.getByRole<HTMLButtonElement>("button", {
    name: "选择 Cursor 历史目录",
  });
const consent = () =>
  screen.getByRole<HTMLInputElement>("checkbox", {
    name: "仅保存此候选位置，不授权读取历史",
  });
const save = () =>
  screen.getByRole<HTMLButtonElement>("button", { name: "保存候选位置" });
async function review() {
  await userEvent.click(choose());
  await screen.findByRole("dialog");
  await screen.findByRole("checkbox");
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.stubGlobal("oneMemory", {
    pickSourceDirectory: api.pick,
    setUiLanguage: vi.fn(async (next: "zh-CN" | "en") => next),
  });
  vi.stubGlobal("matchMedia", () => ({
    matches: true,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }));
  api.pick.mockResolvedValue({
    cancelled: false,
    path: "/isolated/custom/../custom/cursor",
  });
  api.preview.mockResolvedValue(preview);
  api.save.mockResolvedValue({ ...preview, candidate_revision: 1 });
});
afterEach(async () => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
  await setUiLanguage("zh-CN");
});

describe("SourceDirectoryPicker COLD04", () => {
  it("translates directory consent while preserving the chosen path, source name and read-only preview", async () => {
    setup();
    await act(async () => {
      await setUiLanguage("en");
    });
    await userEvent.click(
      screen.getByRole("button", {
        name: "Choose the history directory for Cursor",
      }),
    );
    expect((await screen.findByRole("dialog")).textContent).toContain(
      "Save candidate location for Cursor",
    );
    expect(screen.getByText(preview.path)).toBeTruthy();
    expect(
      await screen.findByRole("checkbox", {
        name: "Save this candidate location only; do not grant history access",
      }),
    ).toBeTruthy();
    expect(api.preview).toHaveBeenCalledWith(
      "cursor",
      "/isolated/custom/../custom/cursor",
    );
    expect(api.save).not.toHaveBeenCalled();
  });
  it("only exposes buttons for the four allowed sources", () => {
    for (const source_id of [
      "workbuddy",
      "cursor",
      "claude-code",
      "codex",
      "claude",
      "openclaw",
      "pi",
      "chatgpt-export",
    ]) {
      const view = setup({ source: source({ source_id }) });
      expect(view.container.querySelector("button") !== null).toBe(
        ["workbuddy", "cursor", "claude-code", "codex"].includes(source_id),
      );
      view.unmount();
    }
    expect(api.pick).not.toHaveBeenCalled();
  });

  it("native cancellation makes no Core call or parent selection change", async () => {
    api.pick.mockResolvedValue({ cancelled: true, path: null });
    const { props } = setup();
    await userEvent.click(choose());
    expect(api.pick.mock.calls).toEqual([["cursor"]]);
    expect(api.preview).not.toHaveBeenCalled();
    expect(api.save).not.toHaveBeenCalled();
    expect(props.onSaved).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(props.onBusyChange).toHaveBeenLastCalledWith(false);
  });

  it("previews the selected path and requires separate consent for the normalized candidate", async () => {
    setup();
    await review();
    expect(api.preview.mock.calls).toEqual([
      ["cursor", "/isolated/custom/../custom/cursor"],
    ]);
    expect(screen.getByRole("dialog").textContent).toContain(preview.path);
    expect(screen.getByRole("dialog").textContent).toContain("持续同步");
    expect(screen.getByRole("dialog").textContent).toContain("模型");
    expect(screen.getByRole("dialog").textContent).toContain("安装");
    expect(consent().checked).toBe(false);
    expect(save().disabled).toBe(true);
    expect(api.save).not.toHaveBeenCalled();
  });

  it("saves the exact preview CAS once, then refreshes without authorizing anything", async () => {
    const { props } = setup();
    await review();
    await userEvent.click(consent());
    await userEvent.click(save());
    expect(api.save.mock.calls).toEqual([
      [
        "cursor",
        {
          path: preview.path,
          expected_revision: 0,
          expected_root_id: preview.root_id,
          user_confirmed: true,
        },
        // The trace id rides along so the PUT's kernel legs join the
        // renderer's ui.action/ui.result pair.
        expect.any(String),
      ],
    ]);
    expect(props.onSaved).toHaveBeenCalledOnce();
    expect(api.save.mock.invocationCallOrder[0]).toBeLessThan(
      vi.mocked(props.onSaved).mock.invocationCallOrder[0],
    );
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("accepts the Core no-op receipt when selecting the same saved root again", async () => {
    const existing = { ...preview, candidate_revision: 2 };
    api.preview.mockResolvedValue(existing);
    api.save.mockResolvedValue(existing);
    const { props } = setup({
      source: source({
        candidate_kind: "user_selected",
        candidate_revision: 2,
        candidate_path: preview.path,
        candidate_root_id: preview.root_id,
      }),
    });
    await review();
    await userEvent.click(consent());
    await userEvent.click(save());
    expect(props.onSaved).toHaveBeenCalledOnce();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(api.save).toHaveBeenCalledOnce();
  });

  it.each(["escape", "cancel"])(
    "%s closes the preview without saving and restores focus",
    async (method) => {
      setup();
      const trigger = choose();
      await review();
      await userEvent.click(consent());
      if (method === "escape") await userEvent.keyboard("{Escape}");
      else await userEvent.click(screen.getByRole("button", { name: "取消" }));
      expect(api.save).not.toHaveBeenCalled();
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(document.activeElement).toBe(trigger);
      await review();
      expect(consent().checked).toBe(false);
    },
  );

  it("supports preview failure and readonly retry without saving", async () => {
    api.preview.mockRejectedValueOnce(
      new Error("source_candidate_unsafe_root"),
    );
    setup();
    await userEvent.click(choose());
    expect((await screen.findByRole("alert")).textContent).toContain("目录");
    expect(screen.queryByRole("checkbox")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "重新核对目录" }));
    expect(api.preview).toHaveBeenCalledTimes(2);
    expect(consent().checked).toBe(false);
    expect(api.save).not.toHaveBeenCalled();
  });

  it("retains a committed save when refresh fails and retries only refresh", async () => {
    const onSaved = vi
      .fn()
      .mockRejectedValueOnce(new Error("refresh failed"))
      .mockResolvedValueOnce(undefined);
    setup({ onSaved });
    await review();
    await userEvent.click(consent());
    await userEvent.click(save());
    expect(screen.getByRole("alert").textContent).toContain("候选位置已保存");
    expect(screen.queryByRole("checkbox")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "刷新来源状态" }));
    expect(api.save).toHaveBeenCalledOnce();
    expect(onSaved).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("does not blindly save again after an unknown save failure", async () => {
    api.save.mockRejectedValueOnce(new Error("response lost"));
    const { props } = setup();
    await review();
    await userEvent.click(consent());
    await userEvent.click(save());
    expect(screen.getByRole("alert").textContent).toContain("保存结果待核对");
    expect(screen.queryByRole("button", { name: "保存候选位置" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "刷新来源状态" }));
    expect(props.onSaved).toHaveBeenCalledOnce();
    expect(api.save).toHaveBeenCalledOnce();
    expect(screen.queryByText("候选位置已保存")).toBeNull();
  });

  it("opens a chain for the folder save, and never logs failed while the copy says unconfirmed", async () => {
    // Saving the folder is a real Core write: without its own ui.action/ui.result
    // pair the PUT's kernel legs are orphaned. And because the copy below says
    // the save is *unconfirmed*, a plain transport error must close the trace as
    // `unknown` — only a definite refusal may say `failed`.
    const emitted: Array<{
      phase: string;
      status?: string;
      reason?: string;
      trace_id?: string;
    }> = [];
    vi.stubGlobal("oneMemory", {
      pickSourceDirectory: api.pick,
      logUiTrace: (input: {
        phase: string;
        status?: string;
        reason?: string;
      }) => {
        emitted.push(input);
        return Promise.resolve({ state: "logged" });
      },
    });
    api.save.mockRejectedValueOnce(new Error("response lost"));
    setup();
    await review();
    await userEvent.click(consent());
    await userEvent.click(save());
    expect(await screen.findByRole("alert")).toBeTruthy();
    const result = emitted.find((item) => item.phase === "result");
    expect(result?.status).toBe("unknown");
    expect(result?.reason).toBe("source_directory_save_unconfirmed");
    expect(result?.status).not.toBe("failed");
    // The id the action opened with must be the one handed to the write.
    expect(api.save.mock.calls[0][2]).toBe(
      emitted.find((item) => item.phase === "action")?.trace_id,
    );
  });

  it.each([
    { candidate_revision: 1 },
    { candidate_path: "/another" },
    { candidate_root_id: "changed" },
    { candidate_kind: "environment_override" as const },
    { candidate_change_allowed: false },
    { authorized: true },
    { imported_messages: 1 },
    { source_id: "codex" },
  ])(
    "invalidates an open preview after scope changes, even A-B-A: %j",
    async (change) => {
      const { update } = setup();
      await review();
      await userEvent.click(consent());
      update({ source: source(change) });
      expect(save().disabled).toBe(true);
      expect(consent().checked).toBe(false);
      expect(screen.getByRole("alert").textContent).toContain("重新选择");
      update({ source: source() });
      fireEvent.click(save());
      expect(api.save).not.toHaveBeenCalled();
      expect(save().disabled).toBe(true);
    },
  );

  it("ignores native result after unmount or changed source before preview", async () => {
    const pending = deferred<{ cancelled: boolean; path: string }>();
    api.pick.mockReturnValue(pending.promise);
    const { update, unmount } = setup();
    fireEvent.click(choose());
    update({ source: source({ candidate_revision: 1 }) });
    await act(async () =>
      pending.resolve({ cancelled: false, path: preview.path }),
    );
    expect(api.preview).not.toHaveBeenCalled();
    unmount();
    expect(api.save).not.toHaveBeenCalled();
  });

  it("ignores late preview after cancellation and cannot affect a later session", async () => {
    const pending = deferred<SourceDirectoryCandidate>();
    api.preview.mockReturnValueOnce(pending.promise);
    setup();
    await userEvent.click(choose());
    await screen.findByRole("dialog");
    await userEvent.keyboard("{Escape}");
    await review();
    await act(async () => pending.resolve({ ...preview, path: "/old/late" }));
    expect(screen.getByRole("dialog").textContent).not.toContain("/old/late");
    expect(api.save).not.toHaveBeenCalled();
  });

  it("locks native picking and save single-flight and blocks busy dismissal", async () => {
    const picking = deferred<{ cancelled: boolean; path: string }>();
    api.pick.mockReturnValueOnce(picking.promise);
    setup();
    const trigger = choose();
    act(() => {
      fireEvent.click(trigger);
      fireEvent.click(trigger);
    });
    expect(api.pick).toHaveBeenCalledOnce();
    await act(async () =>
      picking.resolve({ cancelled: false, path: preview.path }),
    );
    const saving = deferred<SourceDirectoryCandidate>();
    api.save.mockReturnValueOnce(saving.promise);
    await userEvent.click(consent());
    const confirm = save();
    act(() => {
      fireEvent.click(confirm);
      fireEvent.click(confirm);
    });
    expect(api.save).toHaveBeenCalledOnce();
    expect(screen.getByRole("dialog").getAttribute("aria-busy")).toBe("true");
    await userEvent.keyboard("{Escape}{Tab}");
    expect(document.activeElement).toBe(screen.getByRole("dialog"));
    await act(async () =>
      saving.resolve({ ...preview, candidate_revision: 1 }),
    );
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("does not call refresh after unmount during a save", async () => {
    const pending = deferred<SourceDirectoryCandidate>();
    api.save.mockReturnValue(pending.promise);
    const { props, unmount } = setup();
    await review();
    await userEvent.click(consent());
    fireEvent.click(save());
    unmount();
    await act(async () =>
      pending.resolve({ ...preview, candidate_revision: 1 }),
    );
    expect(props.onSaved).not.toHaveBeenCalled();
  });

  it("does not run the new source callback for a late save receipt from the old source", async () => {
    const pending = deferred<SourceDirectoryCandidate>();
    api.save.mockReturnValue(pending.promise);
    const { props, update } = setup();
    await review();
    await userEvent.click(consent());
    fireEvent.click(save());
    const newRefresh = vi.fn().mockResolvedValue(undefined);
    update({
      source: source({ source_id: "codex", display_name: "Codex" }),
      onSaved: newRefresh,
    });
    await act(async () =>
      pending.resolve({ ...preview, candidate_revision: 1 }),
    );
    expect(props.onSaved).not.toHaveBeenCalled();
    expect(newRefresh).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toContain("候选位置已保存");
    expect(screen.getByRole("alert").textContent).toContain("来源状态已变化");
    expect(api.save).toHaveBeenCalledOnce();
    expect(screen.queryByRole("button", { name: "保存候选位置" })).toBeNull();
  });

  it.each([
    [
      {
        candidate_change_allowed: false,
        candidate_change_reason: "source_has_history",
      },
      "已有历史",
    ],
    [{ candidate_kind: "environment_override" }, "环境变量"],
    [{ candidate_change_allowed: undefined }, "Core"],
    [{ authorized: true }, "已有历史"],
  ] satisfies [Partial<SourceResponse>, string][])(
    "explains why changing a source is blocked: %j",
    async (change, explanation) => {
      setup({ source: source(change) });
      expect(choose().disabled).toBe(false);
      expect(screen.queryByText(new RegExp(explanation))).toBeNull();
      await userEvent.click(choose());
      expect(screen.getByText(new RegExp(explanation))).toBeTruthy();
      expect(api.pick).not.toHaveBeenCalled();
    },
  );

  it("old shells display real unavailability without a fake file chooser", () => {
    vi.stubGlobal("oneMemory", undefined);
    setup();
    expect(choose().disabled).toBe(true);
    expect(screen.getByText(/当前桌面版本不支持目录选择/)).toBeTruthy();
    expect(document.querySelector('input[type="file"]')).toBeNull();
  });

  it.each([
    null,
    { ...preview, source_id: "codex" },
    { ...preview, path: "relative" },
    { ...preview, candidate_revision: -1 },
    { ...preview, candidate_revision: "0" },
    { ...preview, candidate_revision: false },
    { ...preview, root_id: "" },
  ])("rejects an invalid preview: %j", async (invalid) => {
    api.preview.mockResolvedValue(invalid);
    setup();
    await userEvent.click(choose());
    await screen.findByRole("alert");
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(api.save).not.toHaveBeenCalled();
  });

  it("traps focus, preserves existing inert values and restores them on cancellation", async () => {
    const outside = document.createElement("button");
    outside.textContent = "outside";
    const inert = document.createElement("div");
    inert.inert = true;
    inert.setAttribute("inert", "");
    document.body.append(outside, inert);
    setup();
    const trigger = choose();
    await review();
    expect(outside.inert).toBe(true);
    expect(screen.getByRole("dialog").contains(document.activeElement)).toBe(
      true,
    );
    const controls = within(screen.getByRole("dialog")).getAllByRole("button");
    controls[0].focus();
    await userEvent.tab({ shift: true });
    expect(document.activeElement).toBe(
      screen.getByRole("button", { name: "取消" }),
    );
    outside.focus();
    expect(screen.getByRole("dialog").contains(document.activeElement)).toBe(
      true,
    );
    await userEvent.keyboard("{Escape}");
    expect(outside.inert).toBe(false);
    expect(inert.inert).toBe(true);
    expect(inert.hasAttribute("inert")).toBe(true);
    expect(document.activeElement).toBe(trigger);
  });

  it("keeps the exit for 160ms, with no save possible while closing", async () => {
    vi.stubGlobal("matchMedia", () => ({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }));
    setup();
    await review();
    vi.useFakeTimers();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.getByRole("dialog").getAttribute("data-closing")).toBe(
      "true",
    );
    act(() => vi.advanceTimersByTime(159));
    expect(screen.getByRole("dialog")).toBeTruthy();
    act(() => vi.advanceTimersByTime(1));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(api.save).not.toHaveBeenCalled();
  });
});
