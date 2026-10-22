import { describe, expect, it } from "vitest";

import {
  putYamlMemoryProvider,
  putYamlServer,
  readYamlMemoryProvider,
  readYamlServer,
  removeYamlServer,
  restoreYamlMemoryProvider,
} from "./yaml-mcp.js";

/** Verbatim bytes Hermes 0.21.0 wrote when it installed our own server.
 *
 * Kept exactly as found on disk, folded `command` included: that fold is the
 * host's choice, not ours, so it is the shape a reader has to survive.
 */
const HOST_WRITTEN = `model:
  default: qwen-plus
  provider: custom
gateway:
  teams:
    - hermes-teams
mcp_servers:
  one-memory:
    command: /Users/me/One Memory.app/Contents/MacOS/One
      Memory
    args:
      - /Users/me/One Memory.app/Contents/Resources/host-bridge/mcp.cjs
    env:
      ELECTRON_RUN_AS_NODE: '1'
      ONE_MEMORY_BROKER_DISCOVERY: /Users/me/.one-memory/integrations/cursor-broker.json
      ONE_MEMORY_MANAGED_BY: one-memory
    enabled: true

# \u2500\u2500 Security \u2500\u2500
# security:
#   redact_secrets: true
`;

describe("yaml mcp editing", () => {
  it("unfolds a scalar the host wrapped across lines", () => {
    const server = readYamlServer(HOST_WRITTEN, "one-memory");

    // Read a line at a time this comes back as ".../MacOS/One", which is a
    // different path, and the installer would take the entry for a stranger's.
    expect(server?.command).toBe(
      "/Users/me/One Memory.app/Contents/MacOS/One Memory",
    );
    expect(server?.args).toEqual([
      "/Users/me/One Memory.app/Contents/Resources/host-bridge/mcp.cjs",
    ]);
    expect(server?.env).toEqual({
      ELECTRON_RUN_AS_NODE: "1",
      ONE_MEMORY_BROKER_DISCOVERY:
        "/Users/me/.one-memory/integrations/cursor-broker.json",
      ONE_MEMORY_MANAGED_BY: "one-memory",
    });
  });

  it("keeps a quoted number a string", () => {
    const written = putYamlServer("", "one-memory", {
      command: "/bin/thing",
      args: [],
      env: { ELECTRON_RUN_AS_NODE: "1" },
    });

    expect(written).toContain("ELECTRON_RUN_AS_NODE: '1'");
    expect(
      readYamlServer(written, "one-memory")?.env.ELECTRON_RUN_AS_NODE,
    ).toBe("1");
  });

  it("leaves every other byte of the document alone", () => {
    const updated = putYamlServer(HOST_WRITTEN, "one-memory", {
      command: "/opt/new/One Memory",
      args: ["/opt/new/mcp.cjs"],
      env: { ONE_MEMORY_MANAGED_BY: "one-memory" },
    });

    // The host's own sections, its comments, and the blank line before them
    // are none of our business.
    expect(updated).toContain("  default: qwen-plus");
    expect(updated).toContain("    - hermes-teams");
    expect(updated).toContain("# \u2500\u2500 Security \u2500\u2500");
    expect(updated).toContain("#   redact_secrets: true");
    expect(readYamlServer(updated, "one-memory")?.command).toBe(
      "/opt/new/One Memory",
    );
    // Replacing an entry must not leave the old one behind.
    expect(updated).not.toContain("MacOS/One");
  });

  it("does not disturb a neighbouring server", () => {
    const withNeighbour = putYamlServer(HOST_WRITTEN, "other-tool", {
      command: "npx",
      args: ["-y", "other"],
      env: {},
    });
    const updated = putYamlServer(withNeighbour, "one-memory", {
      command: "/opt/new/One Memory",
      args: [],
      env: {},
    });

    expect(readYamlServer(updated, "other-tool")).toEqual({
      command: "npx",
      args: ["-y", "other"],
      env: {},
    });
  });

  it("takes the section away with the last entry it held", () => {
    const emptied = removeYamlServer(HOST_WRITTEN, "one-memory");

    // `mcp_servers:` with nothing under it reads as null, not as the empty map
    // the host would have written, so it cannot be left behind.
    expect(emptied).not.toContain("mcp_servers");
    expect(emptied).toContain("  default: qwen-plus");
    expect(emptied).toContain("# \u2500\u2500 Security \u2500\u2500");
  });

  it("keeps the section when someone else still has an entry in it", () => {
    const shared = putYamlServer(HOST_WRITTEN, "other-tool", {
      command: "npx",
      args: [],
      env: {},
    });

    const emptied = removeYamlServer(shared, "one-memory");

    expect(readYamlServer(emptied, "one-memory")).toBeNull();
    expect(readYamlServer(emptied, "other-tool")?.command).toBe("npx");
  });

  it("reports nothing rather than guessing when the entry is absent", () => {
    expect(readYamlServer(HOST_WRITTEN, "not-installed")).toBeNull();
    expect(readYamlServer("model:\n  default: x\n", "one-memory")).toBeNull();
  });
});

describe("Hermes memory provider editing", () => {
  it("updates only memory.provider and restores its prior value", () => {
    const original =
      "# keep\nmemory:\n  provider: existing\n  compact: true\nmodels:\n  default: test\n";
    const previous = readYamlMemoryProvider(original);
    const installed = putYamlMemoryProvider(original, "hy_memory");
    expect(readYamlMemoryProvider(installed)).toEqual({
      existed: true,
      value: "hy_memory",
    });
    expect(installed).toContain("compact: true");
    expect(restoreYamlMemoryProvider(installed, previous)).toBe(original);
  });

  it("adds and removes only the managed leaf", () => {
    const original = "models:\n  default: test\n";
    const previous = readYamlMemoryProvider(original);
    const installed = putYamlMemoryProvider(original, "hy_memory");
    expect(installed).toContain("memory:\n  provider: hy_memory\n");
    expect(restoreYamlMemoryProvider(installed, previous)).toBe(original);
  });
});
