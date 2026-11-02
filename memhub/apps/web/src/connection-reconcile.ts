import type { SourceResponse } from "@one-memory/contracts";
import { useQuery } from "@tanstack/react-query";
import {
  connectionHostId,
  isProfileSource,
  sameConnectionHost,
} from "./connection-hosts";
import {
  readConnectionPreferences,
  writeConnectionPreference,
} from "./connection-preferences";
import {
  cachedRecallHosts,
  enableHostConnection,
  hostConnectionUnsupported,
  hostWriteInFlight,
  hostWriteReason,
  matchHost,
  profileHosts,
  profileWriteFailures,
  sourceConnectionIncomplete,
} from "./host-state";
import { onClientReset, queryClient } from "./query-client";
import { latestSource, sourceWriteInFlight } from "./source-state";
import { traceUiAction } from "./ui-trace";
import { isDefiniteRefusal } from "./write-outcome";

export type AssociationStatus = "检测中" | "成功关联" | "未开启关联";

type SyncWriter = (
  source: SourceResponse,
  enabled: boolean,
  traceId?: string,
) => Promise<void>;
type SourceRefresh = () => Promise<{ sources?: SourceResponse[] } | undefined>;
type ReconcileOptions = {
  sources: SourceResponse[];
  setSync?: SyncWriter;
  refresh?: SourceRefresh;
};

export type AutoConnectFailure = {
  kind: "sync" | "host";
  sourceId: string;
  reason: string | null;
  /** Which profiles of a per-profile source it was; the others are kept. */
  profiles?: string[];
};
export type AutoConnectState = {
  running: string[];
  attempted: string[];
  failures: AutoConnectFailure[];
};

const AUTO_KEY = ["connection-auto"] as const;
const IDLE: AutoConnectState = { running: [], attempted: [], failures: [] };

queryClient.setQueryDefaults(AUTO_KEY, {
  gcTime: Number.POSITIVE_INFINITY,
  staleTime: Number.POSITIVE_INFINITY,
});

let flight: Promise<AutoConnectFailure[]> | null = null;
const seenProfiles = new Map<string, Set<string>>();
onClientReset(() => {
  flight = null;
  seenProfiles.clear();
});

function autoState() {
  return queryClient.getQueryData<AutoConnectState>(AUTO_KEY) ?? IDLE;
}

function updateAuto(change: (state: AutoConnectState) => AutoConnectState) {
  queryClient.setQueryData<AutoConnectState>(AUTO_KEY, change(autoState()));
}

/** What the remembered-preference apply is doing and what it could not do. */
export function useAutoConnect() {
  return useQuery(
    {
      queryKey: AUTO_KEY,
      queryFn: autoState,
      enabled: false,
      initialData: IDLE,
    },
    queryClient,
  ).data;
}

export function hostKey(sourceId: string) {
  return `host:${connectionHostId(sourceId) ?? sourceId}`;
}

function syncKey(sourceId: string) {
  return `sync:${sourceId}`;
}

export function associationStatus(input: {
  probing: boolean;
  approved: boolean;
  intended?: boolean;
}): AssociationStatus {
  if (input.approved) return "成功关联";
  if (input.probing || input.intended) return "检测中";
  return "未开启关联";
}

/**
 * Submits one sync write, never more. A failed write has already been re-read
 * once by the source layer, so whether Core committed it anyway is read from
 * the shared cache rather than asked again.
 */
export async function enableSourceSync(options: {
  sourceId: string;
  sources: SourceResponse[];
  setSync: SyncWriter;
}) {
  const given = options.sources.find(
    (item) => item.source_id === options.sourceId,
  );
  const source = given ? latestSource(given) : undefined;
  if (!source?.authorized) throw Error("source_unavailable");
  if (source.sync_enabled) return;
  const trace = traceUiAction("sources", "confirm");
  try {
    await options.setSync(source, true, trace.traceId);
    trace.done("succeeded");
  } catch (error) {
    if (latestSource(source).sync_enabled) {
      trace.done("succeeded");
      return;
    }
    trace.done(isDefiniteRefusal(error) ? "failed" : "unknown", {
      reason: isDefiniteRefusal(error)
        ? "source_sync_failed"
        : "source_sync_result_unknown",
    });
    throw error;
  }
}

/** One explicit pass over every remembered preference. */
export async function reconcileConnectionPreferences(
  options: ReconcileOptions,
) {
  if (flight) return flight;
  const preferences = readConnectionPreferences();
  flight = runReconcile(options, {
    sync: preferences.syncSourceIds,
    host: preferences.hostSourceIds,
  }).finally(() => {
    flight = null;
  });
  return flight;
}

function hostNeedsApply(sourceId: string) {
  const hosts = cachedRecallHosts();
  if (hosts === undefined) return false;
  return sourceConnectionIncomplete(hosts, sourceId);
}

/** `attempted` holds one apply per source, which is right until a per-profile
 * source grows a profile: that one was never tried, so the remembered switch
 * gets one more apply. A profile that fails is not retried on every poll. */
function rearmNewProfiles(sourceIds: string[]) {
  const hosts = cachedRecallHosts();
  if (hosts === undefined) return;
  for (const sourceId of sourceIds) {
    if (!isProfileSource(sourceId)) continue;
    const current = profileHosts(hosts, sourceId).map((host) => host.hostId);
    const seen = seenProfiles.get(sourceId);
    seenProfiles.set(sourceId, new Set([...(seen ?? []), ...current]));
    if (!seen || current.every((hostId) => seen.has(hostId))) continue;
    updateAuto((state) => ({
      ...state,
      attempted: state.attempted.filter((key) => key !== hostKey(sourceId)),
    }));
  }
}

/** Whether a remembered host is still waiting for (or in) its one apply. */
export function hostApplyPending(state: AutoConnectState, sourceIds: string[]) {
  return sourceIds.some(
    (id) =>
      state.running.includes(hostKey(id)) ||
      (!state.attempted.includes(hostKey(id)) && hostNeedsApply(id)),
  );
}

/** The user chose for this host themselves; the remembered apply stands down. */
export function noteUserHostChoice(sourceIds: string[]) {
  updateAuto((state) => ({
    ...state,
    attempted: [...new Set([...state.attempted, ...sourceIds.map(hostKey)])],
  }));
}

function syncNeedsApply(sources: SourceResponse[], sourceId: string) {
  const source = sources.find((item) => item.source_id === sourceId);
  return Boolean(source?.authorized && source.sync_enabled !== true);
}

/**
 * Applies a remembered preference only where it differs from what is really
 * there, with no host write running, and at most once: a failure is kept for
 * the user to see instead of being retried on the next source change.
 * `only` limits the apply to the host of one app.
 */
export function autoReconcile(
  options: ReconcileOptions & { only?: string },
): Promise<AutoConnectFailure[] | null> {
  if (flight || hostWriteInFlight() || sourceWriteInFlight())
    return flight ?? Promise.resolve(null);
  const hosts = cachedRecallHosts();
  // Unsupported never succeeds; drop it without asking the bridge again.
  for (const id of readConnectionPreferences().hostSourceIds)
    if (hostConnectionUnsupported(matchHost(hosts, id)))
      writeConnectionPreference("host", id, false);
  const preferences = readConnectionPreferences();
  rearmNewProfiles(preferences.hostSourceIds);
  const { attempted } = autoState();
  const only = options.only;
  const wantedHosts = only
    ? preferences.hostSourceIds.some((id) => sameConnectionHost(id, only))
      ? [only]
      : []
    : preferences.hostSourceIds;
  const host = wantedHosts.filter(
    (id) => !attempted.includes(hostKey(id)) && hostNeedsApply(id),
  );
  const sync =
    only || !options.setSync
      ? []
      : preferences.syncSourceIds.filter(
          (id) =>
            !attempted.includes(syncKey(id)) &&
            syncNeedsApply(options.sources, id),
        );
  if (!host.length && !sync.length) return Promise.resolve(null);
  const keys = [...host.map(hostKey), ...sync.map(syncKey)];
  updateAuto((state) => ({
    ...state,
    running: keys,
    attempted: [...new Set([...state.attempted, ...keys])],
  }));
  flight = runReconcile(options, { sync, host })
    .then((failures) => {
      updateAuto((state) => ({
        ...state,
        failures: [
          ...state.failures.filter(
            (old) =>
              !keys.includes(
                old.kind === "host"
                  ? hostKey(old.sourceId)
                  : syncKey(old.sourceId),
              ),
          ),
          ...failures,
        ],
      }));
      return failures;
    })
    .finally(() => {
      flight = null;
      updateAuto((state) => ({ ...state, running: [] }));
    });
  return flight;
}

/** The user asked again: forget what failed and apply it once more. */
export function retryAutoReconcile(options: ReconcileOptions) {
  const failed = autoState().failures.map((item) =>
    item.kind === "host" ? hostKey(item.sourceId) : syncKey(item.sourceId),
  );
  updateAuto((state) => ({
    ...state,
    failures: [],
    attempted: state.attempted.filter((key) => !failed.includes(key)),
  }));
  return autoReconcile(options);
}

export function resetConnectionReconcile() {
  flight = null;
  seenProfiles.clear();
  queryClient.removeQueries({ queryKey: AUTO_KEY });
}

async function runReconcile(
  options: ReconcileOptions,
  targets: { sync: string[]; host: string[] },
) {
  const failures: AutoConnectFailure[] = [];
  if (!targets.sync.length && !targets.host.length) return failures;
  let sources = options.sources;
  if (options.refresh && targets.sync.length) {
    try {
      sources = (await options.refresh())?.sources ?? sources;
    } catch {
      // Fall back to the snapshot the caller already has.
    }
  }
  if (options.setSync) {
    for (const sourceId of targets.sync) {
      const source = sources.find((item) => item.source_id === sourceId);
      if (source?.sync_enabled) continue;
      try {
        await enableSourceSync({
          sourceId,
          sources,
          setSync: options.setSync,
        });
      } catch (error) {
        // Per-app: other sources keep going.
        failures.push({
          kind: "sync",
          sourceId,
          reason:
            error instanceof Error && error.message ? error.message : null,
        });
      }
    }
  }
  for (const sourceId of targets.host) {
    try {
      await enableHostConnection(sourceId);
    } catch (error) {
      // Per-app: other hosts keep going. Unsupported never succeeds on retry.
      if (error instanceof Error && error.message === "host_unsupported")
        writeConnectionPreference("host", sourceId, false);
      else {
        const profiles = profileWriteFailures(error);
        failures.push({
          kind: "host",
          sourceId,
          reason: hostWriteReason(error),
          ...(profiles ? { profiles } : {}),
        });
      }
    }
  }
  return failures;
}
