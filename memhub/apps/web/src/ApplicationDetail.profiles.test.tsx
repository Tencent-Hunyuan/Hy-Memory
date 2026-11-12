// @vitest-environment jsdom

/**
 * #230: DeepSeek Harness has no single host, so the app page used to grey the
 * switch out with `unsupported_host` and offered no other way to grant recall.
 * The one switch now covers every detected profile.
 */

import type { SourceResponse } from "@one-memory/contracts";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ApplicationDetail } from "./ApplicationDetail";
import { readConnectionPreferences } from "./connection-preferences";
import { resetConnectionReconcile } from "./connection-reconcile";
import { harnessBridge } from "./harness-profiles.test-fixture";

const source = {
  source_id: "deepseek-harness",
  display_name: "DeepSeek Harness",
  authorized: true,
  sync_enabled: true,
  capabilities: {
    historical_import: "implemented",
    incremental_import: "implemented",
    live_sync: "implemented",
    recall: "implemented",
    installer: "implemented",
    verification: "real_replayed",
  },
} as SourceResponse;

function renderDetail() {
  return render(
    <ApplicationDetail
      source={source}
      sources={[source]}
      stale={false}
      setSync={vi.fn()}
      refresh={vi.fn().mockResolvedValue({ sources: [source] })}
      onBack={vi.fn()}
    />,
  );
}

function profileItems() {
  return within(
    screen.getByRole("list", { name: "DeepSeek Harness 的 Profile" }),
  )
    .getAllByRole("listitem")
    .map((item) => item.textContent);
}

beforeEach(() => {
  window.localStorage.clear();
  resetConnectionReconcile();
});
afterEach(() => {
  cleanup();
  window.oneMemory = undefined;
  window.localStorage.clear();
  resetConnectionReconcile();
});

it("lists every detected profile with its own state", async () => {
  harnessBridge({ desktop: "approved", web: "not-installed" });
  renderDetail();
  await waitFor(() =>
    expect(profileItems()).toEqual(["desktop：已接入", "web：未接入"]),
  );
  const insertion = screen.getByRole("switch", { name: "一键插入应用" });
  expect(insertion.hasAttribute("disabled")).toBe(false);
  expect(screen.queryByText(/当前安装不支持一键插入/)).toBeNull();
});

it("turns every profile on with the one switch and remembers the choice", async () => {
  const { api, state } = harnessBridge({
    desktop: "not-installed",
    web: "not-installed",
  });
  renderDetail();
  const insertion = await screen.findByRole("switch", { name: "一键插入应用" });
  await waitFor(() => expect(profileItems()).toHaveLength(2));
  expect(insertion.getAttribute("aria-checked")).toBe("false");
  fireEvent.click(insertion);
  await waitFor(() =>
    expect(profileItems()).toEqual(["desktop：已接入", "web：已接入"]),
  );
  expect(api.installRecall.mock.calls.map(([hostId]) => hostId)).toEqual([
    "deepseek-harness:desktop",
    "deepseek-harness:web",
  ]);
  expect([...state.values()]).toEqual(["approved", "approved"]);
  expect(insertion.getAttribute("aria-checked")).toBe("true");
  // Not "未接通": the self-checks the grants recorded were read back.
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: /已接通|未接通|检测中/ }).textContent,
    ).toBe("已接通"),
  );
  // Remembered, so a profile found on a later launch is connected too.
  expect(readConnectionPreferences().hostSourceIds).toEqual([
    "deepseek-harness",
  ]);
});

it("names the profile that failed and keeps the others on", async () => {
  const { state } = harnessBridge(
    { desktop: "not-installed", web: "not-installed" },
    { failInstall: ["web"] },
  );
  renderDetail();
  const insertion = await screen.findByRole("switch", { name: "一键插入应用" });
  await waitFor(() => expect(profileItems()).toHaveLength(2));
  fireEvent.click(insertion);
  const alert = await screen.findByRole("alert");
  expect(alert.textContent).toContain("以下 Profile 未能接入：web");
  expect(alert.textContent).toContain("应用控制通道暂不可用");
  await waitFor(() =>
    expect(profileItems()).toEqual(["desktop：已接入", "web：未接入"]),
  );
  expect(state.get("deepseek-harness:desktop")).toBe("approved");
  expect(insertion.getAttribute("aria-checked")).toBe("true");
});

it("turns every linked profile off with the one switch", async () => {
  const { api, state } = harnessBridge({
    desktop: "approved",
    web: "approved",
  });
  renderDetail();
  const insertion = await screen.findByRole("switch", { name: "一键插入应用" });
  await waitFor(() =>
    expect(insertion.getAttribute("aria-checked")).toBe("true"),
  );
  fireEvent.click(insertion);
  await waitFor(() =>
    expect(profileItems()).toEqual(["desktop：未接入", "web：未接入"]),
  );
  expect(api.uninstallRecall).toHaveBeenCalledTimes(2);
  expect([...state.values()]).toEqual(["not-installed", "not-installed"]);
  expect(insertion.getAttribute("aria-checked")).toBe("false");
  expect(readConnectionPreferences().hostSourceIds).toEqual([]);
});

it("names the profile whose connection could not be removed", async () => {
  harnessBridge(
    { desktop: "approved", web: "approved" },
    {
      failUninstall: ["desktop"],
    },
  );
  renderDetail();
  const insertion = await screen.findByRole("switch", { name: "一键插入应用" });
  await waitFor(() =>
    expect(insertion.getAttribute("aria-checked")).toBe("true"),
  );
  fireEvent.click(insertion);
  // Removal failed but revocation went through: the grant is gone, the file
  // is left for the person to check.
  expect((await screen.findByRole("alert")).textContent).toContain(
    "已撤销以下 Profile 的记忆权限，但连接文件没有全部移除，请核对：desktop",
  );
});

it("says how to get a profile when none is detected", async () => {
  harnessBridge({});
  renderDetail();
  expect(
    await screen.findByText(
      "还没有检测到 DeepSeek Harness 的 Profile。创建 Profile 后再回到此页。",
    ),
  ).toBeTruthy();
  expect(
    screen
      .getByRole("switch", { name: "一键插入应用" })
      .hasAttribute("disabled"),
  ).toBe(true);
});
