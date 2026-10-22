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
    killWhen?: string;
    closeStdout?: boolean;
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
      detached:
        options.killWhen !== undefined || options.killAfterMs !== undefined,
    });
    let stdout = "";
    let stderr = "";
    let firstByteMs: number | null = null;
    let killed = false;
    if (options.closeStdout) child.stdout.destroy();
    else
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
    const killWhen = options.killWhen;
    if (killWhen !== undefined && child.pid !== undefined) {
      const group = child.pid;
      void eventually(killWhen).then(() => {
        try {
          process.kill(-group, "SIGKILL");
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
        if (
          request.method === "GET" &&
          request.url === "/api/v2/memory-authority"
        ) {
          response.setHeader("Content-Type", "application/json");
          response.end(
            JSON.stringify({ authority: "legacy", migration_id: null }),
          );
          return;
        }
        for await (const _chunk of request);
        response.setHeader("Content-Type", "application/json");
        if (request.url === "/api/v1/context/search") {
          response.end(
            JSON.stringify({
              schema_version: 1,
              request_id: "11111111-1111-4111-8111-111111111111",
              state: "grounded",
              paths: {
                raw: {
                  status: "empty",
                  reason: null,
                  elapsed_ms: 1,
                  returned_count: 0,
                  truncated: false,
                  truncated_by: [],
                },
                memory: {
                  status: "ok",
                  reason: null,
                  elapsed_ms: 1,
                  returned_count: 1,
                  truncated: false,
                  truncated_by: [],
                },
              },
              items: [
                {
                  kind: "memory",
                  memory_id: "mem-1",
                  revision: 1,
                  content: "用户喜欢深绿色的界面",
                  memory_kind: "chat",
                  evidence_ids: [],
                  matched_by: ["lexical"],
                  updated_at: "2026-09-01T08:00:00Z",
                  truncated: false,
                },
              ],
              used_characters: 10,
              truncated: false,
              total_ms: 2,
              adoption: "unknown",
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

  it("carries the boundary's notice, and exits inside its timeout when the boundary hangs", async () => {
    const { hook, mode } = await noticeHook();
    const input = noticeInput();

    await writeFile(mode, "answer");
    const answered = await run(hook.command, input);
    expect(answered.code).toBe(0);
    expect(
      JSON.parse(answered.stdout).hookSpecificOutput.additionalContext,
    ).toBe(
      "<memhub_session_notice>\n来源 codex om-derived:abc\n</memhub_session_notice>",
    );

    await writeFile(mode, "hang");
    const hung = await run(hook.command, input);
    expect(hung.code).toBe(0);
    expect(hung.stdout).toBe("");
    expect(hung.ms).toBeLessThan(4_000);
  }, 40_000);
});

// The boundary's 4 s budget runs from the entry's own start, which a loaded
// machine can use up before the fake boundary runs.
describe("a notice is confirmed only after it has been handed over", {
  retry: 2,
}, () => {
  it("confirms exactly what it handed over, without making the host wait", async () => {
    const { hook, mode, acks } = await noticeHook();
    await writeFile(mode, "slow-ack");

    const answered = await run(hook.command, noticeInput());

    expect(answered.code).toBe(0);
    expect(answered.stdout).toContain("om-derived:abc");
    expect(answered.stdout).not.toContain(TOKEN);
    // The confirmation takes 8 s on purpose, past the hook's own timeout;
    // the hook must not wait for it.
    expect(answered.ms).toBeLessThan(hook.timeout * 1000 - 500);
    expect(await eventually(acks, 15_000)).toBe(`${TOKEN}\n`);
  }, 30_000);

  it("keeps this round's output when confirming fails", async () => {
    const { hook, mode, acks } = await noticeHook();
    await writeFile(mode, "ack-fails");

    const answered = await run(hook.command, noticeInput());

    expect(answered.code).toBe(0);
    expect(
      JSON.parse(answered.stdout).hookSpecificOutput.additionalContext,
    ).toContain("om-derived:abc");
    expect(await eventually(acks)).toBe("attempted\n");
  }, 20_000);

  it("does not confirm when the boundary hangs past its budget", async () => {
    const { hook, mode, acks, peeked } = await noticeHook();
    await writeFile(mode, "hang");

    const hung = await run(hook.command, noticeInput());

    expect(hung.stdout).toBe("");
    expect(await readFile(peeked, "utf8")).toBe("peeked\n");
    expect(await settled(acks)).toBeNull();
  }, 20_000);

  it("does not confirm when the hook is killed before it hands anything over", async () => {
    const { hook, mode, acks, peeked } = await noticeHook();
    await writeFile(mode, "slow-close");

    const killed = await run(hook.command, noticeInput(), {
      killWhen: peeked,
    });

    expect(killed.stdout).toBe("");
    expect(await settled(acks, 4_000)).toBeNull();
  }, 20_000);

  it("does not confirm when writing the output fails", async () => {
    const { core, mode, acks, peeked } = await noticeHook();
    await writeFile(mode, "answer");

    // The installed hook captures this entry's stdout in `$(...)`, which
    // always accepts the write, so the entry is run here on its own.
    const entry = [
      "ONE_MEMORY_BROKER_DISCOVERY=/nonexistent/broker.json",
      `ONE_MEMORY_BOUNDARY_COMMAND='${core}'`,
      `'${process.execPath}'`,
      `'${path.join(process.cwd(), "dist", "recall-cli.cjs")}'`,
      "--inject workbuddy",
    ].join(" ");
    await run(entry, noticeInput(), { closeStdout: true });

    // The peek did happen, so there was something to confirm.
    expect(await readFile(peeked, "utf8")).toBe("peeked\n");
    expect(await settled(acks)).toBeNull();
  }, 20_000);
});

const TOKEN = "tok-handed-over";

async function noticeHook() {
  const root = await mkdtemp(path.join(os.tmpdir(), "om-wb-notice-rt-"));
  roots.push(root);
  const core = path.join(root, "one-memory");
  const mode = path.join(root, "mode");
  const acks = path.join(root, "acks");
  const peeked = path.join(root, "peeked");
  const captured = path.join(root, "captured");
  const started = path.join(root, "capture-started");
  const reply = JSON.stringify({
    hookSpecificOutput: {
      hookEventName: "UserPromptSubmit",
      additionalContext: "来源 codex om-derived:abc",
    },
    one_memory_ack: TOKEN,
  });
  // One script, switched by a file, because the hook bakes the path in.
  await writeFile(
    core,
    [
      "#!/bin/sh",
      `m=$(cat '${mode}')`,
      'if [ "$1 $2" = "boundary --ack" ]; then',
      `  if [ "$m" = ack-fails ]; then echo attempted >> '${acks}'; exit 1; fi`,
      '  if [ "$m" = slow-ack ]; then sleep 8; fi',
      `  printf '%s\\n' "$3" >> '${acks}'`,
      "  exit 0",
      "fi",
      'if [ "$1 $2" = "capture workbuddy" ]; then',
      // One file, written whole: the capture's deadline, and that it started.
      `  printf '%s\\n' "$ONE_MEMORY_CAPTURE_DEADLINE_S" > '${started}.part' && mv '${started}.part' '${started}'`,
      '  if [ "$m" = capture-hangs ]; then sleep 30; fi',
      '  case "$m" in capture-slow*) sleep 8 ;; esac',
      `  cat > '${captured}.part' && mv '${captured}.part' '${captured}'`,
      "  exit 0",
      "fi",
      '[ "$1 $2" = "boundary --peek" ] || exit 9',
      "cat >/dev/null",
      `printf '%s\\n' '${reply}'`,
      `echo peeked > '${peeked}'`,
      // A reply that never closes: it was read but must not be handed over.
      'if [ "$m" = hang ]; then sleep 30; fi',
      'if [ "$m" = slow-close ]; then sleep 3; fi',
      // Holds the hook to its own budget, so the host's kill lands mid-run.
      'if [ "$m" = capture-slow-hook ]; then sleep 30; fi',
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
  // These cases preserve the legacy executable protocol itself. Production
  // hooks use the resident broker, covered by recall-injection.test.ts.
  const prompt: Array<{ command: string; timeout: number }> =
    hooks.hooks.UserPromptSubmit.flatMap(
      (entry: { hooks: Array<{ command: string; timeout: number }> }) =>
        entry.hooks,
    );
  for (const entry of [hook, ...prompt]) {
    entry.command = entry.command.replace(
      /ONE_MEMORY_BOUNDARY_VIA_BROKER=\S+/g,
      "ONE_MEMORY_BOUNDARY_VIA_BROKER=0",
    );
  }
  return { hook, prompt, core, mode, acks, peeked, captured, started };
}

function noticeInput() {
  return JSON.stringify({
    session_id: "sess-notice",
    hook_event_name: "UserPromptSubmit",
    prompt: "按钮用什么颜色",
  });
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
    const { prompt, mode, started } = await noticeHook();
    await writeFile(mode, "capture-hangs");

    // As the host runs them: every hook on the event at once.
    const results = await Promise.all(
      prompt.map((hook) =>
        run(hook.command, noticeInput(), {
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
    const { prompt, mode, captured } = await noticeHook();
    await writeFile(mode, "answer");
    const input = noticeInput();

    await Promise.all(prompt.map((hook) => run(hook.command, input)));

    expect(await eventually(captured, 10_000)).toBe(input);
  }, 30_000);

  it("gives capture a deadline of its own, since nothing is left to stop it", async () => {
    const { hook, mode, started } = await noticeHook();
    await writeFile(mode, "answer");

    await run(hook.command, noticeInput());

    expect(await eventually(started, 10_000)).toBe("30\n");
  }, 30_000);

  it("finishes capturing after the hook itself has exited", async () => {
    const { hook, mode, captured } = await noticeHook();
    await writeFile(mode, "capture-slow");
    const input = noticeInput();

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
      const { hook, mode, captured, started } = await noticeHook();
      await writeFile(mode, "capture-slow-hook");
      const input = noticeInput();
      const result = await run(hook.command, input, { killShellWhen: started });
      if (result.code !== null) continue;
      expect(await eventually(captured, 20_000)).toBe(input);
      return;
    }
    expect.fail("the host's kill never landed while the hook was running");
  }, 120_000);

  it("does not capture a session start, which carries no words", async () => {
    const { hook, mode, captured, peeked } = await noticeHook();
    await writeFile(mode, "answer");

    await run(
      hook.command,
      JSON.stringify({
        session_id: "sess-notice",
        hook_event_name: "SessionStart",
        source: "startup",
      }),
    );

    // The same entry did run and reach Core.
    expect(await readFile(peeked, "utf8")).toBe("peeked\n");
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

    const result = await run(command, noticeInput());

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
      const result = await run(await runtime(body), noticeInput());
      expect(result.code, body).toBe(0);
      expect(result.stdout, body).toBe("");
    }
  }, 20_000);

  it("keeps delivery diagnosis silent unless its explicit switch is set", async () => {
    const command = await runtime(
      "printf '%s\\n' '{\"suppressOutput\":true}'; printf '%s\\n' 'one-memory delivery failed: host_scope_denied' >&2",
    );

    const quiet = await run(command, noticeInput());
    expect(quiet.code).toBe(0);
    expect(quiet.stdout).toBe('{"suppressOutput":true}\n');
    expect(quiet.stderr).toBe("");

    const diagnosed = await run(command, noticeInput(), {
      env: { ...process.env, ONE_MEMORY_DEBUG_DELIVERY: "1" },
    });
    expect(diagnosed.code).toBe(0);
    expect(diagnosed.stdout).toBe('{"suppressOutput":true}\n');
    expect(diagnosed.stderr).toBe(
      "one-memory delivery failed: host_scope_denied\n",
    );
  }, 20_000);
});
