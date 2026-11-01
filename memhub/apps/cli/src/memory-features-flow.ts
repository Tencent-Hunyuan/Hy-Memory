/**
 * `memhub settings features [set <name>=<true|false> ... | reset [<name> ...]]`.
 *
 * The SDK's optional retrieval and extraction features (graph rerank, episode
 * recall, the episode write side, intention, few-shot). One global setting in
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
  "few_shot",
] as const;
export type MemoryFeature = (typeof MEMORY_FEATURES)[number];

const FEATURE_LABEL: Record<MemoryFeature, string> = {
  graph_rerank: "图谱重排（沿知识图谱的边传播支持度；仅 ultra）",
  same_series_timeline:
    "同系列时间线（同一系列的记忆按时间放在一起；需图谱重排）",
  episode_recall:
    "片段召回（补入同一片段的记忆并多通道打分；pro / ultra，需片段写入）",
  session_episodes: "片段写入（同一会话提取的记忆归入同一片段）",
  episode_summary: "片段摘要（片段结束时总结一次；每个片段一次模型调用）",
  intention: "意图提取（随事实一起提取用户的计划与意图）",
  few_shot: "提取示例（在提取提示里附上示例；不加调用，输入更长）",
};

const REASON_TEXT: Record<string, string> = {
  disabled: "未开启",
  requires_pro: "lite 模式下不生效（需要 pro 或 ultra）",
  requires_ultra: "当前模式下不生效（需要 ultra）",
  requires_graph_rerank: "需要先开启并生效 graph_rerank",
};

export type FeaturesCommand =
  | { kind: "show" }
  | {
      kind: "set";
      values: Partial<Record<MemoryFeature, boolean>>;
      confirmed: boolean;
    }
  | { kind: "reset"; names: MemoryFeature[] };

function isFeature(value: string): value is MemoryFeature {
  return (MEMORY_FEATURES as readonly string[]).includes(value);
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
    const parsed: Partial<Record<MemoryFeature, boolean>> = {};
    for (const item of positional) {
      const match = /^([a-z_]+)=(true|false)$/.exec(item);
      if (!match || !isFeature(match[1]) || match[1] in parsed) return null;
      parsed[match[1]] = match[2] === "true";
    }
    return { kind: "set", values: parsed, confirmed: yes === 1 };
  }
  if (verb === "reset") {
    if (new Set(positional).size !== positional.length) return null;
    if (!positional.every(isFeature)) return null;
    return { kind: "reset", names: positional as MemoryFeature[] };
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

export interface FeaturesState {
  mode: string;
  features: FeatureState[];
  non_default: Partial<Record<MemoryFeature, boolean>>;
  changed?: MemoryFeature[];
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
      target: Partial<Record<MemoryFeature, boolean>>;
      applied: false;
      next_step: string;
    }
  | { state: "unchanged"; features: FeaturesState }
  | { state: "committed"; features: FeaturesState; changed: MemoryFeature[] }
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
  let body: Record<string, unknown>;
  if (command.kind === "set") {
    const configured = new Map(
      current.features.map((feature) => [feature.name, feature.configured]),
    );
    const differs = Object.entries(command.values).some(
      ([name, value]) => configured.get(name as MemoryFeature) !== value,
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
        : current.features
            .filter((feature) => feature.configured !== null)
            .map((feature) => feature.name);
    if (
      names.every(
        (name) =>
          current.features.find((feature) => feature.name === name)
            ?.configured === null,
      )
    ) {
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
  lines.push(
    "片段召回（episode_recall）只对带片段的记忆有用：先开启 session_episodes，之后提取的记忆才会带片段。",
  );
  if (document.state === "unchanged") lines.push("没有需要改动的设置。");
  if (document.state === "preview") {
    lines.push("将设置：");
    for (const [name, value] of Object.entries(document.target)) {
      lines.push(`- ${name} = ${value ? "开" : "关"}`);
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
