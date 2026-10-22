import type { SdkMemoryPage, SdkMemoryRecord } from "@one-memory/contracts";
import { type CoreReader, RESERVED_MEMORY_SEGMENTS } from "./core-client.js";
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

export interface MemoryQuery {
  query?: string;
  status?: string;
  source?: string;
  offset: number;
  limit: number;
}

export interface MemoryPageDocument {
  schema_version: 1;
  product: string;
  page: Record<string, unknown>;
  memories: Record<string, unknown>[];
}

export interface MemoryDetailDocument {
  schema_version: 1;
  product: string;
  memory: Record<string, unknown>;
  evidence: Record<string, unknown>[];
  history: Record<string, unknown>[];
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
  const outcome = await reader<unknown>("/api/v2/memory-library", parameters);
  if (outcome.state !== "ok") {
    return {
      schema_version: 1,
      product: PRODUCT,
      page: { state: outcome.state, code: outcome.code },
      memories: [],
    };
  }
  const page = sdkMemoryPage(outcome.data);
  if (page === null) {
    return {
      schema_version: 1,
      product: PRODUCT,
      page: { state: "invalid", code: "response_invalid" },
      memories: [],
    };
  }
  const items = page.memories;
  return {
    schema_version: 1,
    product: PRODUCT,
    page: {
      state: "ok",
      total: page.total,
      offset: page.offset,
      limit: page.limit,
      returned: items.length,
      has_more: page.offset + items.length < page.total,
    },
    memories: items.map((item) => describeMemory(item)),
  };
}

export async function readMemoryDetail(
  reader: CoreReader,
  memoryId: string,
): Promise<MemoryDetailDocument> {
  const outcome = await reader<unknown>(`/api/v2/memories/${memoryId}`);
  if (outcome.state !== "ok") {
    return {
      schema_version: 1,
      product: PRODUCT,
      memory: { state: outcome.state, code: outcome.code },
      evidence: [],
      history: [],
    };
  }
  const memory = sdkMemory(outcome.data);
  if (memory === null || memory.memory_id !== memoryId) {
    return {
      schema_version: 1,
      product: PRODUCT,
      memory: { state: "invalid", code: "response_invalid" },
      evidence: [],
      history: [],
    };
  }
  return {
    schema_version: 1,
    product: PRODUCT,
    memory: { state: "ok", ...describeMemory(memory) },
    evidence: [],
    history: [],
  };
}

export function renderMemoryPageText(document: MemoryPageDocument): string {
  const page = document.page;
  if (page.state !== "ok") {
    if (page.state === "unavailable") {
      return "后台正忙，记忆列表这次没查完，不代表没有记忆，请稍后再试\n";
    }
    return `记忆查询未完成：${page.state}（${page.code}）\n`;
  }
  const lines = [
    `共 ${page.total} 条，本页 ${page.returned} 条（offset=${page.offset} limit=${page.limit}）${
      page.has_more ? "，还有更多，请用 --offset 继续" : ""
    }`,
  ];
  for (const memory of document.memories) {
    lines.push(
      `- ${memory.memory_id}  ${memory.status}/${memory.layer}  source=${memory.source ?? "未知"}`,
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
    return `记忆读取未完成：${memory.state}（${memory.code}）\n`;
  }
  const lines = [
    `记忆 ${memory.memory_id}`,
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
    !text(value.user_id) ||
    !text(value.agent_id)
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
