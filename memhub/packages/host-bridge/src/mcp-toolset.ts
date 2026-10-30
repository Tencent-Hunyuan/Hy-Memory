/** The tool contract of MemHub's MCP server, and the stamp that names it.
 *
 * Kept apart from `mcp-session.ts` so the installer can stamp an entry without
 * pulling in the broker client, and listed here rather than inferred so a test
 * can hold `tools/list` to it: change a tool there and that test fails until
 * this list, and with it the version, moves too.
 */

/** The tools `tools/list` returns, in order. */
export const MCP_TOOL_NAMES = [
  "MemHub_recall",
  "MemHub_write",
  "MemHub_diagnostic_trace",
] as const;

/** Bumped whenever a tool is added, removed or renamed.
 *
 * Written into every managed MCP entry under `MCP_TOOLSET_KEY`. Hosts such as
 * WorkBuddy cache a server's tool list under a fingerprint of its launch
 * config, and our entry is otherwise byte-identical across upgrades, since it
 * runs through the `current` symlink. Moving this value is what changes that
 * fingerprint, so those hosts restart the server and drop the tools it no
 * longer has. Deliberately not the build number: that would rewrite every
 * host's config on every upgrade, for nothing.
 */
export const MCP_TOOLSET_VERSION = "3";

/** The env key the version is written under. */
export const MCP_TOOLSET_KEY = "ONE_MEMORY_MCP_TOOLSET";
