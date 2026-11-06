import type { MemoryModePlan, MemoryPipelineMode } from "@one-memory/contracts";
import { useEffect, useRef } from "react";
import { useOnboardingTranslation } from "./onboarding-language";
import { R6Button } from "./R6Controls";

export const MODE_COPY: Record<
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

export const MODE_TIER: Record<MemoryPipelineMode, string> = {
  lite: "Lite",
  pro: "Pro",
  ultra: "Ultra",
};

export function modeDisplayName(
  text: (value: string) => string,
  mode: MemoryPipelineMode,
) {
  return text(MODE_COPY[mode].title)
    .replace(/\s*[（(](?:lite|pro|ultra)[)）]\s*/i, " ")
    .replace(/\s{2,}/g, " ")
    .trim();
}

export const BLOCKER_COPY: Record<MemoryModePlan["blockers"][number], string> =
  {
    active_model_required: "使用 pro 或 ultra 前需要先配置并启用生成模型。",
    embedding_maintenance_in_progress:
      "正在检查或切换向量模型，完成后才能切换记忆模式。",
    memory_lifecycle_busy: "记忆维护正在进行，完成后才能切换模式。",
    graph_store_unavailable: "当前安装缺少知识图谱组件，无法开启 ultra。",
  };

export function memoryModePlanRows(plan: MemoryModePlan) {
  const rows = [
    {
      label: "新对话",
      content:
        plan.capabilities.content_plane === "raw"
          ? "之后只保存原始对话，不再生成新记忆。"
          : plan.capabilities.graph_enabled
            ? "新对话会整理成记忆并写入知识图谱。"
            : "新对话会由模型整理成记忆。",
    },
    {
      label: "模型调用与费用",
      content: plan.capabilities.generation_required
        ? plan.capabilities.evolve_available
          ? "会调用当前生成模型；自动整合可能产生额外费用。切换本身不会立即整合。"
          : "会调用当前生成模型，可能产生服务商费用。"
        : "不调用生成模型，不再产生这部分费用。",
    },
    {
      label: "搜索范围",
      content:
        plan.capabilities.content_plane === "raw"
          ? "切换后搜索原始对话。"
          : plan.capabilities.graph_enabled
            ? "搜索沉淀记忆，并用知识图谱补充关联。"
            : "搜索沉淀后的记忆。",
    },
  ];
  if (plan.effects.includes("earlier_raw_not_extracted"))
    rows.push({
      label: "已有数据",
      content: "已经处理完的旧对话不会补做；原始对话全部保留。",
    });
  if (plan.effects.includes("higher_mode_memories_hidden"))
    rows.push({
      label: "已有数据",
      content:
        plan.effective_target === "pro"
          ? "普通沉淀记忆仍可使用；深度记忆新增的更高层记忆和图谱数据继续保留，但在智能记忆下不显示、不参与召回。原始对话不会删除。"
          : "已有记忆继续保留，但在基础记忆下不显示、不参与召回；原始对话不会删除。",
    });
  if (plan.effects.includes("existing_memories_added_to_graph"))
    rows.push({
      label: "知识图谱",
      content: "已有记忆会在后台加入知识图谱，期间仍可使用。",
    });
  if (plan.effects.includes("evolve_stopped"))
    rows.push({
      label: "每天自动整合",
      content: "每天自动整合会停止，已经生成的数据不会删除。",
    });
  if (plan.capabilities.evolve_available)
    rows.push({
      label: "切换后",
      content: "可设置每天自动整合时间，也可以随时立即整合一次。",
    });
  return rows;
}

export function MemoryModeConfirmDialog({
  mode,
  plan,
  busy,
  error,
  opener,
  allowedBlockers = [],
  onCancel,
  onConfirm,
}: {
  mode: MemoryPipelineMode;
  plan: MemoryModePlan;
  busy: boolean;
  error?: string;
  opener?: HTMLElement | null;
  allowedBlockers?: readonly MemoryModePlan["blockers"][number][];
  onCancel(): void;
  onConfirm(): void;
}) {
  const { text } = useOnboardingTranslation();
  const panel = useRef<HTMLElement>(null);
  const busyRef = useRef(busy);
  const onCancelRef = useRef(onCancel);
  busyRef.current = busy;
  onCancelRef.current = onCancel;
  const blocking = plan.blockers.filter(
    (blocker) => !allowedBlockers.includes(blocker),
  );

  useEffect(() => {
    const element = panel.current;
    if (!element) return;
    const inertSiblings = new Map<HTMLElement, boolean>();
    let branch: HTMLElement = element.parentElement ?? element;
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
      Array.from(
        element.querySelectorAll<HTMLElement>("button:not(:disabled)"),
      );
    controls()[0]?.focus();
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busyRef.current) {
        event.preventDefault();
        onCancelRef.current();
        return;
      }
      if (event.key !== "Tab") return;
      const items = controls();
      const first = items[0];
      const last = items.at(-1);
      if (!first || !last) {
        event.preventDefault();
        element.focus();
      } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    element.addEventListener("keydown", handleKey);
    return () => {
      element.removeEventListener("keydown", handleKey);
      for (const [sibling, wasInert] of inertSiblings) sibling.inert = wasInert;
      if (opener?.isConnected) opener.focus();
    };
  }, [opener]);

  useEffect(() => {
    if (busy) panel.current?.focus();
  }, [busy]);

  return (
    <div className="r6-settings-dialog-layer" role="presentation">
      <section
        ref={panel}
        className="r6-settings-dialog r6-mode-confirm-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="memory-mode-confirm-title"
        tabIndex={-1}
      >
        <h2 id="memory-mode-confirm-title">
          {text("切换到 {mode}？").replace(
            "{mode}",
            `${modeDisplayName(text, mode)} · ${MODE_TIER[mode]}`,
          )}
        </h2>
        <dl className="r6-mode-confirm-effects">
          {memoryModePlanRows(plan).map((row) => (
            <div key={`${row.label}:${row.content}`}>
              <dt>{text(row.label)}</dt>
              <dd>{text(row.content)}</dd>
            </div>
          ))}
        </dl>
        {plan.blockers.map((blocker) => (
          <p
            className={
              blocking.includes(blocker)
                ? "r6-settings-error"
                : "r6-settings-note"
            }
            role={blocking.includes(blocker) ? "alert" : "status"}
            key={blocker}
          >
            {text(BLOCKER_COPY[blocker])}
          </p>
        ))}
        {error ? (
          <p className="r6-settings-error" role="alert">
            {text(error)}
          </p>
        ) : null}
        <footer>
          <R6Button disabled={busy} onClick={onCancel}>
            {text("取消")}
          </R6Button>
          <R6Button
            variant="primary"
            pending={busy}
            disabled={blocking.length > 0}
            onClick={onConfirm}
          >
            {text("切换模式")}
          </R6Button>
        </footer>
      </section>
    </div>
  );
}
