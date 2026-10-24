import { readFile } from "node:fs/promises";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { run } from "./cli.js";
// Aliased: this file already declares a local `COMMANDS` (a list of command
// names). Importing under the same name silently shadowed it, and the test
// then searched a list of strings for a `name` property instead of failing.
import { COMMANDS as COMMAND_DESCRIPTORS } from "./command-catalog.js";
import { PRODUCT } from "./version.js";

const packageVersion = (
  JSON.parse(
    await readFile(new URL("../package.json", import.meta.url), "utf8"),
  ) as { version: string }
).version;

const sideEffects = vi.hoisted(() => ({
  runRuntimeCli: vi.fn(() => {
    throw new Error("runtime was executed");
  }),
  ensureRuntimeProcess: vi.fn(() => {
    throw new Error("runtime was executed");
  }),
  stopRuntimeProcess: vi.fn(() => {
    throw new Error("runtime was executed");
  }),
  inspectRuntimeStatus: vi.fn(() => {
    throw new Error("runtime was executed");
  }),
  planLocalInstall: vi.fn(() => {
    throw new Error("runtime was executed");
  }),
  applyLocalInstall: vi.fn(() => {
    throw new Error("runtime was executed");
  }),
  planCliLink: vi.fn(() => {
    throw new Error("runtime was executed");
  }),
  installCliLink: vi.fn(() => {
    throw new Error("runtime was executed");
  }),
  installUserService: vi.fn(() => {
    throw new Error("runtime was executed");
  }),
  uninstallUserService: vi.fn(() => {
    throw new Error("runtime was executed");
  }),
  restartUserService: vi.fn(() => {
    throw new Error("runtime was executed");
  }),
}));

vi.mock("@one-memory/runtime-control", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@one-memory/runtime-control")>()),
  ...sideEffects,
}));

// Without this, "was never called" would silently mean "was called in an
// earlier test", which is exactly the kind of guard that only looks green.
beforeEach(() => {
  vi.clearAllMocks();
});

const COMMANDS = [
  "serve",
  "start",
  "stop",
  "restart",
  "status",
  "doctor",
  "logs",
  "init",
  "setup",
  "sources",
  "sync",
  "progress",
  "jobs",
  "models",
  "memories",
  "profile",
  // C6 App BLOCK (25f4ecf): UI write operations that had no CLI equivalent.
  "calls",
  "notes",
  "graph",
  "host",
  "recall",
  "service-plan",
  "service-install",
  "service-uninstall",
  "install",
  "uninstall",
  "upgrade",
  "delete-data",
  "onboarding",
  "help",
  "version",
  // A6 product commands. Appended, not inserted: the catalog is a stable
  // contract, so existing names must not shift position.
  "path",
  "adapter",
  "scan",
  "write",
  "task",
  "backup",
  // `ui` is declared but blocked on a Core session contract; it is listed so
  // `help` documents it and the blocked shape is machine-readable.
  "ui",
] as const;

describe("CLI-22 shared output contract", () => {
  describe("help", () => {
    it.each([["help"], ["--help"], ["-h"]])(
      "%s lists every command on stdout and exits 0",
      async (flag) => {
        const streams = captureStreams();
        try {
          expect(await run([flag], {})).toBe(0);
        } finally {
          streams.restore();
        }
        for (const command of COMMANDS) {
          expect(streams.stdout()).toContain(command);
        }
        expect(streams.stderr()).toBe("");
      },
    );

    it.each(COMMANDS)(
      "documents %s the same way through `help <command>` and `<command> --help`",
      async (command) => {
        const viaHelp = captureStreams();
        try {
          expect(await run(["help", command], {})).toBe(0);
        } finally {
          viaHelp.restore();
        }
        const viaFlag = captureStreams();
        try {
          expect(await run([command, "--help"], {})).toBe(0);
        } finally {
          viaFlag.restore();
        }
        expect(viaHelp.stdout()).toContain(`${PRODUCT} ${command}`);
        expect(viaHelp.stdout()).toBe(viaFlag.stdout());
        expect(viaHelp.stderr()).toBe("");
        expect(viaFlag.stderr()).toBe("");
      },
    );

    it("never executes the documented command", async () => {
      for (const command of COMMANDS) {
        for (const argv of [
          ["help", command],
          [command, "--help"],
        ]) {
          const streams = captureStreams();
          try {
            expect(await run(argv, {})).toBe(0);
          } finally {
            streams.restore();
          }
        }
      }
      for (const effect of Object.values(sideEffects)) {
        expect(effect).not.toHaveBeenCalled();
      }
    });

    it("emits a machine-readable catalog with stable exit codes", async () => {
      const streams = captureStreams();
      try {
        expect(await run(["help", "--json"], {})).toBe(0);
      } finally {
        streams.restore();
      }
      const catalog = JSON.parse(streams.stdout());
      expect(catalog.schema_version).toBe(1);
      expect(catalog.product).toBe(PRODUCT);
      expect(catalog.cli_version).toBe(packageVersion);
      expect(
        catalog.commands.map((entry: { name: string }) => entry.name),
      ).toEqual([...COMMANDS]);
      for (const entry of catalog.commands) {
        expect(entry.summary.length).toBeGreaterThan(0);
        expect(entry.usage.startsWith(`${PRODUCT} ${entry.name}`)).toBe(true);
      }
      expect(
        catalog.exit_codes.map((entry: { code: number }) => entry.code),
      ).toEqual([0, 1, 2, 3, 4]);
    });

    it("emits a machine-readable entry for a single command", async () => {
      const streams = captureStreams();
      try {
        expect(await run(["help", "status", "--json"], {})).toBe(0);
      } finally {
        streams.restore();
      }
      const payload = JSON.parse(streams.stdout());
      expect(payload.command.name).toBe("status");
      expect(payload.command.usage).toContain("--json");
      expect(payload.commands).toBeUndefined();
    });

    it("documents the destructive restore confirmation explicitly", async () => {
      const streams = captureStreams();
      try {
        expect(await run(["help", "backup"], {})).toBe(0);
      } finally {
        streams.restore();
      }
      expect(streams.stdout()).toContain(
        "memhub backup restore --source <path> --yes --confirm <path>",
      );
      expect(streams.stdout()).toContain("逐字重复 --source");
    });

    it("does not attach restore's confirmation flag to path", async () => {
      const streams = captureStreams();
      try {
        expect(await run(["help", "path"], {})).toBe(0);
      } finally {
        streams.restore();
      }
      expect(streams.stdout()).not.toContain("--confirm <path>");
    });
  });

  describe("version", () => {
    it.each([["version"], ["--version"], ["-V"]])(
      "%s reports the packaged CLI version on stdout",
      async (flag) => {
        const streams = captureStreams();
        try {
          expect(await run([flag], {})).toBe(0);
        } finally {
          streams.restore();
        }
        expect(streams.stdout()).toBe(`${PRODUCT} ${packageVersion}\n`);
        expect(streams.stderr()).toBe("");
      },
    );

    it("reports the same version in machine-readable form", async () => {
      const streams = captureStreams();
      try {
        expect(await run(["version", "--json"], {})).toBe(0);
      } finally {
        streams.restore();
      }
      expect(JSON.parse(streams.stdout())).toEqual({
        schema_version: 1,
        product: PRODUCT,
        cli_version: packageVersion,
      });
    });
  });

  describe("argument validation", () => {
    it("fails unknown commands without printing data or running anything", async () => {
      for (const argv of [
        ["unknown"],
        ["unknown", "--help"],
        ["--typo"],
        ["permission", "status"],
        ["permission", "allow"],
        ["permission", "deny"],
      ]) {
        const streams = captureStreams();
        try {
          expect(await run(argv, {})).toBe(2);
        } finally {
          streams.restore();
        }
        expect(streams.stdout()).toBe("");
        expect(streams.stderr()).toContain(`${PRODUCT} help`);
      }
      for (const effect of Object.values(sideEffects)) {
        expect(effect).not.toHaveBeenCalled();
      }
    });

    it("answers a rejected invocation with that command's own usage", async () => {
      const cases: Array<[readonly string[], string]> = [
        [["status", "--typo"], `${PRODUCT} status`],
        [["start", "extra"], `${PRODUCT} start`],
        // `--json` is now a valid flag for stop (stable JSON on every declared
        // command); an unknown flag is still rejected.
        [["stop", "--typo"], `${PRODUCT} stop`],
        [["service-plan", "--home", "/tmp/home"], `${PRODUCT} service-plan`],
        [["install", "--source"], `${PRODUCT} install`],
        [
          [
            "install",
            "--source",
            "/tmp/dist",
            "--release-manifest-url",
            "https://downloads.example.test/release.json",
          ],
          `${PRODUCT} install`,
        ],
        [["upgrade", "--yes", "--yes"], `${PRODUCT} upgrade`],
        [["version", "extra"], `${PRODUCT} version`],
        [
          ["sync", "apply", "--plan-file", "/tmp/plan", "--confirm"],
          `${PRODUCT} sync`,
        ],
        [
          ["sync", "receipt", "123e4567-e89b-12d3-a456-426614174000"],
          `${PRODUCT} sync`,
        ],
        [["sync", "pause", "--source"], `${PRODUCT} sync`],
      ];
      for (const [argv, expected] of cases) {
        const streams = captureStreams();
        try {
          expect(await run(argv, {})).toBe(2);
        } finally {
          streams.restore();
        }
        expect(streams.stdout()).toBe("");
        expect(streams.stderr()).toContain(expected);
      }
    });

    it("reports rejected invocations as machine-readable errors on stderr", async () => {
      const streams = captureStreams();
      try {
        expect(await run(["status", "--typo", "--json"], {})).toBe(2);
      } finally {
        streams.restore();
      }
      expect(streams.stdout()).toBe("");
      const error = JSON.parse(streams.stderr());
      expect(error).toMatchObject({
        schema_version: 1,
        status: "usage_error",
        command: "status",
        exit_code: 2,
      });
      expect(error.next_step.length).toBeGreaterThan(0);
    });
  });

  // Main's ARGUMENT-REVIEW (2026-09-19) reproduced both gaps below against the
  // real run(): serve reached the Runtime with junk arguments, and a help token
  // anywhere won, including where an option value was missing.
  describe("strict argument and help parsing", () => {
    it("rejects invalid serve arguments before the Runtime is reachable", async () => {
      for (const argv of [
        ["serve", "--typo"],
        ["serve", "extra"],
        ["serve", "--json"],
        ["serve", "--json", "--json"],
        ["serve", "--help", "extra"],
        ["serve", "--install-root", "/tmp/install"],
      ]) {
        const streams = captureStreams();
        try {
          expect(await run(argv, {})).toBe(2);
        } finally {
          streams.restore();
        }
        expect(streams.stdout()).toBe("");
        expect(streams.stderr()).toContain(`${PRODUCT} serve`);
      }
      for (const effect of Object.values(sideEffects)) {
        expect(effect).not.toHaveBeenCalled();
      }
    });

    it("still runs a bare serve and still explains serve --help", async () => {
      // Positive control: without it, the rejection test above proves nothing.
      sideEffects.runRuntimeCli.mockImplementationOnce(() => 0 as never);
      const served = captureStreams();
      try {
        expect(await run(["serve"], {})).toBe(0);
      } finally {
        served.restore();
      }
      expect(sideEffects.runRuntimeCli).toHaveBeenCalledWith(["serve"], {});

      const helped = captureStreams();
      try {
        expect(await run(["serve", "--help"], {})).toBe(0);
      } finally {
        helped.restore();
      }
      expect(helped.stdout()).toContain(`${PRODUCT} serve`);
      expect(sideEffects.runRuntimeCli).toHaveBeenCalledTimes(1);
    });

    it("refuses a help token standing in for a missing option value", async () => {
      for (const argv of [
        ["service-plan", "--install-root", "/tmp/install", "--uid", "--help"],
        ["service-plan", "--install-root", "--help"],
        ["service-plan", "--install-root", "-h"],
        ["install", "--source", "--help"],
        ["memories", "list", "--query", "--help"],
      ]) {
        const streams = captureStreams();
        try {
          expect(await run(argv, {})).toBe(2);
        } finally {
          streams.restore();
        }
        expect(streams.stdout()).toBe("");
      }
      for (const effect of Object.values(sideEffects)) {
        expect(effect).not.toHaveBeenCalled();
      }
    });

    it("does not let a help token mask an otherwise invalid invocation", async () => {
      for (const argv of [
        ["status", "--typo", "--help"],
        ["status", "--help", "--typo"],
        ["version", "--json", "--json", "--help"],
        ["start", "extra", "-h"],
        ["help", "install", "--help"],
        ["--help", "install", "extra"],
      ]) {
        const streams = captureStreams();
        try {
          expect(await run(argv, {})).toBe(2);
        } finally {
          streams.restore();
        }
        expect(streams.stdout()).toBe("");
      }
      for (const effect of Object.values(sideEffects)) {
        expect(effect).not.toHaveBeenCalled();
      }
    });

    it("keeps the documented help forms working", async () => {
      for (const argv of [
        ["--help"],
        ["-h"],
        ["help"],
        ["help", "install"],
        ["help", "--json"],
        ["install", "--help"],
        ["install", "--help", "--json"],
        ["install", "--json", "--help"],
        ["memories", "-h"],
      ]) {
        const streams = captureStreams();
        try {
          expect(await run(argv, {})).toBe(0);
        } finally {
          streams.restore();
        }
        expect(streams.stdout().length).toBeGreaterThan(0);
        expect(streams.stderr()).toBe("");
      }
      for (const effect of Object.values(sideEffects)) {
        expect(effect).not.toHaveBeenCalled();
      }
    });
  });

  describe("agent-safe output", () => {
    it("keeps colors and animation out of every machine-readable payload", async () => {
      for (const argv of [
        ["help", "--json"],
        ["help", "install", "--json"],
        ["version", "--json"],
        ["status", "--typo", "--json"],
      ]) {
        const streams = captureStreams();
        try {
          await run(argv, {});
        } finally {
          streams.restore();
        }
        const emitted = `${streams.stdout()}${streams.stderr()}`;
        expect(emitted.includes("\u001B")).toBe(false);
        expect(emitted.includes("\r")).toBe(false);
      }
    });

    it("never reads stdin, so a piped or non-TTY invocation cannot hang", async () => {
      const spies = [
        vi.spyOn(process.stdin, "read"),
        vi.spyOn(process.stdin, "resume"),
        vi.spyOn(process.stdin, "on"),
        vi.spyOn(process.stdin, "once"),
        vi.spyOn(process.stdin, "setEncoding"),
      ];
      let touched = 0;
      try {
        for (const argv of [
          ["help"],
          ["version"],
          ["help", "install"],
          ["unknown"],
          ["status", "--typo"],
        ]) {
          const streams = captureStreams();
          try {
            await run(argv, {});
          } finally {
            streams.restore();
          }
        }
        touched = spies.reduce(
          (total, spy) => total + spy.mock.calls.length,
          0,
        );
      } finally {
        for (const spy of spies) spy.mockRestore();
      }
      expect(touched).toBe(0);
    });

    it("keeps credentials out of help, version and diagnostics", async () => {
      const environment = {
        ONE_MEMORY_API_TOKEN: "secret-token-value",
        ONE_MEMORY_DATA_DIR: "relative",
      };
      for (const argv of [
        ["help"],
        ["help", "--json"],
        ["version"],
        ["service-plan", "--install-root", "/tmp/install"],
        ["install", "--data-dir", "relative"],
        ["unknown"],
      ]) {
        const streams = captureStreams();
        try {
          await run(argv, environment);
        } finally {
          streams.restore();
        }
        expect(`${streams.stdout()}${streams.stderr()}`).not.toContain(
          "secret-token-value",
        );
      }
    });
  });
});

describe("documented commands", () => {
  const accepted: Record<string, string[]> = {
    models: [
      "list",
      "show",
      "plan",
      "configure",
      "receipt",
      "probe-plan",
      "probe",
      "probe-receipt",
      "remove",
    ],
    // Source processing is one Freeze/Thaw switch under `sync`; the obsolete
    // independent read/write `sources access` command must not return here.
    sources: ["list", "import", "receipt"],
    jobs: ["list", "show", "start", "<pause|resume|cancel>"],
    // `sync <pause|resume>` is documented as one alternation, so it is
    // checked as an entry rather than as two literal subcommands.
    sync: ["status", "<pause|resume>"],
    memories: ["list", "show", "update", "delete"],
    host: ["list", "status", "install", "uninstall", "sync"],
  };

  // `memhub models remove` parsed and ran for months while `models --help`
  // never mentioned it, so nobody could discover it and no test noticed. The
  // usage string is the only surface users and Agents read, so it has to stay
  // in step with the parser.
  // A subcommand the parser accepts but the usage text omits is invisible:
  it("lists every subcommand the parser accepts in that command's usage", () => {
    for (const [name, subcommands] of Object.entries(accepted)) {
      const descriptor = COMMAND_DESCRIPTORS.find(
        (entry) => entry.name === name,
      );
      expect(descriptor, `${name} is not in the catalog`).toBeDefined();
      // Both surfaces count: commands may document plan/apply variants under
      // `alternative_usage`, printed directly below the primary usage line.
      const documented = `${descriptor?.usage ?? ""}\n${descriptor?.alternative_usage ?? ""}`;
      for (const subcommand of subcommands) {
        expect(
          documented.includes(`${PRODUCT} ${name} ${subcommand}`),
          `${name} ${subcommand} is parsed but never documented`,
        ).toBe(true);
      }
    }
  });

  it("gives every command in the catalog a usage line of its own", () => {
    // The other direction of the same guard: a command could satisfy the
    // checks above by having no usage text at all, which is what "invisible"
    // looks like from the inside. Each catalog entry must start with its own
    // name. Length is not asserted: `serve`, `start`, `stop` and `restart`
    // take no arguments, so their usage is legitimately just the invocation.
    for (const descriptor of COMMAND_DESCRIPTORS) {
      expect(
        descriptor.usage.startsWith(`${PRODUCT} ${descriptor.name}`),
        `${descriptor.name} usage does not start with its own invocation`,
      ).toBe(true);
    }
  });
});

function captureStreams() {
  let out = "";
  let error = "";
  const originalOut = process.stdout.write;
  const originalError = process.stderr.write;
  process.stdout.write = ((chunk: string) => {
    out += chunk;
    return true;
  }) as typeof process.stdout.write;
  process.stderr.write = ((chunk: string) => {
    error += chunk;
    return true;
  }) as typeof process.stderr.write;
  return {
    stdout: () => out,
    stderr: () => error,
    restore: () => {
      process.stdout.write = originalOut;
      process.stderr.write = originalError;
    },
  };
}
