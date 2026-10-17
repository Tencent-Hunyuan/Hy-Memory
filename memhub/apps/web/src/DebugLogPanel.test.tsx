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
import { LanguageProvider, useGuiLanguage } from "./gui-language";
import {
  recordRendererDebugLog,
  resetRendererDebugLogsForTest,
} from "./renderer-debug-log";

const diagnosticLevel = vi.hoisted(() => ({
  get: vi.fn(async () => "info" as const),
  set: vi.fn(async (level: "error" | "warning" | "info" | "debug") => level),
}));

vi.mock("./api", () => ({
  getDiagnosticLogLevel: diagnosticLevel.get,
  setDiagnosticLogLevel: diagnosticLevel.set,
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
  window.localStorage.clear();
  resetRendererDebugLogsForTest();
  diagnosticLevel.get.mockClear();
  diagnosticLevel.set.mockClear();
  window.oneMemory = {
    getDebugLogs: vi.fn().mockResolvedValue(snapshot),
  } as unknown as NonNullable<typeof window.oneMemory>;
});

afterEach(() => {
  cleanup();
  window.oneMemory = undefined;
  vi.useRealTimers();
});

function SwitchLanguage() {
  const { setPreference } = useGuiLanguage();
  return (
    <button type="button" onClick={() => void setPreference("zh-CN")}>
      Cambiar idioma
    </button>
  );
}

it("translates controls live but preserves events and does not change capture", async () => {
  // A language bridge is not required by this synthetic browser fixture.
  const read = vi.fn().mockResolvedValue(snapshot);
  window.oneMemory = undefined;
  localStorage.setItem("one-memory.gui-language", "es");
  const view = render(
    <LanguageProvider>
      <SwitchLanguage />
      <DebugLogPanel />
    </LanguageProvider>,
  );
  window.oneMemory = { getDebugLogs: read } as unknown as NonNullable<
    typeof window.oneMemory
  >;
  view.rerender(
    <LanguageProvider>
      <SwitchLanguage />
      <DebugLogPanel />
    </LanguageProvider>,
  );
  await userEvent.click(
    await screen.findByRole("button", {
      name: "Abrir registros de depuración",
    }),
  );
  await screen.findByText(/source\.scanned/);
  expect(
    screen.getByRole("searchbox", { name: "Buscar registros" }),
  ).toBeTruthy();
  await userEvent.click(
    screen.getByRole("button", { name: "Pausar actualización en vivo" }),
  );
  const count = read.mock.calls.length;
  await userEvent.click(screen.getByRole("button", { name: "Cambiar idioma" }));
  await screen.findByRole("searchbox", { name: "搜索日志" });
  expect(screen.getByText(/source\.scanned/)).toBeTruthy();
  expect(read).toHaveBeenCalledTimes(count);
  expect(diagnosticLevel.set).not.toHaveBeenCalled();
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
