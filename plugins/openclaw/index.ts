/**
 * OpenClaw HY Memory Plugin
 *
 * Self-hosted intelligent agent memory powered by HY Memory SDK.
 * Runs a local Python server and communicates via HTTP.
 *
 * Features:
 * - 6 core tools: memory_search, memory_add, memory_get, memory_list,
 *   memory_update, memory_delete
 * - Auto-recall: injects relevant memories before each agent turn
 * - Auto-capture: stores key facts after each agent turn
 * - Auto-starts local Python server (hy_memory.server)
 * - CLI: openclaw hy-memory status, search, etc.
 */

import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { spawn, execSync } from "node:child_process";
import type { ChildProcess } from "node:child_process";
import { existsSync } from "node:fs";
import { join } from "node:path";
import type { HyMemoryConfig } from "./types.ts";
import {
  VENV_DIR,
  execPythonModule,
  findSystemPython,
  getVenvPythonPath,
  isAutoPythonPath,
  venvLayoutExists,
} from "./python-env.ts";
import { hyMemoryConfigSchema, buildServerEnv } from "./config.ts";
import { HttpProvider } from "./provider.ts";
import { registerAllTools } from "./tools/index.ts";

declare const __OPENCLAW_PLUGIN_VERSION__: string;

// ============================================================================
// Python Environment Manager
// ============================================================================

const SDK_PACKAGE = "hy-memory";
const SDK_VERSION = process.env.HY_MEMORY_SDK_VERSION?.trim() ?? "";
const PYPI_INDEX_URL =
  process.env.HY_MEMORY_PYPI_INDEX_URL ??
  "https://pypi.org/simple";

function isVenvReady(): boolean {
  const python = getVenvPythonPath();
  if (!existsSync(python)) return false;
  try {
    execSync(`${python} -c "import hy_memory.server"`, { stdio: "ignore", timeout: 10_000 });
    return true;
  } catch {
    return false;
  }
}

function ensureVenv(logger: OpenClawPluginApi["logger"], cfg?: HyMemoryConfig): string {
  const python = getVenvPythonPath();

  // Determine pip install spec based on vector store provider.
  // Extras come before the version pin: pkg[extra]==X.Y.Z
  const vsProvider = cfg?.vectorStore?.provider?.toLowerCase() ?? "";
  let extras = "";
  if (vsProvider === "qdrant") extras = "[qdrant]";
  else if (vsProvider === "faiss") extras = "[faiss]";
  const versionSpec = SDK_VERSION ? `==${SDK_VERSION}` : "";
  const pipSpec = `${SDK_PACKAGE}${extras}${versionSpec}`;

  // Custom HTTP indexes may require --trusted-host. Public PyPI uses HTTPS.
  const trustedHostArgs = PYPI_INDEX_URL.startsWith("http://")
    ? `--trusted-host ${new URL(PYPI_INDEX_URL).host}`
    : "";
  const pipIndexArgs = `--index-url ${PYPI_INDEX_URL} ${trustedHostArgs}`.trim();

  if (isVenvReady()) {
    logger.info(`openclaw-hy-memory: venv ready at ${VENV_DIR}`);
    // Always attempt upgrade to ensure latest SDK version + extras
    try {
      execSync(
        `${python} -m pip install --quiet --upgrade ${pipIndexArgs} ${pipSpec}`,
        { stdio: "pipe", timeout: 120_000 },
      );
    } catch {
      // Non-fatal: network issues shouldn't block startup
      logger.warn("openclaw-hy-memory: SDK upgrade check failed (will use existing version)");
    }
    return python;
  }

  logger.info(`openclaw-hy-memory: setting up Python environment...`);

  const sysPython = findSystemPython();

  if (!venvLayoutExists()) {
    logger.info(`openclaw-hy-memory: creating venv with ${sysPython}...`);
    execPythonModule(sysPython, `venv "${VENV_DIR}"`, 30_000);
  }

  // Install SDK
  logger.info(
    `openclaw-hy-memory: installing ${pipSpec} from ${PYPI_INDEX_URL} (this may take 1-2 minutes on first run)...`,
  );
  try {
    execSync(
      `${python} -m pip install --quiet ${pipIndexArgs} ${pipSpec}`,
      { stdio: "pipe", timeout: 300_000 },
    );
    logger.info(`openclaw-hy-memory: SDK installed successfully`);
  } catch (err) {
    logger.error(`openclaw-hy-memory: failed to install SDK: ${err}`);
    throw err;
  }

  return python;
}

// ============================================================================
// Python Server Process Manager
// ============================================================================

let serverProcess: ChildProcess | null = null;

/**
 * Detect and kill a stuck process occupying the given port.
 * This handles the case where a previous Python server is stuck (e.g. blocked
 * on a long LLM call) and not responding to /healthz, preventing a new server
 * from binding to the port.
 */
async function killStuckProcessOnPort(
  port: number,
  logger: OpenClawPluginApi["logger"],
): Promise<void> {
  try {
    // Use lsof (macOS/Linux) to find PID listening on the port
    const result = execSync(
      `lsof -ti tcp:${port} 2>/dev/null || ss -tlnp 'sport = :${port}' 2>/dev/null | grep -oP 'pid=\\K[0-9]+'`,
      { encoding: "utf-8", timeout: 5_000 },
    ).trim();

    if (!result) return;

    // May have multiple PIDs (one per line)
    const pids = result
      .split(/\s+/)
      .map((s) => parseInt(s, 10))
      .filter((n) => n > 0 && n !== process.pid);

    if (pids.length === 0) return;

    logger.warn(
      `openclaw-hy-memory: port ${port} occupied by stuck process(es): ${pids.join(", ")}. Killing...`,
    );

    for (const pid of pids) {
      try {
        process.kill(pid, "SIGKILL");
        logger.info(`openclaw-hy-memory: killed stuck PID ${pid}`);
      } catch (e: any) {
        // ESRCH = process already gone, that's fine
        if (e?.code !== "ESRCH") {
          logger.warn(`openclaw-hy-memory: failed to kill PID ${pid}: ${e}`);
        }
      }
    }

    // Give OS a moment to release the port
    await new Promise((r) => setTimeout(r, 500));
  } catch {
    // lsof/ss not available or failed — skip, the spawn will fail with EADDRINUSE
  }
}

async function startPythonServer(
  cfg: HyMemoryConfig,
  logger: OpenClawPluginApi["logger"],
): Promise<boolean> {
  // Check if server is already running
  const provider = new HttpProvider(cfg);
  if (await provider.healthCheck()) {
    logger.info("openclaw-hy-memory: server already running");
    return true;
  }

  // Port is occupied but not healthy — kill the stuck process
  await killStuckProcessOnPort(cfg.serverPort, logger);

  if (!cfg.autoStartServer) {
    logger.warn(
      "openclaw-hy-memory: server not running and autoStartServer is disabled",
    );
    return false;
  }

  // Ensure Python venv + SDK are ready
  let pythonPath: string;
  try {
    pythonPath = isAutoPythonPath(cfg.pythonPath)
      ? ensureVenv(logger, cfg)
      : cfg.pythonPath;
  } catch (err) {
    logger.error(`openclaw-hy-memory: Python setup failed: ${err}`);
    return false;
  }

  logger.info(
    `openclaw-hy-memory: starting Python server on port ${cfg.serverPort} (python: ${pythonPath})...`,
  );

  const serverEnv = buildServerEnv(cfg);

  return new Promise((resolve) => {
    const proc = spawn(
      pythonPath,
      ["-m", "hy_memory.server", "--port", String(cfg.serverPort)],
      {
        stdio: ["ignore", "pipe", "pipe"],
        detached: false,
        env: { ...process.env, ...serverEnv },
      },
    );

    serverProcess = proc;

    let resolved = false;
    let stderr = "";

    proc.stdout?.on("data", (data: Buffer) => {
      const line = data.toString().trim();
      if (line) logger.debug(`[hy-memory-server] ${line}`);
      // Detect server ready
      if (!resolved && line.includes("Listening on")) {
        resolved = true;
        resolve(true);
      }
    });

    proc.stderr?.on("data", (data: Buffer) => {
      const line = data.toString().trim();
      if (line) {
        logger.warn(`[hy-memory-server:stderr] ${line}`);
        stderr += line + "\n";
      }
      // Python logging goes to stderr
      if (!resolved && line.includes("Listening on")) {
        resolved = true;
        resolve(true);
      }
    });

    proc.on("error", (err) => {
      logger.error(`openclaw-hy-memory: failed to start server: ${err}`);
      if (!resolved) {
        resolved = true;
        resolve(false);
      }
    });

    proc.on("exit", (code) => {
      logger.info(
        `openclaw-hy-memory: server exited with code ${code}`,
      );
      serverProcess = null;
      if (!resolved) {
        resolved = true;
        resolve(false);
      }
    });

    // Timeout: if server hasn't started in 30s, give up
    setTimeout(async () => {
      if (!resolved) {
        // One more health check before giving up
        const ok = await provider.healthCheck();
        resolved = true;
        resolve(ok);
      }
    }, 30_000);
  });
}

function stopPythonServer(logger: OpenClawPluginApi["logger"]): void {
  if (serverProcess) {
    const proc = serverProcess;
    serverProcess = null;
    logger.info("openclaw-hy-memory: stopping Python server...");
    proc.kill("SIGTERM");

    // SIGKILL fallback: if process doesn't exit within 3s, force kill
    const killTimer = setTimeout(() => {
      try {
        proc.kill("SIGKILL");
        logger.warn("openclaw-hy-memory: server did not exit after SIGTERM, sent SIGKILL");
      } catch { /* already dead */ }
    }, 3_000);

    proc.on("exit", () => {
      clearTimeout(killTimer);
    });
  }
}

// ============================================================================
// Plugin Definition
// ============================================================================

const hyMemoryPlugin = definePluginEntry({
  id: "openclaw-hy-memory",
  name: "Memory (HY Memory)",
  description:
    "HY Memory — self-hosted intelligent agent memory with LLM-driven extraction and evolution chains",

  register(api: OpenClawPluginApi) {
    const cfg = hyMemoryConfigSchema.parse(api.pluginConfig);
    const provider = new HttpProvider(cfg);

    // Print full configuration on register
    api.logger.info(
      `openclaw-hy-memory: registered (user: ${cfg.userId}, server: ${cfg.serverUrl}, autoRecall: ${cfg.autoRecall}, autoCapture: ${cfg.autoCapture})`,
    );
    api.logger.info(
      `openclaw-hy-memory: config → mode=${cfg.mode}, port=${cfg.serverPort}, topK=${cfg.topK}, threshold=${cfg.searchThreshold}, writeTurnWindow=${cfg.memoryWriteTurnWindow}, autoStart=${cfg.autoStartServer}`,
    );
    if (cfg.mode === "ultra") {
      api.logger.info("openclaw-hy-memory: graph → provider=kuzu (embedded, local files)");
    }
    if (cfg.llm) {
      api.logger.info(
        `openclaw-hy-memory: llm → provider=${cfg.llm.provider}, model=${cfg.llm.model}, baseUrl=${cfg.llm.baseUrl ?? "(default)"}, thinkingMode=${cfg.llm.thinkingMode ?? "auto"}`,
      );
    }
    if (cfg.embedder) {
      api.logger.info(
        `openclaw-hy-memory: embedder → provider=${cfg.embedder.provider}, model=${cfg.embedder.model}, dims=${cfg.embedder.dims ?? "auto"}, baseUrl=${cfg.embedder.baseUrl ?? "(default)"}`,
      );
    }
    if (cfg.vectorStore) {
      api.logger.info(
        `openclaw-hy-memory: vectorStore → provider=${cfg.vectorStore.provider}, host=${cfg.vectorStore.host ?? "embedded"}, port=${cfg.vectorStore.port ?? "-"}`,
      );
    }

    // ========================================================================
    // Tools
    // ========================================================================

    registerAllTools({ api, provider, cfg });

    // ========================================================================
    // CLI Commands
    // ========================================================================

    api.registerCli(
      ({ program }) => {
        const cmd = program
          .command("hy-memory")
          .description("HY Memory plugin commands");

        cmd
          .command("status")
          .description("Check server connectivity and component health")
          .action(async () => {
            try {
              const resp = await fetch(`${cfg.serverUrl}/api/v1/status`, {
                signal: AbortSignal.timeout(15_000),
              });
              const health = (await resp.json()) as Record<string, unknown>;

              console.log(`HY Memory Server: ${cfg.serverUrl}`);
              console.log(`  Status:     ${health.status === "ok" ? "✓ healthy" : "⚠ degraded"}`);
              console.log(`  User ID:    ${cfg.userId}`);
              console.log(`  VDB:        ${health.vdb}${health.vdb_provider ? ` [${health.vdb_provider}]` : ""}${health.vdb_collection ? ` (collection: ${health.vdb_collection}, points: ${health.vdb_points})` : ""}`);
              console.log(`  Embed:      ${health.embed}${health.embed_dims ? ` (dims: ${health.embed_dims})` : ""}`);
              console.log(`  LLM:        ${health.llm}`);

              try {
                const infoResp = await fetch(`${cfg.serverUrl}/info`);
                const info = (await infoResp.json()) as Record<string, unknown>;
                console.log(`  SDK Version: ${info.version ?? "unknown"}`);
              } catch { /* ignore */ }
            } catch {
              console.log(`Cannot reach HY Memory server at ${cfg.serverUrl}`);
            }
          });

        cmd
          .command("search")
          .description("Search memories")
          .argument("<query>", "Search query")
          .option("--limit <n>", "Max results", String(cfg.topK))
          .option("--user-id <id>", "Override user ID")
          .action(
            async (
              query: string,
              opts: { limit: string; userId?: string },
            ) => {
              try {
                const result = await provider.search(query, {
                  user_id: opts.userId || cfg.userId,
                  limit: parseInt(opts.limit, 10),
                });
                if (!result.memories.length) {
                  console.log("No memories found.");
                  return;
                }
                for (const m of result.memories) {
                  const chain = m.evolution_chain;
                  if (chain && Array.isArray(chain) && chain.length > 1) {
                    console.log(
                      `[${(m.score ?? 0).toFixed(2)}] [${m.layer}] [Evolved, ${chain.length} versions] (${m.memory_id})`,
                    );
                    for (let i = chain.length - 1; i >= 1; i--) {
                      console.log(`    [v${chain.length - i}] ${chain[i].content}`);
                    }
                    console.log(`    [Latest] ${chain[0].content}`);
                  } else {
                    console.log(
                      `[${(m.score ?? 0).toFixed(2)}] [${m.layer}] ${m.content} (${m.memory_id})`,
                    );
                  }
                }
                console.log(`\n${result.memories.length} result(s)`);
              } catch (err) {
                console.error(`Search failed: ${String(err)}`);
              }
            },
          );

        cmd
          .command("add")
          .description("Add a memory")
          .argument("<text>", "Text to store")
          .option("--user-id <id>", "Override user ID")
          .action(async (text: string, opts: { userId?: string }) => {
            try {
              const result = await provider.add(text, {
                user_id: opts.userId || cfg.userId,
              });
              if (result.success) {
                console.log(`Memory stored: ${result.memory_id}`);
              } else {
                console.error(`Failed: ${result.error_message}`);
              }
            } catch (err) {
              console.error(`Add failed: ${String(err)}`);
            }
          });

        cmd
          .command("list")
          .description("List memories")
          .option("--user-id <id>", "Override user ID")
          .option("--limit <n>", "Max results", "50")
          .action(async (opts: { userId?: string; limit: string }) => {
            try {
              const result = await provider.list({
                user_id: opts.userId || cfg.userId,
                limit: parseInt(opts.limit, 10),
              });
              const out: Record<string, unknown> = { vdb: result.vdb };
              if (result.graph) {
                out.graph = result.graph;
              }
              console.log(JSON.stringify(out, null, 2));
            } catch (err) {
              console.error(`List failed: ${String(err)}`);
            }
          });

        cmd
          .command("inspect")
          .description("Inspect a single memory by ID")
          .argument("<id>", "Memory ID")
          .action(async (id: string) => {
            try {
              const resp = await fetch(`${cfg.serverUrl}/api/v1/memories/${id}`);
              if (!resp.ok) {
                console.error(`Not found: ${id}`);
                return;
              }
              const data = await resp.json();
              console.log(JSON.stringify(data, null, 2));
            } catch (err) {
              console.error(`Inspect failed: ${String(err)}`);
            }
          });

        cmd
          .command("delete")
          .description("Delete a single memory by ID")
          .argument("<id>", "Memory ID")
          .action(async (id: string) => {
            try {
              const resp = await fetch(`${cfg.serverUrl}/api/v1/memories/${id}`, { method: "DELETE" });
              const data = (await resp.json()) as Record<string, unknown>;
              if (data.success) {
                console.log(`Deleted: ${id}`);
              } else {
                console.error(`Delete failed: ${JSON.stringify(data)}`);
              }
            } catch (err) {
              console.error(`Delete failed: ${String(err)}`);
            }
          });

        cmd
          .command("reset")
          .description("Delete ALL memories for current user (dangerous!)")
          .option("--yes", "Skip confirmation")
          .action(async (opts: { yes?: boolean }) => {
            if (!opts.yes) {
              const readline = await import("node:readline");
              const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
              const answer = await new Promise<string>((res) =>
                rl.question(`Delete ALL memories for user "${cfg.userId}"? [y/N]: `, (a) => { rl.close(); res(a); })
              );
              if (answer.toLowerCase() !== "y") {
                console.log("Aborted.");
                return;
              }
            }
            try {
              const result = await provider.deleteAll({ user_id: cfg.userId });
              console.log(`Deleted ${result.deleted_count} memories.`);
            } catch (err) {
              console.error(`Reset failed: ${String(err)}`);
            }
          });

        cmd
          .command("init")
          .description("Interactive setup wizard for HY Memory")
          .action(async () => {
            const { runInitWizard } = await import("./cli/init-wizard.js");
            await runInitWizard();
          });

        cmd
          .command("import-from-openclaw")
          .description("Migrate legacy OpenClaw memory-core memories into HY Memory")
          .option(
            "--workspace <dir>",
            "OpenClaw agent workspace dir (auto-detected from config if omitted)",
          )
          .option("--agent <id>", "OpenClaw agent id", "main")
          .option("--state-dir <dir>", "OpenClaw state dir (default: ~/.openclaw)")
          .option("--user-id <id>", "Target HY Memory user id (default: configured userId)")
          .option("--dry-run", "Preview the migration plan without writing memories")
          .option("--force", "Re-import everything, ignoring the import ledger")
          .option("--retries <n>", "Retries per unit on transient errors (429/5xx)", "4")
          .option("--yes", "Skip the confirmation prompt")
          .action(
            async (opts: {
              workspace?: string;
              agent?: string;
              stateDir?: string;
              userId?: string;
              dryRun?: boolean;
              force?: boolean;
              retries?: string;
              yes?: boolean;
            }) => {
              const { runImportFromOpenclaw } = await import(
                "./cli/import-from-openclaw.js"
              );
              await runImportFromOpenclaw(
                {
                  workspace: opts.workspace,
                  agent: opts.agent || "main",
                  stateDir: opts.stateDir,
                  userId: opts.userId,
                  dryRun: Boolean(opts.dryRun),
                  force: Boolean(opts.force),
                  maxRetries: Math.max(0, parseInt(opts.retries ?? "4", 10) || 4),
                  yes: Boolean(opts.yes),
                },
                { provider, cfg },
              );
            },
          );
      },
      {
        descriptors: [
          {
            name: "hy-memory",
            description: "HY Memory plugin commands",
            hasSubcommands: true,
          },
        ],
      },
    );

    // ========================================================================
    // Lifecycle Hooks
    // ========================================================================

    // Auto-recall: inject relevant memories before each agent turn
    if (cfg.autoRecall) {
      api.on("before_prompt_build", async (event: any, _ctx: any) => {
        if (!event.prompt || event.prompt.length < 5) return;

        // Skip system/internal prompts (heartbeat, diagnostics, etc.)
        const prompt: string = event.prompt;
        if (
          prompt.includes("HEARTBEAT") ||
          prompt.includes("heartbeat") ||
          prompt.startsWith("Read HEARTBEAT")
        ) return;

        try {
          const result = await provider.search(event.prompt, {
            user_id: cfg.userId,
            limit: cfg.topK,
            min_score: cfg.searchThreshold,
          });

          if (!result.memories || result.memories.length === 0) return;

          // Helper: format unix timestamp to readable local datetime (minute precision)
          function formatTime(ts: any): string {
            if (!ts) return "";
            const d = typeof ts === "number" ? new Date(ts * 1000) : new Date(ts);
            if (isNaN(d.getTime())) return "";
            const pad = (n: number) => String(n).padStart(2, "0");
            return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
          }

          const memoryContext = result.memories
            .map((r: any, idx: number) => {
              const chain = r.evolution_chain;
              if (chain && Array.isArray(chain) && chain.length > 1) {
                // Evolved memory: show from oldest to newest
                const lines: string[] = [];
                // chain[0] is latest, chain[n-1] is oldest. Reverse for old→new display.
                for (let i = chain.length - 1; i >= 1; i--) {
                  const when = formatTime(chain[i].memory_at);
                  lines.push(`  [v${chain.length - i}] ${when ? when + "  " : ""}${chain[i].content}`);
                }
                // Latest
                const head = chain[0];
                const headWhen = formatTime(head.memory_at);
                lines.push(`  [Latest] ${headWhen ? headWhen + "  " : ""}${head.content ?? r.content}`);
                return `- [${idx + 1}] [Evolved, ${chain.length} versions]\n${lines.join("\n")}`;
              }
              const when = formatTime(r.memory_at);
              return `- [${idx + 1}] ${when ? when + "  " : ""}${r.content}`;
            })
            .join("\n");

          api.logger.info(
            `openclaw-hy-memory: injecting ${result.memories.length} memories into context`,
          );
          api.logger.info(
            `openclaw-hy-memory: [recall-context] ${JSON.stringify(result.memories.map((r: any) => ({ layer: r.layer, content: r.content, score: r.score, evolved: !!r.evolution_chain })))}`,
          );

          return {
            prependContext: `<relevant-memories>\nThe following are stored memories for user "${cfg.userId}". Use them to personalize your response. Memories with evolution chains are expanded from oldest to newest:\n${memoryContext}\n</relevant-memories>`,
          };
        } catch (err) {
          api.logger.warn(
            `openclaw-hy-memory: recall failed: ${String(err)}`,
          );
        }
      });
    }

    // Auto-capture: store conversation incrementally (every N turns or on session end)
    if (cfg.autoCapture) {
      const CAPTURE_INTERVAL = cfg.memoryWriteTurnWindow;
      const autoCaptureCursors = new Map<string, { nextIndex: number; fingerprint: string }>();
      const turnCounters = new Map<string, number>();
      const lastMessagesCache = new Map<string, any[]>();
      const captureWrites = new Map<string, Promise<void>>();

      function messageFingerprint(msg: any): string {
        const text = typeof msg?.content === "string"
          ? msg.content.slice(0, 100)
          : JSON.stringify(msg?.content).slice(0, 100);
        return `${msg?.role}:${text}`;
      }

      function resolveStartIndex(
        messages: any[],
        cursor?: { nextIndex: number; fingerprint: string },
      ): number {
        if (!cursor) return 0;
        // If cursor is beyond messages length, session was likely reset
        if (cursor.nextIndex > messages.length) return 0;
        return cursor.nextIndex;
      }

      function extractMessages(
        allMessages: any[],
        startIndex: number,
      ): Array<{ role: string; content: string }> {
        const result: Array<{ role: string; content: string }> = [];
        for (let i = startIndex; i < allMessages.length; i++) {
          const msg = allMessages[i];
          if (!msg || typeof msg !== "object") continue;
          const role = msg.role;
          if (role !== "user" && role !== "assistant") continue;
          let textContent = "";
          if (typeof msg.content === "string") {
            textContent = msg.content;
          } else if (Array.isArray(msg.content)) {
            for (const block of msg.content) {
              if (block?.type === "text" && typeof block.text === "string") {
                textContent += (textContent ? "\n" : "") + block.text;
              }
            }
          }
          if (textContent.length > 10) {
            // Strip injected memory context to avoid memory nesting
            if (textContent.includes("<relevant-memories>")) {
              textContent = textContent.replace(/<relevant-memories>[\s\S]*?<\/relevant-memories>\s*/g, "").trim();
              if (!textContent || textContent.length <= 10) continue;
            }
            result.push({ role, content: textContent });
          }
        }
        return result;
      }

      async function captureMessages(
        messages: any[],
        cursorKey: string,
      ) {
        const cursor = autoCaptureCursors.get(cursorKey);
        const startIndex = resolveStartIndex(messages, cursor);

        if (startIndex >= messages.length) return; // Nothing new

        const nextCursor = {
          nextIndex: messages.length,
          fingerprint: messageFingerprint(messages[messages.length - 1]),
        };
        const incremental = extractMessages(messages, startIndex);
        if (incremental.length === 0 || !incremental.some((m) => m.role === "user")) {
          // Update cursor even if nothing to write (skip non-user messages)
          autoCaptureCursors.set(cursorKey, nextCursor);
          return;
        }

        api.logger.info(
          `openclaw-hy-memory: doCapture cursor=${cursorKey} startIndex=${startIndex} totalMessages=${messages.length} incrementalCount=${incremental.length}`,
        );
        for (let i = 0; i < incremental.length; i++) {
          api.logger.info(
            `openclaw-hy-memory: [capture][${i}] ${JSON.stringify(incremental[i])}`,
          );
        }

        const result = await provider.add(incremental, {
          user_id: cfg.userId,
          agent_id: cfg.agentId,
          session_id: cfg.sessionId,
        });
        if (!result.success) {
          throw new Error(result.error_message ?? "Memory capture was not acknowledged");
        }

        // Only acknowledged writes advance the cursor. Same-session calls are
        // serialized below, so a failed prefix remains available for retry.
        autoCaptureCursors.set(cursorKey, nextCursor);

        api.logger.info(
          `openclaw-hy-memory: auto-captured ${incremental.length} messages (from index ${startIndex})`,
        );
      }

      async function doCapture(messages: any[], cursorKey: string) {
        const previous = captureWrites.get(cursorKey) ?? Promise.resolve();
        const pending = previous.catch(() => {}).then(() => captureMessages(messages, cursorKey));
        captureWrites.set(cursorKey, pending);
        try {
          await pending;
        } finally {
          if (captureWrites.get(cursorKey) === pending) captureWrites.delete(cursorKey);
        }
      }

      api.on("agent_end", async (event: any, ctx: any) => {
        if (!event.success || !event.messages || event.messages.length === 0) {
          return;
        }

        const cursorKey = ctx?.sessionKey ?? ctx?.sessionId ?? "default";
        // Cache messages for session_end flush
        lastMessagesCache.set(cursorKey, event.messages);

        const count = (turnCounters.get(cursorKey) ?? 0) + 1;
        turnCounters.set(cursorKey, count);

        // Only write every CAPTURE_INTERVAL turns
        if (count % CAPTURE_INTERVAL !== 0) return;

        try {
          await doCapture(event.messages, cursorKey);
        } catch (err) {
          api.logger.warn(
            `openclaw-hy-memory: capture failed: ${String(err)}`,
          );
        }
      });

      // Flush remaining on session end
      api.on("session_end", async (event: any, ctx: any) => {
        const cursorKey = ctx?.sessionKey ?? event?.sessionKey ?? ctx?.sessionId ?? event?.sessionId ?? "default";
        // Use cached messages since session_end doesn't provide them
        const messages = lastMessagesCache.get(cursorKey);

        if (messages && messages.length > 0) {
          try {
            await doCapture(messages, cursorKey);
            api.logger.info(`openclaw-hy-memory: session_end flushed remaining messages`);
          } catch (err) {
            api.logger.warn(
              `openclaw-hy-memory: session_end capture failed: ${String(err)}`,
            );
          }
        }

        // Cleanup
        autoCaptureCursors.delete(cursorKey);
        turnCounters.delete(cursorKey);
        lastMessagesCache.delete(cursorKey);
      });
    }

    // ========================================================================
    // Service (lifecycle management)
    // ========================================================================

    // ========================================================================
    // Lifecycle: start/stop Python server with Gateway
    // ========================================================================

    api.on("gateway_start", async () => {
      const ok = await startPythonServer(cfg, api.logger);
      if (ok) {
        api.logger.info(
          `openclaw-hy-memory: server ready at ${cfg.serverUrl}`,
        );
      } else {
        api.logger.warn(
          "openclaw-hy-memory: server not available. Memory features may not work.\n" +
            `  Start manually: ${cfg.pythonPath} -m hy_memory.server --port ${cfg.serverPort}`,
        );
      }
    });

    api.on("gateway_stop", () => {
      stopPythonServer(api.logger);
      api.logger.info("openclaw-hy-memory: stopped");
    });
  },
});

export default hyMemoryPlugin;
