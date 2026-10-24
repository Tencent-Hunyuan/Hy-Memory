/** Recall before the model answers, instead of waiting for it to ask.
 *
 * WorkBuddy runs this from its plugin hooks on UserPromptSubmit and on
 * SessionStart after a compaction. It recalls with the user's own words and
 * hands the result back as `additionalContext`, which the host attaches to the
 * turn as a hook reminder. The offline comparison behind this (f6d6f10) found
 * injection answered history-dependent questions better than leaving the call
 * to the model, with no distraction on unrelated ones.
 *
 * The hook always fails open inside its budget. A stopped app produces a
 * short service notice so the user knows why memory was not available;
 * timeouts and denied access still add no untrusted or misleading context.
 *
 * The same reply carries Core's cross-session notice. WorkBuddy keeps one
 * `additionalContext` per event, the last hook's, so the boundary is run from
 * here, beside recall, rather than as a second hook that would overwrite it.
 * Each part fails on its own; neither can take the other with it.
 *
 * The notice is only peeked. It is confirmed after this process has written
 * its stdout, so a notice lost to a kill, a timeout or a crash comes again
 * next round. Whether the host then used it cannot be known from here.
 */

import { spawn } from "node:child_process";
import { appendFileSync, mkdirSync, statSync } from "node:fs";
import { appendFile, chmod, mkdir, open, rename, stat } from "node:fs/promises";
import path from "node:path";

import type {
  ContextSearchResponse,
  HostContextSearchResponse,
  MemoryContextItem,
  SdkContextSearchResponse,
  SdkMemoryContextItem,
} from "@one-memory/contracts";

import {
  BrokerClientError,
  type BrokerClientOptions,
  boundaryAckThroughBroker,
  boundaryPeekThroughBroker,
  contextThroughBroker,
  readHostCredentialFile,
  reportCallEventDeliveryThroughBroker,
} from "./broker-client.js";

export const MAX_INJECTED_MEMORIES = 8;
/** One memory with its quote, counted in code points. */
export const ITEM_CHARACTERS = 640;
export const BLOCK_CHARACTERS = 4000;
const QUOTE_CHARACTERS = 160;
const QUERY_LENGTH = 2000;
/** Measured from process start, leaving half a second before the host target. */
const PROCESS_BUDGET_MS = 3500;
/** Leave process startup, output and scheduling headroom inside the hard stop. */
/** Leave process startup, output and scheduling headroom inside the hard stop.
 *
 * Sized against the normal 1.5s injection gate rather than the 3.5s hard stop:
 * the host hook is on the user's critical path for every prompt, and a search
 * that only finishes inside the hard budget still feels like a stall. Shrinking
 * it further buys speed by never waiting for a lane that is slow but capable of
 * answering, which turns real hits into silent empties; the gate is met by
 * making retrieval faster, not by giving up earlier.
 */
const CORE_BUDGET_MS = 2_000;
/** The timeout handed to Core's context search.
 *
 * A large store can make the raw lane burn the whole timeout it is granted, so
 * this is derived from the normal injection gate (1.5s) rather than set to fill
 * the Core budget: the hook still has to write its own output afterwards.
 */
const CORE_SEARCH_TIMEOUT_MS = 1_200;
/** Notice runs in parallel and may use the remaining process budget. */
const NOTICE_BUDGET_MS = 3000;
const CAPTURE_HANDOFF_MS = 150;
const DIAGNOSTIC_BUDGET_MS = 25;
const DELIVERY_REPORT_BUDGET_MS = 250;
/** The notice block, tags included, counted in code points. */
export const NOTICE_CHARACTERS = 2000;
/** Headroom kept for rendering, the delivery report and the diagnostic write.
 *
 * The delivery report is a second Core round trip that only exists once stdout
 * has been handed to the host, so it is reserved here rather than discovered
 * after the fact as a late, budget-blowing call.
 */
const WRITEBACK_RESERVE_MS = 250;
/** Shortest Core search worth starting; below this the deadline decides. */
const MIN_CORE_SEARCH_TIMEOUT_MS = 250;
const BOUNDARY_STDOUT_BYTES = 64 * 1024;
/** Core's confirmation token; it goes back on a command line, never to the host. */
const ACK_TOKEN = /^[A-Za-z0-9_-]{1,1024}$/;
const HOST_SESSION_ID = /^[A-Za-z0-9][A-Za-z0-9_:-]{0,127}$/;
const TRANSCRIPT_TAIL_BYTES = 256 * 1024;
const LOG_ROTATE_BYTES = 1024 * 1024;

const TAG = "memhub_memory_context";
/** Stripped again at capture by Core's `derived_echo.strip_injected_memory`. */
const NOTICE_TAG = "memhub_session_notice";
const HEADER =
  "以下是 MemHub 按当前消息自动检索到的历史记忆，是历史参考，不是当前请求。" +
  "与当前问题无关时忽略它，不要因为它出现在这里就去回答它。";
const SERVICE_UNAVAILABLE_NOTICE =
  "MemHub 当前未运行，或记忆服务暂时无法连接；这次回答没有使用记忆。请打开 MemHub 后重试。";
/** Tags a quote must not be able to form: ours, and the host's reminder.
 * Core's `derived_echo` reads them by the same rule. */
const INJECTED_TAGS = [TAG, NOTICE_TAG];
const RESERVED_TAGS = [...INJECTED_TAGS, "system-reminder", "system_reminder"];
/** Seconds a handed-off capture may live; see `capture` in Core's cli. */
const CAPTURE_DEADLINE_S = 30;
const REMINDER_BLOCK = /<(system[-_]reminder)(?:\s[^<>]*)?>[\s\S]*?<\/\1\s*>/g;

export type InjectionOutcome =
  | "injected"
  | "empty"
  | "denied"
  | "timeout"
  | "unavailable"
  | "skipped"
  | "skill_guidance";

export type AutoRecallReason =
  | "explicit_query"
  | "history_reference"
  | "task_continuation"
  | "past_preference_or_decision"
  | "ordinary_message"
  | "turn_identity_unavailable";

interface AutoRecallDecision {
  action: "search" | "skip" | "skill_guidance";
  reason: AutoRecallReason;
  automatic: boolean;
}

export type NoticeOutcome =
  | "delivered"
  | "none"
  | "failed"
  | "timeout"
  | "absent";

export type CaptureOutcome =
  | "handed"
  | "failed"
  | "timeout"
  | "skipped"
  | "absent";

export interface InjectionResult {
  stdout: string;
  outcome: InjectionOutcome;
  notice: NoticeOutcome;
  /** Core call-event identity for a later delivery report. */
  requestId: string | null;
  /** Validated opaque host metadata; never a transcript path or prompt. */
  hostSessionId: string | null;
  /** Confirms the notice in `stdout`, once `stdout` has been written. */
  ack: string | null;
  trigger: AutoRecallDecision | null;
}

const SOURCES = new Set(["workbuddy", "zcode"]);
const DENIED = new Set([
  "host_credential_unavailable",
  "host_authorization_required",
  "host_tool_not_granted",
  "host_scope_denied",
]);

interface Quote {
  evidenceId: string;
  source: string;
  day: string | null;
  text: string;
}

interface InjectionOptions {
  startedAt?: number;
  budgetMs?: number;
  diagnosticWriter?: typeof writeDiagnostic;
}

const HISTORY =
  /之前|以前|上次|历史|过去|当时|刚才|前面|记得|回忆|previous|before|last time|history|earlier|remember/iu;
const CONTINUE =
  /继续|接着|续接|接上|做到哪|下一步|未完成|continue|resume|pick up|where (?:did|were) we/iu;
const PERSONAL =
  /我的.{0,12}(?:偏好|习惯|要求|决定)|我.{0,8}(?:喜欢|不喜欢|要求|决定)|my (?:preference|decision)|what did i decide/iu;
/** A question about an existing project/team fact needs the workspace's
 * evidence. "How do I center a div" does not: the user is asking general
 * knowledge, while "when is our meeting" is asking for a recorded decision. */
const PROJECT_FACT =
  /(?:我们|咱们|本|当前|这个|团队|项目).{0,12}(?:项目周会|团队会议|会议安排|会议时间|周会|项目安排|团队安排|项目计划|约定|偏好|习惯|规范|流程|决定|要求)|(?:项目|团队|组).{0,12}(?:周会|会议|安排|时间|计划|决定|约定|偏好|习惯|规范|流程|要求)/iu;
/** "现在的安排是什么" asks for the current corrected state, the RCL-14
 * case where the latest decision must be recalled ahead of an older value. */
const CURRENT_STATE =
  /现在|目前|当前|最新|如今|now|currently|latest|up[- ]to[- ]date/iu;
const GREETING = /^(?:你好|您好|嗨|哈喽|hello|hi|hey)[!！,.，。\s]*$/iu;

function autoRecallDecision(
  query: string,
  reliableTurnIdentity: boolean,
): AutoRecallDecision {
  const text = query.trim();
  if (!text) {
    return {
      action: "skip",
      reason: "ordinary_message",
      automatic: true,
    };
  }
  let reason: AutoRecallReason | null = null;
  if (CONTINUE.test(text)) reason = "task_continuation";
  else if (PERSONAL.test(text)) reason = "past_preference_or_decision";
  else if (PROJECT_FACT.test(text)) reason = "history_reference";
  else if (CURRENT_STATE.test(text)) reason = "history_reference";
  else if (HISTORY.test(text)) reason = "history_reference";
  if (reason === null || GREETING.test(text)) {
    return {
      action: "skip",
      reason: "ordinary_message",
      automatic: true,
    };
  }
  if (!reliableTurnIdentity) {
    return {
      action: "skill_guidance",
      reason: "turn_identity_unavailable",
      automatic: true,
    };
  }
  return { action: "search", reason, automatic: true };
}

export async function runRecallInjection(
  sourceId: string,
  input: string,
  environment: NodeJS.ProcessEnv,
  options: InjectionOptions = {},
): Promise<InjectionResult> {
  const startedAt = options.startedAt ?? Date.now();
  const processDeadline = startedAt + (options.budgetMs ?? PROCESS_BUDGET_MS);
  const coreDeadline = Math.min(processDeadline, startedAt + CORE_BUDGET_MS);
  const deadline = signalUntil(coreDeadline);
  const notice = boundaryNotice(
    environment.ONE_MEMORY_BROKER_DISCOVERY,
    environment.ONE_MEMORY_HOST_CREDENTIAL_FILE,
    environment.ONE_MEMORY_BOUNDARY_COMMAND,
    input,
    environment,
    Math.min(processDeadline, startedAt + NOTICE_BUDGET_MS),
  );
  const capture = captureTurn(
    environment.ONE_MEMORY_CAPTURE_COMMAND,
    sourceId,
    input,
    environment,
    Math.min(processDeadline, Date.now() + CAPTURE_HANDOFF_MS),
  );
  const counts = { recalled: 0, hits: 0, evidence: 0 };
  let event = "unknown";
  let hostSessionId: string | null = null;
  let outcome: InjectionOutcome = "skipped";
  let memory: string | null = null;
  let requestId: string | null = null;
  let trigger: AutoRecallDecision | null = null;
  try {
    const payload = parsePayload(input);
    event = eventName(payload) ?? "unknown";
    hostSessionId = sessionId(payload);
    let query = SOURCES.has(sourceId) ? await queryFor(payload) : null;
    if (query !== null) {
      trigger = autoRecallDecision(
        query,
        turnId(payload, environment) !== null,
      );
      if (trigger.action !== "search") {
        query = null;
        outcome =
          trigger.action === "skill_guidance" ? "skill_guidance" : "skipped";
      }
    }
    const discoveryPath = environment.ONE_MEMORY_BROKER_DISCOVERY;
    if (query !== null && !discoveryPath) outcome = "unavailable";
    else if (query !== null && discoveryPath) {
      const credentialFile = environment.ONE_MEMORY_HOST_CREDENTIAL_FILE;
      const client: BrokerClientOptions = {
        signal: deadline,
        ...(credentialFile
          ? { credentialProvider: () => readHostCredentialFile(credentialFile) }
          : {}),
      };
      const remaining = coreDeadline - Date.now() - WRITEBACK_RESERVE_MS;
      if (remaining <= 0) throw new Error("budget_exhausted");
      const searchTimeout = Math.max(
        MIN_CORE_SEARCH_TIMEOUT_MS,
        Math.min(CORE_SEARCH_TIMEOUT_MS, remaining),
      );
      const context = await contextThroughBroker(
        discoveryPath,
        {
          query,
          kinds: ["memory", "raw"],
          limit: MAX_INJECTED_MEMORIES * 2,
          character_budget: BLOCK_CHARACTERS * 3,
          timeout_ms: searchTimeout,
        },
        client,
      );
      requestId = context.schema_version === 1 ? context.request_id : null;
      const memories = validMemories(context);
      counts.recalled = memories.length;
      const rendered = render(context, memories);
      const requestedPaths =
        context.schema_version === 1
          ? [context.paths.raw, context.paths.memory].filter(
              (path) => path.status !== "not_requested",
            )
          : [];
      const unavailable = context.state === "unavailable";
      const timedOut = requestedPaths.some(
        (path) => path.reason === "timeout_budget",
      );
      if (rendered.block === null) {
        outcome = timedOut ? "timeout" : unavailable ? "unavailable" : "empty";
      } else {
        counts.hits = rendered.hits;
        counts.evidence = rendered.evidence;
        memory = rendered.block;
        outcome = "injected";
      }
    }
  } catch (error) {
    memory = null;
    counts.hits = 0;
    counts.evidence = 0;
    outcome = deadline.aborted
      ? "timeout"
      : error instanceof BrokerClientError && DENIED.has(error.code)
        ? "denied"
        : "unavailable";
  }
  const reported = await notice;
  const captured = await capture;
  const serviceNotice =
    outcome === "unavailable" ? SERVICE_UNAVAILABLE_NOTICE : null;
  const sections = [memory, serviceNotice, reported.block].filter(
    (section): section is string => section !== null,
  );
  const stdout =
    sections.length === 0
      ? ""
      : `${JSON.stringify({
          suppressOutput: true,
          hookSpecificOutput: {
            hookEventName: event,
            additionalContext: sections.join("\n\n"),
          },
        })}\n`;
  const diagnostic = {
    at: new Date().toISOString(),
    source: sourceId,
    event,
    outcome,
    request_id: requestId,
    recalled: counts.recalled,
    hits: counts.hits,
    evidence: counts.evidence,
    notice: reported.outcome,
    capture: captured,
    elapsed_ms: Math.round(Date.now() - startedAt),
    timed_out: outcome === "timeout",
    denied: outcome === "denied",
    trigger_action: trigger?.action ?? null,
    trigger_reason: trigger?.reason ?? null,
    trigger_automatic: trigger?.automatic ?? null,
  };
  await writeDiagnosticBestEffort(
    options.diagnosticWriter ?? writeDiagnostic,
    environment.ONE_MEMORY_DATA_DIR,
    diagnostic,
    processDeadline,
  );
  return {
    stdout,
    outcome,
    notice: reported.outcome,
    requestId,
    hostSessionId,
    ack: reported.block === null ? null : reported.ack,
    trigger,
  };
}

async function writeDiagnosticBestEffort(
  writer: typeof writeDiagnostic,
  dataDir: string | undefined,
  record: Record<string, unknown>,
  processDeadline: number,
): Promise<void> {
  let writing: Promise<void>;
  try {
    writing = writer(dataDir, record).catch(() => {});
  } catch {
    return;
  }
  const waitMs = Math.min(
    DIAGNOSTIC_BUDGET_MS,
    Math.max(0, processDeadline - Date.now()),
  );
  if (waitMs === 0) return;
  let timer: NodeJS.Timeout | undefined;
  await Promise.race([
    writing,
    new Promise<void>((resolve) => {
      timer = setTimeout(resolve, waitMs);
    }),
  ]);
  clearTimeout(timer);
}

function signalUntil(until: number): AbortSignal {
  const remaining = Math.floor(until - Date.now());
  return remaining <= 0 ? AbortSignal.abort() : AbortSignal.timeout(remaining);
}

/** Run `<core> boundary --peek` on the same hook input and keep only its notice.
 *
 * Its reply is read as the host would read it, `hookSpecificOutput`
 * .`additionalContext`, so nothing new is agreed between the two runtimes.
 * Anything else it prints — a traceback, `{"continue": true}` — is dropped.
 * Past the deadline the whole process group is killed, so a hung child cannot
 * hold the hook's stdout open. Nothing is confirmed in any of these cases.
 */
function boundaryNotice(
  discoveryPath: string | undefined,
  credentialFile: string | undefined,
  command: string | undefined,
  input: string,
  environment: NodeJS.ProcessEnv,
  until: number,
): Promise<{
  block: string | null;
  outcome: NoticeOutcome;
  ack: string | null;
}> {
  if (environment.ONE_MEMORY_BOUNDARY_VIA_BROKER === "1") {
    if (!discoveryPath)
      return Promise.resolve({ block: null, outcome: "absent", ack: null });
    const remaining = until - Date.now();
    if (remaining <= 0)
      return Promise.resolve({ block: null, outcome: "timeout", ack: null });
    return boundaryPeekThroughBroker(
      discoveryPath,
      parsePayload(input),
      remaining,
      {
        signal: signalUntil(until),
        ...(credentialFile
          ? { credentialProvider: () => readHostCredentialFile(credentialFile) }
          : {}),
      },
    ).then(
      (value) => {
        const reply = noticeReply(JSON.stringify(value));
        if (reply === null)
          return { block: null, outcome: "failed" as const, ack: null };
        if (!reply.context)
          return { block: null, outcome: "none" as const, ack: null };
        return {
          block: noticeBlock(reply.context),
          outcome: "delivered" as const,
          ack: reply.ack,
        };
      },
      (error) => ({
        block: null,
        outcome:
          signalUntil(until).aborted ||
          (error instanceof BrokerClientError &&
            error.code === "context_timeout")
            ? ("timeout" as const)
            : ("failed" as const),
        ack: null,
      }),
    );
  }
  if (!command)
    return Promise.resolve({ block: null, outcome: "absent", ack: null });
  if (until <= Date.now())
    return Promise.resolve({ block: null, outcome: "timeout", ack: null });
  return new Promise((resolve) => {
    let settled = false;
    let timer: NodeJS.Timeout | undefined;
    const finish = (
      block: string | null,
      outcome: NoticeOutcome,
      ack: string | null = null,
    ) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve({ block, outcome, ack });
    };
    const { ELECTRON_RUN_AS_NODE: _node, ...env } = environment;
    const chunks: Buffer[] = [];
    let size = 0;
    let child: ReturnType<typeof spawn>;
    try {
      child = spawn(command, ["boundary", "--peek"], {
        env,
        stdio: ["pipe", "pipe", "ignore"],
        detached: process.platform !== "win32",
      });
    } catch {
      return finish(null, "failed");
    }
    timer = setTimeout(
      () => {
        killChild(child);
        child.stdout?.destroy();
        finish(null, "timeout");
      },
      Math.max(0, until - Date.now()),
    );
    child.on("error", () => finish(null, "failed"));
    child.stdout?.on("data", (chunk: Buffer) => {
      size += chunk.length;
      if (size <= BOUNDARY_STDOUT_BYTES) chunks.push(chunk);
    });
    child.on("close", (code) => {
      if (code !== 0 || size > BOUNDARY_STDOUT_BYTES)
        return finish(null, "failed");
      const text = Buffer.concat(chunks).toString("utf8").trim();
      if (!text) return finish(null, "none");
      const reply = noticeReply(text);
      if (reply === null) return finish(null, "failed");
      if (!reply.context) return finish(null, "none");
      finish(noticeBlock(reply.context), "delivered", reply.ack);
    });
    child.stdin?.on("error", () => {});
    child.stdin?.end(input);
  });
}

/** Hand the prompt to `<core> capture <source>` and let it run on its own.
 *
 * The hook does not wait for capture; only the prompt's bytes must be in its
 * pipe before this process may exit. Once it has, nothing is left to stop a
 * capture that gets stuck, so it carries its own deadline and ends itself.
 * A prompt lost here is not recorded by Stop, whose payload carries only the
 * reply; it comes in with the transcript file, if live sync is on.
 */
function captureTurn(
  command: string | undefined,
  sourceId: string,
  input: string,
  environment: NodeJS.ProcessEnv,
  until: number,
): Promise<CaptureOutcome> {
  if (!command) return Promise.resolve("absent");
  if (!SOURCES.has(sourceId) || promptEvent(input) !== "UserPromptSubmit")
    return Promise.resolve("skipped");
  if (until <= Date.now()) return Promise.resolve("timeout");
  const { ELECTRON_RUN_AS_NODE: _node, ...rest } = environment;
  const env = {
    ...rest,
    ONE_MEMORY_CAPTURE_DEADLINE_S: `${CAPTURE_DEADLINE_S}`,
  };
  return new Promise((resolve) => {
    let settled = false;
    let timer: NodeJS.Timeout | undefined;
    const settle = (outcome: CaptureOutcome) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(outcome);
    };
    let child: ReturnType<typeof spawn>;
    try {
      child = spawn(command, ["capture", sourceId], {
        env,
        stdio: ["pipe", "ignore", "ignore"],
        detached: process.platform !== "win32",
      });
    } catch {
      return settle("failed");
    }
    child.on("error", () => settle("failed"));
    child.unref();
    const stdin = child.stdin;
    if (!stdin) return settle("failed");
    timer = setTimeout(
      () => {
        stdin.destroy();
        killChild(child);
        settle("timeout");
      },
      Math.max(0, until - Date.now()),
    );
    stdin.on("error", () => settle("failed"));
    stdin.end(input, () => settle("handed"));
  });
}

function killChild(child: ReturnType<typeof spawn>): void {
  try {
    if (child.pid !== undefined && process.platform !== "win32")
      process.kill(-child.pid, "SIGKILL");
    else child.kill("SIGKILL");
  } catch {
    // Already gone.
  }
}

function promptEvent(input: string): string | null {
  try {
    return eventName(parsePayload(input));
  } catch {
    return null;
  }
}

/** `context: ""` for a well-formed reply with nothing to say, `null` for anything else. */
function noticeReply(
  stdout: string,
): { context: string; ack: string | null } | null {
  let reply: unknown;
  try {
    reply = JSON.parse(stdout);
  } catch {
    return null;
  }
  if (!isRecord(reply)) return null;
  const ack =
    typeof reply.one_memory_ack === "string" &&
    ACK_TOKEN.test(reply.one_memory_ack)
      ? reply.one_memory_ack
      : null;
  const output = reply.hookSpecificOutput;
  if (output === undefined) return { context: "", ack: null };
  if (!isRecord(output)) return null;
  const context = output.additionalContext;
  if (context === undefined) return { context: "", ack: null };
  return typeof context === "string" ? { context: context.trim(), ack } : null;
}

/** Tell Core the notice was handed over. Not waited for; a failure only
 * means the same notice comes again next round. */
function confirmNotice(
  discoveryPath: string | undefined,
  credentialFile: string | undefined,
  command: string | undefined,
  ack: string,
  environment: NodeJS.ProcessEnv,
): void {
  if (environment.ONE_MEMORY_BOUNDARY_VIA_BROKER === "1") {
    if (!discoveryPath) return;
    void boundaryAckThroughBroker(
      discoveryPath,
      ack,
      DELIVERY_REPORT_BUDGET_MS,
      {
        ...(credentialFile
          ? { credentialProvider: () => readHostCredentialFile(credentialFile) }
          : {}),
      },
    ).catch(() => undefined);
    return;
  }
  if (!command) return;
  const { ELECTRON_RUN_AS_NODE: _node, ...env } = environment;
  try {
    const child = spawn(command, ["boundary", "--ack", ack], {
      env,
      stdio: "ignore",
      detached: process.platform !== "win32",
    });
    child.on("error", () => {});
    child.unref();
  } catch {
    // Sent again next round.
  }
}

function noticeBlock(text: string): string {
  const opening = `<${NOTICE_TAG}>\n`;
  const close = `\n</${NOTICE_TAG}>`;
  const body = clip(
    neutralizeReservedTags(text),
    NOTICE_CHARACTERS - length(opening) - length(close),
  );
  return `${opening}${body}${close}`;
}

/** The hook process: stdin in, stdout out, always exit 0. */
export async function runRecallInjectionProcess(
  sourceId: string | undefined,
): Promise<void> {
  const startedAt = Date.now() - process.uptime() * 1000;
  const processDeadline = startedAt + PROCESS_BUDGET_MS;
  const forceExit = setTimeout(
    () => {
      // A forced exit means the budget fired before the normal path could
      // write its diagnostic. Log synchronously so the next investigation
      // can tell "the timer ran but the process was stuck before it" from
      // "the timer never got scheduled" (the 19:25 outage had no log line).
      try {
        const dataDir = process.env.ONE_MEMORY_DATA_DIR;
        if (dataDir && path.isAbsolute(dataDir)) {
          const directory = path.join(dataDir, "logs");
          mkdirSync(directory, { recursive: true });
          appendFileSync(
            path.join(directory, "recall-inject.jsonl"),
            `${JSON.stringify({ outcome: "forced_exit", uptime_s: Math.round(process.uptime() * 1000) / 1000 })}\n`,
            { flag: "a" },
          );
        }
      } catch {
        // Never let the fallback write block or throw.
      }
      process.exit(0);
    },
    Math.max(0, processDeadline - Date.now()),
  );
  let input = "";
  try {
    input = await readStdin(processDeadline);
  } catch {
    input = "";
  }
  const result = await runRecallInjection(sourceId ?? "", input, process.env, {
    startedAt,
  }).catch(() => ({
    stdout: "",
    ack: null,
    requestId: null,
    hostSessionId: null,
  }));
  process.exitCode = 0;
  if (!result.stdout) process.exit(0);
  // A closed pipe is the host's business; this hook still exits 0.
  process.stdout.on("error", () => {});
  const command = process.env.ONE_MEMORY_BOUNDARY_COMMAND;
  const ack = result.ack;
  process.stdout.write(result.stdout, async (error) => {
    if (!error && ack)
      confirmNotice(
        process.env.ONE_MEMORY_BROKER_DISCOVERY,
        process.env.ONE_MEMORY_HOST_CREDENTIAL_FILE,
        command,
        ack,
        process.env,
      );
    if (!error) {
      await reportDelivered(result, process.env, startedAt, processDeadline);
    }
    clearTimeout(forceExit);
    process.exit(0);
  });
}

async function reportDelivered(
  result: Pick<InjectionResult, "requestId" | "hostSessionId">,
  environment: NodeJS.ProcessEnv,
  startedAt: number,
  processDeadline: number,
): Promise<void> {
  const discoveryPath = environment.ONE_MEMORY_BROKER_DISCOVERY;
  const credentialFile = environment.ONE_MEMORY_HOST_CREDENTIAL_FILE;
  if (!discoveryPath || !result.requestId || !result.hostSessionId) return;
  const remaining = processDeadline - Date.now();
  if (remaining <= 0) return;
  const signal = AbortSignal.timeout(
    Math.min(DELIVERY_REPORT_BUDGET_MS, remaining),
  );
  try {
    await reportCallEventDeliveryThroughBroker(
      discoveryPath,
      {
        request_id: result.requestId,
        delivery: "delivered",
        host_session_id: result.hostSessionId,
        injection_elapsed_ms: Math.max(0, Math.round(Date.now() - startedAt)),
      },
      {
        signal,
        ...(credentialFile
          ? { credentialProvider: () => readHostCredentialFile(credentialFile) }
          : {}),
      },
    );
  } catch (error) {
    // Delivery accounting must never turn a successful host injection into a
    // failure. An explicit debug switch keeps that fail-open behavior while
    // exposing only the broker error code for isolated diagnosis.
    if (environment.ONE_MEMORY_DEBUG_DELIVERY === "1") {
      const code =
        error instanceof BrokerClientError ? error.code : "delivery_error";
      process.stderr.write(`one-memory delivery failed: ${code}\n`);
    }
  }
}

async function readStdin(until: number): Promise<string> {
  const chunks: Buffer[] = [];
  let size = 0;
  const timer = setTimeout(
    () => process.stdin.destroy(),
    Math.max(0, until - Date.now()),
  );
  try {
    for await (const chunk of process.stdin) {
      const buffer = Buffer.from(chunk);
      size += buffer.length;
      if (size > 4 * 1024 * 1024) break;
      chunks.push(buffer);
    }
  } finally {
    clearTimeout(timer);
  }
  return Buffer.concat(chunks).toString("utf8");
}

function parsePayload(input: string): Record<string, unknown> {
  const value: unknown = JSON.parse(input);
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return value as Record<string, unknown>;
}

function eventName(payload: Record<string, unknown>): string | null {
  const event = payload.hook_event_name ?? payload.hookEventName;
  return typeof event === "string" ? event : null;
}

function sessionId(payload: Record<string, unknown>): string | null {
  const value = payload.session_id ?? payload.sessionId;
  return typeof value === "string" && HOST_SESSION_ID.test(value)
    ? value
    : null;
}

function turnId(
  payload: Record<string, unknown>,
  environment: NodeJS.ProcessEnv = {},
): string | null {
  const value =
    payload.turn_id ??
    payload.turnId ??
    payload.message_id ??
    payload.messageId ??
    environment.CODEBUDDY_CONVERSATION_REQUEST_ID;
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

async function queryFor(
  payload: Record<string, unknown>,
): Promise<string | null> {
  const event = eventName(payload);
  if (event === "UserPromptSubmit") {
    return typeof payload.prompt === "string"
      ? cleanQuery(payload.prompt)
      : null;
  }
  // A new, resumed or cleared session has no words yet to recall with. After
  // a compaction the recalled block went with the summarised turn, so the
  // last thing the user asked is recalled again.
  if (event === "SessionStart" && payload.source === "compact") {
    const transcript = payload.transcript_path;
    if (typeof transcript !== "string" || !path.isAbsolute(transcript))
      return null;
    return lastUserMessage(transcript);
  }
  return null;
}

function cleanQuery(text: string): string | null {
  const cleaned = stripInjectedBlocks(text).replace(REMINDER_BLOCK, "").trim();
  if (!cleaned) return null;
  let query = "";
  for (const character of cleaned) {
    if (query.length + character.length > QUERY_LENGTH) break;
    query += character;
  }
  return query;
}

async function lastUserMessage(transcript: string): Promise<string | null> {
  let tail: string;
  try {
    const handle = await open(transcript, "r");
    try {
      const info = await handle.stat();
      if (!info.isFile()) return null;
      const length = Math.min(info.size, TRANSCRIPT_TAIL_BYTES);
      const buffer = Buffer.alloc(length);
      await handle.read(buffer, 0, length, info.size - length);
      tail = buffer.toString("utf8");
    } finally {
      await handle.close();
    }
  } catch {
    return null;
  }
  const lines = tail.split("\n").reverse();
  for (const line of lines) {
    let record: unknown;
    try {
      record = JSON.parse(line);
    } catch {
      continue;
    }
    const text = userText(record);
    if (text === null) continue;
    const query = cleanQuery(text);
    if (query !== null) return query;
  }
  return null;
}

function userText(record: unknown): string | null {
  if (!isRecord(record)) return null;
  const nested = isRecord(record.message) ? record.message : null;
  const role = record.role ?? nested?.role;
  if (role !== "user" && role !== "human") return null;
  const provider = isRecord(record.providerData) ? record.providerData : null;
  if (
    provider?.isMeta === true ||
    provider?.isCompactInternal === true ||
    provider?.isCompacted === true
  )
    return null;
  return textContent(record.content) ?? textContent(nested?.content);
}

function textContent(content: unknown): string | null {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return null;
  const texts = content.flatMap((block) =>
    isRecord(block) &&
    (block.type === "text" || block.type === "input_text") &&
    typeof block.text === "string"
      ? [block.text]
      : [],
  );
  return texts.length > 0 ? texts.join("\n") : null;
}

function validMemories(
  response: HostContextSearchResponse,
): (MemoryContextItem | SdkMemoryContextItem)[] {
  return response.items
    .filter(
      (item): item is MemoryContextItem | SdkMemoryContextItem =>
        item.kind === "memory" && item.content.trim() !== "",
    )
    .slice(0, MAX_INJECTED_MEMORIES);
}

function quotesById(
  response: ContextSearchResponse | null,
): Map<string, Quote> {
  const quotes = new Map<string, Quote>();
  for (const item of response?.items ?? []) {
    if (item.kind !== "raw") continue;
    for (const message of item.messages) {
      if (!isSpeakableMessage(message)) continue;
      quotes.set(message.evidence_id, {
        evidenceId: message.evidence_id,
        source: item.source_id,
        day: message.created_at?.slice(0, 10) ?? null,
        text: message.text,
      });
    }
  }
  return quotes;
}

/** Only words a participant wrote; tool output and denials are not history.
 *
 * Tool results reach the host as noise: permission-denial text and grep hits
 * from unrelated fixtures read as contradictory facts and are quoted back at
 * the user, which is worse than quoting nothing. Retrieval coverage still
 * reports that the raw lane was searched; only the injected excerpt drops it.
 */
function isSpeakableMessage(message: {
  role?: string;
  written_by?: string;
}): boolean {
  const role = message.role ?? "";
  const writtenBy = message.written_by ?? "";
  if (role === "tool" || writtenBy === "tool") return false;
  return role === "user" || role === "assistant" || writtenBy !== "";
}

function render(
  response: HostContextSearchResponse,
  memories: (MemoryContextItem | SdkMemoryContextItem)[],
): { block: string | null; hits: number; evidence: number } {
  if (response.schema_version === 2) return renderSdk(response);
  return renderLegacy(
    response,
    memories.filter(
      (memory): memory is MemoryContextItem => "revision" in memory,
    ),
  );
}

function renderLegacy(
  response: ContextSearchResponse,
  memories: MemoryContextItem[],
): { block: string | null; hits: number; evidence: number } {
  const coverage = coverageLine(response);
  const opening = `<${TAG}>\n${HEADER}${coverage ? `\n${coverage}` : ""}`;
  const close = `</${TAG}>`;
  const items: string[] = [];
  const usedEvidence = new Set<string>();
  let used = length(opening) + 1 + length(close);
  let evidence = 0;
  const quotes = quotesById(response);
  for (const hit of memories) {
    const label = `[${items.length + 1}] `;
    const quote = (hit.evidence_ids ?? [])
      .map((id) => quotes.get(id))
      .find((found) => found !== undefined);
    const quoteLine =
      quote === undefined
        ? ""
        : `\n    原话（${inline(quote.source)}${quote.day ? ` · ${quote.day}` : ""}）：${clip(inline(quote.text), QUOTE_CHARACTERS)}`;
    const content = clip(
      inline(hit.content),
      ITEM_CHARACTERS - length(label) - length(quoteLine),
    );
    const item = `${label}${content}${quoteLine}`;
    if (used + 1 + length(item) > BLOCK_CHARACTERS) break;
    items.push(item);
    used += 1 + length(item);
    if (quote !== undefined) {
      evidence += 1;
      usedEvidence.add(quote.evidenceId);
    }
  }
  for (const item of response.items) {
    if (item.kind !== "raw") continue;
    for (const message of item.messages) {
      if (
        items.length >= MAX_INJECTED_MEMORIES ||
        usedEvidence.has(message.evidence_id)
      )
        continue;
      if (!isSpeakableMessage(message)) continue;
      const label = `[${items.length + 1}] `;
      const source = `${inline(item.source_id)}${message.created_at ? ` · ${message.created_at.slice(0, 10)}` : ""}`;
      const raw = clip(
        `原始对话（${source}）：${inline(message.text)}`,
        ITEM_CHARACTERS - length(label),
      );
      const rendered = `${label}${raw}`;
      if (used + 1 + length(rendered) > BLOCK_CHARACTERS) break;
      items.push(rendered);
      used += 1 + length(rendered);
      evidence += 1;
      usedEvidence.add(message.evidence_id);
    }
  }
  return {
    // A coverage line on its own is not context: with nothing to read the host
    // must get empty stdout rather than our markup around an apology.
    block: items.length > 0 ? [opening, ...items, close].join("\n") : null,
    hits: items.length,
    evidence,
  };
}

function renderSdk(response: SdkContextSearchResponse): {
  block: string | null;
  hits: number;
  evidence: number;
} {
  const coverage =
    response.state === "partial" || response.truncated
      ? "覆盖不完整：result=partial。不得把未查完当作无命中。"
      : null;
  const opening = `<${TAG}>\n${HEADER}${coverage ? `\n${coverage}` : ""}`;
  const close = `</${TAG}>`;
  const items: string[] = [];
  let used = length(opening) + 1 + length(close);
  for (const item of response.items) {
    if (items.length >= MAX_INJECTED_MEMORIES) break;
    const label = `[${items.length + 1}] `;
    const source =
      item.kind === "raw"
        ? `${inline(item.source)}${item.memory_at ? ` · ${item.memory_at.slice(0, 10)}` : ""}`
        : item.source
          ? inline(item.source)
          : null;
    const prefix =
      item.kind === "raw"
        ? `原始对话（${source}）：`
        : `记忆${source ? `（${source}）` : ""}：`;
    const rendered = `${label}${clip(
      `${prefix}${inline(item.content)}`,
      ITEM_CHARACTERS - length(label),
    )}`;
    if (used + 1 + length(rendered) > BLOCK_CHARACTERS) break;
    items.push(rendered);
    used += 1 + length(rendered);
  }
  return {
    block: items.length > 0 ? [opening, ...items, close].join("\n") : null,
    hits: items.length,
    evidence: 0,
  };
}

function coverageLine(response: ContextSearchResponse): string | null {
  const lanes = (["raw", "memory"] as const).flatMap((kind) => {
    const lane = response.paths[kind];
    return lane.status === "degraded" ||
      lane.status === "unavailable" ||
      lane.truncated
      ? [`${kind}=${lane.status}${lane.reason ? `/${lane.reason}` : ""}`]
      : [];
  });
  if (response.state !== "partial" && !response.truncated && lanes.length === 0)
    return null;
  return `覆盖不完整：${lanes.join(", ") || "result=partial"}。不得把未查完当作无命中。`;
}

/** One line, and no markup that could end the block or open a host reminder. */
function inline(text: string): string {
  return neutralizeReservedTags(text.replace(/\s+/g, " ").trim());
}

/** Spaces as both runtimes agree on them, after NFKC has turned the wide and
 * ideographic ones into " ". JavaScript's and Python's `\s` differ. */
const SPACE = "[\\t\\n\\v\\f\\r ]";

interface TagSpan {
  start: number;
  /** Where the tag's last character begins. */
  last: number;
  end: number;
  name: string;
  closing: boolean;
}

/** A reserved tag in any spelling that reads as one: each character taken
 * as NFKC and lower case, spaces allowed around the slash and the name,
 * attributes allowed after it. Anything else with angle brackets is left. */
function tagSpans(text: string, names: readonly string[]): TagSpan[] {
  // Whole-text NFKC can only compose a name's letter with a mark that would
  // already keep it from being a tag, so a miss here is a miss below.
  const whole = text.normalize("NFKC").toLowerCase();
  if (!names.some((name) => whole.includes(name))) return [];
  let view = "";
  const starts: number[] = [];
  const ends: number[] = [];
  let at = 0;
  for (const character of text) {
    const normal = character.normalize("NFKC").toLowerCase();
    for (let unit = 0; unit < normal.length; unit += 1) {
      starts.push(at);
      ends.push(at + character.length);
    }
    view += normal;
    at += character.length;
  }
  const pattern = new RegExp(
    `<${SPACE}*(/)?${SPACE}*(${names.join("|")})(?:${SPACE}[^<>]*)?>`,
    "g",
  );
  return [...view.matchAll(pattern)].map((match) => {
    const final = match.index + match[0].length - 1;
    return {
      start: starts[match.index],
      last: starts[final],
      end: ends[final],
      name: match[2],
      closing: match[1] !== undefined,
    };
  });
}

export function reservedTags(text: string): Array<[number, number]> {
  return tagSpans(text, RESERVED_TAGS).map((span) => [span.start, span.end]);
}

/** Disarms a reserved tag by its two brackets; the rest of the text stays. */
export function neutralizeReservedTags(text: string): string {
  let result = "";
  let at = 0;
  for (const span of tagSpans(text, RESERVED_TAGS)) {
    result += `${text.slice(at, span.start)}‹${text.slice(
      span.start + codePointLength(text, span.start),
      span.last,
    )}›`;
    at = span.end;
  }
  return result + text.slice(at);
}

/** Our own blocks, in any spelling `tagSpans` reads, taken out of a prompt. */
export function stripInjectedBlocks(text: string): string {
  const spans = tagSpans(text, INJECTED_TAGS);
  // Where the next closing tag of the same name is, found in one pass back.
  const nextClose: number[] = new Array(spans.length).fill(-1);
  const ahead = new Map<string, number>();
  for (let index = spans.length - 1; index >= 0; index -= 1) {
    nextClose[index] = ahead.get(spans[index].name) ?? -1;
    if (spans[index].closing) ahead.set(spans[index].name, index);
  }
  let result = "";
  let at = 0;
  for (let index = 0; index < spans.length; index += 1) {
    const open = spans[index];
    if (open.closing || open.start < at) continue;
    const close = nextClose[index];
    if (close < 0) continue;
    result += text.slice(at, open.start);
    at = spans[close].end;
    index = close;
  }
  return result + text.slice(at);
}

function codePointLength(text: string, index: number): number {
  const point = text.codePointAt(index);
  return point !== undefined && point > 0xffff ? 2 : 1;
}

function clip(text: string, limit: number): string {
  const characters = [...text];
  if (characters.length <= limit) return text;
  return `${characters.slice(0, Math.max(0, limit - 1)).join("")}…`;
}

function length(text: string): number {
  return [...text].length;
}

async function writeDiagnostic(
  dataDir: string | undefined,
  record: Record<string, unknown>,
): Promise<void> {
  if (!dataDir || !path.isAbsolute(dataDir)) return;
  try {
    const root = statSync(dataDir);
    if (!root.isDirectory()) return;
    const directory = path.join(dataDir, "logs");
    const target = path.join(directory, "recall-inject.jsonl");
    await mkdir(directory, { recursive: true });
    const size = await stat(target).then(
      (info) => info.size,
      () => 0,
    );
    if (size > LOG_ROTATE_BYTES) await rename(target, `${target}.1`);
    await appendFile(target, `${JSON.stringify(record)}\n`, { mode: 0o600 });
    // `mode` only applies to a file this call creates.
    await chmod(target, 0o600);
  } catch {
    // A diagnostic that cannot be written must not become the host's error.
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
