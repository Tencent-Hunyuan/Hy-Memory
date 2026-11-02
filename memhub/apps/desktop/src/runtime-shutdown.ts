import {
  type RuntimeServiceState,
  type StopAllRuntimesResult,
  stopAllRuntimeProcesses,
} from "@one-memory/runtime-control";

export interface RuntimeShutdownOptions {
  dataDir: string;
  /** Packaged builds run the Runtime as their own process; a dev build's Core
   * is this process's child and is stopped by the supervisor. */
  standalone: boolean;
  /** Asks the service manager whether the background service is on. Called
   * at quit time, every time: what startup saw may no longer be true. */
  queryServiceState: () => Promise<RuntimeServiceState>;
  /** Set when startup adopted a Runtime whose build it could not read. This
   * App never stops such a Runtime. A newer Runtime gets no such pass: it
   * follows the service state like any adopted Runtime. */
  preserveReason?: string | null;
  stopRuntimes?: (options: {
    dataDir: string;
  }) => Promise<StopAllRuntimesResult>;
  /** Tear down this process's supervisor state (Core child, broker). */
  stopSupervisor?: () => Promise<void>;
  onDiagnostic?: (event: {
    outcome: "succeeded" | "degraded";
    detail: string;
  }) => Promise<void> | void;
}

export type RuntimeShutdownResult =
  | { runtime: "stopped" }
  | { runtime: "not-running" }
  | { runtime: "preserved"; reason: string }
  | { runtime: "unchanged" }
  | { runtime: "failed"; detail: string };

/** Stop everything a full quit has to stop, and nothing else.
 *
 * Exactly one Runtime serves a data dir. Whether quitting the App ends it is
 * decided here, at quit time, from the service manager's answer:
 *
 * - background service enabled: the Runtime is the user's background memory
 *   service. Quit never stops it, whoever started the process, including a
 *   transient Runtime this App started earlier.
 * - background service disabled: the Runtime lives only while the App does.
 *   Quit stops every Runtime of this data dir: the App's own child, one
 *   `memhub start` launched, a leftover from an earlier session.
 * - state unknown (query failed or timed out): stop nothing, and say so.
 *
 * A failure here is reported, never thrown. By the time this runs the app is
 * already quitting, and throwing would only turn a failure someone could see
 * into one nobody could.
 */
export async function shutdownRuntime(
  options: RuntimeShutdownOptions,
): Promise<RuntimeShutdownResult> {
  const stopSupervisor = options.stopSupervisor ?? (async () => {});
  const supervisorFailure = await settle(stopSupervisor);
  if (!options.standalone) {
    if (supervisorFailure) {
      await options.onDiagnostic?.({
        outcome: "degraded",
        detail: supervisorFailure,
      });
      return { runtime: "failed", detail: supervisorFailure };
    }
    return { runtime: "unchanged" };
  }
  const preserved = async (
    reason: string,
    outcome: "succeeded" | "degraded",
  ): Promise<RuntimeShutdownResult> => {
    await options.onDiagnostic?.({
      outcome: supervisorFailure ? "degraded" : outcome,
      detail: supervisorFailure ?? `runtime-preserved:${reason}`,
    });
    return supervisorFailure
      ? { runtime: "failed", detail: supervisorFailure }
      : { runtime: "preserved", reason };
  };
  if (options.preserveReason) {
    return preserved(options.preserveReason, "succeeded");
  }
  const service = await options.queryServiceState().catch(
    (): RuntimeServiceState => ({
      state: "unknown",
      reason: "service-state-query-failed",
      jobPid: null,
    }),
  );
  if (service.state === "enabled") {
    return preserved("background-service-enabled", "succeeded");
  }
  if (service.state !== "disabled") {
    // Fail safe: a Runtime stopped by mistake takes the user's background
    // memory with it; one left running by mistake is only a process.
    return preserved(`service-state-unknown:${service.reason}`, "degraded");
  }
  try {
    const result = await (options.stopRuntimes ?? stopAllRuntimeProcesses)({
      dataDir: options.dataDir,
    });
    const stopped = result.stopped.length > 0 ? "stopped" : "not-running";
    const unverified =
      result.unverified.length > 0 ? "unverified-runtime-left-running" : null;
    await options.onDiagnostic?.({
      outcome: supervisorFailure || unverified ? "degraded" : "succeeded",
      detail: supervisorFailure ?? unverified ?? `${stopped}:${service.reason}`,
    });
    if (supervisorFailure) {
      return { runtime: "failed", detail: supervisorFailure };
    }
    if (unverified) return { runtime: "failed", detail: unverified };
    return { runtime: stopped };
  } catch (error) {
    const detail = error instanceof Error ? error.message : "unknown";
    await options.onDiagnostic?.({ outcome: "degraded", detail });
    return { runtime: "failed", detail };
  }
}

/** Run an action and hand back why it failed, instead of throwing. */
async function settle(action: () => Promise<void>): Promise<string | null> {
  try {
    await action();
    return null;
  } catch (error) {
    return error instanceof Error ? error.message : "unknown";
  }
}
