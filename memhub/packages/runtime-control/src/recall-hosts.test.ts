import { mkdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, expect, it } from "vitest";
import { recallHosts } from "./recall-hosts.js";

const roots: string[] = [];

afterEach(async () => {
  await Promise.all(
    roots.splice(0).map((root) => rm(root, { recursive: true, force: true })),
  );
});

it("moves the Hermes host layout with HERMES_HOME instead of always using ~/.hermes", async () => {
  const root = path.join(tmpdir(), `recall-hosts-hermes-${process.pid}-${Date.now()}`);
  roots.push(root);
  const profile = path.join(root, ".hermes", "profiles", "luo-bo");
  await mkdir(profile, { recursive: true });

  const hosts = await recallHosts(root, { HERMES_HOME: profile });
  const hermes = hosts.find((host) => host.hostId === "hermes");

  expect(hermes?.presencePath).toBe(profile);
  expect(hermes?.layout.configPath).toBe(path.join(profile, "config.yaml"));
});

it("gives every DeepSeek Harness profile a matching unique layout identity", async () => {
  const root = path.join(tmpdir(), `recall-hosts-${process.pid}-${Date.now()}`);
  roots.push(root);
  const dshHome = path.join(root, ".dsh");
  for (const profile of ["web", "headless"]) {
    const directory = path.join(dshHome, "profiles", profile);
    await mkdir(directory, { recursive: true });
    await writeFile(path.join(directory, "cordis.yml"), "plugins: []\n");
  }

  const hosts = (await recallHosts(root, { DSH_HOME: dshHome })).filter(
    (host) => host.hostId.startsWith("deepseek-harness:"),
  );

  expect(hosts.map((host) => host.hostId)).toEqual([
    "deepseek-harness:headless",
    "deepseek-harness:web",
  ]);
  expect(hosts.map((host) => host.layout.hostId)).toEqual(
    hosts.map((host) => host.hostId),
  );
  expect(hosts.map((host) => host.layout.hostLabel)).toEqual(
    hosts.map((host) => host.label),
  );
});
