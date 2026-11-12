import type {
  ConnectSourcesResponse,
  HealthResponse,
  ImportJob,
  SourceResponse,
} from "@one-memory/contracts";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  apiErrorCode,
  changeImportJob,
  createImportJob,
  getHealth,
  getImportJob,
  getImportJobs,
  getSourceConnection,
} from "./api";
import {
  appendIntentRequest,
  assertCheckedConnection,
  type CheckedConnection,
  connectionFingerprint,
  forgetIntentRequest,
  freezeConnection,
  readIntentRequests,
} from "./connection-intent";
import { subscribePoll } from "./poll-scheduler";
import {
  cachedSources,
  commitSources,
  connectSourceSet,
  detectSourceAgain,
  readSources,
  useSourceSync,
  useSources,
} from "./source-state";
import { traceUiAction } from "./ui-trace";

const NO_SOURCES: SourceResponse[] = [];
const ACTIVE_IMPORT_STATUSES = new Set(["pending", "running"]);
const RECOVERY_SCOPE_ERROR =
  "无法确认原连接请求所属的数据范围，已保留请求编号。请核对原实例回执，不会自动重新授权。";
const JOB_ACTION_UNCERTAIN =
  "任务操作结果待核对，保留最后已确认状态。请重新核对；不会自动重复发送操作。";

export type ImportJobAction = "pause" | "resume" | "cancel";

export interface ImportJobActionState {
  action: ImportJobAction;
  pending: boolean;
  uncertain: boolean;
  error: string | null;
}

export interface SourceSnapshot {
  confirmedAt: string | null;
  stale: boolean;
}

interface JobActionIntent {
  action: ImportJobAction;
  before: ImportJob;
  instanceId: string;
}

interface JobReceipt {
  job: ImportJob;
  instanceId: string;
}

// Current Core adds these fields; older contract fixtures legitimately omit them.
type JobWithContext = ImportJob & {
  operation?: "history" | "sync";
};

interface ConnectOptions {
  retryFailed?: boolean;
  importUploadId?: string;
  /** The interaction's trace id, so the connect request carries the kernel
   * legs that join the renderer's `ui.action` / `ui.result` pair. */
  traceId?: string;
}

export function useImportSources(_options: { automaticSync?: boolean } = {}) {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const sources = useSources() ?? NO_SOURCES;
  const syncWrite = useSourceSync();
  const [jobs, setJobs] = useState<ImportJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [healthStale, setHealthStale] = useState(false);
  const [sourceSnapshot, setSourceSnapshot] = useState<SourceSnapshot>({
    confirmedAt: null,
    stale: true,
  });
  const [jobActions, setJobActions] = useState<
    ReadonlyMap<string, ImportJobActionState>
  >(() => new Map());
  const [sourceSyncWriteConfirmed, setSourceSyncWriteConfirmed] = useState<
    ReadonlySet<string>
  >(() => new Set());
  const [lastConnectResult, setLastConnectResult] =
    useState<ConnectSourcesResponse | null>(null);
  const alive = useRef(true);
  const refreshVersion = useRef(0);
  const refreshFloor = useRef(0);
  const current = useRef({ health, jobs });
  const jobFlights = useRef(
    new Map<string, { action: ImportJobAction; promise: Promise<ImportJob> }>(),
  );
  const uncertainJobActions = useRef(new Map<string, JobActionIntent>());
  const jobReceipts = useRef(new Map<string, JobReceipt>());
  const liveRequests = useRef(
    new Map<string, { instanceId: string; committed: boolean }>(),
  );
  const connectionFlight = useRef<{
    identity: string;
    promise: Promise<ConnectSourcesResponse>;
  } | null>(null);

  const connectSnapshot = async (
    checked: CheckedConnection,
    retryFailed: boolean,
    traceId?: string,
  ) => {
    assertCheckedConnection(checked, cachedSources() ?? []);
    const instanceId = current.current.health?.instance_id;
    if (!instanceId) throw new Error(RECOVERY_SCOPE_ERROR);
    const fingerprint = await connectionFingerprint(checked.identity);
    const requestIds = readIntentRequests(fingerprint);
    let remaining = checked.sources.map((source) => source.source_id);
    const receipts: ConnectSourcesResponse[] = [];
    let submitted = false;

    const submit = async (ids: string[], requestId?: string) => {
      const live = requestId ? liveRequests.current.get(requestId) : undefined;
      // instance_id is only a live-process check. It is deliberately never persisted.
      const fresh = await getHealth();
      if (!alive.current) throw new Error("连接确认已关闭，尚未提交新的授权。");
      assertCheckedConnection(checked, cachedSources() ?? []);
      if (
        fresh.instance_id !== instanceId ||
        current.current.health?.instance_id !== instanceId ||
        (requestId
          ? !live || live.committed || live.instanceId !== fresh.instance_id
          : false)
      ) {
        throw new Error(RECOVERY_SCOPE_ERROR);
      }
      const id = requestId ?? crypto.randomUUID();
      if (!requestId) {
        appendIntentRequest(fingerprint, requestIds, id, fresh.instance_id);
        requestIds.push(id);
        liveRequests.current.set(id, {
          instanceId: fresh.instance_id,
          committed: false,
        });
      }
      submitted = true;
      let result: ConnectSourcesResponse;
      try {
        result = await connectSourceSet([
          [...ids],
          true,
          {
            fullRescan: checked.onboarding,
            onboarding: checked.onboarding,
            enableSync: true,
            requestId: id,
            ...(traceId ? { traceId } : {}),
            ...(checked.importUploadId === undefined
              ? {}
              : { importUploadId: checked.importUploadId }),
            sourceRoots: Object.fromEntries(
              checked.sources
                .filter((source) => ids.includes(source.source_id))
                .map((source) => [source.source_id, source.candidate_root_id]),
            ),
            ...(checked.sources.some(
              (source) => (source.candidate_revision ?? 0) > 0,
            )
              ? {
                  sourceCandidateRevisions: Object.fromEntries(
                    checked.sources
                      .filter((source) => ids.includes(source.source_id))
                      .map((source) => [
                        source.source_id,
                        source.candidate_revision ?? 0,
                      ]),
                  ),
                }
              : {}),
          },
        ]);
      } catch (reason) {
        // Core persists the idempotent receipt before it finishes activating
        // every source lane. A large cold start can therefore commit and keep
        // working after the POST's 30s transport deadline. Read the receipt
        // before surfacing an unknown result; never replay the write here.
        if (isDefiniteAccessRefusal(reason)) throw reason;
        try {
          result = await getSourceConnection(id);
        } catch {
          throw reason;
        }
      }
      assertConnectionReceipt(result, id, ids);
      liveRequests.current.set(id, {
        instanceId: fresh.instance_id,
        committed: true,
      });
      return result;
    };

    // Rehydrate results only from Core. Stored IDs cannot authorize a missing request.
    for (const requestId of [...requestIds]) {
      if (!remaining.length) throw new Error(RECOVERY_SCOPE_ERROR);
      let known: ConnectSourcesResponse;
      try {
        known = await getSourceConnection(requestId);
      } catch (reason) {
        if (apiErrorCode(reason) !== "source_intent_not_found") throw reason;
        if (!liveRequests.current.has(requestId)) {
          // This core has no receipt. A wiped database plus a new process
          // looks the same as a leftover id from the previous run, including
          // one already stamped with that run's instance id. Drop it and let
          // the confirmation just made start a new request.
          forgetIntentRequest(fingerprint, requestIds, requestId);
          const index = requestIds.indexOf(requestId);
          if (index >= 0) requestIds.splice(index, 1);
          continue;
        }
        known = await submit(remaining, requestId);
      }
      assertConnectionReceipt(known, requestId, remaining);
      assertCheckedConnection(checked, cachedSources() ?? []);
      if (current.current.health?.instance_id !== instanceId)
        throw new Error(RECOVERY_SCOPE_ERROR);
      const live = liveRequests.current.get(requestId);
      if (live) live.committed = true;
      receipts.push(known);
      remaining = Object.keys(known.failed_sources).sort();
    }
    if (!receipts.length || (retryFailed && remaining.length && !submitted)) {
      receipts.push(await submit(remaining));
    }
    if (receipts.length === 1) return receipts[0];
    const last = receipts[receipts.length - 1];
    return {
      // This is a controller summary of multiple Core receipts, not a server receipt.
      request_id: null,
      accepted_source_ids: [
        ...new Set(receipts.flatMap((receipt) => receipt.accepted_source_ids)),
      ].sort(),
      failed_sources: last.failed_sources,
      jobs: receipts.reduce(
        (jobs, receipt) => mergeJobs(jobs, receipt.jobs),
        [] as ImportJob[],
      ),
    };
  };

  const refresh = useCallback(async () => {
    const version = ++refreshVersion.current;
    try {
      // The health object is deliberately kept when a read fails: it names the
      // instance that owns the running backfill and the write receipts, and
      // dropping it would orphan them. Keeping it means the object on screen can
      // be older than the truth, so whether it is still true has to be carried
      // separately rather than inferred from it being present.
      // Stale is raised only when the read fails. A read in flight is not yet
      // grounds to distrust the object, and raising it on every poll made the
      // headline alternate between two states on every tick.
      const [nextHealth, sourceRead, nextJobs] = await Promise.all([
        getHealth(),
        readSources(),
        getImportJobs(),
      ]);
      if (!alive.current || version < refreshFloor.current) {
        throw new Error("来源状态正在更新，请稍后重新核对。");
      }
      refreshFloor.current = version;
      const nextSources = commitSources(sourceRead);
      const confirmedJobs = preserveJobReceipts(
        nextJobs,
        nextHealth.instance_id,
        jobReceipts.current,
      );
      current.current = { health: nextHealth, jobs: confirmedJobs };
      setHealth(nextHealth);
      setJobs(confirmedJobs);
      setHealthStale(false);
      setRefreshError(null);
      // This is a local observation of all three reads, never a source timestamp.
      setSourceSnapshot({
        confirmedAt: new Date().toISOString(),
        stale: false,
      });
      return { health: nextHealth, sources: nextSources, jobs: confirmedJobs };
    } catch (reason) {
      if (alive.current && version >= refreshFloor.current) {
        refreshFloor.current = version;
        setRefreshError(toRefreshMessage(reason));
        setHealthStale(true);
        setSourceSnapshot((previous) => ({ ...previous, stale: true }));
      }
      throw reason;
    }
  }, []);

  const refreshWithStatus = useCallback(async () => {
    try {
      await refresh();
    } catch {
      /* refresh owns status; operation results remain authoritative. */
    }
  }, [refresh]);

  useEffect(() => {
    let active = true;
    alive.current = true;
    refreshWithStatus().finally(() => {
      if (active) setLoading(false);
    });
    return () => {
      active = false;
      alive.current = false;
      refreshFloor.current = ++refreshVersion.current;
    };
  }, [refreshWithStatus]);

  const hasActiveImport = jobs.some((job) =>
    ACTIVE_IMPORT_STATUSES.has(job.status),
  );
  const previouslyImporting = useRef(false);
  useEffect(() => {
    const finished = previouslyImporting.current && !hasActiveImport;
    previouslyImporting.current = hasActiveImport;
    if (!finished) return;
    // Source counts may lag writes by two seconds; settle once after the
    // final job so stopping the active poll cannot leave a stale zero.
    const settle = window.setTimeout(() => void refreshWithStatus(), 2200);
    return () => window.clearTimeout(settle);
  }, [hasActiveImport, refreshWithStatus]);
  // Core catching up on conversations it had no model for is the other thing
  // that finishes on its own. Without asking again, the moment it finishes
  // never reaches the screen. It takes seconds rather than the sub-second
  // steps an import reports, so it is asked after less often.
  const catchingUp = health?.memory_backfill === "running";
  useEffect(() => {
    if (!hasActiveImport && !catchingUp) return;
    const stop = subscribePoll({
      key: "imports",
      intervalMs: hasActiveImport ? 1_000 : 3_000,
      run: refreshWithStatus,
      // An import the user cannot see is still an import that finishes; the
      // settle + backfill reads must not pause with the tab.
      pauseWhenHidden: false,
    });
    return stop;
  }, [hasActiveImport, catchingUp, refreshWithStatus]);

  const latestJobBySource = useMemo(() => {
    const latest = new Map<string, ImportJob>();
    for (const job of jobs) {
      const previous = latest.get(job.source_id);
      if (!previous || previous.created_at < job.created_at) {
        latest.set(job.source_id, job);
      }
    }
    return latest;
  }, [jobs]);

  const syncEligible = useCallback(
    async (traceId?: string) => {
      await Promise.all(
        sources
          .filter((source) => source.authorized)
          .filter((source) => source.sync_enabled === true)
          .filter((source) => source.sync_completeness !== "one_time_snapshot")
          .filter((source) => {
            const job = latestJobBySource.get(source.source_id);
            return !job || ["completed", "cancelled"].includes(job.status);
          })
          .map((source) => createImportJob(source.source_id, "sync", traceId)),
      );
    },
    [latestJobBySource, sources],
  );

  const run = useCallback(
    async (key: string, operation: (traceId: string) => Promise<unknown>) => {
      refreshFloor.current = ++refreshVersion.current;
      setBusyKey(key);
      setError(null);
      // `key` is `detect:<id>` / `import:<id>` / `sync:all`. Only the word
      // before the colon goes into the log, so the trace stays queryable
      // without recording a source id or any content.
      const reasonPrefix = key.includes(":")
        ? key.slice(0, key.indexOf(":"))
        : key;
      const trace = traceUiAction("sources", "click");
      try {
        await operation(trace.traceId);
        trace.done("succeeded");
      } catch (reason) {
        setError(toMessage(reason));
        // A thrown error does not prove Core refused the write. Saying `failed`
        // in the log while the copy hedges invites a retry that can double-apply
        // an import, so the outcome follows the same refusal/unknown split the
        // switch paths already use.
        trace.done(isDefiniteAccessRefusal(reason) ? "failed" : "unknown", {
          reason: isDefiniteAccessRefusal(reason)
            ? `${reasonPrefix}_failed`
            : `${reasonPrefix}_result_unknown`,
        });
      } finally {
        refreshFloor.current = ++refreshVersion.current;
        if (alive.current) {
          await refreshWithStatus();
          setBusyKey(null);
        }
      }
    },
    [refreshWithStatus],
  );

  const changeJob = (
    jobId: string,
    action: ImportJobAction,
  ): Promise<ImportJob> => {
    const flight = jobFlights.current.get(jobId);
    if (flight) {
      return flight.action === action
        ? flight.promise
        : Promise.reject(new Error("此任务的另一操作正在核对，请等待回执。"));
    }
    const uncertain = uncertainJobActions.current.get(jobId);
    if (uncertain && uncertain.action !== action)
      return Promise.reject(new Error(JOB_ACTION_UNCERTAIN));
    const known = current.current.jobs.find((job) => job.job_id === jobId);
    const instanceId = current.current.health?.instance_id;
    if (!alive.current || !known || !instanceId)
      return Promise.reject(new Error("任务身份尚未核对，请先刷新状态。"));
    if (!uncertain && action === "resume" && isFinalJob(known))
      return Promise.reject(new Error("本次任务已结束，不能继续导入。"));
    const intent = uncertain ?? { action, before: { ...known }, instanceId };

    const updateAction = (state: ImportJobActionState) => {
      if (!alive.current) return;
      setJobActions((previous) => new Map(previous).set(jobId, state));
    };
    const requireScope = () => {
      const currentJob = current.current.jobs.find(
        (job) => job.job_id === jobId,
      );
      if (
        !alive.current ||
        current.current.health?.instance_id !== intent.instanceId ||
        !currentJob ||
        !sameJobIdentity(currentJob, intent.before)
      )
        throw new Error(
          "任务身份或 Core 实例已变化，操作结果待核对。请刷新状态。",
        );
    };
    const remember = (job: ImportJob): ImportJob => {
      requireScope();
      assertJobReceipt(job, intent.before);
      const latest = current.current.jobs.find((item) => item.job_id === jobId);
      const confirmed = latest ? newerJob(latest, job) : job;
      jobReceipts.current.set(jobId, {
        job: confirmed,
        instanceId: intent.instanceId,
      });
      // A poll started before the command/read receipt must not restore old state.
      refreshFloor.current = ++refreshVersion.current;
      const next = current.current.jobs.map((item) =>
        item.job_id === jobId ? confirmed : item,
      );
      current.current = { ...current.current, jobs: next };
      setJobs(next);
      return confirmed;
    };

    updateAction({
      action,
      pending: true,
      uncertain: !!uncertain,
      error: null,
    });
    // One interaction, one trace: every await inside this promise belongs to
    // the same user action, and the result line reports what the UI rendered.
    const trace = traceUiAction(
      "sources",
      action === "cancel"
        ? "cancel"
        : action === "resume"
          ? "submit"
          : "confirm",
    );
    const promise = Promise.resolve().then(async () => {
      let needsCheck = !!uncertain;
      try {
        requireScope();
        let confirmed: ImportJob | undefined;
        if (!needsCheck) {
          try {
            confirmed = remember(
              await changeImportJob(jobId, action, trace.traceId),
            );
          } catch (reason) {
            if (isDefiniteJobRejection(reason)) throw reason;
            needsCheck = true;
            uncertainJobActions.current.set(jobId, intent);
          }
        }
        if (needsCheck) {
          requireScope();
          // There is no command receipt/idempotency key for this API. A later
          // running state cannot prove resume completed, so never repost here.
          const observed = remember(await getImportJob(jobId));
          if (!resolvesJobAction(action, observed))
            throw new Error(JOB_ACTION_UNCERTAIN);
          confirmed = observed;
        }
        if (!confirmed) throw new Error(JOB_ACTION_UNCERTAIN);
        uncertainJobActions.current.delete(jobId);
        updateAction({ action, pending: false, uncertain: false, error: null });
        trace.done(needsCheck ? "unknown" : "succeeded");
        // Refresh is supplementary: committed success must not wait on or fail
        // because another source is slow/offline. Receipt guards protect jobs.
        if (alive.current) void refreshWithStatus();
        return confirmed;
      } catch (reason) {
        const unresolved = needsCheck || uncertainJobActions.current.has(jobId);
        if (unresolved) uncertainJobActions.current.set(jobId, intent);
        const message = unresolved
          ? JOB_ACTION_UNCERTAIN
          : jobActionMessage(reason);
        updateAction({
          action,
          pending: false,
          uncertain: unresolved,
          error: message,
        });
        trace.done(unresolved ? "unknown" : "failed", {
          reason: `${action}_${unresolved ? "unconfirmed" : "failed"}`,
        });
        throw new Error(message);
      } finally {
        jobFlights.current.delete(jobId);
      }
    });
    jobFlights.current.set(jobId, { action, promise });
    return promise;
  };

  const connect = (
    sourceIds: string[],
    snapshot: SourceResponse[] | undefined,
    onboarding: boolean,
    options: ConnectOptions = {},
  ): Promise<ConnectSourcesResponse> => {
    let checked: CheckedConnection;
    try {
      checked = freezeConnection(
        sourceIds,
        snapshot,
        onboarding,
        options.importUploadId,
      );
    } catch (reason) {
      setError(toMessage(reason));
      return Promise.reject(reason);
    }
    const flight = connectionFlight.current;
    if (flight) {
      return flight.identity === checked.identity
        ? flight.promise
        : Promise.reject(new Error("另一连接请求仍在确认中，请等待回执。"));
    }
    refreshFloor.current = ++refreshVersion.current;
    setBusyKey(onboarding ? "connect:onboarding" : "connect:selected");
    setError(null);
    setLastConnectResult(null);
    const promise = (async () => {
      try {
        const result = await connectSnapshot(
          checked,
          options.retryFailed === true,
          options.traceId,
        );
        refreshFloor.current = ++refreshVersion.current;
        if (alive.current) {
          setLastConnectResult(result);
          const nextJobs = mergeJobs(current.current.jobs, result.jobs);
          current.current = { ...current.current, jobs: nextJobs };
          setJobs(nextJobs);
        }
        return result;
      } catch (reason) {
        if (alive.current) setError(toMessage(reason));
        throw reason;
      } finally {
        refreshFloor.current = ++refreshVersion.current;
        if (alive.current) {
          await refreshWithStatus();
          setBusyKey(null);
        }
        connectionFlight.current = null;
      }
    })();
    connectionFlight.current = { identity: checked.identity, promise };
    return promise;
  };

  const actionState: {
    jobActions?: ReadonlyMap<string, ImportJobActionState>;
    sourceSnapshot?: SourceSnapshot;
    sourceSyncWriteConfirmed?: ReadonlySet<string>;
  } = { jobActions, sourceSnapshot, sourceSyncWriteConfirmed };
  return {
    ...actionState,
    health,
    healthStale,
    sources,
    jobs,
    loading,
    busyKey,
    error: error ?? refreshError,
    lastConnectResult,
    latestJobBySource,
    refresh,
    connectForOnboarding: (
      sourceIds: string[],
      snapshot?: SourceResponse[],
      options?: ConnectOptions,
    ) => connect(sourceIds, snapshot, true, options),
    connectSelected: (
      sourceIds: string[],
      snapshot?: SourceResponse[],
      options?: ConnectOptions,
    ) => connect(sourceIds, snapshot, false, options),
    syncAll: () => run("sync:all", (traceId) => syncEligible(traceId)),
    setSync: async (
      source: SourceResponse,
      enabled: boolean,
      traceId?: string,
    ) => {
      refreshFloor.current = ++refreshVersion.current;
      setBusyKey(`sync-permission:${source.source_id}`);
      setError(null);
      let written = false;
      try {
        await syncWrite.mutateAsync({ source, enabled, traceId });
        written = true;
        if (alive.current)
          setSourceSyncWriteConfirmed((previous) =>
            new Set(previous).add(source.source_id),
          );
      } catch (reason) {
        const text = toMessage(reason);
        if (alive.current) setError(text);
        // Preserve whether Core definitely refused this. Replacing the error
        // with a plain `Error(text)` erased the HTTP status, which left callers
        // unable to tell a 409 ("refused, safe to re-read") from a timeout
        // ("unknown, do not repeat"). Carry the classification on the thrown
        // error instead of re-deriving it from the message.
        throw Object.assign(new Error(text), {
          definiteRefusal: isDefiniteAccessRefusal(reason),
        });
      } finally {
        refreshFloor.current = ++refreshVersion.current;
        if (alive.current) {
          if (written) await refreshWithStatus();
          setBusyKey(null);
        }
      }
    },
    detect: (sourceId: string) =>
      run(`detect:${sourceId}`, (traceId) =>
        detectSourceAgain([sourceId, traceId]),
      ),
    startImport: (sourceId: string) =>
      run(`import:${sourceId}`, (traceId) =>
        createImportJob(sourceId, "history", traceId),
      ),
    changeJob,
  };
}

export type ImportSourcesController = ReturnType<typeof useImportSources>;

function sameJobIdentity(left: JobWithContext, right: JobWithContext): boolean {
  return (
    left.job_id === right.job_id &&
    left.source_id === right.source_id &&
    left.user_id === right.user_id &&
    left.created_at === right.created_at &&
    left.operation === right.operation
  );
}

function assertJobReceipt(job: ImportJob, expected: ImportJob): void {
  if (
    !job ||
    !sameJobIdentity(job, expected) ||
    ![
      "pending",
      "running",
      "paused",
      "interrupted",
      "failed",
      "completed",
      "cancelled",
    ].includes(job.status) ||
    !Number.isFinite(Date.parse(job.updated_at))
  )
    throw new Error("返回的任务身份或状态不匹配，操作结果待核对。");
}

function isFinalJob(job: ImportJob): boolean {
  return job.status === "completed" || job.status === "cancelled";
}

function resolvesJobAction(action: ImportJobAction, job: ImportJob): boolean {
  return isFinalJob(job) || (action === "pause" && job.status === "paused");
}

function newerJob(known: ImportJob, incoming: ImportJob): ImportJob {
  if (isFinalJob(known)) return known;
  return compareJobUpdate(known, incoming) > 0 ? known : incoming;
}

function compareJobUpdate(left: ImportJob, right: ImportJob): number {
  const milliseconds =
    Date.parse(left.updated_at) - Date.parse(right.updated_at);
  if (milliseconds !== 0) return milliseconds;
  // Python preserves microseconds; Date.parse truncates them to milliseconds.
  const fraction = (value: string) =>
    (value.match(/\.(\d+)(?:Z|[+-]\d{2}:\d{2})$/)?.[1] ?? "").padEnd(6, "0");
  const a = fraction(left.updated_at),
    b = fraction(right.updated_at);
  return a === b ? 0 : a > b ? 1 : -1;
}

function preserveJobReceipts(
  jobs: ImportJob[],
  instanceId: string,
  receipts: Map<string, JobReceipt>,
): ImportJob[] {
  const result = new Map(jobs.map((job) => [job.job_id, job]));
  for (const [id, receipt] of receipts) {
    const polled = result.get(id);
    if (
      receipt.instanceId !== instanceId ||
      (polled && !sameJobIdentity(polled, receipt.job))
    ) {
      receipts.delete(id);
      continue;
    }
    const job =
      polled && compareJobUpdate(polled, receipt.job) > 0
        ? newerJob(receipt.job, polled)
        : receipt.job;
    receipts.set(id, { ...receipt, job });
    result.set(id, job);
  }
  return [...result.values()];
}

/** Did Core definitely refuse this, or might the write have landed?
 *
 * A 4xx (except a 408 request timeout) means the request was answered and
 * refused. A 5xx, a timeout, or a transport failure with no response means the
 * outcome is *unknown*: the write may have been applied before the answer was
 * lost. A trace that says `failed` in that case contradicts the copy and invites
 * a retry that can double-apply the write, so the surfaces that close a trace
 * share this one classification.
 */
export function isDefiniteJobRejection(reason: unknown): boolean {
  if (!reason || typeof reason !== "object" || !("response" in reason))
    return false;
  const response = reason.response;
  if (!response || typeof response !== "object" || !("status" in response))
    return false;
  return (
    typeof response.status === "number" &&
    response.status >= 400 &&
    response.status < 500 &&
    response.status !== 408
  );
}

/** Did Core definitely refuse this, or might the write have landed?
 *
 * A 4xx means the request was answered and refused. A 5xx, a timeout, or a
 * transport failure without a response means the outcome is *unknown*: the
 * write may have been applied before the answer was lost. The UI must not tell
 * the user "未能开启读取" in that case — that is a factual claim it cannot make,
 * and it invites a retry that could double-apply. Exported so the surfaces that
 * close a trace can pick the matching outcome.
 */
export function isDefiniteAccessRefusal(reason: unknown): boolean {
  // A wrapper that already classified the transport error wins: by the time a
  // caller sees it the original axios error (and its status) is gone.
  if (reason && typeof reason === "object" && "definiteRefusal" in reason)
    return (reason as { definiteRefusal?: unknown }).definiteRefusal === true;
  return isDefiniteJobRejection(reason);
}

function jobActionMessage(reason: unknown): string {
  if (apiErrorCode(reason) === "job_not_resumable")
    return "本次任务已结束，不能继续导入。请刷新最后已确认状态。";
  return toMessage(reason);
}

function assertConnectionReceipt(
  receipt: ConnectSourcesResponse,
  requestId: string,
  sourceIds: string[],
) {
  const accepted = receipt?.accepted_source_ids;
  const failed = receipt?.failed_sources;
  if (
    receipt?.request_id !== requestId ||
    !Array.isArray(accepted) ||
    !failed ||
    typeof failed !== "object" ||
    Array.isArray(failed) ||
    !Array.isArray(receipt.jobs)
  ) {
    throw new Error(RECOVERY_SCOPE_ERROR);
  }
  const accounted = [...accepted, ...Object.keys(failed)];
  if (
    accounted.length !== sourceIds.length ||
    new Set(accounted).size !== accounted.length ||
    accounted.some((id) => !sourceIds.includes(id)) ||
    Object.values(failed).some((code) => typeof code !== "string") ||
    receipt.jobs.some((job) => !accepted.includes(job.source_id))
  ) {
    throw new Error(RECOVERY_SCOPE_ERROR);
  }
}

function mergeJobs(current: ImportJob[], incoming: ImportJob[]): ImportJob[] {
  const jobs = new Map(current.map((job) => [job.job_id, job]));
  for (const job of incoming) {
    const previous = jobs.get(job.job_id);
    if (!previous || previous.updated_at <= job.updated_at)
      jobs.set(job.job_id, job);
  }
  return [...jobs.values()];
}

function toMessage(reason: unknown): string {
  const code = apiErrorCode(reason);
  if (code) {
    const labels: Record<string, string> = {
      source_unavailable: "来源目录暂时不可读取，请核对位置和权限后重试。",
      source_not_available: "来源目录暂时不可读取，请核对位置和权限后重试。",
      source_not_authorized: "此来源尚未授权，请先确认读取范围。",
      environment_override_confirmation_required: "自定义来源路径尚未确认。",
      imports_disabled: "当前环境未启用历史导入。",
      grant_required: "请先重新确认此来源的读取权限。",
      source_identity_changed: "来源位置或目录身份已变化，请重新确认。",
      source_permission_changed: "来源权限已变化，请刷新后重新确认。",
      sync_permission_required: "持续同步尚未授权，请在来源行单独开启。",
      source_confirmation_required: "来源身份尚未核对，请重新检测并确认。",
      source_intent_conflict: "连接请求与原确认范围不同，请重新核对回执。",
      import_upload_scope_mismatch:
        "文件回执与本次来源范围不一致，请重新核对已确认的文件和目录。",
      source_intent_job_missing:
        "原连接任务暂不可读取，请核对回执，不要重复授权。",
      source_import_busy: "此来源已有导入任务，请先核对任务状态。",
    };
    return labels[code] ?? "来源操作未完成，请核对当前状态后重试。";
  }
  return reason instanceof Error ? reason.message : "操作失败，请重试。";
}

function toRefreshMessage(reason: unknown): string {
  const message = toMessage(reason);
  if (message.includes("timeout")) {
    return "状态刷新超时，当前显示最后已确认状态。后台状态尚未核对，请刷新。";
  }
  return "暂时无法刷新状态，当前显示最后已确认状态。后台状态尚未核对，请刷新。";
}
