// The rules behind the commands that attach to the Runtime directly.
//
// The parsers are where the contract actually lives — "only irreversible
// operations ask for --yes" and "unknown flags are rejected" are decisions,
// not formatting — so they are tested directly rather than only through the
// CLI.

import { describe, expect, it } from "vitest";

import { parseOpenAIMessages } from "./core-client.js";
import {
  parseBackupCommand,
  parseScanCommand,
  parseScanRootsCommand,
  parseTaskCommand,
  parseWriteCommand,
  restoreConfirmationMatches,
} from "./product-flows.js";

describe("scan-roots", () => {
  it("reads the root, source and recursive flag off add", () => {
    expect(
      parseScanRootsCommand([
        "add",
        "--root",
        "/tmp/x",
        "--source",
        "codex",
        "--no-recursive",
      ]),
    ).toEqual({
      kind: "add",
      root: "/tmp/x",
      source: "codex",
      recursive: false,
    });
    expect(parseScanRootsCommand(["add", "--root", "/tmp/x"])).toEqual({
      kind: "add",
      root: "/tmp/x",
      recursive: true,
    });
  });

  it("distinguishes remove from add and refuses add-only flags on remove", () => {
    expect(parseScanRootsCommand(["remove", "--root", "/tmp/x"])).toEqual({
      kind: "remove",
      root: "/tmp/x",
    });
    expect(
      parseScanRootsCommand(["remove", "--root", "/tmp/x", "--no-recursive"]),
    ).toBeNull();
    expect(
      parseScanRootsCommand([
        "remove",
        "--root",
        "/tmp/x",
        "--source",
        "codex",
      ]),
    ).toBeNull();
  });

  it("is direct, but keeps accepting a legacy --yes", () => {
    expect(parseScanRootsCommand(["add", "--root", "/tmp/x", "--yes"])).toEqual(
      { kind: "add", root: "/tmp/x", recursive: true },
    );
  });

  it("rejects unknown verbs, typos and repeated flags", () => {
    expect(parseScanRootsCommand(["create", "--root", "/tmp/x"])).toBeNull();
    expect(parseScanRootsCommand(["add", "--rot", "/tmp/x"])).toBeNull();
    expect(parseScanRootsCommand(["add"])).toBeNull();
    expect(
      parseScanRootsCommand(["add", "--root", "/a", "--root", "/b"]),
    ).toBeNull();
    expect(
      parseScanRootsCommand(["add", "--root", "/a", "--yes", "--yes"]),
    ).toBeNull();
  });
});

describe("scan", () => {
  it("needs a source: there is no endpoint that scans a bare path", () => {
    expect(parseScanCommand(["--source", "codex"])).toEqual({
      kind: "source",
      sourceId: "codex",
    });
    expect(parseScanCommand([])).toBeNull();
    expect(parseScanCommand(["--root", "/tmp/a"])).toBeNull();
  });

  it("no longer accepts --full, which was never sent to Core", () => {
    expect(parseScanCommand(["--source", "codex", "--full"])).toBeNull();
  });

  it("keeps --all as a hidden alias and a legacy --yes", () => {
    expect(parseScanCommand(["--all", "--yes"])).toEqual({ kind: "all" });
    expect(parseScanCommand(["--all", "--source", "codex"])).toBeNull();
    expect(parseScanCommand(["--source", "codex", "--yes"])).toEqual({
      kind: "source",
      sourceId: "codex",
    });
  });

  it("rejects a verb, a bad source id and repeated flags", () => {
    expect(parseScanCommand(["all"])).toBeNull();
    expect(parseScanCommand(["--source", "Bad Id"])).toBeNull();
    expect(
      parseScanCommand(["--source", "codex", "--source", "cursor"]),
    ).toBeNull();
    expect(
      parseScanCommand(["--source", "codex", "--json", "--json"]),
    ).toBeNull();
  });
});

describe("backup", () => {
  it("creates directly and requires a target", () => {
    expect(parseBackupCommand(["create", "--target", "/tmp/b"])).toEqual({
      verb: "create",
      target: "/tmp/b",
    });
    expect(parseBackupCommand(["create"])).toBeNull();
    expect(
      parseBackupCommand(["create", "--target", "/tmp/b", "--source", "/x"]),
    ).toBeNull();
  });

  it("restores only with --yes and requires a source", () => {
    expect(parseBackupCommand(["restore", "--source", "/tmp/b"])).toEqual({
      verb: "restore",
      source: "/tmp/b",
      confirmed: false,
    });
    expect(
      parseBackupCommand(["restore", "--source", "/tmp/b", "--yes"]),
    ).toMatchObject({ confirmed: true });
    expect(parseBackupCommand(["restore", "--yes"])).toBeNull();
  });

  it("accepts the legacy --confirm <path> and checks it lexically", () => {
    const parsed = parseBackupCommand([
      "restore",
      "--source",
      "/tmp/b",
      "--yes",
      "--confirm",
      "/tmp/b",
    ]);
    expect(parsed).toMatchObject({ confirmPath: "/tmp/b" });
    expect(restoreConfirmationMatches("/tmp/b", "/tmp/b")).toBe(true);
    expect(restoreConfirmationMatches("/tmp/b", undefined)).toBe(true);
    expect(restoreConfirmationMatches("/tmp/b", "/tmp/./b")).toBe(false);
  });

  it("rejects an unknown verb", () => {
    expect(parseBackupCommand(["delete", "--target", "/tmp/b"])).toBeNull();
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

  it("parses status under write, positional or --id", () => {
    expect(parseWriteCommand(["status", "--id", "request-1"])).toEqual({
      kind: "status",
      id: "request-1",
    });
    expect(parseWriteCommand(["status", "request-1"])).toEqual({
      kind: "status",
      id: "request-1",
    });
  });

  it("rejects a repeated --session instead of letting the last one win", () => {
    expect(parseWriteCommand([...base, "--session", "s2"])).toBeNull();
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
