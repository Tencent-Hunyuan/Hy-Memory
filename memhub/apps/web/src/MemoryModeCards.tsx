import type { MemoryPipelineMode } from "@one-memory/contracts";
import { Check, Clock3 } from "lucide-react";
import { type ReactNode, useId } from "react";
import { MODE_COPY, MODE_TIER, modeDisplayName } from "./memory-mode-ui";
import { useOnboardingTranslation } from "./onboarding-language";
import "./memory-mode-cards.css";

const MODES: Array<{
  id: MemoryPipelineMode;
  index: string;
  description: string;
}> = [
  {
    id: "lite",
    index: "01",
    description: "不调用生成模型。保存并检索原始对话，不再生成高级记忆。",
  },
  {
    id: "pro",
    index: "02",
    description: "使用当前模型从新对话中提取事实、偏好和计划。",
  },
  {
    id: "ultra",
    index: "03",
    description: "在智能记忆之上使用知识图谱，并默认每天 03:00 自动整合记忆。",
  },
];

export function MemoryModeCards({
  value,
  current,
  currentBadge,
  disabled = false,
  layout = "grid",
  describedBy,
  onChange,
  ultraDetail,
  ultraDetailBusy = false,
  "aria-label": ariaLabel,
  "aria-busy": ariaBusy,
}: {
  value: MemoryPipelineMode | null;
  current?: MemoryPipelineMode;
  currentBadge?: ReactNode;
  disabled?: boolean | ((mode: MemoryPipelineMode) => boolean);
  layout?: "grid" | "rows";
  describedBy?: Partial<Record<MemoryPipelineMode, string | undefined>>;
  onChange(mode: MemoryPipelineMode, control: HTMLInputElement): void;
  ultraDetail?: ReactNode;
  ultraDetailBusy?: boolean;
  "aria-label": string;
  "aria-busy"?: boolean;
}) {
  const { text } = useOnboardingTranslation();
  const name = useId();
  const unavailable = (mode: MemoryPipelineMode) =>
    typeof disabled === "function" ? disabled(mode) : disabled;

  return (
    <div
      className={`memory-mode-cards memory-mode-cards--${layout}`}
      role="radiogroup"
      aria-label={ariaLabel}
      aria-busy={ariaBusy || undefined}
    >
      <div className="memory-mode-card-list">
        {MODES.map((mode) => {
          const selected = value === mode.id;
          const modeDisabled = unavailable(mode.id);
          const title = modeDisplayName(text, mode.id);
          const tier = MODE_TIER[mode.id];
          const features = text(MODE_COPY[mode.id].description).split(
            /\s*·\s*/,
          );
          return (
            <div
              key={mode.id}
              className="memory-mode-card"
              data-mode={mode.id}
              data-selected={selected || undefined}
              data-current={current === mode.id || undefined}
              data-disabled={modeDisabled || undefined}
            >
              <label className="memory-mode-card-choice">
                <input
                  type="radio"
                  name={name}
                  value={mode.id}
                  aria-label={`${title} · ${tier}`}
                  aria-describedby={describedBy?.[mode.id]}
                  checked={selected}
                  disabled={modeDisabled}
                  onChange={(event) => onChange(mode.id, event.currentTarget)}
                />
                <span className="memory-mode-title">
                  <span className="memory-mode-index">{mode.index}</span>
                  <strong>{title}</strong>
                  <span className="memory-mode-tier">· {tier}</span>
                  {current === mode.id && currentBadge ? (
                    <span className="memory-mode-current">{currentBadge}</span>
                  ) : null}
                  <span className="memory-mode-radio" aria-hidden="true">
                    {selected ? <Check size={13} /> : null}
                  </span>
                </span>
                <span className="memory-mode-description">
                  {text(mode.description)}
                </span>
                <span className="memory-mode-features">
                  {features.map((feature) => (
                    <span key={feature}>
                      <i aria-hidden="true" />
                      {text(feature)}
                    </span>
                  ))}
                </span>
              </label>
              {mode.id === "ultra" && selected && ultraDetail ? (
                <div
                  className="memory-mode-detail"
                  aria-busy={ultraDetailBusy || undefined}
                >
                  <Clock3 size={14} aria-hidden="true" />
                  <div>{ultraDetail}</div>
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}
