import { mkdir, mkdtemp, readFile, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { HostGrantStore } from "@one-memory/host-bridge";
import { afterAll, describe, expect, it } from "vitest";
import { HostPermissions } from "./host-permissions.js";

const roots: string[] = [];

afterAll(async () => {
  const fs = await import("node:fs");
  await Promise.all(
    roots.map((root) => fs.promises.rm(root, { recursive: true, force: true })),
  );
});

/** The #387 scene: a hand-edited host-grants.json that does not validate. */
async function corruptHome() {
  const root = await mkdtemp(path.join(os.tmpdir(), "om-quarantine-"));
  roots.push(root);
  const dataDir = path.join(root, "data");
  await mkdir(dataDir, { mode: 0o700 });
  const hostPermissions = path.join(dataDir, "host-permissions");
  const grants = path.join(hostPermissions, "grants");
  await mkdir(hostPermissions, { mode: 0o700 });
  await mkdir(path.join(hostPermissions, "installations"), {
    mode: 0o700,
  });
  const file = path.join(grants, "host-grants.json");
  const store = await HostGrantStore.initialize(grants);
  await store.createPairing({ displayName: "quarantine" });
  const before = await readFile(file, "utf8");
  const corrupt = JSON.parse(before);
  // Values the schema does not know, in an approved entry: validation fails.
  corrupt.installations[0].allowedScopes = [
    "all-apps",
    "chat",
    "working",
    "coding",
  ];
  await writeFile(file, JSON.stringify(corrupt));
  const runtime = {
    runtimeCommand: path.join(root, "mock-runtime"),
    mcpEntryPath: path.join(root, "mock-mcp.cjs"),
    discoveryPath: path.join(dataDir, "broker.json"),
    recallCliPath: path.join(root, "mock-cli.cjs"),
  };
  return { root, dataDir, runtime, file };
}

describe("HostPermissions with a host-grants store that failed validation", () => {
  it("opens quarantined instead of refusing startup (#387)", async () => {
    const { dataDir, runtime } = await corruptHome();
    // This call is what the Runtime startup path awaits uncaught; it used to
    // reject with host_grant_store_corrupt and crash-loop the service.
    const permissions = await HostPermissions.open(dataDir, runtime);
    expect(permissions.hostGrantStore.quarantined).toContain("allowedScopes");
  });

  it("denies every grant operation with the structural reason", async () => {
    const { dataDir, runtime, file } = await corruptHome();
    const permissions = await HostPermissions.open(dataDir, runtime);
    const store = permissions.hostGrantStore;
    await expect(store.listInstallations()).rejects.toMatchObject({
      code: "host_grant_store_corrupt",
      reason: expect.stringContaining("allowedScopes"),
    });
    await expect(
      store.authenticate(`omhg_v1_${"a".repeat(43)}`),
    ).rejects.toMatchObject({ code: "host_grant_store_corrupt" });
    // The broken file is left untouched for a human to fix.
    expect(JSON.parse(await readFile(file, "utf8"))).toMatchObject({
      schemaVersion: 1,
    });
  });
});
