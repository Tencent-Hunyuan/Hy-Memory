import type {
  EvolveSchedule,
  MemoryModeChange,
  MemoryModePlan,
  MemoryModeState,
  MemoryPipelineMode,
} from "@one-memory/contracts";
import { legacyMemoryModePlan } from "@one-memory/operations";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import {
  apiErrorCode,
  getEvolveSchedule,
  getMemoryMode,
  getMemoryProcessing,
  memoryModePlanUnsupported,
  planMemoryMode,
  setEvolveSchedule,
  setMemoryMode,
  startEvolve,
} from "./api";
import {
  commitConfirmedMemoryMode,
  issueConfirmedModeTicket,
} from "./confirmed-memory-mode";
import { commitEvolveRun, useEvolveState } from "./evolve-state";
import {
  BLOCKER_COPY,
  MemoryModeConfirmDialog,
  MODE_COPY,
  MODE_TIER,
  modeDisplayName,
} from "./memory-mode-ui";
import { OptionMenu } from "./OptionMenu";
import { useOnboardingTranslation } from "./onboarding-language";
import { R6Button } from "./R6Controls";
import { R6StateRecovery } from "./R6StateRecovery";
import { UltraEvolveScheduleFields } from "./UltraEvolveScheduleFields";

export function MemoryModeSettings({
  hasActiveModel,
  modelStateKnown = true,
  onConfigureModel,
  onModeChange,
}: {
  hasActiveModel: boolean;
  modelStateKnown?: boolean;
  onConfigureModel(message?: string): void;
  onModeChange?(mode: MemoryPipelineMode): void;
}) {
  const { text, locale } = useOnboardingTranslation();
  const id = useId();
  const [state, setState] = useState<MemoryModeState | null>(null);
  const [schedule, setScheduleState] = useState<EvolveSchedule | null>(null);
  const evolveState = useEvolveState();
  const overview = evolveState.data ?? null;
  const run =
    overview?.running.find((candidate) => candidate.user_id === "local") ??
    null;
  const [pendingMode, setPendingMode] = useState<MemoryPipelineMode | null>(
    null,
  );
  const [pendingPlan, setPendingPlan] = useState<MemoryModePlan | null>(null);
  const [busy, setBusy] = useState(false);
  const [maintenance, setMaintenance] = useState(false);
  const [error, setError] = useState("");
  const [readFailed, setReadFailed] = useState(false);
  const [scheduleReadFailed, setScheduleReadFailed] = useState(false);
  const refreshVersion = useRef(0);
  const dialogOpener = useRef<HTMLElement | null>(null);
  const focusEvolveWhenReady = useRef(false);
  const focusModeWhenReady = useRef<MemoryPipelineMode | null>(null);
  const focusScheduleWhenReady = useRef(false);

  useEffect(() => {
    if (!focusEvolveWhenReady.current || pendingMode || state?.mode !== "ultra")
      return;
    const evolve = document.getElementById(`${id}-evolve`);
    if (evolve) {
      focusEvolveWhenReady.current = false;
      evolve.focus();
    }
  }, [id, pendingMode, state?.mode]);

  useEffect(() => {
    const target = focusModeWhenReady.current;
    if (!target || busy || pendingMode || state?.mode !== target) return;
    focusModeWhenReady.current = null;
    document.getElementById(`${id}-select`)?.focus();
  }, [busy, id, pendingMode, state?.mode]);

  useEffect(() => {
    if (!focusScheduleWhenReady.current || scheduleReadFailed || !schedule)
      return;
    focusScheduleWhenReady.current = false;
    document
      .getElementById(`${id}-evolve`)
      ?.querySelector<HTMLElement>('[role="switch"]')
      ?.focus();
  }, [id, schedule, scheduleReadFailed]);

  const refresh = useCallback(async () => {
    const version = ++refreshVersion.current;
    const modeTicket = issueConfirmedModeTicket("settings");
    const [modeResult, scheduleResult, processingResult] =
      await Promise.allSettled([
        getMemoryMode(),
        getEvolveSchedule(),
        getMemoryProcessing(),
      ]);
    if (version !== refreshVersion.current) return;
    if (modeResult.status === "fulfilled") {
      commitConfirmedMemoryMode(modeResult.value.mode, modeTicket);
      setState(modeResult.value);
      onModeChange?.(modeResult.value.mode);
      setError("");
      setReadFailed(false);
    } else {
      setError("记忆模式状态暂时无法读取，请重试。");
      setReadFailed(true);
    }
    if (scheduleResult.status === "fulfilled") {
      setScheduleState(scheduleResult.value);
      setScheduleReadFailed(false);
    } else {
      setScheduleReadFailed(true);
    }
    if (processingResult.status === "fulfilled")
      setMaintenance(Boolean(processingResult.value.embedding_switch));
  }, [onModeChange]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    const running = maintenance || state?.graph_build.state === "running";
    if (!running) return;
    const timer = window.setInterval(() => void refresh(), 1500);
    return () => window.clearInterval(timer);
  }, [maintenance, refresh, state?.graph_build.state]);

  const lifecycleBusy =
    maintenance ||
    run?.state === "running" ||
    state?.graph_build.state === "running";

  async function choose(mode: MemoryPipelineMode) {
    if (!state || mode === state.mode || busy || lifecycleBusy) return;
    setError("");
    setBusy(true);
    let plan: MemoryModePlan;
    try {
      plan = await planMemoryMode(mode);
    } catch (reason) {
      if (!memoryModePlanUnsupported(reason)) {
        setError("模式切换影响暂时无法读取，请重试。");
        focusModeWhenReady.current = state.mode;
        setBusy(false);
        return;
      }
      plan = legacyMemoryModePlan(state.mode, mode, { hasActiveModel });
    }
    setBusy(false);
    if (plan.blockers.includes("active_model_required")) {
      onConfigureModel(BLOCKER_COPY.active_model_required);
      return;
    }
    dialogOpener.current = document.getElementById(`${id}-select`);
    setPendingPlan(plan);
    setPendingMode(mode);
  }

  async function commitMode() {
    if (!pendingMode) return;
    const targetMode = pendingMode;
    refreshVersion.current++;
    const modeTicket = issueConfirmedModeTicket("settings");
    setBusy(true);
    setError("");
    try {
      const changed: MemoryModeChange = await setMemoryMode(pendingMode);
      commitConfirmedMemoryMode(changed.mode, modeTicket);
      focusEvolveWhenReady.current = targetMode === "ultra";
      focusModeWhenReady.current = targetMode === "ultra" ? null : targetMode;
      setState(changed);
      onModeChange?.(changed.mode);
      setPendingPlan(null);
      setPendingMode(null);
      await refresh();
    } catch (reason) {
      const code = apiErrorCode(reason);
      setError(
        code === "embedding_maintenance_in_progress"
          ? "正在检查或切换向量模型，完成后才能切换记忆模式。"
          : code === "graph_store_unavailable"
            ? "当前安装缺少知识图谱组件，无法开启 ultra。"
            : "模式切换未能确认，仍显示上次确认的模式。",
      );
    } finally {
      setBusy(false);
    }
  }

  async function changeSchedule(update: Partial<EvolveSchedule>) {
    refreshVersion.current++;
    setBusy(true);
    setError("");
    try {
      setScheduleState(await setEvolveSchedule(update));
      return true;
    } catch {
      setError("自动整合设置未能保存，请重试。");
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function evolveNow() {
    if (!state) return;
    refreshVersion.current++;
    setBusy(true);
    setError("");
    try {
      const receipt = await startEvolve();
      commitEvolveRun(
        receipt,
        overview ??
          (schedule
            ? {
                mode: state.mode,
                schedule,
                users: {},
                running: [],
              }
            : undefined),
      );
    } catch (reason) {
      const code = apiErrorCode(reason);
      setError(
        code === "embedding_maintenance_in_progress"
          ? "正在检查或切换向量模型，完成后才能整合记忆。"
          : "未能开始整合记忆，请重试。",
      );
    } finally {
      setBusy(false);
    }
  }

  const last = overview?.users.local;
  return (
    <section
      className="r6-memory-mode-settings"
      aria-labelledby="memory-mode-title"
    >
      <div className="r6-mode-heading">
        <div>
          <h2 id="memory-mode-title">{text("记忆模式")}</h2>
          <p>{text("模式决定新对话如何保存，以及记忆页面展示什么。")}</p>
        </div>
      </div>
      <div
        className="r6-mode-control"
        data-expanded={state?.mode === "ultra" || undefined}
      >
        <div className="r6-mode-setting-row" aria-busy={state === null}>
          <OptionMenu
            id={`${id}-select`}
            ariaLabel={text("记忆模式")}
            value={state?.mode ?? ""}
            disabled={
              !state || busy || lifecycleBusy || Boolean(state?.forced_by_env)
            }
            options={(["lite", "pro", "ultra"] as const).map((mode) => ({
              value: mode,
              label: modeDisplayName(text, mode),
              suffix: MODE_TIER[mode],
              description: text(MODE_COPY[mode].description),
              disabled: mode !== "lite" && !modelStateKnown,
            }))}
            className="r6-language-option-menu r6-mode-option-menu"
            panelClassName="r6-language-option-menu-panel--settings r6-mode-option-menu-panel"
            onChange={(value) => {
              if (value === "lite" || value === "pro" || value === "ultra")
                void choose(value);
            }}
          />
        </div>
        {state?.forced_by_env ? (
          <p className="r6-settings-note" role="status">
            {text("开发环境当前强制使用 ultra，保存的选择暂不会生效。")}
          </p>
        ) : null}
        {maintenance ? (
          <p className="r6-settings-note" role="status">
            {text("正在检查或切换向量模型，完成前不能切换模式或手动整合。")}
          </p>
        ) : null}
        {state?.graph_build.state === "running" ? (
          <p className="r6-settings-note" role="status">
            {text("正在把已有高级记忆加入知识图谱…")}
          </p>
        ) : null}
        {error && !pendingMode ? (
          readFailed ? (
            <R6StateRecovery
              message={text(error)}
              onRetry={() => void refresh()}
            />
          ) : (
            <p className="r6-settings-error" role="alert">
              {text(error)}
            </p>
          )
        ) : null}

        {state?.mode === "ultra" ? (
          <section
            id={`${id}-evolve`}
            className="r6-evolve-settings"
            aria-labelledby={`${id}-evolve-title`}
            tabIndex={-1}
          >
            {schedule ? (
              <UltraEvolveScheduleFields
                schedule={schedule}
                headingId={`${id}-evolve-title`}
                disabled={busy || maintenance}
                onEnabledChange={(enabled) => void changeSchedule({ enabled })}
                onTimeChange={(time) => changeSchedule({ time })}
              />
            ) : (
              <>
                <div className="r6-evolve-heading">
                  <h3 id={`${id}-evolve-title`}>
                    {text("记忆整合（Evolve）")}
                  </h3>
                  <p>
                    {text("定期重新整理已有记忆，补充更稳定的关联和结论。")}
                  </p>
                </div>
                {scheduleReadFailed ? (
                  <R6StateRecovery
                    message={text("自动整合设置暂时无法读取，请重试。")}
                    onRetry={() => {
                      focusScheduleWhenReady.current = true;
                      void refresh();
                    }}
                  />
                ) : null}
              </>
            )}
            <div className="r6-evolve-setting r6-evolve-setting--manual">
              <div className="r6-evolve-setting-copy">
                <h3>{text("立即整合一次")}</h3>
                <p>
                  {text("只运行这一次，不改变每天的计划。")}
                  {" · "}
                  {text(
                    run?.state === "running"
                      ? "正在整合记忆…"
                      : last?.last_run_at
                        ? text("上次整合：{time}").replace(
                            "{time}",
                            new Date(last.last_run_at).toLocaleString(locale),
                          )
                        : "尚未整合",
                  )}
                </p>
              </div>
              <R6Button
                pending={run?.state === "running"}
                disabled={busy || lifecycleBusy}
                onClick={() => void evolveNow()}
              >
                {text("立即整合")}
              </R6Button>
            </div>
          </section>
        ) : null}
      </div>

      {pendingMode && pendingPlan ? (
        <MemoryModeConfirmDialog
          mode={pendingMode}
          plan={pendingPlan}
          busy={busy}
          error={error}
          opener={dialogOpener.current}
          onCancel={() => {
            setPendingPlan(null);
            setPendingMode(null);
          }}
          onConfirm={() => void commitMode()}
        />
      ) : null}
    </section>
  );
}
