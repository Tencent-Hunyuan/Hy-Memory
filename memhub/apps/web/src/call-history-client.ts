import {
  type GuiLocale,
  type MessageKey,
  plainCount,
  translate,
  translateCount,
} from "@one-memory/i18n";
import {
  clearAllCallEventsRequest,
  clearCallEventsRequest,
  getCallHistoryRequest,
  getCallReferencesRequest,
} from "./api";

export interface CallReference {
  kind: "raw" | "memory";
  id: string;
  revision: number;
  source_id: string | null;
}
export interface CallEvent {
  event_id: string;
  occurred_at: string;
  host_id: string | null;
  installation_id: string | null;
  tool: "om_recall" | "om_conversations" | "om_ask" | "om_matters";
  state:
    | "grounded"
    | "empty"
    | "partial"
    | "timed_out"
    | "unavailable"
    | "denied";
  stage: "core_result";
  delivery: "unknown" | "delivered" | "not_delivered";
  adoption: "unknown";
  host_session_id: string | null;
  injection_elapsed_ms: number | null;
  elapsed_ms: number;
  raw_count: number;
  memory_count: number;
  references: CallReference[];
  /** Populated only for Core's separate explicit-feedback events; null here. */
  feedback?: null;
}
export interface CallHistoryPage {
  schema_version: 1;
  available: boolean;
  recording_error: boolean;
  total: number;
  offset: number;
  limit: number;
  items: CallEvent[];
  retention_days: number | null;
  max_events: number | null;
}
export const CALL_HOSTS: Record<string, string> = {
  cursor: "Cursor",
  codex: "Codex",
  "claude-code": "Claude Code",
  workbuddy: "WorkBuddy",
  opencode: "OpenCode",
  "qwen-code": "Qwen Code",
  hermes: "Hermes",
  pi: "Pi",
  "deepseek-harness": "DeepSeek Harness",
};
export function callHostName(id: string | null, locale: GuiLocale = "zh-CN") {
  return id === null
    ? translate(locale, "calls.directHost")
    : Object.hasOwn(CALL_HOSTS, id)
      ? CALL_HOSTS[id]
      : id;
}
const HEADLINES = {
  timed_out: "calls.headline.timedOut",
  unavailable: "calls.headline.unavailable",
  denied: "calls.headline.denied",
  empty: "calls.headline.empty",
} as const satisfies Partial<Record<CallEvent["state"], MessageKey>>;
/** The host defaults to its name in the same locale as the sentence. */
export function callEventHeadline(
  item: CallEvent,
  host?: string,
  locale: GuiLocale = "zh-CN",
) {
  const name = host ?? callHostName(item.host_id, locale);
  const fixed = HEADLINES[item.state as keyof typeof HEADLINES];
  if (fixed) return translate(locale, fixed, { host: name });
  const count = item.raw_count + item.memory_count;
  return translateCount(locale, "calls.headline.found", count, {
    host: name,
    count: plainCount(locale, count),
  });
}
export function callRowHeadline(item: CallEvent, locale: GuiLocale = "zh-CN") {
  return callEventHeadline(item, undefined, locale);
}
export function callRowDetail(item: CallEvent, locale: GuiLocale = "zh-CN") {
  const host = callHostName(item.host_id, locale);
  const key: MessageKey =
    item.state === "timed_out"
      ? "calls.detail.timedOut"
      : item.state === "unavailable"
        ? "calls.detail.unavailable"
        : item.state === "denied"
          ? "calls.detail.denied"
          : item.state === "partial"
            ? item.delivery === "delivered"
              ? "calls.detail.partialDelivered"
              : "calls.detail.partial"
            : item.delivery === "delivered"
              ? "calls.detail.delivered"
              : item.delivery === "not_delivered"
                ? "calls.detail.notDelivered"
                : item.state === "empty"
                  ? "calls.detail.empty"
                  : "calls.detail.success";
  return translate(locale, key, { host });
}
export function callDetailHeadline(
  item: CallEvent,
  host?: string,
  locale: GuiLocale = "zh-CN",
) {
  return callEventHeadline(item, host, locale);
}
export function callDeliveryLabel(
  item: CallEvent,
  locale: GuiLocale = "zh-CN",
) {
  if (item.delivery === "delivered")
    return translate(locale, "calls.detail.delivered", {
      host: callHostName(item.host_id, locale),
    });
  if (item.delivery === "not_delivered")
    return translate(locale, "calls.detail.notDelivered");
  return "";
}
export function callReturnDeliveryLabel(
  item: CallEvent,
  locale: GuiLocale = "zh-CN",
) {
  if (
    item.state === "timed_out" ||
    item.state === "unavailable" ||
    item.state === "denied" ||
    item.state === "partial"
  )
    return translate(locale, `calls.return.${item.state}`, {
      host: callHostName(item.host_id, locale),
    });
  return "";
}
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const OCCURRED_AT =
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/;
function fail(): never {
  throw Error("call_history_unavailable");
}
function object(value: unknown, keys: string[]): Record<string, unknown> {
  if (
    !value ||
    typeof value !== "object" ||
    Array.isArray(value) ||
    Object.keys(value).length !== keys.length ||
    keys.some((k) => !Object.hasOwn(value, k))
  )
    fail();
  return value as Record<string, unknown>;
}
function integer(
  value: unknown,
  min = 0,
  max = Number.MAX_SAFE_INTEGER,
): value is number {
  return (
    typeof value === "number" &&
    Number.isSafeInteger(value) &&
    value >= min &&
    value <= max
  );
}
function id(value: unknown): value is string {
  return typeof value === "string" && UUID.test(value);
}
export async function getCallHistory(
  input: { limit?: number; offset?: number; hostId?: string } = {},
): Promise<CallHistoryPage> {
  const limit = input.limit ?? 20,
    offset = input.offset ?? 0;
  if (
    !integer(limit, 1, 100) ||
    !integer(offset, 0, 100000) ||
    (input.hostId !== undefined && !Object.hasOwn(CALL_HOSTS, input.hostId))
  )
    fail();
  const raw = await getCallHistoryRequest({
    limit,
    offset,
    ...(input.hostId ? { host_id: input.hostId } : {}),
  });
  const page = object(raw, [
    "schema_version",
    "available",
    "recording_error",
    "total",
    "offset",
    "limit",
    "items",
    "retention_days",
    "max_events",
  ]);
  if (
    page.schema_version !== 1 ||
    typeof page.available !== "boolean" ||
    typeof page.recording_error !== "boolean" ||
    !integer(page.total) ||
    page.offset !== offset ||
    page.limit !== limit ||
    !Array.isArray(page.items) ||
    page.items.length > limit
  )
    fail();
  if (page.available) {
    if (
      !integer(page.retention_days, 1, 365) ||
      !integer(page.max_events, 1, 100000)
    )
      fail();
  } else if (
    page.retention_days !== null ||
    page.max_events !== null ||
    page.total !== 0 ||
    page.items.length
  )
    fail();
  const seen = new Set<string>();
  const items = page.items.map((value) => {
    const e = object(value, [
      "event_id",
      "occurred_at",
      "host_id",
      "installation_id",
      "tool",
      "state",
      "stage",
      "delivery",
      "adoption",
      "host_session_id",
      "injection_elapsed_ms",
      "elapsed_ms",
      "raw_count",
      "memory_count",
      "references",
      "feedback",
    ]);
    if (
      !id(e.event_id) ||
      seen.has(e.event_id) ||
      typeof e.occurred_at !== "string" ||
      e.occurred_at.length > 64 ||
      !OCCURRED_AT.test(e.occurred_at) ||
      !Number.isFinite(Date.parse(e.occurred_at)) ||
      (e.host_id !== null &&
        (typeof e.host_id !== "string" ||
          !Object.hasOwn(CALL_HOSTS, e.host_id))) ||
      (input.hostId !== undefined && e.host_id !== input.hostId) ||
      (e.installation_id !== null && !id(e.installation_id)) ||
      !["om_recall", "om_conversations", "om_ask", "om_matters"].includes(
        e.tool as string,
      ) ||
      ![
        "grounded",
        "empty",
        "partial",
        "timed_out",
        "unavailable",
        "denied",
      ].includes(e.state as string) ||
      e.stage !== "core_result" ||
      !["unknown", "delivered", "not_delivered"].includes(
        e.delivery as string,
      ) ||
      e.adoption !== "unknown" ||
      (e.host_session_id !== null &&
        (typeof e.host_session_id !== "string" ||
          !e.host_session_id.length ||
          e.host_session_id.length > 512 ||
          /\p{C}/u.test(e.host_session_id))) ||
      (e.injection_elapsed_ms !== null &&
        (typeof e.injection_elapsed_ms !== "number" ||
          !Number.isFinite(e.injection_elapsed_ms) ||
          e.injection_elapsed_ms < 0)) ||
      typeof e.elapsed_ms !== "number" ||
      !Number.isFinite(e.elapsed_ms) ||
      e.elapsed_ms < 0 ||
      !integer(e.raw_count, 0, 20) ||
      !integer(e.memory_count, 0, 20) ||
      (e.raw_count as number) + (e.memory_count as number) > 20 ||
      !Array.isArray(e.references) ||
      e.references.length > 128
    )
      fail();
    // Feedback events are a separate Core surface the UI does not render yet.
    // A populated label must not be silently dropped as if it were a normal
    // result, so refuse anything other than the null emitted for core results.
    if (e.feedback !== null) fail();
    seen.add(e.event_id);
    const references = e.references.map((ref) => {
      const r = object(ref, ["kind", "id", "revision", "source_id"]);
      if (
        !["raw", "memory"].includes(r.kind as string) ||
        typeof r.id !== "string" ||
        !r.id.length ||
        r.id.length > 512 ||
        /\p{C}/u.test(r.id) ||
        !integer(r.revision, 1) ||
        (r.source_id !== null &&
          (typeof r.source_id !== "string" ||
            !/^[a-z0-9][a-z0-9-]{0,127}$/.test(r.source_id)))
      )
        fail();
      return r as unknown as CallReference;
    });
    if (
      references.some(
        (reference) =>
          (reference.kind === "raw" ? e.raw_count : e.memory_count) === 0,
      )
    )
      fail();
    return { ...e, references } as unknown as CallEvent;
  });
  if (items.length > page.total) fail();
  return { ...page, items } as unknown as CallHistoryPage;
}

export async function clearCallEvents(
  ids: string[],
  traceId?: string,
): Promise<void> {
  if (
    !Array.isArray(ids) ||
    !ids.length ||
    ids.length > 100 ||
    ids.some((value) => !id(value)) ||
    new Set(ids).size !== ids.length
  )
    fail();
  const response = object(await clearCallEventsRequest(ids, traceId), [
    "removed",
  ]);
  if (!integer(response.removed, 0, ids.length)) fail();
}

export async function clearAllCallEvents(traceId?: string): Promise<void> {
  const response = object(await clearAllCallEventsRequest(traceId), [
    "removed",
  ]);
  if (!integer(response.removed, 0)) fail();
}

export interface ResolvedCallReference extends CallReference {
  available: boolean;
  title: string | null;
  reason: "changed" | "unavailable" | null;
}
export async function getCallReferences(
  event: CallEvent,
): Promise<ResolvedCallReference[]> {
  if (!id(event.event_id)) fail();
  const response = await getCallReferencesRequest(event.event_id);
  if (!Array.isArray(response) || response.length !== event.references.length)
    fail();
  return response.map((value, index) => {
    const r = object(value, [
      "kind",
      "id",
      "revision",
      "source_id",
      "available",
      "title",
      "reason",
    ]);
    const expected = event.references[index];
    if (
      r.kind !== expected.kind ||
      r.id !== expected.id ||
      r.revision !== expected.revision ||
      r.source_id !== expected.source_id ||
      typeof r.available !== "boolean"
    )
      fail();
    if (r.available) {
      if (
        typeof r.title !== "string" ||
        [...r.title].length > 120 ||
        r.reason !== null
      )
        fail();
    } else if (
      r.title !== null ||
      !["changed", "unavailable"].includes(r.reason as string)
    )
      fail();
    return r as unknown as ResolvedCallReference;
  });
}
