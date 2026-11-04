import type { MemoryPipelineMode } from "@one-memory/contracts";
import { type ReactNode, useId } from "react";
import "./r6-controls.css";

export type MemoryModeChoice = {
  mode: MemoryPipelineMode;
  title: ReactNode;
  description: ReactNode;
};

export function MemoryModeChoiceList({
  modes,
  current,
  selected,
  disabled = false,
  onChange,
  currentBadge,
  recommended,
  "aria-label": ariaLabel,
  "aria-busy": ariaBusy,
}: {
  modes: readonly MemoryModeChoice[];
  current?: MemoryPipelineMode;
  selected: MemoryPipelineMode | null;
  disabled?: boolean | ((mode: MemoryPipelineMode) => boolean);
  onChange(mode: MemoryPipelineMode, control: HTMLInputElement): void;
  currentBadge?: ReactNode;
  recommended?: {
    mode: MemoryPipelineMode;
    badge: ReactNode;
  };
  "aria-label": string;
  "aria-busy"?: boolean;
}) {
  const name = useId();
  const isDisabled = (mode: MemoryPipelineMode) =>
    typeof disabled === "function" ? disabled(mode) : disabled;

  return (
    <div
      className="memory-mode-choice-list"
      role="radiogroup"
      aria-label={ariaLabel}
      aria-busy={ariaBusy || undefined}
    >
      {modes.map((choice) => {
        const checked = selected === choice.mode;
        const unavailable = isDisabled(choice.mode);
        return (
          <label
            key={choice.mode}
            className="memory-mode-choice"
            data-selected={checked || undefined}
            data-current={current === choice.mode || undefined}
            data-disabled={unavailable || undefined}
          >
            <input
              type="radio"
              name={name}
              value={choice.mode}
              checked={checked}
              disabled={unavailable}
              onChange={(event) => onChange(choice.mode, event.currentTarget)}
            />
            <span className="memory-mode-choice-copy">
              <span className="memory-mode-choice-heading">
                <strong>{choice.title}</strong>
                {recommended?.mode === choice.mode ? (
                  <span className="memory-mode-choice-badge">
                    {recommended.badge}
                  </span>
                ) : null}
              </span>
              <small>{choice.description}</small>
            </span>
            {current === choice.mode && currentBadge ? (
              <span className="memory-mode-choice-current">{currentBadge}</span>
            ) : null}
          </label>
        );
      })}
    </div>
  );
}
