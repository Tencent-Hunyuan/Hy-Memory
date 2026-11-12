import type { HomeProjectionV1 } from "@one-memory/contracts";
import { describe, expect, it } from "vitest";
import {
  buildHomeViewModel,
  type HomeProjectionReadState,
} from "./home-view-model";

function projection(
  mode: "lite" | "pro" | "ultra",
  overrides: Partial<HomeProjectionV1> = {},
): HomeProjectionV1 {
  return {
    projection_version: 1,
    user_id: "local",
    effective_mode: mode,
    capabilities: {
      content_plane: mode === "lite" ? "raw" : "memory",
      generation_required: mode !== "lite",
      graph_enabled: mode === "ultra",
      evolve_available: mode === "ultra",
    },
    visible_count: 0,
    display_state: "empty",
    latest: null,
    observed_at: "2026-11-04T00:00:00Z",
    ...overrides,
  };
}

const base = {
  processing: null,
  sedimentation: null,
  recentCall: null,
  importsRunning: false,
  hostState: "ready" as const,
  syncEnabledCount: 0,
};

describe("home view model", () => {
  it.each([
    ["lite", "raw", "apps.rawConversations", "apps.rawSavingCompact"],
    ["pro", "memory", "apps.memories", "apps.running"],
    ["ultra", "memory", "apps.memories", "apps.running"],
  ] as const)(
    "projects %s without component mode branches",
    (mode, plane, count, processing) => {
      const result = buildHomeViewModel({
        ...base,
        projection: projection(mode),
        projectionState: "ready",
        sedimentation: {
          enabled: true,
          pending_retained: true,
          imports_unaffected: true,
        },
      });
      expect(result.plane).toBe(plane);
      expect(result.countLabel).toBe(count);
      expect(result.processingLabel).toBe(processing);
      expect(result.currentModeLabel).toBe(mode);
    },
  );

  it.each(["loading", "stale", "unavailable"] as HomeProjectionReadState[])(
    "preserves explicit %s reads",
    (state) => {
      expect(
        buildHomeViewModel({
          ...base,
          projection: null,
          projectionState: state,
        }).state,
      ).toBe(state);
    },
  );

  it("keeps a raw row with no user excerpt truthful", () => {
    const result = buildHomeViewModel({
      ...base,
      projectionState: "ready",
      projection: projection("lite", {
        visible_count: 1,
        display_state: "no_user_excerpt",
        latest: {
          type: "raw",
          raw_id: "r1",
          session_id: "s1",
          source_id: "cursor",
          excerpt: "",
          truncated: false,
          display_state: "no_user_excerpt",
          time: null,
          time_basis: "import",
        },
      }),
    });
    expect(result.recent).toMatchObject({ kind: "raw", emptyExcerpt: true });
  });

  it("renders a checked lite empty state without generation copy", () => {
    const result = buildHomeViewModel({
      ...base,
      projection: projection("lite"),
      projectionState: "ready",
      processing: {
        running: 4,
        pending: 7,
        waiting_for_model: 2,
      } as never,
    });
    expect(result.recentMetric).toEqual({
      valueKey: "apps.recentEmptyCompact",
      detailKey: "apps.checkedRecent",
    });
    expect(result.activity).toMatchObject({
      kind: "empty",
      headingKey: "apps.recentUpdate",
      titleKey: "apps.rawCheckedEmpty",
      detailKey: "apps.rawWaiting",
    });
  });

  it("uses raw-saving activity during lite imports", () => {
    const result = buildHomeViewModel({
      ...base,
      projection: projection("lite"),
      projectionState: "ready",
      importsRunning: true,
    });
    expect(result.activity).toMatchObject({
      kind: "importing",
      headingKey: "apps.recentUpdate",
      titleKey: "apps.rawSaving",
      detailKey: "apps.rawSavingDetail",
    });
  });

  it.each([
    [{ running: 1 }, "first-running", "apps.firstRunning"],
    [{ pending: 1 }, "first-queued", "apps.firstQueued"],
  ] as const)(
    "keeps pro generation state in the view model",
    (processing, kind, titleKey) => {
      const result = buildHomeViewModel({
        ...base,
        projection: projection("pro"),
        projectionState: "ready",
        processing: processing as never,
      });
      expect(result.activity).toMatchObject({ kind, titleKey });
    },
  );

  it("renders a checked pro empty state", () => {
    const result = buildHomeViewModel({
      ...base,
      projection: projection("pro"),
      projectionState: "ready",
    });
    expect(result.recentMetric).toEqual({
      valueKey: "apps.recentEmptyCompact",
      detailKey: "apps.checkedRecent",
    });
    expect(result.activity.titleKey).toBe("apps.memoryCheckedEmpty");
    expect(result.activity.kind).toBe("empty");
  });

  it("never treats a legacy memory summary as lite raw data", () => {
    const result = buildHomeViewModel({
      ...base,
      confirmedMode: "lite",
      projection: null,
      projectionState: "unavailable",
      legacySummary: {
        memory_count: 99,
        latest: null,
        observed_at: "2026-11-04T00:00:00Z",
      },
    });
    expect(result.count).toBeNull();
    expect(result.plane).toBe("raw");
  });

  it("distinguishes unknown count from a confirmed empty projection", () => {
    expect(
      buildHomeViewModel({
        ...base,
        projection: null,
        projectionState: "loading",
      }).count,
    ).toBeNull();
    expect(
      buildHomeViewModel({
        ...base,
        projection: projection("pro", { visible_count: 0 }),
        projectionState: "ready",
      }).count,
    ).toBe(0);
  });

  it.each([
    ["loading", "apps.rawReadingCount"],
    ["unavailable", "apps.rawCountWillUpdate"],
  ] as const)("uses lite-specific count copy while %s", (state, key) => {
    expect(
      buildHomeViewModel({
        ...base,
        confirmedMode: "lite",
        projection: null,
        projectionState: state,
      }).countDetail,
    ).toBe(key);
  });
});
