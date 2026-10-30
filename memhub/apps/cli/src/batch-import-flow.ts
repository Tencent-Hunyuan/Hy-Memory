/** A19: one-shot connect/import of every *detected* source
 * (`sources connect --all-detected --import`).
 *
 * The engine already takes many sources in a single `POST /api/v1/sources/connect`
 * (`source_ids` is a 1..32 tuple) and answers per source. The CLI used to reject
 * more than one id before ever calling it, which is exactly what a CVM user with
 * no App hit: they had to name every source by hand. This module enumerates the
 * `found` sources, calls the batch endpoint once, and reports one row per source
 * so a partial success is visible instead of being swallowed.
 *
 * Per the C1 contract (`docs/contracts/a19-batch-scan-import-contract.md`):
 * a batch call, never a per-source loop that stops at the first failure;
 * `request_id` is the idempotency key; a poll of the job rows supplies
 * discovered/stored/errors.
 */

import { randomUUID } from "node:crypto";
import type { ImportJob } from "@one-memory/contracts";
import { buildSourceImportRequest } from "@one-memory/operations";
import type { CoreReader } from "./core-client.js";
import type { CoreOperations } from "./core-operations.js";
import {
  type SourceConnectionCandidate,
  validateSourceConnectionCandidates,
} from "./core-responses.js";
import { PRODUCT } from "./version.js";

export interface BatchSourceRow {
  source_id: string;
  status: "imported" | "already_importing" | "failed" | "not_found";
  discovered: number | null;
  stored: number | null;
  errors: number | null;
  job_id: string | null;
  failure_reason: string | null;
}

export interface BatchScanDocument {
  schema_version: 1;
  product: string;
  command: "sources connect";
  state: "committed" | "partial" | "rejected" | "unknown" | "no_sources";
  request_id: string | null;
  accepted_source_ids: string[];
  sources: BatchSourceRow[];
  /** `location_state != found` rows are listed so the user can see what was skipped. */
  not_found_source_ids: string[];
  /** Detected sources whose location comes from an environment override and
   * that were left out because `--confirm-overrides` was not given. */
  override_required_source_ids?: string[];
  code?: string;
}

const SOURCE_LIST_LIMIT = 200;

/** Detected, connectable sources only — `location_state == found` with a root. */
export function detectedSourceIds(
  candidates: readonly SourceConnectionCandidate[],
): string[] {
  return candidates.map((candidate) => candidate.source_id);
}

export async function planBatchImport(reader: CoreReader): Promise<
  | {
      state: "preview";
      candidates: SourceConnectionCandidate[];
      rows: BatchSourceRow[];
      notFound: string[];
    }
  | {
      state: "rejected" | "unknown" | "no_sources";
      code: string;
      notFound: string[];
    }
> {
  const outcome = await reader<unknown>("/api/v1/sources");
  if (outcome.state !== "ok") {
    return {
      state: outcome.state === "unavailable" ? "unknown" : "rejected",
      code: outcome.code,
      notFound: [],
    };
  }
  const catalog = outcome.data;
  if (!Array.isArray(catalog) || catalog.length > SOURCE_LIST_LIMIT) {
    return {
      state: "rejected",
      code: "sources_response_invalid",
      notFound: [],
    };
  }
  const candidates = validateSourceConnectionCandidates(catalog);
  if (!candidates) {
    return { state: "rejected", code: "source_response_invalid", notFound: [] };
  }
  const notFound = catalog
    .map((item) =>
      item && typeof item === "object"
        ? (item as Record<string, unknown>).source_id
        : undefined,
    )
    .filter((id): id is string => typeof id === "string")
    .filter((id) => !candidates.some((c) => c.source_id === id));
  if (candidates.length === 0) {
    return { state: "no_sources", code: "no_detected_sources", notFound };
  }
  return {
    state: "preview",
    candidates,
    rows: candidates.map((candidate) => ({
      source_id: candidate.source_id,
      status: "imported",
      discovered: null,
      stored: null,
      errors: null,
      job_id: null,
      failure_reason: null,
    })),
    notFound,
  };
}

/** Run the batch connect once and fold the per-source receipt into rows. */
export async function executeBatchImport(
  detected: readonly SourceConnectionCandidate[],
  reader: CoreReader,
  operations: CoreOperations,
  notFound: readonly string[],
  options: { confirmOverrides: boolean },
): Promise<BatchScanDocument> {
  const requestId = randomUUID();
  // An environment-override location is a choice the user has to confirm:
  // without `--confirm-overrides` those sources are left out and named.
  const skipped = options.confirmOverrides
    ? []
    : detected
        .filter((candidate) => candidate.requires_override_confirmation)
        .map((candidate) => candidate.source_id);
  const overrideFields =
    skipped.length > 0 ? { override_required_source_ids: skipped } : {};
  const candidates = detected.filter(
    (candidate) => !skipped.includes(candidate.source_id),
  );
  if (candidates.length === 0) {
    return {
      schema_version: 1,
      product: PRODUCT,
      command: "sources connect",
      state: "rejected",
      request_id: null,
      accepted_source_ids: [],
      sources: [],
      not_found_source_ids: [...notFound],
      ...overrideFields,
      code: "environment_override_confirmation_required",
    };
  }
  // Same shared builder as the single-source import and the Web console, so a
  // batch connect cannot drift from either.
  const request = buildSourceImportRequest({
    sourceIds: candidates.map((candidate) => candidate.source_id),
    sourceRoots: Object.fromEntries(
      candidates.map((candidate) => [
        candidate.source_id,
        candidate.candidate_root_id,
      ]),
    ),
    sourceCandidateRevisions: Object.fromEntries(
      candidates.flatMap((candidate) =>
        candidate.candidate_revision === undefined
          ? []
          : [[candidate.source_id, candidate.candidate_revision]],
      ),
    ),
    confirmEnvironmentOverrides:
      options.confirmOverrides &&
      candidates.some((candidate) => candidate.requires_override_confirmation),
    fullRescan: false,
    onboarding: true,
    enableSync: true,
    requestId,
  });
  const result = await operations.post<unknown>(request.path, request.body);
  if (result.state !== "committed") {
    return {
      schema_version: 1,
      product: PRODUCT,
      command: "sources connect",
      state: result.state,
      request_id: requestId,
      accepted_source_ids: [],
      sources: candidates.map((candidate) => ({
        source_id: candidate.source_id,
        status: "failed",
        discovered: null,
        stored: null,
        errors: null,
        job_id: null,
        failure_reason: result.code,
      })),
      not_found_source_ids: [...notFound],
      ...overrideFields,
      code: result.code,
    };
  }
  const receipt = result.data as Record<string, unknown>;
  const accepted = Array.isArray(receipt.accepted_source_ids)
    ? (receipt.accepted_source_ids as string[])
    : [];
  const failed =
    receipt.failed_sources && typeof receipt.failed_sources === "object"
      ? (receipt.failed_sources as Record<string, string>)
      : {};
  const jobs = Array.isArray(receipt.jobs) ? (receipt.jobs as ImportJob[]) : [];
  const firstSeen = jobs.map((job) => ({
    source_id: job.source_id,
    discovered: job.discovered,
    stored: job.stored,
    errors: job.errors,
    job_id: job.job_id,
  }));
  const rows: BatchSourceRow[] = [];
  const seen = new Set<string>();
  for (const candidate of candidates) {
    const id = candidate.source_id;
    seen.add(id);
    const job = firstSeen.find((entry) => entry.source_id === id) ?? null;
    if (failed[id] !== undefined) {
      const code = failed[id]!;
      rows.push({
        source_id: id,
        // A fresh request refused because an import is already in flight is
        // still a non-committed result. Keep the exact reason visible; only a
        // same-request-ID receipt lookup can establish recovery.
        status: "failed",
        discovered: job?.discovered ?? null,
        stored: job?.stored ?? null,
        errors: job?.errors ?? null,
        job_id: job?.job_id ?? null,
        failure_reason: code,
      });
      continue;
    }
    rows.push({
      source_id: id,
      status: accepted.includes(id) ? "imported" : "failed",
      discovered: job?.discovered ?? null,
      stored: job?.stored ?? null,
      errors: job?.errors ?? null,
      job_id: job?.job_id ?? null,
      failure_reason: accepted.includes(id) ? null : "source_not_accepted",
    });
  }
  for (const id of Object.keys(failed)) {
    if (seen.has(id)) continue;
    rows.push({
      source_id: id,
      status: "failed",
      discovered: null,
      stored: null,
      errors: null,
      job_id: null,
      failure_reason: failed[id]!,
    });
  }
  for (const id of notFound) {
    rows.push({
      source_id: id,
      status: "not_found",
      discovered: null,
      stored: null,
      errors: null,
      job_id: null,
      failure_reason: "location_not_found",
    });
  }
  // The exit code is about the sources we tried (the `found` set). A source
  // that was not found is listed for visibility but must not drag the whole
  // run to "partial" — nothing was attempted for it.
  const attempted = rows.filter((row) => row.status !== "not_found");
  // Any failed source keeps the document from being `committed`.
  const failedCount = attempted.filter((row) => row.status === "failed").length;
  const acceptedCount = attempted.filter(
    (row) => row.status !== "failed",
  ).length;
  return {
    schema_version: 1,
    product: PRODUCT,
    command: "sources connect",
    state:
      failedCount === 0
        ? "committed"
        : acceptedCount === 0
          ? "rejected"
          : "partial",
    ...(failedCount > 0 &&
    acceptedCount === 0 &&
    attempted.every((row) => row.failure_reason === "source_import_busy")
      ? { code: "source_import_all_busy" }
      : {}),
    request_id: requestId,
    accepted_source_ids: accepted,
    sources: rows,
    not_found_source_ids: [...notFound],
    ...overrideFields,
  };
}

export function batchScanExitCode(document: BatchScanDocument): number {
  if (document.state === "committed" || document.state === "no_sources")
    return 0;
  if (document.state === "partial") return 1;
  return 1;
}

export function renderBatchScanText(document: BatchScanDocument): string {
  if (document.state === "no_sources") {
    return "本机没有检测到可接入的来源。\n";
  }
  const lines = document.sources.map((row) => {
    const counters =
      row.discovered === null
        ? ""
        : `  发现=${row.discovered} 存储=${row.stored ?? 0} 错误=${row.errors ?? 0}`;
    const reason =
      row.failure_reason === null ? "" : `  原因=${row.failure_reason}`;
    const label =
      row.status === "already_importing" ? "导入进行中" : row.status;
    return `  ${row.source_id}  ${label}${counters}${reason}`;
  });
  const summary =
    document.state === "committed"
      ? `已完成：${document.accepted_source_ids.length} 个来源`
      : document.state === "partial"
        ? `部分完成：${document.accepted_source_ids.length} 个成功，其余见上方`
        : `未完成：${document.code ?? "unknown"}`;
  const overrides = document.override_required_source_ids ?? [];
  const hint =
    overrides.length > 0
      ? `\n未接入（目录来自环境变量覆盖，需确认）：${overrides.join("、")}\n确认目录无误后加 --confirm-overrides 重新运行。`
      : "";
  return `${summary}\n${lines.join("\n")}${hint}\n`;
}
