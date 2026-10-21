// @vitest-environment jsdom

import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { SourceSnapshotStatus } from "./SourceSnapshotStatus";
import { setUiLanguage } from "./ui-language";

afterEach(async () => {
  cleanup();
  await setUiLanguage("zh-CN");
});
const confirmedAt = "2026-09-14T04:00:00.000Z";

it("updates visible dates and labels with language without changing the receipt timestamp", async () => {
  render(
    <SourceSnapshotStatus
      snapshot={{ confirmedAt, stale: false }}
      onRefresh={vi.fn()}
    />,
  );
  await act(async () => {
    await setUiLanguage("en");
  });
  const stamp = screen.getByText(/Status confirmed at/).querySelector("time");
  expect(stamp?.dateTime).toBe(confirmedAt);
  expect(stamp?.textContent).toBe(new Date(confirmedAt).toLocaleString("en"));
  expect(
    screen.getByRole("button", { name: "Refresh source status" }),
  ).toBeTruthy();
});

it("labels the last full-source observation without inventing a source event time", () => {
  render(
    <SourceSnapshotStatus
      snapshot={{ confirmedAt, stale: false }}
      onRefresh={vi.fn()}
    />,
  );
  const stamp = screen.getByText(/状态确认于/).querySelector("time");
  expect(stamp?.dateTime).toBe(confirmedAt);
  expect(screen.queryByText("状态待核对")).toBeNull();
});

it("retains the exact prior time in stale state and offers an explicit read-only refresh", async () => {
  const onRefresh = vi.fn().mockResolvedValue(undefined);
  render(
    <SourceSnapshotStatus
      snapshot={{ confirmedAt, stale: true }}
      onRefresh={onRefresh}
    />,
  );
  expect(screen.getByText("状态待核对")).toBeTruthy();
  expect(screen.getByText(/上次确认/).querySelector("time")?.dateTime).toBe(
    confirmedAt,
  );
  await userEvent.click(screen.getByRole("button", { name: "刷新来源状态" }));
  expect(onRefresh).toHaveBeenCalledOnce();
  expect(screen.getByText("状态待核对")).toBeTruthy();
});

it.each([null, "not-a-time"])(
  "does not fabricate an observation for %s",
  (confirmedAt) => {
    render(
      <SourceSnapshotStatus
        snapshot={{ confirmedAt, stale: true }}
        onRefresh={vi.fn()}
      />,
    );
    expect(screen.getByText("尚无已确认状态")).toBeTruthy();
    expect(document.querySelector("time")).toBeNull();
  },
);

it("coalesces repeated refresh clicks and retains the error until a real new snapshot", async () => {
  let fail!: (reason: Error) => void;
  const onRefresh = vi.fn(
    () =>
      new Promise((_, reject) => {
        fail = reject;
      }),
  );
  const view = render(
    <SourceSnapshotStatus
      snapshot={{ confirmedAt, stale: true }}
      onRefresh={onRefresh}
    />,
  );
  await userEvent.dblClick(
    screen.getByRole("button", { name: "刷新来源状态" }),
  );
  expect(onRefresh).toHaveBeenCalledOnce();
  expect(
    screen.getByRole<HTMLButtonElement>("button", { name: "刷新来源状态" })
      .disabled,
  ).toBe(true);
  await act(async () => fail(new Error("offline")));
  expect(screen.getByRole("alert").textContent).toContain("尚未取得新状态");
  expect(screen.getByText(/上次确认/).querySelector("time")?.dateTime).toBe(
    confirmedAt,
  );
  view.rerender(
    <SourceSnapshotStatus
      snapshot={{ confirmedAt: "2026-09-14T04:01:00.000Z", stale: false }}
      onRefresh={onRefresh}
    />,
  );
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.queryByText("状态待核对")).toBeNull();
});

it("reports a failed refresh even when the last snapshot was not stale", async () => {
  const onRefresh = vi.fn().mockRejectedValue(new Error("offline"));
  render(
    <SourceSnapshotStatus
      snapshot={{ confirmedAt, stale: false }}
      onRefresh={onRefresh}
    />,
  );
  await userEvent.click(screen.getByRole("button", { name: "刷新来源状态" }));
  expect((await screen.findByRole("alert")).textContent).toContain(
    "刷新未成功，仍显示上次确认的状态。",
  );
  expect(screen.getByText(/状态确认于/).querySelector("time")?.dateTime).toBe(
    confirmedAt,
  );
  expect(screen.queryByText("尚无已确认状态")).toBeNull();
});

it("clears a failed refresh only after a newer authoritative snapshot arrives", async () => {
  const onRefresh = vi.fn().mockRejectedValue(new Error("offline"));
  const view = render(
    <SourceSnapshotStatus
      snapshot={{ confirmedAt, stale: false }}
      onRefresh={onRefresh}
    />,
  );
  await userEvent.click(screen.getByRole("button", { name: "刷新来源状态" }));
  await screen.findByRole("alert");
  view.rerender(
    <SourceSnapshotStatus
      snapshot={{ confirmedAt: "2026-09-14T04:01:00.000Z", stale: false }}
      onRefresh={onRefresh}
    />,
  );
  expect(screen.queryByRole("alert")).toBeNull();
});

it("has no UI for legacy callers with no snapshot", () => {
  render(<SourceSnapshotStatus onRefresh={vi.fn()} />);
  expect(screen.queryByRole("button")).toBeNull();
});
