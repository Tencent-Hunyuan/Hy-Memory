import type { CoreContextQuery, ReadOutcome } from "./core-client.js";
import { PRODUCT } from "./version.js";

export const CONTEXT_QUERY_MAX_CHARACTERS = 2_000;
const CONTEXT_KINDS = ["raw", "memory"] as const;
const PATH_STATES = [
  "ok",
  "empty",
  "not_requested",
  "not_authorized",
  "unavailable",
  "degraded",
] as const;
const PATH_REASONS = [
  "not_requested",
  "permission_required",
  "source_revoked",
  "read_unavailable",
  "timeout_budget",
  "evidence_changed",
  "memory_changed",
  "lexical_only",
] as const;
const LIMIT_REASONS = [
  "count_budget",
  "character_budget",
  "timeout_budget",
  "metadata_budget",
] as const;

export interface ContextSearchInput {
  query: string;
  kinds: Array<(typeof CONTEXT_KINDS)[number]>;
  raw_source_ids?: string[];
  limit: number;
  character_budget: number;
  timeout_ms: number;
}

export interface ContextSearchDocument {
  schema_version: 1;
  product: string;
  state:
    | "grounded"
    | "empty"
    | "partial"
    | "unavailable"
    | "denied"
    | "not_found"
    | "invalid";
  code?: string;
  request_id?: string;
  paths?: Record<string, unknown>;
  items: Array<Record<string, unknown>>;
  used_characters?: number;
  truncated?: boolean;
  total_ms?: number;
  adoption?: "unknown";
}

export async function searchContext(
  query: CoreContextQuery,
  input: ContextSearchInput,
): Promise<ContextSearchDocument> {
  const outcome = await query<unknown>(input);
  if (outcome.state !== "ok") return failed(outcome);
  const validated = validateContextResponse(outcome.data, input);
  return (
    validated ?? {
      schema_version: 1,
      product: PRODUCT,
      state: "invalid",
      code: "response_invalid",
      items: [],
    }
  );
}

export function parseContextText(
  bytes: Buffer,
  maxCharacters = CONTEXT_QUERY_MAX_CHARACTERS,
): string | null {
  if (bytes.byteLength > maxCharacters * 4 + 2) return null;
  const text = new TextDecoder("utf-8", { fatal: true });
  let decoded: string;
  try {
    decoded = text.decode(bytes);
  } catch {
    return null;
  }
  const value = decoded.replace(/\r?\n$/, "");
  if (
    value.length < 1 ||
    value.length > maxCharacters ||
    [...value].some((character) => {
      const code = character.charCodeAt(0);
      return code <= 31 || code === 127;
    })
  ) {
    return null;
  }
  return value;
}

export async function readBoundedStdin(): Promise<Buffer | null> {
  const chunks: Buffer[] = [];
  let total = 0;
  const maximum = CONTEXT_QUERY_MAX_CHARACTERS * 4 + 2;
  for await (const value of process.stdin) {
    const chunk = Buffer.isBuffer(value) ? value : Buffer.from(value);
    total += chunk.byteLength;
    if (total > maximum) return null;
    chunks.push(chunk);
  }
  return Buffer.concat(chunks, total);
}

export function contextExitCode(document: ContextSearchDocument): number {
  return ["grounded", "empty", "partial"].includes(document.state) ? 0 : 1;
}

export function renderContextText(document: ContextSearchDocument): string {
  if (document.state === "unavailable") return "暂时无法查记忆，请稍后重试。\n";
  if (document.state === "denied") return "当前应用没有查记忆权限。\n";
  if (document.state === "invalid") return "查记忆返回了无法识别的结果。\n";
  const lines = [
    document.state === "empty"
      ? "没有查到相关记忆。"
      : document.state === "partial"
        ? "查到以下内容，部分内容还在整理中："
        : "查到以下相关内容：",
  ];
  for (const item of document.items) {
    if (item.kind === "memory") {
      lines.push(`- ${String(item.content)}`);
    } else {
      for (const message of item.messages as Array<Record<string, unknown>>) {
        lines.push(`- ${String(message.text)}`);
      }
    }
  }
  if (document.truncated) lines.push("结果已按请求预算截断。");
  return `${lines.join("\n")}\n`;
}

function validateContextResponse(
  value: unknown,
  request: ContextSearchInput,
): ContextSearchDocument | null {
  const response = record(value);
  if (
    response?.schema_version !== 1 ||
    !oneOf(response.state, ["grounded", "empty", "partial", "unavailable"]) ||
    !boundedText(response.request_id, 64) ||
    !record(response.paths) ||
    !Array.isArray(response.items) ||
    response.items.length > request.limit ||
    !integer(response.used_characters, 0, request.character_budget) ||
    typeof response.truncated !== "boolean" ||
    !number(response.total_ms, 0) ||
    response.adoption !== "unknown"
  ) {
    return null;
  }
  const paths = response.paths as Record<string, unknown>;
  const rawPath = validatePath(paths.raw);
  const memoryPath = validatePath(paths.memory);
  if (!rawPath || !memoryPath) return null;
  const requested = new Set(request.kinds);
  if (
    (!requested.has("raw") && rawPath.status !== "not_requested") ||
    (!requested.has("memory") && memoryPath.status !== "not_requested")
  ) {
    return null;
  }
  const items: Array<Record<string, unknown>> = [];
  let characters = 0;
  for (const itemValue of response.items) {
    const item = validateItem(itemValue, request);
    if (!item || !requested.has(item.kind as "raw" | "memory")) return null;
    items.push(item);
    characters +=
      item.kind === "memory"
        ? characterCount(item.content)
        : (item.messages as Array<Record<string, unknown>>).reduce(
            (sum, message) => sum + characterCount(message.text),
            0,
          );
  }
  if (characters !== response.used_characters) return null;
  if (response.state === "empty" && items.length !== 0) return null;
  return {
    schema_version: 1,
    product: PRODUCT,
    state: response.state,
    request_id: response.request_id as string,
    paths: { raw: rawPath, memory: memoryPath },
    items,
    used_characters: response.used_characters as number,
    truncated: response.truncated,
    total_ms: response.total_ms as number,
    adoption: "unknown",
  };
}

function validatePath(value: unknown): Record<string, unknown> | null {
  const path = record(value);
  if (
    !path ||
    !oneOf(path.status, PATH_STATES) ||
    !number(path.elapsed_ms, 0) ||
    !integer(path.returned_count, 0, 20) ||
    typeof path.truncated !== "boolean" ||
    !enumList(path.truncated_by, LIMIT_REASONS) ||
    !(
      path.reason === null ||
      path.reason === undefined ||
      oneOf(path.reason, PATH_REASONS)
    )
  ) {
    return null;
  }
  return {
    status: path.status,
    reason: path.reason ?? null,
    elapsed_ms: path.elapsed_ms,
    returned_count: path.returned_count,
    truncated: path.truncated,
    truncated_by: path.truncated_by,
  };
}

function validateItem(
  value: unknown,
  request: ContextSearchInput,
): Record<string, unknown> | null {
  const item = record(value);
  if (!item || !oneOf(item.kind, CONTEXT_KINDS)) return null;
  if (item.kind === "memory") {
    if (
      !boundedText(item.memory_id, 512) ||
      !integer(item.revision, 1) ||
      boundedText(item.content, 20_000, true) === null ||
      boundedText(item.memory_kind, 64, true) === null ||
      !boundedText(item.updated_at, 64) ||
      !textList(item.evidence_ids, 128, 512) ||
      typeof item.truncated !== "boolean"
    ) {
      return null;
    }
    return {
      kind: "memory",
      memory_id: item.memory_id,
      revision: item.revision,
      content: item.content,
      memory_kind: item.memory_kind,
      updated_at: item.updated_at,
      evidence_ids: item.evidence_ids,
      truncated: item.truncated,
    };
  }
  if (
    !boundedText(item.source_id, 128) ||
    (request.raw_source_ids !== undefined &&
      !request.raw_source_ids.includes(item.source_id as string)) ||
    !boundedText(item.conversation_id, 512) ||
    !boundedText(item.anchor_evidence_id, 512) ||
    !Array.isArray(item.messages) ||
    item.messages.length < 1 ||
    item.messages.length > 3 ||
    typeof item.truncated !== "boolean"
  ) {
    return null;
  }
  const messages = item.messages.map(validateMessage);
  if (messages.some((message) => message === null)) return null;
  return {
    kind: "raw",
    source_id: item.source_id,
    conversation_id: item.conversation_id,
    anchor_evidence_id: item.anchor_evidence_id,
    messages,
    truncated: item.truncated,
  };
}

function validateMessage(value: unknown): Record<string, unknown> | null {
  const message = record(value);
  if (
    !message ||
    !boundedText(message.evidence_id, 512) ||
    !integer(message.revision, 1) ||
    boundedText(message.role, 32, true) === null ||
    boundedText(message.written_by, 32, true) === null ||
    !(
      message.created_at === null ||
      boundedText(message.created_at, 64) !== null
    ) ||
    boundedText(message.text, 20_000, true) === null ||
    typeof message.truncated !== "boolean"
  ) {
    return null;
  }
  return {
    evidence_id: message.evidence_id,
    revision: message.revision,
    role: message.role,
    written_by: message.written_by,
    created_at: message.created_at,
    text: message.text,
    truncated: message.truncated,
  };
}

function failed(
  outcome: Exclude<ReadOutcome<unknown>, { state: "ok" }>,
): ContextSearchDocument {
  return {
    schema_version: 1,
    product: PRODUCT,
    state: outcome.state,
    code: outcome.code,
    items: [],
  };
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

/**
 * Count characters the way Core does. Python's len() counts Unicode code
 * points; String.prototype.length counts UTF-16 code units, so an emoji outside
 * the BMP is one character to Core and two here. Comparing the two totals made
 * every response containing an emoji fail the used_characters cross-check and
 * surface as response_invalid.
 */
function characterCount(value: unknown): number {
  return Array.from(String(value)).length;
}

function boundedText(
  value: unknown,
  maximum: number,
  allowEmpty = false,
): string | null {
  return typeof value === "string" &&
    value.length <= maximum &&
    (allowEmpty || value.length > 0)
    ? value
    : null;
}

function integer(value: unknown, minimum: number, maximum = Infinity): boolean {
  return (
    Number.isSafeInteger(value) &&
    (value as number) >= minimum &&
    (value as number) <= maximum
  );
}

function number(value: unknown, minimum: number): boolean {
  return (
    typeof value === "number" && Number.isFinite(value) && value >= minimum
  );
}

function oneOf<const T extends readonly string[]>(
  value: unknown,
  values: T,
): value is T[number] {
  return typeof value === "string" && values.includes(value as T[number]);
}

function enumList(
  value: unknown,
  values: readonly string[],
): value is string[] {
  return (
    Array.isArray(value) &&
    value.length <= 4 &&
    new Set(value).size === value.length &&
    value.every((item) => oneOf(item, values))
  );
}

function textList(
  value: unknown,
  maximumItems: number,
  maximumText: number,
): value is string[] {
  return (
    Array.isArray(value) &&
    value.length <= maximumItems &&
    new Set(value).size === value.length &&
    value.every((item) => boundedText(item, maximumText) !== null)
  );
}
