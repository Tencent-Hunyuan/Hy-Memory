/** `memhub ui`: exchange a one-time loopback code, then open the system browser.
 *
 * Contract: docs/contracts/c0-ui-and-restore-contract.md (C0, H25).
 * The Core bearer token never reaches the browser. Core mints a single-use,
 * 60s, instance-bound exchange code; the CLI only forwards the returned
 * loopback URL to the OS opener. `GET /ui/login?code=...` converts that code
 * into an HttpOnly/SameSite=Strict session cookie.
 */

import { spawn } from "node:child_process";

export interface UiSessionExchange {
  url: string;
  expiresInSeconds: number;
}

export type UiResult =
  | { state: "opened"; url: string }
  | { state: "blocked"; code: string }
  | { state: "failed"; code: string };

const EXCHANGE_PATH = "/api/v1/ui/session";

export function uiSessionExchangePath(): string {
  return EXCHANGE_PATH;
}

/** True only for an exact loopback `http:` origin.
 *
 * `127.0.0.1`, `[::1]` and `localhost` are loopback; `127.0.0.1.evil.com`
 * and any other scheme are not. The contract's URL is `http://127.0.0.1:<port>`
 * (loopback, not TLS); requiring exactly `http:` keeps us from opening a
 * different origin shape than the one Core minted. Parsing (not prefix
 * matching) is what makes the suffix attack fail.
 */
function isLoopbackHttpUrl(value: string): boolean {
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    return false;
  }
  if (parsed.protocol !== "http:") return false;
  const host = parsed.hostname.toLowerCase();
  return (
    host === "localhost" ||
    host === "127.0.0.1" ||
    host === "[::1]" ||
    host === "::1"
  );
}

/** Validate the Core exchange document; return null when it does not match the
 * contract, so the caller reports a stable failure instead of opening a URL we
 * cannot attribute to the contract. */
export function parseUiSessionExchange(
  value: unknown,
): UiSessionExchange | null {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  const record = value as Record<string, unknown>;
  if (record.schema_version !== 1) return null;
  if (typeof record.exchange_code !== "string" || record.exchange_code === "") {
    return null;
  }
  if (
    typeof record.expires_in_seconds !== "number" ||
    !Number.isFinite(record.expires_in_seconds) ||
    record.expires_in_seconds <= 0
  ) {
    return null;
  }
  if (typeof record.url !== "string" || !isLoopbackHttpUrl(record.url)) {
    return null;
  }
  // The URL must carry exactly the code we were issued, so a hostile or
  // corrupted document cannot redirect the browser elsewhere.
  const url = new URL(record.url);
  if (url.pathname !== "/ui/login") return null;
  if (url.searchParams.get("code") !== record.exchange_code) return null;
  const extra = [...url.searchParams.keys()].filter((key) => key !== "code");
  if (extra.length > 0) return null;
  return {
    url: record.url,
    expiresInSeconds: record.expires_in_seconds,
  };
}

/** Map a Core failure code to the contract's CLI vocabulary. */
export function uiSessionFailure(code: string): { code: string } {
  const known = new Set([
    "ui_web_assets_unavailable",
    "ui_session_code_invalid",
    "ui_session_contract_unavailable",
    "ui_session_exchange_failed",
    "ui_session_transport_failed",
    "runtime_not_running",
  ]);
  return { code: known.has(code) ? code : "ui_session_contract_unavailable" };
}

export function uiExitCode(result: UiResult): number {
  if (result.state === "opened") return 0;
  // A stopped Runtime is the same exit 3 every other command reports.
  if (result.state === "blocked" && result.code === "runtime_not_running") {
    return 3;
  }
  if (result.state === "blocked") return 4;
  return 1;
}

/** Open a URL with the platform's default handler. Injectable for tests. */
export async function openBrowser(
  url: string,
  options: {
    platform?: NodeJS.Platform;
    run?: (command: string, url: string) => Promise<boolean>;
  } = {},
): Promise<boolean> {
  const platform = options.platform ?? process.platform;
  const command =
    platform === "darwin" ? "open" : platform === "win32" ? "cmd" : "xdg-open";
  const run = options.run ?? defaultRunner;
  return run(command, url);
}

function defaultRunner(command: string, url: string): Promise<boolean> {
  return new Promise((resolve) => {
    const args = command === "cmd" ? ["/c", "start", "", url] : [url];
    let settled = false;
    try {
      const child = spawn(command, args, {
        stdio: "ignore",
        detached: true,
      });
      child.once("error", () => {
        if (!settled) {
          settled = true;
          resolve(false);
        }
      });
      child.once("spawn", () => {
        if (!settled) {
          settled = true;
          child.unref();
          resolve(true);
        }
      });
    } catch {
      resolve(false);
    }
  });
}
