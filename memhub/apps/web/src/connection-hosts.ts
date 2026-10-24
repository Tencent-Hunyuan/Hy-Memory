/** Mirrors desktop `SOURCE_HOST`. OpenClaw and DeepSeek Harness profiles
 * are not accepted by one-click insertion. */
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
  zcode: "zcode",
};

export const ONE_CLICK_SOURCES = new Set(Object.keys(SOURCE_HOST));

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
