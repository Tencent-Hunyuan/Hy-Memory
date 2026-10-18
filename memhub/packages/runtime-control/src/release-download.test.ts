import { createHash } from "node:crypto";
import {
  lstat,
  mkdir,
  mkdtemp,
  readFile,
  rm,
  symlink,
  writeFile,
} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { Readable } from "node:stream";
import * as tar from "tar";
import { afterEach, describe, expect, it } from "vitest";
import {
  acquireRuntimeDistribution,
  type HttpGet,
  parseRuntimeReleaseManifest,
  type RuntimeReleaseArtifact,
  type RuntimeReleaseManifest,
  selectRuntimeReleaseArtifact,
  validateTarEntry,
} from "./release-download.js";

const roots: string[] = [];

afterEach(async () => {
  await Promise.all(
    roots.splice(0).map((root) => rm(root, { recursive: true, force: true })),
  );
});

function sha256(value: Uint8Array | string): string {
  return createHash("sha256").update(value).digest("hex");
}

async function fixture(
  options: {
    escapingLink?: boolean;
    corruptPayload?: boolean;
    manifestPlatform?: "darwin" | "linux";
  } = {},
) {
  const root = await mkdtemp(
    path.join(os.tmpdir(), "memhub-release-download-"),
  );
  roots.push(root);
  const distribution = path.join(root, "distribution");
  await mkdir(distribution, { mode: 0o700 });
  const payload = "verified runtime payload\n";
  await writeFile(
    path.join(distribution, "payload.txt"),
    options.corruptPayload ? "corrupted runtime payload\n" : payload,
  );
  await writeFile(path.join(distribution, "launcher"), "#!/bin/sh\nexit 0\n", {
    mode: 0o700,
  });
  if (options.escapingLink) {
    await symlink("../../outside", path.join(distribution, "escape"));
  }
  const files: Record<string, string> = {
    "payload.txt": sha256(payload),
    launcher: sha256("#!/bin/sh\nexit 0\n"),
  };
  if (options.escapingLink) files.escape = sha256("../../outside");
  await writeFile(
    path.join(distribution, "manifest.json"),
    `${JSON.stringify({ schema_version: 1, product: "one-memory-runtime", version: "1.2.3", build_number: 42, git_commit: "a".repeat(40), runtime_protocol: 1, supported_cli_protocol: { min: 1, max: 1 }, platform: options.manifestPlatform ?? "linux", arch: "x64", files }, null, 2)}\n`,
  );
  const archive = path.join(root, "memhub-runtime-v1.2.3-linux-x64.tar.gz");
  await tar.c(
    {
      cwd: root,
      file: archive,
      gzip: true,
      portable: true,
    },
    ["distribution"],
  );
  const archiveBytes = await readFile(archive);
  const artifact: RuntimeReleaseArtifact = {
    kind: "runtime",
    platform: "linux",
    arch: "x64",
    format: "tar.gz",
    filename: path.basename(archive),
    url: "https://downloads.example.test/runtime.tar.gz",
    bytes: archiveBytes.byteLength,
    unpacked_bytes: 1024 * 1024,
    sha256: sha256(archiveBytes),
    runtime_manifest_schema: 1,
  };
  const release: RuntimeReleaseManifest = {
    schema_version: 1,
    product: "memhub-release",
    version: "1.2.3",
    build_number: 42,
    git_commit: "a".repeat(40),
    channel: "stable",
    cli_protocol: 1,
    supported_runtime_protocol: { min: 1, max: 1 },
    runtime_protocol: 1,
    supported_cli_protocol: { min: 1, max: 1 },
    published_at: "2026-10-13T06:00:00Z",
    artifacts: [artifact],
  };
  const manifestBytes = Buffer.from(JSON.stringify(release));
  return { root, archiveBytes, artifact, release, manifestBytes };
}

function response(body: Uint8Array, status = 200, headers = {}) {
  return {
    status,
    headers: {
      "content-length": String(body.byteLength),
      ...headers,
    },
    body: Readable.from([body]),
  };
}

function httpFixture(
  values: Record<
    string,
    ReturnType<typeof response> | (() => ReturnType<typeof response>)
  >,
): HttpGet {
  return async (url) => {
    const value = values[url];
    if (!value) throw new Error(`unexpected URL ${url}`);
    return typeof value === "function" ? value() : value;
  };
}

describe("release manifest", () => {
  it("parses a strict release and selects the exact platform artifact", async () => {
    const value = await fixture();
    const parsed = parseRuntimeReleaseManifest({
      ...value.release,
      artifacts: [
        {
          kind: "desktop",
          platform: "darwin",
          arch: "arm64",
          format: "dmg",
          filename: "MemHub-v1.2.3-macos-arm64.dmg",
          url: "https://downloads.example.test/MemHub.dmg",
          bytes: 123,
          sha256: "b".repeat(64),
          minimum_system_version: "13.0",
        },
        {
          kind: "metadata",
          role: "sbom",
          format: "spdx-json",
          filename: "SBOM.spdx.json",
          url: "https://downloads.example.test/SBOM.spdx.json",
          bytes: 45,
          sha256: "c".repeat(64),
        },
        value.artifact,
      ],
    });
    expect(
      selectRuntimeReleaseArtifact(parsed, {
        version: "1.2.3",
        channel: "stable",
        platform: "linux",
        arch: "x64",
      }),
    ).toEqual(value.artifact);
  });

  it("rejects duplicate platform/architecture entries and version drift", async () => {
    const value = await fixture();
    expect(() =>
      parseRuntimeReleaseManifest({
        ...value.release,
        artifacts: [value.artifact, value.artifact],
      }),
    ).toThrow("release_manifest_invalid");
    expect(() =>
      selectRuntimeReleaseArtifact(value.release, {
        version: "1.2.4",
        channel: "stable",
        platform: "linux",
        arch: "x64",
      }),
    ).toThrow("release_version_mismatch");
  });

  it("fails closed when protocol declarations are missing or incompatible", async () => {
    const value = await fixture();
    const { runtime_protocol: _runtime, ...missing } = value.release;
    expect(() => parseRuntimeReleaseManifest(missing)).toThrow(
      "release_manifest_invalid",
    );
    expect(() =>
      parseRuntimeReleaseManifest({
        ...value.release,
        runtime_protocol: 2,
        supported_runtime_protocol: { min: 2, max: 2 },
      }),
    ).toThrow("release_protocol_incompatible");
  });

  it("allows Runtime and Desktop assets for the same target but rejects duplicate kinds", async () => {
    const value = await fixture();
    const desktop = {
      kind: "desktop",
      platform: "darwin",
      arch: "x64",
      format: "dmg",
      filename: "MemHub-v1.2.3-macos-x64.dmg",
      url: "https://downloads.example.test/MemHub-x64.dmg",
      bytes: 123,
      sha256: "d".repeat(64),
      minimum_system_version: "13.0",
    } as const;
    expect(() =>
      parseRuntimeReleaseManifest({
        ...value.release,
        artifacts: [value.artifact, desktop],
      }),
    ).not.toThrow();
    expect(() =>
      parseRuntimeReleaseManifest({
        ...value.release,
        artifacts: [desktop, desktop],
      }),
    ).toThrow("release_manifest_invalid");
  });
});

describe("archive entry safety", () => {
  it.each([
    ["../outside", "File", undefined, "release_archive_path_invalid"],
    ["/absolute", "File", undefined, "release_archive_path_invalid"],
    ["safe\\windows", "File", undefined, "release_archive_path_invalid"],
    [
      "safe/device",
      "CharacterDevice",
      undefined,
      "release_archive_type_invalid",
    ],
    [
      "safe/symlink",
      "SymbolicLink",
      "../../outside",
      "release_archive_link_escapes",
    ],
    ["safe/hardlink", "Link", "../outside", "release_archive_link_escapes"],
    ["safe/hardlink", "Link", "/absolute", "release_archive_link_escapes"],
  ] as const)(
    "rejects path=%s type=%s link=%s with %s",
    (entryPath, type, linkPath, expected) => {
      expect(() => validateTarEntry(entryPath, type, linkPath)).toThrow(
        expected,
      );
    },
  );

  it.each([
    ["safe/file", "File", undefined],
    ["safe/directory", "Directory", undefined],
    ["safe/symlink", "SymbolicLink", "../target"],
    ["safe/hardlink", "Link", "safe/target"],
  ] as const)(
    "accepts contained path=%s type=%s link=%s",
    (entryPath, type, linkPath) => {
      expect(() => validateTarEntry(entryPath, type, linkPath)).not.toThrow();
    },
  );
});

describe("remote Runtime acquisition", () => {
  it("downloads, hashes, safely extracts and verifies the existing distribution contract", async () => {
    const value = await fixture();
    const temporaryParent = await mkdtemp(
      path.join(os.tmpdir(), "memhub-release-parent-"),
    );
    roots.push(temporaryParent);
    const acquired = await acquireRuntimeDistribution({
      manifestUrl: "https://downloads.example.test/release-manifest.json",
      expectedVersion: "1.2.3",
      channel: "stable",
      platform: "linux",
      arch: "x64",
      temporaryParent,
      httpGet: httpFixture({
        "https://downloads.example.test/release-manifest.json": response(
          value.manifestBytes,
        ),
        "https://downloads.example.test/runtime.tar.gz": response(
          value.archiveBytes,
        ),
      }),
    });

    expect(
      await readFile(path.join(acquired.distribution, "payload.txt"), "utf8"),
    ).toBe("verified runtime payload\n");
    expect(
      (await lstat(path.join(acquired.distribution, "launcher"))).mode & 0o111,
    ).toBe(0o100);
    expect(acquired.release.version).toBe("1.2.3");
    expect((await lstat(acquired.temporary_root)).mode & 0o777).toBe(0o700);
    await acquired.cleanup();
    await expect(lstat(acquired.temporary_root)).rejects.toMatchObject({
      code: "ENOENT",
    });
  });

  it("allows the exact GitHub Release asset redirect hosts by default", async () => {
    const value = await fixture();
    const manifestUrl =
      "https://github.com/example/project/releases/download/v1.2.3/release-manifest.json";
    const artifactUrl =
      "https://github.com/example/project/releases/download/v1.2.3/runtime.tar.gz";
    const manifestAssetUrl =
      "https://release-assets.githubusercontent.com/manifest-download";
    const runtimeAssetUrl =
      "https://release-assets.githubusercontent.com/runtime-download";
    const release = {
      ...value.release,
      artifacts: [{ ...value.artifact, url: artifactUrl }],
    };
    const temporaryParent = await mkdtemp(
      path.join(os.tmpdir(), "memhub-github-release-parent-"),
    );
    roots.push(temporaryParent);

    const acquired = await acquireRuntimeDistribution({
      manifestUrl,
      expectedVersion: "1.2.3",
      channel: "stable",
      platform: "linux",
      arch: "x64",
      temporaryParent,
      httpGet: httpFixture({
        [manifestUrl]: response(new Uint8Array(), 302, {
          location: manifestAssetUrl,
        }),
        [manifestAssetUrl]: response(Buffer.from(JSON.stringify(release))),
        [artifactUrl]: response(new Uint8Array(), 302, {
          location: runtimeAssetUrl,
        }),
        [runtimeAssetUrl]: response(value.archiveBytes),
      }),
    });

    expect(acquired.release.version).toBe("1.2.3");
    await acquired.cleanup();
  });

  it("rejects an archive whose internal commit drifts from the Release", async () => {
    const value = await fixture();
    const release = { ...value.release, git_commit: "b".repeat(40) };
    await expect(
      acquireRuntimeDistribution({
        manifestUrl: "https://downloads.example.test/release-manifest.json",
        expectedVersion: "1.2.3",
        channel: "stable",
        platform: "linux",
        arch: "x64",
        httpGet: httpFixture({
          "https://downloads.example.test/release-manifest.json": response(
            Buffer.from(JSON.stringify(release)),
          ),
          "https://downloads.example.test/runtime.tar.gz": response(
            value.archiveBytes,
          ),
        }),
      }),
    ).rejects.toThrow("release_runtime_identity_mismatch");
  });

  it.each([
    ["distribution_hash_mismatch", { corruptPayload: true }],
    ["distribution_target_mismatch", { manifestPlatform: "darwin" }],
  ] as const)(
    "rejects %s inside a fully downloaded archive and cleans temporary state",
    async (expected, fixtureOptions) => {
      const value = await fixture(fixtureOptions);
      const temporaryParent = await mkdtemp(
        path.join(os.tmpdir(), "memhub-release-parent-"),
      );
      roots.push(temporaryParent);
      await expect(
        acquireRuntimeDistribution({
          manifestUrl: "https://downloads.example.test/release-manifest.json",
          expectedVersion: "1.2.3",
          channel: "stable",
          platform: "linux",
          arch: "x64",
          temporaryParent,
          httpGet: httpFixture({
            "https://downloads.example.test/release-manifest.json": response(
              value.manifestBytes,
            ),
            "https://downloads.example.test/runtime.tar.gz": response(
              value.archiveBytes,
            ),
          }),
        }),
      ).rejects.toThrow(expected);
      expect(await readdirNames(temporaryParent)).toEqual([]);
    },
  );

  it("rejects a redirect to an untrusted host", async () => {
    await expect(
      acquireRuntimeDistribution({
        manifestUrl: "https://downloads.example.test/release-manifest.json",
        expectedVersion: "1.2.3",
        channel: "stable",
        platform: "linux",
        arch: "x64",
        httpGet: httpFixture({
          "https://downloads.example.test/release-manifest.json": response(
            new Uint8Array(),
            302,
            { location: "https://evil.example.test/release.json" },
          ),
        }),
      }),
    ).rejects.toThrow("release_url_untrusted");
  });

  it("times out a response body that stops making progress", async () => {
    async function* stalled(signal: AbortSignal): AsyncGenerator<Uint8Array> {
      yield Buffer.from("{");
      await new Promise<void>((resolve) => {
        signal.addEventListener("abort", () => resolve(), { once: true });
      });
      if (signal.aborted) throw new Error("aborted");
    }
    const httpGet: HttpGet = async (_url, options) => ({
      status: 200,
      headers: {},
      body: stalled(options.signal),
    });

    await expect(
      acquireRuntimeDistribution({
        manifestUrl: "https://downloads.example.test/release-manifest.json",
        expectedVersion: "1.2.3",
        channel: "stable",
        platform: "linux",
        arch: "x64",
        manifestTimeoutMs: 20,
        httpGet,
      }),
    ).rejects.toThrow("release_download_timeout");
  });

  it("cleans partial bytes when the artifact stream is interrupted", async () => {
    const value = await fixture();
    const temporaryParent = await mkdtemp(
      path.join(os.tmpdir(), "memhub-release-parent-"),
    );
    roots.push(temporaryParent);
    async function* interrupted(): AsyncGenerator<Uint8Array> {
      yield value.archiveBytes.subarray(0, 128);
      throw new Error("synthetic_connection_reset");
    }
    const httpGet: HttpGet = async (url) =>
      url.endsWith("release-manifest.json")
        ? response(value.manifestBytes)
        : { status: 200, headers: {}, body: interrupted() };

    await expect(
      acquireRuntimeDistribution({
        manifestUrl: "https://downloads.example.test/release-manifest.json",
        expectedVersion: "1.2.3",
        channel: "stable",
        platform: "linux",
        arch: "x64",
        temporaryParent,
        httpGet,
      }),
    ).rejects.toThrow("synthetic_connection_reset");
    expect(await readdirNames(temporaryParent)).toEqual([]);
  });

  it("refuses insufficient disk space before creating download state", async () => {
    const value = await fixture();
    const temporaryParent = await mkdtemp(
      path.join(os.tmpdir(), "memhub-release-parent-"),
    );
    roots.push(temporaryParent);
    let artifactRequests = 0;
    const httpGet: HttpGet = async (url) => {
      if (url.endsWith("release-manifest.json")) {
        return response(value.manifestBytes);
      }
      artifactRequests += 1;
      return response(value.archiveBytes);
    };
    await expect(
      acquireRuntimeDistribution({
        manifestUrl: "https://downloads.example.test/release-manifest.json",
        expectedVersion: "1.2.3",
        channel: "stable",
        platform: "linux",
        arch: "x64",
        temporaryParent,
        httpGet,
        statfsImpl: async () => ({ bavail: 1, bsize: 1 }),
      }),
    ).rejects.toThrow("release_disk_space_insufficient");
    expect(artifactRequests).toBe(0);
    expect(await readdirNames(temporaryParent)).toEqual([]);
  });

  it.each([
    ["release_artifact_size_mismatch", "size"],
    ["release_artifact_hash_mismatch", "hash"],
  ])("rejects %s", async (expected, mutation) => {
    const value = await fixture();
    const artifact = {
      ...value.artifact,
      ...(mutation === "size" ? { bytes: value.artifact.bytes + 1 } : {}),
      ...(mutation === "hash" ? { sha256: "0".repeat(64) } : {}),
    };
    const release = { ...value.release, artifacts: [artifact] };
    await expect(
      acquireRuntimeDistribution({
        manifestUrl: "https://downloads.example.test/release-manifest.json",
        expectedVersion: "1.2.3",
        channel: "stable",
        platform: "linux",
        arch: "x64",
        httpGet: httpFixture({
          "https://downloads.example.test/release-manifest.json": response(
            Buffer.from(JSON.stringify(release)),
          ),
          "https://downloads.example.test/runtime.tar.gz": response(
            value.archiveBytes,
          ),
        }),
      }),
    ).rejects.toThrow(expected);
  });

  it("rejects an archive symlink that escapes extraction root and cleans temporary state", async () => {
    const value = await fixture({ escapingLink: true });
    const temporaryParent = await mkdtemp(
      path.join(os.tmpdir(), "memhub-release-parent-"),
    );
    roots.push(temporaryParent);
    await expect(
      acquireRuntimeDistribution({
        manifestUrl: "https://downloads.example.test/release-manifest.json",
        expectedVersion: "1.2.3",
        channel: "stable",
        platform: "linux",
        arch: "x64",
        temporaryParent,
        httpGet: httpFixture({
          "https://downloads.example.test/release-manifest.json": response(
            value.manifestBytes,
          ),
          "https://downloads.example.test/runtime.tar.gz": response(
            value.archiveBytes,
          ),
        }),
      }),
    ).rejects.toThrow("release_archive_link_escapes");
    expect(await readdirNames(temporaryParent)).toEqual([]);
  });
});

async function readdirNames(directory: string): Promise<string[]> {
  return import("node:fs/promises").then(({ readdir }) => readdir(directory));
}
