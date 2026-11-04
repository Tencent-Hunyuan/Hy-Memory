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
  const option = await screen.findByRole("radio", { name });
  await user.click(option);
  return option;
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.mocked(apiErrorCode).mockReturnValue(null);
  vi.mocked(memoryModePlanUnsupported).mockReturnValue(false);
});

it("renders all settings modes as one grouped shared choice list", async () => {
  prepare("pro");
  render(<MemoryModeSettings hasActiveModel onConfigureModel={vi.fn()} />);

  const group = await screen.findByRole("radiogroup", {
    name: "当前模式",
  });
  expect(within(group).getAllByRole("radio")).toHaveLength(3);
  expect(
    within(group).getByText("当前模式").closest("label")?.textContent,
  ).toContain("智能记忆（pro）");
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

  await chooseMode(user, /基础记忆（lite）/);
  expect(screen.getByText(/已有记忆继续保留/)).toBeTruthy();
  expect(screen.getByText("模型调用与费用")).toBeTruthy();
  expect(screen.getByText("搜索范围")).toBeTruthy();
  expect(setMemoryMode).not.toHaveBeenCalled();

  await user.click(
    screen.getByRole("button", { name: "切换到基础记忆（lite）" }),
  );
  await waitFor(() => expect(setMemoryMode).toHaveBeenCalledWith("lite"));
  await waitFor(() =>
    expect(document.activeElement).toBe(
      screen.getByRole("radio", { name: /基础记忆（lite）/ }),
    ),
  );
});

it("traps modal focus, closes on Escape and restores the mode-card focus", async () => {
  prepare("pro");
  const outside = document.createElement("button");
  document.body.append(outside);
  const user = userEvent.setup();
  render(<MemoryModeSettings hasActiveModel onConfigureModel={vi.fn()} />);
  const lite = await chooseMode(user, /基础记忆（lite）/);

  const cancel = screen.getByRole("button", { name: "取消" });
  await waitFor(() => expect(document.activeElement).toBe(cancel));
  expect(outside.inert).toBe(true);
  await user.keyboard("{Shift>}{Tab}{/Shift}");
  expect(document.activeElement).toBe(
    screen.getByRole("button", { name: "切换到基础记忆（lite）" }),
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
  await chooseMode(user, /基础记忆（lite）/);
  await user.click(
    screen.getByRole("button", { name: "切换到基础记忆（lite）" }),
  );

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

  await chooseMode(user, /智能记忆（pro）/);
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

  await chooseMode(user, /基础记忆（lite）/);
  expect(planMemoryMode).toHaveBeenCalledWith("lite");
  expect(await screen.findByText(/已有记忆继续保留/)).toBeTruthy();
});

it("keeps downgrade effects when an old Core falls back from ultra to pro", async () => {
  prepare("ultra");
  vi.mocked(planMemoryMode).mockRejectedValue(new Error("404"));
  vi.mocked(memoryModePlanUnsupported).mockReturnValue(true);
  const user = userEvent.setup();
  render(<MemoryModeSettings hasActiveModel onConfigureModel={vi.fn()} />);

  await chooseMode(user, /智能记忆（pro）/);

  const dialog = screen.getByRole("dialog");
  expect(within(dialog).getAllByText("已有数据")).toHaveLength(1);
  expect(within(dialog).getByText("每天自动整合")).toBeTruthy();
});

it("explains model cost, search scope and the next step before switching to ultra", async () => {
  prepare("lite");
  const user = userEvent.setup();
  render(<MemoryModeSettings hasActiveModel onConfigureModel={vi.fn()} />);

  await chooseMode(user, /深度记忆（ultra）/);

  expect(screen.getByText("模型调用与费用")).toBeTruthy();
  expect(screen.getByText("搜索范围")).toBeTruthy();
  expect(screen.getByText("切换后")).toBeTruthy();
  expect(screen.getByText(/已经处理完的旧对话不会补做/)).toBeTruthy();
  expect(screen.getByText("知识图谱")).toBeTruthy();
  expect(screen.getAllByText("已有数据")).toHaveLength(1);
  expect(screen.getByText(/切换本身不会立即整合/)).toBeTruthy();
});

it("distinguishes regular memories from higher-level Ultra data when switching to pro", async () => {
  prepare("ultra");
  const user = userEvent.setup();
  render(<MemoryModeSettings hasActiveModel onConfigureModel={vi.fn()} />);

  await chooseMode(user, /智能记忆（pro）/);

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

  await chooseMode(user, /智能记忆（pro）/);

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
  await screen.findByRole("radio", { name: /智能记忆（pro）/ });
  vi.mocked(getMemoryMode).mockResolvedValue(state("ultra"));

  await chooseMode(user, /深度记忆（ultra）/);
  await user.click(
    screen.getByRole("button", { name: "切换到深度记忆（ultra）" }),
  );

  await waitFor(() =>
    expect(document.activeElement?.getAttribute("id")).toContain("-evolve"),
  );
  expect(
    screen.getByRole("heading", { name: "记忆整合（Evolve）" }),
  ).toBeTruthy();
});

it("shows the default nightly evolve controls in ultra", async () => {
  prepare("ultra");
  render(<MemoryModeSettings hasActiveModel onConfigureModel={vi.fn()} />);

  expect(
    (await screen.findByRole("switch", { name: "每天自动整合" })).getAttribute(
      "aria-checked",
    ),
  ).toBe("true");
  expect((screen.getByLabelText("执行时间") as HTMLInputElement).value).toBe(
    "03:00",
  );
  expect(screen.getByText(/只运行这一次，不改变每天的计划/)).toBeTruthy();
  expect(screen.getByRole("button", { name: "立即整合" })).toBeTruthy();
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
  expect(screen.queryByLabelText("执行时间")).toBeNull();
  expect(screen.getByRole("button", { name: "立即整合" })).toBeTruthy();
});

it("updates mode state automatically without showing a manual refresh control", async () => {
  prepare("pro");
  render(<MemoryModeSettings hasActiveModel onConfigureModel={vi.fn()} />);
  const pro = await screen.findByRole("radio", { name: /智能记忆（pro）/ });

  expect((pro as HTMLInputElement).checked).toBe(true);
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
    (await screen.findByRole("radio", {
      name: /智能记忆（pro）/,
    })) as HTMLInputElement,
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
  expect(
    screen.getByRole("radio", { name: /基础记忆（lite）/ }),
  ).toHaveProperty("disabled", true);

  await user.click(screen.getByRole("button", { name: "重新读取" }));
  await waitFor(() =>
    expect(
      screen.getByRole("radio", { name: /智能记忆（pro）/ }),
    ).toHaveProperty("disabled", false),
  );
});

it("keeps a failed switch explanation inside the open confirmation dialog", async () => {
  prepare("pro");
  vi.mocked(apiErrorCode).mockReturnValue("graph_store_unavailable");
  vi.mocked(setMemoryMode).mockRejectedValue(new Error("unavailable"));
  const user = userEvent.setup();
  render(<MemoryModeSettings hasActiveModel onConfigureModel={vi.fn()} />);

  await chooseMode(user, /深度记忆（ultra）/);
  await user.click(
    screen.getByRole("button", { name: "切换到深度记忆（ultra）" }),
  );

  const dialog = screen.getByRole("dialog");
  expect(
    await within(dialog).findByText(
      "当前安装缺少知识图谱组件，无法开启 ultra。",
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

  expect(
    await screen.findByRole("radio", { name: /基础记忆（lite）/ }),
  ).toHaveProperty("disabled", false);
  expect(screen.getByRole("radio", { name: /智能记忆（pro）/ })).toHaveProperty(
    "disabled",
    true,
  );
  expect(
    screen.getByRole("radio", { name: /深度记忆（ultra）/ }),
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
      "正在检查或切换向量模型，完成前不能切换模式或手动整合。",
    ),
  ).toBeTruthy();
  expect(
    screen.getByRole("radio", { name: /基础记忆（lite）/ }),
  ).toHaveProperty("disabled", true);
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
