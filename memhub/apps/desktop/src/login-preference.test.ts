import { access, mkdtemp } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import {
  claimRuntimeServiceForApp,
  claimRuntimeServiceForCli,
  enableRuntimeService,
  type RuntimeServiceState,
  runtimeServiceEnableMarkerPath,
} from "@one-memory/runtime-control";
import { expect, it } from "vitest";
import { readLoginPreference, setLoginPreference } from "./login-preference";

async function appOwned() {
  const dataDir = await mkdtemp(path.join(os.tmpdir(), "memhub-login-"));
  await claimRuntimeServiceForApp({
    dataDir,
    appBundle: "/Applications/MemHub.app",
    appExecutable: "/Applications/MemHub.app/Contents/MacOS/MemHub",
  });
  await enableRuntimeService(dataDir);
  return dataDir;
}

it("reads and changes the App-owned LaunchAgent login preference", async () => {
  const dataDir = await appOwned();
  await expect(readLoginPreference(dataDir, "darwin", true)).resolves.toEqual({
    supported: true,
    enabled: true,
    status: "enabled",
  });

  await expect(
    setLoginPreference(
      dataDir,
      "darwin",
      { openAtLogin: false, userConfirmed: true },
      true,
    ),
  ).resolves.toEqual({
    supported: true,
    enabled: false,
    status: "disabled",
  });
  await expect(
    access(runtimeServiceEnableMarkerPath(dataDir)),
  ).rejects.toMatchObject({
    code: "ENOENT",
  });

  await expect(
    setLoginPreference(
      dataDir,
      "darwin",
      { openAtLogin: true, userConfirmed: true },
      true,
    ),
  ).resolves.toMatchObject({ enabled: true, status: "enabled" });
  await expect(
    access(runtimeServiceEnableMarkerPath(dataDir)),
  ).resolves.toBeUndefined();
});

it("shows a CLI-owned service as read-only", async () => {
  const dataDir = await mkdtemp(path.join(os.tmpdir(), "memhub-login-"));
  await claimRuntimeServiceForCli(dataDir);

  await expect(readLoginPreference(dataDir, "darwin", true)).resolves.toEqual({
    supported: false,
    enabled: true,
    status: "managed-by-cli",
  });
  await expect(
    setLoginPreference(
      dataDir,
      "darwin",
      { openAtLogin: false, userConfirmed: true },
      true,
    ),
  ).rejects.toThrow("runtime_service_owner_not_app");
});

it("rejects unsupported and unconfirmed writes", async () => {
  const dataDir = await appOwned();
  await expect(
    readLoginPreference(dataDir, "linux", true),
  ).resolves.toMatchObject({
    supported: false,
  });
  await expect(
    setLoginPreference(dataDir, "darwin", { openAtLogin: true }, true),
  ).rejects.toThrow("login_preference_confirmation_required");
  await expect(
    setLoginPreference(
      dataDir,
      "darwin",
      { openAtLogin: true, userConfirmed: true },
      false,
    ),
  ).rejects.toThrow("login_preference_unsupported");
});

it("reads a launchctl-disabled service as off and not switchable, asked fresh", async () => {
  const dataDir = await appOwned();
  let state: RuntimeServiceState = {
    state: "disabled",
    reason: "service-disabled-by-launchctl",
    jobPid: null,
  };
  const serviceState = async () => state;

  await expect(
    readLoginPreference(dataDir, "darwin", true, serviceState),
  ).resolves.toEqual({
    supported: false,
    enabled: false,
    status: "disabled-by-system",
  });
  state = { state: "enabled", reason: "service-enabled", jobPid: 4321 };
  await expect(
    readLoginPreference(dataDir, "darwin", true, serviceState),
  ).resolves.toEqual({ supported: true, enabled: true, status: "enabled" });
});
