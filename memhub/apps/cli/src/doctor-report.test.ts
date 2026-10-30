import { describe, expect, it } from "vitest";

import { composeDoctorReport } from "./doctor-report.js";
import type { ProgressDocument } from "./source-progress-report.js";
import type { StatusDocument } from "./status-report.js";

describe("doctor report", () => {
  it("separates runtime, broker, permissions, model, and pending operations", () => {
    const report = composeDoctorReport(status(), progress());
    expect(report.state).toBe("attention");
    expect(report.checks).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: "core", state: "pass" }),
        expect.objectContaining({ id: "broker", state: "warn" }),
        expect.objectContaining({
          id: "source_permissions",
          state: "warn",
          code: "sources_need_authorization",
        }),
        expect.objectContaining({
          id: "model",
          state: "warn",
          code: "model_credential_unavailable",
        }),
        expect.objectContaining({
          id: "pending_operations",
          state: "warn",
          code: "operations_need_attention",
        }),
      ]),
    );
  });

  it("reports unread sections as not_checked rather than healthy", () => {
    const value = status();
    value.sections.sources = {
      state: "unavailable",
      code: "transport_failed",
    };
    const report = composeDoctorReport(value, null);
    expect(report.checks).toContainEqual(
      expect.objectContaining({
        id: "source_permissions",
        state: "not_checked",
        code: "transport_failed",
      }),
    );
    expect(report.checks).toContainEqual(
      expect.objectContaining({
        id: "pending_operations",
        state: "not_checked",
      }),
    );
  });
});

function status(): StatusDocument {
  return {
    schema_version: 1,
    product: "one-memory",
    data_dir: "/synthetic",
    runtime: {
      state: "running",
      code: "runtime_verified",
      instance_id: "instance-1",
      host: "127.0.0.1",
      port: 8765,
      version: "1.2.3",
    },
    bridge: {
      state: "degraded",
      code: "broker_stopped",
      core: "running",
      broker: "stopped",
    },
    sections: {
      sources: {
        state: "ok",
        total: 2,
        available: 2,
        authorized: 1,
        imported_messages: 1,
        imported_conversations: 1,
      },
      sync: {
        state: "ok",
        sources: 1,
        running: 0,
        idle: 0,
        degraded: 1,
        unavailable: 0,
        error_codes: ["source_unreadable"],
      },
      processing: {
        state: "ok",
        pending: 1,
        running: 0,
        waiting_for_model: 1,
        failed: 0,
        blocked: [],
      },
      model: {
        state: "ok",
        profiles: 1,
        active: {
          name: "Synthetic",
          provider_kind: "openai_compatible",
          model: "synthetic-model",
          has_api_key: false,
          credential_state: "unavailable",
        },
      },
      memories: { state: "ok", memory_count: 1, observed_at: "T" },
    },
    summary: { state: "partial", degraded: ["bridge"], not_checked: [] },
  };
}

function progress(): ProgressDocument {
  return {
    schema_version: 1,
    product: "one-memory",
    state: "partial",
    imports: {
      state: "ok",
      totals_state: { state: "ok" },
      imported_conversations: 1,
      imported_messages: 2,
      jobs: [{ job_id: "j1", status: "failed" }],
      returned: 1,
      validated: 1,
      output_truncated: false,
    },
    generation: {
      summary_state: { state: "ok" },
      summary: { waiting_for_model: 1, failed: 0 },
    },
  };
}
