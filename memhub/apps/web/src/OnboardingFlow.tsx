import type {
  ConnectSourcesResponse,
  ImportJob,
  OnboardingState,
  SourceResponse,
} from "@one-memory/contracts";
import { Check, X } from "lucide-react";
import {
  type CSSProperties,
  type ReactNode,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";

import { completeOnboarding } from "./api";
import { AppMark } from "./app-mark";
import { saveConnectionPreferences } from "./connection-preferences";
import {
  defaultHostSourceIds,
  FirstImportReview,
  type ImportConnectionDraft,
} from "./FirstImportReview";
import {
  enableHostConnection,
  hostCapabilities,
  hostWriteReason,
} from "./host-state";
import { OnboardingModelController } from "./OnboardingModelController";
import { useOnboardingTranslation } from "./onboarding-language";
import { R6ColdStartFrame } from "./R6ColdStartFrame";
import { R6Button } from "./R6Controls";
import { traceUiAction } from "./ui-trace";
import type { ImportSourcesController } from "./use-import-sources";
import { isDefiniteRefusal } from "./write-outcome";
import "./cold-start.css";

export type OnboardingDestination = "memory" | "sources" | "settings";

// Cold start neither reads nor writes installation intent.
export const PLUGIN_CONSENT_KEY = "one-memory.consent.plugins";

const SCOPE_CHANGED =
  "来源位置、身份或状态已变化，请重新选择并核对。尚未提交本次授权。";
type LocationCheck = "idle" | "checking" | "ready" | "failed";

function defaultConnectionDraft(
  sources: SourceResponse[],
  sourceIds: readonly string[],
): ImportConnectionDraft {
  return {
    hostSourceIds: defaultHostSourceIds(sources, sourceIds),
  };
}

export function OnboardingFlow({
  state,
  imports,
  onFinish,
}: {
  state: OnboardingState;
  imports: ImportSourcesController;
  onFinish(destination: OnboardingDestination): void;
}) {
  const { text, t } = useOnboardingTranslation();
  const [sources, setSources] = useState(imports.sources);
  const sourceGeneration = useRef({ value: imports.sources, epoch: 0 });
  if (sourceGeneration.current.value !== imports.sources) {
    sourceGeneration.current = {
      value: imports.sources,
      epoch: sourceGeneration.current.epoch + 1,
    };
  }
  const [locationCheck, setLocationCheck] = useState<LocationCheck>("checking");
  const [selected, setSelected] = useState<SourceResponse[]>([]);
  const [custom, setCustom] = useState<SourceResponse | null>(null);
  const [busy, setBusy] = useState<
    "checking" | "submitting" | "leaving" | null
  >(null);
  const [error, setError] = useState<string | null>(null);
  const [itemErrors, setItemErrors] = useState<Record<string, string>>({});
  const [exitDestination, setExitDestination] =
    useState<OnboardingDestination | null>(null);
  const [completionFailed, setCompletionFailed] = useState(false);
  const [accepted, setAccepted] = useState<ConnectSourcesResponse | null>(null);
  const [startingImport, setStartingImport] = useState<SourceResponse[] | null>(
    null,
  );
  const [modelStep, setModelStep] = useState(false);
  const [visitedModel, setVisitedModel] = useState(false);
  const [modelSourceIds, setModelSourceIds] = useState<string[]>([]);
  const [connectionDraft, setConnectionDraft] = useState<ImportConnectionDraft>(
    { hostSourceIds: [] },
  );
  const [connectionTouched, setConnectionTouched] = useState(false);
  const [connectionAvailable, setConnectionAvailable] = useState(false);
  const [hostConnectionAvailable, setHostConnectionAvailable] = useState(true);
  const [connectionApplication, setConnectionApplication] = useState<
    "idle" | "applying" | "applied" | "partial" | "failed"
  >("idle");
  const [applyTick, setApplyTick] = useState(0);
  const [connectionNote, setConnectionNote] = useState<
    string | { success: number; failures: [string, string | null][] }
  >("");
  const renderedConnectionNote =
    typeof connectionNote === "string"
      ? text(connectionNote)
      : t("onboardingConnectionPartial", {
          success: connectionNote.success,
          failed: connectionNote.failures.length,
          names: connectionNote.failures
            .map(([name, reason]) =>
              reason ? `${name}（${text(reason)}）` : name,
            )
            .join("、"),
        });
  const flight = useRef(false);
  const connectionAttempt = useRef<string | null>(null);
  const connectionQueue = useRef(Promise.resolve());
  const connectionQueueDepth = useRef(0);
  const connectionQueueFailed = useRef(false);
  const appliedConnections = useRef({
    host: new Set<string>(),
  });
  const pendingConnections = useRef({
    host: new Set<string>(),
  });
  const connectSent = useRef(false);
  const alive = useRef(true);
  const finished = useRef(false);
  const scopeVersion = useRef(0);
  const initialDiscovery = useRef(false);
  const consideredSources = useRef(new Set<string>());
  const heading = useRef<HTMLHeadingElement>(null);
  const reviewingImport =
    visitedModel ||
    accepted !== null ||
    startingImport !== null ||
    state.status === "importing";
  const refreshImports = imports.refresh;
  const checkLocationsRef = useRef<() => Promise<void>>(async () => {});
  useEffect(() => {
    if (!reviewingImport) return;
    const api = window.oneMemory;
    setConnectionAvailable(hostCapabilities().permission);
    if (!api?.getConnectionSetupCapability) return;
    let active = true;
    void api.getConnectionSetupCapability().then(
      (capability) => {
        if (!active) return;
        if (capability.reason === "noncanonical_data_dir") {
          setConnectionAvailable(true);
          setHostConnectionAvailable(false);
          setConnectionNote("测试实例不接入应用");
          return;
        }
        // An install made with `ditto` runs from wherever it was copied to,
        // which is never /Applications. The application detail screen already
        // said so; cold start disabled the connection and left the reason
        // unsaid, so the first launch after such an install looked broken.
        if (capability.reason === "app_not_in_applications") {
          setConnectionAvailable(capability.available);
          setHostConnectionAvailable(false);
          setConnectionNote(
            "请先把 MemHub 拖到“应用程序”并从那里重新打开，才能接入应用。",
          );
          return;
        }
        setConnectionAvailable(capability.available);
        setHostConnectionAvailable(capability.available);
      },
      () => {
        if (active) setConnectionAvailable(false);
      },
    );
    return () => {
      active = false;
    };
  }, [reviewingImport]);
  useEffect(() => {
    if (!reviewingImport || connectionTouched) return;
    const sourceIds =
      accepted?.accepted_source_ids ??
      startingImport?.map((source) => source.source_id) ??
      (modelSourceIds.length > 0 ? modelSourceIds : state.selected_source_ids);
    const reviewSources = startingImport
      ? imports.sources.map((source) =>
          startingImport.some((item) => item.source_id === source.source_id)
            ? { ...source, authorized: true }
            : source,
        )
      : imports.sources;
    const next = defaultConnectionDraft(reviewSources, sourceIds);
    if (!next.hostSourceIds.length) return;
    saveConnectionPreferences({
      syncSourceIds: sourceIds,
      hostSourceIds: [...next.hostSourceIds],
    });
  }, [
    reviewingImport,
    connectionTouched,
    accepted,
    startingImport,
    imports.sources,
    modelSourceIds,
    state.selected_source_ids,
  ]);
  useEffect(() => {
    if (!applyTick) return;
    const selection = {
      hostSourceIds: [...connectionDraft.hostSourceIds],
    };
    if (!selection.hostSourceIds.length) return;
    const key = `${applyTick}:${JSON.stringify(selection)}`;
    if (connectionAttempt.current === key) return;
    connectionAttempt.current = key;
    const canInsert = hostCapabilities().permission;
    const operations = [
      ...selection.hostSourceIds.map((id) => ({ id, kind: "host" as const })),
    ].filter((operation) => {
      if (
        appliedConnections.current[operation.kind].has(operation.id) ||
        pendingConnections.current[operation.kind].has(operation.id)
      )
        return false;
      if (operation.kind === "host" && !canInsert) return false;
      return true;
    });
    if (!operations.length) return;
    for (const operation of operations)
      pendingConnections.current[operation.kind].add(operation.id);
    connectionQueueDepth.current += 1;
    setConnectionApplication("applying");
    setConnectionNote("");
    const names = new Map(
      sources.map((item) => [item.source_id, item.display_name]),
    );
    const label = (id: string) => names.get(id) ?? id;
    connectionQueue.current = connectionQueue.current
      .then(async () => {
        let succeeded = 0;
        const failed = new Map<string, string | null>();
        const apps = new Set<string>();
        for (const operation of operations) {
          apps.add(label(operation.id));
          if (appliedConnections.current[operation.kind].has(operation.id)) {
            succeeded += 1;
            continue;
          }
          try {
            await enableHostConnection(operation.id);
            appliedConnections.current[operation.kind].add(operation.id);
            succeeded += 1;
          } catch (error) {
            const name = label(operation.id);
            failed.set(name, failed.get(name) ?? hostWriteReason(error));
          }
        }
        if (failed.size && alive.current) {
          connectionQueueFailed.current = true;
          setConnectionNote({
            success: apps.size - failed.size,
            failures: [...failed],
          });
        }
        try {
          await refreshImports();
        } catch {
          // Home and settings re-read the authoritative host/source state.
        }
        if (!succeeded && failed.size)
          throw new Error("connection_setup_failed");
      })
      .catch(() => {
        connectionQueueFailed.current = true;
        if (alive.current)
          setConnectionNote(
            (current) =>
              current ||
              "部分应用未能完成接入，已成功的设置会保留，可进入应用后单独处理。",
          );
      })
      .finally(() => {
        for (const operation of operations)
          pendingConnections.current[operation.kind].delete(operation.id);
        connectionQueueDepth.current -= 1;
        if (alive.current && connectionQueueDepth.current === 0)
          setConnectionApplication(
            connectionQueueFailed.current ? "partial" : "applied",
          );
      });
  }, [applyTick, connectionDraft, refreshImports, sources]);
  const appSources = sources.filter(isAppSource);
  const readableApps = appSources.filter(canRead);
  const listedApps = appSources.filter((item) => !isMissingFromScan(item));
  const visibleApps =
    locationCheck === "checking" ? readableApps.slice(0, 1) : listedApps;
  const allSelected =
    readableApps.length > 0 &&
    readableApps.every((item) =>
      selected.some((chosen) => chosen.source_id === item.source_id),
    );
  const locked =
    busy !== null || exitDestination !== null || state.status === "importing";

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  useEffect(() => {
    setSources(imports.sources);
  }, [imports.sources]);

  const invalidateScope = useCallback(() => {
    scopeVersion.current += 1;
    // An identity change requires a fresh user choice, not another default.
    for (const item of sources) consideredSources.current.add(item.source_id);
    setSelected([]);
    setCustom(null);
    setItemErrors({});
    setError(
      connectSent.current
        ? "来源位置、身份或状态已变化，正在等待本次提交回执。不会自动扩大已提交范围。"
        : SCOPE_CHANGED,
    );
  }, [sources]);

  useEffect(() => {
    if (exitDestination !== null || reviewingImport) return;
    if (
      !scopeMatches(selected, sources) ||
      (custom !== null && !scopeMatches([custom], sources))
    )
      invalidateScope();
  }, [
    sources,
    selected,
    custom,
    exitDestination,
    reviewingImport,
    invalidateScope,
  ]);

  useLayoutEffect(() => {
    heading.current?.focus({ preventScroll: true });
  }, []);
  useEffect(() => {
    const restoreFirstStep = () => {
      if (!reviewingImport && document.activeElement === document.body) {
        heading.current?.focus({ preventScroll: true });
      }
    };
    window.addEventListener("focus", restoreFirstStep);
    document.addEventListener("visibilitychange", restoreFirstStep);
    return () => {
      window.removeEventListener("focus", restoreFirstStep);
      document.removeEventListener("visibilitychange", restoreFirstStep);
    };
  }, [reviewingImport]);

  useEffect(() => {
    if (initialDiscovery.current || reviewingImport) return;
    initialDiscovery.current = true;
    void checkLocationsRef.current();
  }, [reviewingImport]);

  useEffect(() => {
    if (
      locationCheck !== "ready" ||
      locked ||
      reviewingImport ||
      flight.current
    )
      return;
    if (!scopeMatches(selected, sources)) return;
    const discovered = sources.filter(
      (item) =>
        isAppSource(item) &&
        canRead(item) &&
        item.candidate_kind === "default" &&
        (item.authorized || (item.grant_revision ?? 0) === 0) &&
        !consideredSources.current.has(item.source_id),
    );
    if (!discovered.length) return;
    for (const item of discovered)
      consideredSources.current.add(item.source_id);
    setSelected((current) => [...current, ...discovered.map(copySource)]);
  }, [sources, selected, locationCheck, locked, reviewingImport]);

  const finishOnce = useCallback(
    (destination: OnboardingDestination) => {
      if (!alive.current || finished.current) return;
      finished.current = true;
      onFinish(destination);
    },
    [onFinish],
  );

  const persistFinish = useCallback(
    async (destination: OnboardingDestination) => {
      setExitDestination(destination);
      setCompletionFailed(false);
      setError(null);
      // Completing setup records durable onboarding state, so it is a real
      // write and gets its own chain like every other non-GET.
      const trace = traceUiAction("onboarding", "submit");
      try {
        await completeOnboarding(trace.traceId);
        trace.done("succeeded");
      } catch (reason) {
        const refused = isDefiniteRefusal(reason);
        trace.done(refused ? "failed" : "unknown", {
          reason: refused
            ? "onboarding_complete_failed"
            : "onboarding_complete_result_unknown",
        });
        if (alive.current) {
          setCompletionFailed(true);
          setModelStep(false);
          // A definite refusal means it was not saved; anything else (timeout,
          // 5xx, no response) leaves the outcome unknown, and the copy must not
          // assert a failure the UI cannot prove.
          setError(
            refused
              ? "首次设置的完成状态未能保存。已确认的导入回执不受影响；下次启动可能再次出现此页。"
              : "首次设置的完成状态尚未确认。已确认的导入回执不受影响；下次启动可能再次出现此页。",
          );
        }
        return;
      }
      finishOnce(destination);
    },
    [finishOnce],
  );

  const leave = useCallback(
    async (destination: OnboardingDestination) => {
      if (flight.current || finished.current) return;
      flight.current = true;
      setBusy("leaving");
      await persistFinish(destination);
      flight.current = false;
      if (alive.current) setBusy(null);
    },
    [persistFinish],
  );

  async function checkLocations() {
    if (flight.current || locked) return;
    flight.current = true;
    scopeVersion.current += 1;
    setBusy("checking");
    setLocationCheck("checking");
    setCustom(null);
    setError(null);
    setItemErrors({});
    try {
      // refresh returns root metadata; imports.detect enumerates registered source files.
      const startedAt = performance.now();
      const current = await imports.refresh();
      const remaining = window.oneMemory
        ? Math.max(0, 850 - (performance.now() - startedAt))
        : 0;
      if (remaining)
        await new Promise((resolve) => window.setTimeout(resolve, remaining));
      if (!alive.current) return;
      setSources(current.sources);
      setLocationCheck("ready");
      if (!scopeMatches(selected, current.sources)) invalidateScope();
    } catch {
      if (!alive.current) return;
      setLocationCheck("failed");
      setError("位置检测未完成，尚未读取历史。可以重新检测，或先进入工作台。");
    } finally {
      flight.current = false;
      if (alive.current) setBusy(null);
    }
  }
  checkLocationsRef.current = checkLocations;

  function toggleSource(source: SourceResponse, checked: boolean) {
    if (
      flight.current ||
      locked ||
      locationCheck !== "ready" ||
      !isAppSource(source) ||
      !canRead(source)
    )
      return;
    scopeVersion.current += 1;
    consideredSources.current.add(source.source_id);
    setError(null);
    setItemErrors({});
    if (!checked) {
      setSelected((current) =>
        current.filter((item) => item.source_id !== source.source_id),
      );
    } else if (source.candidate_kind !== "default") {
      setCustom(copySource(source));
    } else {
      setSelected((current) => [...current, copySource(source)]);
    }
  }

  function selectAllSources() {
    if (flight.current || locked || locationCheck !== "ready") return;
    scopeVersion.current += 1;
    setError(null);
    setItemErrors({});
    for (const item of readableApps)
      consideredSources.current.add(item.source_id);
    setSelected(readableApps.map(copySource));
  }

  const closeCustom = useCallback(() => setCustom(null), []);

  async function startImport() {
    if (
      flight.current ||
      locked ||
      locationCheck !== "ready" ||
      !selected.length
    )
      return;
    if (!scopeMatches(selected, sources)) {
      invalidateScope();
      return;
    }
    await submitImport(selected.map(copySource));
  }

  async function submitImport(approved: SourceResponse[]) {
    flight.current = true;
    if (window.oneMemory) setStartingImport(approved);
    const version = scopeVersion.current;
    const retryFailed = Object.keys(itemErrors).length > 0;
    setBusy("submitting");
    setError(null);
    setItemErrors({});
    try {
      let current: SourceResponse[];
      try {
        current = (await imports.refresh()).sources;
      } catch {
        if (alive.current)
          setError("暂时无法核对来源，尚未提交授权。选择已保留，可以重试。");
        return;
      }
      if (!alive.current) return;
      if (scopeVersion.current !== version) {
        invalidateScope();
        return;
      }
      setSources(current);
      if (!scopeMatches(approved, current)) {
        invalidateScope();
        return;
      }
      connectSent.current = true;
      const result = await imports.connectForOnboarding(
        approved.map((item) => item.source_id),
        approved,
        ...(retryFailed ? [{ retryFailed: true }] : []),
      );
      if (!alive.current) return;
      setItemErrors(result.failed_sources);
      if (result.accepted_source_ids.length === 0) {
        setStartingImport(null);
        setError(
          "本次没有来源获准导入。请核对下方原因后重试，或到应用页查看状态。",
        );
        return;
      }
      // Keep the accepted intent and job IDs while the user inspects early originals.
      setAccepted(result);
      setStartingImport(null);
      setError(null);
    } catch {
      if (alive.current) {
        setStartingImport(null);
        setError(
          "开始导入的结果尚未确认，选择已保留。可以重试，或进入应用页核对。",
        );
      }
    } finally {
      connectSent.current = false;
      flight.current = false;
      if (alive.current) setBusy(null);
    }
  }

  const count = selected.length;
  // D51 item 3: only a *confirmed* snapshot may show a number. A failed
  // detection must read 未完成 even when sources are listed — otherwise the
  // number branch answered first and the summary claimed a count while the
  // same screen said "检测未完成". The checking branch keeps showing the one
  // optimistically listed row, which is the intended first-paint state.
  const summaryCount =
    locationCheck === "ready"
      ? String(count)
      : locationCheck === "failed"
        ? "未完成"
        : visibleApps.length > 0
          ? String(visibleApps.length)
          : "核对中";
  if (reviewingImport) {
    const sourceIds =
      accepted?.accepted_source_ids ??
      startingImport?.map((source) => source.source_id) ??
      state.selected_source_ids;
    const reviewJobs = accepted
      ? accepted.jobs.map((saved) =>
          newerJobReceipt(
            saved,
            imports.jobs.find((job) => job.job_id === saved.job_id),
          ),
        )
      : imports.jobs.filter((job) => sourceIds.includes(job.source_id));
    const reviewSources = startingImport
      ? imports.sources.map((source) =>
          startingImport.some((item) => item.source_id === source.source_id)
            ? { ...source, authorized: true }
            : source,
        )
      : imports.sources;
    const defaultDraft = defaultConnectionDraft(reviewSources, sourceIds);
    const activeDraft =
      connectionTouched || connectionDraft.hostSourceIds.length > 0
        ? connectionDraft
        : defaultDraft;
    return (
      <>
        <div
          hidden={modelStep}
          data-import-submission={startingImport ? "pending" : "settled"}
        >
          <FirstImportReview
            connectionDraft={activeDraft}
            onConnectionDraftChange={(draft) => {
              setConnectionTouched(true);
              setConnectionDraft(draft);
              saveConnectionPreferences({
                syncSourceIds: sourceIds,
                hostSourceIds: [...draft.hostSourceIds],
              });
            }}
            connectionExecutionAvailable={connectionAvailable}
            hostConnectionAvailable={hostConnectionAvailable}
            connectionNote={renderedConnectionNote}
            acceptedJobIds={accepted?.jobs.map((job) => job.job_id)}
            onChangeJob={imports.changeJob}
            sourceIds={sourceIds}
            sources={reviewSources}
            jobs={reviewJobs}
            failedSources={accepted?.failed_sources}
            sourceSnapshot={imports.sourceSnapshot}
            busy={busy === "leaving"}
            error={error && text(error)}
            onContinue={(destination) => {
              if (busy === "leaving") return;
              const selection = {
                hostSourceIds: hostConnectionAvailable
                  ? [...activeDraft.hostSourceIds]
                  : [],
              };
              saveConnectionPreferences({
                syncSourceIds: sourceIds,
                hostSourceIds: selection.hostSourceIds,
              });
              setConnectionDraft(selection);
              setApplyTick((value) => value + 1);
              if (destination === "settings") {
                setModelSourceIds([...sourceIds]);
                setVisitedModel(true);
                setModelStep(true);
              } else void leave(destination);
            }}
            recovery={
              completionFailed && exitDestination
                ? {
                    destination: exitDestination,
                    onSkip: () => finishOnce(exitDestination),
                  }
                : undefined
            }
          />
        </div>
        {visitedModel && (
          <div hidden={!modelStep}>
            <OnboardingModelController
              connectionAlreadyApplied={
                connectionApplication === "applied" ||
                connectionApplication === "partial"
              }
              onEnabled={() => void leave("sources")}
              sourceNames={sourceIds.map(
                (id) =>
                  imports.sources.find((source) => source.source_id === id)
                    ?.display_name ?? id,
              )}
              importedConversationCount={imports.sources
                .filter((source) => sourceIds.includes(source.source_id))
                .reduce(
                  (total, source) => total + source.imported_conversations,
                  0,
                )}
              onBack={() => setModelStep(false)}
              onSkip={() => void leave("sources")}
            />
          </div>
        )}
      </>
    );
  }

  // 冷启动直接进入来源选择：引导欢迎页已按产品要求随教学链路一并下线，
  // 仅保留保存失败时的恢复出口。
  if (completionFailed && exitDestination)
    return (
      <R6ColdStartFrame
        step={1}
        title={text("把熟悉的背景，带到这里")}
        eyebrow={text("你的记忆，从这里接上")}
        footer={
          <>
            <R6Button
              variant="quiet"
              disabled={busy !== null}
              onClick={() => finishOnce(exitDestination)}
            >
              {text(
                exitDestination === "sources" ? "仍进入应用页" : "仍进入工作台",
              )}
            </R6Button>
            <R6Button
              variant="primary"
              disabled={busy !== null}
              onClick={() => void leave(exitDestination)}
            >
              {text("重试保存并进入")}
            </R6Button>
          </>
        }
      >
        <p className="onboarding-r6-error" role="alert">
          {error && text(error)}
        </p>
      </R6ColdStartFrame>
    );

  return (
    <R6ColdStartFrame
      step={1}
      title={text("把熟悉的背景，带到这里")}
      eyebrow={text("你的记忆，从这里接上")}
      footer={
        <>
          <R6Button
            variant="quiet"
            disabled={locked}
            onClick={() => void leave("sources")}
          >
            {text("稍后添加，先进入")}
          </R6Button>
          {error && (
            <p className="onboarding-r6-error" role="alert">
              {error && text(error)}
            </p>
          )}
          <R6Button
            className="onboarding-r6-submit"
            variant="primary"
            disabled={locked || count === 0}
            onClick={() => void startImport()}
          >
            {text(busy === "submitting" ? "正在开始导入…" : "开始导入")}
          </R6Button>
        </>
      }
    >
      <div className="onboarding-r6-discovery">
        <section
          className="onboarding-r6-apps"
          aria-labelledby="cold-start-phase"
        >
          <div className="onboarding-r6-intro">
            <p>
              {text("不用从头介绍自己。从常用的 App 开始，把已有背景带过来。")}
            </p>
            <p>{text("确认前只检查应用信息，不读取对话，也不安装连接。")}</p>
          </div>
          <div className="onboarding-r6-list-heading">
            <h2 id="cold-start-phase" ref={heading} tabIndex={-1}>
              {text("本机 AI 应用")}
            </h2>
            <button
              type="button"
              className="onboarding-r6-quiet"
              aria-label={text("重新检测位置")}
              disabled={locked}
              onClick={() => void checkLocations()}
            >
              {text(busy === "checking" ? "检测中…" : "重新检测")}
            </button>
          </div>
          <SourceList
            className="onboarding-r6-list"
            aria-label={text("候选应用")}
            aria-busy={busy === "checking"}
            count={visibleApps.length}
            status={
              locationCheck === "checking"
                ? text("正在检测其他应用…")
                : locationCheck === "failed"
                  ? text("检测未完成")
                  : undefined
            }
            selection={
              <button
                type="button"
                className="onboarding-r6-quiet"
                disabled={locked || allSelected || readableApps.length === 0}
                onClick={selectAllSources}
              >
                {text(allSelected ? "已全选" : "全选")}
              </button>
            }
          >
            {locationCheck === "checking" && visibleApps[0] && (
              <li className="onboarding-r6-row onboarding-r6-row-scanning">
                <label>
                  <input
                    type="checkbox"
                    aria-label={t("onboardingImportHistory", {
                      name: visibleApps[0].display_name,
                    })}
                    checked
                    disabled
                    readOnly
                  />
                  <R6SourceIcon id={visibleApps[0].source_id} />
                  <span className="onboarding-r6-row-info">
                    <strong>{visibleApps[0].display_name}</strong>
                    <small>{text("正在核对本机保存位置")}</small>
                  </span>
                  <span className="onboarding-r6-badge" data-found="true">
                    <i aria-hidden="true" />
                    {text("已发现")}
                  </span>
                </label>
              </li>
            )}
            {locationCheck !== "checking" &&
              visibleApps.map((item, index) => {
                const checked = selected.some(
                  (chosen) => chosen.source_id === item.source_id,
                );
                const note =
                  locationCheck === "ready" ? locationNote(item) : null;
                const translatedNote = note ? text(note) : null;
                return (
                  <li
                    className={`onboarding-r6-row${checked ? " is-selected" : ""}`}
                    key={item.source_id}
                    style={
                      { "--source-order": Math.min(index, 8) } as CSSProperties
                    }
                  >
                    <label>
                      <input
                        type="checkbox"
                        aria-label={t("onboardingImportHistory", {
                          name: item.display_name,
                        })}
                        checked={checked}
                        disabled={
                          locked || locationCheck !== "ready" || !canRead(item)
                        }
                        onChange={(event) =>
                          toggleSource(item, event.target.checked)
                        }
                      />
                      <R6SourceIcon id={item.source_id} />
                      <span className="onboarding-r6-row-info">
                        <strong>{item.display_name}</strong>
                        <small title={translatedNote ?? item.candidate_path}>
                          {item.candidate_path || text("位置待核对")} ·{" "}
                          {text(pathKind(item))}
                        </small>
                        {itemErrors[item.source_id] && (
                          <small className="onboarding-r6-error">
                            {failureLabel(itemErrors[item.source_id], text, t)}
                          </small>
                        )}
                      </span>
                      <span
                        className="onboarding-r6-badge"
                        data-found={
                          locationCheck === "ready" &&
                          item.location_state === "found"
                        }
                        title={translatedNote ?? undefined}
                      >
                        <i aria-hidden="true" />
                        {text(locationLabel(item, locationCheck))}
                      </span>
                    </label>
                  </li>
                );
              })}
            {locationCheck === "checking" &&
              ["first", "second", "third"]
                .slice(visibleApps[0] ? 1 : 0)
                .map((slot) => (
                  <li
                    className="onboarding-r6-skeleton"
                    key={`pending-${slot}`}
                    aria-hidden="true"
                  >
                    <i />
                    <span />
                    <small />
                  </li>
                ))}
            {!visibleApps.length && busy !== "checking" && (
              <li className="onboarding-r6-empty">
                {locationCheck === "ready"
                  ? text("当前没有候选来源。可以稍后在应用页添加历史包。")
                  : text("候选来源尚未加载。可以检测位置，或先进入工作台。")}
              </li>
            )}
          </SourceList>
        </section>
        <aside className="onboarding-r6-summary" aria-label={text("本次导入")}>
          <p>{text("本次导入")}</p>
          <p
            className="onboarding-r6-count"
            aria-live="polite"
            data-unknown={locationCheck !== "ready"}
          >
            {text(summaryCount)}
          </p>
          <p>{text("个 App 的历史对话")}</p>
          <h2>
            {text("把零散的会话，")}
            <br />
            {text("汇成完整的记忆")}
          </h2>
          <p className="onboarding-r6-signoff">
            {text("少一些重复交代，多一些理解。")}
          </p>
        </aside>
      </div>
      {custom && (
        <CustomLocationDialog
          source={custom}
          onCancel={closeCustom}
          onConfirm={() => {
            if (!scopeMatches([custom], sources)) {
              invalidateScope();
              return;
            }
            setSelected((current) => [...current, copySource(custom)]);
            setCustom(null);
          }}
        />
      )}
    </R6ColdStartFrame>
  );
}

function SourceList({
  children,
  count,
  className,
  "aria-label": label,
  "aria-busy": busy,
  status,
  selection,
}: {
  children: ReactNode;
  count: number;
  className: string;
  "aria-label": string;
  "aria-busy"?: boolean;
  status?: string;
  selection: ReactNode;
}) {
  const { t } = useOnboardingTranslation();
  const list = useRef<HTMLUListElement>(null);
  const [more, setMore] = useState(0);
  const measure = useCallback(() => {
    const element = list.current;
    if (!element) return;
    const bottom = element.getBoundingClientRect().bottom;
    setMore(
      Array.from(element.children).filter(
        (child) => child.getBoundingClientRect().bottom > bottom + 1,
      ).length,
    );
  }, []);
  // biome-ignore lint/correctness/useExhaustiveDependencies: The list's children are measured again when its item count changes.
  useLayoutEffect(() => {
    measure();
    const resize =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(measure);
    const mutation = new MutationObserver(measure);
    if (list.current) resize?.observe(list.current);
    if (list.current) mutation.observe(list.current, { childList: true });
    window.addEventListener("resize", measure);
    return () => {
      resize?.disconnect();
      mutation.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [measure, count]);
  return (
    <div className="onboarding-r6-list-area">
      <ul
        ref={list}
        className={className}
        aria-label={label}
        aria-busy={busy}
        data-many={count > 3}
        // biome-ignore lint/a11y/noNoninteractiveTabindex: This bounded list requires keyboard scrolling.
        tabIndex={0}
        onScroll={measure}
        onToggle={measure}
      >
        {children}
      </ul>
      <div className="onboarding-r6-list-status" role="status">
        <span data-scroll-hint={!status && more > 0 ? "true" : "false"}>
          {status ??
            (more > 0
              ? t("onboardingMoreApps", { count: more })
              : count > 3
                ? t("onboardingAllApps", { count })
                : "")}
        </span>
        {selection}
      </div>
    </div>
  );
}

function isAppSource(source: SourceResponse) {
  return (
    !source.source_id.endsWith("-export") &&
    source.source_id !== "one-memory-manual"
  );
}

function newerJobReceipt(saved: ImportJob, latest: ImportJob | undefined) {
  return latest &&
    latest.source_id === saved.source_id &&
    latest.user_id === saved.user_id &&
    Date.parse(latest.updated_at) >= Date.parse(saved.updated_at)
    ? latest
    : saved;
}

function canRead(source: SourceResponse) {
  return (
    source.available &&
    source.location_state === "found" &&
    Boolean(source.candidate_root_id?.trim()) &&
    Boolean(source.candidate_path.trim()) &&
    source.capabilities.historical_import === "implemented"
  );
}

function isMissingFromScan(source: SourceResponse) {
  return (
    source.location_state === "not_found" ||
    source.location_state === "inaccessible"
  );
}

function scopeMatches(snapshot: SourceResponse[], current: SourceResponse[]) {
  return snapshot.every((approved) =>
    current.some(
      (fresh) =>
        fresh.source_id === approved.source_id &&
        canRead(fresh) &&
        fresh.candidate_path === approved.candidate_path &&
        fresh.candidate_kind === approved.candidate_kind &&
        (fresh.candidate_revision ?? 0) ===
          (approved.candidate_revision ?? 0) &&
        fresh.candidate_root_id === approved.candidate_root_id,
    ),
  );
}

function copySource(source: SourceResponse): SourceResponse {
  return {
    ...source,
    storage_kinds: [...source.storage_kinds],
    capabilities: { ...source.capabilities },
  };
}

function pathKind(source: SourceResponse) {
  return source.candidate_kind === "default" ? "默认位置" : "自定义位置";
}

function locationLabel(source: SourceResponse, check: LocationCheck) {
  if (check === "idle") return "尚未检测";
  if (check === "checking") return "检测中";
  if (check === "failed") return "检测未完成";
  return source.location_state
    ? {
        found: "已发现",
        not_found: "未发现",
        inaccessible: "无法访问",
        invalid: "位置无效",
      }[source.location_state]
    : "需要更新 Core";
}

function locationNote(source: SourceResponse) {
  if (source.location_state === "not_found")
    return "候选位置不存在，可能尚无历史或路径不同。";
  if (source.location_state === "inaccessible")
    return "无法检查此位置，请核对目录访问权限后重试。";
  if (source.location_state === "invalid")
    return "此位置未通过安全检查，不能读取。";
  if (!source.candidate_root_id?.trim() || !source.location_state)
    return "当前 Core 未提供可确认的位置身份，需更新后再选择。";
  if (source.capabilities.historical_import !== "implemented")
    return "暂不支持历史导入";
  if (!source.available) return "当前来源不可读取，请重新检测。";
  return null;
}

function failureLabel(
  reason: string,
  text: (value: string) => string,
  t: ReturnType<typeof useOnboardingTranslation>["t"],
) {
  const labels: Record<string, string> = {
    source_unavailable: "来源目录暂时不可读取，请核对位置和权限。",
    source_not_available: "来源目录暂时不可读取，请核对位置和权限。",
    source_identity_changed: "来源目录身份已变化，请返回重新检测。",
    source_permission_changed: "来源权限已变化，请返回重新确认。",
    source_not_authorized: "此来源尚未获得读取授权。",
    environment_override_confirmation_required: "自定义来源位置需要重新确认。",
    imports_disabled: "当前 Core 未启用历史导入。",
  };
  return labels[reason]
    ? text(labels[reason])
    : t("onboardingImportRefused", { reason });
}

// S1.3 supplied app marks; WorkBuddy keeps the source geometry with flat fills.
function R6SourceIcon({ id }: { id: string }) {
  return (
    <span
      className="onboarding-r6-source-icon"
      data-app={id}
      aria-hidden="true"
    >
      <AppMark id={id} size={34} />
    </span>
  );
}

function CustomLocationDialog({
  source,
  onCancel,
  onConfirm,
}: {
  source: SourceResponse;
  onCancel(): void;
  onConfirm(): void;
}) {
  const { text, t } = useOnboardingTranslation();
  const [confirmed, setConfirmed] = useState(false);
  const panel = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const element = panel.current;
    if (!element) return;
    const before = document.activeElement;
    const fallbackFocus = element
      .closest(".onboarding-r6")
      ?.querySelector<HTMLElement>("#cold-start-phase");
    const siblings = new Map<HTMLElement, boolean>();
    let branch = element.parentElement ?? element;
    while (branch.parentElement) {
      for (const sibling of branch.parentElement.children) {
        if (!(sibling instanceof HTMLElement) || sibling === branch) continue;
        siblings.set(sibling, sibling.inert);
        sibling.inert = true;
      }
      branch = branch.parentElement;
      if (branch === document.body) break;
    }
    const controls = () =>
      Array.from(
        element.querySelectorAll<HTMLElement>(
          "button:not(:disabled), input:not(:disabled)",
        ),
      );
    const first = () => controls()[0] ?? element;
    first().focus({ preventScroll: true });
    const containFocus = (event: FocusEvent) => {
      if (event.target instanceof Node && !element.contains(event.target))
        first().focus({ preventScroll: true });
    };
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        onCancel();
      }
      if (event.key !== "Tab") return;
      const items = controls();
      const head = items[0];
      const tail = items.at(-1);
      if (
        !head ||
        (event.shiftKey &&
          (document.activeElement === head ||
            document.activeElement === element))
      ) {
        event.preventDefault();
        (tail ?? element).focus();
      } else if (!event.shiftKey && document.activeElement === tail) {
        event.preventDefault();
        head.focus();
      }
    };
    document.addEventListener("focusin", containFocus);
    element.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("focusin", containFocus);
      element.removeEventListener("keydown", handleKey);
      for (const [sibling, inert] of siblings) sibling.inert = inert;
      if (
        before instanceof HTMLElement &&
        before.isConnected &&
        !before.matches(":disabled")
      )
        before.focus({ preventScroll: true });
      else if (fallbackFocus?.isConnected)
        fallbackFocus.focus({ preventScroll: true });
    };
  }, [onCancel]);

  return (
    <div className="cold-start-cover">
      <div
        ref={panel}
        className="cold-start-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="cold-start-custom-title"
        aria-describedby="cold-start-custom-description"
        tabIndex={-1}
      >
        <header>
          <h2 id="cold-start-custom-title">{text("确认自定义位置")}</h2>
          <button
            type="button"
            className="cold-start-icon-button"
            aria-label={text("关闭位置确认")}
            title={text("关闭位置确认")}
            onClick={onCancel}
          >
            <X size={18} aria-hidden="true" />
          </button>
        </header>
        <div className="cold-start-dialog-body">
          <p id="cold-start-custom-description">
            {text(
              "此位置来自当前配置，并非应用默认目录。确认位置不会开始读取历史。",
            )}
          </p>
          <strong>{source.display_name}</strong>
          <code>{source.candidate_path}</code>
          <label className="cold-start-read-consent">
            <input
              type="checkbox"
              checked={confirmed}
              onChange={(event) => setConfirmed(event.target.checked)}
            />
            <span>
              {t("onboardingConfirmHistory", { name: source.display_name })}
            </span>
          </label>
        </div>
        <footer>
          <button
            type="button"
            className="cold-start-secondary"
            onClick={onCancel}
          >
            {text("取消")}
          </button>
          <button
            type="button"
            disabled={!confirmed}
            onClick={() => {
              if (confirmed) onConfirm();
            }}
          >
            <Check size={16} aria-hidden="true" />
            {text("确认此位置")}
          </button>
        </footer>
      </div>
    </div>
  );
}
