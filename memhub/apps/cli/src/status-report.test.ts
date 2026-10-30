import { describe, expect, it } from "vitest";

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
