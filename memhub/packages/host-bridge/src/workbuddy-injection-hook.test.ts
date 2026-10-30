import { spawn } from "node:child_process";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import { injectionHookCommand } from "./codebuddy-plugin.js";
import { HostGrantStore } from "./host-grants.js";
import { RecallBroker } from "./recall-broker.js";
import {
  applyRecallInstall,
  planRecallInstall,
  workbuddyLayout,
} from "./recall-installer.js";

const roots: string[] = [];
const closers: Array<() => Promise<void>> = [];

afterEach(async () => {
  for (const close of closers.splice(0).reverse()) await close();
  await Promise.all(
    // Ack and capture are detached on purpose and may still be finishing.
    roots.splice(0).map((root) =>
      rm(root, {
        recursive: true,
        force: true,
        maxRetries: 20,
        retryDelay: 100,
      }),
    ),
  );
});

function run(
  command: string,
  stdin: string,
  options: {
    killAfterMs?: number;
    env?: NodeJS.ProcessEnv;
    /** As the host does on a timeout: the hook's own shell, nothing below it. */
    killShellWhen?: string;
  } = {},
) {
  return new Promise<{
    code: number | null;
    stdout: string;
    stderr: string;
    ms: number;
    firstByteMs: number | null;
    killed: boolean;
  }>((resolve, reject) => {
    const started = Date.now();
    const child = spawn("/bin/sh", ["-c", command], {
      stdio: ["pipe", "pipe", "pipe"],
      env: options.env ?? process.env,
      detached: options.killAfterMs !== undefined,
    });
    let stdout = "";
    let stderr = "";
    let firstByteMs: number | null = null;
    let killed = false;
    child.stdout.on("data", (chunk) => {
      firstByteMs ??= Date.now() - started;
      stdout += chunk;
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });
    if (options.killAfterMs !== undefined && child.pid !== undefined) {
      const group = child.pid;
      const timer = setTimeout(() => {
        killed = true;
        try {
          process.kill(-group, "SIGKILL");
        } catch {}
      }, options.killAfterMs);
      child.on("close", () => clearTimeout(timer));
    }
    const killShellWhen = options.killShellWhen;
    if (killShellWhen !== undefined && child.pid !== undefined) {
      const shell = child.pid;
      void eventually(killShellWhen).then(() => {
        try {
          process.kill(shell, "SIGKILL");
        } catch {}
      });
    }
    child.on("error", reject);
    child.on("close", (code) =>
      resolve({
        code,
        stdout,
        stderr,
        ms: Date.now() - started,
        firstByteMs,
        killed,
      }),
    );
    child.stdin.end(stdin);
  });
}

describe("WorkBuddy injection hook as the host runs it", () => {
  it("adds context while linked and nothing after the link is turned off", async () => {
    const root = await mkdtemp(path.join(os.tmpdir(), "om-wb-inject-rt-"));
    roots.push(root);
    const store = await HostGrantStore.initialize(path.join(root, "grants"));
    const issued = await store.createInstallation({ hostId: "workbuddy" });
    const approved = await store.approveGrants({
      installationId: issued.receipt.installationId,
      expectedRevision: issued.receipt.revision,
      allowedTools: ["om_recall", "om_conversations"],
      allowedScopes: ["all-apps", "source:workbuddy"],
    });
    const credentialDir = path.join(root, "credential");
    await mkdir(credentialDir, { mode: 0o700 });
    const credentialFile = path.join(credentialDir, "host-credential");
    await writeFile(credentialFile, `${issued.secret}\n`, { mode: 0o600 });
    const core = createServer((request, response) => {
      void (async () => {
        for await (const _chunk of request);
        response.setHeader("Content-Type", "application/json");
        if (request.url === "/api/v2/context/search") {
          response.end(
            JSON.stringify({
              schema_version: 2,
              request_id: "11111111-1111-4111-8111-111111111111",
              state: "grounded",
              items: [
                {
                  kind: "memory",
                  memory_id: "mem-1",
                  content: "用户喜欢深绿色的界面",
                  layer: "l2_fact",
                  status: "active",
                  source: "workbuddy",
                  updated_at: "2026-09-01T08:00:00Z",
                  score: 0.9,
                  truncated: false,
                },
              ],
              used_characters: 10,
              truncated: false,
              total_ms: 2,
            }),
          );
          return;
        }
        response.statusCode = 404;
        response.end("{}");
      })();
    });
    await new Promise<void>((resolve) => core.listen(0, "127.0.0.1", resolve));
    closers.push(async () => {
      core.closeAllConnections();
      await new Promise<void>((resolve) => core.close(() => resolve()));
    });
    const discoveryPath = path.join(root, "broker.json");
    const broker = new RecallBroker({
      coreBaseUrl: `http://127.0.0.1:${(core.address() as AddressInfo).port}`,
      coreApiToken: "isolated-core-token",
      discoveryPath,
      instanceId: "wb-inject-roundtrip",
      preferredPort: 0,
      hostGrantStore: store,
    });
    await broker.start();
    closers.push(() => broker.stop());

    const hostDir = path.join(root, "home", ".workbuddy");
    await mkdir(hostDir, { recursive: true });
    const layout = workbuddyLayout(hostDir);
    const dataDir = path.join(root, "data");
    await applyRecallInstall(
      await planRecallInstall(layout, {
        runtimeCommand: process.execPath,
        mcpEntryPath: path.join(process.cwd(), "dist", "mcp.cjs"),
        recallCliPath: path.join(process.cwd(), "dist", "recall-cli.cjs"),
        discoveryPath,
        credentialFile,
        captureCommand: "/bin/false",
        dataDir,
      }),
    );
    const plugin = layout.plugin;
    if (!plugin) throw new Error("WorkBuddy installs as a plugin");
    const hooks = JSON.parse(
      await readFile(
        path.join(plugin.installRoot, "hooks", "hooks.json"),
        "utf8",
      ),
    );
    const command: string = hooks.hooks.UserPromptSubmit.map(
      (entry: { hooks: Array<{ command: string }> }) => entry.hooks[0].command,
    ).find((text: string) => text.includes("--inject"));
    expect(command).toBeDefined();
    const input = JSON.stringify({
      session_id: "sess-rt",
      transcript_path: path.join(hostDir, "projects", "sess-rt.jsonl"),
      cwd: root,
      hook_event_name: "UserPromptSubmit",
      prompt: "继续上次的按钮颜色任务",
    });

    const linked = await run(command, input, {
      env: {
        ...process.env,
        CODEBUDDY_CONVERSATION_REQUEST_ID: "host-turn-1",
      },
    });
    expect(linked.code).toBe(0);
    const output = JSON.parse(linked.stdout);
    expect(output.hookSpecificOutput.hookEventName).toBe("UserPromptSubmit");
    expect(output.hookSpecificOutput.additionalContext).toContain(
      "用户喜欢深绿色的界面",
    );
    expect(linked.ms).toBeLessThan(3_000);

    const normalLatencies = [linked.ms];
    for (let index = 1; index < 20; index += 1) {
      normalLatencies.push((await run(command, input)).ms);
    }
    normalLatencies.sort((left, right) => left - right);
    const p95 = normalLatencies[Math.ceil(normalLatencies.length * 0.95) - 1];
    expect(p95).toBeLessThanOrEqual(1_500);

    await store.revoke({
      installationId: approved.installationId,
      expectedRevision: approved.revision,
    });
    const revoked = await run(command, input);
    expect(revoked.code).toBe(0);
    expect(revoked.stdout).toBe("");

    await broker.stop();
    const stopped = await run(command, input);
    expect(stopped.code).toBe(0);
    expect(stopped.stdout).toBe("");
    expect(stopped.ms).toBeLessThan(3_000);
  }, 120_000);
});

async function installedHook() {
  const root = await mkdtemp(path.join(os.tmpdir(), "om-wb-hook-rt-"));
  roots.push(root);
  const core = path.join(root, "one-memory");
  const mode = path.join(root, "mode");
  const captured = path.join(root, "captured");
  const started = path.join(root, "capture-started");
  // One script, switched by a file, because the hook bakes the path in.
  await writeFile(
    core,
    [
      "#!/bin/sh",
      `m=$(cat '${mode}')`,
      'if [ "$1 $2" = "capture workbuddy" ]; then',
      // One file, written whole: the capture's deadline, and that it started.
      `  printf '%s\\n' "$ONE_MEMORY_CAPTURE_DEADLINE_S" > '${started}.part' && mv '${started}.part' '${started}'`,
      '  if [ "$m" = capture-hangs ]; then sleep 30; fi',
      '  case "$m" in capture-slow*) sleep 8 ;; esac',
      `  cat > '${captured}.part' && mv '${captured}.part' '${captured}'`,
      "  exit 0",
      "fi",
      "exit 9",
      "",
    ].join("\n"),
    { mode: 0o755 },
  );
  const hostDir = path.join(root, "home", ".workbuddy");
  await mkdir(hostDir, { recursive: true });
  const layout = workbuddyLayout(hostDir);
  await applyRecallInstall(
    await planRecallInstall(layout, {
      runtimeCommand: process.execPath,
      mcpEntryPath: path.join(process.cwd(), "dist", "mcp.cjs"),
      recallCliPath: path.join(process.cwd(), "dist", "recall-cli.cjs"),
      discoveryPath: path.join(root, "no-broker.json"),
      captureCommand: core,
      dataDir: path.join(root, "data"),
    }),
  );
  const plugin = layout.plugin;
  if (!plugin) throw new Error("WorkBuddy installs as a plugin");
  const hooks = JSON.parse(
    await readFile(
      path.join(plugin.installRoot, "hooks", "hooks.json"),
      "utf8",
    ),
  );
  const hook: { command: string; timeout: number } =
    hooks.hooks.UserPromptSubmit.map(
      (entry: { hooks: Array<{ command: string; timeout: number }> }) =>
        entry.hooks[0],
    ).find((entry: { command: string }) => entry.command.includes("--inject"));
  const prompt: Array<{ command: string; timeout: number }> =
    hooks.hooks.UserPromptSubmit.flatMap(
      (entry: { hooks: Array<{ command: string; timeout: number }> }) =>
        entry.hooks,
    );
  return { root, hook, prompt, core, mode, captured, started };
}

function hookInput() {
  return JSON.stringify({
    session_id: "sess-hook",
    hook_event_name: "UserPromptSubmit",
    prompt: "按钮用什么颜色",
  });
}

/** A broker that accepts the connection and never answers, so the hook is
 * still waiting on it when the host's kill lands. */
async function holdOnSilentBroker(root: string) {
  const silent = createServer(() => {});
  await new Promise<void>((resolve) => silent.listen(0, "127.0.0.1", resolve));
  closers.push(async () => {
    silent.closeAllConnections();
    await new Promise<void>((resolve) => silent.close(() => resolve()));
  });
  await writeFile(
    path.join(root, "no-broker.json"),
    JSON.stringify({
      schema_version: 1,
      service: "one-memory-recall-broker",
      status: "running",
      host: "127.0.0.1",
      port: (silent.address() as AddressInfo).port,
      instance_id: "silent-broker",
      pid: process.pid,
      updated_at: new Date().toISOString(),
    }),
  );
}

async function eventually(file: string, withinMs = 5_000) {
  const until = Date.now() + withinMs;
  for (;;) {
    const text = await readFile(file, "utf8").catch(() => null);
    if (text !== null || Date.now() > until) return text;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
}

async function settled(file: string, afterMs = 2_500) {
  await new Promise((resolve) => setTimeout(resolve, afterMs));
  return readFile(file, "utf8").catch(() => null);
}

describe("the prompt is captured without a hook of its own", () => {
  it("does not hold up the send when capture hangs", async () => {
    const { prompt, mode, started } = await installedHook();
    await writeFile(mode, "capture-hangs");

    // As the host runs them: every hook on the event at once.
    const results = await Promise.all(
      prompt.map((hook) =>
        run(hook.command, hookInput(), {
          killAfterMs: hook.timeout * 1000 + 1000,
        }),
      ),
    );

    prompt.forEach((hook, index) => {
      expect(results[index].killed, hook.command).toBe(false);
      expect(results[index].ms).toBeLessThan(4_000);
    });
    // Capture did start, and is past writing anything before its hang.
    expect(await eventually(started, 10_000)).toBe("30\n");
  }, 40_000);

  it("still hands capture the exact prompt the host sent", async () => {
    const { prompt, mode, captured } = await installedHook();
    await writeFile(mode, "answer");
    const input = hookInput();

    await Promise.all(prompt.map((hook) => run(hook.command, input)));

    expect(await eventually(captured, 10_000)).toBe(input);
  }, 30_000);

  it("gives capture a deadline of its own, since nothing is left to stop it", async () => {
    const { hook, mode, started } = await installedHook();
    await writeFile(mode, "answer");

    await run(hook.command, hookInput());

    expect(await eventually(started, 10_000)).toBe("30\n");
  }, 30_000);

  it("finishes capturing after the hook itself has exited", async () => {
    const { hook, mode, captured } = await installedHook();
    await writeFile(mode, "capture-slow");
    const input = hookInput();

    const result = await run(hook.command, input);

    expect(result.code).toBe(0);
    // The hook is gone and capture is still on its way.
    expect(await readFile(captured, "utf8").catch(() => null)).toBeNull();
    expect(await eventually(captured, 20_000)).toBe(input);
  }, 40_000);

  it("finishes capturing when the host kills only the hook's shell", async () => {
    // The kill has to land while the hook still runs. On a loaded machine the
    // hook can reach its own budget first; then the case did not happen.
    for (let attempt = 1; attempt <= 3; attempt += 1) {
      const { root, hook, mode, captured, started } = await installedHook();
      await writeFile(mode, "capture-slow");
      await holdOnSilentBroker(root);
      // A history prompt with a turn id, so the hook does go to the broker.
      const input = JSON.stringify({
        session_id: "sess-hook",
        turn_id: "turn-1",
        hook_event_name: "UserPromptSubmit",
        prompt: "继续上次的按钮颜色任务",
      });
      const result = await run(hook.command, input, { killShellWhen: started });
      if (result.code !== null) continue;
      expect(await eventually(captured, 20_000)).toBe(input);
      return;
    }
    expect.fail("the host's kill never landed while the hook was running");
  }, 120_000);

  it("does not capture a session start, which carries no words", async () => {
    const { root, hook, mode, captured } = await installedHook();
    await writeFile(mode, "answer");
    // The hook logs only into a data directory that already exists.
    await mkdir(path.join(root, "data"), { mode: 0o700 });

    await run(
      hook.command,
      JSON.stringify({
        session_id: "sess-hook",
        hook_event_name: "SessionStart",
        source: "startup",
      }),
    );

    // The same entry did run: it logged the session start.
    expect(
      await eventually(path.join(root, "data", "logs", "recall-inject.jsonl")),
    ).toContain('"event":"SessionStart"');
    expect(await settled(captured)).toBeNull();
  }, 20_000);
});

describe("the injection hook's output is the entry's own", () => {
  async function runtime(body: string) {
    const root = await mkdtemp(path.join(os.tmpdir(), "om-wb-passthrough-"));
    roots.push(root);
    const script = path.join(root, "runtime");
    await writeFile(script, `#!/bin/sh\n${body}\n`, { mode: 0o755 });
    return injectionHookCommand({
      runtimeCommand: script,
      recallCliPath: path.join(root, "recall-cli.cjs"),
      discoveryPath: path.join(root, "broker.json"),
    });
  }

  it("reaches the host as soon as it is written, not when the hook exits", async () => {
    const command = await runtime(
      `printf '%s\\n' '{"suppressOutput":true}'; sleep 2`,
    );

    const result = await run(command, hookInput());

    expect(result.stdout).toBe('{"suppressOutput":true}\n');
    expect(result.ms).toBeGreaterThanOrEqual(1_900);
    expect(result.firstByteMs).not.toBeNull();
    expect(result.firstByteMs ?? Infinity).toBeLessThan(1_000);
  }, 20_000);

  it("still exits 0 and prints nothing when the runtime is gone or fails", async () => {
    for (const body of [
      "exit 127",
      "echo 'Error: boom' >&2; exit 1",
      "exec /nonexistent/node",
    ]) {
      const result = await run(await runtime(body), hookInput());
      expect(result.code, body).toBe(0);
      expect(result.stdout, body).toBe("");
    }
  }, 20_000);

  it("keeps delivery diagnosis silent unless its explicit switch is set", async () => {
    const command = await runtime(
      "printf '%s\\n' '{\"suppressOutput\":true}'; printf '%s\\n' 'one-memory delivery failed: host_scope_denied' >&2",
    );

    const quiet = await run(command, hookInput());
    expect(quiet.code).toBe(0);
    expect(quiet.stdout).toBe('{"suppressOutput":true}\n');
    expect(quiet.stderr).toBe("");

    const diagnosed = await run(command, hookInput(), {
      env: { ...process.env, ONE_MEMORY_DEBUG_DELIVERY: "1" },
    });
    expect(diagnosed.code).toBe(0);
    expect(diagnosed.stdout).toBe('{"suppressOutput":true}\n');
    expect(diagnosed.stderr).toBe(
      "one-memory delivery failed: host_scope_denied\n",
    );
  }, 20_000);
});
