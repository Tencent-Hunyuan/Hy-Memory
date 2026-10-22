import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  applyRecallInstall,
  claudeCodeLayout,
  codexLayout,
  cursorLayout,
  mcpConfigPath,
  planRecallInstall,
  previewRecallInstall,
  type RecallRuntime,
  readInstalledRecallCommand,
  renderMcpInstallSnippet,
  renderSkillContent,
  SKILL_VERSION,
  verifyRecallInstall,
  workbuddyLayout,
} from "./recall-installer.js";

vi.mock("./recall-probe.js", () => ({
  probeRecallInstall: vi.fn(async () => ({
    state: "reachable_but_blind",
    detail: "isolated install-only fixture",
    probedAt: new Date(0).toISOString(),
  })),
}));

const roots: string[] = [];
afterEach(async () => {
  await Promise.all(
    roots.splice(0).map((root) => rm(root, { recursive: true, force: true })),
  );
});

describe("credential file install serialization", () => {
  it.each(["cursor", "codex", "claude-code", "workbuddy"] as const)(
    "round trips only a stable file path through %s native config",
    async (host) => {
      const root = await mkdtemp(
        path.join(os.tmpdir(), "om-credential-install-"),
      );
      roots.push(root);
      const hostDir = path.join(root, host);
      await mkdir(hostDir);
      const layout =
        host === "cursor"
          ? cursorLayout(hostDir)
          : host === "codex"
            ? codexLayout(hostDir)
            : host === "workbuddy"
              ? workbuddyLayout(hostDir)
              : claudeCodeLayout({
                  claudeDir: hostDir,
                  configPath: path.join(root, "claude.json"),
                });
      const runtime = {
        runtimeCommand: path.join(root, "runtime"),
        mcpEntryPath: path.join(root, "mcp.cjs"),
        discoveryPath: path.join(root, "discovery.json"),
        credentialFile: path.join(root, "private path", "credential"),
      } satisfies RecallRuntime;
      if (host === "cursor" || host === "claude-code") {
        await writeFile(
          mcpConfigPath(layout),
          JSON.stringify({
            mcpServers: { neighbor: { command: "keep-this-server" } },
            setting: "keep-this-setting",
          }),
        );
      }
      if (host === "codex") {
        await writeFile(
          mcpConfigPath(layout),
          '# keep this comment\n[mcp_servers.neighbor]\ncommand = "keep-this-server"\n',
        );
      }
      const plan = await planRecallInstall(layout, runtime);
      const preview = await previewRecallInstall(plan);
      expect(JSON.stringify(preview)).toContain(
        "ONE_MEMORY_HOST_CREDENTIAL_FILE",
      );
      expect(plan.runtime.credentialFile).toBe(runtime.credentialFile);
      await expect(readFile(runtime.credentialFile)).rejects.toMatchObject({
        code: "ENOENT",
      });
      await applyRecallInstall(
        await planRecallInstall(layout, runtime),
        preview.fingerprint,
      );
      await verifyRecallInstall(layout);
      const installed = await readInstalledRecallCommand(layout);
      expect(installed?.env.ONE_MEMORY_HOST_CREDENTIAL_FILE).toBe(
        runtime.credentialFile,
      );
      expect(installed?.env.ONE_MEMORY_MANAGED_BY).toBe("one-memory");
      expect(installed?.args).toEqual([runtime.mcpEntryPath]);
      expect(JSON.stringify(installed)).not.toContain("omhg_v1_");
      if (layout.configPath !== null) {
        expect(await readFile(layout.configPath, "utf8")).toContain(
          "keep-this-server",
        );
      }
      expect(SKILL_VERSION).toBe(17);
      expect(renderSkillContent("mcp", runtime)).toBe(
        renderSkillContent("mcp", { ...runtime, credentialFile: undefined }),
      );
    },
  );

  it("keeps credentialFile optional and normalizes it without reading it", async () => {
    const root = await mkdtemp(path.join(os.tmpdir(), "om-credential-path-"));
    roots.push(root);
    const runtime = {
      runtimeCommand: process.execPath,
      mcpEntryPath: path.join(root, "mcp.cjs"),
      discoveryPath: path.join(root, "broker.json"),
    };
    const legacy = JSON.parse(renderMcpInstallSnippet(runtime));
    expect(legacy.mcpServers["one-memory"].env).not.toHaveProperty(
      "ONE_MEMORY_HOST_CREDENTIAL_FILE",
    );
    const plan = await planRecallInstall(cursorLayout(root), {
      ...runtime,
      credentialFile: "relative/credential",
    });
    expect(plan.runtime.credentialFile).toBe(
      path.resolve("relative/credential"),
    );
    expect(plan.next.server?.env.ONE_MEMORY_HOST_CREDENTIAL_FILE).toBe(
      path.resolve("relative/credential"),
    );
  });
});
