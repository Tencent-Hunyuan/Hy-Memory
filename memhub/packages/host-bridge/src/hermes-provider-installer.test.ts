import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import {
  applyRecallInstall,
  applyRecallUninstall,
  hermesLayout,
  planRecallInstall,
  planRecallUninstall,
  previewRecallInstall,
  previewRecallUninstall,
} from "./recall-installer.js";
import { readYamlMemoryProvider } from "./yaml-mcp.js";

const roots: string[] = [];

async function fixture() {
  const root = await mkdtemp(path.join(os.tmpdir(), "memhub-hermes-provider-"));
  roots.push(root);
  const hermes = path.join(root, ".hermes");
  const source = path.join(root, "assets", "hy_memory");
  await mkdir(source, { recursive: true });
  await Promise.all([
    writeFile(path.join(source, "__init__.py"), "provider = 'fixture'\n"),
    writeFile(path.join(source, "provider.py"), "class Provider: pass\n"),
    writeFile(path.join(source, "plugin.yaml"), "name: hy_memory\n"),
  ]);
  await mkdir(hermes, { recursive: true });
  await writeFile(
    path.join(hermes, "config.yaml"),
    "# keep\nmemory:\n  provider: existing_provider\n  compact: true\nmodels:\n  default: test\n",
  );
  const runtime = {
    runtimeCommand: "/synthetic/node",
    mcpEntryPath: "/synthetic/mcp.cjs",
    discoveryPath: path.join(root, "broker.json"),
    credentialFile: path.join(root, "credential"),
    hermesProviderSource: source,
  };
  return { root, hermes, source, runtime, layout: hermesLayout(hermes) };
}

afterEach(async () => {
  await Promise.all(
    roots.splice(0).map((root) => rm(root, { recursive: true, force: true })),
  );
});

describe("Hermes native provider install", () => {
  it("replaces the selected provider and restores it on managed uninstall", async () => {
    const value = await fixture();
    const plan = await planRecallInstall(value.layout, value.runtime, {
      takeover: true,
    });
    const preview = await previewRecallInstall(plan);
    expect(preview.files.map((file) => file.path)).toEqual(
      expect.arrayContaining([
        path.join(value.hermes, "config.yaml"),
        path.join(value.hermes, "plugins/hy_memory/provider.py"),
        path.join(value.hermes, "plugins/hy_memory/connection.json"),
      ]),
    );
    await applyRecallInstall(plan, preview.fingerprint);

    const config = await readFile(
      path.join(value.hermes, "config.yaml"),
      "utf8",
    );
    expect(readYamlMemoryProvider(config)).toEqual({
      existed: true,
      value: "hy_memory",
    });
    expect(config).toContain("compact: true");
    expect(config).toContain("models:");
    const connection = JSON.parse(
      await readFile(
        path.join(value.hermes, "plugins/hy_memory/connection.json"),
        "utf8",
      ),
    );
    expect(connection).toEqual({
      schema_version: 1,
      transport: "memhub-broker",
      discovery_path: value.runtime.discoveryPath,
      credential_file: value.runtime.credentialFile,
    });

    const removal = await planRecallUninstall(value.layout);
    const removalPreview = previewRecallUninstall(removal);
    await applyRecallUninstall(removal, removalPreview.fingerprint);
    const restored = await readFile(
      path.join(value.hermes, "config.yaml"),
      "utf8",
    );
    expect(readYamlMemoryProvider(restored)).toEqual({
      existed: true,
      value: "existing_provider",
    });
    await expect(
      readFile(path.join(value.hermes, "plugins/hy_memory/provider.py")),
    ).rejects.toMatchObject({ code: "ENOENT" });
  });

  it("keeps the original provider lineage across reinstall", async () => {
    const value = await fixture();
    let plan = await planRecallInstall(value.layout, value.runtime, {
      takeover: true,
    });
    await applyRecallInstall(plan);
    await writeFile(
      path.join(value.source, "provider.py"),
      "class ProviderV2: pass\n",
    );
    plan = await planRecallInstall(value.layout, value.runtime, {
      takeover: true,
    });
    await applyRecallInstall(plan);

    const removal = await planRecallUninstall(value.layout);
    await applyRecallUninstall(removal);
    const restored = await readFile(
      path.join(value.hermes, "config.yaml"),
      "utf8",
    );
    expect(readYamlMemoryProvider(restored).value).toBe("existing_provider");
  });

  it("refuses to remove modified provider files", async () => {
    const value = await fixture();
    const plan = await planRecallInstall(value.layout, value.runtime, {
      takeover: true,
    });
    await applyRecallInstall(plan);
    await writeFile(
      path.join(value.hermes, "plugins/hy_memory/provider.py"),
      "user modification\n",
    );
    await expect(planRecallUninstall(value.layout)).rejects.toThrow(
      /provider files changed/,
    );
  });
});
