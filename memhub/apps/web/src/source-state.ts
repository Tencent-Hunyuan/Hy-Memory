import type { SourceResponse, SourceSyncState } from "@one-memory/contracts";
import { replaceEqualDeep, useMutation, useQuery } from "@tanstack/react-query";
import {
  connectSources,
  detectSource,
  getSources,
  saveSourceDirectory,
  setSourceSync,
} from "./api";
import { onClientReset, queryClient } from "./query-client";
import { isDefiniteRefusal } from "./write-outcome";

export const SOURCES_KEY = ["sources"] as const;
const FRESH_MS = 5_000;
const SYNC_UNCONFIRMED = "此应用的处理开关结果未能确认，其他应用不受影响。";

type Revisioned = {
  grant_revision?: number | null;
};

function compareRevision(left: Revisioned, right: Revisioned) {
  return (left.grant_revision ?? 0) - (right.grant_revision ?? 0);
}

function newer(given: SourceResponse, known: SourceResponse | undefined) {
  return known && compareRevision(known, given) >= 0 ? known : given;
}

/** A source copy with a lower revision never replaces a higher one. */
function keepHigherRevisions(
  previous: SourceResponse[] | undefined,
  next: SourceResponse[],
) {
  if (!previous) return next;
  const known = new Map(previous.map((item) => [item.source_id, item]));
  const merged = next.map((item) => {
    const old = known.get(item.source_id);
    return old && compareRevision(old, item) > 0 ? old : item;
  });
  return replaceEqualDeep(previous, merged);
}

queryClient.setQueryDefaults(SOURCES_KEY, {
  gcTime: Number.POSITIVE_INFINITY,
  staleTime: FRESH_MS,
  structuralSharing: (previous: unknown, next: unknown) =>
    keepHigherRevisions(
      previous as SourceResponse[] | undefined,
      next as SourceResponse[],
    ),
});

let readsStarted = 0;
let lastLandedRead = 0;
onClientReset(() => {
  readsStarted = 0;
  lastLandedRead = 0;
});

// A read that began before the last one to land may only add what it saw at
// a strictly higher revision; it cannot add, drop or rewind sources.
export async function readSources(
  signal?: AbortSignal,
): Promise<SourceResponse[]> {
  const order = ++readsStarted;
  const next = await getSources(signal ? { signal } : undefined);
  const current = cachedSources();
  if (current && order < lastLandedRead) {
    const seen = new Map(next.map((item) => [item.source_id, item]));
    return current.map((item) => {
      const later = seen.get(item.source_id);
      return later && compareRevision(later, item) > 0 ? later : item;
    });
  }
  lastLandedRead = order;
  return next;
}

export function cachedSources() {
  return queryClient.getQueryData<SourceResponse[]>(SOURCES_KEY);
}

/** The highest-revision copy known for this source. */
export function latestSource(source: SourceResponse) {
  return newer(
    source,
    cachedSources()?.find((item) => item.source_id === source.source_id),
  );
}

/** Puts a read into the shared cache; lower revisions never replace higher. */
export function commitSources(next: SourceResponse[]): SourceResponse[] {
  return queryClient.setQueryData<SourceResponse[]>(SOURCES_KEY, next) ?? next;
}

/** Always a new request, committed as soon as it lands. */
export async function refreshSources(): Promise<SourceResponse[]> {
  return commitSources(await readSources());
}

/** Reuses data younger than FRESH_MS; otherwise reads again. */
export function prefetchSources() {
  return queryClient.prefetchQuery({
    queryKey: SOURCES_KEY,
    queryFn: ({ signal }) => readSources(signal),
  });
}

export function useSources() {
  return useQuery(
    {
      queryKey: SOURCES_KEY,
      queryFn: ({ signal }) => readSources(signal),
      enabled: false,
    },
    queryClient,
  ).data;
}

export function useSource(source: SourceResponse) {
  const sources = useSources();
  return newer(
    source,
    sources?.find((item) => item.source_id === source.source_id),
  );
}

function applySyncReceipt(receipt: SourceSyncState | undefined) {
  if (!receipt?.source_id) return;
  queryClient.setQueryData<SourceResponse[]>(SOURCES_KEY, (items) =>
    items?.map((item) =>
      item.source_id === receipt.source_id
        ? {
            ...item,
            sync_enabled: receipt.sync_enabled,
          }
        : item,
    ),
  );
}

/** What a source authorization state asks the person to do, or null.
 *
 * Every one of these is a source fix. None of them is a plugin fix, and
 * pointing at "reinstall" here is what sent people through uninstall cycles
 * that could not touch the cause (issue #29).
 */
export function sourceAuthorizationNote(source: SourceResponse): string | null {
  switch (source.authorization_state) {
    case "missing_grant":
      return "此应用还没有读取授权，请先授权它的对话目录。";
    case "revoked_grant":
      return "此应用的读取授权已撤销，请重新授权。";
    case "source_identity_changed":
      return "此应用的对话目录已被重建，请重新授权。";
    case "source_root_unavailable":
      return "此应用的对话目录现在读不到，请确认应用已安装并产生过对话。";
    case "candidate_changed":
      return "此应用的对话目录已变更，请按最新目录重新授权。";
    default:
      return null;
  }
}

/** Core stores a rejected sync write without its reason; the re-read shows it. */
function syncRejectionMessage(
  before: SourceResponse,
  _enabled: boolean,
  latest: SourceResponse | undefined,
): string | null {
  if (!latest) return null;
  const authorization = sourceAuthorizationNote(latest);
  if (authorization) return `${authorization}完成后再开启持续同步。`;
  if (!latest.authorized)
    return "此应用的读取授权已失效，请重新授权后再开启持续同步。";
  if (
    latest.root_id !== before.root_id ||
    latest.grant_revision !== before.grant_revision
  )
    return "此应用的授权或目录已变化，已按最新状态显示，请确认后再试。";
  return null;
}

export type SourceSyncWrite = {
  source: SourceResponse;
  enabled: boolean;
  /** Set by the caller so the ui.action/ui.result pair joins the kernel legs. */
  traceId?: string;
};

async function writeSourceSync({ source, enabled, traceId }: SourceSyncWrite) {
  const current = latestSource(source);
  try {
    return await setSourceSync(current, enabled, traceId);
  } catch (reason) {
    const latest = await refreshSources().then(
      (items) => items.find((item) => item.source_id === source.source_id),
      () => undefined,
    );
    if (latest?.sync_enabled === enabled) {
      return { source_id: source.source_id, sync_enabled: enabled };
    }
    throw Object.assign(
      new Error(
        syncRejectionMessage(current, enabled, latest) ??
          (reason instanceof Error && reason.message
            ? reason.message
            : SYNC_UNCONFIRMED),
      ),
      { definiteRefusal: isDefiniteRefusal(reason) },
    );
  }
}

export function useSourceSync() {
  return useMutation(
    {
      mutationKey: ["source-sync"],
      mutationFn: writeSourceSync,
      onSuccess: applySyncReceipt,
    },
    queryClient,
  );
}

let sourceWrites = 0;
onClientReset(() => {
  sourceWrites = 0;
});

/** Source writes other than sync. Their receipts do not carry a whole source
 * record, and each caller re-reads the list once afterwards, success or not,
 * so the re-read is where the result lands. Counted so the remembered apply
 * stays out of the way while one runs. */
function sourceWrite<TVars, TData>(write: (vars: TVars) => Promise<TData>) {
  return async (vars: TVars) => {
    sourceWrites += 1;
    try {
      return await write(vars);
    } finally {
      sourceWrites -= 1;
    }
  };
}

export function sourceWriteInFlight() {
  return (
    sourceWrites > 0 ||
    queryClient.isMutating({ mutationKey: ["source-sync"] }) > 0
  );
}

export const detectSourceAgain = sourceWrite(
  (input: Parameters<typeof detectSource>) => detectSource(...input),
);

export const connectSourceSet = sourceWrite(
  (input: Parameters<typeof connectSources>) => connectSources(...input),
);

export const saveSourceRoot = sourceWrite(
  (input: Parameters<typeof saveSourceDirectory>) =>
    saveSourceDirectory(...input),
);

/** Looks up a pending sync intent's receipt; creates no write of its own. */
