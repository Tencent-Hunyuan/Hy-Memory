import type {
  EvolveOverview,
  EvolveRunReceipt,
  EvolveSchedule,
  MemoryModeChange,
  MemoryModeState,
  MemoryPipelineMode,
} from "@one-memory/contracts";
import { useCallback, useEffect, useId, useRef, useState } from "react";
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
import { R6StateRecovery } from "./R6StateRecovery";

const MODE_COPY: Record<
  MemoryPipelineMode,
  { title: string; description: string }
> = {
  lite: {
    title: "基础记忆（lite）",
    description: "保存原始对话 · 不调用生成模型 · 搜索原始会话",
  },
  pro: {
    title: "智能记忆（pro）",
    description: "沉淀高级记忆 · 调用生成模型 · 搜索沉淀记忆",
  },
  ultra: {
    title: "深度记忆（ultra）",
    description: "智能记忆 + 知识图谱 · 可自动 Evolve · 增强关联召回",
  },
};

function confirmationRows(
  current: MemoryPipelineMode,
  target: MemoryPipelineMode,
): Array<{ label: string; content: string }> {
  if (target === "lite") {
    return [
      {
        label: "新对话",
        content: "之后只保存原始对话，不再生成新记忆。",
      },
      {
        label: "模型调用与费用",
        content:
          current === "ultra"
            ? "停止调用生成模型；每天自动整合也会停止。"
            : "停止调用生成模型，不再产生这部分费用。",
      },
      {
        label: "搜索范围",
        content: "立即改为搜索原始对话。",
      },
      {
        label: "已有数据",
        content:
          "已有记忆继续保留，但在基础记忆下不显示、不参与召回；原始对话不会删除。",
      },
    ];
  }
  if (target === "pro") {
    return [
      {
        label: "新对话",
        content:
          current === "lite"
            ? "排队中和之后的新对话会由模型整理成记忆；已经处理完的旧对话不会补做。"
            : "仍会由模型整理成记忆；新记忆不再写入知识图谱。",
      },
      {
        label: "模型调用与费用",
        content:
          current === "ultra"
            ? "提取仍会调用生成模型；每天自动整合会停止。"
            : "开始调用当前生成模型，可能产生服务商费用。",
      },
      {
        label: "搜索范围",
        content:
          current === "ultra"
            ? "改为搜索智能记忆，不再使用知识图谱补充关联。"
            : "立即改为搜索沉淀后的记忆。",
      },
      {
        label: "已有数据",
        content:
          current === "ultra"
            ? "普通沉淀记忆仍可使用；深度记忆新增的更高层记忆和图谱数据继续保留，但在智能记忆下不显示、不参与召回。原始对话不会删除。"
            : "原始对话全部保留，不会删除。",
      },
    ];
  }
  return [
    {
      label: "新对话",
      content:
        current === "lite"
          ? "排队中和之后的新对话会提取记忆并写入知识图谱；已经处理完的旧对话不会补做。"
          : "提取方式不变，新记忆会写入知识图谱。",
    },
    {
      label: "模型调用与费用",
      content:
        "提取会调用生成模型；若每天自动整合已开启，到设定时间还会产生额外调用。切换本身不会立即整合。",
    },
    {
      label: "搜索范围",
      content: "搜索沉淀后的记忆，并用知识图谱补充关联。",
    },
    {
      label: "已有数据",
      content: "已有记忆会在后台加入知识图谱，期间仍可使用；原始对话不会删除。",
    },
    {
      label: "切换后",
      content: "可设置每天自动整合时间，也可以随时立即整合一次。",
    },
  ];
}

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
  const [overview, setOverview] = useState<EvolveOverview | null>(null);
  const [pendingMode, setPendingMode] = useState<MemoryPipelineMode | null>(
    null,
  );
  const [busy, setBusy] = useState(false);
  const [maintenance, setMaintenance] = useState(false);
  const [error, setError] = useState("");
  const [readFailed, setReadFailed] = useState(false);
  const [run, setRun] = useState<EvolveRunReceipt | null>(null);
  const refreshVersion = useRef(0);
  const dialog = useRef<HTMLElement>(null);
  const dialogOpener = useRef<HTMLElement | null>(null);
  const focusEvolveWhenReady = useRef(false);
  const focusModeWhenReady = useRef<MemoryPipelineMode | null>(null);
  const busyRef = useRef(false);
  busyRef.current = busy;

  useEffect(() => {
    if (!pendingMode || !dialog.current) return;
    const panel = dialog.current;
    const inertSiblings = new Map<HTMLElement, boolean>();
    let branch: HTMLElement = panel.parentElement ?? panel;
    while (branch.parentElement) {
      for (const sibling of branch.parentElement.children) {
        if (!(sibling instanceof HTMLElement) || sibling === branch) continue;
        inertSiblings.set(sibling, sibling.inert);
        sibling.inert = true;
      }
      branch = branch.parentElement;
      if (branch === document.body) break;
    }
    const controls = () =>
      Array.from(panel.querySelectorAll<HTMLElement>("button:not(:disabled)"));
    controls()[0]?.focus();
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busyRef.current) {
        event.preventDefault();
        setPendingMode(null);
        return;
      }
      if (event.key !== "Tab") return;
      const items = controls();
      const first = items[0];
      const last = items.at(-1);
      if (!first || !last) {
        event.preventDefault();
        panel.focus();
        return;
      }
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    panel.addEventListener("keydown", handleKey);
    return () => {
      panel.removeEventListener("keydown", handleKey);
      for (const [sibling, wasInert] of inertSiblings) sibling.inert = wasInert;
      if (dialogOpener.current?.isConnected) dialogOpener.current.focus();
    };
  }, [pendingMode]);

  useEffect(() => {
    if (pendingMode && busy) dialog.current?.focus();
  }, [busy, pendingMode]);

  useEffect(() => {
    if (!focusEvolveWhenReady.current || pendingMode || state?.mode !== "ultra")
      return;
    focusEvolveWhenReady.current = false;
    document.getElementById(`${id}-evolve`)?.focus();
  }, [id, pendingMode, state?.mode]);

  useEffect(() => {
    const target = focusModeWhenReady.current;
    if (!target || busy || pendingMode || state?.mode !== target) return;
    focusModeWhenReady.current = null;
    document
      .querySelector<HTMLInputElement>(
        `input[name="${id}-mode"][value="${target}"]`,
      )
      ?.focus();
  }, [busy, id, pendingMode, state?.mode]);

  const refresh = useCallback(async () => {
    const version = ++refreshVersion.current;
    const [modeResult, scheduleResult, overviewResult, processingResult] =
      await Promise.allSettled([
        getMemoryMode(),
        getEvolveSchedule(),
        getEvolveOverview(),
        getMemoryProcessing(),
      ]);
    if (version !== refreshVersion.current) return;
    if (modeResult.status === "fulfilled") {
      setState(modeResult.value);
      onModeChange?.(modeResult.value.mode);
      setError("");
      setReadFailed(false);
    } else {
      setError("记忆模式状态暂时无法读取，请重试。");
      setReadFailed(true);
    }
    if (scheduleResult.status === "fulfilled")
      setScheduleState(scheduleResult.value);
    if (overviewResult.status === "fulfilled") {
      setOverview(overviewResult.value);
      setRun(overviewResult.value.running[0] ?? null);
    }
    if (processingResult.status === "fulfilled")
      setMaintenance(Boolean(processingResult.value.embedding_switch));
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

  function choose(mode: MemoryPipelineMode, opener: HTMLElement) {
    if (!state || mode === state.mode || busy || lifecycleBusy) return;
    if (mode !== "lite" && !hasActiveModel) {
      const message = "使用 pro 或 ultra 前需要先配置并启用生成模型。";
      setError("");
      onConfigureModel(message);
      return;
    }
    setError("");
    dialogOpener.current = opener;
    setPendingMode(mode);
  }

  async function commitMode() {
    if (!pendingMode) return;
    const targetMode = pendingMode;
    refreshVersion.current++;
    setBusy(true);
    setError("");
    try {
      const changed: MemoryModeChange = await setMemoryMode(pendingMode);
      focusEvolveWhenReady.current = targetMode === "ultra";
      focusModeWhenReady.current = targetMode === "ultra" ? null : targetMode;
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
      </div>
      <div
        className="r6-mode-choice-list"
        role="radiogroup"
        aria-label={text("当前模式")}
        aria-busy={state === null}
      >
        {(["lite", "pro", "ultra"] as const).map((mode) => {
          const checked = state?.mode === mode;
          return (
            <label
              key={mode}
              className="r6-mode-choice"
              data-current={checked || undefined}
              data-disabled={
                !state ||
                busy ||
                lifecycleBusy ||
                (state?.forced_by_env && mode !== state.mode) ||
                (mode !== "lite" && !modelStateKnown) ||
                undefined
              }
            >
              <input
                type="radio"
                name={`${id}-mode`}
                value={mode}
                checked={checked}
                disabled={
                  !state ||
                  busy ||
                  lifecycleBusy ||
                  (state?.forced_by_env && mode !== state.mode) ||
                  (mode !== "lite" && !modelStateKnown)
                }
                onChange={(event) => choose(mode, event.currentTarget)}
              />
              <span className="r6-mode-choice-copy">
                <strong>{text(MODE_COPY[mode].title)}</strong>
                <small>{text(MODE_COPY[mode].description)}</small>
              </span>
              {checked ? (
                <span className="r6-mode-current">{text("当前模式")}</span>
              ) : null}
            </label>
          );
        })}
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

      {state?.mode === "ultra" && schedule ? (
        <section
          id={`${id}-evolve`}
          className="r6-evolve-settings"
          aria-labelledby={`${id}-evolve-title`}
          tabIndex={-1}
        >
          <div className="r6-evolve-heading">
            <h3 id={`${id}-evolve-title`}>{text("记忆整合（Evolve）")}</h3>
            <p>{text("定期重新整理已有记忆，补充更稳定的关联和结论。")}</p>
          </div>
          <div className="r6-evolve-setting">
            <div className="r6-evolve-setting-copy">
              <h3>{text("每天自动整合")}</h3>
              <p>
                {text(
                  schedule.enabled
                    ? "每天 {time} 左右自动整合，使用这台设备的本地时间；休眠错过后会补跑。"
                    : "已关闭。重新开启后仍按 {time} 运行；已生成的结果会保留。",
                ).replace("{time}", schedule.time)}
              </p>
            </div>
            <R6Switch
              label={text("每天自动整合")}
              checked={schedule.enabled}
              disabled={busy || maintenance}
              onCheckedChange={(enabled) => void changeSchedule({ enabled })}
            />
            {schedule.enabled ? (
              <div className="r6-evolve-time">
                <div>
                  <label htmlFor={`${id}-evolve-time`}>
                    {text("执行时间")}
                  </label>
                  <p>{text("使用这台设备的本地时间。")}</p>
                </div>
                <input
                  id={`${id}-evolve-time`}
                  type="time"
                  value={schedule.time}
                  disabled={busy || maintenance}
                  onChange={(event) =>
                    void changeSchedule({ time: event.target.value })
                  }
                />
              </div>
            ) : null}
          </div>
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

      {pendingMode ? (
        <div className="r6-settings-dialog-layer" role="presentation">
          <section
            ref={dialog}
            className="r6-settings-dialog r6-mode-confirm-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="memory-mode-confirm-title"
            tabIndex={-1}
          >
            <h2 id="memory-mode-confirm-title">
              {text("切换到 {mode}？").replace(
                "{mode}",
                text(MODE_COPY[pendingMode].title),
              )}
            </h2>
            <dl className="r6-mode-confirm-effects">
              {confirmationRows(state?.mode ?? "pro", pendingMode).map(
                (row) => (
                  <div key={row.label}>
                    <dt>{text(row.label)}</dt>
                    <dd>{text(row.content)}</dd>
                  </div>
                ),
              )}
            </dl>
            {error ? (
              <p className="r6-settings-error" role="alert">
                {text(error)}
              </p>
            ) : null}
            <footer>
              <R6Button disabled={busy} onClick={() => setPendingMode(null)}>
                {text("取消")}
              </R6Button>
              <R6Button
                variant="primary"
                pending={busy}
                onClick={() => void commitMode()}
              >
                {text("切换到 {mode}").replace(
                  "{mode}",
                  text(MODE_COPY[pendingMode].title),
                )}
              </R6Button>
            </footer>
          </section>
        </div>
      ) : null}
    </section>
  );
}
