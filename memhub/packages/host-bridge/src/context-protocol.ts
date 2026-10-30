import type {
  ContextSearchRequest,
  SdkContextSearchResponse,
} from "@one-memory/contracts";
import { HostGrantError, type VerifiedHostIdentity } from "./host-grants.js";

/** What a lookup gets when the host does not ask for a budget of its own.
 *
 * Core's own default is two seconds, which a first lookup after launch
 * overruns: the whole path then comes back as a timeout and the host sees
 * nothing. The host side fills this in so Core's contract stays as it is.
 */
export const HOST_CONTEXT_TIMEOUT_MS = 8000;

/** Return only the invalid field path; never echo content from Core. */
export function contextResponseInvalidField(value: unknown): string | null {
  return isSdkContextSearchResponse(value) ? null : "response";
}

export function isContextSearchRequest(
  value: unknown,
): value is ContextSearchRequest {
  if (
    !record(value) ||
    !only(value, [
      "query",
      "kinds",
      "raw_source_ids",
      "limit",
      "character_budget",
      "timeout_ms",
    ]) ||
    !text(value.query, 1, 2000) ||
    !value.query.trim()
  )
    return false;
  const kinds = value.kinds ?? ["raw", "memory"];
  if (
    (value.kinds !== undefined && value.kinds === null) ||
    !Array.isArray(kinds) ||
    kinds.length < 1 ||
    kinds.length > 2 ||
    !kinds.every((kind) => kind === "raw" || kind === "memory") ||
    new Set(kinds).size !== kinds.length
  )
    return false;
  const sources = value.raw_source_ids;
  if (
    sources !== undefined &&
    sources !== null &&
    (!kinds.includes("raw") ||
      !Array.isArray(sources) ||
      sources.length < 1 ||
      sources.length > 32 ||
      new Set(sources).size !== sources.length ||
      !sources.every(
        (source) =>
          typeof source === "string" &&
          /^[a-z0-9][a-z0-9-]{0,127}$/.test(source),
      ))
  )
    return false;
  return (
    optionalInteger(value.limit, 1, 20) &&
    optionalInteger(value.character_budget, 200, 20000) &&
    optionalInteger(value.timeout_ms, 50, 30000)
  );
}

/** Composition of existing rights, not a new all-content grant. Core checks live sources. */
export function authorizeContext(
  identity: VerifiedHostIdentity,
  request: ContextSearchRequest,
): void {
  const kinds = request.kinds ?? ["raw", "memory"];
  const raw =
    kinds.includes("raw") &&
    identity.allowedTools.includes("om_conversations") &&
    identity.allowedScopes.includes("all-apps") &&
    identity.allowedScopes.some((s) => s.startsWith("source:"));
  const memory = kinds.includes("memory") && hasMemoryAuthority(identity);
  if (
    request.raw_source_ids &&
    (!raw ||
      !request.raw_source_ids.every((s) =>
        identity.allowedScopes.includes(`source:${s}`),
      ))
  )
    throw new HostGrantError("host_grant_scope_denied");
  if (!raw && !memory) throw new HostGrantError("host_grant_tool_denied");
}

function hasMemoryAuthority(identity: VerifiedHostIdentity): boolean {
  return (
    identity.allowedTools.includes("om_recall") &&
    identity.allowedScopes.some(
      (scope) =>
        scope === "all-apps" || /^workspace:v1:[a-f0-9]{64}$/.test(scope),
    )
  );
}

/** Defense in depth for a faulty Core response. Memory content scopes remain Core-owned. */
export function isContextResponseAuthorized(
  response: SdkContextSearchResponse,
  identity: VerifiedHostIdentity,
): boolean {
  return response.items.every((item) =>
    item.kind === "memory"
      ? hasMemoryAuthority(identity)
      : identity.allowedTools.includes("om_conversations") &&
        identity.allowedScopes.includes("all-apps") &&
        identity.allowedScopes.includes(`source:${item.source}`),
  );
}

export function isContextResponseForRequest(
  value: unknown,
  request: ContextSearchRequest,
): value is SdkContextSearchResponse {
  if (!isSdkContextSearchResponse(value)) return false;
  const kinds = request.kinds ?? ["raw", "memory"];
  // Core's default item cap.
  const limit = request.limit ?? 10;
  return (
    value.items.length <= limit &&
    value.used_characters <= (request.character_budget ?? 4000) &&
    value.items.every(
      (item) =>
        kinds.includes(item.kind) &&
        (item.kind !== "raw" ||
          !request.raw_source_ids ||
          request.raw_source_ids.includes(item.source)),
    )
  );
}

export function isSdkContextSearchResponse(
  value: unknown,
): value is SdkContextSearchResponse {
  if (
    !record(value) ||
    !exact(value, [
      "schema_version",
      "request_id",
      "state",
      "items",
      "used_characters",
      "truncated",
      "total_ms",
    ]) ||
    value.schema_version !== 2 ||
    !text(value.request_id, 1, 64) ||
    !member(value.state, ["grounded", "empty", "partial", "unavailable"]) ||
    !integer(value.used_characters, 0, 20000) ||
    typeof value.truncated !== "boolean" ||
    !nonnegative(value.total_ms) ||
    !Array.isArray(value.items) ||
    value.items.length > 20 ||
    !value.items.every(validSdkItem)
  )
    return false;
  const typed = value as unknown as SdkContextSearchResponse;
  if (typed.state === "grounded" && typed.items.length === 0) return false;
  if (
    (typed.state === "empty" || typed.state === "unavailable") &&
    typed.items.length !== 0
  )
    return false;
  return (
    typed.items.reduce((used, item) => used + [...item.content].length, 0) ===
    typed.used_characters
  );
}

function validSdkItem(value: unknown): boolean {
  if (!record(value) || typeof value.truncated !== "boolean") return false;
  if (value.kind === "raw")
    return (
      exact(value, [
        "kind",
        "raw_id",
        "content",
        "source",
        "session_id",
        "memory_at",
        "score",
        "truncated",
      ]) &&
      text(value.raw_id, 1, 512) &&
      text(value.content, 0, 20000) &&
      text(value.source, 1, 128) &&
      text(value.session_id, 1, 512) &&
      (value.memory_at === null || date(value.memory_at)) &&
      score(value.score)
    );
  return (
    value.kind === "memory" &&
    exact(value, [
      "kind",
      "memory_id",
      "content",
      "layer",
      "status",
      "source",
      "updated_at",
      "score",
      "truncated",
    ]) &&
    text(value.memory_id, 1, 512) &&
    text(value.content, 0, 20000) &&
    text(value.layer, 0, 64) &&
    text(value.status, 0, 32) &&
    (value.source === null || text(value.source, 0, 128)) &&
    (value.updated_at === null || date(value.updated_at)) &&
    score(value.score)
  );
}

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function only(value: Record<string, unknown>, keys: string[]): boolean {
  return Object.keys(value).every((key) => keys.includes(key));
}
function exact(value: Record<string, unknown>, keys: string[]): boolean {
  return only(value, keys) && keys.every((key) => Object.hasOwn(value, key));
}
function text(value: unknown, min: number, max: number): value is string {
  return (
    typeof value === "string" &&
    [...value].length >= min &&
    [...value].length <= max
  );
}
function integer(value: unknown, min: number, max: number): boolean {
  return (
    typeof value === "number" &&
    Number.isSafeInteger(value) &&
    value >= min &&
    value <= max
  );
}
function optionalInteger(value: unknown, min: number, max: number): boolean {
  return value === undefined || integer(value, min, max);
}
function nonnegative(value: unknown): boolean {
  return typeof value === "number" && Number.isFinite(value) && value >= 0;
}
function score(value: unknown): boolean {
  return nonnegative(value) && (value as number) <= 1;
}
function member(value: unknown, values: string[]): boolean {
  return typeof value === "string" && values.includes(value);
}
function date(value: unknown): boolean {
  return text(value, 1, 64) && Number.isFinite(Date.parse(value));
}
