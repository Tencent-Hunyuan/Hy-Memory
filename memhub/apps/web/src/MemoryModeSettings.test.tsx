// @vitest-environment jsdom

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

it("explains model cost, search scope and the next step before switching to ultra", async () => {
  prepare("lite");
  const user = userEvent.setup();
  render(<MemoryModeSettings hasActiveModel onConfigureModel={vi.fn()} />);

  await chooseMode(user, /深度记忆（ultra）/);

  expect(screen.getByText("模型调用与费用")).toBeTruthy();
  expect(screen.getByText("搜索范围")).toBeTruthy();
  expect(screen.getByText("切换后")).toBeTruthy();
  expect(screen.getByText(/已经处理完的旧对话不会补做/)).toBeTruthy();
  expect(screen.getByText(/切换本身不会立即整合/)).toBeTruthy();
});

it("distinguishes regular memories from higher-level Ultra data when switching to pro", async () => {
  prepare("ultra");
  const user = userEvent.setup();
  render(<MemoryModeSettings hasActiveModel onConfigureModel={vi.fn()} />);

  await chooseMode(user, /智能记忆（pro）/);

  expect(screen.getByText(/普通沉淀记忆仍可使用/)).toBeTruthy();
  expect(screen.getByText(/更高层记忆和图谱数据继续保留/)).toBeTruthy();
  expect(screen.getByText(/每天自动整合会停止/)).toBeTruthy();
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
