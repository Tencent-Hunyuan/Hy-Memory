// @vitest-environment jsdom
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { DebugLogPanel } from "./DebugLogPanel";
import {
  recordRendererDebugLog,
  resetRendererDebugLogsForTest,
} from "./renderer-debug-log";

const diagnosticLevel = vi.hoisted(() => ({
  get: vi.fn(async () => "info" as const),
  set: vi.fn(async (level: "error" | "warning" | "info" | "debug") => level),
  usage: vi.fn(async () => ({
    schema_version: 1 as const,
    summary: {
      request_count: 3,
      succeeded: 3,
      failed: 0,
      in_flight: 0,
      prompt_tokens: 1200,
      completion_tokens: 300,
      total_tokens: 1500,
      cached_prompt_tokens: 0,
      reasoning_tokens: 0,
      avg_prompt_tokens: 400,
      avg_completion_tokens: 100,
    },
    by_minute: [
      {
        minute: "2026-10-17T12:47:00Z",
        prompt_stage: "coding_judge",
        request_count: 1,
        prompt_tokens: 200,
        completion_tokens: 50,
        total_tokens: 250,
        avg_prompt_tokens: 200,
        avg_completion_tokens: 50,
      },
    ],
    requests: [],
  })),
}));

vi.mock("./api", () => ({
  getDiagnosticLogLevel: diagnosticLevel.get,
  setDiagnosticLogLevel: diagnosticLevel.set,
  getModelUsage: diagnosticLevel.usage,
}));

const snapshot = {
  started_at: "2026-10-14T10:00:00.000Z",
  read_at: "2026-10-14T10:01:00.000Z",
  truncated: false,
  entries: [
    {
      id: "core-info",
      timestamp: "2026-10-14T10:00:01.000Z",
      level: "info" as const,
      service: "core" as const,
      component: "import.scan",
      event: "source.scanned",
      outcome: "succeeded",
      details: { source_id: "cursor", count: 3 },
      source: "diagnostic" as const,
    },
    {
      id: "desktop-error",
      timestamp: "2026-10-14T10:00:02.000Z",
      level: "error" as const,
      service: "desktop" as const,
      component: "desktop.action",
      event: "sync.failed",
      outcome: "failed",
      error_code: "sync_failed",
      source: "diagnostic" as const,
    },
  ],
};

beforeEach(() => {
  resetRendererDebugLogsForTest();
  diagnosticLevel.get.mockClear();
  diagnosticLevel.set.mockClear();
  diagnosticLevel.usage.mockClear();
  window.oneMemory = {
    getDebugLogs: vi.fn().mockResolvedValue(snapshot),
  } as unknown as NonNullable<typeof window.oneMemory>;
});

afterEach(() => {
  cleanup();
  window.oneMemory = undefined;
  vi.useRealTimers();
});

it("opens from the floating button and filters by text, level and source", async () => {
  render(<DebugLogPanel />);
  await userEvent.click(
    screen.getByRole("button", { name: "打开 Debug 日志" }),
  );
  const panel = await screen.findByRole("complementary", {
    name: "Debug 日志",
  });
  await waitFor(() =>
    expect(within(panel).getByText(/source\.scanned/)).toBeTruthy(),
  );

  await userEvent.type(
    within(panel).getByRole("searchbox", { name: "搜索日志" }),
    "sync_failed",
  );
  expect(within(panel).getByText(/sync\.failed/)).toBeTruthy();
  expect(within(panel).queryByText(/source\.scanned/)).toBeNull();

  await userEvent.clear(
    within(panel).getByRole("searchbox", { name: "搜索日志" }),
  );
  await userEvent.click(within(panel).getByRole("button", { name: "Error" }));
  expect(within(panel).getByText(/sync\.failed/)).toBeTruthy();
  expect(within(panel).queryByText(/source\.scanned/)).toBeNull();

  await userEvent.click(within(panel).getByRole("button", { name: "全部" }));
  await userEvent.selectOptions(
    within(panel).getByRole("combobox", { name: "日志来源" }),
    "core",
  );
  expect(within(panel).getByText(/source\.scanned/)).toBeTruthy();
  expect(within(panel).queryByText(/sync\.failed/)).toBeNull();
});

it("keeps the underlying page interactive while the log panel is open", async () => {
  const action = vi.fn();
  render(
    <>
      <button type="button" onClick={action}>
        页面操作
      </button>
      <DebugLogPanel />
    </>,
  );
  await userEvent.click(
    screen.getByRole("button", { name: "打开 Debug 日志" }),
  );
  await screen.findByRole("complementary", { name: "Debug 日志" });

  await userEvent.click(screen.getByRole("button", { name: "页面操作" }));

  expect(action).toHaveBeenCalledOnce();
});

it("toggles the diagnostics panel fullscreen", async () => {
  render(<DebugLogPanel />);
  await userEvent.click(
    screen.getByRole("button", { name: "打开 Debug 日志" }),
  );
  const panel = await screen.findByRole("complementary", {
    name: "Debug 日志",
  });

  await userEvent.click(
    within(panel).getByRole("button", { name: "全屏显示日志" }),
  );
  expect(panel.classList.contains("is-fullscreen")).toBe(true);

  await userEvent.click(
    within(panel).getByRole("button", { name: "退出全屏显示日志" }),
  );
  expect(panel.classList.contains("is-fullscreen")).toBe(false);
});

it("shows renderer logs immediately and polls desktop logs while live", async () => {
  vi.useFakeTimers();
  const read = window.oneMemory?.getDebugLogs;
  if (!read) throw new Error("missing debug log reader");
  const readMock = vi.mocked(read);
  render(<DebugLogPanel />);
  fireEvent.click(screen.getByRole("button", { name: "打开 Debug 日志" }));
  await act(async () => {
    await Promise.resolve();
  });

  await act(async () => {
    recordRendererDebugLog({
      level: "warn",
      event: "console.warn",
      outcome: "observed",
      message: "renderer live message",
    });
  });

  expect(screen.getByText("renderer live message")).toBeTruthy();
  await act(async () => vi.advanceTimersByTimeAsync(1_000));
  expect(readMock.mock.calls.length).toBeGreaterThanOrEqual(2);
});

it("changes the Core and HY capture level independently from display filtering", async () => {
  render(<DebugLogPanel />);
  await userEvent.click(
    screen.getByRole("button", { name: "打开 Debug 日志" }),
  );
  const panel = await screen.findByRole("complementary", {
    name: "Debug 日志",
  });
  await waitFor(() => expect(diagnosticLevel.get).toHaveBeenCalledOnce());

  await userEvent.selectOptions(
    within(panel).getByRole("combobox", { name: "采集级别" }),
    "debug",
  );

  await waitFor(() =>
    expect(diagnosticLevel.set).toHaveBeenCalledWith("debug"),
  );
  expect(
    (
      within(panel).getByRole("combobox", {
        name: "采集级别",
      }) as HTMLSelectElement
    ).value,
  ).toBe("debug");
  expect(
    within(panel)
      .getByRole("button", { name: "全部" })
      .getAttribute("aria-pressed"),
  ).toBe("true");
});

it("shows per-minute model usage in a separate tab", async () => {
  render(<DebugLogPanel />);
  await userEvent.click(
    screen.getByRole("button", { name: "打开 Debug 日志" }),
  );
  const panel = await screen.findByRole("complementary", {
    name: "Debug 日志",
  });

  await userEvent.click(within(panel).getByRole("tab", { name: "模型用量" }));

  await waitFor(() => expect(diagnosticLevel.usage).toHaveBeenCalled());
  expect(within(panel).getByText("Avg Prompt")).toBeTruthy();
  expect(within(panel).getByText("400")).toBeTruthy();
  expect(within(panel).getAllByText("Judge").length).toBeGreaterThan(0);
  expect(
    within(panel).getByRole("img", {
      name: "按分钟显示各 Prompt 的 Token 花费",
    }),
  ).toBeTruthy();
});
