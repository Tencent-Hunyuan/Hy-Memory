import {
  disableRuntimeService,
  enableRuntimeService,
  type RuntimeServiceState,
  readRuntimeServiceOwner,
  runtimeServiceEnabled,
  setAppRuntimeStartAtLogin,
} from "@one-memory/runtime-control";

export interface LoginPreference {
  supported: boolean;
  enabled: boolean;
  status: string;
}

/** "Launch at login" is the background service switch: it writes the enable
 * marker the LaunchAgent's `KeepAlive` waits on. On it means the Runtime
 * outlives the App and quitting leaves it running; off means quitting stops
 * it. `serviceState`, when given, is asked fresh so a `launchctl disable`
 * override (which the switch cannot undo) reads as off, not as on. */
export async function readLoginPreference(
  dataDir: string,
  platform: NodeJS.Platform,
  supported: boolean,
  serviceState?: () => Promise<RuntimeServiceState>,
): Promise<LoginPreference> {
  if (platform !== "darwin" || !supported) {
    return { supported: false, enabled: false, status: "unsupported" };
  }
  try {
    const owner = await readRuntimeServiceOwner(dataDir);
    if (owner?.owner === "cli") {
      return { supported: false, enabled: true, status: "managed-by-cli" };
    }
    if (owner?.owner !== "app") {
      return { supported: false, enabled: false, status: "not-installed" };
    }
    if (
      serviceState &&
      (await serviceState()).reason === "service-disabled-by-launchctl"
    ) {
      return { supported: false, enabled: false, status: "disabled-by-system" };
    }
    return {
      supported: true,
      enabled: owner.start_at_login && (await runtimeServiceEnabled(dataDir)),
      status:
        owner.start_at_login && (await runtimeServiceEnabled(dataDir))
          ? "enabled"
          : "disabled",
    };
  } catch {
    return { supported: true, enabled: false, status: "unavailable" };
  }
}

export async function setLoginPreference(
  dataDir: string,
  platform: NodeJS.Platform,
  input: unknown,
  supported: boolean,
  serviceState?: () => Promise<RuntimeServiceState>,
): Promise<LoginPreference> {
  if (
    !input ||
    typeof input !== "object" ||
    !("openAtLogin" in input) ||
    typeof input.openAtLogin !== "boolean" ||
    !("userConfirmed" in input) ||
    input.userConfirmed !== true
  ) {
    throw Error("login_preference_confirmation_required");
  }
  if (platform !== "darwin" || !supported) {
    throw Error("login_preference_unsupported");
  }
  await setAppRuntimeStartAtLogin(dataDir, input.openAtLogin);
  if (input.openAtLogin) await enableRuntimeService(dataDir);
  else await disableRuntimeService(dataDir);
  return readLoginPreference(dataDir, platform, supported, serviceState);
}
