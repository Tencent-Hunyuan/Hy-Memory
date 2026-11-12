import type { EvolveSchedule } from "@one-memory/contracts";
import { useId } from "react";
import { useOnboardingTranslation } from "./onboarding-language";
import { R6Switch } from "./R6Controls";
import { TimePicker } from "./TimePicker";

export function UltraEvolveScheduleFields({
  schedule,
  headingId,
  disabled = false,
  onEnabledChange,
  onTimeChange,
}: {
  schedule: EvolveSchedule;
  headingId?: string;
  disabled?: boolean;
  onEnabledChange(enabled: boolean): void;
  onTimeChange(time: string): Promise<boolean>;
}) {
  const { text } = useOnboardingTranslation();
  const id = useId();
  return (
    <>
      <div className="r6-evolve-heading">
        <h3 id={headingId}>{text("记忆整合（Evolve）")}</h3>
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
          disabled={disabled}
          onCheckedChange={onEnabledChange}
        />
        {schedule.enabled ? (
          <div className="r6-evolve-time">
            <div>
              <label htmlFor={`${id}-time`}>{text("执行时间")}</label>
              <p>{text("使用这台设备的本地时间。")}</p>
            </div>
            <TimePicker
              id={`${id}-time`}
              label={text("执行时间")}
              value={schedule.time}
              disabled={disabled}
              onChange={onTimeChange}
            />
          </div>
        ) : null}
      </div>
    </>
  );
}
