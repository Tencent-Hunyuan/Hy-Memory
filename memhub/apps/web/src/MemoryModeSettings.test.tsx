// @vitest-environment jsdom

import { legacyMemoryModePlan } from "@one-memory/operations";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import {
  apiErrorCode,
  getEvolveOverview,
  getEvolveSchedule,
  getMemoryMode,
  getMemoryProcessing,
  memoryModePlanUnsupported,
  planMemoryMode,
  setEvolveSchedule,
  setMemoryMode,
} from "./api";
import { MemoryModeSettings } from "./MemoryModeSettings";
import { resetClient } from "./query-client";

vi.mock("./api", () => ({
  getMemoryMode: vi.fn(),
  setMemoryMode: vi.fn(),
  getEvolveSchedule: vi.fn(),
  setEvolveSchedule: vi.fn(),
  getEvolveOverview: vi.fn(),
  startEvolve: vi.fn(),
  getMemoryProcessing: vi.fn(),
  planMemoryMode: vi.fn(),
  memoryModePlanUnsupported: vi.fn().mockReturnValue(false),
  apiErrorCode: vi.fn().mockReturnValue(null),
}));

function state(mode: "lite" | "pro" | "ultra" = "pro") {
  return {
    mode,
    configured_mode: mode,
    explicitly_configured: true,
    forced_by_env: false,
    modes: ["lite", "pro", "ultra"] as Array<"lite" | "pro" | "ultra">,
    graph_build: {
      state: "idle" as const,
      started_at: null,
      finished_at: null,
      users: {},
    },
  };
}

function prepare(mode: "lite" | "pro" | "ultra" = "pro") {
  vi.mocked(getMemoryMode).mockResolvedValue(state(mode));
  vi.mocked(getEvolveSchedule).mockResolvedValue({
    enabled: true,
    time: "03:00",
  });
  vi.mocked(getEvolveOverview).mockResolvedValue({
    mode,
    schedule: { enabled: true, time: "03:00" },
    users: {},
    running: [],
  });
  vi.mocked(getMemoryProcessing).mockResolvedValue({} as never);
  vi.mocked(planMemoryMode).mockImplementation(async (target) =>
    legacyMemoryModePlan(mode, target, { hasActiveModel: true }),
  );
}

async function chooseMode(
  user: ReturnType<typeof userEvent.setup>,
  name: RegExp,
) {
  const control = await screen.findByRole("combobox", {
    name: "记忆模式",
  });
  await user.click(control);
  const option = await screen.findByRole("option", { name });
  await user.click(option);
  return control;
}

afterEach(() => {
  cleanup();
  resetClient();
  vi.clearAllMocks();
  vi.mocked(apiErrorCode).mockReturnValue(null);
  vi.mocked(memoryModePlanUnsupported).mockReturnValue(false);
});

it("shows the current mode in a compact dropdown", async () => {
  prepare("pro");
  const user = userEvent.setup();
  render(<MemoryModeSettings hasActiveModel onConfigureModel={vi.fn()} />);

  const control = await screen.findByRole("combobox", {
    name: "记忆模式",
  });
  expect(control.textContent).toContain("智能记忆 · Pro");
  expect(control.textContent).toContain("沉淀高级记忆");
  expect(
    control.querySelector(".om-option-menu-suffix")?.textContent,
  ).toContain("· Pro");
  expect(control.textContent).not.toContain("（");
  expect(screen.queryByText("当前模式")).toBeNull();
  expect(screen.queryByRole("radiogroup")).toBeNull();

  await user.click(control);
  expect(screen.getAllByRole("option")).toHaveLength(3);
});

it("shows the effective Ultra mode and disables selection when forced by the environment", async () => {
  prepare("ultra");
  vi.mocked(getMemoryMode).mockResolvedValue({
    ...state("ultra"),
    configured_mode: "pro",
    forced_by_env: true,
  });
  render(<MemoryModeSettings hasActiveModel onConfigureModel={vi.fn()} />);

  const control = await screen.findByRole("combobox", {
    name: "记忆模式",
  });
  expect(control.textContent).toContain("深度记忆 · Ultra");
  expect(control).toHaveProperty("disabled", true);
  expect(screen.getByRole("switch", { name: "每天自动整合" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "立即整合" })).toBeTruthy();
});

it("confirms the data effects before switching to lite", async () => {
  prepare("pro");
  vi.mocked(setMemoryMode).mockResolvedValue({
    ...state("lite"),
    previous_mode: "pro",
    effects: ["higher_mode_memories_hidden"],
  });
  const user = userEvent.setup();
  render(<MemoryModeSettings hasActiveModel onConfigureModel={vi.fn()} />);

  await chooseMode(user, /基础记忆 · Lite/);
  expect(screen.getByText(/已有记忆继续保留/)).toBeTruthy();
  expect(screen.getByText("模型调用与费用")).toBeTruthy();
  expect(screen.getByText("搜索范围")).toBeTruthy();
  expect(setMemoryMode).not.toHaveBeenCalled();

  await user.click(screen.getByRole("button", { name: "确认选择" }));
  await waitFor(() => expect(setMemoryMode).toHaveBeenCalledWith("lite"));
  await waitFor(() =>
    expect(document.activeElement).toBe(
      screen.getByRole("combobox", { name: "记忆模式" }),
    ),
  );
});

it("traps modal focus, closes on Escape and restores the mode dropdown", async () => {
  prepare("pro");
  const outside = document.createElement("button");
  document.body.append(outside);
  const user = userEvent.setup();
  render(<MemoryModeSettings hasActiveModel onConfigureModel={vi.fn()} />);
  const lite = await chooseMode(user, /基础记忆 · Lite/);

  const cancel = screen.getByRole("button", { name: "取消" });
  await waitFor(() => expect(document.activeElement).toBe(cancel));
  expect(outside.inert).toBe(true);
  await user.keyboard("{Shift>}{Tab}{/Shift}");
  expect(document.activeElement).toBe(
    screen.getByRole("button", { name: "确认选择" }),
  );
  await user.keyboard("{Escape}");

  expect(screen.queryByRole("dialog")).toBeNull();
  expect(outside.inert).toBeFalsy();
  expect(document.activeElement).toBe(lite);
  outside.remove();
});

it("keeps focus inside the modal while the mode change is pending", async () => {
  prepare("pro");
  vi.mocked(setMemoryMode).mockReturnValue(new Promise(() => {}));
  const user = userEvent.setup();
  render(<MemoryModeSettings hasActiveModel onConfigureModel={vi.fn()} />);
  await chooseMode(user, /基础记忆 · Lite/);
  await user.click(screen.getByRole("button", { name: "确认选择" }));

  const modal = screen.getByRole("dialog");
  await waitFor(() => expect(document.activeElement).toBe(modal));
  await user.keyboard("{Tab}");
  expect(document.activeElement).toBe(modal);
});

it("routes to model setup instead of switching pro without a model", async () => {
  prepare("lite");
  vi.mocked(planMemoryMode).mockResolvedValue({
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
  const configure = vi.fn();
  const user = userEvent.setup();
  render(
    <MemoryModeSettings hasActiveModel={false} onConfigureModel={configure} />,
  );

  await chooseMode(user, /智能记忆 · Pro/);
  expect(configure).toHaveBeenCalledExactlyOnceWith(
    "使用 pro 或 ultra 前需要先配置并启用生成模型。",
  );
  expect(setMemoryMode).not.toHaveBeenCalled();
});

it("uses the isolated legacy plan only for a route-level old-Core 404", async () => {
  prepare("pro");
  vi.mocked(planMemoryMode).mockRejectedValue(new Error("404"));
  vi.mocked(memoryModePlanUnsupported).mockReturnValue(true);
  const user = userEvent.setup();
  render(<MemoryModeSettings hasActiveModel onConfigureModel={vi.fn()} />);

  await chooseMode(user, /基础记忆 · Lite/);
  expect(planMemoryMode).toHaveBeenCalledWith("lite");
  expect(await screen.findByText(/已有记忆继续保留/)).toBeTruthy();
});

it("restores focus to the mode dropdown when planning fails", async () => {
  prepare("pro");
  vi.mocked(planMemoryMode).mockRejectedValue(new Error("offline"));
  const user = userEvent.setup();
  render(<MemoryModeSettings hasActiveModel onConfigureModel={vi.fn()} />);

  await chooseMode(user, /基础记忆 · Lite/);

  expect(
    await screen.findByText("模式选择影响暂时无法读取，请重试。"),
  ).toBeTruthy();
  await waitFor(() =>
    expect(document.activeElement).toBe(
      screen.getByRole("combobox", { name: "记忆模式" }),
    ),
  );
});

it("keeps downgrade effects when an old Core falls back from ultra to pro", async () => {
  prepare("ultra");
  vi.mocked(planMemoryMode).mockRejectedValue(new Error("404"));
  vi.mocked(memoryModePlanUnsupported).mockReturnValue(true);
  const user = userEvent.setup();
  render(<MemoryModeSettings hasActiveModel onConfigureModel={vi.fn()} />);

  await chooseMode(user, /智能记忆 · Pro/);

  const dialog = screen.getByRole("dialog");
  expect(within(dialog).getAllByText("已有数据")).toHaveLength(1);
  expect(within(dialog).getByText("每天自动整合")).toBeTruthy();
});

it("explains model cost, search scope and the next step before switching to ultra", async () => {
  prepare("lite");
  const user = userEvent.setup();
  render(<MemoryModeSettings hasActiveModel onConfigureModel={vi.fn()} />);

  await chooseMode(user, /深度记忆 · Ultra/);

  expect(screen.getByText("模型调用与费用")).toBeTruthy();
  expect(screen.getByText("搜索范围")).toBeTruthy();
  expect(screen.getByText("选择后")).toBeTruthy();
  expect(screen.getByText(/已经处理完的旧对话不会补做/)).toBeTruthy();
  expect(screen.getByText("知识图谱")).toBeTruthy();
  expect(screen.getAllByText("已有数据")).toHaveLength(1);
  expect(screen.getByText(/选择模式本身不会立即整合/)).toBeTruthy();
});

it("distinguishes regular memories from higher-level Ultra data when switching to pro", async () => {
  prepare("ultra");
  const user = userEvent.setup();
  render(<MemoryModeSettings hasActiveModel onConfigureModel={vi.fn()} />);

  await chooseMode(user, /智能记忆 · Pro/);

  const dialog = screen.getByRole("dialog");
  expect(within(dialog).getAllByText("已有数据")).toHaveLength(1);
  expect(within(dialog).getByText(/普通沉淀记忆仍可使用/)).toBeTruthy();
  expect(within(dialog).getByText(/更高层记忆和图谱数据继续保留/)).toBeTruthy();
  expect(within(dialog).getByText("每天自动整合")).toBeTruthy();
  expect(
    within(dialog).getByText("每天自动整合会停止；已生成的整合结果继续保留。"),
  ).toBeTruthy();
});

it("does not show automatic consolidation when switching from lite to pro", async () => {
  prepare("lite");
  const user = userEvent.setup();
  render(<MemoryModeSettings hasActiveModel onConfigureModel={vi.fn()} />);

  await chooseMode(user, /智能记忆 · Pro/);

  const dialog = screen.getByRole("dialog");
  expect(within(dialog).getAllByText("已有数据")).toHaveLength(1);
  expect(within(dialog).getByText(/已经处理完的旧对话不会补做/)).toBeTruthy();
  expect(within(dialog).queryByText("每天自动整合")).toBeNull();
});

it("focuses the Evolve settings after switching to ultra", async () => {
  prepare("pro");
  vi.mocked(setMemoryMode).mockResolvedValue({
    ...state("ultra"),
    previous_mode: "pro",
    effects: ["existing_memories_added_to_graph"],
  });
  const user = userEvent.setup();
  render(<MemoryModeSettings hasActiveModel onConfigureModel={vi.fn()} />);
  await screen.findByRole("combobox", { name: "记忆模式" });
  vi.mocked(getMemoryMode).mockResolvedValue(state("ultra"));

  await chooseMode(user, /深度记忆 · Ultra/);
  await user.click(screen.getByRole("button", { name: "确认选择" }));

  await waitFor(() =>
    expect(document.activeElement?.getAttribute("id")).toContain("-evolve"),
  );
  expect(
    screen.getByRole("heading", { name: "记忆整合（Evolve）" }),
  ).toBeTruthy();
});

it("focuses the recoverable Evolve section when Ultra schedule loading fails", async () => {
  prepare("pro");
  vi.mocked(getEvolveSchedule).mockRejectedValue(new Error("offline"));
  vi.mocked(setMemoryMode).mockResolvedValue({
    ...state("ultra"),
    previous_mode: "pro",
    effects: ["existing_memories_added_to_graph"],
  });
  const user = userEvent.setup();
  render(<MemoryModeSettings hasActiveModel onConfigureModel={vi.fn()} />);
  await screen.findByRole("combobox", { name: "记忆模式" });
  vi.mocked(getMemoryMode).mockResolvedValue(state("ultra"));

  await chooseMode(user, /深度记忆 · Ultra/);
  await user.click(screen.getByRole("button", { name: "确认选择" }));

  await waitFor(() =>
    expect(document.activeElement?.getAttribute("id")).toContain("-evolve"),
  );
  expect(screen.getByText("自动整合设置暂时无法读取，请重试。")).toBeTruthy();
  expect(screen.getByRole("button", { name: "立即整合" })).toBeTruthy();
});

it("shows the default nightly evolve controls in ultra", async () => {
  prepare("ultra");
  render(<MemoryModeSettings hasActiveModel onConfigureModel={vi.fn()} />);

  expect(
    (await screen.findByRole("switch", { name: "每天自动整合" })).getAttribute(
      "aria-checked",
    ),
  ).toBe("true");
  const timePicker = screen.getByRole("button", {
    name: "执行时间，03:00",
  });
  expect(timePicker.tagName).toBe("BUTTON");
  expect(timePicker.textContent).toContain("03:00");
  expect(screen.getByText(/只运行这一次，不改变每天的计划/)).toBeTruthy();
  expect(screen.getByRole("button", { name: "立即整合" })).toBeTruthy();
});

it("keeps the immediate action visible and recovers when the schedule read fails", async () => {
  prepare("ultra");
  vi.mocked(getEvolveSchedule).mockRejectedValueOnce(new Error("offline"));
  const user = userEvent.setup();
  render(<MemoryModeSettings hasActiveModel onConfigureModel={vi.fn()} />);

  expect(
    await screen.findByText("自动整合设置暂时无法读取，请重试。"),
  ).toBeTruthy();
  expect(screen.queryByRole("switch", { name: "每天自动整合" })).toBeNull();
  expect(screen.getByRole("button", { name: "立即整合" })).toBeTruthy();

  await user.click(screen.getByRole("button", { name: "重新读取" }));

  const automatic = await screen.findByRole("switch", {
    name: "每天自动整合",
  });
  await waitFor(() => expect(document.activeElement).toBe(automatic));
});

it("opens the time picker from the whole control and saves a complete time", async () => {
  prepare("ultra");
  vi.mocked(setEvolveSchedule).mockResolvedValue({
    enabled: true,
    time: "04:30",
  });
  const user = userEvent.setup();
  render(<MemoryModeSettings hasActiveModel onConfigureModel={vi.fn()} />);

  const trigger = await screen.findByRole("button", {
    name: "执行时间，03:00",
  });
  await user.click(trigger);

  const picker = screen.getByRole("dialog", { name: "执行时间" });
  await user.selectOptions(
    within(picker).getByRole("listbox", { name: "执行时间 HH" }),
    "04",
  );
  await user.selectOptions(
    within(picker).getByRole("listbox", { name: "执行时间 MM" }),
    "30",
  );
  expect(setEvolveSchedule).not.toHaveBeenCalled();

  await user.click(within(picker).getByRole("button", { name: "保存修改" }));
  await waitFor(() =>
    expect(setEvolveSchedule).toHaveBeenCalledExactlyOnceWith({
      time: "04:30",
    }),
  );
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "执行时间，04:30" })).toBe(
      document.activeElement,
    ),
  );
  expect(screen.queryByRole("dialog", { name: "执行时间" })).toBeNull();
});

it("keeps Tab inside the time picker and restores focus on Escape", async () => {
  prepare("ultra");
  const user = userEvent.setup();
  render(<MemoryModeSettings hasActiveModel onConfigureModel={vi.fn()} />);

  const trigger = await screen.findByRole("button", {
    name: "执行时间，03:00",
  });
  await user.click(trigger);
  const picker = screen.getByRole("dialog", { name: "执行时间" });
  const hours = within(picker).getByRole("listbox", {
    name: "执行时间 HH",
  });
  const save = within(picker).getByRole("button", { name: "保存修改" });
  expect(document.activeElement).toBe(hours);
  await user.keyboard("{Shift>}{Tab}{/Shift}");
  expect(document.activeElement).toBe(save);
  await user.keyboard("{Tab}");
  expect(document.activeElement).toBe(hours);

  await user.keyboard("{Escape}");

  expect(screen.queryByRole("dialog", { name: "执行时间" })).toBeNull();
  expect(document.activeElement).toBe(trigger);
});

it("keeps the selected time open for retry when saving fails", async () => {
  prepare("ultra");
  vi.mocked(setEvolveSchedule).mockRejectedValue(new Error("offline"));
  const user = userEvent.setup();
  render(<MemoryModeSettings hasActiveModel onConfigureModel={vi.fn()} />);

  await user.click(
    await screen.findByRole("button", { name: "执行时间，03:00" }),
  );
  const picker = screen.getByRole("dialog", { name: "执行时间" });
  const minutes = within(picker).getByRole("listbox", {
    name: "执行时间 MM",
  });
  await user.selectOptions(minutes, "30");
  await user.click(within(picker).getByRole("button", { name: "保存修改" }));

  expect(
    await screen.findByText("自动整合设置未能保存，请重试。"),
  ).toBeTruthy();
  expect(screen.getByRole("dialog", { name: "执行时间" })).toBeTruthy();
  expect((minutes as HTMLSelectElement).value).toBe("30");
  await waitFor(() =>
    expect(document.activeElement).toBe(
      within(picker).getByRole("button", { name: "保存修改" }),
    ),
  );
});

it("keeps the time dialog and focus stable while saving is pending", async () => {
  prepare("ultra");
  vi.mocked(setEvolveSchedule).mockReturnValue(new Promise(() => {}));
  const user = userEvent.setup();
  render(<MemoryModeSettings hasActiveModel onConfigureModel={vi.fn()} />);

  await user.click(
    await screen.findByRole("button", { name: "执行时间，03:00" }),
  );
  const picker = screen.getByRole("dialog", { name: "执行时间" });
  await user.selectOptions(
    within(picker).getByRole("listbox", { name: "执行时间 MM" }),
    "30",
  );
  await user.click(within(picker).getByRole("button", { name: "保存修改" }));
  await waitFor(() => expect(setEvolveSchedule).toHaveBeenCalledTimes(1));

  expect(document.activeElement).toBe(picker);
  await user.keyboard("{Escape}");
  expect(screen.getByRole("dialog", { name: "执行时间" })).toBe(picker);
  expect(document.activeElement).toBe(picker);
});

it("hides the time field when automatic evolve is off and keeps the saved time visible", async () => {
  prepare("ultra");
  vi.mocked(setEvolveSchedule).mockResolvedValue({
    enabled: false,
    time: "03:00",
  });
  const user = userEvent.setup();
  render(<MemoryModeSettings hasActiveModel onConfigureModel={vi.fn()} />);

  const automatic = await screen.findByRole("switch", {
    name: "每天自动整合",
  });
  await user.click(automatic);

  await screen.findByText(/重新开启后仍按 03:00 运行/);
  expect(screen.queryByRole("button", { name: /执行时间/ })).toBeNull();
  expect(screen.getByRole("button", { name: "立即整合" })).toBeTruthy();
});

it("updates mode state automatically without showing a manual refresh control", async () => {
  prepare("pro");
  render(<MemoryModeSettings hasActiveModel onConfigureModel={vi.fn()} />);
  const mode = await screen.findByRole("combobox", { name: "记忆模式" });

  expect(mode.textContent).toContain("智能记忆 · Pro");
  expect(screen.queryByRole("button", { name: "刷新记忆模式" })).toBeNull();
  expect(getMemoryMode).toHaveBeenCalledTimes(1);
  await waitFor(() => {
    expect(getEvolveSchedule).toHaveBeenCalledTimes(1);
  });
});

it("keeps the mode usable when an evolve status read fails", async () => {
  prepare("pro");
  vi.mocked(getEvolveSchedule).mockRejectedValueOnce(new Error("offline"));
  render(<MemoryModeSettings hasActiveModel onConfigureModel={vi.fn()} />);

  expect(
    await screen.findByRole("combobox", {
      name: "记忆模式",
    }),
  ).toHaveProperty("disabled", false);
});

it("offers a text recovery action when the mode read fails", async () => {
  prepare("pro");
  vi.mocked(getMemoryMode).mockRejectedValueOnce(new Error("offline"));
  const user = userEvent.setup();
  render(<MemoryModeSettings hasActiveModel onConfigureModel={vi.fn()} />);

  expect(
    await screen.findByText("记忆模式状态暂时无法读取，请重试。"),
  ).toBeTruthy();
  expect(screen.getByRole("combobox", { name: "记忆模式" })).toHaveProperty(
    "disabled",
    true,
  );

  await user.click(screen.getByRole("button", { name: "重新读取" }));
  await waitFor(() =>
    expect(screen.getByRole("combobox", { name: "记忆模式" })).toHaveProperty(
      "disabled",
      false,
    ),
  );
});

it("keeps a failed switch explanation inside the open confirmation dialog", async () => {
  prepare("pro");
  vi.mocked(apiErrorCode).mockReturnValue("graph_store_unavailable");
  vi.mocked(setMemoryMode).mockRejectedValue(new Error("unavailable"));
  const user = userEvent.setup();
  render(<MemoryModeSettings hasActiveModel onConfigureModel={vi.fn()} />);

  await chooseMode(user, /深度记忆 · Ultra/);
  await user.click(screen.getByRole("button", { name: "确认选择" }));

  const dialog = screen.getByRole("dialog");
  expect(
    await within(dialog).findByText(
      "当前安装缺少知识图谱组件，无法选择 ultra。",
    ),
  ).toBeTruthy();
});

it("keeps lite available while model status is unknown", async () => {
  prepare("pro");
  render(
    <MemoryModeSettings
      hasActiveModel={false}
      modelStateKnown={false}
      onConfigureModel={vi.fn()}
    />,
  );

  const user = userEvent.setup();
  await user.click(await screen.findByRole("combobox", { name: "记忆模式" }));
  expect(
    await screen.findByRole("option", { name: /基础记忆 · Lite/ }),
  ).toHaveProperty("disabled", false);
  expect(screen.getByRole("option", { name: /智能记忆 · Pro/ })).toHaveProperty(
    "disabled",
    true,
  );
  expect(
    screen.getByRole("option", { name: /深度记忆 · Ultra/ }),
  ).toHaveProperty("disabled", true);
});

it("blocks mode and Evolve controls during embedding maintenance", async () => {
  prepare("ultra");
  vi.mocked(getMemoryProcessing).mockResolvedValue({
    embedding_switch: { phase: "running" },
  } as never);
  render(<MemoryModeSettings hasActiveModel onConfigureModel={vi.fn()} />);

  expect(
    await screen.findByText(
      "正在检查或切换向量模型，完成前不能选择模式或手动整合。",
    ),
  ).toBeTruthy();
  expect(screen.getByRole("combobox", { name: "记忆模式" })).toHaveProperty(
    "disabled",
    true,
  );
  expect(screen.getByRole("button", { name: "立即整合" })).toHaveProperty(
    "disabled",
    true,
  );
});

it("blocks lifecycle controls while the knowledge graph is being built", async () => {
  prepare("ultra");
  vi.mocked(getMemoryMode).mockResolvedValue({
    ...state("ultra"),
    graph_build: {
      state: "running",
      started_at: "2026-11-03T09:00:00Z",
      finished_at: null,
      users: {},
    },
  });
  render(<MemoryModeSettings hasActiveModel onConfigureModel={vi.fn()} />);

  expect(
    await screen.findByText("正在把已有高级记忆加入知识图谱…"),
  ).toBeTruthy();
  expect(screen.getByRole("button", { name: "立即整合" })).toHaveProperty(
    "disabled",
    true,
  );
});
