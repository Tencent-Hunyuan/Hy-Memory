import { spawn } from "node:child_process";
import { readdir, readFile, realpath } from "node:fs/promises";
import path from "node:path";
import { parseDisabledOverride } from "./service-install.js";
import { runtimeServiceEnabled } from "./service-ownership.js";
import { RUNTIME_SERVICE_ID } from "./service-plan.js";

/**
 * Whether the background service would keep a Runtime for this data dir alive
 * without the App.
 *
 * `enabled`: a LaunchAgent (or systemd user unit) declaring this data dir is
 * installed, the OS has no disable override for it, and on macOS the enable
 * marker its `KeepAlive` waits on is present. The marker is the App's "Launch
 * at login" switch and `memhub service disable`: without it launchd neither
 * starts nor restarts the job, so the service is off even while the plist is
 * still installed.
 *
 * `disabled`: none of that holds. The service was never installed or was
 * uninstalled, `launchctl disable` / `systemctl disable` turned it off (the
 * case in which the App falls back to a transient Runtime), or the enable
 * marker is gone.
 *
 * `unknown`: a query failed or timed out. Callers must not act on it.
 */
export type RuntimeServiceStateName = "enabled" | "disabled" | "unknown";

export type RuntimeServiceStateReason =
  | "service-enabled"
  | "service-not-installed"
  | "service-enable-marker-absent"
  | "service-disabled-by-launchctl"
  | "service-disabled-by-systemd"
  | "service-unsupported-platform"
  | "service-state-query-failed"
  | "service-state-timeout";

export interface RuntimeServiceState {
  state: RuntimeServiceStateName;
  reason: RuntimeServiceStateReason;
  /** The pid the service manager reports for the running job: null when the
   * job is not loaded, not running, or the state is unknown. */
  jobPid: number | null;
}

export type ServiceStateProbe = (
  command: readonly string[],
  timeoutMs: number,
) => Promise<{ code: number | null; stdout: string }>;

export interface RuntimeServiceStateOptions {
  dataDir: string;
  home: string;
  platform?: NodeJS.Platform;
  uid?: number;
  xdgConfigHome?: string;
  /** Budget for the whole query; past it the state is `unknown`. */
  timeoutMs?: number;
  probe?: ServiceStateProbe;
  markerEnabled?: (dataDir: string) => Promise<boolean>;
}

const LAUNCH_AGENT_FILE = new RegExp(
  `^${RUNTIME_SERVICE_ID.replaceAll(".", "\\.")}(?:\\.[0-9a-f]{16})?\\.plist$`,
);

/** Ask the service manager, now, whether the background service is on.
 *
 * Never throws and never caches: every answer is a fresh `launchctl` /
 * `systemctl` read, and any failure or timeout is `unknown`.
 */
export async function queryRuntimeServiceState(
  options: RuntimeServiceStateOptions,
): Promise<RuntimeServiceState> {
  const timeoutMs = options.timeoutMs ?? 3_000;
  let timer: NodeJS.Timeout | undefined;
  const timeout = new Promise<RuntimeServiceState>((resolve) => {
    timer = setTimeout(
      () => resolve(state("unknown", "service-state-timeout")),
      timeoutMs,
    );
  });
  try {
    return await Promise.race([
      inspect(options, timeoutMs).catch(() =>
        state("unknown", "service-state-query-failed"),
      ),
      timeout,
    ]);
  } finally {
    clearTimeout(timer);
  }
}

async function inspect(
  options: RuntimeServiceStateOptions,
  timeoutMs: number,
): Promise<RuntimeServiceState> {
  const platform = options.platform ?? process.platform;
  const probe = options.probe ?? runProbe;
  if (platform === "darwin")
    return inspectLaunchAgent(options, probe, timeoutMs);
  if (platform === "linux")
    return inspectSystemdUnit(options, probe, timeoutMs);
  return state("disabled", "service-unsupported-platform");
}

async function inspectLaunchAgent(
  options: RuntimeServiceStateOptions,
  probe: ServiceStateProbe,
  timeoutMs: number,
): Promise<RuntimeServiceState> {
  const uid = options.uid;
  if (uid === undefined || !Number.isSafeInteger(uid) || uid < 1) {
    return state("unknown", "service-state-query-failed");
  }
  const labels = await launchAgentsServing(options.home, options.dataDir);
  if (labels.length === 0) return state("disabled", "service-not-installed");
  const domain = `gui/${uid}`;
  const overrides = await probe(
    ["launchctl", "print-disabled", domain],
    timeoutMs,
  );
  if (overrides.code !== 0) {
    return state("unknown", "service-state-query-failed");
  }
  const marker = await (options.markerEnabled ?? runtimeServiceEnabled)(
    options.dataDir,
  );
  let first: RuntimeServiceState | null = null;
  for (const label of labels) {
    const job = await probe(
      ["launchctl", "print", `${domain}/${label}`],
      timeoutMs,
    );
    // 113: the label is not loaded. Anything else unexpected is unknown.
    if (job.code !== 0 && job.code !== 113) {
      return state("unknown", "service-state-query-failed");
    }
    const jobPid = job.code === 0 ? parseLaunchctlPid(job.stdout) : null;
    const current = parseDisabledOverride(overrides.stdout, label)
      ? state("disabled", "service-disabled-by-launchctl", jobPid)
      : marker
        ? state("enabled", "service-enabled", jobPid)
        : state("disabled", "service-enable-marker-absent", jobPid);
    if (current.state === "enabled") return current;
    first ??= current;
  }
  return first ?? state("disabled", "service-not-installed");
}

/** Labels of the MemHub LaunchAgents whose declaration serves `dataDir`.
 * The daily install and isolated install roots use different labels, and a
 * LaunchAgent for another data dir is not this data dir's service. */
async function launchAgentsServing(
  home: string,
  dataDir: string,
): Promise<string[]> {
  const directory = path.join(home, "Library", "LaunchAgents");
  let names: string[];
  try {
    names = await readdir(directory);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return [];
    throw error;
  }
  const wanted = await canonical(dataDir);
  const labels: string[] = [];
  for (const name of names
    .filter((item) => LAUNCH_AGENT_FILE.test(item))
    .sort()) {
    const label = name.slice(0, -".plist".length);
    const content = await readFile(path.join(directory, name), "utf8");
    if (!content.includes(`<key>Label</key><string>${label}</string>`))
      continue;
    const declared =
      /<key>ONE_MEMORY_DATA_DIR<\/key><string>([^<]*)<\/string>/.exec(
        content,
      )?.[1];
    if (declared === undefined) continue;
    if ((await canonical(unescapeXml(declared))) === wanted) labels.push(label);
  }
  return labels;
}

async function inspectSystemdUnit(
  options: RuntimeServiceStateOptions,
  probe: ServiceStateProbe,
  timeoutMs: number,
): Promise<RuntimeServiceState> {
  const config = path.resolve(
    options.xdgConfigHome?.trim() || path.join(options.home, ".config"),
  );
  let content: string;
  try {
    content = await readFile(
      path.join(config, "systemd", "user", "one-memory.service"),
      "utf8",
    );
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      return state("disabled", "service-not-installed");
    }
    throw error;
  }
  const declared =
    /^Environment="ONE_MEMORY_DATA_DIR=((?:[^"\\]|\\.)*)"$/m.exec(content)?.[1];
  if (
    declared === undefined ||
    (await canonical(declared.replace(/\\(.)/g, "$1"))) !==
      (await canonical(options.dataDir))
  ) {
    return state("disabled", "service-not-installed");
  }
  const enabled = await probe(
    ["systemctl", "--user", "is-enabled", "one-memory.service"],
    timeoutMs,
  );
  const answer = enabled.stdout.trim().split(/\r?\n/)[0] ?? "";
  if (enabled.code === null || answer === "") {
    return state("unknown", "service-state-query-failed");
  }
  const main = await probe(
    [
      "systemctl",
      "--user",
      "show",
      "one-memory.service",
      "--property=MainPID",
      "--value",
    ],
    timeoutMs,
  );
  const pid = Number(main.stdout.trim());
  const jobPid =
    main.code === 0 && Number.isSafeInteger(pid) && pid > 1 ? pid : null;
  if (answer === "enabled" || answer === "enabled-runtime") {
    return state("enabled", "service-enabled", jobPid);
  }
  return state("disabled", "service-disabled-by-systemd", jobPid);
}

/** The top-level `pid = N` launchctl prints for a running job. Top-level
 * properties are indented by exactly one tab; nested blocks (endpoints,
 * spawn info) carry their own `pid` lines and must not be read as the job's. */
export function parseLaunchctlPid(stdout: string): number | null {
  for (const line of stdout.split(/\r?\n/)) {
    const match = /^\tpid = (\d+)\s*$/.exec(line);
    if (!match) continue;
    const pid = Number(match[1]);
    return Number.isSafeInteger(pid) && pid > 1 ? pid : null;
  }
  return null;
}

function state(
  name: RuntimeServiceStateName,
  reason: RuntimeServiceStateReason,
  jobPid: number | null = null,
): RuntimeServiceState {
  return { state: name, reason, jobPid };
}

async function canonical(target: string): Promise<string> {
  const resolved = path.resolve(target);
  return realpath(resolved).catch(() => resolved);
}

function unescapeXml(value: string): string {
  return value
    .replaceAll("&lt;", "<")
    .replaceAll("&gt;", ">")
    .replaceAll("&quot;", '"')
    .replaceAll("&apos;", "'")
    .replaceAll("&amp;", "&");
}

async function runProbe(
  command: readonly string[],
  timeoutMs: number,
): Promise<{ code: number | null; stdout: string }> {
  const [program, ...args] = command;
  if (!program) throw new Error("runtime_service_inspection_failed");
  return new Promise((resolve, reject) => {
    const child = spawn(program, args, {
      shell: false,
      windowsHide: true,
      stdio: ["ignore", "pipe", "ignore"],
    });
    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      reject(new Error("runtime_service_inspection_timeout"));
    }, timeoutMs);
    const chunks: Buffer[] = [];
    let size = 0;
    child.stdout.on("data", (chunk: Buffer) => {
      size += chunk.length;
      if (size > 256 * 1024) {
        child.kill();
        return;
      }
      chunks.push(chunk);
    });
    child.once("error", () => {
      clearTimeout(timer);
      reject(new Error("runtime_service_inspection_failed"));
    });
    child.once("close", (code) => {
      clearTimeout(timer);
      if (size > 256 * 1024) {
        reject(new Error("runtime_service_inspection_failed"));
        return;
      }
      resolve({ code, stdout: Buffer.concat(chunks).toString("utf8") });
    });
  });
}
