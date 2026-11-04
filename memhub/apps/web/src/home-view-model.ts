import type {
  HomeProjectionV1,
  HostHomeSummary,
  MemoryProcessingSummary,
} from "@one-memory/contracts";
import type { SedimentationState } from "./api";
import type { CallEvent } from "./call-history-client";

export type HomeProjectionReadState =
  | "loading"
  | "ready"
  | "stale"
  | "unavailable";

export interface HomeViewModel {
  mode: "lite" | "pro" | "ultra" | null;
  plane: "raw" | "memory" | null;
  count: number | null;
  countLabel: "apps.rawConversations" | "apps.memories";
  countDetail:
    | "apps.readingCount"
    | "apps.rawReadingCount"
    | "apps.scope"
    | "apps.showingPrevious"
    | "apps.countWillUpdate"
    | "apps.rawCountWillUpdate";
  processingTitle: "apps.rawSavingStatus" | "apps.processing";
  processingLabel: string;
  processingDetail: string;
  organizingTitle: "apps.rawSavingStatus" | "apps.organizing";
  organizingLabel: string;
  currentModeLabel: "lite" | "pro" | "ultra" | "—";
  state: HomeProjectionReadState;
  recent: {
    kind: "raw" | "memory";
    excerpt: string;
    sourceId: string;
    time: string | null;
    timeBasis: string | null;
    emptyExcerpt: boolean;
  } | null;
  recentMetric: {
    valueKey: "apps.checkingRecent" | "apps.recentEmptyCompact" | null;
    detailKey:
      | "apps.rawSaved"
      | "apps.saved"
      | "apps.recentWillUpdate"
      | "apps.checkedRecent";
  };
  activity: {
    kind:
      | "update"
      | "importing"
      | "first-running"
      | "first-queued"
      | "empty"
      | "loading"
      | "unavailable";
    headingKey:
      | "apps.recentUpdate"
      | "apps.firstBatch"
      | "apps.activityUnavailable";
    titleKey:
      | "apps.rawSaving"
      | "apps.importing"
      | "apps.firstRunning"
      | "apps.firstQueued"
      | "apps.rawCheckedEmpty"
      | "apps.memoryCheckedEmpty"
      | "apps.rawReadingCount"
      | "apps.readingActivity"
      | "apps.memoryUnavailable"
      | null;
    detailKey:
      | "apps.rawWaiting"
      | "apps.rawSavingDetail"
      | "apps.savingConversations"
      | "apps.waitingMemories"
      | "apps.recentWillUpdate"
      | "apps.memoryReadFailure"
      | null;
  };
  recentCall: CallEvent | null;
  importsRunning: boolean;
  hostState: "loading" | "ready" | "unavailable";
  syncEnabledCount: number;
}

export function buildHomeViewModel(input: {
  projection: HomeProjectionV1 | null;
  confirmedMode?: "lite" | "pro" | "ultra" | null;
  projectionState: HomeProjectionReadState;
  legacySummary?: HostHomeSummary | null;
  processing: MemoryProcessingSummary | null;
  sedimentation: SedimentationState | null;
  recentCall: CallEvent | null;
  importsRunning: boolean;
  hostState: "loading" | "ready" | "unavailable";
  syncEnabledCount: number;
}): HomeViewModel {
  const { projection } = input;
  const mode = projection?.effective_mode ?? input.confirmedMode ?? null;
  const plane =
    projection?.capabilities.content_plane ??
    (mode === "lite" ? "raw" : mode ? "memory" : null);
  const legacy =
    mode !== "lite" && !projection ? (input.legacySummary ?? null) : null;
  const count = projection
    ? projection.visible_count
    : legacy
      ? legacy.memory_count
      : null;
  const state = input.projectionState;
  const generationRequired =
    projection?.capabilities.generation_required ?? mode !== "lite";
  const sedimenting = input.sedimentation?.enabled;
  const processingLabel =
    !generationRequired || mode === "lite"
      ? "apps.rawSavingCompact"
      : sedimenting === true
        ? "apps.running"
        : sedimenting === false
          ? "apps.paused"
          : "apps.checkingStatus";
  const processingDetail =
    !generationRequired || mode === "lite"
      ? "apps.rawSavingDetail"
      : sedimenting === true
        ? "apps.organizingNew"
        : sedimenting === false
          ? "apps.stoppedNew"
          : "apps.checkingProcessing";
  const latest = projection?.latest;
  const legacyLatest = legacy?.latest ?? null;
  const recent = latest
    ? {
        kind: latest.type,
        excerpt: latest.excerpt,
        sourceId: latest.source_id,
        time: latest.time,
        timeBasis: latest.time_basis,
        emptyExcerpt:
          latest.type === "raw" && latest.display_state === "no_user_excerpt",
      }
    : legacyLatest
      ? {
          kind: "memory" as const,
          excerpt: legacyLatest.content,
          sourceId:
            "revision" in legacyLatest
              ? (legacyLatest.source_ids[0] ?? "")
              : (legacyLatest.source ?? ""),
          time:
            "revision" in legacyLatest
              ? legacyLatest.updated_at
              : legacyLatest.gmt_created === null
                ? null
                : new Date(legacyLatest.gmt_created * 1000).toISOString(),
          timeBasis: null,
          emptyExcerpt: false,
        }
      : null;
  const recentMetric = recent
    ? {
        valueKey: null,
        detailKey:
          recent.kind === "raw"
            ? ("apps.rawSaved" as const)
            : ("apps.saved" as const),
      }
    : state === "loading"
      ? {
          valueKey: "apps.checkingRecent" as const,
          detailKey: "apps.recentWillUpdate" as const,
        }
      : state === "ready" || state === "stale"
        ? {
            valueKey: "apps.recentEmptyCompact" as const,
            detailKey: "apps.checkedRecent" as const,
          }
        : {
            valueKey: "apps.checkingRecent" as const,
            detailKey: "apps.recentWillUpdate" as const,
          };
  const activity: HomeViewModel["activity"] =
    state === "unavailable"
      ? {
          kind: "unavailable",
          headingKey: "apps.activityUnavailable",
          titleKey: "apps.memoryUnavailable",
          detailKey: "apps.memoryReadFailure",
        }
      : state === "loading"
        ? plane === "raw"
          ? {
              kind: "loading",
              headingKey: "apps.recentUpdate",
              titleKey: "apps.rawReadingCount",
              detailKey: "apps.recentWillUpdate",
            }
          : {
              kind: "loading",
              headingKey: "apps.firstBatch",
              titleKey: "apps.readingActivity",
              detailKey: "apps.recentWillUpdate",
            }
        : recent
          ? {
              kind: "update",
              headingKey: "apps.recentUpdate",
              titleKey: null,
              detailKey: null,
            }
          : input.importsRunning
            ? generationRequired
              ? {
                  kind: "importing",
                  headingKey: "apps.firstBatch",
                  titleKey: "apps.importing",
                  detailKey: "apps.savingConversations",
                }
              : {
                  kind: "importing",
                  headingKey: "apps.recentUpdate",
                  titleKey: "apps.rawSaving",
                  detailKey: "apps.rawSavingDetail",
                }
            : generationRequired && input.processing?.running
              ? {
                  kind: "first-running",
                  headingKey: "apps.firstBatch",
                  titleKey: "apps.firstRunning",
                  detailKey: "apps.waitingMemories",
                }
              : generationRequired &&
                  (input.processing?.pending ||
                    input.processing?.waiting_for_model)
                ? {
                    kind: "first-queued",
                    headingKey: "apps.firstBatch",
                    titleKey: "apps.firstQueued",
                    detailKey: "apps.waitingMemories",
                  }
                : plane === "raw"
                  ? {
                      kind: "empty",
                      headingKey: "apps.recentUpdate",
                      titleKey: "apps.rawCheckedEmpty",
                      detailKey: "apps.rawWaiting",
                    }
                  : {
                      kind: "empty",
                      headingKey: "apps.firstBatch",
                      titleKey: "apps.memoryCheckedEmpty",
                      detailKey: "apps.waitingMemories",
                    };
  return {
    mode,
    plane,
    count,
    countLabel: plane === "raw" ? "apps.rawConversations" : "apps.memories",
    countDetail:
      state === "stale"
        ? "apps.showingPrevious"
        : state === "unavailable"
          ? plane === "raw"
            ? "apps.rawCountWillUpdate"
            : "apps.countWillUpdate"
          : state === "loading"
            ? plane === "raw"
              ? "apps.rawReadingCount"
              : "apps.readingCount"
            : "apps.scope",
    processingLabel,
    processingDetail,
    processingTitle:
      plane === "raw" ? "apps.rawSavingStatus" : "apps.processing",
    organizingTitle:
      plane === "raw" ? "apps.rawSavingStatus" : "apps.organizing",
    organizingLabel: processingLabel,
    currentModeLabel: mode ?? "—",
    state,
    recent,
    recentMetric,
    activity,
    recentCall: input.recentCall,
    importsRunning: input.importsRunning,
    hostState: input.hostState,
    syncEnabledCount: input.syncEnabledCount,
  };
}
