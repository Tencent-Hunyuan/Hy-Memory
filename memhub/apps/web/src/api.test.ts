import { describe, expect, it } from "vitest";

import {
  DEFAULT_CORE_URL,
  homeProjectionUnsupported,
  isHealthResponse,
  memoryModePlanUnsupported,
  parseHomeProjection,
} from "./api";

describe("Core API contract", () => {
  it("defaults to the local-only Core address", () => {
    expect(DEFAULT_CORE_URL).toBe("http://127.0.0.1:8765");
  });

  it("rejects an old or unrelated health payload", () => {
    expect(
      isHealthResponse({
        status: "ok",
        service: "one-memory-core",
        version: "0.0.0",
      }),
    ).toBe(false);
  });
});

describe("home projection parser", () => {
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
    visible_count: 1,
    display_state: "no_user_excerpt",
    latest: {
      type: "raw",
      raw_id: "raw-1",
      session_id: "session-1",
      source_id: "cursor",
      excerpt: "",
      truncated: false,
      display_state: "no_user_excerpt",
      time: null,
      time_basis: "import",
    },
    observed_at: "2026-11-04T00:00:00Z",
  };

  it("accepts a self-consistent lite projection", () => {
    expect(parseHomeProjection(projection)).toEqual(projection);
  });

  it("rejects cross-mode and extra fields", () => {
    expect(
      parseHomeProjection({ ...projection, effective_mode: "pro" }),
    ).toBeNull();
    expect(parseHomeProjection({ ...projection, surprise: true })).toBeNull();
  });
});

it("accepts only a bare route-level Not Found as unsupported", () => {
  const error = (data: unknown) => ({
    isAxiosError: true,
    response: { status: 404, data },
  });
  expect(homeProjectionUnsupported(error({ detail: "Not Found" }))).toBe(true);
  expect(memoryModePlanUnsupported(error({ detail: "Not Found" }))).toBe(true);
  expect(homeProjectionUnsupported(error({ detail: "memory_not_found" }))).toBe(
    false,
  );
  expect(
    memoryModePlanUnsupported(
      error({ detail: "Not Found", context: "domain" }),
    ),
  ).toBe(false);
});
