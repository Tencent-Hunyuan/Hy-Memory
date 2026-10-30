import type {
  ImportIssue,
  ImportJob,
  LiveSyncHealth,
  MemoryProcessingSummary,
  SourceResponse,
} from "@one-memory/contracts";
import type { CoreReader, ReadOutcome } from "./core-client.js";
import {
  validateImportIssues,
  validateImportJob,
  validateImportJobs,
  validateLiveSync,
  validateProcessing,
  validateSources,
} from "./core-responses.js";
import { PRODUCT } from "./version.js";

type ReadState =
  | { state: "ok" }
  | {
      state: "denied" | "unavailable" | "not_found" | "invalid" | "not_checked";
      code: string;
    };

const SCHEMA_VERSION = 1;

function sourceTotal(
  sources: SourceResponse[],
  key: "imported_conversations" | "imported_messages",
): number {
  return sources.reduce((total, source) => total + source[key], 0);
}

export interface SourcesDocument {
  schema_version: 1;
  product: string;
  state: ReadState["state"];
  code?: string;
  sources: Array<Record<string, unknown>>;
}

export interface SyncDocument {
  schema_version: 1;
  product: string;
  state: "ok" | "partial" | Exclude<ReadState["state"], "ok">;
  code?: string;
  sources: Array<Record<string, unknown>>;
}

export interface ProgressDocument {
  schema_version: 1;
  product: string;
  state: "ok" | "active" | "partial";
  imports: {
    state: ReadState["state"];
    code?: string;
    totals_state: ReadState;
    imported_conversations: number | null;
    imported_messages: number | null;
    jobs: Array<Record<string, unknown>>;
    issues?: Array<Record<string, unknown>>;
    issues_state?: ReadState;
    returned: number;
    validated: number;
    output_truncated: boolean;
  };
  generation: {
    summary_state: ReadState;
    summary: Record<string, unknown> | null;
  };
}

export async function readSources(
  reader: CoreReader,
): Promise<SourcesDocument> {
  const result = validated(
    await reader<unknown>("/api/v1/sources"),
    validateSources,
  );
  if (result.state !== "ok") {
    return {
      schema_version: SCHEMA_VERSION,
      product: PRODUCT,
      ...result,
      sources: [],
    };
  }
  return {
    schema_version: SCHEMA_VERSION,
    product: PRODUCT,
    state: "ok",
    sources: result.data.map(projectSource),
  };
}

export async function readSync(reader: CoreReader): Promise<SyncDocument> {
  const result = validated(
    await reader<unknown>("/api/v1/live-sync"),
    validateLiveSync,
  );
  if (result.state !== "ok") {
    return {
      schema_version: SCHEMA_VERSION,
      product: PRODUCT,
      ...result,
      sources: [],
    };
  }
  const sources = result.data.map(projectSync);
  return {
    schema_version: SCHEMA_VERSION,
    product: PRODUCT,
    state: result.data.some(
      (item) => item.state === "degraded" || item.state === "unavailable",
    )
      ? "partial"
      : "ok",
    sources,
  };
}

export async function readProgress(
  reader: CoreReader,
  options: { jobId?: string; issues: boolean; limit: number },
): Promise<ProgressDocument> {
  const importsPath = options.jobId
    ? `/api/v1/import-jobs/${options.jobId}`
    : "/api/v1/import-jobs";
  // Generation progress comes from `memory-processing` alone: under SDK
  // authority the per-task list (`/api/v1/memory-tasks`) is retired and empty.
  const [importsRead, sourcesRead, processingRead, issuesRead] =
    await Promise.all([
      reader<unknown>(importsPath),
      reader<unknown>("/api/v1/sources"),
      reader<unknown>("/api/v1/memory-processing"),
      options.jobId && options.issues
        ? reader<unknown>(`/api/v1/import-jobs/${options.jobId}/issues`)
        : Promise.resolve(null),
    ]);

  const imports = options.jobId
    ? validated(importsRead, (value) => {
        const job = validateImportJob(value);
        return job && job.job_id === options.jobId ? [job] : null;
      })
    : validated(importsRead, validateImportJobs);
  const processing = validated(processingRead, validateProcessing);
  const sources = validated(sourcesRead, validateSources);
  const issues =
    options.jobId && options.issues && issuesRead
      ? validated(issuesRead, (value) =>
          validateImportIssues(value, options.jobId as string),
        )
      : null;

  const jobs = imports.state === "ok" ? imports.data : [];
  const issueItems = issues?.state === "ok" ? issues.data : [];
  const importState = stateOnly(imports);
  const summaryState = stateOnly(processing);

  const hasReadFailure =
    importState.state !== "ok" ||
    sources.state !== "ok" ||
    summaryState.state !== "ok" ||
    (issues !== null && issues.state !== "ok");
  const hasProblem =
    jobs.some((job) =>
      ["paused", "interrupted", "failed"].includes(job.status),
    ) ||
    (processing.state === "ok" &&
      (processing.data.failed > 0 ||
        processing.data.waiting_for_model > 0 ||
        processing.data.blocked_stages.length > 0)) ||
    issueItems.length > 0;
  const hasActive =
    jobs.some((job) => ["pending", "running"].includes(job.status)) ||
    (processing.state === "ok" &&
      (processing.data.pending > 0 || processing.data.running > 0));

  return {
    schema_version: SCHEMA_VERSION,
    product: PRODUCT,
    state:
      hasReadFailure || hasProblem ? "partial" : hasActive ? "active" : "ok",
    imports: {
      ...importState,
      totals_state: stateOnly(sources),
      imported_conversations:
        sources.state === "ok"
          ? sourceTotal(sources.data, "imported_conversations")
          : null,
      imported_messages:
        sources.state === "ok"
          ? sourceTotal(sources.data, "imported_messages")
          : null,
      jobs: jobs.slice(0, options.limit).map(projectImportJob),
      ...(issues
        ? {
            issues:
              issues.state === "ok"
                ? issues.data.slice(0, options.limit).map(projectImportIssue)
                : [],
            ...(issues.state === "ok"
              ? {}
              : { issues_state: stateOnly(issues) }),
          }
        : {}),
      returned: Math.min(jobs.length, options.limit),
      validated: jobs.length,
      output_truncated:
        jobs.length > options.limit || issueItems.length > options.limit,
    },
    generation: {
      summary_state: summaryState,
      summary:
        processing.state === "ok" ? projectProcessing(processing.data) : null,
    },
  };
}

export function renderSourcesText(document: SourcesDocument): string {
  if (document.state !== "ok") {
    return "暂时无法读取应用连接状态，请确认后台正在运行后重试。\n";
  }
  const lines = [`已发现 ${document.sources.length} 个应用：`];
  for (const source of document.sources) {
    lines.push(
      `- ${source.display_name}：${source.authorized ? "已连接" : source.available ? "可连接" : "未发现"}；处理${source.sync_enabled === true ? "已开启" : "已冻结"}`,
      `  已导入 ${source.imported_conversations} 个会话 · ${source.imported_messages} 条消息`,
    );
  }
  if (document.sources.length === 0) lines.push("（没有来源）");
  lines.push("");
  return lines.join("\n");
}

export function renderSyncText(document: SyncDocument): string {
  if (!["ok", "partial"].includes(document.state)) {
    return `同步状态读取未完成：${document.state}（${document.code}）\n`;
  }
  const lines = [`同步状态：${document.state}`];
  for (const source of document.sources) {
    lines.push(
      `- ${source.source_id}  ${source.state}  模式=${source.mode}  最近错误=${source.last_error_code ?? "无"}`,
    );
  }
  if (document.sources.length === 0) lines.push("（没有同步来源）");
  lines.push("");
  return lines.join("\n");
}

export function renderProgressText(document: ProgressDocument): string {
  const lines = [
    document.state === "ok"
      ? "导入与整理已完成。"
      : document.state === "active"
        ? "正在导入和整理，你可以继续使用。"
        : "部分任务需要处理。",
    document.imports.imported_messages === null
      ? "累计导入：暂时无法读取"
      : `累计导入：${document.imports.imported_conversations} 个会话 · ${document.imports.imported_messages} 条消息`,
    `导入任务：${document.imports.validated} 个`,
  ];
  if (document.imports.issues) {
    lines.push(`需要留意的导入记录：${document.imports.issues.length} 条`);
  }
  lines.push(
    document.generation.summary
      ? `记忆整理：待处理 ${document.generation.summary.pending} · 进行中 ${document.generation.summary.running} · 等待模型 ${document.generation.summary.waiting_for_model} · 失败 ${document.generation.summary.failed}`
      : "记忆整理：暂时无法读取",
  );
  if (document.imports.output_truncated) {
    lines.push("这里只显示汇总；完整记录请使用 --json。");
  }
  lines.push("");
  return lines.join("\n");
}

export function sourceProgressExitCode(
  document: SourcesDocument | SyncDocument | ProgressDocument,
): number {
  return document.state === "ok" || document.state === "active" ? 0 : 1;
}

export function unreadSources(
  state: Exclude<ReadState["state"], "ok">,
  code: string,
): SourcesDocument {
  return {
    schema_version: SCHEMA_VERSION,
    product: PRODUCT,
    state,
    code,
    sources: [],
  };
}

export function unreadSync(
  state: Exclude<ReadState["state"], "ok">,
  code: string,
): SyncDocument {
  return {
    schema_version: SCHEMA_VERSION,
    product: PRODUCT,
    state,
    code,
    sources: [],
  };
}

export function unreadProgress(
  state: Exclude<ReadState["state"], "ok">,
  code: string,
): ProgressDocument {
  const failed = { state, code } as const;
  return {
    schema_version: SCHEMA_VERSION,
    product: PRODUCT,
    state: "partial",
    imports: {
      ...failed,
      totals_state: failed,
      imported_conversations: null,
      imported_messages: null,
      jobs: [],
      returned: 0,
      validated: 0,
      output_truncated: false,
    },
    generation: {
      summary_state: failed,
      summary: null,
    },
  };
}

function validated<T>(
  outcome: ReadOutcome<unknown>,
  validate: (value: unknown) => T | null,
):
  | { state: "ok"; data: T }
  | { state: Exclude<ReadState["state"], "ok">; code: string } {
  if (outcome.state !== "ok") {
    // A Core refusal of the request itself reads as an invalid answer here.
    const state = outcome.state === "rejected" ? "invalid" : outcome.state;
    return { state, code: outcome.code };
  }
  const data = validate(outcome.data);
  return data === null
    ? { state: "invalid", code: "response_invalid" }
    : { state: "ok", data };
}

function stateOnly(result: { state: string; code?: string }): ReadState {
  return result.state === "ok"
    ? { state: "ok" }
    : {
        state: result.state as Exclude<ReadState["state"], "ok">,
        code: result.code ?? "read_failed",
      };
}

function projectSource(source: SourceResponse): Record<string, unknown> {
  return {
    source_id: source.source_id,
    display_name: source.display_name,
    storage_kinds: source.storage_kinds,
    available: source.available,
    authorized: source.authorized,
    detected: source.detected,
    root_label: source.root_label,
    candidate_kind: source.candidate_kind,
    location_state: source.location_state ?? "unknown",
    imported_messages: source.imported_messages,
    imported_conversations: source.imported_conversations,
    capabilities: source.capabilities,
    sync_completeness: source.sync_completeness,
    sync_enabled: source.sync_enabled,
  };
}

function projectSync(health: LiveSyncHealth): Record<string, unknown> {
  return {
    source_id: health.source_id,
    mode: health.mode,
    state: health.state,
    last_job_id: health.last_job_id,
    last_attempt_at: health.last_attempt_at,
    last_success_at: health.last_success_at,
    last_error_code: health.last_error_code,
  };
}

function projectImportJob(job: ImportJob): Record<string, unknown> {
  return {
    job_id: job.job_id,
    source_id: job.source_id,
    operation: job.operation ?? "history",
    status: job.status,
    phase: job.phase,
    discovered: job.discovered,
    discovered_targets: job.discovered_targets,
    emitted: job.emitted,
    stored: job.stored,
    revised: job.revised,
    merged: job.merged,
    skipped: job.skipped,
    errors: job.errors,
    created_at: job.created_at,
    updated_at: job.updated_at,
  };
}

function projectImportIssue(issue: ImportIssue): Record<string, unknown> {
  return {
    issue_id: issue.issue_id,
    job_id: issue.job_id,
    kind: issue.kind,
    conversation_id: issue.conversation_id,
    reason: issue.reason,
    created_at: issue.created_at,
  };
}

function projectProcessing(
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
    blocked_stages: summary.blocked_stages.map((stage) => ({
      kind: stage.kind,
      reason: stage.reason,
      count: stage.count,
    })),
  };
}
