// @vitest-environment jsdom

import type { SourceResponse } from "@one-memory/contracts";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApplicationsHome,
  CALL_HISTORY_ENTRY_VISIBLE,
} from "./ApplicationsHome";
import * as api from "./api";
import { saveConnectionPreferences } from "./connection-preferences";
import { resetConnectionReconcile } from "./connection-reconcile";
import { LanguageProvider } from "./gui-language";
import { queryClient, resetClient } from "./query-client";

it("localizes the whole home live without repeating reads or changing imported text", async () => {
  let change:
    | ((snapshot: {
        preference: "es" | "zh-CN";
        locale: "es" | "zh-CN";
        revision: number;
      }) => void)
    | undefined;
  Object.assign(window.oneMemory ?? {}, {
    getLanguagePreference: vi
      .fn()
      .mockResolvedValue({ preference: "es", locale: "es", revision: 1 }),
    onLanguagePreferenceChanged: vi.fn((listener) => {
      change = listener;
      return () => {};
    }),
  });
  render(<LanguageProvider>{mountHome()}</LanguageProvider>);
  await screen.findByRole("heading", { name: "Aplicaciones", level: 1 });
  await screen.findByText("实际治理记忆");
  expect(
    screen.getByRole("region", { name: "Resumen de memorias" }),
  ).toBeTruthy();
  expect(screen.getByText("Servicio activo")).toBeTruthy();
  expect(screen.getByText("2 aplicaciones conectadas")).toBeTruthy();
  expect(screen.getAllByRole("button", { name: "Ver detalles" })).toHaveLength(
    3,
  );
  const calls = summary.mock.calls.length;
  fireEvent.click(
    screen.getByRole("button", { name: "Ver las 5 aplicaciones" }),
  );
  act(() => change?.({ preference: "zh-CN", locale: "zh-CN", revision: 2 }));
  expect(
    screen.getByRole("heading", { name: "全部应用", level: 1 }),
  ).toBeTruthy();
  expect(screen.getByText("App 4")).toBeTruthy();
  expect(summary).toHaveBeenCalledTimes(calls);
  act(() => change?.({ preference: "es", locale: "es", revision: 3 }));
  expect(
    screen.getByRole("heading", { name: "Todas las aplicaciones", level: 1 }),
  ).toBeTruthy();
  expect(summary).toHaveBeenCalledTimes(calls);
});

const { summary, processing, sedimentation, history, liveSync } = vi.hoisted(
  () => ({
    summary: vi.fn(),
    processing: vi.fn(),
    sedimentation: vi.fn(),
    history: vi.fn(),
    liveSync: vi.fn(),
  }),
);
vi.mock("./call-history-client", async (original) => ({
  ...(await original<typeof import("./call-history-client")>()),
  getCallHistory: history,
}));
vi.mock("./api", () => ({
  getHomeSummary: summary,
  getLiveSyncHealth: liveSync,
  getMemoryProcessing: processing,
  getSedimentation: sedimentation,
  getSources: vi.fn().mockResolvedValue([]),
}));
const sources = Array.from(
  { length: 5 },
  (_, i) =>
    ({
      source_id: `app-${i}`,
      display_name: `App ${i}`,
      authorized: true,
      sync_enabled: false,
      available: true,
    }) as SourceResponse,
);
const imports = {
  sources,
  jobs: [],
  loading: false,
  health: { status: "ok" },
  sourceSnapshot: { stale: false },
} as unknown as Parameters<typeof ApplicationsHome>[0]["imports"];
beforeEach(() => {
  history
    .mockReset()
    .mockResolvedValue({ available: true, recording_error: false, items: [] });
  imports.refresh = vi.fn();
  summary.mockReset().mockResolvedValue({
    memory_count: 601,
    latest: {
      memory_id: "m1",
      content: "实际治理记忆",
      truncated: false,
      revision: 2,
      source_ids: ["app-0"],
      updated_at: "2026-09-19T00:00:00Z",
    },
    observed_at: "2026-09-19T00:00:00Z",
  });
  processing.mockReset().mockResolvedValue({ running: 0, pending: 0 });
  sedimentation.mockReset().mockResolvedValue({
    enabled: true,
    pending_retained: true,
    imports_unaffected: true,
  });
  liveSync.mockReset().mockResolvedValue([]);
  window.oneMemory = {
    restartBackground: vi.fn().mockResolvedValue({ state: "restarted" }),
    getRecallHosts: vi.fn().mockResolvedValue([
      {
        hostId: "app-0",
        label: "App 0",
        present: true,
        installed: true,
        permission: { state: "approved" },
      },
      {
        hostId: "app-1",
        label: "App 1",
        present: true,
        installed: true,
        permission: { state: "approved" },
      },
    ]),
  } as unknown as NonNullable<typeof window.oneMemory>;
});
it.each([
  [true, "Organizando", "Organizando nuevas conversaciones"],
  [false, "En pausa", "Se dejó de generar memorias nuevas"],
])(
  "renders the Spanish sedimentation state without extra requests: %s",
  async (enabled, label, detail) => {
    // Task rows still running must not override the sedimentation switch.
    processing.mockResolvedValue({ running: 3, pending: 10 });
    sedimentation.mockResolvedValue({
      enabled,
      pending_retained: true,
      imports_unaffected: true,
    });
    Object.assign(window.oneMemory ?? {}, {
      getLanguagePreference: vi
        .fn()
        .mockResolvedValue({ preference: "es", locale: "es", revision: 1 }),
      onLanguagePreferenceChanged: vi.fn(() => () => {}),
    });
    render(<LanguageProvider>{mountHome()}</LanguageProvider>);
    expect(await screen.findAllByText(label)).toHaveLength(2);
    expect(screen.getByText(detail)).toBeTruthy();
    expect(sedimentation).toHaveBeenCalledOnce();
  },
);
it("localizes startup recovery and empty states without leaking errors", async () => {
  summary.mockRejectedValue(new Error("synthetic_private_error"));
  Object.assign(window.oneMemory ?? {}, {
    getLanguagePreference: vi
      .fn()
      .mockResolvedValue({ preference: "es", locale: "es", revision: 1 }),
    onLanguagePreferenceChanged: vi.fn(() => () => {}),
  });
  render(
    <LanguageProvider>
      <ApplicationsHome
        imports={{ ...imports, sources: [] }}
        onManage={() => {}}
        onMemories={() => {}}
        onSettings={() => {}}
      />
    </LanguageProvider>,
  );
  expect(
    await screen.findByText(
      "El servicio se está iniciando; se actualizará automáticamente",
    ),
  ).toBeTruthy();
  expect(screen.getByText("0")).toBeTruthy();
  expect(screen.getByText("Aún no hay aplicaciones para mostrar")).toBeTruthy();
  expect(
    screen.queryByRole("button", { name: "Reintentar lectura" }),
  ).toBeNull();
  expect(document.body.textContent).not.toContain("synthetic_private_error");
});
it("shows latest recorded call separately without exposing recent-update detail entries", async () => {
  history.mockResolvedValue({
    available: true,
    recording_error: false,
    items: [
      {
        event_id: "event-1",
        host_id: "codex",
        state: "grounded",
        occurred_at: new Date().toISOString(),
        memory_count: 2,
        raw_count: 1,
        references: [
          { kind: "memory", id: "m1", revision: 2, source_id: "app-0" },
        ],
      },
    ],
  });
  const onCalls = vi.fn();
  const onMemories = vi.fn();
  render(
    <ApplicationsHome
      imports={imports}
      onManage={() => {}}
      onMemories={onMemories}
      onSettings={() => {}}
      onCalls={onCalls}
    />,
  );
  await screen.findByText("实际治理记忆");
  fireEvent.click(screen.getByRole("tab", { name: "最近调用" }));
  await screen.findByText("Codex 查到 3 条相关内容");
  expect(document.querySelector(".r6-home-call-direction")).toBeTruthy();
  expect(screen.queryByText("实际治理记忆")).toBeNull();
  expect(screen.getByText(/Codex · 今天 .+ · 查询成功/)).toBeTruthy();
  fireEvent.click(screen.getByRole("tab", { name: "最近更新" }));
  expect(screen.getByText("实际治理记忆")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "查看记忆" })).toBeNull();
  expect(onMemories).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("tab", { name: "最近调用" }));
  expect(history).toHaveBeenCalledWith({ limit: 20, offset: 0 });
  if (CALL_HISTORY_ENTRY_VISIBLE) {
    fireEvent.click(screen.getByRole("button", { name: "查看记录" }));
    expect(onCalls).toHaveBeenCalledOnce();
  } else {
    // D96: the recent call still shows on the home page; only the way into
    // the call-history page is hidden.
    expect(screen.getByText(/Codex · 今天 .+ · 查询成功/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "查看记录" })).toBeNull();
    expect(onCalls).not.toHaveBeenCalled();
  }
});
it("skips direct diagnostics and shows the latest attributed application call", async () => {
  history.mockResolvedValue({
    available: true,
    recording_error: false,
    items: [
      {
        event_id: "direct-diagnostic",
        host_id: null,
        state: "grounded",
        occurred_at: new Date().toISOString(),
        memory_count: 1,
        raw_count: 0,
        references: [],
      },
      {
        event_id: "workbuddy-call",
        host_id: "workbuddy",
        state: "grounded",
        occurred_at: new Date(Date.now() - 60_000).toISOString(),
        memory_count: 5,
        raw_count: 0,
        references: [],
      },
    ],
  });
  render(
    <ApplicationsHome
      imports={imports}
      onManage={() => {}}
      onMemories={() => {}}
      onSettings={() => {}}
      onCalls={() => {}}
    />,
  );

  fireEvent.click(await screen.findByRole("tab", { name: "最近调用" }));
  expect(await screen.findByText("WorkBuddy 查到 5 条相关内容")).toBeTruthy();
  expect(screen.queryByText(/直接调用/)).toBeNull();
  expect(screen.getByText(/WorkBuddy · 今天 .+ · 查询成功/)).toBeTruthy();
  const direction = document.querySelector(".r6-home-call-direction");
  expect(direction?.querySelector("img")?.getAttribute("src")).toContain(
    "/r6/workbuddy.png",
  );
  expect(direction?.querySelector(".r6-app-initial")).toBeNull();
});
it("shows only the host for memory hits whose references have no source", async () => {
  history.mockResolvedValue({
    available: true,
    recording_error: false,
    items: [
      {
        event_id: "event-1",
        host_id: "workbuddy",
        tool: "om_recall",
        state: "grounded",
        occurred_at: new Date().toISOString(),
        memory_count: 2,
        raw_count: 0,
        references: [
          { kind: "memory", id: "m1", revision: 2, source_id: null },
        ],
      },
    ],
  });
  render(
    <ApplicationsHome
      imports={imports}
      onManage={() => {}}
      onMemories={() => {}}
      onSettings={() => {}}
      onCalls={() => {}}
    />,
  );
  fireEvent.click(await screen.findByRole("tab", { name: "最近调用" }));
  await screen.findByText("WorkBuddy 查到 2 条相关内容");
  const direction = document.querySelector(".r6-home-call-direction");
  expect(direction?.querySelectorAll("img")).toHaveLength(1);
  expect(direction?.querySelector("img")?.getAttribute("src")).toContain(
    "/r6/workbuddy.png",
  );
  expect(direction?.querySelector("svg")).toBeNull();
  expect(direction?.querySelector(".r6-app-initial")).toBeNull();
});
it("keeps source to host direction for raw hits with a source", async () => {
  history.mockResolvedValue({
    available: true,
    recording_error: false,
    items: [
      {
        event_id: "event-1",
        host_id: "workbuddy",
        tool: "om_recall",
        state: "grounded",
        occurred_at: new Date().toISOString(),
        memory_count: 0,
        raw_count: 1,
        references: [
          { kind: "raw", id: "e1", revision: 1, source_id: "app-0" },
        ],
      },
    ],
  });
  render(
    <ApplicationsHome
      imports={imports}
      onManage={() => {}}
      onMemories={() => {}}
      onSettings={() => {}}
      onCalls={() => {}}
    />,
  );
  fireEvent.click(await screen.findByRole("tab", { name: "最近调用" }));
  await screen.findByText("WorkBuddy 查到 1 条相关内容");
  const direction = document.querySelector(".r6-home-call-direction");
  expect(direction?.querySelectorAll("img")).toHaveLength(1);
  expect(direction?.querySelector("svg")).toBeTruthy();
  expect(direction?.querySelector(".r6-app-initial")?.textContent).toBe("A");
});
it("shows unlinked apps and the latest returned call on matching rows", async () => {
  history.mockResolvedValue({
    available: true,
    recording_error: false,
    items: [
      {
        event_id: "event-1",
        host_id: "cursor",
        state: "grounded",
        occurred_at: new Date().toISOString(),
        memory_count: 2,
        raw_count: 1,
      },
    ],
  });
  render(
    <ApplicationsHome
      imports={
        {
          ...imports,
          sources: [
            {
              ...sources[0],
              source_id: "cursor",
              display_name: "Cursor",
            },
            {
              ...sources[1],
              source_id: "codebuddy",
              display_name: "CodeBuddy",
              authorized: false,
              detected: true,
            },
          ],
        } as typeof imports
      }
      onManage={() => {}}
      onMemories={() => {}}
      onSettings={() => {}}
      onCalls={() => {}}
    />,
  );
  expect(await screen.findByText(/Cursor 查到 3 条相关内容 ·/)).toBeTruthy();
  expect(screen.getByText("尚未关联")).toBeTruthy();
});
it("keeps the latest call visible when recording is incomplete", async () => {
  history.mockResolvedValue({
    available: true,
    recording_error: true,
    items: [
      {
        event_id: "event-1",
        host_id: "cursor",
        state: "grounded",
        occurred_at: new Date().toISOString(),
        memory_count: 1,
        raw_count: 0,
      },
    ],
  });
  render(
    <ApplicationsHome
      imports={imports}
      onManage={() => {}}
      onMemories={() => {}}
      onSettings={() => {}}
      onCalls={() => {}}
    />,
  );
  fireEvent.click(await screen.findByRole("tab", { name: "最近调用" }));
  await screen.findByText("Cursor 查到 1 条相关内容");
  expect(screen.getByText(/有几次调用没被记下来/)).toBeTruthy();
  expect(screen.queryByText("暂时无法读取调用记录")).toBeNull();
  expect(screen.queryByText("暂无已记录调用")).toBeNull();
});
it("does not present failed or disabled call history as zero usage", async () => {
  history.mockRejectedValue(Error("private error"));
  render(
    <ApplicationsHome
      imports={imports}
      onManage={() => {}}
      onMemories={() => {}}
      onSettings={() => {}}
      onCalls={() => {}}
    />,
  );
  await screen.findByText("实际治理记忆");
  expect(screen.queryByText("暂无已记录调用")).toBeNull();
  expect(document.body.textContent).not.toContain("private error");
  expect(screen.queryByText("暂时无法读取调用记录")).toBeNull();
});
it("marks a retained application activity as last-read when call history cannot refresh", async () => {
  history
    .mockResolvedValueOnce({
      available: true,
      recording_error: false,
      items: [
        {
          event_id: "event-1",
          host_id: "app-0",
          state: "grounded",
          occurred_at: new Date().toISOString(),
          memory_count: 2,
          raw_count: 1,
          references: [
            { kind: "memory", id: "m1", revision: 2, source_id: "app-0" },
          ],
        },
      ],
    })
    .mockRejectedValueOnce(Error("refresh failed"));
  const view = render(
    <ApplicationsHome
      active
      imports={imports}
      onManage={() => {}}
      onMemories={() => {}}
      onSettings={() => {}}
      onCalls={() => {}}
    />,
  );

  await screen.findByText(/App 0 查到 3 条相关内容 ·/);
  view.rerender(
    <ApplicationsHome
      active={false}
      imports={imports}
      onManage={() => {}}
      onMemories={() => {}}
      onSettings={() => {}}
      onCalls={() => {}}
    />,
  );
  view.rerender(
    <ApplicationsHome
      active
      imports={imports}
      onManage={() => {}}
      onMemories={() => {}}
      onSettings={() => {}}
      onCalls={() => {}}
    />,
  );
  await waitFor(() => expect(history).toHaveBeenCalledTimes(2));

  expect(await screen.findByText("实际治理记忆")).toBeTruthy();
  expect(screen.getByText(/上次读取：App 0 查到 3 条相关内容/)).toBeTruthy();
  expect(screen.queryByText(/^App 0 查到/)).toBeNull();
});
it("does not say an unavailable call returned memories", async () => {
  history.mockResolvedValue({
    available: true,
    recording_error: false,
    items: [
      {
        event_id: "event-1",
        host_id: "app-0",
        state: "unavailable",
        occurred_at: new Date().toISOString(),
        memory_count: 0,
        raw_count: 0,
        references: [],
      },
    ],
  });
  render(
    <ApplicationsHome
      imports={imports}
      onManage={() => {}}
      onMemories={() => {}}
      onSettings={() => {}}
      onCalls={() => {}}
    />,
  );

  expect(
    await screen.findByText("后台服务没有运行，应用暂时查不到记忆"),
  ).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "重新启动" }));
  await waitFor(() =>
    expect(window.oneMemory?.restartBackground).toHaveBeenCalledOnce(),
  );
  expect(screen.queryByText(/App 0 暂时无法查记忆/)).toBeNull();
  expect(screen.queryByText(/查到 [0-9]+ 条相关内容/)).toBeNull();
});

it("warns only after three consecutive timeouts and keeps them out of app rows", async () => {
  history.mockResolvedValue({
    available: true,
    recording_error: false,
    items: Array.from({ length: 3 }, (_, index) => ({
      event_id: `timeout-${index}`,
      host_id: "workbuddy",
      tool: "om_recall",
      state: "timed_out",
      occurred_at: new Date(Date.now() - index * 1000).toISOString(),
      memory_count: 0,
      raw_count: 0,
      references: [],
    })),
  });
  const onCalls = vi.fn();
  render(
    <ApplicationsHome
      imports={imports}
      onManage={() => {}}
      onMemories={() => {}}
      onSettings={() => {}}
      onCalls={onCalls}
    />,
  );

  expect(
    await screen.findByText(
      "WorkBuddy 最近几次没查到记忆，后台可能正忙，稍后会自动恢复",
    ),
  ).toBeTruthy();
  expect(screen.queryByText(/WorkBuddy 这次没来得及/)).toBeNull();
  if (CALL_HISTORY_ENTRY_VISIBLE) {
    fireEvent.click(screen.getByRole("button", { name: "查看记录" }));
    expect(onCalls).toHaveBeenCalledOnce();
  } else {
    // D96: the warning stays, without a button into the call history.
    expect(screen.queryByRole("button", { name: "查看记录" })).toBeNull();
  }
});
it("shows the consecutive-timeout warning in Spanish with the host name", async () => {
  history.mockResolvedValue({
    available: true,
    recording_error: false,
    items: Array.from({ length: 3 }, (_, index) => ({
      event_id: `timeout-es-${index}`,
      host_id: "workbuddy",
      tool: "om_recall",
      state: "timed_out",
      occurred_at: new Date(Date.now() - index * 1000).toISOString(),
      memory_count: 0,
      raw_count: 0,
      references: [],
    })),
  });
  Object.assign(window.oneMemory ?? {}, {
    getLanguagePreference: vi
      .fn()
      .mockResolvedValue({ preference: "es", locale: "es", revision: 1 }),
    onLanguagePreferenceChanged: vi.fn(() => () => {}),
  });
  render(
    <LanguageProvider>
      <ApplicationsHome
        imports={imports}
        onManage={() => {}}
        onMemories={() => {}}
        onSettings={() => {}}
        onCalls={() => {}}
      />
    </LanguageProvider>,
  );
  const warning = await screen.findByText(/Las últimas consultas de WorkBuddy/);
  expect(warning.textContent).not.toMatch(/[\u4e00-\u9fff]/);
});
it("uses live-sync success time for an application row", async () => {
  liveSync.mockResolvedValue([
    {
      source_id: "app-0",
      mode: "watch_and_polling",
      state: "idle",
      last_job_id: "job-1",
      last_attempt_at: "2026-09-20T09:00:00Z",
      last_success_at: new Date().toISOString(),
      last_error_code: null,
    },
  ]);
  render(
    <ApplicationsHome
      imports={imports}
      onManage={() => {}}
      onMemories={() => {}}
      onSettings={() => {}}
    />,
  );
  expect(await screen.findByText(/最近同步 今天/)).toBeTruthy();
});
it.each([
  [{ running: 1, pending: 0 }, "正在沉淀第一批记忆"],
  [{ running: 0, pending: 1 }, "准备沉淀第一批记忆"],
  [{ running: 0, pending: 0, waiting_for_model: 1 }, "准备沉淀第一批记忆"],
])(
  "renders the first-memory state from processing: %j",
  async (value, label) => {
    summary.mockResolvedValue({
      memory_count: 0,
      latest: null,
      observed_at: "2026-09-20T09:00:00Z",
    });
    processing.mockResolvedValue(value);
    render(
      <ApplicationsHome
        imports={imports}
        onManage={() => {}}
        onMemories={() => {}}
        onSettings={() => {}}
        onCalls={() => {}}
      />,
    );
    expect(await screen.findByText(label)).toBeTruthy();
    expect(
      screen.getByText("历史已保存，记忆生成后会显示在这里。"),
    ).toBeTruthy();
  },
);

afterEach(() => {
  cleanup();
  resetClient();
  resetConnectionReconcile();
  window.localStorage.clear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  delete document.documentElement.dataset.nativeReduceMotion;
  Reflect.deleteProperty(Element.prototype, "animate");
});
it("reads sources again on a later visit once the earlier read has gone stale", async () => {
  const getSources = vi.mocked(api.getSources);
  getSources.mockClear();
  const home = (active: boolean) => (
    <ApplicationsHome
      imports={imports}
      active={active}
      onManage={() => {}}
      onMemories={() => {}}
      onSettings={() => {}}
    />
  );
  const view = render(home(true));
  await waitFor(() => expect(getSources).toHaveBeenCalledTimes(1));
  await act(async () => {
    await getSources.mock.results[0]?.value;
  });
  const later = Date.now() + 10 * 60_000;
  vi.spyOn(Date, "now").mockReturnValue(later);
  view.rerender(home(false));
  view.rerender(home(true));
  await waitFor(() => expect(getSources).toHaveBeenCalledTimes(2));
});
it("uses full summary count, renders latest real memory and expands all applications", async () => {
  render(
    <ApplicationsHome
      imports={imports}
      onManage={() => {}}
      onMemories={() => {}}
      onSettings={() => {}}
    />,
  );
  await screen.findByText("601");
  expect(screen.getByText("条")).toBeTruthy();
  expect(screen.getByText("后台运行中")).toBeTruthy();
  expect(screen.getByText("2 个应用已接入")).toBeTruthy();
  expect(screen.getByText("实际治理记忆")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "添加应用" })).toBeNull();
  expect(screen.queryByText("App 4")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "查看全部 5 个应用" }));
  expect(screen.getByText("App 4")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "返回首页" }));
  expect(screen.queryByText("App 4")).toBeNull();
});
it("keeps authoritative home and processing data when only the source snapshot becomes stale", async () => {
  const view = render(
    <ApplicationsHome
      imports={imports}
      onManage={() => {}}
      onMemories={() => {}}
      onSettings={() => {}}
    />,
  );
  await screen.findByText("实际治理记忆");
  view.rerender(
    <ApplicationsHome
      imports={
        {
          ...imports,
          sourceSnapshot: { ...imports.sourceSnapshot, stale: true },
        } as typeof imports
      }
      onManage={() => {}}
      onMemories={() => {}}
      onSettings={() => {}}
    />,
  );
  expect(screen.getByText("实际治理记忆")).toBeTruthy();
  expect(screen.getByText("601")).toBeTruthy();
  expect(screen.getByText("后台运行中")).toBeTruthy();
  expect(screen.getByText("2 个应用已接入")).toBeTruthy();
  expect(screen.getAllByText("整理中")).toHaveLength(2);
  expect(screen.queryByText("状态待核对")).toBeNull();
});
it("keeps a neutral zero while the first home summary read recovers", async () => {
  summary.mockRejectedValue(new Error("private offline detail"));
  render(
    <ApplicationsHome
      imports={imports}
      onManage={() => {}}
      onMemories={() => {}}
      onSettings={() => {}}
    />,
  );
  await waitFor(() =>
    expect(screen.getByText("后台正在启动，稍后自动更新")).toBeTruthy(),
  );
  expect(screen.getByText("0")).toBeTruthy();
  expect(screen.getByText("正在准备记忆概览")).toBeTruthy();
  expect(screen.queryByText(/暂时?无法读取|读取失败/)).toBeNull();
  expect(document.body.textContent).not.toContain("private offline detail");
});
it("offers a calm retry after repeated home summary failures", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    summary.mockRejectedValue(new Error("private offline detail"));
    render(
      <ApplicationsHome
        imports={imports}
        onManage={() => {}}
        onMemories={() => {}}
        onSettings={() => {}}
      />,
    );
    await waitFor(() =>
      expect(screen.getByText("后台正在启动，稍后自动更新")).toBeTruthy(),
    );
    await act(async () => vi.advanceTimersByTimeAsync(10_000));
    expect(await screen.findByText("记忆概览还没准备好")).toBeTruthy();
    expect(
      screen.getByText("后台恢复后会自动更新，也可以重新读取。"),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "重新读取" })).toBeTruthy();
  } finally {
    vi.useRealTimers();
  }
});
it("counts repeated stale summaries and recovers on the next confirmed read", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    const stale = {
      memory_count: 0,
      latest: null,
      observed_at: "2026-09-19T00:00:00Z",
      stale: true,
    };
    summary
      .mockResolvedValueOnce(stale)
      .mockResolvedValueOnce(stale)
      .mockResolvedValue({
        memory_count: 4,
        latest: null,
        observed_at: "2026-09-19T00:01:00Z",
      });
    render(
      <ApplicationsHome
        imports={imports}
        onManage={() => {}}
        onMemories={() => {}}
        onSettings={() => {}}
      />,
    );
    await waitFor(() =>
      expect(screen.getByText("后台正在启动，稍后自动更新")).toBeTruthy(),
    );
    await act(async () => vi.advanceTimersByTimeAsync(10_000));
    expect(await screen.findByText("记忆概览还没准备好")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "重新读取" }));
    expect(await screen.findByText("4")).toBeTruthy();
    expect(screen.queryByText("记忆概览还没准备好")).toBeNull();
  } finally {
    vi.useRealTimers();
  }
});
it("labels a retained count as previous data after repeated stale reads", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    const stale = {
      memory_count: 0,
      latest: null,
      observed_at: "2026-09-19T00:00:00Z",
      stale: true,
    };
    summary
      .mockResolvedValueOnce({
        memory_count: 601,
        latest: null,
        observed_at: "2026-09-19T00:00:00Z",
      })
      .mockResolvedValueOnce(stale)
      .mockResolvedValueOnce(stale)
      .mockResolvedValue({
        memory_count: 4,
        latest: null,
        observed_at: "2026-09-19T00:01:00Z",
      });
    render(
      <ApplicationsHome
        imports={imports}
        onManage={() => {}}
        onMemories={() => {}}
        onSettings={() => {}}
      />,
    );
    expect(await screen.findByText("601")).toBeTruthy();
    await act(async () => vi.advanceTimersByTimeAsync(20_000));
    expect(screen.getByText("601")).toBeTruthy();
    expect(screen.getByText("正在显示上次数据，稍后自动更新")).toBeTruthy();
    await act(async () => vi.advanceTimersByTimeAsync(10_000));
    expect(await screen.findByText("4")).toBeTruthy();
    expect(screen.queryByText("正在显示上次数据，稍后自动更新")).toBeNull();
  } finally {
    vi.useRealTimers();
  }
});
it("never calls a recall event a success when only stored memories are available", async () => {
  render(
    <ApplicationsHome
      imports={imports}
      onManage={() => {}}
      onMemories={() => {}}
      onSettings={() => {}}
    />,
  );
  await screen.findByText("实际治理记忆");
  expect(screen.queryByText(/已返回|已采用/)).toBeNull();
});

it.each([
  { running: 0, pending: 0 },
  { running: 0, pending: 0, waiting_for_model: 1 },
  { running: 0, pending: 0, blocked_stages: [{ count: 1 }] },
  { running: 0, pending: 0, failed: 1 },
])("shows the enabled switch instead of task counters: %j", async (value) => {
  processing.mockResolvedValue(value);
  render(
    <ApplicationsHome
      imports={imports}
      onManage={() => {}}
      onMemories={() => {}}
      onSettings={() => {}}
    />,
  );
  expect(await screen.findAllByText("整理中")).toHaveLength(2);
});

it("shows a paused switch immediately even when task rows still say running", async () => {
  processing.mockResolvedValue({ running: 3, pending: 10 });
  sedimentation.mockResolvedValue({
    enabled: false,
    pending_retained: true,
    imports_unaffected: true,
  });

  render(
    <ApplicationsHome
      imports={imports}
      onManage={() => {}}
      onMemories={() => {}}
      onSettings={() => {}}
    />,
  );

  expect(await screen.findAllByText("已暂停")).toHaveLength(2);
  expect(screen.getByText("已停止生成新记忆")).toBeTruthy();
  expect(screen.queryByText("新的对话正在整理")).toBeNull();
});

it("keeps the import-in-progress empty state stable without a progress link", async () => {
  summary.mockResolvedValue({
    memory_count: 0,
    latest: null,
    observed_at: "2026-09-19T00:00:00Z",
  });
  const importing = {
    ...imports,
    jobs: [{ source_id: "app-0", status: "running" }],
  } as unknown as Parameters<typeof ApplicationsHome>[0]["imports"];
  render(
    <ApplicationsHome
      imports={importing}
      onManage={() => {}}
      onMemories={() => {}}
      onSettings={() => {}}
    />,
  );
  expect(
    await screen.findByText("对话持续导入中，即将沉淀你的记忆"),
  ).toBeTruthy();
  expect(screen.queryByRole("button", { name: "查看进度" })).toBeNull();
});

it.each([null, "", "not-a-date"])(
  "keeps the home usable when the latest timestamp is unavailable: %s",
  async (updated_at) => {
    summary.mockResolvedValue({
      memory_count: 1,
      latest: {
        memory_id: "m1",
        content: "实际治理记忆",
        revision: 2,
        source_ids: ["app-0"],
        updated_at,
      },
    });
    render(
      <ApplicationsHome
        imports={imports}
        onManage={() => {}}
        onMemories={() => {}}
        onSettings={() => {}}
      />,
    );
    await screen.findByText("实际治理记忆");
    expect(screen.getByText("时间未知 · App 0")).toBeTruthy();
  },
);

it("shows only time and source for an SDK recent update", async () => {
  summary.mockResolvedValue({
    memory_count: 1,
    latest: {
      memory_id: "sdk-memory-1",
      content: "SDK 原生记忆",
      layer: "l2_fact",
      source: "app-0",
      gmt_created: 1_761_000_000,
    },
    observed_at: "2026-09-20T09:00:00Z",
  });

  render(
    <ApplicationsHome
      imports={imports}
      onManage={() => {}}
      onMemories={() => {}}
      onSettings={() => {}}
    />,
  );

  await screen.findByText("SDK 原生记忆");
  expect(screen.getByText(/^\d{2}:\d{2} · App 0$/)).toBeTruthy();
  expect(document.body.textContent).not.toContain("SDK l2_fact");
  expect(screen.queryByRole("button", { name: "查看记忆" })).toBeNull();
});

it("does not treat an available adapter as an installed application", async () => {
  render(
    <ApplicationsHome
      imports={{
        ...imports,
        sources: [
          {
            ...sources[0],
            authorized: false,
            detected: false,
            available: true,
            display_name: "Not installed",
          },
        ],
      }}
      onManage={() => {}}
      onMemories={() => {}}
      onSettings={() => {}}
    />,
  );
  await screen.findByText("601");
  expect(screen.queryByRole("heading", { name: "Not installed" })).toBeNull();
});

function mountHome(active = true) {
  return (
    <ApplicationsHome
      imports={imports}
      onManage={() => {}}
      onMemories={() => {}}
      onSettings={() => {}}
      active={active}
    />
  );
}

function mockMotion() {
  const media = new EventTarget() as EventTarget & { matches: boolean };
  media.matches = false;
  vi.stubGlobal("matchMedia", () => media);
  const jobs: {
    element: Element;
    frames: Keyframe[];
    options: KeyframeAnimationOptions;
    cancel: ReturnType<typeof vi.fn>;
    finish(): void;
  }[] = [];
  Object.defineProperty(Element.prototype, "animate", {
    configurable: true,
    value: vi.fn(function (
      this: Element,
      frames: Keyframe[],
      options: KeyframeAnimationOptions,
    ) {
      let finish = () => {};
      const finished = new Promise<void>((resolve) => {
        finish = resolve;
      });
      const cancel = vi.fn(finish);
      jobs.push({ element: this, frames, options, cancel, finish });
      return { finished, cancel };
    }),
  });
  return {
    jobs,
    media,
    finish: async () => {
      await act(async () => {
        jobs
          .filter((j) => !j.cancel.mock.calls.length)
          .forEach((j) => {
            j.finish();
          });
      });
    },
  };
}

it("sequences a quick overview exit before translation-only expansion and reverses it", async () => {
  const motion = mockMotion();
  const view = render(mountHome());
  await screen.findByText("601");
  const expand = screen.getByRole("button", { name: "查看全部 5 个应用" });
  fireEvent.click(expand);
  expect(motion.jobs).toHaveLength(1);
  expect(motion.jobs[0].element.className).toBe("r6-home-top");
  expect(motion.jobs[0].options.duration).toBe(90);
  expect(screen.queryByText("App 4")).toBeNull();
  await motion.finish();
  expect(screen.getByText("App 4")).toBeTruthy();
  expect(
    motion.jobs.find((j) => j.element.className === "r6-home-apps")?.options
      .duration,
  ).toBe(190);
  expect(JSON.stringify(motion.jobs.map((j) => j.frames))).not.toMatch(/scale/);
  await motion.finish();
  expect(document.activeElement).toBe(
    screen.getByRole("button", { name: "返回首页" }),
  );
  fireEvent.click(screen.getByRole("button", { name: "返回首页" }));
  expect(
    view.container.querySelector(".r6-home")?.getAttribute("data-phase"),
  ).toBe("collapsing");
  await motion.finish();
  expect(motion.jobs.at(-1)?.options.duration).toBe(100);
  await motion.finish();
  expect(document.activeElement).toBe(expand);
  expect(expand.getAttribute("aria-expanded")).toBe("false");
});

it("expands on downward wheel and collapses on upward wheel at the list top", async () => {
  document.documentElement.dataset.nativeReduceMotion = "true";
  render(mountHome());
  await screen.findByText("601");
  const panel = screen.getByRole("region", { name: "我的应用" });
  fireEvent.wheel(panel, { deltaY: 80 });
  expect(screen.getByText("App 4")).toBeTruthy();
  const list = screen.getByRole("region", { name: "应用列表" });
  list.scrollTop = 0;
  fireEvent.wheel(panel, { deltaY: -80 });
  expect(screen.queryByText("App 4")).toBeNull();
});

it("Escape interrupts overview exit without a stale expansion completing", async () => {
  const motion = mockMotion();
  const view = render(mountHome());
  await screen.findByText("601");
  fireEvent.click(screen.getByRole("button", { name: "查看全部 5 个应用" }));
  fireEvent.keyDown(screen.getByRole("region", { name: "应用首页" }), {
    key: "Escape",
  });
  await motion.finish();
  await motion.finish();
  expect(motion.jobs[0].cancel).toHaveBeenCalled();
  expect(screen.queryByText("App 4")).toBeNull();
  expect(
    view.container.querySelector(".r6-home")?.getAttribute("data-phase"),
  ).toBe("idle");
});

it("Escape still interrupts after the disappearing trigger loses focus to body", async () => {
  const motion = mockMotion();
  render(mountHome());
  await screen.findByText("601");
  fireEvent.click(screen.getByRole("button", { name: "查看全部 5 个应用" }));
  await motion.finish();
  fireEvent.keyDown(document.body, { key: "Escape" });
  await motion.finish();
  await motion.finish();
  expect(screen.queryByText("App 4")).toBeNull();
  expect(
    screen.getByRole("region", { name: "应用首页" }).getAttribute("data-phase"),
  ).toBe("idle");
});

it.each(["native", "prefers"])(
  "%s reduce motion settles an in-flight transition immediately",
  async (kind) => {
    const motion = mockMotion();
    const view = render(mountHome());
    await screen.findByText("601");
    fireEvent.click(screen.getByRole("button", { name: "查看全部 5 个应用" }));
    await act(async () => {
      if (kind === "native")
        document.documentElement.dataset.nativeReduceMotion = "true";
      else {
        motion.media.matches = true;
        motion.media.dispatchEvent(new Event("change"));
      }
    });
    expect(screen.getByText("App 4")).toBeTruthy();
    expect(motion.jobs.every((j) => j.cancel.mock.calls.length > 0)).toBe(true);
    expect(
      view.container.querySelector(".r6-home")?.getAttribute("data-phase"),
    ).toBe("idle");
    const count = motion.jobs.length;
    fireEvent.click(screen.getByRole("button", { name: "返回首页" }));
    expect(motion.jobs).toHaveLength(count);
    expect(screen.queryByText("App 4")).toBeNull();
  },
);

it("preserves independent nonzero list scroll through collapse and inactive navigation", async () => {
  const view = render(mountHome());
  await screen.findByText("601");
  fireEvent.click(screen.getByRole("button", { name: "查看全部 5 个应用" }));
  const list = screen.getByRole("region", { name: "应用列表" });
  list.scrollTop = 240;
  fireEvent.scroll(list);
  const detail = screen.getAllByRole("button", { name: "查看详情" })[3];
  detail.focus();
  view.rerender(mountHome(false));
  view.rerender(mountHome(true));
  expect(screen.getByText("App 4")).toBeTruthy();
  expect(list.scrollTop).toBe(240);
  expect(document.activeElement).toBe(detail);
  fireEvent.click(screen.getByRole("button", { name: "返回首页" }));
  fireEvent.click(screen.getByRole("button", { name: "查看全部 5 个应用" }));
  expect(list.scrollTop).toBe(240);
});

it.each(["unmount", "inactive"])(
  "cancels animation work on %s without deferred layout or focus",
  async (mode) => {
    const motion = mockMotion();
    const raf = vi.spyOn(window, "requestAnimationFrame");
    const view = render(mountHome());
    await screen.findByText("601");
    fireEvent.click(screen.getByRole("button", { name: "查看全部 5 个应用" }));
    if (mode === "unmount") view.unmount();
    else view.rerender(mountHome(false));
    await motion.finish();
    expect(motion.jobs.every((j) => j.cancel.mock.calls.length > 0)).toBe(true);
    expect(raf).not.toHaveBeenCalled();
    const calls = summary.mock.calls.length;
    expect(calls).toBeGreaterThan(0);
  },
);

it("restores expanded list and source focus after detail unmount, scoped to the import owner", async () => {
  const view = render(mountHome());
  await screen.findByText("601");
  fireEvent.click(screen.getByRole("button", { name: "查看全部 5 个应用" }));
  const list = screen.getByRole("region", { name: "应用列表" });
  list.scrollTop = 220;
  fireEvent.scroll(list);
  screen.getAllByRole("button", { name: "查看详情" })[3].focus();
  view.unmount();
  const returned = render(mountHome());
  expect(screen.getByText("App 4")).toBeTruthy();
  expect(screen.getByRole("region", { name: "应用列表" }).scrollTop).toBe(220);
  expect(document.activeElement).toBe(
    screen.getAllByRole("button", { name: "查看详情" })[3],
  );
  returned.unmount();
  imports.refresh = vi.fn();
  render(mountHome());
  expect(screen.queryByText("App 4")).toBeNull();
});

it("does not show zero connected apps before host status is ready", async () => {
  window.oneMemory = {
    getRecallHosts: vi.fn(() => new Promise(() => {})),
  } as unknown as NonNullable<typeof window.oneMemory>;
  render(
    <ApplicationsHome
      imports={imports}
      onManage={() => {}}
      onMemories={() => {}}
      onSettings={() => {}}
    />,
  );
  await screen.findByText("601");
  expect(screen.getByText("正在核对中")).toBeTruthy();
  expect(screen.queryByText("0 个应用已接入")).toBeNull();
});

it("does not keep saying sync is being checked after the remembered apply failed", async () => {
  saveConnectionPreferences({ syncSourceIds: ["app-0"], hostSourceIds: [] });
  const setSync = vi.fn().mockRejectedValue(new Error("offline"));
  render(
    <ApplicationsHome
      imports={{ ...imports, setSync } as typeof imports}
      onManage={() => {}}
      onMemories={() => {}}
      onSettings={() => {}}
    />,
  );

  expect(await screen.findByText(/未能自动开启/)).toBeTruthy();
  const sync = screen.getByText("对话同步").closest("div");
  expect(sync?.textContent).not.toContain("正在检查状态");
  expect(sync?.textContent).toContain("未能开启");
  expect(screen.getByRole("button", { name: "重新接入" })).toBeTruthy();
});

it("keeps the conversation-sync checking copy before the first home read", () => {
  summary.mockImplementation(() => new Promise(() => {}));
  render(
    <ApplicationsHome
      imports={imports}
      onManage={() => {}}
      onMemories={() => {}}
      onSettings={() => {}}
    />,
  );
  expect(screen.getByText("正在检查状态")).toBeTruthy();
  expect(screen.queryByText(/个应用已开启/)).toBeNull();
});

it("shows a provisional zero while the first summary read is pending", async () => {
  summary.mockImplementation(() => new Promise(() => {}));
  history.mockResolvedValue({
    available: true,
    recording_error: false,
    items: [],
  });
  processing.mockResolvedValue({ running: 0, pending: 0 });
  render(
    <ApplicationsHome
      imports={imports}
      onManage={() => {}}
      onMemories={() => {}}
      onSettings={() => {}}
    />,
  );
  await screen.findByText("正在准备记忆概览");
  expect(screen.getByText("0")).toBeTruthy();
  expect(screen.getByText("正在核对本机记忆…")).toBeTruthy();
  expect(screen.queryByText("还没有已生成的记忆")).toBeNull();
});

it("counts enabled apps for conversation sync and does not report a degraded run", async () => {
  liveSync.mockResolvedValue([
    {
      source_id: "app-0",
      mode: "polling",
      state: "degraded",
      last_job_id: "job-1",
      last_attempt_at: "2026-09-20T09:00:00Z",
      last_success_at: null,
      last_error_code: "live_sync_import_errors",
    },
  ]);
  render(
    <ApplicationsHome
      imports={
        {
          ...imports,
          sources: imports.sources.map((source, index) =>
            index < 2 ? { ...source, sync_enabled: true } : source,
          ),
          jobs: [{ source_id: "app-0", status: "running" }],
        } as unknown as Parameters<typeof ApplicationsHome>[0]["imports"]
      }
      onManage={() => {}}
      onMemories={() => {}}
      onSettings={() => {}}
    />,
  );
  await screen.findByText("601");
  const sync = screen.getByText("对话同步").closest("div");
  expect(sync?.textContent).toContain("2 个应用已开启");
  expect(sync?.textContent).not.toContain("部分异常");
  expect(sync?.textContent).not.toContain("正在导入");
  expect(sync?.textContent).not.toContain("持续同步已开启");
  expect(await screen.findByText("2 个应用已接入")).toBeTruthy();
});

it("does not show zero connected apps while cold-start insertion is still applying", async () => {
  saveConnectionPreferences({
    syncSourceIds: ["app-0"],
    hostSourceIds: ["cursor"],
  });
  window.oneMemory = {
    getRecallHosts: vi.fn().mockResolvedValue([]),
    previewRecallInstall: vi.fn(() => new Promise(() => {})),
    installRecall: vi.fn(),
    getRecallPermission: vi.fn(),
    setRecallPermission: vi.fn(),
  } as unknown as NonNullable<typeof window.oneMemory>;
  render(
    <ApplicationsHome
      imports={imports}
      onManage={() => {}}
      onMemories={() => {}}
      onSettings={() => {}}
    />,
  );
  await screen.findByText("601");
  expect(screen.getByText("正在核对中")).toBeTruthy();
  expect(screen.queryByText("0 个应用已接入")).toBeNull();
});

function homeWithHosts(getRecallHosts: ReturnType<typeof vi.fn>) {
  queryClient.clear();
  window.oneMemory = { getRecallHosts } as unknown as NonNullable<
    typeof window.oneMemory
  >;
  render(
    <ApplicationsHome
      imports={imports}
      onManage={() => {}}
      onMemories={() => {}}
      onSettings={() => {}}
    />,
  );
}
function accessRow() {
  return screen.getByText("应用访问").closest("div")?.textContent ?? "";
}
const installed = (hostId: string, permission?: object) => ({
  hostId,
  label: hostId === "app-0" ? "App 0" : "App 1",
  present: true,
  installed: true,
  ...(permission
    ? { permission: { hostId, reason: null, ...permission } }
    : {}),
});
const unreadable = {
  state: "unavailable",
  reason: "permission_store_unavailable",
};

it("never renders an app whose permission could not be read as zero connected", async () => {
  const read = vi
    .fn()
    .mockResolvedValueOnce([installed("app-0", unreadable)])
    .mockResolvedValue([installed("app-0", { state: "approved" })]);
  homeWithHosts(read);
  await waitFor(() => expect(accessRow()).toContain("1 个应用待核对"));
  expect(screen.queryByText("0 个应用已接入")).toBeNull();
  expect(
    screen.getByText("App 0 的记忆权限暂时没有读到，无法确认是否已接入。"),
  ).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "重新核对" }));
  await waitFor(() => expect(accessRow()).toContain("1 个应用已接入"));
  expect(read).toHaveBeenCalledTimes(2);
  expect(screen.queryByRole("button", { name: "重新核对" })).toBeNull();
});

it("treats a host without any permission answer as unchecked, not unconnected", async () => {
  homeWithHosts(vi.fn().mockResolvedValue([installed("app-0")]));
  await waitFor(() => expect(accessRow()).toContain("1 个应用待核对"));
  expect(screen.queryByText("0 个应用已接入")).toBeNull();
});

it("keeps the known count and names only the unchecked rest", async () => {
  homeWithHosts(
    vi
      .fn()
      .mockResolvedValue([
        installed("app-0", { state: "approved" }),
        installed("app-1", unreadable),
      ]),
  );
  await waitFor(() =>
    expect(accessRow()).toContain("1 个应用已接入，1 个待核对"),
  );
  expect(
    screen.getByText("App 1 的记忆权限暂时没有读到，无法确认是否已接入。"),
  ).toBeTruthy();
});

it("says a failed host read is unchecked and offers to read again", async () => {
  const read = vi
    .fn()
    .mockRejectedValueOnce(new Error("host_read_timeout"))
    .mockResolvedValue([installed("app-0", { state: "approved" })]);
  homeWithHosts(read);
  await waitFor(() => expect(accessRow()).toContain("待核对"));
  expect(accessRow()).not.toContain("正在核对中");
  expect(screen.queryByText("0 个应用已接入")).toBeNull();
  expect(
    screen.getByText("应用接入状态暂时没有读到，无法确认有几个应用已接入。"),
  ).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "重新核对" }));
  await waitFor(() => expect(accessRow()).toContain("1 个应用已接入"));
  expect(read).toHaveBeenCalledTimes(2);
});

it("still shows a checked zero as zero", async () => {
  homeWithHosts(
    vi
      .fn()
      .mockResolvedValue([
        installed("app-0", { state: "install-only" }),
        { ...installed("app-1", { state: "not-installed" }), installed: false },
      ]),
  );
  await waitFor(() => expect(accessRow()).toContain("0 个应用已接入"));
  expect(screen.queryByRole("button", { name: "重新核对" })).toBeNull();
});

describe("运行状态卡：一次失败的刷新不能让卡片继续说在运行", () => {
  function renderHome(next: Partial<typeof imports>) {
    render(
      <ApplicationsHome
        imports={{ ...imports, ...next } as typeof imports}
        onManage={() => {}}
        onMemories={() => {}}
        onSettings={() => {}}
      />,
    );
  }

  it("says it is running while the last health read succeeded", async () => {
    renderHome({ healthStale: false });
    await screen.findByText("实际治理记忆");
    expect(screen.getByText("后台运行中")).toBeTruthy();
  });

  it("stops saying it is running once a health read has failed", async () => {
    renderHome({ healthStale: true });
    await screen.findByText("实际治理记忆");
    expect(screen.queryByText("后台运行中")).toBeNull();
    expect(screen.getByText("连接状态待核对")).toBeTruthy();
    // The health object itself is kept: it names the instance that owns the
    // running backfill and the write receipts.
    expect(screen.getByText("601")).toBeTruthy();
  });
});

it("words an automatic sync failure and its reason in Spanish", async () => {
  localStorage.setItem(
    "one-memory.connection-preferences.v1",
    JSON.stringify({ version: 1, syncSourceIds: ["app-0"], hostSourceIds: [] }),
  );
  queryClient.setQueryData(["connection-auto"], {
    running: [],
    attempted: ["app-0"],
    failures: [
      {
        kind: "sync",
        sourceId: "app-0",
        reason: "此应用的读取授权已撤销，请重新授权。",
      },
    ],
  });
  Object.assign(window.oneMemory ?? {}, {
    getLanguagePreference: vi
      .fn()
      .mockResolvedValue({ preference: "es", locale: "es", revision: 1 }),
    onLanguagePreferenceChanged: vi.fn(() => () => {}),
  });
  render(<LanguageProvider>{mountHome()}</LanguageProvider>);
  const notice = await screen.findByText(
    /No se pudo activar la sincronización continua de App 0\./,
  );
  expect(notice.textContent).toContain(
    "Se revocó el permiso de lectura de esta aplicación. Vuelve a autorizarla.",
  );
  expect(notice.textContent).not.toMatch(/[一-鿿]/);
  localStorage.removeItem("one-memory.connection-preferences.v1");
});
