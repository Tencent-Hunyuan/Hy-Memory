import { mkdtemp, readdir, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { inflateRawSync } from "node:zlib";
import { DiagnosticLogger } from "@one-memory/runtime-control";
import { afterEach, expect, it, vi } from "vitest";

import { run } from "./cli.js";

const roots: string[] = [];
afterEach(async () => {
  vi.restoreAllMocks();
  await Promise.all(
    roots.splice(0).map((root) => rm(root, { recursive: true })),
  );
});

async function fixture() {
  const dataDir = await mkdtemp(path.join(os.tmpdir(), "om-cli-command-log-"));
  roots.push(dataDir);
  const logger = new DiagnosticLogger({ dataDir, service: "runtime" });
  await logger.log({
    level: "error",
    component: "runtime.core",
    event: "core.start_failed",
    outcome: "failed",
    trace_id: "11111111-1111-4111-8111-111111111111",
    error_code: "core_start_failed",
  });
  return dataDir;
}

function readZipEntry(archive: Buffer, expected: string): string {
  let offset = 0;
  while (archive.readUInt32LE(offset) === 0x04034b50) {
    const method = archive.readUInt16LE(offset + 8);
    const compressedSize = archive.readUInt32LE(offset + 18);
    const nameLength = archive.readUInt16LE(offset + 26);
    const extraLength = archive.readUInt16LE(offset + 28);
    const nameStart = offset + 30;
    const dataStart = nameStart + nameLength + extraLength;
    const name = archive.subarray(nameStart, nameStart + nameLength).toString();
    const compressed = archive.subarray(dataStart, dataStart + compressedSize);
    if (name === expected) {
      if (method === 0) return compressed.toString();
      if (method === 8) return inflateRawSync(compressed).toString();
      throw new Error("unsupported zip compression");
    }
    offset = dataStart + compressedSize;
  }
  throw new Error(`missing ${expected}`);
}

it("prints matching diagnostic events as NDJSON without starting Runtime", async () => {
  const dataDir = await fixture();
  let stdout = "";
  vi.spyOn(process.stdout, "write").mockImplementation((value) => {
    stdout += String(value);
    return true;
  });
  expect(
    await run(
      ["logs", "--trace", "11111111-1111-4111-8111-111111111111", "--json"],
      { ONE_MEMORY_DATA_DIR: dataDir },
    ),
  ).toBe(0);
  expect(JSON.parse(stdout)).toMatchObject({
    event: "core.start_failed",
    error_code: "core_start_failed",
  });
});

it("exports one support zip to the requested downloads directory", async () => {
  const dataDir = await fixture();
  const downloads = await mkdtemp(path.join(os.tmpdir(), "om-cli-downloads-"));
  roots.push(downloads);
  let stdout = "";
  vi.spyOn(process.stdout, "write").mockImplementation((value) => {
    stdout += String(value);
    return true;
  });

  expect(
    await run(["logs", "export", "--json"], {
      ONE_MEMORY_DATA_DIR: dataDir,
      ONE_MEMORY_EXPORT_DIR: downloads,
      ONE_MEMORY_BUILD_NUMBER: "3800",
    }),
  ).toBe(0);

  const receipt = JSON.parse(stdout);
  expect(receipt).toMatchObject({
    schema_version: 1,
    command: "logs export",
    state: "exported",
  });
  expect(receipt.path).toMatch(/MemHub-日志-3800-\d{8}-\d{4}\.zip$/);
  expect(await readdir(downloads)).toEqual([path.basename(receipt.path)]);
  const archive = await readFile(receipt.path);
  expect(JSON.parse(readZipEntry(archive, "summary.json")).commit).toBeNull();
});

it("fails closed for an explicitly supplied invalid commit", async () => {
  const dataDir = await fixture();
  const downloads = await mkdtemp(path.join(os.tmpdir(), "om-cli-downloads-"));
  roots.push(downloads);
  vi.spyOn(process.stdout, "write").mockImplementation(() => true);

  await expect(
    run(["logs", "export", "--json"], {
      ONE_MEMORY_DATA_DIR: dataDir,
      ONE_MEMORY_EXPORT_DIR: downloads,
      ONE_MEMORY_BUILD_NUMBER: "3800",
      ONE_MEMORY_COMMIT: "unknown",
    }),
  ).rejects.toThrow("diagnostic_commit_invalid");
  expect(await readdir(downloads)).toEqual([]);
});

it("never emits a secret from a malformed manually written event", async () => {
  const dataDir = await mkdtemp(path.join(os.tmpdir(), "om-cli-redact-"));
  roots.push(dataDir);
  await import("node:fs/promises").then(({ mkdir }) =>
    mkdir(path.join(dataDir, "logs"), { recursive: true, mode: 0o700 }),
  );
  const event = {
    schema_version: 1,
    timestamp: "2026-09-24T02:00:00.000Z",
    level: "error",
    service: "runtime",
    component: "runtime.core",
    event: "core.failed",
    outcome: "failed",
    session_id: "11111111-1111-4111-8111-111111111111",
    details: {
      count: 1,
      prompt: "secret-token-123",
      authorization: "Bearer secret-token-123",
    },
    safe_stack: ["/Users/me/private.py:1"],
  };
  await writeFile(
    path.join(dataDir, "logs", "diagnostic-runtime.jsonl"),
    `${JSON.stringify(event)}\n`,
    { mode: 0o600 },
  );
  let stdout = "";
  vi.spyOn(process.stdout, "write").mockImplementation((value) => {
    stdout += String(value);
    return true;
  });
  expect(await run(["logs", "--json"], { ONE_MEMORY_DATA_DIR: dataDir })).toBe(
    0,
  );
  expect(stdout).not.toContain("secret-token-123");
  expect(stdout).not.toContain("/Users/me");
  // The read layer rejects the whole malformed line, but success must still
  // produce one parseable status record rather than silent stdout.
  expect(JSON.parse(stdout)).toMatchObject({
    schema_version: 1,
    product: "memhub",
    command: "logs",
    state: "empty",
    events: 0,
    invalid_lines: 1,
  });
});

it("rejects invalid filters without touching the log directory", async () => {
  const dataDir = path.join(
    await mkdtemp(path.join(os.tmpdir(), "om-cli-no-log-")),
    "data",
  );
  roots.push(path.dirname(dataDir));
  vi.spyOn(process.stderr, "write").mockImplementation(() => true);
  expect(
    await run(["logs", "--tail", "5001"], { ONE_MEMORY_DATA_DIR: dataDir }),
  ).toBe(2);
});

// A `logs` path that exists but is not a readable directory used to yield
// exit 0 with zero output — indistinguishable from "no matching events". A
// script cannot tell a broken install from a quiet one, which violates the
// stable-JSON/exit-code contract.
it("reports an unusable log directory instead of silently returning nothing", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "om-cli-badlogs-"));
  roots.push(root);
  // `logs` exists, but as a regular file.
  await writeFile(path.join(root, "logs"), "not a directory");
  let stdout = "";
  let stderr = "";
  vi.spyOn(process.stdout, "write").mockImplementation((value) => {
    stdout += String(value);
    return true;
  });
  vi.spyOn(process.stderr, "write").mockImplementation((value) => {
    stderr += String(value);
    return true;
  });
  expect(await run(["logs", "--json"], { ONE_MEMORY_DATA_DIR: root })).toBe(1);
  expect(stdout).toBe("");
  expect(JSON.parse(stderr)).toMatchObject({
    schema_version: 1,
    product: "memhub",
    command: "logs",
    status: "error",
    code: "log_directory_unreadable",
    exit_code: 1,
  });
});
