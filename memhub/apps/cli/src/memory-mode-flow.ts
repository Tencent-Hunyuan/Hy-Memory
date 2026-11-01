/**
 * `memhub settings mode` and `memhub evolve`.
 *
 * The mode (lite / pro / ultra) decides what the SDK does with every
 * conversation, and switching it migrates nothing: what one mode made is not
 * shown by a lower one, and raw kept by lite is not extracted until it is
 * imported again. So a change prints its plan first and needs `--yes`, like
 * every other command whose effect a person cannot simply take back.
 *
 * `memhub evolve` starts System 2 (ultra only) and, with `--wait`, follows
 * the run until it ends.
 */

import type { CoreReader } from "./core-client.js";
import type { CoreOperations, CorePutOperations } from "./core-operations.js";
import { PRODUCT } from "./version.js";

export const MEMORY_MODES = ["lite", "pro", "ultra"] as const;
export type MemoryMode = (typeof MEMORY_MODES)[number];

export type ModeEffect =
  | "earlier_raw_not_extracted"
  | "higher_mode_memories_hidden"
  | "existing_memories_added_to_graph"
  | "evolve_stopped"
  | "lite_search_stops"
  | "graph_backfill_already_running";

const MODE_SUMMARY: Record<MemoryMode, string> = {
  lite: "lite：不调用模型，保留全部原始对话，只在原始对话里检索",
  pro: "pro：模型从对话中提取记忆（System 1）",
  ultra: "ultra：pro + 每晚自动进化（System 2，使用知识图谱）",
};

const EFFECT_TEXT: Record<ModeEffect, string> = {
  earlier_raw_not_extracted:
    "lite 下已处理完的对话不会再被提取；只有尚未处理的和之后的新对话会由模型整理成记忆，检索立即改为记忆检索",
  higher_mode_memories_hidden:
    "更高模式生成的记忆仍保存着，但在这个模式下不会显示",
  existing_memories_added_to_graph:
    "已有记忆会在后台加入知识图谱（每个用户一次）",
  evolve_stopped: "每晚的自动进化（System 2）停止",
  lite_search_stops: "lite 只在原始对话里的检索立即停止，改为检索记忆",
  graph_backfill_already_running: "知识图谱的补建已在进行中",
};

/** The same rule Core applies (`memory_mode_effects`), for the plan. */
export function modeEffects(
  previous: MemoryMode,
  next: MemoryMode,
): ModeEffect[] {
  if (previous === next) return [];
  const rank: Record<MemoryMode, number> = { lite: 0, pro: 1, ultra: 2 };
  const effects: ModeEffect[] = [];
  if (previous === "lite") {
    effects.push("earlier_raw_not_extracted", "lite_search_stops");
  }
  if (rank[next] < rank[previous]) effects.push("higher_mode_memories_hidden");
  if (previous === "ultra") effects.push("evolve_stopped");
  if (next === "ultra") effects.push("existing_memories_added_to_graph");
  return effects;
}

export type ModeCommand =
  | { kind: "show" }
  | { kind: "set"; mode: MemoryMode; confirmed: boolean };

/** `settings mode [lite|pro|ultra] [--yes]` after `settings`; null = usage. */
export function parseSettingsCommand(
  values: readonly string[],
): ModeCommand | null {
  const rest = values.filter((value) => value !== "--json");
  if (rest[0] !== "mode") return null;
  const tail = rest.slice(1);
  const confirmed = tail.includes("--yes");
  const positional = tail.filter((value) => value !== "--yes");
  if (tail.filter((value) => value === "--yes").length > 1) return null;
  if (positional.length === 0) return confirmed ? null : { kind: "show" };
  if (positional.length !== 1) return null;
  const mode = positional[0] as MemoryMode;
  if (!(MEMORY_MODES as readonly string[]).includes(mode)) return null;
  return { kind: "set", mode, confirmed };
}

export interface ModeState {
  mode: MemoryMode;
  configured_mode: MemoryMode;
  forced_by_env: boolean;
  graph_build?: { state?: string } | null;
  previous_mode?: MemoryMode;
  effects?: ModeEffect[];
}

function modeState(value: unknown): ModeState | null {
  if (!value || typeof value !== "object") return null;
  const item = value as Record<string, unknown>;
  if (
    !(MEMORY_MODES as readonly unknown[]).includes(item.mode) ||
    !(MEMORY_MODES as readonly unknown[]).includes(item.configured_mode) ||
    typeof item.forced_by_env !== "boolean"
  ) {
    return null;
  }
  return item as unknown as ModeState;
}

export type ModeDocument = {
  schema_version: 1;
  product: string;
  command: "settings mode";
} & (
  | { state: "ok"; mode: ModeState }
  | {
      state: "preview";
      mode: ModeState;
      target: MemoryMode;
      effects: ModeEffect[];
      applied: false;
      next_step: string;
    }
  | { state: "unchanged"; mode: ModeState }
  | { state: "committed"; mode: ModeState; effects: ModeEffect[] }
  | { state: "rejected" | "unknown" | "unavailable"; code: string }
);

const BASE = {
  schema_version: 1 as const,
  product: PRODUCT,
  command: "settings mode" as const,
};

export async function runModeCommand(
  command: ModeCommand,
  reader: CoreReader,
  operations: CorePutOperations,
): Promise<ModeDocument> {
  const read = await reader<unknown>("/api/v1/settings/memory-mode");
  if (read.state !== "ok") {
    return { ...BASE, state: "unavailable", code: read.code };
  }
  const current = modeState(read.data);
  if (current === null) {
    return { ...BASE, state: "unavailable", code: "response_invalid" };
  }
  if (command.kind === "show") return { ...BASE, state: "ok", mode: current };
  if (current.configured_mode === command.mode) {
    return { ...BASE, state: "unchanged", mode: current };
  }
  if (!command.confirmed) {
    return {
      ...BASE,
      state: "preview",
      mode: current,
      target: command.mode,
      effects: modeEffects(current.mode, command.mode),
      applied: false,
      next_step: `${PRODUCT} settings mode ${command.mode} --yes`,
    };
  }
  const result = await operations.put<unknown>("/api/v1/settings/memory-mode", {
    mode: command.mode,
  });
  if (result.state !== "committed") {
    return { ...BASE, state: result.state, code: result.code };
  }
  const changed = modeState(result.data);
  if (changed === null) {
    return { ...BASE, state: "unknown", code: "response_invalid" };
  }
  return {
    ...BASE,
    state: "committed",
    mode: changed,
    effects: changed.effects ?? [],
  };
}

export function modeExitCode(document: ModeDocument): number {
  return ["ok", "preview", "unchanged", "committed"].includes(document.state)
    ? 0
    : 1;
}

export function renderModeText(document: ModeDocument): string {
  if ("code" in document) {
    return `记忆模式未能${document.state === "unavailable" ? "读取" : "修改"}：${document.code}\n`;
  }
  const mode = document.mode;
  const lines = [`当前模式：${MODE_SUMMARY[mode.mode]}`];
  if (mode.forced_by_env) {
    lines.push(
      `设置里是 ${mode.configured_mode}；环境变量 ONE_MEMORY_GRAPH_STORE 强制使用 ultra。`,
    );
  }
  lines.push("原始对话：全部保留（任何模式都不删除）");
  if (document.state === "unchanged") {
    lines.push("已经是这个模式，未做改动。");
  }
  if (document.state === "preview") {
    lines.push(`将切换到：${MODE_SUMMARY[document.target]}`);
    for (const effect of document.effects)
      lines.push(`- ${EFFECT_TEXT[effect]}`);
    lines.push(`未执行任何改动。确认后运行：${document.next_step}`);
  }
  if (document.state === "committed") {
    lines.push("已切换。");
    for (const effect of document.effects)
      lines.push(`- ${EFFECT_TEXT[effect]}`);
  }
  return `${lines.join("\n")}\n`;
}

export type EvolveCommand = {
  agentId?: string;
  maxBatches?: number;
  wait: boolean;
};

/** `evolve [--agent-id <id>] [--max-batches <n>] [--wait]`; null = usage. */
export function parseEvolveCommand(
  values: readonly string[],
): EvolveCommand | null {
  const parsed: EvolveCommand = { wait: false };
  const rest = values.filter((value) => value !== "--json");
  for (let index = 0; index < rest.length; index += 1) {
    const flag = rest[index];
    if (flag === "--wait" && !parsed.wait) {
      parsed.wait = true;
    } else if (flag === "--agent-id" && parsed.agentId === undefined) {
      const value = rest[index + 1];
      if (!value || !/^[A-Za-z0-9._:-]{1,128}$/.test(value)) return null;
      parsed.agentId = value;
      index += 1;
    } else if (flag === "--max-batches" && parsed.maxBatches === undefined) {
      const value = Number(rest[index + 1]);
      if (!Number.isInteger(value) || value < 1 || value > 1000) return null;
      parsed.maxBatches = value;
      index += 1;
    } else {
      return null;
    }
  }
  return parsed;
}

export type EvolveDocument = {
  schema_version: 1;
  product: string;
  command: "evolve";
} & (
  | { state: "running" | "succeeded" | "failed"; run: Record<string, unknown> }
  | { state: "rejected" | "unknown" | "unavailable"; code: string }
);

const RUN_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

function evolveRun(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object") return null;
  const run = value as Record<string, unknown>;
  if (
    typeof run.run_id !== "string" ||
    !RUN_ID.test(run.run_id) ||
    !["running", "succeeded", "failed"].includes(String(run.state))
  ) {
    return null;
  }
  return run;
}

export async function runEvolveCommand(
  command: EvolveCommand,
  options: {
    userId?: string;
    reader: CoreReader;
    operations: CoreOperations;
    sleep?: (ms: number) => Promise<void>;
    pollMs?: number;
  },
): Promise<EvolveDocument> {
  const base = {
    schema_version: 1 as const,
    product: PRODUCT,
    command: "evolve" as const,
  };
  const body: Record<string, unknown> = {};
  if (options.userId !== undefined) body.user_id = options.userId;
  if (command.agentId !== undefined) body.agent_id = command.agentId;
  if (command.maxBatches !== undefined) body.max_batches = command.maxBatches;
  const started = await options.operations.post<unknown>(
    "/api/v2/evolve",
    body,
  );
  if (started.state !== "committed") {
    return { ...base, state: started.state, code: started.code };
  }
  let run = evolveRun(started.data);
  if (run === null)
    return { ...base, state: "unknown", code: "response_invalid" };
  const sleep =
    options.sleep ??
    ((ms: number) => new Promise((done) => setTimeout(done, ms)));
  while (command.wait && run.state === "running") {
    await sleep(options.pollMs ?? 2_000);
    const read = await options.reader<unknown>(
      `/api/v2/evolve/${run.run_id}`,
      options.userId === undefined ? {} : { user_id: options.userId },
    );
    if (read.state !== "ok") {
      return { ...base, state: "unavailable", code: read.code };
    }
    const next = evolveRun(read.data);
    if (next === null)
      return { ...base, state: "unknown", code: "response_invalid" };
    run = next;
  }
  return {
    ...base,
    state: run.state as "running" | "succeeded" | "failed",
    run: projectRun(run),
  };
}

/** Counts and codes; SDK result bodies stay in the Core receipt. */
function projectRun(run: Record<string, unknown>): Record<string, unknown> {
  const keep = [
    "run_id",
    "user_id",
    "agent_id",
    "state",
    "trigger",
    "started_at",
    "finished_at",
    "rounds",
    "tasks_processed",
    "batch_failures",
    "cycle_status",
    "should_continue",
    "continuation_reason",
    "remaining_clusters",
    "remaining_actionable_clusters",
    "remaining_fresh_facts",
    "capped",
    "elapsed_ms",
    "error_code",
    "failure_kind",
  ];
  return Object.fromEntries(
    keep.filter((key) => key in run).map((key) => [key, run[key]]),
  );
}

export function evolveExitCode(document: EvolveDocument): number {
  return document.state === "running" || document.state === "succeeded" ? 0 : 1;
}

export function renderEvolveText(document: EvolveDocument): string {
  if ("code" in document) {
    return document.code === "memory_mode_not_ultra"
      ? "进化只在 ultra 模式下可用。切换：memhub settings mode ultra --yes\n"
      : `进化未能开始：${document.code}\n`;
  }
  const run = document.run;
  if (document.state === "running") {
    return `进化已开始（${run.run_id}），在后台运行；加 --wait 等待结果。\n`;
  }
  if (document.state === "failed") {
    return `进化失败（${run.run_id}）：${String(run.error_code ?? "unknown")}\n`;
  }
  return `进化完成（${run.run_id}）：${run.rounds} 轮，处理 ${run.tasks_processed} 项${
    run.cycle_status ? `，周期 ${run.cycle_status}` : ""
  }${run.capped ? "，已到轮数上限，可再次运行继续" : ""}\n`;
}
