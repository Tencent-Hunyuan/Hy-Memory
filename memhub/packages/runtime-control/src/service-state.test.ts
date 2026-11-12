import { mkdir, mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { describe, expect, it, vi } from "vitest";

import {
  parseLaunchctlPid,
  queryRuntimeServiceState,
  type ServiceStateProbe,
} from "./service-state.js";

const LABEL = "com.tencent.onememory.runtime";

async function home() {
  const root = await mkdtemp(path.join(tmpdir(), "service-state-"));
  const dataDir = path.join(root, ".one-memory");
  await mkdir(dataDir, { recursive: true });
  return { root, dataDir };
}

async function writeLaunchAgent(root: string, dataDir: string, label = LABEL) {
  const directory = path.join(root, "Library", "LaunchAgents");
  await mkdir(directory, { recursive: true });
  await writeFile(
    path.join(directory, `${label}.plist`),
    `<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
<key>Label</key><string>${label}</string>
<key>EnvironmentVariables</key><dict><key>ONE_MEMORY_DATA_DIR</key><string>${dataDir.replaceAll("&", "&amp;")}</string></dict>
</dict></plist>
`,
  );
}

function launchctl(options: {
  disabled?: boolean;
  job?: { code: number; pid?: number };
  overridesCode?: number;
}): ServiceStateProbe {
  return vi.fn(async (command: readonly string[]) => {
    if (command[1] === "print-disabled") {
      return {
        code: options.overridesCode ?? 0,
        stdout: `disabled services = {\n\t"${LABEL}" => ${options.disabled ? "disabled" : "enabled"}\n}\n`,
      };
    }
    const job = options.job ?? { code: 0, pid: 4321 };
    return {
      code: job.code,
      stdout:
        job.code === 0
          ? `${LABEL} = {\n\tpath = /x.plist\n${job.pid ? `\tpid = ${job.pid}\n` : ""}\tendpoints = {\n\t\tpid = 1\n\t}\n}\n`
          : "",
    };
  });
}

describe("asking launchd whether the background service is on", () => {
  it("is enabled for an installed, loaded job with its enable marker", async () => {
    const { root, dataDir } = await home();
    await writeLaunchAgent(root, dataDir);

    await expect(
      queryRuntimeServiceState({
        dataDir,
        home: root,
        platform: "darwin",
        uid: 501,
        probe: launchctl({}),
        markerEnabled: async () => true,
      }),
    ).resolves.toEqual({
      state: "enabled",
      reason: "service-enabled",
      jobPid: 4321,
    });
  });

  it("is disabled when no LaunchAgent serves this data dir", async () => {
    const { root, dataDir } = await home();
    const probe = launchctl({});

    await expect(
      queryRuntimeServiceState({
        dataDir,
        home: root,
        platform: "darwin",
        uid: 501,
        probe,
        markerEnabled: async () => true,
      }),
    ).resolves.toMatchObject({
      state: "disabled",
      reason: "service-not-installed",
    });
    expect(probe).not.toHaveBeenCalled();
  });

  it("ignores a LaunchAgent that serves another data dir", async () => {
    const { root, dataDir } = await home();
    await writeLaunchAgent(root, path.join(root, "other-data"));

    await expect(
      queryRuntimeServiceState({
        dataDir,
        home: root,
        platform: "darwin",
        uid: 501,
        probe: launchctl({}),
        markerEnabled: async () => true,
      }),
    ).resolves.toMatchObject({
      state: "disabled",
      reason: "service-not-installed",
    });
  });

  it("finds an isolated install root's scoped label", async () => {
    const { root, dataDir } = await home();
    await writeLaunchAgent(root, dataDir, `${LABEL}.0123456789abcdef`);
    const probe: ServiceStateProbe = async (command) =>
      command[1] === "print-disabled"
        ? { code: 0, stdout: "" }
        : command[2] === `gui/501/${LABEL}.0123456789abcdef`
          ? { code: 0, stdout: "\tpid = 999\n" }
          : { code: 113, stdout: "" };

    await expect(
      queryRuntimeServiceState({
        dataDir,
        home: root,
        platform: "darwin",
        uid: 501,
        probe,
        markerEnabled: async () => true,
      }),
    ).resolves.toEqual({
      state: "enabled",
      reason: "service-enabled",
      jobPid: 999,
    });
  });

  it("is disabled under a launchctl disable override (the transient fallback)", async () => {
    const { root, dataDir } = await home();
    await writeLaunchAgent(root, dataDir);

    await expect(
      queryRuntimeServiceState({
        dataDir,
        home: root,
        platform: "darwin",
        uid: 501,
        probe: launchctl({ disabled: true, job: { code: 113 } }),
        markerEnabled: async () => true,
      }),
    ).resolves.toEqual({
      state: "disabled",
      reason: "service-disabled-by-launchctl",
      jobPid: null,
    });
  });

  it("is disabled when Launch at login is off, even with the job still loaded", async () => {
    const { root, dataDir } = await home();
    await writeLaunchAgent(root, dataDir);

    await expect(
      queryRuntimeServiceState({
        dataDir,
        home: root,
        platform: "darwin",
        uid: 501,
        probe: launchctl({}),
        markerEnabled: async () => false,
      }),
    ).resolves.toEqual({
      state: "disabled",
      reason: "service-enable-marker-absent",
      jobPid: 4321,
    });
  });

  it("stays enabled for an installed job launchd has not loaded yet", async () => {
    const { root, dataDir } = await home();
    await writeLaunchAgent(root, dataDir);

    await expect(
      queryRuntimeServiceState({
        dataDir,
        home: root,
        platform: "darwin",
        uid: 501,
        probe: launchctl({ job: { code: 113 } }),
        markerEnabled: async () => true,
      }),
    ).resolves.toEqual({
      state: "enabled",
      reason: "service-enabled",
      jobPid: null,
    });
  });

  it.each([
    ["the override table cannot be read", launchctl({ overridesCode: 1 })],
    ["the job query fails", launchctl({ job: { code: 5 } })],
    [
      "launchctl cannot run",
      (async () => {
        throw new Error("spawn failed");
      }) as ServiceStateProbe,
    ],
  ])("is unknown when %s", async (_case, probe) => {
    const { root, dataDir } = await home();
    await writeLaunchAgent(root, dataDir);

    await expect(
      queryRuntimeServiceState({
        dataDir,
        home: root,
        platform: "darwin",
        uid: 501,
        probe,
        markerEnabled: async () => true,
      }),
    ).resolves.toMatchObject({ state: "unknown" });
  });

  it("is unknown when the query outlives its budget", async () => {
    const { root, dataDir } = await home();
    await writeLaunchAgent(root, dataDir);

    await expect(
      queryRuntimeServiceState({
        dataDir,
        home: root,
        platform: "darwin",
        uid: 501,
        timeoutMs: 20,
        probe: () => new Promise(() => {}),
        markerEnabled: async () => true,
      }),
    ).resolves.toEqual({
      state: "unknown",
      reason: "service-state-timeout",
      jobPid: null,
    });
  });

  it("is unknown without a user id to name the launchd domain", async () => {
    const { root, dataDir } = await home();
    await writeLaunchAgent(root, dataDir);

    await expect(
      queryRuntimeServiceState({
        dataDir,
        home: root,
        platform: "darwin",
        probe: launchctl({}),
        markerEnabled: async () => true,
      }),
    ).resolves.toMatchObject({ state: "unknown" });
  });
});

describe("asking systemd whether the background service is on", () => {
  async function writeUnit(root: string, dataDir: string) {
    const directory = path.join(root, ".config", "systemd", "user");
    await mkdir(directory, { recursive: true });
    await writeFile(
      path.join(directory, "one-memory.service"),
      `[Service]\nEnvironment="ONE_MEMORY_DATA_DIR=${dataDir}"\n`,
    );
  }
  const systemctl =
    (answer: string, code = 0): ServiceStateProbe =>
    async (command) =>
      command.includes("is-enabled")
        ? { code, stdout: `${answer}\n` }
        : { code: 0, stdout: "2468\n" };

  it("follows systemctl is-enabled", async () => {
    const { root, dataDir } = await home();
    await writeUnit(root, dataDir);

    await expect(
      queryRuntimeServiceState({
        dataDir,
        home: root,
        platform: "linux",
        probe: systemctl("enabled"),
      }),
    ).resolves.toEqual({
      state: "enabled",
      reason: "service-enabled",
      jobPid: 2468,
    });
    await expect(
      queryRuntimeServiceState({
        dataDir,
        home: root,
        platform: "linux",
        probe: systemctl("disabled", 1),
      }),
    ).resolves.toMatchObject({
      state: "disabled",
      reason: "service-disabled-by-systemd",
    });
  });

  it("is disabled without a unit and unknown without an answer", async () => {
    const { root, dataDir } = await home();
    await expect(
      queryRuntimeServiceState({
        dataDir,
        home: root,
        platform: "linux",
        probe: systemctl("enabled"),
      }),
    ).resolves.toMatchObject({
      state: "disabled",
      reason: "service-not-installed",
    });
    await writeUnit(root, dataDir);
    await expect(
      queryRuntimeServiceState({
        dataDir,
        home: root,
        platform: "linux",
        probe: systemctl("", 1),
      }),
    ).resolves.toMatchObject({ state: "unknown" });
  });
});

it("reads only the job's own top-level pid", () => {
  expect(parseLaunchctlPid("x = {\n\tpid = 77\n\tendpoints = {\n}\n")).toBe(77);
  expect(parseLaunchctlPid("x = {\n\tstate = waiting\n}\n")).toBeNull();
});
