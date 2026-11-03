import type {
  EvolveOverview,
  EvolveRunReceipt,
  EvolveSchedule,
  MemoryModeChange,
  MemoryModeState,
  MemoryPipelineMode,
} from "@one-memory/contracts";
import { RefreshCw } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  apiErrorCode,
  getEvolveOverview,
  getEvolveSchedule,
  getMemoryMode,
  getMemoryProcessing,
  setEvolveSchedule,
  setMemoryMode,
  startEvolve,
} from "./api";
import { useOnboardingTranslation } from "./onboarding-language";
import { R6Button, R6Switch } from "./R6Controls";

const MODE_COPY: Record<
  MemoryPipelineMode,
  { title: string; description: string }
> = {
  lite: {
    title: "基础记忆（lite）",
    description: "不调用生成模型。保存并检索原始对话，不再生成高级记忆。",
  },
  pro: {
    title: "智能记忆（pro）",
    description: "使用当前模型从新对话中提取事实、偏好和计划。",
  },
  ultra: {
    title: "深度记忆（ultra）",
    description: "在智能记忆之上使用知识图谱，并默认每天 03:00 自动整合记忆。",
  },
};

const EFFECT_COPY: Record<string, string> = {
  earlier_raw_not_extracted:
    "lite 下已经处理完成的原始对话不会补提取；排队中和之后的新对话会生成高级记忆。",
  lite_search_stops: "原始对话检索会停止，改为使用高级记忆。",
  higher_mode_memories_hidden:
    "以前生成的高级记忆会继续保留，但在 lite 下不会显示或参与记忆召回。",
  evolve_stopped: "每天自动整合会停止，已经生成的数据不会删除。",
  existing_memories_added_to_graph:
    "已有高级记忆会在后台加入知识图谱，期间可以继续使用 MemHub。",
  graph_backfill_already_running: "知识图谱仍在补建，不会重复启动。",
};

function plannedEffects(
  current: MemoryPipelineMode,
  target: MemoryPipelineMode,
): string[] {
  if (current === target) return [];
  const effects: string[] = [];
  if (current === "lite" && target !== "lite")
    effects.push("earlier_raw_not_extracted", "lite_search_stops");
  if (target === "lite") effects.push("higher_mode_memories_hidden");
  if (current === "ultra" && target !== "ultra") effects.push("evolve_stopped");
  if (target === "ultra") effects.push("existing_memories_added_to_graph");
  return effects;
}

export function MemoryModeSettings({
  hasActiveModel,
  onConfigureModel,
  onModeChange,
}: {
  hasActiveModel: boolean;
  onConfigureModel(): void;
  onModeChange?(mode: MemoryPipelineMode): void;
}) {
  const { text } = useOnboardingTranslation();
  const [state, setState] = useState<MemoryModeState | null>(null);
  const [schedule, setScheduleState] = useState<EvolveSchedule | null>(null);
  const [overview, setOverview] = useState<EvolveOverview | null>(null);
  const [pendingMode, setPendingMode] = useState<MemoryPipelineMode | null>(
    null,
  );
  const [busy, setBusy] = useState(false);
  const [maintenance, setMaintenance] = useState(false);
  const [error, setError] = useState("");
  const [run, setRun] = useState<EvolveRunReceipt | null>(null);
  const refreshVersion = useRef(0);

  const refresh = useCallback(async () => {
    const version = ++refreshVersion.current;
    try {
      const [nextMode, nextSchedule, nextOverview, processing] =
        await Promise.all([
          getMemoryMode(),
          getEvolveSchedule(),
          getEvolveOverview(),
          getMemoryProcessing(),
        ]);
      if (version !== refreshVersion.current) return;
      setState(nextMode);
      onModeChange?.(nextMode.mode);
      setScheduleState(nextSchedule);
      setOverview(nextOverview);
      setMaintenance(Boolean(processing.embedding_switch));
      setRun(nextOverview.running[0] ?? null);
      setError("");
    } catch {
      if (version !== refreshVersion.current) return;
      setError("记忆模式状态暂时无法读取，请重试。");
    }
  }, [onModeChange]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    const running =
      maintenance ||
      state?.graph_build.state === "running" ||
      run?.state === "running";
    if (!running) return;
    const timer = window.setInterval(() => void refresh(), 1500);
    return () => window.clearInterval(timer);
  }, [maintenance, refresh, run?.state, state?.graph_build.state]);

  const lifecycleBusy =
    maintenance ||
    run?.state === "running" ||
    state?.graph_build.state === "running";

  function choose(mode: MemoryPipelineMode) {
    if (!state || mode === state.mode || busy || lifecycleBusy) return;
    if (mode !== "lite" && !hasActiveModel) {
      setError("使用 pro 或 ultra 前需要先配置并启用生成模型。");
      onConfigureModel();
      return;
    }
    setError("");
    setPendingMode(mode);
  }

  async function commitMode() {
    if (!pendingMode) return;
    refreshVersion.current++;
    setBusy(true);
    setError("");
    try {
      const changed: MemoryModeChange = await setMemoryMode(pendingMode);
      setState(changed);
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
    } catch {
      setError("自动整合设置未能保存，请重试。");
    } finally {
      setBusy(false);
    }
  }

  async function evolveNow() {
    refreshVersion.current++;
    setBusy(true);
    setError("");
    try {
      const receipt = await startEvolve();
      setRun(receipt);
      await refresh();
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
        <R6Button
          variant="quiet"
          aria-label={text("刷新记忆模式")}
          title={text("刷新记忆模式")}
          disabled={busy}
          onClick={() => void refresh()}
        >
          <RefreshCw size={16} aria-hidden="true" />
        </R6Button>
      </div>
      <div className="r6-mode-options" aria-busy={state === null}>
        {(["lite", "pro", "ultra"] as const).map((mode) => (
          <button
            key={mode}
            type="button"
            className="r6-mode-option"
            aria-pressed={state?.mode === mode}
            disabled={!state || busy || lifecycleBusy}
            onClick={() => choose(mode)}
          >
            <strong>{text(MODE_COPY[mode].title)}</strong>
            <span>{text(MODE_COPY[mode].description)}</span>
            {state?.mode === mode ? <small>{text("当前模式")}</small> : null}
          </button>
        ))}
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
      {error ? (
        <p className="r6-settings-error" role="alert">
          {text(error)}
        </p>
      ) : null}

      {state?.mode === "ultra" && schedule ? (
        <div className="r6-evolve-settings">
          <div className="r6-setting-row">
            <div>
              <h3>{text("每天自动整合")}</h3>
              <p>
                {text(
                  "每天 {time} 自动运行 evolve，设备休眠错过后会补跑。",
                ).replace("{time}", schedule.time)}
              </p>
            </div>
            <R6Switch
              label={text("每天自动整合")}
              checked={schedule.enabled}
              disabled={busy || maintenance}
              onCheckedChange={(enabled) => void changeSchedule({ enabled })}
            />
          </div>
          <div className="r6-setting-row">
            <div>
              <h3>{text("自动整合时间")}</h3>
              <p>{text("使用这台设备的本地时间。")}</p>
            </div>
            <input
              type="time"
              value={schedule.time}
              disabled={busy || maintenance}
              aria-label={text("自动整合时间")}
              onChange={(event) =>
                void changeSchedule({ time: event.target.value })
              }
            />
          </div>
          <div className="r6-setting-row">
            <div>
              <h3>{text("立即整合记忆")}</h3>
              <p>
                {text(
                  run?.state === "running"
                    ? "正在整合记忆…"
                    : last?.last_run_at
                      ? text("上次整合：{time}").replace(
                          "{time}",
                          new Date(last.last_run_at).toLocaleString(),
                        )
                      : "尚未整合",
                )}
              </p>
            </div>
            <R6Button
              variant="primary"
              pending={run?.state === "running"}
              disabled={busy || lifecycleBusy}
              onClick={() => void evolveNow()}
            >
              {text("立即整合")}
            </R6Button>
          </div>
        </div>
      ) : null}

      {pendingMode ? (
        <div className="r6-settings-dialog-layer" role="presentation">
          <section
            className="r6-settings-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="memory-mode-confirm-title"
          >
            <h2 id="memory-mode-confirm-title">
              {text("切换到 {mode}？").replace("{mode}", pendingMode)}
            </h2>
            <p>{text(MODE_COPY[pendingMode].description)}</p>
            <ul>
              {plannedEffects(state?.mode ?? "pro", pendingMode).map(
                (effect) => (
                  <li key={effect}>{text(EFFECT_COPY[effect])}</li>
                ),
              )}
              <li>{text("原始对话在所有模式下都会保留。")}</li>
            </ul>
            <footer>
              <R6Button disabled={busy} onClick={() => setPendingMode(null)}>
                {text("取消")}
              </R6Button>
              <R6Button
                variant="primary"
                pending={busy}
                onClick={() => void commitMode()}
              >
                {text("切换模式")}
              </R6Button>
            </footer>
          </section>
        </div>
      ) : null}
    </section>
  );
}
