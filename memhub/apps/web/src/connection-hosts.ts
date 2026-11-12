/** Mirrors desktop `SOURCE_HOST`. OpenClaw is not accepted by one-click
 * insertion; DeepSeek Harness has no single host and goes through
 * `PROFILE_SOURCES` instead. */
export const SOURCE_HOST: Readonly<Record<string, string>> = {
  cursor: "cursor",
  "claude-code": "claude-code",
  claude: "claude-code",
  codex: "codex",
  workbuddy: "workbuddy",
  opencode: "opencode",
  "qwen-code": "qwen-code",
  hermes: "hermes",
  pi: "pi",
};

export const ONE_CLICK_SOURCES = new Set(Object.keys(SOURCE_HOST));

/** Sources configured per profile. Each profile is its own host
 * (`deepseek-harness:<profile>`, with its own patch, credential and grant), so
 * the app's one switch covers every profile the desktop detected. */
export const PROFILE_SOURCES: ReadonlySet<string> = new Set([
  "deepseek-harness",
]);

export function isProfileSource(sourceId: string) {
  return PROFILE_SOURCES.has(sourceId);
}

/** `deepseek-harness:web` -> `web`. */
export function profileName(hostId: string) {
  const colon = hostId.indexOf(":");
  return colon < 0 ? hostId : hostId.slice(colon + 1);
}

export function connectionHostId(sourceId: string): string | null {
  return SOURCE_HOST[sourceId] ?? null;
}

export function sameConnectionHost(left: string, right: string) {
  if (left === right) return true;
  return (
    (connectionHostId(left) ?? left) === (connectionHostId(right) ?? right)
  );
}

export function preferenceHasSource(ids: readonly string[], sourceId: string) {
  return ids.some((id) => sameConnectionHost(id, sourceId));
}

export function hostMatchesSource(sourceId: string, hostId: string) {
  const source = sourceId.toLowerCase();
  const host = hostId.toLowerCase();
  if (source === host) return true;
  const mapped = SOURCE_HOST[source];
  if (mapped && mapped === host) return true;
  return host.startsWith(`${source}:`);
}
