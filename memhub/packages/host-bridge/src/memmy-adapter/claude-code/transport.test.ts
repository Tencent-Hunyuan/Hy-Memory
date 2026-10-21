import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import {
  chmod,
  mkdir,
  mkdtemp,
  readdir,
  readFile,
  realpath,
  rm,
  writeFile,
} from "node:fs/promises";
import {
  createServer,
  type IncomingMessage,
  type ServerResponse,
} from "node:http";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, it } from "vitest";
import { verifiedAssets } from "./install-plan.js";
import { createClaudeCodeTarget } from "./target.js";

const hash = (value: string) =>
  createHash("sha256").update(value).digest("hex");

async function body(request: IncomingMessage) {
  let value = "";
  for await (const part of request) value += part;
  return value ? JSON.parse(value) : null;
}
function respond(response: ServerResponse, status: number, value: unknown) {
  response.writeHead(status, { "content-type": "application/json" });
  response.end(JSON.stringify(value));
}
function executeHook(root: string, input: object) {
  return new Promise<{ stdout: string; stderr: string; code: number | null }>(
    (resolve, reject) => {
      const child = spawn(
        process.execPath,
        [path.join(root, ".one-memory/mh-c01/hook.mjs")],
        {
          cwd: root,
          env: { HOME: root, PATH: "/usr/bin:/bin", LANG: "en_US.UTF-8" },
          stdio: "pipe",
        },
      );
      let stdout = "",
        stderr = "";
      child.stdout.on("data", (value) => {
        stdout += value;
      });
      child.stderr.on("data", (value) => {
        stderr += value;
      });
      const timer = setTimeout(() => {
        child.kill("SIGKILL");
        reject(new Error("isolated_hook_timeout"));
      }, 8000);
      child.on("error", (error) => {
        clearTimeout(timer);
        reject(error);
      });
      child.on("close", (code) => {
        clearTimeout(timer);
        resolve({ stdout, stderr, code });
      });
      child.stdin.end(JSON.stringify(input));
    },
  );
}

it.each(["recall-and-sync", "sync-only"] as const)(
  "runs the installed candidate and production asset in %s mode without a host/model process",
  async (mode) => {
    const root = await realpath(
      await mkdtemp(path.join(os.tmpdir(), "mh-c01-wire-")),
    );
    const claude = path.join(root, "claude");
    await mkdir(claude);
    const privateDirectory = path.join(root, "private");
    await mkdir(privateDirectory, { mode: 0o700 });
    await chmod(privateDirectory, 0o700);
    const credentialFile = path.join(privateDirectory, "credential");
    const credential = `omhg_v1_${"c".repeat(43)}`;
    await writeFile(credentialFile, credential, { mode: 0o600 });
    const requests: Array<{
      path: string;
      body: unknown;
      authorized: boolean;
    }> = [];
    let granted = true;
    const instanceId = "synthetic-instance";
    const server = createServer(async (request, response) => {
      if (request.url === "/health")
        return respond(response, 200, {
          status: "ok",
          service: "one-memory-recall-broker",
          instance_id: instanceId,
        });
      const data = await body(request);
      const authorized =
        request.headers.authorization === `Bearer ${credential}`;
      requests.push({ path: request.url ?? "", body: data, authorized });
      if (!authorized || !granted)
        return respond(response, 401, {
          error: "host_grant_authentication_failed",
        });
      if (request.url === "/source-changed")
        return respond(response, 200, { accepted: true });
      if (request.url === "/recall")
        return respond(response, 200, {
          state: "grounded",
          query: data.query,
          scope: data.scope,
          hits: [
            {
              memory_id: "synthetic-memory",
              content: "Synthetic remembered decision",
              kind: "chat",
              scopes: ["all-apps"],
              evidence_ids: ["synthetic-evidence"],
              updated_at: "2026-09-14",
              score: 1,
            },
          ],
          used_characters: 29,
          truncated: false,
        });
      return respond(response, 404, { error: "unexpected_route" });
    });
    try {
      await new Promise<void>((resolve) =>
        server.listen(0, "127.0.0.1", resolve),
      );
      const address = server.address();
      if (!address || typeof address === "string")
        throw new Error("missing test address");
      const discoveryPath = path.join(root, "discovery.json");
      await writeFile(
        discoveryPath,
        JSON.stringify({
          schema_version: 1,
          service: "one-memory-recall-broker",
          status: "running",
          host: "127.0.0.1",
          port: address.port,
          instance_id: instanceId,
          pid: process.pid,
          updated_at: new Date().toISOString(),
        }),
      );
      const assetsDirectory = path.join(root, "built-assets");
      // Explicit temporary output and config:false never touch the package default dist.
      // Loading the dev tool does not pull its optional SWC declarations into product typechecking.
      await createRequire(import.meta.url)("tsup").build({
        entry: {
          runtime: fileURLToPath(new URL("./runtime.ts", import.meta.url)),
          notification: fileURLToPath(
            new URL("../../notification-transport.ts", import.meta.url),
          ),
        },
        outDir: assetsDirectory,
        config: false,
        bundle: true,
        splitting: false,
        format: ["esm"],
        platform: "node",
        target: "node20",
        noExternal: ["@one-memory/contracts", "axios"],
        banner: {
          js: 'import { createRequire as __mhCreateRequire } from "node:module"; const require = __mhCreateRequire(import.meta.url);',
        },
        outExtension: () => ({ js: ".mjs" }),
        shims: true,
        silent: true,
        sourcemap: false,
        dts: false,
      });
      const runtime = await readFile(
        path.join(assetsDirectory, "runtime.mjs"),
        "utf8",
      );
      const notificationAsset = await readFile(
        path.join(assetsDirectory, "notification.mjs"),
        "utf8",
      );
      expect(notificationAsset).not.toContain("/test-source-changed");
      const assets = verifiedAssets({
        runtime,
        notification: notificationAsset,
        proof: {
          runtimeSha256: hash(runtime),
          notificationSha256: hash(notificationAsset),
          integrationTestsPassed: true,
        },
      });
      const target = createClaudeCodeTarget({
        claudeDirectory: claude,
        nodeExecutable: process.execPath,
        assets,
        config: {
          schemaVersion: 1,
          mode,
          installationId: "00000000-0000-4000-8000-000000000001",
          discoveryPath,
          credentialFile,
          stateDirectory: path.join(claude, ".one-memory/mh-c01/state"),
        },
      });
      const plan = await target.planInstall();
      await target.applyInstall(plan, plan.fingerprint);
      for (const hook_event_name of ["SessionStart", "PostCompact"]) {
        const result = await executeHook(claude, {
          hook_event_name,
          session_id: "s1",
        });
        expect(result.code, result.stderr).toBe(0);
        expect(JSON.parse(result.stdout)).toEqual({
          continue: true,
          suppressOutput: true,
        });
      }
      expect(requests).toHaveLength(0);
      const prompt = await executeHook(claude, {
        hook_event_name: "UserPromptSubmit",
        session_id: "s1",
        prompt:
          mode === "sync-only"
            ? "long private prompt ".repeat(1000)
            : "synthetic user query",
      });
      expect(prompt.code, prompt.stderr).toBe(0);
      if (mode === "recall-and-sync") {
        expect(
          JSON.parse(prompt.stdout).hookSpecificOutput.additionalContext,
        ).toContain("synthetic-memory");
        expect(prompt.stderr).toContain("正在使用 MemHub 查询记忆");
      } else {
        expect(JSON.parse(prompt.stdout)).toEqual({
          continue: true,
          suppressOutput: true,
        });
        expect(prompt.stderr).toBe("");
        expect(requests).toHaveLength(0);
        expect(
          await readFile(
            path.join(claude, "skills/one-memory-lifecycle/SKILL.md"),
            "utf8",
          ),
        ).toContain("does not recall or inject context");
      }
      expect(prompt.stdout + prompt.stderr).not.toContain(credential);
      const stop = {
        hook_event_name: "Stop",
        session_id: "s1",
        transcript_path: "/never/read",
        last_assistant_message: "not sent or saved",
      };
      await executeHook(claude, stop);
      await executeHook(claude, stop);
      expect(requests.map((value) => value.path)).toEqual(
        mode === "sync-only"
          ? ["/source-changed"]
          : ["/recall", "/source-changed"],
      );
      const notificationRequest = requests.at(-1);
      if (!notificationRequest) throw new Error("notification_request_missing");
      expect(Object.keys(notificationRequest.body as object).sort()).toEqual([
        "event_id",
        "event_kind",
        "session_id",
        "version",
      ]);
      expect(requests.every((value) => value.authorized)).toBe(true);
      expect(JSON.stringify(notificationRequest)).not.toMatch(
        /not sent|never\/read|synthetic user query|long private prompt/,
      );
      granted = false;
      const denied = await executeHook(claude, {
        hook_event_name: "UserPromptSubmit",
        session_id: "s1",
        prompt: "denied query",
      });
      if (mode === "recall-and-sync")
        expect(
          JSON.parse(denied.stdout).hookSpecificOutput.additionalContext,
        ).toContain("denied");
      else {
        expect(JSON.parse(denied.stdout)).toEqual({
          continue: true,
          suppressOutput: true,
        });
        const deniedStop = await executeHook(claude, stop);
        expect(JSON.parse(deniedStop.stdout)).toEqual({
          continue: true,
          suppressOutput: true,
        });
        expect(requests.at(-1)?.path).toBe("/source-changed");
      }
      expect(denied.stdout).not.toContain("synthetic-memory");
      await executeHook(claude, {
        hook_event_name: "SessionEnd",
        session_id: "s1",
      });
      const stateDirectory = path.join(claude, ".one-memory/mh-c01/state");
      for (const file of await readdir(stateDirectory)) {
        expect(
          await readFile(path.join(stateDirectory, file), "utf8"),
        ).not.toMatch(
          /synthetic user query|denied query|long private prompt|not sent|never\/read|omhg_v1_/,
        );
      }
      const notificationPath = path.join(
        claude,
        ".one-memory/mh-c01/notification.mjs",
      );
      await rm(notificationPath);
      const unavailable = await executeHook(claude, {
        hook_event_name: "SessionStart",
        session_id: "s2",
      });
      expect(unavailable.code, unavailable.stderr).toBe(0);
      expect(JSON.parse(unavailable.stdout)).toEqual({
        continue: true,
        suppressOutput: true,
      });
      expect(unavailable.stderr).toContain("MemHub hook unavailable");
      expect(unavailable.stderr).not.toContain(root);
      await writeFile(notificationPath, notificationAsset, { mode: 0o600 });
      const removal = await target.planUninstall();
      await target.applyUninstall(removal, removal.fingerprint);
      expect(await readFile(credentialFile, "utf8")).toBe(credential);
    } finally {
      await new Promise<void>((resolve, reject) =>
        server.close((error) => (error ? reject(error) : resolve())),
      );
      await rm(root, { recursive: true, force: true });
    }
  },
  25_000,
);
