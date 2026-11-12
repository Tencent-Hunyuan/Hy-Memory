import { describe, expect, it } from "vitest";

import type { CoreReader, ReadOutcome } from "./core-client.js";
import { composeDoctorReport, renderDoctorText } from "./doctor-report.js";
import {
  composeStatusReport,
  renderStatusText,
  type StatusDocument,
} from "./status-report.js";

describe("status default output", () => {
  it("formats blocked task objects recursively instead of printing object coercions", () => {
    const report: StatusDocument = {
      schema_version: 1,
      product: "memhub",
      data_dir: "/tmp/isolated",
      runtime: { state: "running", code: "ok", host: "127.0.0.1", port: 30001 },
      bridge: { state: "healthy", code: "ok" },
      sections: {
        sources: { state: "ok", authorized: 1 },
        sync: { state: "ok", running: 0 },
        processing: {
          state: "ok",
          blocked: [
            { kind: "extract", reason: "waiting_for_model", count: 2 },
            { kind: "extract", reason: "input_too_long", count: 1 },
          ],
        },
        model: { state: "ok", profiles: 1 },
        memories: { state: "ok", memory_count: 8 },
      },
      summary: { state: "ok", degraded: [], not_checked: [] },
    };

    const text = renderStatusText(report, "MemHub");
    expect(text).not.toContain("[object Object]");
    expect(text).toContain("卡住 类型 整理对话/原因 等待模型/数量 2");
    expect(text).not.toMatch(/background_verified|additions_only|error_codes=/);
    expect(text).not.toMatch(
      /provider_kind|has_api_key|credential_state|observed_at/,
    );
  });
});

describe("status on a paused model", () => {
  const processing = (backoff: {
    llm_backoff_until: string | null;
    llm_backoff_error: string | null;
  }) => ({
    pending: 4,
    fresh_pending: 4,
    backlog_pending: 0,
    outside_window_pending: 0,
    running: 0,
    waiting_for_model: 0,
    completed: 1,
    failed: 0,
    blocked_stages: [],
    suspended: 2,
    ...backoff,
  });
  const reader =
    (summary: unknown): CoreReader =>
    async <T>(path: string) =>
      (path === "/api/v1/memory-processing"
        ? { state: "ok", data: summary }
        : { state: "unavailable", code: "x" }) as ReadOutcome<T>;
  const compose = (summary: unknown) =>
    composeStatusReport({
      dataDir: "/tmp/isolated/.one-memory",
      runtime: {
        state: "running",
        code: "runtime_verified",
        host: "127.0.0.1",
        port: 30001,
      },
      background: null,
      reader: reader(summary),
    });

  it("names the code and the retry time while the breaker pauses", async () => {
    const report = await compose(
      processing({
        llm_backoff_until: "2026-11-02T10:00:00+00:00",
        llm_backoff_error: "model_auth_failed",
      }),
    );
    expect(report.sections.processing).toMatchObject({
      state: "ok",
      pending: 4,
      suspended: 2,
      llm_backoff_until: "2026-11-02T10:00:00+00:00",
      llm_backoff_error: "model_auth_failed",
    });
    const text = renderStatusText(report, "MemHub");
    expect(text).toContain(
      "模型暂停：model_auth_failed，将于 2026-11-02T10:00:00+00:00 自动重试",
    );
    expect(text).not.toContain("llm_backoff");
  });

  it("prints no pause line and null fields when nothing is paused", async () => {
    const report = await compose(
      processing({ llm_backoff_until: null, llm_backoff_error: null }),
    );
    expect(report.sections.processing).toMatchObject({
      llm_backoff_until: null,
      llm_backoff_error: null,
    });
    expect(renderStatusText(report, "MemHub")).not.toContain("模型暂停");
  });

  it("reports a restarting write drain and its exit code", async () => {
    const report = await compose({
      ...processing({ llm_backoff_until: null, llm_backoff_error: null }),
      write_drain: "restarting",
      write_drain_error: "operational_error",
    });
    expect(report.sections.processing).toMatchObject({
      write_drain: "restarting",
      write_drain_error: "operational_error",
    });
    const text = renderStatusText(report, "MemHub");
    expect(text).toContain(
      "写入队列：异常退出（operational_error），正在自动重启",
    );
    expect(text).not.toContain("write_drain");
  });

  it("says nothing about a running drain and nulls what an older Core omits", async () => {
    const running = await compose({
      ...processing({ llm_backoff_until: null, llm_backoff_error: null }),
      write_drain: "running",
      write_drain_error: null,
    });
    expect(renderStatusText(running, "MemHub")).not.toContain("写入队列");
    const older = await compose(
      processing({ llm_backoff_until: null, llm_backoff_error: null }),
    );
    expect(older.sections.processing).toMatchObject({
      write_drain: null,
      write_drain_error: null,
    });
  });
});

describe("status and doctor on an unfinished upgrade migration", () => {
  it("names the migration log and the issues page", async () => {
    const report = await composeStatusReport({
      dataDir: "/tmp/isolated/.one-memory",
      runtime: { state: "stopped", code: "sdk_migration_required" },
      background: null,
      reader: null,
    });

    expect(report.runtime.log_file).toBe(
      "/tmp/isolated/.one-memory/logs/sdk-migration.log",
    );
    expect(report.runtime.issues_url).toBe(
      "https://github.com/Tencent-Hunyuan/Hy-Memory/issues/new",
    );
    const text = renderStatusText(report, "MemHub");
    expect(text).toContain("sdk_migration_required");
    expect(text).toContain("/tmp/isolated/.one-memory/logs/sdk-migration.log");

    const doctor = renderDoctorText(composeDoctorReport(report, null));
    expect(doctor).toContain(
      "/tmp/isolated/.one-memory/logs/sdk-migration.log",
    );
    expect(doctor).not.toContain("帮助与诊断");
  });

  it("adds no log path for an ordinary stopped runtime", async () => {
    const report = await composeStatusReport({
      dataDir: "/tmp/isolated/.one-memory",
      runtime: { state: "stopped", code: "runtime_not_running" },
      background: null,
      reader: null,
    });
    expect(report.runtime.log_file).toBeUndefined();
  });
});

describe("mode-aware status content projection", () => {
  const runtime = {
    state: "running" as const,
    code: "runtime_verified",
    host: "127.0.0.1",
    port: 30001,
  };
  const processing = (mode: "lite" | "pro" | "ultra") => ({
    pending: 0,
    fresh_pending: 0,
    backlog_pending: 0,
    outside_window_pending: 0,
    running: 0,
    waiting_for_model: 0,
    completed: 0,
    failed: 0,
    pipeline: { mode, configured_mode: mode },
  });
  const projection = {
    projection_version: 1,
    user_id: "local",
    effective_mode: "lite",
    capabilities: {
      content_plane: "raw",
      generation_required: false,
      graph_enabled: false,
      evolve_available: false,
    },
    visible_count: 7,
    display_state: "excerpt",
    latest: {
      type: "raw",
      raw_id: "raw-1",
      session_id: "session-1",
      display_state: "excerpt",
      time: null,
      time_basis: null,
      excerpt: "hello",
      truncated: false,
      source_id: "cursor",
    },
    observed_at: "2026-11-04T00:00:00Z",
  };

  it("uses the projection without reading either legacy count endpoint", async () => {
    const calls: string[] = [];
    const reader: CoreReader = async <T>(path: string) => {
      calls.push(path);
      const data =
        path === "/api/v2/home-projection"
          ? projection
          : path === "/api/v1/memory-processing"
            ? processing("lite")
            : null;
      return { state: "ok", data } as ReadOutcome<T>;
    };
    const report = await composeStatusReport({
      dataDir: "/tmp/isolated",
      runtime,
      background: null,
      reader,
    });
    expect(report.sections.memories).toEqual({
      state: "ok",
      content_plane: "raw",
      visible_count: 7,
      effective_mode: "lite",
      raw_count: 7,
    });
    expect(calls).not.toContain("/api/v2/raw");
    expect(calls).not.toContain("/api/v2/home-summary");
    expect(renderStatusText(report, "MemHub")).toContain(
      "原始对话：原始对话 7",
    );
  });

  it("falls back to the real raw total only for an old Lite Core", async () => {
    const reader: CoreReader = async <T>(path: string) => {
      if (path === "/api/v2/home-projection")
        return { state: "not_found", code: "http_404" } as ReadOutcome<T>;
      if (path === "/api/v1/memory-processing")
        return {
          state: "ok",
          data: processing("lite"),
        } as ReadOutcome<T>;
      if (path === "/api/v2/raw")
        return {
          state: "ok",
          data: {
            user_id: "local",
            items: [],
            total: 9,
            offset: 0,
            limit: 1,
            time_filtered: false,
          },
        } as ReadOutcome<T>;
      return { state: "unavailable", code: "x" } as ReadOutcome<T>;
    };
    const report = await composeStatusReport({
      dataDir: "/tmp/isolated",
      runtime,
      background: null,
      reader,
    });
    expect(report.sections.memories).toMatchObject({
      state: "ok",
      content_plane: "raw",
      visible_count: 9,
      raw_count: 9,
    });
    expect(report.sections.memories).not.toHaveProperty("memory_count");
  });

  it("rejects extra projection fields instead of falling back or leaking them", async () => {
    const reader: CoreReader = async <T>(path: string) =>
      ({
        state: "ok",
        data:
          path === "/api/v2/home-projection"
            ? { ...projection, secret: "must-not-leak" }
            : processing("lite"),
      }) as ReadOutcome<T>;
    const report = await composeStatusReport({
      dataDir: "/tmp/isolated",
      runtime,
      background: null,
      reader,
    });
    expect(report.sections.memories).toEqual({
      state: "invalid",
      code: "response_invalid",
    });
    expect(JSON.stringify(report)).not.toContain("must-not-leak");
  });
});
