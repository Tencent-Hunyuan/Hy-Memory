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

  it.each([
    ["lite", "pro", ["earlier_raw_not_extracted", "lite_search_stops"]],
    [
      "lite",
      "ultra",
      [
        "earlier_raw_not_extracted",
        "lite_search_stops",
        "existing_memories_added_to_graph",
      ],
    ],
    ["pro", "lite", ["higher_mode_memories_hidden"]],
    ["pro", "ultra", ["existing_memories_added_to_graph"]],
    ["ultra", "lite", ["higher_mode_memories_hidden", "evolve_stopped"]],
    ["ultra", "pro", ["higher_mode_memories_hidden", "evolve_stopped"]],
  ] as const)(
    "keeps the old-Core %s to %s effects aligned with the plan route",
    (current, target, effects) => {
      expect(
        legacyMemoryModePlan(current, target, { hasActiveModel: true }).effects,
      ).toEqual(effects);
    },
  );

  it("keeps the old-Core model blocker and target capabilities", () => {
    expect(
      legacyMemoryModePlan("lite", "ultra", { hasActiveModel: false }),
    ).toMatchObject({
      capabilities: {
        content_plane: "memory",
        generation_required: true,
        graph_enabled: true,
      },
      blockers: ["active_model_required"],
    });
  });
});
