import { randomUUID } from "node:crypto";
import type {
  MemoryAddReceipt,
  SdkMemoryPage,
  SdkMemoryRecord,
} from "@one-memory/contracts";
import {
  buildMemoryAddRequest,
  MemoryAddInputError,
  memoryAddStatusPath,
  parseMemoryAddReceipt,
} from "@one-memory/operations";
import { type CoreReader, RESERVED_MEMORY_SEGMENTS } from "./core-client.js";
import type { CoreOperations } from "./core-operations.js";
import { PRODUCT } from "./version.js";

/** Output budget: a long memory is shortened visibly, never dropped silently. */
const CONTENT_BUDGET = 240;

const MEMORY_ID_SHAPE = /^[A-Za-z0-9][\w.-]{0,127}$/;

/** Reserved segments are rejected here too, before a request is even built. */
export function isMemoryId(value: string): boolean {
  return (
    MEMORY_ID_SHAPE.test(value) &&
    !(RESERVED_MEMORY_SEGMENTS as readonly string[]).includes(value)
  );
}

const CONTENT_MAX_CHARACTERS = 100_000;

/** A memory body read from `--content-file`: UTF-8, non-blank, bounded, and
 * free of control characters other than tab and newline. */
export function parseMemoryContent(bytes: Buffer): string | null {
  if (bytes.byteLength > CONTENT_MAX_CHARACTERS * 4 + 2) return null;
  let decoded: string;
  try {
    decoded = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    return null;
  }
  const content = decoded.replace(/\r?\n$/, "");
  return content.trim().length > 0 &&
    [...content].length <= CONTENT_MAX_CHARACTERS &&
    ![...content].some((character) => {
      const code = character.charCodeAt(0);
      return code <= 8 || (code >= 11 && code <= 31) || code === 127;
    })
    ? content
    : null;
}

export const MEMORY_SCENES = ["all", "chat", "working", "coding"] as const;
export type MemoryScene = (typeof MEMORY_SCENES)[number];
export const MEMORY_KINDS = ["chat", "experience", "fact"] as const;
export type MemoryKind = (typeof MEMORY_KINDS)[number];
export const MEMORY_VARIANTS = ["working", "coding"] as const;
export type MemoryVariant = (typeof MEMORY_VARIANTS)[number];

/** Which SDK namespace a memory lives in. A chat memory needs neither; a
 * working/coding experience or fact needs both so Core does not have to guess
 * (an id present in two namespaces is refused as ambiguous). */
export interface MemoryNamespace {
  kind?: MemoryKind;
  variant?: MemoryVariant;
}

export function namespaceParameters(
  namespace: MemoryNamespace,
): Record<string, string> {
  return {
    ...(namespace.kind === undefined ? {} : { kind: namespace.kind }),
    ...(namespace.variant === undefined ? {} : { variant: namespace.variant }),
  };
}

export interface MemoryQuery {
  query?: string;
  status?: string;
  source?: string;
  scene?: MemoryScene;
  offset: number;
  limit: number;
}

/** Every `memories` document is schema 2: list/show share it with
 * add/update/delete, and the v1-era `evidence`/`history` fields are gone. */
export interface MemoryPageDocument {
  schema_version: 2;
  product: string;
  page: Record<string, unknown>;
  memories: Record<string, unknown>[];
}

export interface MemoryDetailDocument {
  schema_version: 2;
  product: string;
  memory: Record<string, unknown>;
}

export async function readMemoryPage(
  reader: CoreReader,
  query: MemoryQuery,
): Promise<MemoryPageDocument> {
  const parameters: Record<string, string | number> = {
    offset: query.offset,
    limit: query.limit,
  };
  if (query.status !== undefined) parameters.statuses = query.status;
  if (query.source !== undefined) parameters.source = query.source;
  if (query.query !== undefined) parameters.query = query.query;
  if (query.scene !== undefined && query.scene !== "all") {
    parameters.scene = query.scene;
  }
  const outcome = await reader<unknown>("/api/v2/memory-library", parameters);
  if (outcome.state !== "ok") {
    return {
      schema_version: 2,
      product: PRODUCT,
      page: { state: outcome.state, code: outcome.code },
      memories: [],
    };
  }
  const page = sdkMemoryPage(outcome.data);
  if (page === null) {
    return {
      schema_version: 2,
      product: PRODUCT,
      page: { state: "invalid", code: "response_invalid" },
      memories: [],
    };
  }
  const items = page.memories;
  return {
    schema_version: 2,
    product: PRODUCT,
    page: {
      state: "ok",
      scene: query.scene ?? "all",
      ...(query.query === undefined ? {} : { query: query.query }),
      total: page.total,
      offset: page.offset,
      limit: page.limit,
      returned: items.length,
      has_more: page.offset + items.length < page.total,
      // A keyword search only looks among this many most relevant memories.
      ...(typeof page.search_window === "number"
        ? { search_window: page.search_window }
        : {}),
    },
    memories: items.map((item) => describeMemory(item)),
  };
}

export async function readMemoryDetail(
  reader: CoreReader,
  memoryId: string,
  namespace: MemoryNamespace = {},
): Promise<MemoryDetailDocument> {
  const parameters = namespaceParameters(namespace);
  const outcome = await reader<unknown>(
    `/api/v2/memories/${memoryId}`,
    Object.keys(parameters).length > 0 ? parameters : undefined,
  );
  if (outcome.state !== "ok") {
    return {
      schema_version: 2,
      product: PRODUCT,
      memory: { state: outcome.state, code: outcome.code },
    };
  }
  const memory = sdkMemory(outcome.data);
  if (memory === null || memory.memory_id !== memoryId) {
    return {
      schema_version: 2,
      product: PRODUCT,
      memory: { state: "invalid", code: "response_invalid" },
    };
  }
  return {
    schema_version: 2,
    product: PRODUCT,
    memory: { state: "ok", ...describeMemory(memory) },
  };
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
/** The same bound the app's 添加记忆 box applies. */
export const MEMORY_ADD_MAX_CHARACTERS = 12_000;

export interface MemoryAddDocument {
  schema_version: 2;
  product: string;
  command: "memories add" | "memories add-status";
  state: "accepted" | "rejected" | "unknown" | "not_found" | "invalid";
  request_id: string;
  code?: string;
  receipt?: Record<string, unknown>;
}

export function isRequestId(value: string): boolean {
  return UUID.test(value);
}

/** A memory text: non-blank, bounded, no control characters except tab and
 * newline. */
export function validMemoryText(text: string): boolean {
  return (
    text.trim().length > 0 &&
    [...text].length <= MEMORY_ADD_MAX_CHARACTERS &&
    ![...text].some((character) => {
      const code = character.charCodeAt(0);
      return code <= 8 || (code >= 11 && code <= 31) || code === 127;
    })
  );
}

function addReceipt(receipt: MemoryAddReceipt): Record<string, unknown> {
  return {
    evidence_id: receipt.evidence_id,
    state: receipt.state,
    created_at: receipt.created_at,
    task_id: receipt.task_id,
    memory_ids: receipt.memory_ids,
    error: receipt.error,
  };
}

/**
 * Add one memory directly, the same request as the app's 添加记忆.
 *
 * `requestId` is Core's idempotency key: retrying with the same id and text
 * returns the first receipt; the same id with other text is refused.
 */
export async function addMemory(
  operations: CoreOperations,
  text: string,
  requestId: string = randomUUID(),
): Promise<MemoryAddDocument> {
  const base = {
    schema_version: 2 as const,
    product: PRODUCT,
    command: "memories add" as const,
    request_id: requestId,
  };
  if (!validMemoryText(text) || !UUID.test(requestId)) {
    return { ...base, state: "invalid", code: "memory_add_invalid" };
  }
  let request: ReturnType<typeof buildMemoryAddRequest>;
  try {
    request = buildMemoryAddRequest({
      request_id: requestId,
      text,
      scope: "all-apps",
    });
  } catch (error) {
    if (error instanceof MemoryAddInputError) {
      return { ...base, state: "invalid", code: "memory_add_invalid" };
    }
    throw error;
  }
  const result = await operations.post<unknown>(request.path, request.body);
  if (result.state !== "committed") {
    return { ...base, state: result.state, code: result.code };
  }
  const receipt = parseMemoryAddReceipt(result.data);
  return receipt === null
    ? { ...base, state: "unknown", code: "memory_add_receipt_invalid" }
    : { ...base, state: "accepted", receipt: addReceipt(receipt) };
}

/** Read how one addition is doing, by the request id it was submitted with. */
export async function readMemoryAddStatus(
  reader: CoreReader,
  requestId: string,
): Promise<MemoryAddDocument> {
  const base = {
    schema_version: 2 as const,
    product: PRODUCT,
    command: "memories add-status" as const,
    request_id: requestId,
  };
  if (!UUID.test(requestId)) {
    return { ...base, state: "invalid", code: "invalid_request_id" };
  }
  const outcome = await reader<unknown>(memoryAddStatusPath(requestId));
  if (outcome.state === "not_found") {
    return { ...base, state: "not_found", code: "memory_add_not_found" };
  }
  if (outcome.state !== "ok") {
    return {
      ...base,
      state: outcome.state === "rejected" ? "rejected" : "unknown",
      code: outcome.code,
    };
  }
  const receipt = parseMemoryAddReceipt(outcome.data);
  return receipt === null
    ? { ...base, state: "unknown", code: "memory_add_receipt_invalid" }
    : { ...base, state: "accepted", receipt: addReceipt(receipt) };
}

export function memoryAddExitCode(document: MemoryAddDocument): number {
  return document.state === "accepted" ? 0 : 1;
}

export function renderMemoryAddText(document: MemoryAddDocument): string {
  if (document.state === "accepted" && document.receipt) {
    return [
      `request_id: ${document.request_id}`,
      `evidence_id: ${document.receipt.evidence_id}`,
      `state: ${document.receipt.state}`,
      ...((document.receipt.memory_ids as string[]).length > 0
        ? [`memory_ids: ${(document.receipt.memory_ids as string[]).join(",")}`]
        : []),
      document.command === "memories add"
        ? `查询进度：memhub memories add-status ${document.request_id}；重试请带上同一个 --request-id，不会重复添加。`
        : "",
      "",
    ]
      .filter((line, index, all) => line !== "" || index === all.length - 1)
      .join("\n");
  }
  if (document.state === "not_found") {
    return `未找到该 request id：${document.request_id}\n`;
  }
  return [
    `添加记忆未完成：${document.state}（${document.code}）`,
    document.state === "unknown"
      ? `结果未知；可以用同一个 --request-id ${document.request_id} 安全重试。`
      : "",
    "",
  ]
    .filter((line, index, all) => line !== "" || index === all.length - 1)
    .join("\n");
}

export function renderMemoryPageText(document: MemoryPageDocument): string {
  const page = document.page;
  if (page.state !== "ok") {
    if (page.state === "unavailable") {
      return "后台正忙，记忆列表这次没查完，不代表没有记忆，请稍后再试\n";
    }
    if (page.state === "rejected") {
      return `记忆查询被拒绝（${page.code}），请检查参数。\n`;
    }
    return `记忆查询未完成：${page.state}（${page.code}）\n`;
  }
  const lines = [
    `共 ${page.total} 条，本页 ${page.returned} 条（offset=${page.offset} limit=${page.limit}）${
      page.has_more ? "，还有更多，请用 --offset 继续" : ""
    }`,
  ];
  if (typeof page.search_window === "number") {
    lines.push(`（搜索只在最相关的 ${page.search_window} 条里匹配关键词）`);
  }
  for (const memory of document.memories) {
    const namespace =
      memory.kind === "chat"
        ? ""
        : ` kind=${memory.kind} variant=${memory.variant}`;
    lines.push(
      `- ${memory.memory_id}  [${memory.scene}] ${memory.status}/${memory.layer}  source=${memory.source ?? "未知"}${namespace}`,
      `  ${memory.content}${memory.content_truncated ? ` …（共 ${memory.content_characters} 字）` : ""}`,
    );
  }
  if (document.memories.length === 0) lines.push("（没有符合条件的记忆）");
  lines.push("");
  return lines.join("\n");
}

export function renderMemoryDetailText(document: MemoryDetailDocument): string {
  const memory = document.memory;
  if (memory.state !== "ok") {
    if (memory.state === "unavailable") {
      return "后台正忙，记忆详情这次没查完，不代表没有这条记忆，请稍后再试\n";
    }
    if (memory.code === "memory_id_ambiguous") {
      return "这个 ID 同时存在于多个场景；请加 --kind（以及 --variant）指明是哪一条。\n";
    }
    return `记忆读取未完成：${memory.state}（${memory.code}）\n`;
  }
  const lines = [
    `记忆 ${memory.memory_id}`,
    `场景：${memory.scene}  类型：${memory.kind}${memory.variant ? `（${memory.variant}）` : ""}`,
    `状态：${memory.status}  层：${memory.layer}  来源：${memory.source ?? "未知"}`,
    `owner：${memory.owner ?? "未知"}  latest：${memory.is_latest}`,
    `raw IDs：${(memory.source_raw_ids as string[]).join(",") || "无"}`,
    "",
    String(memory.content),
    memory.content_truncated ? `…（共 ${memory.content_characters} 字）` : "",
    "",
    "SDK memory 数据不在 MemHub 中保留 Evidence/版本副本。",
  ];
  lines.push("");
  return lines.join("\n");
}

function describeMemory(memory: SdkMemoryRecord): Record<string, unknown> {
  const budgeted = budget(memory.content ?? "");
  return {
    memory_id: memory.memory_id,
    // Pre-2.0.6 Core listed chat memories only and named no scene.
    scene: memory.scene ?? "chat",
    kind: memory.kind ?? "chat",
    variant: memory.variant ?? null,
    status: memory.status,
    layer: memory.layer,
    is_latest: memory.is_latest,
    owner: memory.owner,
    source: memory.source,
    source_raw_ids: memory.source_raw_ids,
    supersedes: memory.supersedes,
    superseded_by: memory.superseded_by,
    tags: memory.tags ?? [],
    memory_at: memory.memory_at,
    gmt_created: memory.gmt_created,
    ...(memory.task === undefined || memory.task === null
      ? {}
      : { task: memory.task }),
    content: budgeted.text,
    content_truncated: budgeted.truncated,
    content_characters: budgeted.characters,
  };
}

function sdkMemoryPage(value: unknown): SdkMemoryPage | null {
  if (!record(value) || !Array.isArray(value.memories)) return null;
  const memories = value.memories.map(sdkMemory);
  if (memories.some((item) => item === null)) return null;
  if (!integer(value.total) || !integer(value.limit) || !integer(value.offset))
    return null;
  return {
    memories: memories as SdkMemoryRecord[],
    total: value.total,
    limit: value.limit,
    offset: value.offset,
    ...(integer(value.search_window)
      ? { search_window: value.search_window }
      : {}),
  };
}

function sdkMemory(value: unknown): SdkMemoryRecord | null {
  if (
    !record(value) ||
    !text(value.memory_id) ||
    typeof value.content !== "string" ||
    !text(value.layer) ||
    !text(value.status) ||
    typeof value.is_latest !== "boolean" ||
    !strings(value.supersedes) ||
    !strings(value.superseded_by) ||
    !strings(value.tags) ||
    !strings(value.source_raw_ids) ||
    // Working/coding memories may carry an empty user or agent id.
    typeof value.user_id !== "string" ||
    typeof value.agent_id !== "string"
  )
    return null;
  return value as unknown as SdkMemoryRecord;
}

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function text(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

function strings(value: unknown): value is string[] {
  return (
    Array.isArray(value) && value.every((item) => typeof item === "string")
  );
}

function integer(value: unknown): value is number {
  return Number.isInteger(value) && Number(value) >= 0;
}

function budget(value: string): {
  text: string;
  truncated: boolean;
  characters: number;
} {
  const points = [...value];
  return points.length <= CONTENT_BUDGET
    ? { text: value, truncated: false, characters: points.length }
    : {
        text: points.slice(0, CONTENT_BUDGET).join(""),
        truncated: true,
        characters: points.length,
      };
}
