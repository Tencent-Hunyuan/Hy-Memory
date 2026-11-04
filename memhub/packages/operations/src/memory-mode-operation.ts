import type {
  HomeProjectionCapabilities,
  MemoryModeChange,
  MemoryModePlan,
  MemoryPipelineMode,
} from "@one-memory/contracts";

const MODES = ["lite", "pro", "ultra"] as const;
const EFFECTS: MemoryModeChange["effects"] = [
  "earlier_raw_not_extracted",
  "higher_mode_memories_hidden",
  "existing_memories_added_to_graph",
  "evolve_stopped",
  "lite_search_stops",
  "graph_backfill_already_running",
];
const WARNINGS = ["forced_by_env", "effective_no_change"] as const;
const BLOCKERS = [
  "active_model_required",
  "embedding_maintenance_in_progress",
  "memory_lifecycle_busy",
  "graph_store_unavailable",
] as const;

/** FastAPI's route miss, excluding domain 404 bodies such as `{detail: code}`. */
export function isBareNotFound(value: unknown): boolean {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const body = value as Record<string, unknown>;
  return Object.keys(body).length === 1 && body.detail === "Not Found";
}

function isMode(value: unknown): value is MemoryPipelineMode {
  return MODES.includes(value as MemoryPipelineMode);
}

function listOf<T extends string>(
  value: unknown,
  allowed: readonly T[],
): value is T[] {
  return (
    Array.isArray(value) &&
    value.every((item) => allowed.includes(item as T)) &&
    new Set(value).size === value.length
  );
}

function capabilitiesFor(mode: MemoryPipelineMode): HomeProjectionCapabilities {
  return {
    content_plane: mode === "lite" ? "raw" : "memory",
    generation_required: mode !== "lite",
    graph_enabled: mode === "ultra",
    evolve_available: mode === "ultra",
  };
}

export function parseMemoryModePlan(value: unknown): MemoryModePlan | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const plan = value as Record<string, unknown>;
  const capabilities = plan.capabilities as Record<string, unknown> | null;
  const allowed = [
    "current",
    "target",
    "effective_target",
    "capabilities",
    "effects",
    "warnings",
    "blockers",
  ];
  if (
    Object.keys(plan).some((key) => !allowed.includes(key)) ||
    !isMode(plan.current) ||
    !isMode(plan.target) ||
    !isMode(plan.effective_target) ||
    !capabilities ||
    Object.keys(capabilities).some(
      (key) =>
        ![
          "content_plane",
          "generation_required",
          "graph_enabled",
          "evolve_available",
        ].includes(key),
    ) ||
    (capabilities.content_plane !== "raw" &&
      capabilities.content_plane !== "memory") ||
    typeof capabilities.generation_required !== "boolean" ||
    typeof capabilities.graph_enabled !== "boolean" ||
    typeof capabilities.evolve_available !== "boolean" ||
    capabilities.content_plane !==
      (plan.effective_target === "lite" ? "raw" : "memory") ||
    capabilities.generation_required !== (plan.effective_target !== "lite") ||
    capabilities.graph_enabled !== (plan.effective_target === "ultra") ||
    capabilities.evolve_available !== (plan.effective_target === "ultra") ||
    !listOf(plan.effects, EFFECTS) ||
    !listOf(plan.warnings, WARNINGS) ||
    !listOf(plan.blockers, BLOCKERS)
  )
    return null;
  return plan as unknown as MemoryModePlan;
}

/** Explicit compatibility matrix for Core builds predating the plan route. */
export function legacyMemoryModePlan(
  current: MemoryPipelineMode,
  target: MemoryPipelineMode,
  options: { hasActiveModel: boolean },
): MemoryModePlan {
  const effects: MemoryModeChange["effects"] = [];
  const rank: Record<MemoryPipelineMode, number> = {
    lite: 0,
    pro: 1,
    ultra: 2,
  };
  if (current !== target && current === "lite")
    effects.push("earlier_raw_not_extracted", "lite_search_stops");
  if (rank[target] < rank[current]) effects.push("higher_mode_memories_hidden");
  if (current !== target && current === "ultra") effects.push("evolve_stopped");
  if (target === "ultra" && current !== "ultra")
    effects.push("existing_memories_added_to_graph");
  return {
    current,
    target,
    effective_target: target,
    capabilities: capabilitiesFor(target),
    effects,
    warnings: [],
    blockers:
      target !== "lite" && !options.hasActiveModel
        ? ["active_model_required"]
        : [],
  };
}
