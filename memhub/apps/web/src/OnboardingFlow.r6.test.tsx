// @vitest-environment jsdom
import type { SourceResponse } from "@one-memory/contracts";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { OnboardingFlow } from "./OnboardingFlow";
import { setUiLanguage } from "./ui-language";
import type { ImportSourcesController } from "./use-import-sources";

vi.mock("./api", () => ({ completeOnboarding: vi.fn() }));
vi.mock("./FirstImportReview", () => ({
  FirstImportReview: ({ sourceIds }: { sourceIds: string[] }) => (
    <p>已受理 {sourceIds.join(",")}</p>
  ),
  defaultHostSourceIds: () => [],
}));
beforeEach(() => {
  delete window.oneMemory;
});
afterEach(async () => {
  cleanup();
  delete window.oneMemory;
  vi.useRealTimers();
  await setUiLanguage("zh-CN");
});
const state = {
  status: "not_started",
  selected_source_ids: [],
  started_at: null,
  completed_at: null,
} as const;
function source(
  id: string,
  overrides: Partial<SourceResponse> = {},
): SourceResponse {
  return {
    source_id: id,
    display_name: id,
    available: true,
    authorized: false,
    detected: false,
    storage_kinds: ["jsonl"],
    root_label: null,
    candidate_path: `/fixture/${id}`,
    candidate_kind: "default",
    candidate_root_id: `root-${id}`,
    location_state: "found",
    imported_messages: 0,
    imported_conversations: 0,
    sync_enabled: false,
    sync_completeness: "additions_only",
    capabilities: {
      historical_import: "implemented",
      incremental_import: "implemented",
      live_sync: "not_implemented",
      recall: "not_implemented",
      installer: "not_implemented",
      verification: "fixture_only",
    },
    ...overrides,
  };
}
function controller(sources: SourceResponse[]) {
  return {
    sources,
    jobs: [],
    loading: false,
    refresh: vi.fn().mockResolvedValue({ sources, jobs: [], health: null }),
    connectForOnboarding: vi.fn().mockResolvedValue({
      accepted_source_ids: [sources[0]?.source_id],
      failed_sources: {},
      jobs: [],
    }),
    detect: vi.fn(),
  } as unknown as ImportSourcesController;
}
function mount(imports: ImportSourcesController) {
  return render(
    <OnboardingFlow
      state={{ ...state, selected_source_ids: [] }}
      imports={imports}
      onFinish={() => {}}
    />,
  );
}
it("renders the final shell, double title, step one, branded rows and exact source metadata", async () => {
  const imports = controller([
    source("workbuddy"),
    source("cursor"),
    source("codebuddy"),
  ]);
  const view = mount(imports);
  await waitFor(() =>
    expect(view.container.querySelectorAll(".onboarding-r6-row").length).toBe(
      3,
    ),
  );
  expect(view.container.querySelector(".onboarding-r6-shell")).toBeTruthy();
  expect(view.container.querySelector(".cold-start-layout")).toBeNull();
  expect(
    screen.getByRole("heading", { name: "把熟悉的背景，带到这里" }),
  ).toBeTruthy();
  expect(screen.getByText("你的记忆，从这里接上")).toBeTruthy();
  expect(
    screen
      .getByRole("navigation", { name: "首次设置进度" })
      .querySelector('[aria-current="step"]')?.textContent,
  ).toContain("导入历史");
  expect(screen.getByRole("heading", { name: "本机 AI 应用" })).toBeTruthy();
  expect(
    screen.getByText("确认前只检查应用信息，不读取对话，也不安装连接。"),
  ).toBeTruthy();
  expect(screen.queryByRole("button", { name: "导入历史包" })).toBeNull();
  expect(screen.getByText("/fixture/workbuddy · 默认位置")).toBeTruthy();
  expect(
    view.container.querySelector('img[src$="r6/workbuddy.png"]'),
  ).toBeTruthy();
  expect(
    await within(
      screen.getByRole("complementary", { name: "本次导入" }),
    ).findByText("3"),
  ).toBeTruthy();
  expect(screen.getByText("少一些重复交代，多一些理解。")).toBeTruthy();
  expect(imports.detect).not.toHaveBeenCalled();
});
it("does not fabricate found rows while metadata is pending, then uses actual source count", async () => {
  let done!: (
    v: Awaited<ReturnType<ImportSourcesController["refresh"]>>,
  ) => void;
  const imports = controller([]);
  vi.mocked(imports.refresh).mockReturnValueOnce(
    new Promise((resolve) => {
      done = resolve;
    }),
  );
  const view = mount(imports);
  expect(
    view.container.querySelectorAll(".onboarding-r6-skeleton").length,
  ).toBeGreaterThan(0);
  expect(screen.queryByText("已发现")).toBeNull();
  await act(async () =>
    done({
      sources: [source("codex")],
      jobs: [],
      health: {} as Awaited<
        ReturnType<ImportSourcesController["refresh"]>
      >["health"],
    }),
  );
  expect(screen.getByText("/fixture/codex · 默认位置")).toBeTruthy();
  expect(screen.getAllByText("已发现")).toHaveLength(1);
});
it("starts location discovery once and keeps a single initial focus owner", async () => {
  const imports = controller([source("codex")]);
  mount(imports);
  await waitFor(() => expect(imports.refresh).toHaveBeenCalledTimes(1));
  expect(document.activeElement).toBe(
    screen.getByRole("heading", { name: "本机 AI 应用" }),
  );
});
it("restores the first-step focus when the native window becomes active", async () => {
  const imports = controller([source("codex")]);
  mount(imports);
  const firstStep = screen.getByRole("heading", { name: "本机 AI 应用" });
  (document.activeElement as HTMLElement | null)?.blur();
  expect(document.activeElement).not.toBe(firstStep);

  act(() => window.dispatchEvent(new Event("focus")));

  expect(document.activeElement).toBe(firstStep);
});
it("keeps the summary count real and large enough to see while metadata resolves", async () => {
  const imports = controller([
    source("workbuddy"),
    source("cursor"),
    source("codex"),
  ]);
  const view = mount(imports);
  const summary = screen.getByRole("complementary", { name: "本次导入" });
  const count = summary.querySelector(".onboarding-r6-count");
  expect(count?.classList.contains("onboarding-r6-count")).toBe(true);
  expect(count?.textContent).toBe("1");
  expect(count?.getAttribute("aria-live")).toBe("polite");
  expect(count?.getAttribute("data-unknown")).toBe("true");
  fireEvent.click(screen.getByRole("button", { name: "重新检测位置" }));
  await waitFor(() =>
    expect(summary.querySelector(".onboarding-r6-count")?.textContent).toBe(
      "3",
    ),
  );
  expect(
    summary.querySelector(".onboarding-r6-count")?.getAttribute("data-unknown"),
  ).toBe("false");
  expect(view.container.querySelector(".onboarding-r6-list-area")).toBeTruthy();
});
it("shows 未完成, never a number or zero, when the location check fails", async () => {
  // D51 item 3: only a *confirmed* source snapshot may show a numeric count.
  // A failed detection must say 未完成 — a number here (especially `0`) would be
  // a factual claim about the user's machine that the UI cannot support.
  const imports = controller([source("workbuddy"), source("cursor")]);
  vi.mocked(imports.refresh).mockRejectedValue(new Error("offline"));
  const view = mount(imports);
  const count = () =>
    view.container.querySelector(".onboarding-r6-count")?.textContent;
  await waitFor(() => expect(count()).toBe("未完成"));
  expect(count()).not.toBe("0");
  expect(count()).not.toBe("2");
  // The unknown-state styling must still be applied, so a failure is not
  // presented in the large confirmed-number treatment.
  expect(
    view.container
      .querySelector(".onboarding-r6-count")
      ?.getAttribute("data-unknown"),
  ).toBe("true");
  // And it must be announced as a failure in words, not only as a glyph.
  expect(screen.getByRole("alert").textContent).toContain("位置检测未完成");
});
it("keeps the first step inside the selected R6 cold-start shell", () => {
  const view = mount(controller([source("workbuddy")]));
  const shell = view.container.querySelector<HTMLElement>(
    ".onboarding-r6-shell",
  );
  const discovery = view.container.querySelector<HTMLElement>(
    ".onboarding-r6-discovery",
  );
  expect(shell).toBeTruthy();
  expect(discovery).toBeTruthy();
  expect(
    discovery?.parentElement?.classList.contains("onboarding-r6-body"),
  ).toBe(true);
  expect(shell?.getAttribute("data-step")).toBe("1");
  expect(view.container.querySelector(".onboarding-r6-titlebar")).toBeTruthy();
});
it("shows the scroll hint only when the first-step list actually clips rows", async () => {
  const imports = controller([
    source("workbuddy"),
    source("cursor"),
    source("codex"),
    source("claude"),
  ]);
  const view = mount(imports);
  await waitFor(() =>
    expect(view.container.querySelectorAll(".onboarding-r6-row").length).toBe(
      4,
    ),
  );
  const list = view.container.querySelector<HTMLElement>(".onboarding-r6-list");
  if (!list) throw new Error("missing source list");
  vi.spyOn(list, "getBoundingClientRect").mockReturnValue({
    bottom: 100,
  } as DOMRect);
  for (const child of Array.from(list.children))
    vi.spyOn(child, "getBoundingClientRect").mockReturnValue({
      bottom: 80,
    } as DOMRect);
  fireEvent.scroll(list);
  expect(screen.queryByText(/向下查看更多/)).toBeNull();
  vi.mocked(list.children[3].getBoundingClientRect).mockReturnValue({
    bottom: 140,
  } as DOMRect);
  fireEvent.scroll(list);
  expect(await screen.findByText("向下查看更多 · 还有 1 个应用")).toBeTruthy();
});
it("holds the native discovery placeholder, then shows every detected app ready to continue", async () => {
  vi.useFakeTimers();
  window.oneMemory = {} as NonNullable<Window["oneMemory"]>;
  const sources = ["workbuddy", "cursor", "codebuddy", "codex"].map((id) =>
    source(id),
  );
  const imports = controller(sources);
  const view = mount(imports);

  expect(view.container.querySelectorAll(".onboarding-r6-row")).toHaveLength(1);
  expect(
    view.container.querySelectorAll(".onboarding-r6-skeleton"),
  ).toHaveLength(2);
  expect(screen.getByText("正在检测其他应用…")).toBeTruthy();
  expect(screen.getByText("已发现")).toBeTruthy();
  expect(screen.getByRole("button", { name: "开始导入" })).toHaveProperty(
    "disabled",
    true,
  );
  expect(
    within(screen.getByRole("complementary", { name: "本次导入" })).getByText(
      "1",
    ),
  ).toBeTruthy();

  await act(async () => vi.advanceTimersByTimeAsync(850));
  expect(view.container.querySelectorAll(".onboarding-r6-row")).toHaveLength(4);
  expect(
    view.container.querySelectorAll(".onboarding-r6-skeleton"),
  ).toHaveLength(0);
  expect(
    screen.getByLabelText<HTMLInputElement>("导入 cursor 历史").checked,
  ).toBe(true);
  expect(
    screen.getByLabelText<HTMLInputElement>("导入 codebuddy 历史").checked,
  ).toBe(true);
  expect(
    screen.getByLabelText<HTMLInputElement>("导入 codex 历史").checked,
  ).toBe(true);
  expect(screen.getByRole("button", { name: "开始导入" })).toHaveProperty(
    "disabled",
    false,
  );
});
it("custom source cancel does not grant or select and accepted import retains review branch", async () => {
  const imports = controller([
    source("workbuddy"),
    source("codex", { candidate_kind: "environment_override" }),
  ]);
  mount(imports);
  await waitFor(() =>
    expect(
      (screen.getByLabelText("导入 workbuddy 历史") as HTMLInputElement)
        .checked,
    ).toBe(true),
  );
  const codex = await screen.findByLabelText("导入 codex 历史");
  await waitFor(() => expect((codex as HTMLInputElement).disabled).toBe(false));
  fireEvent.click(codex);
  fireEvent.click(screen.getByRole("button", { name: "取消" }));
  expect(
    (screen.getByLabelText("导入 codex 历史") as HTMLInputElement).checked,
  ).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "开始导入" }));
  await screen.findByText("已受理 workbuddy");
  expect(vi.mocked(imports.connectForOnboarding).mock.calls[0]?.[0]).toEqual([
    "workbuddy",
  ]);
});

it("switches custom location confirmation and aria without changing unchecked consent", async () => {
  const name = "codex-custom-原样";
  const path = "/fixture/自定义/codex";
  const imports = controller([
    source("codex", {
      display_name: name,
      candidate_path: path,
      candidate_kind: "environment_override",
    }),
  ]);
  mount(imports);
  await waitFor(() =>
    expect(
      screen.getByRole("checkbox", { name: `导入 ${name} 历史` }),
    ).toHaveProperty("disabled", false),
  );
  fireEvent.click(screen.getByRole("checkbox", { name: `导入 ${name} 历史` }));
  const dialog = screen.getByRole("dialog", { name: "确认自定义位置" });
  const consent = within(dialog).getByRole("checkbox", {
    name: `确认这是 ${name} 的历史位置`,
  });
  const confirm = within(dialog).getByRole("button", { name: "确认此位置" });
  expect(consent).toHaveProperty("checked", false);
  expect(confirm).toHaveProperty("disabled", true);
  expect(within(dialog).getByText(path).textContent).toBe(path);
  expect(dialog.getAttribute("aria-describedby")).toBe(
    "cold-start-custom-description",
  );
  expect(imports.connectForOnboarding).not.toHaveBeenCalled();

  await act(() => setUiLanguage("en"));
  const englishDialog = screen.getByRole("dialog", {
    name: "Confirm custom location",
  });
  expect(
    within(englishDialog)
      .getByRole("button", { name: "Close location confirmation" })
      .getAttribute("title"),
  ).toBe("Close location confirmation");
  expect(
    within(englishDialog).getByText(
      /Confirming the location does not start reading history/,
    ),
  ).toBeTruthy();
  const englishConsent = within(englishDialog).getByRole("checkbox", {
    name: `Confirm this is ${name}'s history location`,
  });
  expect(englishConsent).toHaveProperty("checked", false);
  expect(
    within(englishDialog).getByRole("button", {
      name: "Confirm this location",
    }),
  ).toHaveProperty("disabled", true);
  expect(within(englishDialog).getByText(path).textContent).toBe(path);
  expect(imports.connectForOnboarding).not.toHaveBeenCalled();

  fireEvent.click(englishConsent);
  expect(
    within(englishDialog).getByRole("button", {
      name: "Confirm this location",
    }),
  ).toHaveProperty("disabled", false);
  await act(() => setUiLanguage("zh-CN"));
  expect(
    screen.getByRole("checkbox", { name: `确认这是 ${name} 的历史位置` }),
  ).toHaveProperty("checked", true);
  fireEvent.click(screen.getByRole("button", { name: "取消" }));
  expect(screen.queryByRole("dialog", { name: "确认自定义位置" })).toBeNull();
  expect(
    screen.getByRole("checkbox", { name: `导入 ${name} 历史` }),
  ).toHaveProperty("checked", false);
  expect(imports.connectForOnboarding).not.toHaveBeenCalled();
});

it("does not submit authorization when the first-step owner leaves during final metadata check", async () => {
  const imports = controller([source("workbuddy")]);
  const view = mount(imports);
  await waitFor(() =>
    expect(
      (screen.getByLabelText("导入 workbuddy 历史") as HTMLInputElement)
        .checked,
    ).toBe(true),
  );
  let done!: (
    value: Awaited<ReturnType<ImportSourcesController["refresh"]>>,
  ) => void;
  vi.mocked(imports.refresh).mockReturnValueOnce(
    new Promise((resolve) => {
      done = resolve;
    }),
  );
  fireEvent.click(screen.getByRole("button", { name: "开始导入" }));
  view.unmount();
  await act(async () =>
    done({
      sources: imports.sources,
      jobs: [],
      health: {} as Awaited<
        ReturnType<ImportSourcesController["refresh"]>
      >["health"],
    }),
  );
  expect(imports.connectForOnboarding).not.toHaveBeenCalled();
});
