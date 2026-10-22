// @vitest-environment jsdom

import type { ImportJob, SourceResponse } from "@one-memory/contracts";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { R6ImportProgress } from "./R6ImportProgress";

afterEach(cleanup);
const source = {
  source_id: "cursor",
  display_name: "Cursor",
  authorized: true,
  imported_messages: 9999,
} as SourceResponse;
const job = {
  job_id: "j1",
  source_id: "cursor",
  status: "running",
  stored: 17,
  errors: 0,
  created_at: "2026-09-19T00:00:00Z",
  updated_at: "2026-09-19T00:01:00Z",
} as ImportJob;
it("uses the selected job's message count, never the source total or fake percentage", () => {
  const { container } = render(
    <R6ImportProgress
      sourceIds={["cursor", "cursor"]}
      sources={[source]}
      jobs={[job]}
    />,
  );
  expect(screen.getByText("已保存 17 条消息")).toBeTruthy();
  expect(container.querySelectorAll("article")).toHaveLength(1);
  expect(container.querySelector("article")?.getAttribute("data-running")).toBe(
    "true",
  );
  expect(container.textContent).not.toContain("9999");
  expect(container.textContent).not.toContain("%");
  expect(container.textContent).not.toContain("持续导入中");
});

it("does not describe scanned-but-skipped history as saved", () => {
  const { container } = render(
    <R6ImportProgress
      sourceIds={["cursor"]}
      sources={[source]}
      jobs={[
        {
          ...job,
          status: "completed",
          discovered: 12_375,
          stored: 0,
          skipped: 12_375,
          history_after: "2026-10-15T00:00:00Z",
        },
      ]}
    />,
  );
  expect(
    screen.getByText("已扫描 12,375 条，本次没有符合时间范围的对话"),
  ).toBeTruthy();
  expect(screen.getByText("导入完成")).toBeTruthy();
  expect(container.textContent).not.toContain("正在保存");
  expect(container.textContent).not.toContain("已保存 12,375");
});

it("calls an active bounded scan filtering rather than saving", () => {
  render(
    <R6ImportProgress
      sourceIds={["cursor"]}
      sources={[source]}
      jobs={[
        {
          ...job,
          discovered: 3_522,
          stored: 0,
          history_after: "2026-10-15T00:00:00Z",
        },
      ]}
    />,
  );
  expect(screen.getByText("已扫描 3,522 条，正在筛选最近 3 天")).toBeTruthy();
});

it("reports only stored rows as saved after a completed partial import", () => {
  const { container } = render(
    <R6ImportProgress
      sourceIds={["cursor"]}
      sources={[source]}
      jobs={[
        {
          ...job,
          status: "completed",
          discovered: 25_177,
          stored: 20_826,
          skipped: 4_351,
        },
      ]}
    />,
  );
  expect(screen.getByText("已保存 20,826 条消息")).toBeTruthy();
  expect(screen.getByText("导入完成")).toBeTruthy();
  expect(container.textContent).not.toContain("25,177");
});

it.each(["paused", "interrupted", "failed", "completed", "cancelled"] as const)(
  "does not animate %s as active work",
  (status) => {
    const { container } = render(
      <R6ImportProgress
        sourceIds={["cursor"]}
        sources={[source]}
        jobs={[{ ...job, status }]}
      />,
    );
    expect(
      container.querySelector("article")?.getAttribute("data-running"),
    ).toBe("false");
  },
);
it("stops animation when source state is stale or revoked, without converting old counts to zero", () => {
  const view = render(
    <R6ImportProgress
      sourceIds={["cursor"]}
      sources={[source]}
      jobs={[job]}
      stale
    />,
  );
  expect(screen.getByText("状态待核对")).toBeTruthy();
  expect(
    view.container.querySelector("article")?.getAttribute("data-running"),
  ).toBe("false");
  view.rerender(
    <R6ImportProgress
      sourceIds={["cursor"]}
      sources={[{ ...source, authorized: false }]}
      jobs={[job]}
    />,
  );
  expect(screen.getByText("读取授权不可用")).toBeTruthy();
  expect(screen.getByText("已保存 17 条消息")).toBeTruthy();
});
it.each(["missing", "pending"] as const)(
  "shows %s work optimistically as a single importing status",
  (kind) => {
    const { container } = render(
      <R6ImportProgress
        sourceIds={["cursor"]}
        sources={[source]}
        jobs={kind === "pending" ? [{ ...job, status: "pending" }] : []}
      />,
    );
    expect(screen.getByText("正在导入")).toBeTruthy();
    expect(container.textContent).not.toContain("等待保存回执");
    expect(container.textContent).not.toContain("持续导入中");
    expect(
      container.querySelector("article")?.getAttribute("data-running"),
    ).toBe("true");
  },
);

it("centers one or two apps and keeps three or more in a scrolling row", () => {
  function shown(ids: string[]) {
    const view = render(
      <R6ImportProgress
        sourceIds={ids}
        sources={ids.map((id) => ({
          ...source,
          source_id: id,
          display_name: id,
        }))}
        jobs={[]}
      />,
    );
    const count = view.container
      .querySelector(".r6-import-progress")
      ?.getAttribute("data-count");
    view.unmount();
    return count;
  }
  expect(shown(["cursor"])).toBe("1");
  expect(shown(["cursor", "codex"])).toBe("2");
  expect(shown(["cursor", "codex", "claude-code"])).toBe("many");
  expect(shown(["cursor", "codex", "claude-code", "workbuddy"])).toBe("many");
  const four = render(
    <R6ImportProgress
      sourceIds={["cursor", "codex", "claude-code", "workbuddy"]}
      sources={["cursor", "codex", "claude-code", "workbuddy"].map((id) => ({
        ...source,
        source_id: id,
        display_name: id,
      }))}
      jobs={[]}
    />,
  );
  expect(four.container.querySelectorAll("article")).toHaveLength(4);
  expect(
    four.container
      .querySelector(".r6-import-progress")
      ?.getAttribute("data-count"),
  ).toBe("many");
});

it("keeps the first app anchored across receipts unless the user scrolls", () => {
  const sources = ["cursor", "codex", "claude-code", "workbuddy"].map((id) => ({
    ...source,
    source_id: id,
    display_name: id,
  }));
  const view = render(
    <R6ImportProgress
      sourceIds={sources.map((item) => item.source_id)}
      sources={sources}
      jobs={[]}
    />,
  );
  const progress = screen.getByRole("region", {
    name: "本次应用导入进度",
  });
  progress.scrollLeft = 420;
  fireEvent.scroll(progress);
  view.rerender(
    <R6ImportProgress
      sourceIds={sources.map((item) => item.source_id)}
      sources={sources}
      jobs={[job]}
    />,
  );
  expect(progress.scrollLeft).toBe(0);

  fireEvent.wheel(progress, { deltaX: 420 });
  progress.scrollLeft = 420;
  fireEvent.scroll(progress);
  view.rerender(
    <R6ImportProgress
      sourceIds={sources.map((item) => item.source_id)}
      sources={sources}
      jobs={[{ ...job, stored: 18 }]}
    />,
  );
  expect(progress.scrollLeft).toBe(420);
});

it("does not claim importing when the source is revoked", () => {
  const { container } = render(
    <R6ImportProgress
      sourceIds={["cursor"]}
      sources={[{ ...source, authorized: false }]}
      jobs={[]}
    />,
  );
  expect(screen.getByText("读取授权不可用")).toBeTruthy();
  expect(container.querySelector("article")?.getAttribute("data-running")).toBe(
    "false",
  );
});
