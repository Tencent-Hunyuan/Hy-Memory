import { describe, expect, it } from "vitest";
import {
  isBareNotFound,
  legacyMemoryModePlan,
  parseMemoryModePlan,
} from "./memory-mode-operation";

const valid = {
  current: "lite",
  target: "pro",
  effective_target: "pro",
  capabilities: {
    content_plane: "memory",
    generation_required: true,
    graph_enabled: false,
    evolve_available: false,
  },
  effects: ["earlier_raw_not_extracted", "lite_search_stops"],
  warnings: [],
  blockers: ["active_model_required"],
};

describe("memory mode plan", () => {
  it("recognizes only FastAPI's bare route miss", () => {
    expect(isBareNotFound({ detail: "Not Found" })).toBe(true);
    expect(isBareNotFound({ detail: "memory_not_found" })).toBe(false);
    expect(isBareNotFound({ detail: "Not Found", extra: true })).toBe(false);
  });
  it("strictly parses the server plan", () => {
    expect(parseMemoryModePlan(valid)).toEqual(valid);
    expect(parseMemoryModePlan({ ...valid, target: "future" })).toBeNull();
    expect(parseMemoryModePlan({ ...valid, blockers: ["unknown"] })).toBeNull();
  });

  it("isolates the old-Core matrix in one helper", () => {
    expect(
      legacyMemoryModePlan("lite", "ultra", { hasActiveModel: false }),
    ).toMatchObject({
      capabilities: {
        content_plane: "memory",
        generation_required: true,
        graph_enabled: true,
      },
      blockers: ["active_model_required"],
      effects: expect.arrayContaining([
        "earlier_raw_not_extracted",
        "existing_memories_added_to_graph",
      ]),
    });
  });
});
