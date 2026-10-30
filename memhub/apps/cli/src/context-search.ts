import type { CoreContextQuery, ReadOutcome } from "./core-client.js";
import { PRODUCT } from "./version.js";

export const CONTEXT_QUERY_MAX_CHARACTERS = 2_000;
const CONTEXT_KINDS = ["raw", "memory"] as const;
export interface ContextSearchInput {
  query: string;
  kinds: Array<(typeof CONTEXT_KINDS)[number]>;
  raw_source_ids?: string[];
  limit: number;
  character_budget: number;
  timeout_ms: number;
}

/** The CLI's JSON document mirrors Core's SDK-native context schema v2. */
export interface ContextSearchDocument {
  schema_version: 2;
  product: string;
  state:
    | "grounded"
    | "empty"
    | "partial"
    | "unavailable"
    | "denied"
    | "not_found"
    | "rejected"
    | "invalid";
  code?: string;
  request_id?: string;
  items: Array<Record<string, unknown>>;
  used_characters?: number;
  truncated?: boolean;
  total_ms?: number;
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
      schema_version: 2,
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
  if (document.state === "rejected") {
    return `查记忆被拒绝（${document.code}），请检查参数。\n`;
  }
  const lines = [
    document.state === "empty"
      ? "没有查到相关记忆。"
      : document.state === "partial"
        ? "查到以下内容，部分内容还在整理中："
        : "查到以下相关内容：",
  ];
  // A raw hit is an excerpt of an original conversation, not a distilled
  // memory; say which one each line is so it is not read as a conclusion.
  for (const item of document.items) {
    const label =
      item.kind === "raw"
        ? `[原始对话摘录 · ${String(item.source)}]`
        : "[记忆]";
    lines.push(`- ${label} ${String(item.content)}`);
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
    response?.schema_version !== 2 ||
    !oneOf(response.state, ["grounded", "empty", "partial", "unavailable"]) ||
    !boundedText(response.request_id, 64) ||
    !Array.isArray(response.items) ||
    response.items.length > request.limit ||
    !integer(response.used_characters, 0, request.character_budget) ||
    typeof response.truncated !== "boolean" ||
    !number(response.total_ms, 0)
  ) {
    return null;
  }
  const requested = new Set(request.kinds);
  const items: Array<Record<string, unknown>> = [];
  let characters = 0;
  for (const itemValue of response.items) {
    const item = validateItem(itemValue, request);
    if (!item || !requested.has(item.kind as "raw" | "memory")) return null;
    items.push(item);
    characters += characterCount(item.content);
  }
  if (characters !== response.used_characters) return null;
  if (response.state === "empty" && items.length !== 0) return null;
  return {
    schema_version: 2,
    product: PRODUCT,
    state: response.state,
    request_id: response.request_id as string,
    items,
    used_characters: response.used_characters as number,
    truncated: response.truncated,
    total_ms: response.total_ms as number,
  };
}

function validateItem(
  value: unknown,
  request: ContextSearchInput,
): Record<string, unknown> | null {
  const item = record(value);
  if (
    !item ||
    !oneOf(item.kind, CONTEXT_KINDS) ||
    boundedText(item.content, 20_000, true) === null ||
    !number(item.score, 0) ||
    (item.score as number) > 1 ||
    typeof item.truncated !== "boolean"
  ) {
    return null;
  }
  if (item.kind === "memory") {
    if (
      !boundedText(item.memory_id, 512) ||
      boundedText(item.layer, 64, true) === null ||
      boundedText(item.status, 32, true) === null ||
      !optionalText(item.source, 128) ||
      !optionalText(item.updated_at, 64)
    ) {
      return null;
    }
    return {
      kind: "memory",
      memory_id: item.memory_id,
      content: item.content,
      layer: item.layer,
      status: item.status,
      source: item.source ?? null,
      updated_at: item.updated_at ?? null,
      score: item.score,
      truncated: item.truncated,
    };
  }
  if (
    !boundedText(item.raw_id, 512) ||
    !boundedText(item.source, 128) ||
    (request.raw_source_ids !== undefined &&
      !request.raw_source_ids.includes(item.source as string)) ||
    !boundedText(item.session_id, 512) ||
    !optionalText(item.memory_at, 64)
  ) {
    return null;
  }
  return {
    kind: "raw",
    raw_id: item.raw_id,
    content: item.content,
    source: item.source,
    session_id: item.session_id,
    memory_at: item.memory_at ?? null,
    score: item.score,
    truncated: item.truncated,
  };
}

function failed(
  outcome: Exclude<ReadOutcome<unknown>, { state: "ok" }>,
): ContextSearchDocument {
  return {
    schema_version: 2,
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

function optionalText(value: unknown, maximum: number): boolean {
  return (
    value === null ||
    value === undefined ||
    boundedText(value, maximum) !== null
  );
}
