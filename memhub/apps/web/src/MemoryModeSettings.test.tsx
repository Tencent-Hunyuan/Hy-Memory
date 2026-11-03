// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import {
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

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
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

  await user.click(
    await screen.findByRole("button", { name: /基础记忆（lite）/ }),
  );
  expect(screen.getByText(/以前生成的高级记忆会继续保留/)).toBeTruthy();
  expect(setMemoryMode).not.toHaveBeenCalled();

  await user.click(screen.getByRole("button", { name: "切换模式" }));
  await waitFor(() => expect(setMemoryMode).toHaveBeenCalledWith("lite"));
});

it("routes to model setup instead of switching pro without a model", async () => {
  prepare("lite");
  const configure = vi.fn();
  const user = userEvent.setup();
  render(
    <MemoryModeSettings hasActiveModel={false} onConfigureModel={configure} />,
  );

  await user.click(
    await screen.findByRole("button", { name: /智能记忆（pro）/ }),
  );
  expect(configure).toHaveBeenCalledTimes(1);
  expect(setMemoryMode).not.toHaveBeenCalled();
  expect(
    screen.getByText("使用 pro 或 ultra 前需要先配置并启用生成模型。"),
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
  expect(
    (screen.getByLabelText("自动整合时间") as HTMLInputElement).value,
  ).toBe("03:00");
  expect(screen.getByRole("button", { name: "立即整合" })).toBeTruthy();
});

it("ignores an older refresh that finishes after a newer mode read", async () => {
  prepare("pro");
  const user = userEvent.setup();
  render(<MemoryModeSettings hasActiveModel onConfigureModel={vi.fn()} />);
  await screen.findByRole("button", { name: /基础记忆（lite）/ });

  let resolveOld!: (value: ReturnType<typeof state>) => void;
  vi.mocked(getMemoryMode)
    .mockReturnValueOnce(
      new Promise((resolve) => {
        resolveOld = resolve;
      }),
    )
    .mockResolvedValueOnce(state("lite"));

  const refresh = screen.getByRole("button", { name: "刷新记忆模式" });
  await user.click(refresh);
  await user.click(refresh);
  await waitFor(() =>
    expect(
      screen
        .getByRole("button", { name: /基础记忆（lite）/ })
        .getAttribute("aria-pressed"),
    ).toBe("true"),
  );
  resolveOld(state("pro"));

  await waitFor(() =>
    expect(
      screen
        .getByRole("button", { name: /基础记忆（lite）/ })
        .getAttribute("aria-pressed"),
    ).toBe("true"),
  );
});

it("does not let an older refresh undo a saved schedule change", async () => {
  prepare("ultra");
  vi.mocked(setEvolveSchedule).mockResolvedValue({
    enabled: false,
    time: "03:00",
  });
  const user = userEvent.setup();
  render(<MemoryModeSettings hasActiveModel onConfigureModel={vi.fn()} />);
  const toggle = await screen.findByRole("switch", {
    name: "每天自动整合",
  });

  let resolveOld!: (value: { enabled: true; time: string }) => void;
  vi.mocked(getEvolveSchedule).mockReturnValueOnce(
    new Promise((resolve) => {
      resolveOld = resolve;
    }),
  );
  await user.click(screen.getByRole("button", { name: "刷新记忆模式" }));
  await user.click(toggle);
  await waitFor(() =>
    expect(setEvolveSchedule).toHaveBeenCalledWith({ enabled: false }),
  );
  resolveOld({ enabled: true, time: "03:00" });

  await waitFor(() =>
    expect(toggle.getAttribute("aria-checked")).toBe("false"),
  );
});
