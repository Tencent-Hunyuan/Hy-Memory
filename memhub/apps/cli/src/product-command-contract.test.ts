import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdtemp, readdir, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { CAPABILITY_ALIASES, COMMANDS } from "./command-catalog.js";
import { renderCatalogDocument, renderGeneralHelp } from "./usage.js";
import { CLI_VERSION, PRODUCT } from "./version.js";

const packageJson = JSON.parse(
  await readFile(new URL("../package.json", import.meta.url), "utf8"),
) as {
  name: string;
  version: string;
  bin: Record<string, string>;
};

const bundle = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../dist/one-memory.cjs",
);

// The user requirement (2026-09-29 10:5x) is a short `memhub ...` command that
// works immediately after install. These guards exist because the previous
// product name `one-memory` was hardcoded in 36 usage strings plus the help and
// version surfaces; a rename that misses any one of them silently leaves a
// command the user cannot invoke.
describe("memhub short-command contract", () => {
  it("exposes the synchronized package SemVer", () => {
    expect(CLI_VERSION).toBe(packageJson.version);
    expect(CLI_VERSION).not.toBe("development");
  });

  it("names the product memhub", () => {
    expect(PRODUCT).toBe("memhub");
  });

  it("accepts memhub as the documented top-level invocation", () => {
    expect(renderGeneralHelp()).toContain("Usage: memhub <command>");
  });

  it("starts every catalog usage line with the memhub command", () => {
    for (const command of COMMANDS) {
      expect(
        command.usage.startsWith(`memhub ${command.name}`),
        `${command.name} usage is not a memhub invocation: ${command.usage}`,
      ).toBe(true);
      if (command.alternative_usage) {
        expect(
          command.alternative_usage.startsWith(`memhub ${command.name}`),
          `${command.name} alternative usage is not a memhub invocation`,
        ).toBe(true);
      }
    }
  });

  it("documents the required short forms as separate tokens", () => {
    const flattened = COMMANDS.flatMap((command) =>
      [command.usage, command.alternative_usage ?? ""].join("\n"),
    ).join("\n");
    for (const shortForm of [
      "memhub status",
      "memhub sources list",
      "memhub recall",
      "memhub memories list",
      "memhub host install",
    ]) {
      expect(flattened).toContain(shortForm);
    }
  });

  it("maps lifecycle capability names to existing commands without duplicates", () => {
    expect(CAPABILITY_ALIASES).toEqual({
      daemon: ["start", "stop", "restart", "status"],
      "startup-service": [
        "service-plan",
        "service-install",
        "service-uninstall",
      ],
    });

    const commandNames = new Set(COMMANDS.map((command) => command.name));
    for (const [capability, targets] of Object.entries(CAPABILITY_ALIASES)) {
      expect(targets.length).toBeGreaterThan(0);
      expect(new Set(targets).size).toBe(targets.length);
      for (const target of targets) {
        expect(
          commandNames.has(target),
          `${capability} maps to unknown command ${target}`,
        ).toBe(true);
      }
    }

    const catalog = JSON.parse(renderCatalogDocument()) as {
      capability_aliases?: Record<string, string[]>;
    };
    expect(catalog.capability_aliases).toEqual(CAPABILITY_ALIASES);
  });

  it("covers the required product lifecycle in the declared command surface", () => {
    const commandNames = new Set(COMMANDS.map((command) => command.name));
    const mappedNames = new Set(
      Object.values(CAPABILITY_ALIASES).flatMap((targets) => [...targets]),
    );
    const declared = new Set([...commandNames, ...mappedNames]);

    for (const required of [
      "install",
      "init",
      "sources",
      "sync",
      "memories",
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
    ]) {
      expect(
        declared.has(required),
        `required product capability has no declared command: ${required}`,
      ).toBe(true);
    }
  });

  // C6 App BLOCK 25f4ecf: UI write operations the CLI did not expose. The
  // short forms are the deliverable, so they are asserted as literal usage
  // tokens rather than only as catalog names.
  it("declares the UI write commands with memhub short forms", () => {
    const flattened = COMMANDS.flatMap((command) =>
      [command.usage, command.alternative_usage ?? ""].join("\n"),
    ).join("\n");
    for (const shortForm of [
      "memhub memories add",
      "memhub memories add-status",
      "memhub models setup",
      "memhub models add",
      "memhub models test",
      "memhub models activate",
      "memhub models edit",
      "memhub graph-width",
      "memhub sources connect",
      "memhub write submit",
      "memhub write status",
      "memhub memories update",
      "memhub memories delete",
    ]) {
      expect(flattened).toContain(shortForm);
    }
    // No "note" concept and no plan files on the CLI surface.
    for (const removed of ["memhub notes", "memhub calls", "--plan-file"]) {
      expect(flattened).not.toContain(removed);
    }
    expect(COMMANDS.some((command) => command.name === "permission")).toBe(
      false,
    );
  });

  it("ships a memhub bin entry pointing at a real bundle path", () => {
    expect(packageJson.bin.memhub).toBe("dist/one-memory.cjs");
    expect(packageJson.bin["memhub-cli"]).toBe("dist/one-memory.cjs");
  });

  it("keeps one-memory only as a compatibility alias, not the primary name", () => {
    expect(Object.keys(packageJson.bin)[0]).toBe("memhub");
  });

  // The 2026-09-29 decision is "switches are the only authorization surface".
  // A surviving standalone `sources revoke` would be a competing second step,
  // so the parser must reject it and help must not advertise it.
  it("does not expose a standalone sources revoke entry", () => {
    const sources = COMMANDS.find((command) => command.name === "sources");
    const documented = `${sources?.usage ?? ""}\n${sources?.alternative_usage ?? ""}`;
    expect(documented).not.toContain("sources revoke");
  });

  // A2 / O-16 (2026-09-29 acceptance): the original defect was `init`
  // advertising an interactive wizard it did not have. A2 now implements the
  // wizard, so the requirement flips: never promise a prompt without saying it
  // is TTY-only, because a machine caller must never be blocked. The guard
  // still covers every user-visible string (summary, usage, and each option),
  // which is what let the first version slip past an option summary.
  it("promises the init wizard only alongside its no-TTY fallback", () => {
    const init = COMMANDS.find((command) => command.name === "init");
    expect(init).toBeDefined();
    const text = [
      init?.summary ?? "",
      init?.usage ?? "",
      init?.alternative_usage ?? "",
      ...(init?.options ?? []).flatMap((option) => [
        option.flag,
        option.summary,
      ]),
    ].join("\n");
    // If the wizard is mentioned at all, the non-TTY behaviour must be too.
    if (/交互式|向导/.test(text)) {
      expect(text).toMatch(/非终端|不读 stdin|TTY/);
    }
  });

  // Help must not understate what the CLI actually does. `path list/add/remove`
  // and `adapter list/probe` are wired to real Runtime endpoints (verified live),
  // but an earlier catalog text still claimed they were "尚不可用". A user
  // reading `help path` would not try a command that works.
  it("does not call the wired path/adapter commands unavailable", () => {
    for (const name of ["scan-roots", "adapter"]) {
      const command = COMMANDS.find((entry) => entry.name === name);
      expect(command).toBeDefined();
      const text = [
        command?.summary ?? "",
        ...(command?.options ?? []).map((option) => option.summary),
      ].join("\n");
      expect(text).not.toContain("尚不可用");
    }
  });

  it("documents logs --json as NDJSON, not one JSON document", () => {
    const logs = COMMANDS.find((command) => command.name === "logs");
    expect(logs).toBeDefined();
    const text = `${logs?.summary ?? ""}\n${(logs?.options ?? [])
      .map((option) => `${option.flag} ${option.summary}`)
      .join("\n")}`;
    expect(text).toContain("NDJSON");
    expect(text).toContain("每行一个");
  });

  it("keeps mutating previews free of data-directory side effects", {
    timeout: 20_000,
  }, async () => {
    // `test` runs `tsup` first, so the bundle is always built here. A missing
    // bundle is a real failure, not something to skip: skipping is what let the
    // release gate run against a stale artifact unseen.
    expect(
      existsSync(bundle),
      "run `pnpm --filter @one-memory/cli build`",
    ).toBe(true);
    const root = await mkdtemp(path.join(os.tmpdir(), "memhub-preview-"));
    const home = path.join(root, "home");
    const dataDir = path.join(root, "data");
    await import("node:fs/promises").then(({ mkdir }) =>
      Promise.all([
        mkdir(home, { recursive: true, mode: 0o700 }),
        mkdir(dataDir, { recursive: true, mode: 0o700 }),
      ]),
    );
    try {
      const before = (await readdir(dataDir)).sort();
      execFileSync(
        process.execPath,
        [
          bundle,
          "service-install",
          "--install-root",
          path.join(root, "install"),
          "--home",
          home,
          "--data-dir",
          dataDir,
          "--json",
        ],
        {
          env: { ...process.env, HOME: home, ONE_MEMORY_DATA_DIR: dataDir },
          stdio: "pipe",
        },
      );
      expect((await readdir(dataDir)).sort()).toEqual(before);
    } finally {
      await rm(root, { recursive: true, force: true });
    }
  });

  it("keeps destructive previews free of diagnostic side effects", {
    timeout: 20_000,
  }, async () => {
    expect(
      existsSync(bundle),
      "run `pnpm --filter @one-memory/cli build`",
    ).toBe(true);
    const root = await mkdtemp(path.join(os.tmpdir(), "memhub-plan-preview-"));
    const home = path.join(root, "home");
    await import("node:fs/promises").then(({ mkdir }) =>
      mkdir(home, { recursive: true, mode: 0o700 }),
    );
    try {
      for (const [index, argv] of [
        ["memories", "delete", "m-1"],
        ["models", "remove", "44444444-4444-4444-8444-444444444444"],
        ["backup", "restore", "--source", path.join(root, "backup.sqlite3")],
        ["graph-width", "--decision", "rebuild_with_new"],
      ].entries()) {
        const dataDir = path.join(root, `data-${index}`);
        await import("node:fs/promises").then(({ mkdir }) =>
          mkdir(dataDir, { recursive: true, mode: 0o700 }),
        );
        try {
          execFileSync(process.execPath, [bundle, ...argv, "--json"], {
            env: { ...process.env, HOME: home, ONE_MEMORY_DATA_DIR: dataDir },
            stdio: "pipe",
          });
        } catch {
          // A failure path is acceptable; the guard is that no preview adds
          // a diagnostic file.
        }
        expect(await readdir(dataDir)).toEqual([]);
      }
    } finally {
      await rm(root, { recursive: true, force: true });
    }
  });
});
