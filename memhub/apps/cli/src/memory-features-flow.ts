/**
 * `memhub settings features [set <name>=<true|false> ... | reset [<name> ...]]`.
 *
 * The SDK's optional retrieval and extraction features (graph rerank, episode
 * recall, the episode write side, intention and its dedup, few-shot), plus the
 * intention dedup threshold, the one numeric setting. One global setting in
 * the Runtime; an unset feature keeps the SDK default, so an install nobody
 * touched behaves as before. `set` prints its plan first and needs `--yes`,
 * like `settings mode`, because some features spend model calls; `reset`
 * only returns features to their defaults and applies at once.
 */

import type { CoreReader } from "./core-client.js";
import type { CorePutOperations } from "./core-operations.js";
import { PRODUCT } from "./version.js";

export const MEMORY_FEATURES = [
  "graph_rerank",
  "same_series_timeline",
  "episode_recall",
  "session_episodes",
  "episode_summary",
  "intention",
  "intention_dedup",
  "few_shot",
] as const;
export type MemoryFeature = (typeof MEMORY_FEATURES)[number];

/** Numeric settings, with the range the Runtime accepts (inclusive). */
export const MEMORY_PARAMETERS = {
  intention_dedup_threshold: { minimum: 0.8, maximum: 1 },
} as const;
export type MemoryParameter = keyof typeof MEMORY_PARAMETERS;
export type MemorySetting = MemoryFeature | MemoryParameter;
type SettingValues = Partial<Record<MemorySetting, boolean | number>>;

const FEATURE_LABEL: Record<MemoryFeature, string> = {
  graph_rerank: "图谱重排（沿知识图谱的边传播支持度；仅 ultra）",
  same_series_timeline:
    "同系列时间线（同一系列的记忆按时间放在一起；需图谱重排）",
  episode_recall:
    "片段召回（补入同一片段的记忆并多通道打分；pro / ultra，需片段写入）",
  session_episodes: "片段写入（同一会话提取的记忆归入同一片段）",
  episode_summary: "片段摘要（片段结束时总结一次；每个片段一次模型调用）",
  intention: "意图提取（随事实一起提取用户的计划与意图）",
  intention_dedup:
    "意图判重（检索时把同一天、相似度达到阈值的意图视为重复，只返回最早一条并删除其余；关闭则不判重、不删除）",
  few_shot: "提取示例（在提取提示里附上示例；不加调用，输入更长）",
};

const REASON_TEXT: Record<string, string> = {
  disabled: "未开启",
  requires_pro: "lite 模式下不生效（需要 pro 或 ultra）",
  requires_ultra: "当前模式下不生效（需要 ultra）",
  requires_graph_rerank: "需要先开启并生效 graph_rerank",
  requires_intention_dedup: "intention_dedup 已关闭，阈值不起作用",
};

const PARAMETER_LABEL: Record<MemoryParameter, string> = {
  intention_dedup_threshold: "意图判重阈值（余弦相似度，越高保留越多）",
};

export type FeaturesCommand =
  | { kind: "show" }
  | {
      kind: "set";
      values: SettingValues;
      confirmed: boolean;
    }
  | { kind: "reset"; names: MemorySetting[] };

function isFeature(value: string): value is MemoryFeature {
  return (MEMORY_FEATURES as readonly string[]).includes(value);
}

function isParameter(value: string): value is MemoryParameter {
  return Object.hasOwn(MEMORY_PARAMETERS, value);
}

function isSetting(value: string): value is MemorySetting {
  return isFeature(value) || isParameter(value);
}

/** A parameter's value: a plain decimal in its range; null = usage. */
function parameterValue(name: MemoryParameter, raw: string): number | null {
  if (!/^\d+(\.\d+)?$/.test(raw)) return null;
  const value = Number(raw);
  const { minimum, maximum } = MEMORY_PARAMETERS[name];
  return value >= minimum && value <= maximum ? value : null;
}

/** Arguments after `settings features`; null = usage. */
export function parseFeaturesCommand(
  values: readonly string[],
): FeaturesCommand | null {
  const rest = values.filter((value) => value !== "--json");
  if (rest.length === 0) return { kind: "show" };
  const [verb, ...tail] = rest;
  const yes = tail.filter((value) => value === "--yes").length;
  if (yes > 1) return null;
  const positional = tail.filter((value) => value !== "--yes");
  if (verb === "set") {
    if (positional.length === 0) return null;
    const parsed: SettingValues = {};
    for (const item of positional) {
      const match = /^([a-z_]+)=(.+)$/.exec(item);
      if (!match || match[1] in parsed) return null;
      const [, name, raw] = match;
      if (isFeature(name)) {
        if (raw !== "true" && raw !== "false") return null;
        parsed[name] = raw === "true";
      } else if (isParameter(name)) {
        const value = parameterValue(name, raw);
        if (value === null) return null;
        parsed[name] = value;
      } else {
        return null;
      }
    }
    return { kind: "set", values: parsed, confirmed: yes === 1 };
  }
  if (verb === "reset") {
    if (new Set(positional).size !== positional.length) return null;
    if (!positional.every(isSetting)) return null;
    return { kind: "reset", names: positional as MemorySetting[] };
  }
  return null;
}

export interface FeatureState {
  name: MemoryFeature;
  enabled: boolean;
  configured: boolean | null;
  default: boolean;
  active: boolean;
  reason: string | null;
  cost: string;
  requires_mode: string;
  description: string;
}

export interface ParameterState {
  name: MemoryParameter;
  value: number;
  configured: number | null;
  default: number;
  minimum: number;
  maximum: number;
  active: boolean;
  reason: string | null;
  requires_feature: string;
  description: string;
}

export interface FeaturesState {
  mode: string;
  features: FeatureState[];
  /** Absent from a Runtime older than the intention dedup threshold. */
  parameters?: ParameterState[];
  non_default: SettingValues;
  changed?: MemorySetting[];
}

function featuresState(value: unknown): FeaturesState | null {
  if (!value || typeof value !== "object") return null;
  const item = value as Record<string, unknown>;
  if (
    typeof item.mode !== "string" ||
    !Array.isArray(item.features) ||
    !item.non_default ||
    typeof item.non_default !== "object"
  ) {
    return null;
  }
  for (const feature of item.features) {
    if (
      !feature ||
      typeof feature !== "object" ||
      typeof (feature as FeatureState).name !== "string" ||
      typeof (feature as FeatureState).enabled !== "boolean" ||
      typeof (feature as FeatureState).active !== "boolean"
    ) {
      return null;
    }
  }
  if (item.parameters !== undefined) {
    if (!Array.isArray(item.parameters)) return null;
    for (const parameter of item.parameters) {
      if (
        !parameter ||
        typeof parameter !== "object" ||
        typeof (parameter as ParameterState).name !== "string" ||
        typeof (parameter as ParameterState).value !== "number" ||
        typeof (parameter as ParameterState).active !== "boolean"
      ) {
        return null;
      }
    }
  }
  return item as unknown as FeaturesState;
}

export type FeaturesDocument = {
  schema_version: 1;
  product: string;
  command: "settings features";
} & (
  | { state: "ok"; features: FeaturesState }
  | {
      state: "preview";
      features: FeaturesState;
      target: SettingValues;
      applied: false;
      next_step: string;
    }
  | { state: "unchanged"; features: FeaturesState }
  | { state: "committed"; features: FeaturesState; changed: MemorySetting[] }
  | { state: "rejected" | "unknown" | "unavailable"; code: string }
);

const BASE = {
  schema_version: 1 as const,
  product: PRODUCT,
  command: "settings features" as const,
};

const PATH = "/api/v1/settings/memory-features";

export async function runFeaturesCommand(
  command: FeaturesCommand,
  reader: CoreReader,
  operations: CorePutOperations,
): Promise<FeaturesDocument> {
  const read = await reader<unknown>(PATH);
  if (read.state !== "ok") {
    return { ...BASE, state: "unavailable", code: read.code };
  }
  const current = featuresState(read.data);
  if (current === null) {
    return { ...BASE, state: "unavailable", code: "response_invalid" };
  }
  if (command.kind === "show") {
    return { ...BASE, state: "ok", features: current };
  }
  // What a person set, per name this Runtime lists; null = the default.
  const configured = new Map<string, boolean | number | null>([
    ...current.features.map(
      (feature) => [feature.name, feature.configured] as const,
    ),
    ...(current.parameters ?? []).map(
      (parameter) => [parameter.name, parameter.configured] as const,
    ),
  ]);
  let body: Record<string, unknown>;
  if (command.kind === "set") {
    if (Object.keys(command.values).some((name) => !configured.has(name))) {
      // An older Runtime does not know the name; it would answer 422.
      return { ...BASE, state: "rejected", code: "memory_feature_unsupported" };
    }
    const differs = Object.entries(command.values).some(
      ([name, value]) => configured.get(name) !== value,
    );
    if (!differs) return { ...BASE, state: "unchanged", features: current };
    if (!command.confirmed) {
      const pairs = Object.entries(command.values)
        .map(([name, value]) => `${name}=${value}`)
        .join(" ");
      return {
        ...BASE,
        state: "preview",
        features: current,
        target: command.values,
        applied: false,
        next_step: `${PRODUCT} settings features set ${pairs} --yes`,
      };
    }
    body = { ...command.values };
  } else {
    const names =
      command.names.length > 0
        ? command.names
        : [...configured]
            .filter(([, value]) => value !== null)
            .map(([name]) => name as MemorySetting);
    if (names.every((name) => (configured.get(name) ?? null) === null)) {
      return { ...BASE, state: "unchanged", features: current };
    }
    body = Object.fromEntries(names.map((name) => [name, null]));
  }
  const result = await operations.put<unknown>(PATH, body);
  if (result.state !== "committed") {
    return { ...BASE, state: result.state, code: result.code };
  }
  const changed = featuresState(result.data);
  if (changed === null) {
    return { ...BASE, state: "unknown", code: "response_invalid" };
  }
  return {
    ...BASE,
    state: "committed",
    features: changed,
    changed: changed.changed ?? [],
  };
}

export function featuresExitCode(document: FeaturesDocument): number {
  return ["ok", "preview", "unchanged", "committed"].includes(document.state)
    ? 0
    : 1;
}

function parameterLine(parameter: ParameterState): string {
  const label = PARAMETER_LABEL[parameter.name] ?? parameter.name;
  const origin = parameter.configured === null ? "默认" : "已设置";
  const activity =
    !parameter.active && parameter.reason
      ? `，${REASON_TEXT[parameter.reason] ?? parameter.reason}`
      : "";
  return `${parameter.name}：${parameter.value}（${origin}${activity}，可设 ${parameter.minimum}–${parameter.maximum}）— ${label}`;
}

function featureLine(feature: FeatureState): string {
  const label = FEATURE_LABEL[feature.name] ?? feature.name;
  const value = feature.enabled ? "开" : "关";
  const origin = feature.configured === null ? "默认" : "已设置";
  const activity =
    feature.enabled && !feature.active && feature.reason
      ? `，${REASON_TEXT[feature.reason] ?? feature.reason}`
      : "";
  return `${feature.name}：${value}（${origin}${activity}）— ${label}`;
}

export function renderFeaturesText(document: FeaturesDocument): string {
  if ("code" in document) {
    return `SDK 功能未能${document.state === "unavailable" ? "读取" : "修改"}：${document.code}\n`;
  }
  const state = document.features;
  const lines = [`当前模式：${state.mode}`];
  for (const feature of state.features) lines.push(featureLine(feature));
  for (const parameter of state.parameters ?? [])
    lines.push(parameterLine(parameter));
  lines.push(
    "片段召回（episode_recall）只对带片段的记忆有用：先开启 session_episodes，之后提取的记忆才会带片段。",
  );
  if (document.state === "unchanged") lines.push("没有需要改动的设置。");
  if (document.state === "preview") {
    lines.push("将设置：");
    for (const [name, value] of Object.entries(document.target)) {
      lines.push(
        `- ${name} = ${typeof value === "number" ? value : value ? "开" : "关"}`,
      );
    }
    lines.push(`未执行任何改动。确认后运行：${document.next_step}`);
  }
  if (document.state === "committed") {
    lines.push(
      document.changed.length > 0
        ? `已更新：${document.changed.join("、")}。正在进行的写入按原设置完成，之后的写入和检索使用新设置。`
        : "已保存，生效的值没有变化。",
    );
  }
  return `${lines.join("\n")}\n`;
}
