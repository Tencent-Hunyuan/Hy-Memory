// @vitest-environment jsdom
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { App } from "./App";

const memoryProps = vi.hoisted(() => vi.fn());

vi.mock("./api", () => ({
  getOnboarding: vi.fn().mockResolvedValue({ status: "completed" }),
}));
vi.mock("./use-import-sources", () => ({
  useImportSources: () => ({
    sources: [],
    jobs: [],
    loading: false,
    health: null,
    healthStale: false,
  }),
}));
vi.mock("./SourcesView", () => ({ SourcesView: () => <p>应用视图内容</p> }));
vi.mock("./ApplicationsHome", () => ({
  ApplicationsHome: () => <p>应用视图内容</p>,
}));
vi.mock("./MemorySpace", () => ({
  MemorySpace: (props: { active?: boolean; preload?: boolean }) => {
    memoryProps(props);
    return <p data-testid="memory-mounted">记忆视图内容</p>;
  },
}));
vi.mock("./ModelSettings", () => ({
  ModelSettings: () => <p>模型设置内容</p>,
}));
vi.mock("./ImportProgressStrip", () => ({ ImportProgressStrip: () => null }));
vi.mock("./MatterWorkspace", () => ({ MatterWorkspace: () => null }));
vi.mock("./HealthPanel", () => ({ HealthPanel: () => null }));
vi.mock("./FirstUseGuide", () => ({ FirstUseGuide: () => null }));
vi.mock("./OnboardingFlow", () => ({ OnboardingFlow: () => null }));

beforeEach(() => {
  window.history.replaceState({}, "", "/");
  localStorage.clear();
});
afterEach(cleanup);

it("starts in applications without mounting the hidden memory workspace", async () => {
  render(<App />);
  await screen.findByText("应用视图内容");
  const nav = screen.getByRole("navigation", { name: "主要导航" });
  expect(
    within(nav)
      .getAllByRole("button")
      .map((n) => n.textContent),
  ).toEqual(["应用", "记忆", "设置"]);
  expect(
    within(nav)
      .getByRole("button", { name: "应用" })
      .getAttribute("aria-current"),
  ).toBe("page");
  expect(screen.queryByTestId("memory-mounted")).toBeNull();
  await waitFor(() => expect(memoryProps).not.toHaveBeenCalled());
});

it("uses explicit memory deep links and restores the applications default URL", async () => {
  render(<App />);
  const nav = await screen.findByRole("navigation", { name: "主要导航" });
  await userEvent.click(within(nav).getByRole("button", { name: "记忆" }));
  expect(window.location.search).toBe("?view=memory");
  expect(screen.getByTestId("memory-mounted")).toBeTruthy();
  await userEvent.click(within(nav).getByRole("button", { name: "应用" }));
  expect(window.location.search).toBe("");
  expect(
    within(nav)
      .getByRole("button", { name: "应用" })
      .getAttribute("aria-current"),
  ).toBe("page");
});

it("keeps an independent scroll position for each primary page", async () => {
  render(<App />);
  const nav = await screen.findByRole("navigation", { name: "主要导航" });
  const content = document.querySelector<HTMLElement>(".app-content");
  expect(content).toBeTruthy();
  if (!content) return;
  content.scrollTop = 240;
  await userEvent.click(within(nav).getByRole("button", { name: "记忆" }));
  expect(content.scrollTop).toBe(0);
  content.scrollTop = 80;
  await userEvent.click(within(nav).getByRole("button", { name: "应用" }));
  expect(content.scrollTop).toBe(240);
});
