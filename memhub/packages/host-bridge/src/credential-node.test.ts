import { lstat, mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import {
  assertPrivateCredentialNode,
  type CredentialNodeStats,
  UnsafeCredentialNodeError,
} from "./credential-node.js";

function fileNode(
  overrides: Partial<CredentialNodeStats> = {},
): CredentialNodeStats {
  return {
    isDirectory: () => false,
    isFile: () => true,
    isSymbolicLink: () => false,
    nlink: 1,
    uid: 501,
    mode: 0o100600,
    ...overrides,
  };
}

function directoryNode(
  overrides: Partial<CredentialNodeStats> = {},
): CredentialNodeStats {
  return {
    isDirectory: () => true,
    isFile: () => false,
    isSymbolicLink: () => false,
    nlink: 1,
    uid: 501,
    mode: 0o40700,
    ...overrides,
  };
}

/** Windows `fs.Stats` values as reported in #384: uid 0, mode 0666. */
function windowsFileNode(
  overrides: Partial<CredentialNodeStats> = {},
): CredentialNodeStats {
  return fileNode({ uid: 0, mode: 0o100666, ...overrides });
}

function windowsDirectoryNode(
  overrides: Partial<CredentialNodeStats> = {},
): CredentialNodeStats {
  return directoryNode({ uid: 0, mode: 0o40666, ...overrides });
}

const originalGetuid = process.getuid;

function stubGetuid(uid: number): void {
  Object.defineProperty(process, "getuid", {
    configurable: true,
    value: () => uid,
  });
}

afterEach(() => {
  if (originalGetuid === undefined) {
    Reflect.deleteProperty(process, "getuid");
  } else {
    Object.defineProperty(process, "getuid", {
      configurable: true,
      value: originalGetuid,
    });
  }
});

describe("assertPrivateCredentialNode on POSIX", () => {
  it("accepts an owner-only file and parent directory", () => {
    stubGetuid(501);
    expect(() => assertPrivateCredentialNode(fileNode(), false, "linux")).not.toThrow();
    expect(() =>
      assertPrivateCredentialNode(directoryNode(), true, "linux"),
    ).not.toThrow();
  });

  it("still rejects a world-readable file, another owner and a hard link", () => {
    stubGetuid(501);
    expect(() =>
      assertPrivateCredentialNode(fileNode({ mode: 0o100644 }), false, "linux"),
    ).toThrow(UnsafeCredentialNodeError);
    expect(() =>
      assertPrivateCredentialNode(fileNode({ uid: 502 }), false, "linux"),
    ).toThrow(UnsafeCredentialNodeError);
    expect(() =>
      assertPrivateCredentialNode(fileNode({ nlink: 2 }), false, "linux"),
    ).toThrow(UnsafeCredentialNodeError);
  });

  it("still rejects a parent directory with loose permissions", () => {
    stubGetuid(501);
    expect(() =>
      assertPrivateCredentialNode(
        directoryNode({ mode: 0o40755 }),
        true,
        "linux",
      ),
    ).toThrow(UnsafeCredentialNodeError);
  });

  it("still refuses when getuid is unavailable", () => {
    Reflect.deleteProperty(process, "getuid");
    expect(() =>
      assertPrivateCredentialNode(fileNode(), false, "linux"),
    ).toThrow(UnsafeCredentialNodeError);
  });
});

describe("assertPrivateCredentialNode on Windows", () => {
  it("accepts the real Windows stats for a credential file and its parent", () => {
    // No getuid on Windows; POSIX uid/mode comparisons are not applicable.
    Reflect.deleteProperty(process, "getuid");
    expect(() =>
      assertPrivateCredentialNode(windowsFileNode(), false, "win32"),
    ).not.toThrow();
    expect(() =>
      assertPrivateCredentialNode(windowsDirectoryNode(), true, "win32"),
    ).not.toThrow();
  });

  it("still rejects a reparse point / symlink", () => {
    expect(() =>
      assertPrivateCredentialNode(
        windowsFileNode({ isSymbolicLink: () => true }),
        false,
        "win32",
      ),
    ).toThrow(UnsafeCredentialNodeError);
  });

  it("still rejects a hard-linked file and a non-regular file", () => {
    expect(() =>
      assertPrivateCredentialNode(
        windowsFileNode({ nlink: 2 }),
        false,
        "win32",
      ),
    ).toThrow(UnsafeCredentialNodeError);
    expect(() =>
      assertPrivateCredentialNode(
        windowsFileNode({ isFile: () => false }),
        false,
        "win32",
      ),
    ).toThrow(UnsafeCredentialNodeError);
    expect(() =>
      assertPrivateCredentialNode(windowsFileNode(), true, "win32"),
    ).toThrow(UnsafeCredentialNodeError);
  });
});

describe("real Windows filesystem stats (#384)", () => {
  const roots: string[] = [];
  afterEach(async () => {
    await Promise.all(
      roots.splice(0).map((root) => rm(root, { recursive: true, force: true })),
    );
  });

  it.skipIf(process.platform !== "win32")(
    "reads a plain installer-shaped credential file",
    async () => {
      const root = await mkdtemp(path.join(os.tmpdir(), "om-credential-node-"));
      roots.push(root);
      const credential = path.join(root, "credential");
      await writeFile(credential, `omhg_v1_${"a".repeat(43)}\n`);
      const directoryStats = await lstat(root);
      const fileStats = await lstat(credential);
      expect(() =>
        assertPrivateCredentialNode(directoryStats, true),
      ).not.toThrow();
      expect(() =>
        assertPrivateCredentialNode(fileStats, false),
      ).not.toThrow();
    },
  );
});
