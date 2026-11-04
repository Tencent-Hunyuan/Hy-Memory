import type {
  HomeProjectionV1,
  LiveSyncHealth,
  MemoryProcessingSummary,
  ModelProfile,
  SourceResponse,
} from "@one-memory/contracts";
import type {
  BackgroundHealth,
  RuntimeStatus,
} from "@one-memory/runtime-control";
import { sdkMigrationLogPath } from "@one-memory/runtime-control";
import type { CoreReader, ReadOutcome } from "./core-client.js";
import {
  type SdkHomeSummary,
  validateHomeProjection,
  validateHomeSummary,
  validateLiveSync,
  validateModelProfiles,
  validateProcessing,
  validateRawTotal,
  validateSources,
} from "./core-responses.js";
import { describeEmbeddingSwitchSummary } from "./embedding-switch-flow.js";
import { PRODUCT } from "./version.js";

const MIGRATION_ISSUES_URL =
  "https://github.com/Tencent-Hunyuan/Hy-Memory/issues/new";

export type SectionState =
  | "ok"
  | "denied"
  | "unavailable"
  | "invalid"
  | "not_checked";

export type Section = { state: SectionState; code?: string } & Record<
  string,
  unknown
>;

export interface StatusDocument {
  schema_version: 1;
  product: string;
  data_dir: string;
  runtime: {
    state: RuntimeStatus["state"];
    code: string;
    instance_id?: string;
    host?: string;
    port?: number;
    version?: string;
    /** Set only when an unfinished upgrade migration is why nothing serves. */
    log_file?: string;
    issues_url?: string;
  };
  bridge: {
    state: BackgroundHealth["state"] | "not_checked";
    code: string;
    core?: BackgroundHealth["core"];
    broker?: BackgroundHealth["broker"];
  };
  sections: Record<string, Section>;
  /** R10: the SDK mode and System 2 state; null when the Runtime is not read
   * or is older than the setting. */
  pipeline?: PipelineSummary | null;
  summary: {
    state: "ok" | "partial";
    degraded: string[];
    not_checked: string[];
  };
}

export interface PipelineSummary {
  mode: "lite" | "pro" | "ultra";
  configured_mode: "lite" | "pro" | "ultra";
  forced_by_env: boolean;
  last_evolve_at: string | null;
  evolve_schedule: { enabled: boolean; time: string } | null;
  evolve_running: number;
  evolve_users: Record<
    string,
    {
      last_run_at: string | null;
      last_error: string | null;
      state: string | null;
    }
  >;
  graph_build: string | null;
  /** SDK features (`memory_features`) whose value differs from the default. */
  features: Record<string, boolean | number>;
  /** The App's display language; null = never chosen or "follow the system". */
  ui_language: string | null;
  /** The SDK prompt language: `auto` (zh/en detection) or the code passed. */
  sdk_language: string;
}

const MODES = new Set(["lite", "pro", "ultra"]);

/** Projection of `pipeline` in `/api/v1/memory-processing`; null if absent or malformed. */
export function describePipeline(value: unknown): PipelineSummary | null {
  if (!value || typeof value !== "object") return null;
  const pipeline = (value as { pipeline?: unknown }).pipeline;
  if (!pipeline || typeof pipeline !== "object") return null;
  const item = pipeline as Record<string, unknown>;
  if (
    !MODES.has(String(item.mode)) ||
    !MODES.has(String(item.configured_mode))
  ) {
    return null;
  }
  const evolve =
    item.evolve && typeof item.evolve === "object"
      ? (item.evolve as Record<string, unknown>)
      : {};
  const schedule =
    evolve.schedule && typeof evolve.schedule === "object"
      ? (evolve.schedule as Record<string, unknown>)
      : null;
  const users: PipelineSummary["evolve_users"] = {};
  if (evolve.users && typeof evolve.users === "object") {
    for (const [userId, raw] of Object.entries(
      evolve.users as Record<string, unknown>,
    )) {
      const user =
        raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
      const result =
        user.last_result && typeof user.last_result === "object"
          ? (user.last_result as Record<string, unknown>)
          : {};
      users[userId] = {
        last_run_at:
          typeof user.last_run_at === "string" ? user.last_run_at : null,
        last_error:
          typeof user.last_error === "string" ? user.last_error : null,
        state: typeof result.state === "string" ? result.state : null,
      };
    }
  }
  const features: Record<string, boolean | number> = {};
  if (item.features && typeof item.features === "object") {
    for (const [name, value] of Object.entries(
      item.features as Record<string, unknown>,
    )) {
      if (typeof value === "boolean" || typeof value === "number")
        features[name] = value;
    }
  }
  const graph =
    item.graph_build && typeof item.graph_build === "object"
      ? (item.graph_build as Record<string, unknown>)
      : {};
  return {
    mode: item.mode as PipelineSummary["mode"],
    configured_mode: item.configured_mode as PipelineSummary["mode"],
    forced_by_env: item.forced_by_env === true,
    last_evolve_at:
      typeof item.last_evolve_at === "string" ? item.last_evolve_at : null,
    evolve_schedule:
      schedule &&
      typeof schedule.enabled === "boolean" &&
      typeof schedule.time === "string"
        ? { enabled: schedule.enabled, time: schedule.time }
        : null,
    evolve_running: Array.isArray(evolve.running) ? evolve.running.length : 0,
    evolve_users: users,
    graph_build: typeof graph.state === "string" ? graph.state : null,
    features,
    ui_language: typeof item.ui_language === "string" ? item.ui_language : null,
    // An older Runtime has no setting: it always left the SDK on auto.
    sdk_language:
      typeof item.sdk_language === "string" ? item.sdk_language : "auto",
  };
}

const SECTION_NAMES = [
  "sources",
  "sync",
  "processing",
  "model",
  "memories",
] as const;

const BRIDGE_LABELS: Record<StatusDocument["bridge"]["state"], string> = {
  healthy: "正常",
  stopped: "未运行",
  degraded: "降级",
  untrusted: "身份不可信",
  unknown: "未知",
  not_checked: "未检查",
};

const SECTION_LABELS: Record<string, string> = {
  sources: "来源",
  sync: "同步",
  processing: "生成任务",
  model: "模型",
  memories: "记忆",
};

export async function composeStatusReport(options: {
  dataDir: string;
  runtime: RuntimeStatus;
  background: BackgroundHealth | null;
  reader: CoreReader | null;
  attachFailure?: string;
}): Promise<StatusDocument> {
  const sections: Record<string, Section> = {};
  let pipeline: PipelineSummary | null = null;
  if (options.reader === null) {
    const state: SectionState =
      options.attachFailure === undefined ? "not_checked" : "unavailable";
    const code = options.attachFailure ?? options.runtime.code;
    for (const name of SECTION_NAMES) sections[name] = { state, code };
  } else {
    const reader = options.reader;
    const [sources, sync, processing, model, projection] = await Promise.all([
      reader<unknown>("/api/v1/sources"),
      reader<unknown>("/api/v1/live-sync"),
      reader<unknown>("/api/v1/memory-processing"),
      reader<unknown>("/api/v1/model-profiles"),
      reader<unknown>("/api/v2/home-projection"),
    ]);
    sections.sources = section(sources, validateSources, describeSources);
    sections.sync = section(sync, validateLiveSync, describeSync);
    sections.processing = section(
      processing,
      validateProcessing,
      describeProcessing,
    );
    pipeline =
      processing.state === "ok" ? describePipeline(processing.data) : null;
    sections.model = section(model, validateModelProfiles, describeModel);
    sections.memories = await memorySection(reader, projection, pipeline);
  }
  // A bridge we could not inspect is unknown, not broken and not fine.
  const bridge: StatusDocument["bridge"] =
    options.background === null
      ? { state: "not_checked", code: "bridge_inspection_failed" }
      : {
          state: options.background.state,
          code: options.background.code,
          core: options.background.core,
          broker: options.background.broker,
        };
  const degraded: string[] = SECTION_NAMES.filter((name) =>
    ["denied", "unavailable", "invalid"].includes(
      String(sections[name]?.state),
    ),
  );
  if (bridge.state === "degraded" || bridge.state === "untrusted") {
    degraded.push("bridge");
  }
  const notChecked: string[] = SECTION_NAMES.filter(
    (name) => sections[name]?.state === "not_checked",
  );
  if (bridge.state === "not_checked" || bridge.state === "unknown") {
    notChecked.push("bridge");
  }
  return {
    schema_version: 1,
    product: PRODUCT,
    data_dir: options.dataDir,
    runtime: {
      state: options.runtime.state,
      code: options.runtime.code,
      instance_id: options.runtime.instanceId,
      host: options.runtime.host,
      port: options.runtime.port,
      version: options.runtime.version,
      ...(options.runtime.code.startsWith("sdk_migration_")
        ? {
            log_file: sdkMigrationLogPath(options.dataDir),
            issues_url: MIGRATION_ISSUES_URL,
          }
        : {}),
    },
    bridge,
    sections,
    pipeline,
    summary: {
      state:
        degraded.length === 0 && notChecked.length === 0 ? "ok" : "partial",
      degraded,
      not_checked: notChecked,
    },
  };
}

export function statusExitCode(report: StatusDocument): number {
  if (report.runtime.state === "stopped") return 3;
  if (report.runtime.state !== "running") return 1;
  return report.summary.state === "ok" ? 0 : 1;
}

export function renderStatusText(
  report: StatusDocument,
  heading: string,
): string {
  const lines = [
    `${heading}：${runtimeLine(report)}`,
    ...(report.runtime.log_file
      ? [
          `升级迁移没有完成（错误码：${report.runtime.code}），MemHub 不会启动。`,
          `请把迁移日志附在 GitHub 反馈里发给我们：${report.runtime.log_file}`,
          `反馈地址：${report.runtime.issues_url}`,
        ]
      : []),
    `资料空间：${report.data_dir}`,
    `应用连接：${BRIDGE_LABELS[report.bridge.state]}`,
  ];
  for (const name of SECTION_NAMES) {
    lines.push(
      `${sectionLabel(name, report.sections[name])}：${sectionLine(
        report.sections[name],
      )}`,
    );
  }
  if (report.pipeline) lines.push(`记忆模式：${pipelineLine(report.pipeline)}`);
  const embeddingSwitch = describeEmbeddingSwitchSummary(
    report.sections.processing?.embedding_switch,
  );
  if (embeddingSwitch !== null) lines.push(`向量模型：${embeddingSwitch}`);
  const backoff = modelBackoffLine(report.sections.processing);
  if (backoff !== null) lines.push(backoff);
  const drain = writeDrainLine(report.sections.processing);
  if (drain !== null) lines.push(drain);
  if (report.summary.state === "partial") {
    const notes = [
      report.summary.degraded.length > 0
        ? `${report.summary.degraded.join("、")} 异常`
        : "",
      report.summary.not_checked.length > 0
        ? `${report.summary.not_checked.join("、")} 未能读到`
        : "",
    ].filter((note) => note.length > 0);
    lines.push(`注意：${notes.join("；")}，以上不是完整状态。`);
  }
  lines.push("");
  return lines.join("\n");
}

/** "Model paused" while Core's circuit breaker holds model work back; null
 * otherwise. Nothing to do here: the queue resumes by itself once the model
 * answers again (or after the model is fixed). */
function modelBackoffLine(section: Section | undefined): string | null {
  if (section?.state !== "ok") return null;
  const error = section.llm_backoff_error;
  if (typeof error !== "string" || error.length === 0) return null;
  const until = section.llm_backoff_until;
  return `模型暂停：${error}，${
    typeof until === "string" && until.length > 0
      ? `将于 ${until} 自动重试`
      : "稍后自动重试"
  }`;
}

/** The durable write drain, when it is not simply running: it exited on an
 * error and is coming back (`restarting`), or it is not running at all. */
function writeDrainLine(section: Section | undefined): string | null {
  if (section?.state !== "ok") return null;
  const state = section.write_drain;
  if (state !== "restarting" && state !== "stopped") return null;
  const error = section.write_drain_error;
  const reason =
    typeof error === "string" && error.length > 0 ? `（${error}）` : "";
  return state === "restarting"
    ? `写入队列：异常退出${reason}，正在自动重启`
    : `写入队列：已停止${reason}`;
}

export function pipelineLine(pipeline: PipelineSummary): string {
  const parts: string[] = [pipeline.mode];
  if (pipeline.forced_by_env)
    parts.push("（ONE_MEMORY_GRAPH_STORE 强制 ultra）");
  if (pipeline.mode === "ultra") {
    const schedule = pipeline.evolve_schedule;
    parts.push(
      schedule?.enabled
        ? `，每日 ${schedule.time} 自动进化`
        : "，自动进化已关闭",
    );
    parts.push(
      pipeline.last_evolve_at
        ? `，上次进化 ${pipeline.last_evolve_at}`
        : "，尚未进化",
    );
    if (pipeline.evolve_running > 0)
      parts.push(`，进行中 ${pipeline.evolve_running}`);
  }
  const changed = Object.entries(pipeline.features);
  if (changed.length > 0) {
    parts.push(
      `；SDK 功能：${changed
        .map(
          ([name, value]) =>
            `${name}=${typeof value === "number" ? value : value ? "开" : "关"}`,
        )
        .join("、")}`,
    );
  }
  parts.push(
    `；界面语言 ${pipeline.ui_language ?? "跟随系统"}，SDK 提示语言 ${
      pipeline.sdk_language === "auto"
        ? "auto（自动识别中/英）"
        : pipeline.sdk_language
    }`,
  );
  return parts.join("");
}

function runtimeLine(report: StatusDocument): string {
  if (report.runtime.state !== "running") {
    return report.runtime.state === "stopped" ? "未运行" : "暂时无法确认";
  }
  return `运行中（${report.runtime.host}:${report.runtime.port}${
    report.runtime.version ? `，版本 ${report.runtime.version}` : ""
  }）`;
}

/** Fields that have their own line (or none) instead of a place in the
 * section's one-line summary. */
const SECTION_LINE_OMITTED = new Set([
  "state",
  "code",
  "embedding_switch",
  "llm_backoff_until",
  "llm_backoff_error",
  "write_drain",
  "write_drain_error",
  "content_plane",
  "visible_count",
  "effective_mode",
]);

function sectionLabel(name: string, section: Section | undefined): string {
  return name === "memories" && section?.content_plane === "raw"
    ? "原始对话"
    : SECTION_LABELS[name];
}

function sectionLine(section: Section | undefined): string {
  if (!section) return "未检查";
  if (section.state === "not_checked") return "未检查";
  if (section.state !== "ok") return "暂时不可用，请稍后重试";
  return Object.entries(section)
    .filter(([key]) => !SECTION_LINE_OMITTED.has(key))
    .map(([key, value]) => `${fieldLabel(key)} ${formatValue(value)}`)
    .join(" ");
}

function formatValue(value: unknown): string {
  if (Array.isArray(value))
    return value.length === 0
      ? "无"
      : value.map((item) => formatValue(item)).join("；");
  if (value === null || value === undefined) return "无";
  if (typeof value === "object") {
    return Object.entries(value as Record<string, unknown>)
      .map(([key, item]) => `${fieldLabel(key)} ${formatValue(item)}`)
      .join("/");
  }
  return valueLabel(String(value));
}

function fieldLabel(key: string): string {
  const labels: Record<string, string> = {
    total: "共",
    authorized: "已授权",
    available: "已发现",
    imported_conversations: "会话",
    imported_messages: "原文",
    sources: "来源",
    running: "进行中",
    idle: "空闲",
    degraded: "需留意",
    unavailable: "不可用",
    pending: "待处理",
    fresh_pending: "新对话待处理",
    backlog_pending: "历史待处理",
    outside_window_pending: "稍后处理",
    waiting_for_model: "等待模型",
    suspended: "来源已暂停",
    completed: "已完成",
    failed: "失败",
    blocked: "卡住",
    kind: "类型",
    reason: "原因",
    count: "数量",
    profiles: "配置",
    active: "当前",
    memory_count: "记忆",
    raw_count: "原始对话",
    visible_count: "可见",
    content_plane: "内容层",
    effective_mode: "模式",
  };
  return labels[key] ?? key;
}

function valueLabel(value: string): string {
  const labels: Record<string, string> = {
    extract: "整理对话",
    waiting_for_model: "等待模型",
    input_too_long: "内容过长",
    background_verified: "后台正常",
    additions_only: "只同步新增内容",
  };
  return labels[value] ?? value;
}

function section<T>(
  outcome: ReadOutcome<unknown>,
  validate: (value: unknown) => T | null,
  describe: (data: T) => Record<string, unknown>,
): Section {
  if (outcome.state !== "ok") {
    // A status route that is missing is a Runtime we cannot read, not an empty one.
    return {
      state: outcome.state === "denied" ? "denied" : "unavailable",
      code: outcome.code,
    };
  }
  const data = validate(outcome.data);
  // Answered, but not with something we can read: not a smaller truth.
  return data === null
    ? { state: "invalid", code: "response_invalid" }
    : { state: "ok", ...describe(data) };
}

function describeSources(sources: SourceResponse[]): Record<string, unknown> {
  return {
    total: sources.length,
    authorized: sources.filter((source) => source.authorized).length,
    available: sources.filter((source) => source.available).length,
    imported_conversations: sum(sources, "imported_conversations"),
    imported_messages: sum(sources, "imported_messages"),
  };
}

function describeSync(health: LiveSyncHealth[]): Record<string, unknown> {
  return {
    sources: health.length,
    running: health.filter((item) => item.state === "running").length,
    idle: health.filter((item) => item.state === "idle").length,
    degraded: health.filter((item) => item.state === "degraded").length,
    unavailable: health.filter((item) => item.state === "unavailable").length,
  };
}

function describeProcessing(
  summary: MemoryProcessingSummary,
): Record<string, unknown> {
  return {
    pending: summary.pending,
    fresh_pending: summary.fresh_pending,
    backlog_pending: summary.backlog_pending,
    outside_window_pending: summary.outside_window_pending,
    running: summary.running,
    waiting_for_model: summary.waiting_for_model,
    completed: summary.completed,
    failed: summary.failed,
    blocked: (summary.blocked_stages ?? []).map((block) => ({
      kind: block.kind,
      reason: block.reason,
      count: block.count,
    })),
    suspended: typeof summary.suspended === "number" ? summary.suspended : 0,
    ...llmBackoffFields(summary),
    ...writeDrainFields(summary),
    ...embeddingSwitchField(summary),
  };
}

/** The model circuit breaker: set only while Core pauses model work because
 * the provider keeps failing. Both null when nothing is paused. */
function llmBackoffFields(summary: MemoryProcessingSummary): {
  llm_backoff_until: string | null;
  llm_backoff_error: string | null;
} {
  const until = summary.llm_backoff_until;
  const error = summary.llm_backoff_error;
  return {
    llm_backoff_until:
      typeof until === "string" && until.length <= 64 ? until : null,
    llm_backoff_error:
      typeof error === "string" && /^[a-z_]{1,64}$/.test(error) ? error : null,
  };
}

/** Durable write drain liveness. Null when Core does not report it (older
 * Core, or recovery has not started the drain yet). */
function writeDrainFields(summary: MemoryProcessingSummary): {
  write_drain: "running" | "restarting" | "stopped" | null;
  write_drain_error: string | null;
} {
  const state = summary.write_drain;
  const error = summary.write_drain_error;
  return {
    write_drain:
      state === "running" || state === "restarting" || state === "stopped"
        ? state
        : null,
    write_drain_error:
      typeof error === "string" && /^[a-z_]{1,64}$/.test(error) ? error : null,
  };
}

/** The running switch or merge, projected; absent when none runs. */
function embeddingSwitchField(
  summary: MemoryProcessingSummary,
): Record<string, unknown> {
  const value = (summary as unknown as Record<string, unknown>)
    .embedding_switch;
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  const item = value as Record<string, unknown>;
  const kind = item.kind === "merge" ? "merge" : "switch";
  const state =
    typeof item.state === "string" && /^[a-z_]{1,32}$/.test(item.state)
      ? item.state
      : "unknown";
  const percent =
    typeof item.percent === "number" && Number.isFinite(item.percent)
      ? Math.min(100, Math.max(0, item.percent))
      : 0;
  return { embedding_switch: { kind, state, percent } };
}

/** Projection, not passthrough: a Core that leaks a key must not make us leak. */
function describeModel(profiles: ModelProfile[]): Record<string, unknown> {
  const active = profiles.find((profile) => profile.is_active);
  return {
    profiles: profiles.length,
    active: active
      ? {
          name: active.name,
          model: active.model,
        }
      : null,
  };
}

function describeMemories(summary: SdkHomeSummary): Record<string, unknown> {
  return {
    memory_count: summary.memory_count,
  };
}

async function memorySection(
  reader: CoreReader,
  projection: ReadOutcome<unknown>,
  pipeline: PipelineSummary | null,
): Promise<Section> {
  if (projection.state === "ok") {
    const parsed = validateHomeProjection(projection.data);
    return parsed === null
      ? { state: "invalid", code: "response_invalid" }
      : { state: "ok", ...describeProjection(parsed) };
  }
  if (projection.state !== "not_found") {
    return {
      state: projection.state === "denied" ? "denied" : "unavailable",
      code: projection.code,
    };
  }
  if (pipeline === null) {
    return { state: "unavailable", code: "effective_mode_unavailable" };
  }
  if (pipeline.mode === "lite") {
    const raw = await reader<unknown>("/api/v2/raw", {
      limit: 1,
      offset: 0,
    });
    if (raw.state !== "ok") {
      return {
        state: raw.state === "denied" ? "denied" : "unavailable",
        code: raw.code,
      };
    }
    const total = validateRawTotal(raw.data);
    return total === null
      ? { state: "invalid", code: "response_invalid" }
      : {
          state: "ok",
          content_plane: "raw",
          visible_count: total,
          effective_mode: "lite",
          raw_count: total,
        };
  }
  const legacy = await reader<unknown>("/api/v2/home-summary");
  return section(legacy, validateHomeSummary, (summary) => ({
    content_plane: "memory",
    visible_count: summary.memory_count,
    effective_mode: pipeline.mode,
    ...describeMemories(summary),
  }));
}

function describeProjection(
  projection: HomeProjectionV1,
): Record<string, unknown> {
  return {
    content_plane: projection.capabilities.content_plane,
    visible_count: projection.visible_count,
    effective_mode: projection.effective_mode,
    ...(projection.capabilities.content_plane === "raw"
      ? { raw_count: projection.visible_count }
      : { memory_count: projection.visible_count }),
  };
}

function sum(
  sources: SourceResponse[],
  key: "imported_conversations" | "imported_messages",
): number {
  return sources.reduce((total, source) => total + (source[key] ?? 0), 0);
}
