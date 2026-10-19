import { execFileSync } from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";
import { describe, expect, it } from "vitest";
import type { RecallRuntime } from "./recall-installer.js";
import {
  DISCOVERY_KEY,
  OWNER_KEY,
  OWNER_VALUE,
  renderConnectionMaterial,
  renderMcpInstallSnippet,
  renderShellInstallSnippet,
  SERVER_NAME,
} from "./recall-installer.js";

/** M-16: the two things a host outside the built-in list needs from us.
 *
 * The built-in one-click install stays the first path for the ten hosts we
 * ship layouts for: it previews a diff, waits for approval, and can be undone
 * byte for byte. These snippets are the fallback for everything else, so they
 * are held to one rule above all: they must be the same command line the
 * installer writes, not a second rendering of it. `recall-installer.ts:938`
 * already records what a second copy costs, and a copy that drifts here is
 * worse than elsewhere, because it drifts on somebody else's machine where we
 * see no error at all.
 */

const CONTRACT_DOC = path.join(
  process.cwd(),
  "..",
  "..",
  "docs",
  "contracts",
  "one-memory-recall-install-v1.md",
);

function runtime(overrides: Partial<RecallRuntime> = {}): RecallRuntime {
  return {
    runtimeCommand: "/Applications/MemHub.app/Contents/MacOS/MemHub",
    mcpEntryPath: "/Applications/MemHub.app/Contents/Resources/mcp.cjs",
    discoveryPath: "/Users/somebody/.one-memory/broker.json",
    recallCliPath: "/Applications/MemHub.app/Contents/Resources/recall-cli.cjs",
    ...overrides,
  };
}

describe("mcp install snippet", () => {
  it("registers under the same server name the installer uses", () => {
    const parsed = JSON.parse(renderMcpInstallSnippet(runtime())) as {
      mcpServers: Record<string, unknown>;
    };
    expect(Object.keys(parsed.mcpServers)).toEqual([SERVER_NAME]);
  });

  it("carries the env keys the installer writes, spelled the same way", () => {
    const parsed = JSON.parse(renderMcpInstallSnippet(runtime())) as {
      mcpServers: Record<string, { env: Record<string, string> }>;
    };
    const env = parsed.mcpServers[SERVER_NAME].env;
    expect(env[DISCOVERY_KEY]).toBe(runtime().discoveryPath);
    expect(env[OWNER_KEY]).toBe(OWNER_VALUE);
    expect(env.ELECTRON_RUN_AS_NODE).toBe("1");
  });

  it("states the entry this install resolved, not a name to look up", () => {
    const parsed = JSON.parse(renderMcpInstallSnippet(runtime())) as {
      mcpServers: Record<string, { command: string; args: string[] }>;
    };
    const server = parsed.mcpServers[SERVER_NAME];
    expect(server.command).toBe(runtime().runtimeCommand);
    expect(server.args).toEqual([runtime().mcpEntryPath]);
  });
});

describe("shell install snippet", () => {
  it("treats local paths as literals, including shell substitutions", () => {
    const marker = "/tmp/$(printf EXPANDED) ' quote";
    const snippet = renderShellInstallSnippet(
      runtime({
        runtimeCommand: "/bin/echo",
        recallCliPath: marker,
        discoveryPath: marker,
      }),
    );
    if (snippet === null) throw new Error("Shell material must exist");
    const output = execFileSync("/bin/sh", ["-c", snippet], {
      encoding: "utf8",
    });
    expect(output).toContain(marker);
    expect(output).not.toContain("/tmp/EXPANDED");
  });
  it("hands over the command line this install resolved", () => {
    const snippet = renderShellInstallSnippet(runtime());
    expect(snippet).not.toBeNull();
    const text = snippet as string;
    expect(text).toContain(`${DISCOVERY_KEY}="${runtime().discoveryPath}"`);
    expect(text).toContain(runtime().runtimeCommand);
    expect(text).toContain(runtime().recallCliPath as string);
    expect(text).toContain('--workspace-path "$PWD"');
  });

  it("offers nothing when there is no recall command on disk", () => {
    // A snippet naming a command that is not there recalls nothing, and the
    // host has no way to tell that apart from us having no memories. Better
    // to withhold the shell form than to hand over one that cannot work.
    expect(
      renderShellInstallSnippet(runtime({ recallCliPath: undefined })),
    ).toBeNull();
  });
});

describe("connection prompt", () => {
  it("marks only the generic copyable MCP connection, not legacy host installs", () => {
    const withCredential = runtime({ credentialFile: "/fixture/credential" });
    const generic = JSON.parse(renderConnectionMaterial(withCredential).mcp)
      .mcpServers[SERVER_NAME];
    const legacy = JSON.parse(renderMcpInstallSnippet(withCredential))
      .mcpServers[SERVER_NAME];
    expect(generic.env).toMatchObject({
      ONE_MEMORY_GENERIC_LOCAL_MCP: "1",
      ONE_MEMORY_HOST_CREDENTIAL_FILE: "/fixture/credential",
    });
    expect(legacy.env.ONE_MEMORY_GENERIC_LOCAL_MCP).toBeUndefined();
    expect(generic.command).toBe(legacy.command);
    expect(generic.args).toEqual(legacy.args);
  });

  it("reuses runtime snippets and distinguishes configuration from actual recall", () => {
    const material = renderConnectionMaterial(runtime());
    expect(material.prompt).toContain(material.mcp);
    expect(material.prompt).toContain(material.shell);
    expect(material.prompt).toContain("不能把写入配置当作连接成功");
    expect(material.prompt).toContain("不要创建公网代理");
    expect(material.prompt).toContain("保留其他服务");
  });
});

describe("public contract document", () => {
  it("spells the env keys exactly as the code does", async () => {
    // The drift this guards against fails on somebody else's machine: they
    // follow the published document, the key no longer matches, and nothing
    // reports it to us. Same class of problem R-33 treated for skill text,
    // and treated the same way: one source, checked by a test.
    const doc = await fs.readFile(CONTRACT_DOC, "utf8");
    for (const key of [DISCOVERY_KEY, OWNER_KEY]) {
      expect(doc).toContain(key);
    }
    expect(doc).toContain(OWNER_VALUE);
    expect(doc).toContain(SERVER_NAME);
  });

  it("publishes the shape without anybody's paths in it", async () => {
    // The three values that make a snippet work are per-machine. A document
    // carrying one install's paths reads as if it were copyable and is not,
    // so the paths belong in the app, and only the shape belongs here.
    const doc = await fs.readFile(CONTRACT_DOC, "utf8");
    expect(doc).not.toContain("/Users/");
    expect(doc).not.toContain("/Applications/MemHub.app");
  });
});
