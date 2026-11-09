import { useEffect, useState } from "react";

import {
  getMemoryFeatures,
  type MemoryFeatureState,
  setMemoryFeature,
} from "./api";
import { useOnboardingTranslation } from "./onboarding-language";
import { R6Switch } from "./R6Controls";

const REASON_LABELS: Record<string, string> = {
  disabled: "未开启",
  requires_pro: "lite 模式下不生效（需要 pro 或 ultra）",
  requires_ultra: "当前模式下不生效（需要 ultra）",
  requires_graph_rerank: "需要先开启并生效图谱重排",
  requires_intention_dedup: "意图判重已关闭，阈值不起作用",
};

/** Exposes the SDK optional retrieval/extraction features already available
 * through the CLI and `/api/v1/settings/memory-features` (issue #386).
 */
export function MemoryFeaturesSettings() {
  const { text } = useOnboardingTranslation();
  const [features, setFeatures] = useState<MemoryFeatureState[]>([]);
  const [mode, setMode] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let active = true;
    getMemoryFeatures()
      .then((state) => {
        if (!active) return;
        setFeatures(state.features);
        setMode(state.mode);
      })
      .catch(() => {
        if (active) setError("功能设置读取失败");
      })
      .finally(() => {
        if (active) setLoaded(true);
      });
    return () => {
      active = false;
    };
  }, []);

  const toggle = async (feature: MemoryFeatureState) => {
    if (pending !== null) return;
    setPending(feature.name);
    setError("");
    try {
      const next = await setMemoryFeature(feature.name, !feature.enabled);
      setFeatures(next.features);
      setMode(next.mode);
    } catch {
      setError("功能设置保存失败，请重试");
    } finally {
      setPending(null);
    }
  };

  return (
    <section className="r6-settings-features" aria-label={text("高级记忆功能")}>
      <h3>{text("高级记忆功能")}</h3>
      <p>{text("全局设置；未设置的项保持 SDK 默认值。当前模式：")}{mode}</p>
      {!loaded ? <p>{text("正在读取…")}</p> : null}
      {error ? <p role="alert">{error}</p> : null}
      <ul className="r6-features-list">
        {features.map((feature) => (
          <li key={feature.name}>
            <div>
              <strong>{feature.name}</strong>
              <span>{feature.description}</span>
              {!feature.active && feature.reason ? (
                <small>{REASON_LABELS[feature.reason] ?? feature.reason}</small>
              ) : null}
            </div>
            <R6Switch
              label={feature.name}
              checked={feature.enabled}
              pending={pending === feature.name}
              disabled={pending !== null}
              onCheckedChange={() => void toggle(feature)}
            />
          </li>
        ))}
      </ul>
    </section>
  );
}
