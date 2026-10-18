/** Stable, allowlisted failure codes for cross-layer diagnosis.
 *
 * A Desktop action used to log `desktop_action_failed` for every failure, and a
 * host uninstall logged one message for every way it can refuse. Neither says
 * whether the fix is to re-authorize a source, repair a plugin, or recover a
 * half-applied install, which is what issue #29 needed to know.
 *
 * The codes are an allowlist on purpose. The alternative — parsing a domain
 * code back out of a thrown `Error.message` — is a protocol smuggled through
 * free text: any message that happens to match changes the classification, and
 * any message that does not match loses it entirely. Throwing a typed error at
 * the point of failure is the only place the truth is known.
 *
 * This lives in host-bridge rather than runtime-control because both layers
 * throw these and runtime-control already depends on host-bridge.
 */

export const DOMAIN_CODES = [
  "grant_required",
  "source_identity_changed",
  "host_uninstall_modified_managed_value",
  "host_uninstall_unknown_owned_file",
  "host_uninstall_needs_review",
  "host_install_journal_invalid",
  "host_install_recovery_required",
  "host_permission_storage_unsafe",
] as const;

export type DomainCode = (typeof DOMAIN_CODES)[number];

const CODES: ReadonlySet<string> = new Set(DOMAIN_CODES);

export function isDomainCode(value: unknown): value is DomainCode {
  return typeof value === "string" && CODES.has(value);
}

/** A failure the caller classified at the point it happened. */
export class DomainError extends Error {
  readonly code: DomainCode;

  constructor(code: DomainCode, message?: string) {
    super(message ?? code);
    this.name = "DomainError";
    this.code = code;
  }
}

export function isDomainError(value: unknown): value is DomainError {
  return value instanceof DomainError && isDomainCode(value.code);
}

/** The code to log, or null when the failure was never classified.
 *
 * Returning null rather than inventing a code is deliberate: an unclassified
 * error is information, and collapsing it into a guess is how a support bundle
 * starts agreeing with itself instead of with the machine.
 */
export function domainCodeOf(error: unknown): DomainCode | null {
  if (isDomainError(error)) return error.code;
  if (
    error &&
    typeof error === "object" &&
    isDomainCode((error as { code?: unknown }).code)
  )
    return (error as { code: DomainCode }).code;
  return null;
}

const SAFE_STACK_PREFIXES = ["apps/", "packages/", "scripts/"] as const;

function isSafeStackCharacter(character: string): boolean {
  const code = character.charCodeAt(0);
  return (
    (code >= 48 && code <= 57) ||
    (code >= 65 && code <= 90) ||
    (code >= 97 && code <= 122) ||
    "_./-:".includes(character)
  );
}

function isAsciiDigits(value: string): boolean {
  if (!value.length) return false;
  for (const character of value) {
    const code = character.charCodeAt(0);
    if (code < 48 || code > 57) return false;
  }
  return true;
}

/** Extract one repo-relative `path:line:column` in linear time. */
function safeStackLocation(line: string): string | null {
  for (const prefix of SAFE_STACK_PREFIXES) {
    const start = line.indexOf(prefix);
    if (start < 0) continue;
    let end = start;
    while (end < line.length && isSafeStackCharacter(line[end] ?? "")) end += 1;
    const candidate = line.slice(start, end);
    const columnSeparator = candidate.lastIndexOf(":");
    const lineSeparator = candidate.lastIndexOf(":", columnSeparator - 1);
    if (
      lineSeparator > prefix.length &&
      columnSeparator > lineSeparator + 1 &&
      isAsciiDigits(candidate.slice(lineSeparator + 1, columnSeparator)) &&
      isAsciiDigits(candidate.slice(columnSeparator + 1))
    )
      return candidate;
  }
  return null;
}

/** The diagnostic fields for a failure, keeping the code when there is one.
 *
 * Only `error_type` and a repo-relative stack survive. `Error.message` is
 * dropped: host paths, transcript names and user content all end up in
 * messages that were written for a person reading them on screen.
 */
export function diagnosticDomainError(error: unknown): {
  error_type: string;
  error_code?: string;
  safe_stack?: string[];
} {
  const code = domainCodeOf(error);
  if (!(error instanceof Error))
    return code
      ? { error_type: "DomainError", error_code: code }
      : { error_type: "UnknownError" };
  const stack = (error.stack ?? "")
    .split("\n")
    .flatMap((line) => {
      const location = safeStackLocation(line);
      return location ? [location] : [];
    })
    .slice(0, 16);
  return {
    error_type: /^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/.test(error.name)
      ? error.name
      : "Error",
    ...(code ? { error_code: code } : {}),
    ...(stack.length ? { safe_stack: stack } : {}),
  };
}
