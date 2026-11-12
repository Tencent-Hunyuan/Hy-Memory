import type {
  RuntimeReceiptBuild,
  RuntimeServiceState,
} from "@one-memory/runtime-control";

/** The build this App carries. */
export interface AppBuild {
  buildNumber?: number;
  commit?: string;
}

/** The Runtime found serving the data dir when the App starts. */
export interface RunningRuntime {
  pid: number;
  /** As published in its receipt; `undefined` for a receipt without the
   * fields, which only a Runtime older than this App writes. */
  build: RuntimeReceiptBuild | undefined;
}

export type RuntimeVersionRelation =
  | "same"
  | "app-newer"
  | "runtime-newer"
  | "unknown";

export type RuntimeAdoption =
  /** Nothing is running: install or start this App's Runtime. */
  | { action: "start" }
  /** Same build: use it as it is. */
  | { action: "adopt" }
  /** Its build cannot be compared: use it, never stop it, tell the user. */
  | { action: "adopt-unverified" }
  /** Older, and it is the service manager's job: replace it through the
   * service manager, never with a plain kill launchd would answer with a
   * respawn of whatever it last loaded. */
  | { action: "replace-via-service" }
  /** Older and transient/CLI-started, or a stray beside an enabled service:
   * stop it gracefully, then install or start the right one. */
  | { action: "stop-then-start"; reason: "app-newer" | "stray-runtime" }
  /** Newer than this App: use it as it is, silently. Never stopped or
   * downgraded at startup and never joined by a second Runtime; quit treats
   * it like any adopted Runtime. */
  | { action: "adopt-newer" };

/** Compare by build number, then by commit when both sides know theirs. */
export function compareRuntimeBuild(
  app: AppBuild,
  runtime: RuntimeReceiptBuild | undefined,
): RuntimeVersionRelation {
  if (app.buildNumber === undefined) return "unknown";
  // Every Runtime that publishes its build carries the fields, even as null.
  // A receipt without them predates that contract, and so this App.
  if (runtime === undefined) return "app-newer";
  if (runtime.buildNumber === null) return "unknown";
  if (runtime.buildNumber < app.buildNumber) return "app-newer";
  if (runtime.buildNumber > app.buildNumber) return "runtime-newer";
  const appCommit = app.commit?.trim().toLowerCase();
  if (
    appCommit &&
    runtime.commit &&
    !appCommit.startsWith(runtime.commit) &&
    !runtime.commit.startsWith(appCommit)
  ) {
    // Same build, different commit: the App's bundle takes over, exactly as
    // `installPackagedRuntime({ replaceSameBuild: true })` already does.
    return "app-newer";
  }
  return "same";
}

export function decideRuntimeAdoption(input: {
  running: RunningRuntime | null;
  app: AppBuild;
  service: RuntimeServiceState;
  /** This launch just moved `current` to the App's bundled Runtime, so the
   * process still running was started from the install it replaced. */
  installedChanged?: boolean;
}): RuntimeAdoption {
  const { running, service } = input;
  if (running === null) return { action: "start" };
  let relation = compareRuntimeBuild(input.app, running.build);
  if (relation === "same" && input.installedChanged) relation = "app-newer";
  if (relation === "runtime-newer") return { action: "adopt-newer" };
  if (relation === "unknown") return { action: "adopt-unverified" };
  const serviceJob = service.jobPid !== null && service.jobPid === running.pid;
  if (relation === "app-newer") {
    return serviceJob
      ? { action: "replace-via-service" }
      : { action: "stop-then-start", reason: "app-newer" };
  }
  // One Runtime per data dir: with the service on, the one that stays is the
  // service's own job, not a transient or CLI process holding its lease.
  if (service.state === "enabled" && !serviceJob) {
    return { action: "stop-then-start", reason: "stray-runtime" };
  }
  return { action: "adopt" };
}

export interface RuntimeReconcileDependencies {
  app: AppBuild;
  installedChanged?: boolean;
  findRunning(): Promise<RunningRuntime | null>;
  queryServiceState(): Promise<RuntimeServiceState>;
  /** Graceful stop of the one verified Runtime: in-flight writes finish. */
  stopRuntime(): Promise<unknown>;
  log?(event: {
    decision: RuntimeAdoption;
    service: RuntimeServiceState | null;
    running: RunningRuntime | null;
  }): Promise<void> | void;
}

export interface RuntimeReconcileResult {
  decision: RuntimeAdoption;
  /** The running Runtime stays and the caller must not start another. */
  adopted: boolean;
  /** Quit must leave the adopted Runtime alone whatever the service state:
   * this App could not prove it is its own build. */
  preserveReason: string | null;
}

/** Decide what to do with a Runtime already serving the data dir at App start,
 * and do the part that has to happen before the normal start path runs. */
export async function reconcileRunningRuntime(
  dependencies: RuntimeReconcileDependencies,
): Promise<RuntimeReconcileResult> {
  const running = await dependencies.findRunning();
  const service =
    running === null ? null : await dependencies.queryServiceState();
  const decision = decideRuntimeAdoption({
    running,
    app: dependencies.app,
    service: service ?? {
      state: "unknown",
      reason: "service-state-query-failed",
      jobPid: null,
    },
    installedChanged: dependencies.installedChanged ?? false,
  });
  await dependencies.log?.({ decision, service, running });
  switch (decision.action) {
    case "start":
    case "replace-via-service":
      return { decision, adopted: false, preserveReason: null };
    case "stop-then-start":
      await dependencies.stopRuntime();
      return { decision, adopted: false, preserveReason: null };
    case "adopt":
    case "adopt-newer":
      return { decision, adopted: true, preserveReason: null };
    case "adopt-unverified":
      return {
        decision,
        adopted: true,
        preserveReason: "runtime-build-unknown",
      };
  }
}
