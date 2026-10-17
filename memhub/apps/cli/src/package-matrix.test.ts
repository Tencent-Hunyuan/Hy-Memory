import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import { homedir, tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import { describe, expect, it } from "vitest";

const execFileAsync = promisify(execFile);
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const script = path.join(root, "scripts", "verify-package-matrix.mjs");
const bundle = path.join(root, "dist", "one-memory.cjs");
const packageVersion = (
  JSON.parse(await readFile(path.join(root, "package.json"), "utf8")) as {
    version: string;
  }
).version;
const harness = await import(
  new URL("../scripts/verify-package-matrix.mjs", import.meta.url).href
);

/** Every probe in this file spawns the real CLI bundle. The bundle resolves
 * its data dir from `HOME`, so a leaked real HOME would let a probe attach to
 * the developer's daily Core. Always pass an isolated HOME with no real data.
 */
const isolatedEnv = {
  ...process.env,
  HOME: tmpdir(),
  ONE_MEMORY_DATA_DIR: path.join(
    tmpdir(),
    "one-memory-package-matrix-isolated",
  ),
  ONE_MEMORY_TEST_REAL_HOME: homedir(),
};

async function writeRuntimeManifest(runtimeDist: string): Promise<void> {
  await writeFile(
    path.join(runtimeDist, "manifest.json"),
    `${JSON.stringify({ schema_version: 1, product: "one-memory-runtime", version: packageVersion, build_number: 1, platform: process.platform, arch: process.arch, files: {} })}\n`,
  );
}

describe("package matrix harness", () => {
  it("rejects a runtime-dist without a CLI bundle", async () => {
    const empty = await mkdtemp(path.join(tmpdir(), "memhub-empty-dist-"));
    await expect(
      execFileAsync(process.execPath, [script, empty, "--json"], {
        env: isolatedEnv,
      }),
    ).rejects.toMatchObject({ code: 1 });
  });

  // These spawn the packaged CLI once per probe and are slow under load.
  it("fails fast when --lifecycle has no runtime entry", {
    timeout: 60_000,
  }, async () => {
    const runtimeDist = await mkdtemp(
      path.join(tmpdir(), "memhub-lifecycle-missing-"),
    );
    await import("node:fs/promises").then(({ mkdir }) =>
      mkdir(path.join(runtimeDist, "bin"), { recursive: true }),
    );
    await writeFile(
      path.join(runtimeDist, "bin", "one-memory.cjs"),
      await readFile(bundle, "utf8"),
    );
    await writeRuntimeManifest(runtimeDist);
    const result = await (async () => {
      try {
        await execFileAsync(
          process.execPath,
          [script, runtimeDist, "--lifecycle", "--json"],
          { env: isolatedEnv },
        );
        return null;
      } catch (error) {
        const failure = error as { stdout?: string };
        return JSON.parse(failure.stdout ?? "{}") as {
          failures?: string[];
        };
      }
    })();
    expect(result?.failures?.join("\n")).toContain(
      "packaged runtime entry not found",
    );
  });

  it("documents usage and rejects a missing runtime-dist with exit 2", async () => {
    const help = await execFileAsync(process.execPath, [script, "--help"], {
      env: isolatedEnv,
    });
    expect(help.stdout).toContain(
      "verify-package-matrix.mjs <runtime-dist> [--expect-sha <sha256>] [--stopped-commands] [--lifecycle] [--json]",
    );
    expect(help.stdout).toContain("--stopped-commands");
    await expect(
      execFileAsync(process.execPath, [script], { env: isolatedEnv }),
    ).rejects.toMatchObject({
      code: 2,
      stderr: expect.stringContaining(
        "usage: verify-package-matrix.mjs <runtime-dist>",
      ),
    });
  });

  it("fails closed on every destructive UI-write command without a Runtime", {
    timeout: 120_000,
  }, async () => {
    const runtimeDist = await mkdtemp(
      path.join(tmpdir(), "memhub-stopped-matrix-"),
    );
    await import("node:fs/promises").then(({ mkdir }) =>
      mkdir(path.join(runtimeDist, "bin"), { recursive: true }),
    );
    await writeFile(
      path.join(runtimeDist, "bin", "one-memory.cjs"),
      await readFile(bundle, "utf8"),
    );
    await writeRuntimeManifest(runtimeDist);
    const positive = await execFileAsync(
      process.execPath,
      [script, runtimeDist, "--stopped-commands", "--json"],
      { env: isolatedEnv },
    );
    const document = JSON.parse(positive.stdout) as {
      state: string;
      commands: {
        stopped_destructive: {
          results: Record<
            string,
            { exit: number; state: string; code: string }
          >;
          failures: string[];
        };
      };
    };
    expect(document.state).toBe("verified");
    const results = document.commands.stopped_destructive.results;
    // 11 destructive commands, each unknown/runtime_not_running with exit 3.
    expect(Object.keys(results)).toHaveLength(11);
    for (const [name, result] of Object.entries(results)) {
      expect(result, `${name} did not fail closed`).toEqual({
        exit: 3,
        state: "unknown",
        code: "runtime_not_running",
      });
    }
    expect(document.commands.stopped_destructive.failures).toEqual([]);
  });

  it("rejects a CLI that falsely reports a destructive write as committed", {
    timeout: 120_000,
  }, async () => {
    const runtimeDist = await mkdtemp(
      path.join(tmpdir(), "memhub-fake-success-"),
    );
    await import("node:fs/promises").then(({ mkdir }) =>
      mkdir(path.join(runtimeDist, "bin"), { recursive: true }),
    );
    const real = await readFile(bundle, "utf8");
    const shim = [
      "#!/usr/bin/env node",
      "const a=process.argv.slice(2);",
      "if(['calls','import-file','notes','extraction','graph','models'].includes(a[0])||(a[0]==='processing'&&a[1]==='extract')){",
      '  process.stdout.write(\'{"schema_version":1,"product":"memhub","state":"committed","operation_id":"x","receipt":{}}\\n\');',
      "  process.exit(0);",
      "}",
      real.replace(/^#![^\n]*\n/, ""),
    ].join("\n");
    await writeFile(path.join(runtimeDist, "bin", "one-memory.cjs"), shim);
    await writeRuntimeManifest(runtimeDist);
    const failure = await (async () => {
      try {
        await execFileAsync(
          process.execPath,
          [script, runtimeDist, "--stopped-commands", "--json"],
          { env: isolatedEnv },
        );
        return null;
      } catch (error) {
        return JSON.parse((error as { stdout?: string }).stdout ?? "{}") as {
          state: string;
          failures: string[];
        };
      }
    })();
    expect(failure?.state).toBe("failed");
    expect(
      failure?.failures.filter((item) => item.includes("did not fail closed")),
    ).toHaveLength(11);
  });

  it("can require an expected bundle SHA and reports bundle metadata", {
    timeout: 60_000,
  }, async () => {
    const runtimeDist = await mkdtemp(
      path.join(tmpdir(), "memhub-sha-matrix-"),
    );
    await import("node:fs/promises").then(({ mkdir }) =>
      mkdir(path.join(runtimeDist, "bin"), { recursive: true }),
    );
    const source = await readFile(bundle, "utf8");
    await writeFile(path.join(runtimeDist, "bin", "one-memory.cjs"), source);
    await writeRuntimeManifest(runtimeDist);
    const sha = createHash("sha256").update(source).digest("hex");

    const positive = await execFileAsync(
      process.execPath,
      [script, runtimeDist, "--expect-sha", sha, "--json"],
      { env: isolatedEnv },
    );
    const document = JSON.parse(positive.stdout) as {
      bundle: { sha256: string; bytes: number };
    };
    expect(document.bundle.sha256).toBe(sha);
    expect(document.bundle.bytes).toBeGreaterThan(0);

    await expect(
      execFileAsync(
        process.execPath,
        [script, runtimeDist, "--expect-sha", "0".repeat(64), "--json"],
        { env: isolatedEnv },
      ),
    ).rejects.toMatchObject({ code: 1 });
  });

  it("rejects a catalog missing a required command surface", async () => {
    const catalog = {
      schema_version: 1,
      product: "memhub",
      commands: [{ name: "status" }],
      capability_aliases: {
        daemon: ["start", "stop", "restart", "status"],
        "startup-service": [
          "service-plan",
          "service-install",
          "service-uninstall",
        ],
      },
    };
    const failures = harness.validateCatalog(catalog);
    expect(failures).toContain("required command is not declared: delete-data");
    expect(failures).toContain("required command is not declared: recall");
    expect(failures).toContain(
      "required command is not declared: service-plan",
    );
  });

  it("requires the service-install readiness failure marker in a package", async () => {
    const runtimeDist = await mkdtemp(
      path.join(tmpdir(), "memhub-stale-service-install-"),
    );
    await import("node:fs/promises").then(({ mkdir }) =>
      mkdir(path.join(runtimeDist, "bin"), { recursive: true }),
    );
    const stale = (await readFile(bundle, "utf8")).replaceAll(
      "runtime_service_activation_unverified",
      "",
    );
    await writeFile(path.join(runtimeDist, "bin", "one-memory.cjs"), stale);
    await writeRuntimeManifest(runtimeDist);
    const failure = await (async () => {
      try {
        await execFileAsync(process.execPath, [script, runtimeDist, "--json"], {
          env: isolatedEnv,
        });
        return null;
      } catch (error) {
        return JSON.parse((error as { stdout?: string }).stdout ?? "{}") as {
          state: string;
          failures: string[];
        };
      }
    })();
    expect(failure?.state).toBe("failed");
    expect(failure?.failures.join("\n")).toContain(
      "runtime_service_activation_unverified",
    );
  });

  it("rejects a catalog that declares the parent command but not the new verb", async () => {
    // A bundle can still declare `models` while missing `models activate`.
    // Names alone would pass it; the usage marker must not.
    const declared = [
      "install",
      "init",
      "sources",
      "sync",
      "memories",
      "calls",
      "import-file",
      "notes",
      "extraction",
      "graph",
      "models",
      "host",
      "doctor",
      "logs",
      "ui",
      "start",
      "stop",
      "restart",
      "status",
      "service-plan",
      "service-install",
      "service-uninstall",
      "backup",
      "upgrade",
      "uninstall",
      "delete-data",
      "recall",
      "setup",
      "scan",
      "progress",
      "jobs",
      "processing",
      "path",
      "adapter",
      "write",
      "task",
      "serve",
    ];
    const catalog = {
      schema_version: 1,
      product: "memhub",
      // Every parent command is present, but each usage string omits the new
      // verb that the parity work introduced.
      commands: declared.map((name) => ({
        name,
        usage: `memhub ${name} list`,
      })),
      capability_aliases: {
        daemon: ["start", "stop", "restart", "status"],
        "startup-service": [
          "service-plan",
          "service-install",
          "service-uninstall",
        ],
      },
    };
    const failures = harness.validateCatalog(catalog);
    expect(failures).toContain(
      'command "models" is missing usage "memhub models activate"',
    );
    expect(failures).toContain(
      'command "import-file" is missing usage "memhub import-file discard"',
    );
    expect(failures).toContain(
      'command "jobs" is missing usage "memhub jobs start"',
    );
  });

  it("is documented as a read-only contract probe", async () => {
    const source = await readFile(script, "utf8");
    expect(source).toContain("Read-only package CLI contract probe");
    expect(source).toContain('rest.includes("--lifecycle")');
    expect(source).toContain("stop left the packaged Runtime process alive");
    expect(source).toContain("packaged runtime entry not found");
    expect(source).toContain('"runtime_not_running"');
  });
});
