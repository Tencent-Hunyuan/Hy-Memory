// @vitest-environment jsdom

import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Ref } from "react";
import { afterEach, expect, it, vi } from "vitest";
import {
  getMemoryMode,
  getModelProfiles,
  getSedimentation,
  planMemoryMode,
  setSedimentation,
} from "./api";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "./gui-language";
import { queryClient, resetClient } from "./query-client";
import { R6Settings } from "./R6Settings";

const editProfileAction = vi.fn(() => true);
const addProfileAction = vi.fn(() => true);

it("changes settings copy live without rereading profiles or changing permissions", async () => {
  const user = userEvent.setup();
  localStorage.setItem(LANGUAGE_STORAGE_KEY, "es");
  const { getModelProfiles } = await import("./api");
  render(
    <LanguageProvider>
      <R6Settings onDiagnostics={vi.fn()} />
    </LanguageProvider>,
  );
  expect(
    await screen.findByRole("heading", { name: "Modo de memoria" }),
  ).toBeTruthy();
  expect(
    screen.queryByRole("switch", {
      name: "Permitir que el modelo configurado lea las conversaciones locales",
    }),
  ).toBeNull();
  const reads = vi.mocked(getModelProfiles).mock.calls.length;
  await user.click(
    screen.getByRole("button", { name: "Idioma de la interfaz" }),
  );
  await user.click(screen.getByRole("combobox", { name: "Idioma" }));
  await user.click(screen.getByRole("option", { name: "简体中文" }));
  await user.click(screen.getByRole("button", { name: "模型配置" }));
  expect(await screen.findByRole("heading", { name: "模型配置" })).toBeTruthy();
  expect(vi.mocked(getModelProfiles).mock.calls).toHaveLength(reads);
});
vi.mock("./api", () => ({
  getModelProfiles: vi.fn().mockResolvedValue([
    {
      profile_id: "profile-1",
      name: "当前模型",
      model: "model-1",
      base_url: "https://example.test/v1",
      is_active: true,
    },
  ]),
  getSedimentation: vi.fn().mockResolvedValue({
    enabled: true,
    pending_retained: true,
    imports_unaffected: true,
  }),
  setSedimentation: vi.fn(),
  getMemoryMode: vi.fn().mockResolvedValue({
    mode: "pro",
    configured_mode: "pro",
    explicitly_configured: true,
    forced_by_env: false,
    modes: ["lite", "pro", "ultra"],
    graph_build: {
      state: "idle",
      started_at: null,
      finished_at: null,
      users: {},
    },
  }),
  setMemoryMode: vi.fn(),
  getEvolveSchedule: vi.fn().mockResolvedValue({
    enabled: true,
    time: "03:00",
  }),
  setEvolveSchedule: vi.fn(),
  getEvolveOverview: vi.fn().mockResolvedValue({
    mode: "pro",
    schedule: { enabled: true, time: "03:00" },
    users: {},
    running: [],
  }),
  startEvolve: vi.fn(),
  getMemoryProcessing: vi.fn().mockResolvedValue({
    embedding_switch: null,
  }),
  planMemoryMode: vi.fn(),
  memoryModePlanUnsupported: vi.fn().mockReturnValue(false),
  apiErrorCode: vi.fn().mockReturnValue(null),
}));
vi.mock("./ModelSettings", async () => {
  const { createElement, useImperativeHandle } = await import("react");
  return {
    // Stands in for the real component at its worst: every mutating action is
    // fenced, and the only way out is the recovery entry that lives inside the
    // same area the fence used to keep shut.
    ModelSettings: ({
      controlsRef,
      focusSection,
    }: {
      controlsRef?: Ref<unknown>;
      focusSection?: "generation" | "embedding";
    }) => {
      useImperativeHandle(controlsRef, () => ({
        edit: () => false,
        editProfile: editProfileAction,
        add: addProfileAction,
        test: () => Promise.resolve(undefined),
      }));
      return createElement(
        "div",
        null,
        createElement("span", null, focusSection),
        createElement("button", { type: "button" }, "核对测试结果"),
      );
    },
  };
});
vi.mock("./ConnectionFallback", () => ({
  ConnectionFallback: ({ defaultExpanded }: { defaultExpanded?: boolean }) => (
    <p data-testid="connection-default">{String(defaultExpanded)}</p>
  ),
}));
vi.mock("./runtime-state", () => ({
  refreshLoginPreference: vi.fn(),
  setLoginPreference: vi.fn(),
  useLoginPreference: () => null,
}));
vi.mock("./diagnostics-export", () => ({
  exportDiagnosticsFile: vi.fn(),
}));
afterEach(() => {
  cleanup();
  localStorage.clear();
  resetClient();
  vi.clearAllMocks();
  window.oneMemory = undefined;
});

it("shows the help and diagnostics group by default and hides it when opted out", async () => {
  window.oneMemory = {
    getBuildInfo: () => ({
      version: "1.0.4",
      buildNumber: "8",
      commit: "1234567890abcdef",
    }),
  } as NonNullable<typeof window.oneMemory>;
  const user = userEvent.setup();
  const view = render(<R6Settings onDiagnostics={vi.fn()} />);
  await user.click(screen.getByRole("button", { name: "界面语言" }));
  expect(screen.getByRole("combobox", { name: "语言" })).toBeTruthy();
  await user.click(screen.getByRole("combobox", { name: "语言" }));
  expect(screen.getAllByRole("option")).toHaveLength(12);
  await user.keyboard("{Escape}");
  await user.click(screen.getByRole("button", { name: "连接其他应用" }));
  expect(screen.getByTestId("connection-default").textContent).toBe("true");
  await user.click(screen.getByRole("button", { name: "帮助与诊断" }));
  expect(screen.getByRole("heading", { name: "帮助与诊断" })).toBeTruthy();
  expect(screen.getByRole("heading", { name: "导出日志" })).toBeTruthy();
  expect(screen.getByText(/可能包含会话正文和本机路径/)).toBeTruthy();
  expect(screen.getByRole("heading", { name: "关于" })).toBeTruthy();
  expect(screen.getByText("1.0.4")).toBeTruthy();
  expect(screen.getByText("8")).toBeTruthy();
  expect(screen.getByText("1234567890ab")).toBeTruthy();

  view.rerender(<R6Settings onDiagnostics={vi.fn()} showDiagnostics={false} />);
  expect(screen.queryByRole("button", { name: "帮助与诊断" })).toBeNull();
});

it("uses one sidebar hierarchy and explains when a saved model is inactive in lite", async () => {
  vi.mocked(getMemoryMode).mockResolvedValueOnce({
    mode: "lite",
    configured_mode: "lite",
    explicitly_configured: true,
    forced_by_env: false,
    modes: ["lite", "pro", "ultra"],
    graph_build: {
      state: "idle",
      started_at: null,
      finished_at: null,
      users: {},
    },
  });
  const user = userEvent.setup();
  render(<R6Settings onDiagnostics={vi.fn()} />);

  expect(screen.getByRole("button", { name: "界面语言" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "记忆模式" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "模型配置" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "刷新记忆模式" })).toBeNull();

  await user.click(screen.getByRole("button", { name: "界面语言" }));
  expect(screen.getByRole("combobox", { name: "语言" })).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "模型配置" }));
  expect(
    await screen.findByText(
      (_, element) =>
        element?.tagName === "STRONG" &&
        element.textContent === "基础记忆 · Lite",
    ),
  ).toBeTruthy();
  expect(
    screen.getByText(/是否开始整理取决于.*“用模型沉淀记忆”.*开关/),
  ).toBeTruthy();
  expect(screen.queryByRole("button", { name: "刷新模型状态" })).toBeNull();
});

it("carries a missing-model explanation into model configuration and moves focus", async () => {
  vi.mocked(getModelProfiles).mockResolvedValueOnce([]);
  vi.mocked(getMemoryMode).mockResolvedValueOnce({
    mode: "lite",
    configured_mode: "lite",
    explicitly_configured: true,
    forced_by_env: false,
    modes: ["lite", "pro", "ultra"],
    graph_build: {
      state: "idle",
      started_at: null,
      finished_at: null,
      users: {},
    },
  });
  vi.mocked(planMemoryMode).mockResolvedValueOnce({
    current: "lite",
    target: "pro",
    effective_target: "pro",
    capabilities: {
      content_plane: "memory",
      generation_required: true,
      graph_enabled: false,
      evolve_available: false,
    },
    effects: ["earlier_raw_not_extracted", "lite_search_stops"],
    warnings: [],
    blockers: ["active_model_required"],
  });
  const user = userEvent.setup();
  render(<R6Settings onDiagnostics={vi.fn()} />);

  await user.click(await screen.findByRole("combobox", { name: "记忆模式" }));
  await user.click(
    await screen.findByRole("option", { name: /智能记忆 · Pro/ }),
  );

  expect(
    await screen.findByText("使用 pro 或 ultra 前需要先配置并启用生成模型。"),
  ).toBeTruthy();
  const heading = screen.getByRole("heading", { name: "模型配置" });
  await waitFor(() => expect(document.activeElement).toBe(heading));
});

it("offers a text recovery action when the model overview cannot be read", async () => {
  vi.mocked(getModelProfiles).mockRejectedValueOnce(new Error("offline"));
  const user = userEvent.setup();
  render(<R6Settings onDiagnostics={vi.fn()} />);
  await user.click(screen.getByRole("button", { name: "模型配置" }));

  expect(await screen.findByText("模型状态未能读取，请重试。")).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "重新读取" }));

  expect(await screen.findByText("当前模型")).toBeTruthy();
});

it("stops model sedimentation on one switch flip without touching the model config", async () => {
  const user = userEvent.setup();
  vi.mocked(setSedimentation).mockResolvedValue({
    enabled: false,
    pending_retained: true,
    imports_unaffected: true,
  });
  render(<R6Settings onDiagnostics={vi.fn()} />);
  await user.click(screen.getByRole("button", { name: "模型配置" }));

  const toggle = await screen.findByRole("switch", { name: "用模型沉淀记忆" });
  expect(toggle.getAttribute("aria-checked")).toBe("true");
  await user.click(toggle);

  expect(setSedimentation).toHaveBeenCalledExactlyOnceWith(false);
  expect(queryClient.getQueryData(["sedimentation"])).toEqual({
    enabled: false,
    pending_retained: true,
    imports_unaffected: true,
  });
  await screen.findByText(/停止后不再调用模型/);
});

it("reports a stop that did not confirm instead of showing it stopped", async () => {
  const user = userEvent.setup();
  vi.mocked(setSedimentation).mockRejectedValue(new Error("offline"));
  render(<R6Settings onDiagnostics={vi.fn()} />);
  await user.click(screen.getByRole("button", { name: "模型配置" }));

  const toggle = await screen.findByRole("switch", { name: "用模型沉淀记忆" });
  await user.click(toggle);

  const alert = await screen.findByRole("alert");
  expect(alert.textContent).toContain(
    "停止未能确认，模型可能仍在整理记忆，请重试。",
  );
  // The model configuration is a different store: a failed switch must not
  // leave the page claiming the model was removed.
  expect(screen.getByText("当前模型")).toBeTruthy();
});

it("opens the model area even when a pending probe fences the editor, so recovery stays reachable", async () => {
  editProfileAction.mockReturnValueOnce(false);
  const user = userEvent.setup();
  render(<R6Settings onDiagnostics={vi.fn()} />);
  await user.click(screen.getByRole("button", { name: "模型配置" }));

  const generationLane = (
    await screen.findByRole("heading", { name: "记忆整理" })
  ).closest(".r6-model-lane") as HTMLElement | null;
  if (!generationLane) throw new Error("Missing generation lane");
  await user.click(
    within(generationLane).getByRole("button", { name: "配置" }),
  );

  // The fence is still reported: the editor really did refuse.
  const alert = await screen.findByRole("alert");
  expect(alert.textContent).toContain("待核对");
  // What changed is that the recovery entry no longer hides with the form it
  // is supposed to unlock.
  expect(
    within(generationLane).getByRole("button", { name: "核对测试结果" }),
  ).toBeTruthy();
});

it("reaches the same recovery entry when no model has been configured yet", async () => {
  addProfileAction.mockReturnValueOnce(false);
  vi.mocked(getModelProfiles).mockResolvedValueOnce([]);
  const user = userEvent.setup();
  render(<R6Settings onDiagnostics={vi.fn()} />);
  await user.click(screen.getByRole("button", { name: "模型配置" }));

  const generationLane = (
    await screen.findByRole("heading", { name: "记忆整理" })
  ).closest(".r6-model-lane") as HTMLElement | null;
  if (!generationLane) throw new Error("Missing generation lane");
  await user.click(
    await within(generationLane).findByRole("button", { name: "配置模型" }),
  );

  expect(screen.getByRole("button", { name: "核对测试结果" })).toBeTruthy();
  const searchLane = screen
    .getByRole("heading", { name: "记忆搜索" })
    .closest(".r6-model-lane") as HTMLElement | null;
  if (!searchLane) throw new Error("Missing search lane");
  expect(
    within(searchLane).getByRole("button", { name: "配置" }),
  ).toHaveProperty("disabled", true);
  expect(searchLane.textContent).toContain("配置记忆整理后可自定义");
});

it("shows separate compact lanes and opens the requested presentation", async () => {
  const user = userEvent.setup();
  render(<R6Settings onDiagnostics={vi.fn()} />);
  await user.click(screen.getByRole("button", { name: "模型配置" }));

  const generationLane = (
    await screen.findByRole("heading", { name: "记忆整理" })
  ).closest(".r6-model-lane") as HTMLElement | null;
  const embeddingLane = screen
    .getByRole("heading", { name: "记忆搜索" })
    .closest(".r6-model-lane") as HTMLElement | null;
  if (!generationLane || !embeddingLane) throw new Error("Missing model lane");

  expect(generationLane.textContent).toContain("当前模型");
  expect(generationLane.textContent).toContain("LLM");
  expect(embeddingLane.textContent).toContain("本地内置（推荐）");
  expect(embeddingLane.textContent).toContain("Embedding");

  await user.click(within(embeddingLane).getByRole("button", { name: "配置" }));
  expect(await within(embeddingLane).findByText("embedding")).toBeTruthy();
  expect(editProfileAction).toHaveBeenCalledTimes(1);
  expect(editProfileAction).toHaveBeenLastCalledWith(
    expect.objectContaining({ profile_id: "profile-1" }),
  );
  expect(embeddingLane.dataset.expanded).toBe("true");
  expect(
    within(embeddingLane)
      .getByRole("button", { name: "收起" })
      .getAttribute("aria-expanded"),
  ).toBe("true");
  await user.click(
    within(generationLane).getByRole("button", { name: "配置" }),
  );
  expect(await within(generationLane).findByText("generation")).toBeTruthy();
  expect(editProfileAction).toHaveBeenCalledTimes(2);
  expect(generationLane.dataset.expanded).toBe("true");
  expect(within(embeddingLane).queryByText("embedding")).toBeNull();

  await user.click(
    within(generationLane).getByRole("button", { name: "收起" }),
  );
  expect(
    within(generationLane)
      .getByText("generation")
      .closest(".r6-model-owner")
      ?.hasAttribute("hidden"),
  ).toBe(true);

  await user.click(
    within(generationLane).getByRole("button", { name: "配置" }),
  );
  expect(editProfileAction).toHaveBeenCalledTimes(2);
  expect(
    within(generationLane)
      .getByText("generation")
      .closest(".r6-model-owner")
      ?.hasAttribute("hidden"),
  ).toBe(false);
  expect(screen.queryByRole("alert")).toBeNull();
});

it("leaves the switch unusable when the state cannot be read", async () => {
  vi.mocked(getSedimentation).mockRejectedValueOnce(new Error("offline"));
  render(<R6Settings onDiagnostics={vi.fn()} />);
  await userEvent.click(screen.getByRole("button", { name: "模型配置" }));

  const toggle = (await screen.findByRole("switch", {
    name: "用模型沉淀记忆",
  })) as HTMLButtonElement;
  expect(toggle.disabled).toBe(true);
  expect(setSedimentation).not.toHaveBeenCalled();
});
