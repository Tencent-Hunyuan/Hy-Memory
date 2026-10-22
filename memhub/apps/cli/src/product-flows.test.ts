// A6: the rules behind the six product commands.
//
// The parsers are where the contract actually lives — "needs --yes to change
// anything" and "no write without evidence" are decisions, not formatting — so
// they are tested directly rather than only through the CLI.

import { describe, expect, it } from "vitest";

import { parseOpenAIMessages } from "./core-client.js";
import {
  asPlanDocument,
  describe as describePlan,
  parseProductCommand,
  parseTaskCommand,
  parseWriteCommand,
  restoreConfirmationMatches,
} from "./product-flows.js";

describe("mutating commands are two-phase", () => {
  it("plans without --yes", () => {
    const parsed = parseProductCommand("scan", ["--root", "/tmp/a"]);
    expect(parsed?.confirmed).toBe(false);
  });

  it("confirms with --yes", () => {
    const parsed = parseProductCommand("scan", ["--root", "/tmp/a", "--yes"]);
    expect(parsed?.confirmed).toBe(true);
  });

  it("says what it would do", () => {
    const parsed = parseProductCommand("scan", ["--root", "/tmp/a"]);
    // `?.plan!` would let a null parse pass silently; assert the parse first.
    if (parsed === null) {
      throw new Error("scan with a root must parse");
    }
    expect(describePlan(parsed.plan)).toContain("scan: /tmp/a");
  });

  it("marks a plan as not applied", () => {
    // Assert the parse itself: `?.` let a null result satisfy this test for as
    // long as `backup create` was unparseable.
    const parsed = parseProductCommand("backup", [
      "create",
      "--target",
      "/tmp/b",
    ]);
    if (parsed === null) {
      throw new Error("backup create must parse");
    }
    expect(parsed.plan.verb).toBe("create");
    expect(asPlanDocument(parsed.plan).applied).toBe(false);
  });

  it("parses the verb for every command that takes one", () => {
    // `path add` / `path remove` / `backup create` were rejected outright
    // before the verb was skipped in parsing.
    expect(
      parseProductCommand("path", ["add", "--root", "/tmp/x"])?.plan.action,
    ).toBe("add scan root");
    expect(
      parseProductCommand("path", ["remove", "--root", "/tmp/x"])?.plan.action,
    ).toBe("remove scan root");
    expect(
      parseProductCommand("backup", ["restore", "--source", "/tmp/b"])?.plan
        .action,
    ).toBe("restore backup");
  });

  it("defaults a scan to all registered roots", () => {
    const parsed = parseProductCommand("scan", []);
    expect(parsed?.plan.target).toBe("all registered roots");
  });
});

describe("unknown flags are rejected", () => {
  it("rejects a typo'd flag rather than ignoring it", () => {
    // A silently ignored --yes would turn a real apply into a plan, and the
    // user would believe the change had been made.
    expect(parseProductCommand("scan", ["--yesx"])).toBeNull();
    expect(parseProductCommand("scan", ["--root"])).toBeNull();
    expect(parseProductCommand("scan", ["--root", "--yes"])).toBeNull();
  });

  it("rejects an unknown verb for backup", () => {
    expect(
      parseProductCommand("backup", ["make", "--target", "/tmp/b"]),
    ).toBeNull();
  });

  // `path` and `scan` share one parser. Before this guard, an unknown verb was
  // silently treated as `path add` or a default `scan`, so `--yes` executed a
  // different operation than the one the caller named.
  it("rejects an unknown verb for path without turning it into add", () => {
    expect(
      parseProductCommand("path", ["frobnicate", "--root", "/tmp/x", "--yes"]),
    ).toBeNull();
  });

  it("rejects an unknown verb for scan without turning it into all", () => {
    expect(
      parseProductCommand("scan", ["frobnicate", "--all", "--yes"]),
    ).toBeNull();
    expect(parseProductCommand("scan", ["frobnicate", "--yes"])).toBeNull();
  });

  it("still accepts the documented verbless scan", () => {
    expect(parseProductCommand("scan", ["--all", "--yes"])?.plan.all).toBe(
      true,
    );
    expect(
      parseProductCommand("scan", ["--source", "codex"])?.plan.sourceId,
    ).toBe("codex");
  });

  it("rejects flags that the named verb does not consume", () => {
    // These were accepted and then silently dropped. An operator who typed
    // `path remove --source codex` would believe the source filter applied.
    expect(
      parseProductCommand("path", [
        "remove",
        "--root",
        "/tmp/x",
        "--source",
        "codex",
      ]),
    ).toBeNull();
    expect(
      parseProductCommand("path", [
        "remove",
        "--root",
        "/tmp/x",
        "--no-recursive",
      ]),
    ).toBeNull();
    expect(
      parseProductCommand("path", ["add", "--root", "/tmp/x", "--full"]),
    ).toBeNull();
    expect(
      parseProductCommand("backup", [
        "create",
        "--target",
        "/tmp/b",
        "--source",
        "/tmp/s",
      ]),
    ).toBeNull();
    expect(
      parseProductCommand("backup", [
        "restore",
        "--source",
        "/tmp/s",
        "--target",
        "/tmp/t",
      ]),
    ).toBeNull();
  });

  // A later value silently won before this guard. For `scan --source` that
  // means one extra shell/make variable can change which source is scanned
  // while the operator believes both were applied.
  it("rejects repeated value-bearing flags instead of letting the last one win", () => {
    expect(
      parseProductCommand("scan", ["--source", "codex", "--source", "cursor"]),
    ).toBeNull();
    expect(
      parseProductCommand("path", [
        "add",
        "--root",
        "/tmp/a",
        "--root",
        "/tmp/b",
      ]),
    ).toBeNull();
    expect(
      parseProductCommand("backup", [
        "create",
        "--target",
        "/tmp/a",
        "--target",
        "/tmp/b",
      ]),
    ).toBeNull();
  });

  it("rejects repeated boolean flags instead of treating them as idempotent", () => {
    expect(parseProductCommand("scan", ["--all", "--all", "--yes"])).toBeNull();
    expect(
      parseProductCommand("scan", ["--full", "--full", "--yes"]),
    ).toBeNull();
    expect(
      parseProductCommand("path", [
        "add",
        "--root",
        "/tmp/a",
        "--no-recursive",
        "--no-recursive",
      ]),
    ).toBeNull();
    expect(
      parseProductCommand("backup", [
        "create",
        "--target",
        "/tmp/a",
        "--yes",
        "--yes",
      ]),
    ).toBeNull();
  });

  it("rejects a repeated global --json spelling", () => {
    expect(
      parseProductCommand("scan", ["--root", "/tmp/a", "--json", "--json"]),
    ).toBeNull();
    expect(
      parseProductCommand("backup", [
        "create",
        "--target",
        "/tmp/a",
        "--json",
        "--json",
      ]),
    ).toBeNull();
  });

  it("requires a source when restoring", () => {
    expect(
      parseProductCommand("backup", ["restore", "--target", "/tmp/b"]),
    ).toBeNull();
  });

  it("parses the explicit restore confirmation path", () => {
    const parsed = parseProductCommand("backup", [
      "restore",
      "--source",
      "/tmp/backups/a.sqlite3",
      "--yes",
      "--confirm",
      "/tmp/backups/a.sqlite3",
    ]);
    expect(parsed).not.toBeNull();
    expect(parsed?.confirmPath).toBe("/tmp/backups/a.sqlite3");
    expect(
      restoreConfirmationMatches("/tmp/backups/a.sqlite3", parsed?.confirmPath),
    ).toBe(true);
    expect(
      restoreConfirmationMatches("/tmp/backups/a.sqlite3", undefined),
    ).toBe(false);
    expect(
      restoreConfirmationMatches(
        "/tmp/backups/a.sqlite3",
        "/tmp/backups/../backups/a.sqlite3",
      ),
    ).toBe(false);
  });

  it("requires a target when creating a backup", () => {
    expect(parseProductCommand("backup", ["create"])).toBeNull();
  });
});

describe("write", () => {
  const base = [
    "submit",
    "--session",
    "s1",
    "--messages-file",
    "/tmp/messages.json",
  ];

  it("accepts a well-formed write", () => {
    const parsed = parseWriteCommand(base);
    expect(parsed).toEqual({
      kind: "submit",
      session: "s1",
      source: "memhub-cli",
      messages_file: "/tmp/messages.json",
    });
  });

  it("rejects a write with no messages file", () => {
    expect(parseWriteCommand(["submit", "--session", "s1"])).toBeNull();
  });

  it("accepts source, request identity, and RFC3339 time", () => {
    expect(
      parseWriteCommand([
        ...base,
        "--source",
        "cursor",
        "--request-id",
        "request-1",
        "--memory-at",
        "2026-10-22T00:00:00+08:00",
      ]),
    ).toMatchObject({
      source: "cursor",
      request_id: "request-1",
      memory_at: "2026-10-22T00:00:00+08:00",
    });
  });

  it("rejects a timestamp without timezone", () => {
    expect(
      parseWriteCommand([...base, "--memory-at", "2026-10-22T00:00:00"]),
    ).toBeNull();
  });

  it("parses status under write", () => {
    expect(parseWriteCommand(["status", "--id", "request-1"])).toEqual({
      kind: "status",
      id: "request-1",
    });
  });

  it("marks --live as priority live", () => {
    expect(parseWriteCommand([...base, "--live"])).toMatchObject({
      kind: "submit",
      priority: "live",
    });
  });

  it("leaves priority unset by default", () => {
    expect(parseWriteCommand(base)).not.toHaveProperty("priority");
  });

  it("rejects a repeated --json", () => {
    expect(parseWriteCommand([...base, "--json", "--json"])).toBeNull();
  });

  it("validates OpenAI text messages and requires a user turn", () => {
    expect(
      parseOpenAIMessages({
        messages: [
          { role: "user", content: "Remember Tuesday." },
          { role: "assistant", content: "Understood.", ignored: true },
        ],
      }),
    ).toEqual([
      { role: "user", content: "Remember Tuesday." },
      { role: "assistant", content: "Understood." },
    ]);
    expect(
      parseOpenAIMessages([{ role: "assistant", content: "No user turn." }]),
    ).toBeNull();
    expect(
      parseOpenAIMessages([{ role: "system", content: "Hidden prompt." }]),
    ).toBeNull();
  });
});

describe("task", () => {
  it("reads the request id", () => {
    expect(parseTaskCommand(["--id", "req-1"])).toEqual({ id: "req-1" });
  });

  it("rejects a missing id", () => {
    expect(parseTaskCommand([])).toBeNull();
    expect(parseTaskCommand(["--id"])).toBeNull();
  });

  it("rejects an unknown flag", () => {
    expect(parseTaskCommand(["--request", "req-1"])).toBeNull();
  });

  it("rejects a repeated --json", () => {
    expect(parseTaskCommand(["--id", "req-1", "--json", "--json"])).toBeNull();
  });
});

describe("path add and remove carry what the endpoint needs", () => {
  it("reads the source and recursive flag off the path add", () => {
    const parsed = parseProductCommand("path", [
      "add",
      "--root",
      "/tmp/scanme",
      "--source",
      "codex",
      "--no-recursive",
    ]);
    // `?.plan!` let a null parse pass silently, so assert the parse itself.
    if (parsed === null) {
      throw new Error("path add must parse");
    }
    expect(parsed.plan.action).toBe("add scan root");
    expect(parsed.plan.target).toBe("/tmp/scanme");
    expect(parsed.plan.detail).toContain("source=codex");
    expect(parsed.plan.detail).toContain("recursive=false");
  });

  it("distinguishes remove from add", () => {
    const parsed = parseProductCommand("path", [
      "remove",
      "--root",
      "/tmp/scanme",
    ]);
    if (parsed === null) {
      throw new Error("path remove must parse");
    }
    // The action is the discriminator at apply time: add and remove share a
    // `--root`, so a mis-set action would register the directory it meant to delete.
    expect(parsed.plan.action).toBe("remove scan root");
  });

  it("does not silently drop --yes", () => {
    const parsed = parseProductCommand("path", [
      "add",
      "--root",
      "/tmp/a",
      "--yes",
    ]);
    expect(parsed?.confirmed).toBe(true);
    // Without this, a typo'd `--yes` would fall through to a plan and the user
    // would believe the change had been made.

    const without = parseProductCommand("path", ["add", "--root", "/tmp/a"]);
    expect(without?.confirmed).toBe(false);
  });
});
