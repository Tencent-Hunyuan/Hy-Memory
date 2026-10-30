import { describe, expect, it } from "vitest";

import { coreStatus, PRIMARY_VIEWS, viewFromUrl } from "./App";
import { CALL_HISTORY_ENTRY_VISIBLE } from "./ApplicationsHome";

const core = {
  status: "ok",
  service: "one-memory-core",
  version: "test",
  instance_id: "instance",
} as const;

it("lands on applications and preserves explicit memory deep links", () => {
  expect(viewFromUrl("")).toBe("sources");
  expect(viewFromUrl("?view=matters")).toBe("sources");
  expect(viewFromUrl("?view=sources")).toBe("sources");
  expect(viewFromUrl("?view=memory")).toBe("memory");
  expect(viewFromUrl("?view=unknown")).toBe("sources");
  expect(viewFromUrl("?view=guide")).toBe("sources");
});

it("D96: an old call-history link lands on the home page while that page has no entry", () => {
  expect(viewFromUrl("?view=calls")).toBe(
    CALL_HISTORY_ENTRY_VISIBLE ? "calls" : "sources",
  );
});

it("keeps diagnostics out of primary destinations without breaking old URLs", () => {
  expect(PRIMARY_VIEWS).toEqual(["sources", "memory"]);
  expect(viewFromUrl("?view=health")).toBe("health");
});

describe("coreStatus", () => {
  it("distinguishes connected from still reading old conversations", () => {
    // Connected and busy looks identical to connected and idle otherwise, and
    // in that stretch the memories genuinely are not in yet.
    expect(coreStatus({ ...core, memory_backfill: "running" })).toBe(
      "本地 Core 正在整理历史对话",
    );
    expect(coreStatus({ ...core, memory_backfill: "done" })).toBe(
      "本地 Core 已连接",
    );
  });

  it("does not present a failed catch-up as a healthy connection", () => {
    expect(coreStatus({ ...core, memory_backfill: "failed" })).toBe(
      "历史对话整理未完成",
    );
  });

  it("reads an older Core, which has no such field, as simply connected", () => {
    expect(coreStatus(core)).toBe("本地 Core 已连接");
    expect(coreStatus(null)).toBe("连接 Core…");
  });
});
