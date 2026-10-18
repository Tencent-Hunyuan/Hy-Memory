import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import {
  chmod,
  lstat,
  mkdir,
  mkdtemp,
  open,
  readdir,
  readFile,
  realpath,
  rm,
  statfs,
} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { Readable } from "node:stream";
import axios from "axios";
import { HttpsProxyAgent } from "https-proxy-agent";
// proxy-from-env 2.1.0 ships ESM/CJS entrypoints but no TypeScript declarations.
// The one exported function used here is pinned by the package version and by
// the real HTTPS_PROXY/NO_PROXY acceptance test.
// @ts-expect-error upstream package has no declaration file
import { getProxyForUrl } from "proxy-from-env";
import * as tar from "tar";
import { verifyInstalledDistribution } from "./local-install.js";
import {
  CLI_PROTOCOL,
  CLI_SUPPORTED_RUNTIME_PROTOCOL,
  type ProtocolRange,
  protocolIncludes,
  protocolRange,
} from "./release-contract.js";

const SEMVER =
  /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-((?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$/;
const SHA256 = /^[a-f0-9]{64}$/;
const COMMIT = /^[0-9a-f]{40}$/;
const SAFE_FILE_NAME = /^[0-9A-Za-z][0-9A-Za-z._-]{0,255}$/;
const REDIRECTS = new Set([301, 302, 303, 307, 308]);
const GITHUB_RELEASE_REDIRECT_HOSTS = [
  "release-assets.githubusercontent.com",
  "objects.githubusercontent.com",
] as const;

export type ReleaseChannel = "stable" | "prerelease";
export type RuntimeReleasePlatform = "darwin" | "linux";
export type RuntimeReleaseArch = "x64" | "arm64";

export interface RuntimeReleaseArtifact {
  kind: "runtime";
  platform: RuntimeReleasePlatform;
  arch: RuntimeReleaseArch;
  format: "tar.gz";
  filename: string;
  url: string;
  bytes: number;
  unpacked_bytes: number;
  sha256: string;
  runtime_manifest_schema: 1;
}

export interface DesktopReleaseArtifact {
  kind: "desktop";
  platform: "darwin";
  arch: RuntimeReleaseArch;
  format: "dmg";
  filename: string;
  url: string;
  bytes: number;
  sha256: string;
  minimum_system_version: string;
}

export interface MetadataReleaseArtifact {
  kind: "metadata";
  role: "sbom" | "notices";
  format: "spdx-json" | "markdown";
  filename: string;
  url: string;
  bytes: number;
  sha256: string;
}

export type ReleaseArtifact =
  | RuntimeReleaseArtifact
  | DesktopReleaseArtifact
  | MetadataReleaseArtifact;

export interface RuntimeReleaseManifest {
  schema_version: 1;
  product: "memhub-release";
  version: string;
  build_number: number;
  git_commit: string;
  channel: ReleaseChannel;
  cli_protocol: number;
  supported_runtime_protocol: ProtocolRange;
  runtime_protocol: number;
  supported_cli_protocol: ProtocolRange;
  published_at: string;
  artifacts: ReleaseArtifact[];
}

export interface HttpGetResponse {
  status: number;
  headers: Record<string, string | undefined>;
  body: AsyncIterable<Uint8Array>;
}

export type HttpGet = (
  url: string,
  options: { signal: AbortSignal; timeoutMs: number },
) => Promise<HttpGetResponse>;

export type StatfsAvailable = (
  directory: string,
) => Promise<{ bavail: number | bigint; bsize: number | bigint }>;

export interface AcquiredRuntimeDistribution {
  release: RuntimeReleaseManifest;
  artifact: RuntimeReleaseArtifact;
  temporary_root: string;
  distribution: string;
  cleanup(): Promise<void>;
  [Symbol.asyncDispose](): Promise<void>;
}

export interface ResolvedRuntimeRelease {
  release: RuntimeReleaseManifest;
  artifact: RuntimeReleaseArtifact;
  manifest_url: string;
  allowed_hosts: readonly string[];
}

export function parseRuntimeReleaseManifest(
  value: unknown,
): RuntimeReleaseManifest {
  if (!isObject(value)) throw new Error("release_manifest_invalid");
  const artifacts = value.artifacts;
  if (
    value.schema_version !== 1 ||
    value.product !== "memhub-release" ||
    typeof value.version !== "string" ||
    !SEMVER.test(value.version) ||
    !isBuildNumber(value.build_number) ||
    typeof value.git_commit !== "string" ||
    !COMMIT.test(value.git_commit) ||
    (value.channel !== "stable" && value.channel !== "prerelease") ||
    !isProtocolVersion(value.cli_protocol) ||
    !protocolRange(value.supported_runtime_protocol) ||
    !isProtocolVersion(value.runtime_protocol) ||
    !protocolRange(value.supported_cli_protocol) ||
    typeof value.published_at !== "string" ||
    !validTimestamp(value.published_at) ||
    !Array.isArray(artifacts) ||
    artifacts.length === 0 ||
    artifacts.length > 32
  ) {
    throw new Error("release_manifest_invalid");
  }
  if (
    value.cli_protocol !== CLI_PROTOCOL ||
    !protocolIncludes(CLI_SUPPORTED_RUNTIME_PROTOCOL, value.runtime_protocol) ||
    !protocolIncludes(
      value.supported_runtime_protocol,
      value.runtime_protocol,
    ) ||
    !protocolIncludes(value.supported_cli_protocol, CLI_PROTOCOL)
  ) {
    throw new Error("release_protocol_incompatible");
  }
  const parsed = artifacts.map(parseArtifact);
  const identities = new Set<string>();
  for (const artifact of parsed) {
    const identity =
      artifact.kind === "metadata"
        ? `${artifact.kind}/${artifact.role}`
        : `${artifact.kind}/${artifact.platform}/${artifact.arch}`;
    if (identities.has(identity)) throw new Error("release_manifest_invalid");
    identities.add(identity);
  }
  return Object.freeze({
    schema_version: 1,
    product: "memhub-release",
    version: value.version,
    build_number: value.build_number,
    git_commit: value.git_commit,
    channel: value.channel,
    cli_protocol: value.cli_protocol,
    supported_runtime_protocol: Object.freeze({
      min: value.supported_runtime_protocol.min,
      max: value.supported_runtime_protocol.max,
    }),
    runtime_protocol: value.runtime_protocol,
    supported_cli_protocol: Object.freeze({
      min: value.supported_cli_protocol.min,
      max: value.supported_cli_protocol.max,
    }),
    published_at: value.published_at,
    artifacts: Object.freeze(
      parsed.map((artifact) => Object.freeze(artifact)),
    ) as unknown as ReleaseArtifact[],
  });
}

export function selectRuntimeReleaseArtifact(
  release: RuntimeReleaseManifest,
  options: {
    version: string;
    channel: ReleaseChannel;
    platform: NodeJS.Platform;
    arch: string;
  },
): RuntimeReleaseArtifact {
  if (release.version !== options.version) {
    throw new Error("release_version_mismatch");
  }
  if (release.channel !== options.channel) {
    throw new Error("release_channel_mismatch");
  }
  if (options.platform !== "darwin" && options.platform !== "linux") {
    throw new Error("release_platform_unsupported");
  }
  if (options.arch !== "x64" && options.arch !== "arm64") {
    throw new Error("release_arch_unsupported");
  }
  const artifact = release.artifacts.find(
    (candidate): candidate is RuntimeReleaseArtifact =>
      candidate.kind === "runtime" &&
      candidate.platform === options.platform &&
      candidate.arch === options.arch,
  );
  if (!artifact) throw new Error("release_artifact_unavailable");
  return artifact;
}

export async function acquireRuntimeDistribution(options: {
  manifestUrl: string;
  expectedVersion: string;
  channel: ReleaseChannel;
  platform: NodeJS.Platform;
  arch: string;
  allowedHosts?: readonly string[];
  temporaryParent?: string;
  httpGet?: HttpGet;
  manifestTimeoutMs?: number;
  artifactTimeoutMs?: number;
  maxManifestBytes?: number;
  maxArtifactBytes?: number;
  maxUnpackedBytes?: number;
  maxRedirects?: number;
  statfsImpl?: StatfsAvailable;
}): Promise<AcquiredRuntimeDistribution> {
  const resolved = await resolveRuntimeRelease(options);
  const { release, artifact } = resolved;
  const allowedHosts = new Set(resolved.allowed_hosts);
  const httpGet = options.httpGet ?? defaultHttpGet;
  const artifactUrl = trustedUrl(artifact.url);
  const maxArtifactBytes = options.maxArtifactBytes ?? 2 * 1024 * 1024 * 1024;
  const maxUnpackedBytes = options.maxUnpackedBytes ?? 4 * 1024 * 1024 * 1024;
  if (
    artifact.bytes > maxArtifactBytes ||
    artifact.unpacked_bytes > maxUnpackedBytes
  ) {
    throw new Error("release_artifact_too_large");
  }

  const temporaryParent = path.resolve(options.temporaryParent ?? os.tmpdir());
  await assertDiskSpace(
    temporaryParent,
    artifact.bytes + artifact.unpacked_bytes,
    options.statfsImpl ?? statfs,
  );
  const temporaryRoot = await mkdtemp(
    path.join(temporaryParent, "memhub-runtime-"),
  );
  await chmod(temporaryRoot, 0o700);
  const archive = path.join(temporaryRoot, artifact.filename);
  const extracted = path.join(temporaryRoot, "extracted");
  try {
    await downloadArtifact(artifactUrl, archive, artifact, {
      httpGet,
      allowedHosts,
      timeoutMs: options.artifactTimeoutMs ?? 5 * 60_000,
      maxRedirects: options.maxRedirects ?? 3,
      maxBytes: maxArtifactBytes,
    });
    await mkdir(extracted, { mode: 0o700 });
    await extractRuntimeArchive(archive, extracted, artifact.unpacked_bytes);
    const distribution = await locateDistribution(extracted);
    const runtimeManifest = await verifyInstalledDistribution(
      distribution,
      options.platform,
      options.arch,
    );
    if (
      runtimeManifest.version !== release.version ||
      runtimeManifest.build_number !== release.build_number ||
      runtimeManifest.git_commit !== release.git_commit ||
      runtimeManifest.runtime_protocol !== release.runtime_protocol ||
      runtimeManifest.supported_cli_protocol?.min !==
        release.supported_cli_protocol.min ||
      runtimeManifest.supported_cli_protocol?.max !==
        release.supported_cli_protocol.max
    ) {
      throw new Error("release_runtime_identity_mismatch");
    }
    return Object.freeze({
      release,
      artifact,
      temporary_root: temporaryRoot,
      distribution,
      cleanup: () => rm(temporaryRoot, { recursive: true, force: true }),
      [Symbol.asyncDispose]: () =>
        rm(temporaryRoot, { recursive: true, force: true }),
    });
  } catch (error) {
    await rm(temporaryRoot, { recursive: true, force: true });
    throw error;
  }
}

export async function resolveRuntimeRelease(options: {
  manifestUrl: string;
  expectedVersion: string;
  channel: ReleaseChannel;
  platform: NodeJS.Platform;
  arch: string;
  allowedHosts?: readonly string[];
  httpGet?: HttpGet;
  manifestTimeoutMs?: number;
  maxManifestBytes?: number;
  maxRedirects?: number;
}): Promise<ResolvedRuntimeRelease> {
  const manifestUrl = trustedUrl(options.manifestUrl);
  const defaultHosts =
    normalizedHost(manifestUrl.hostname) === "github.com"
      ? [manifestUrl.hostname, ...GITHUB_RELEASE_REDIRECT_HOSTS]
      : [manifestUrl.hostname];
  const allowedHosts = new Set(
    (options.allowedHosts ?? defaultHosts).map(normalizedHost),
  );
  if (!allowedHosts.has(normalizedHost(manifestUrl.hostname))) {
    throw new Error("release_url_untrusted");
  }
  const httpGet = options.httpGet ?? defaultHttpGet;
  const manifestBytes = await fetchBytes(manifestUrl, {
    httpGet,
    allowedHosts,
    timeoutMs: options.manifestTimeoutMs ?? 15_000,
    maxBytes: options.maxManifestBytes ?? 1024 * 1024,
    maxRedirects: options.maxRedirects ?? 3,
  });
  let releaseValue: unknown;
  try {
    releaseValue = JSON.parse(
      new TextDecoder("utf-8", { fatal: true }).decode(manifestBytes),
    );
  } catch {
    throw new Error("release_manifest_invalid");
  }
  const release = parseRuntimeReleaseManifest(releaseValue);
  const artifact = selectRuntimeReleaseArtifact(release, {
    version: options.expectedVersion,
    channel: options.channel,
    platform: options.platform,
    arch: options.arch,
  });
  const artifactUrl = trustedUrl(artifact.url);
  if (!allowedHosts.has(normalizedHost(artifactUrl.hostname))) {
    throw new Error("release_url_untrusted");
  }
  return Object.freeze({
    release,
    artifact,
    manifest_url: manifestUrl.href,
    allowed_hosts: Object.freeze([...allowedHosts]),
  });
}

async function defaultHttpGet(
  url: string,
  options: { signal: AbortSignal; timeoutMs: number },
): Promise<HttpGetResponse> {
  const proxyUrl = getProxyForUrl(url);
  const httpsAgent = proxyUrl ? trustedProxyAgent(proxyUrl) : undefined;
  const response = await axios.get<Readable>(url, {
    responseType: "stream",
    timeout: options.timeoutMs,
    signal: options.signal,
    maxRedirects: 0,
    proxy: false,
    ...(httpsAgent ? { httpsAgent } : {}),
    validateStatus: () => true,
  });
  const headers: Record<string, string | undefined> = {};
  for (const [name, value] of Object.entries(response.headers)) {
    headers[name.toLowerCase()] = Array.isArray(value)
      ? value.join(", ")
      : value === undefined
        ? undefined
        : String(value);
  }
  return { status: response.status, headers, body: response.data };
}

function trustedProxyAgent(raw: string): HttpsProxyAgent<string> {
  let proxy: URL;
  try {
    proxy = new URL(raw);
  } catch {
    throw new Error("release_proxy_invalid");
  }
  if (
    (proxy.protocol !== "http:" && proxy.protocol !== "https:") ||
    proxy.hash !== "" ||
    (proxy.pathname !== "" && proxy.pathname !== "/") ||
    proxy.search !== ""
  ) {
    throw new Error("release_proxy_invalid");
  }
  return new HttpsProxyAgent(proxy);
}

async function fetchBytes(
  initial: URL,
  options: {
    httpGet: HttpGet;
    allowedHosts: Set<string>;
    timeoutMs: number;
    maxBytes: number;
    maxRedirects: number;
  },
): Promise<Uint8Array> {
  const chunks: Uint8Array[] = [];
  let total = 0;
  await forResponse(initial, options, async (response) => {
    const declared = contentLength(response.headers);
    if (declared !== null && declared > options.maxBytes) {
      throw new Error("release_response_too_large");
    }
    for await (const chunk of response.body) {
      total += chunk.byteLength;
      if (total > options.maxBytes)
        throw new Error("release_response_too_large");
      chunks.push(chunk);
    }
  });
  const body = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return body;
}

async function downloadArtifact(
  initial: URL,
  destination: string,
  artifact: RuntimeReleaseArtifact,
  options: {
    httpGet: HttpGet;
    allowedHosts: Set<string>;
    timeoutMs: number;
    maxRedirects: number;
    maxBytes: number;
  },
): Promise<void> {
  const digest = createHash("sha256");
  let total = 0;
  const file = await open(destination, "wx", 0o600);
  try {
    await forResponse(initial, options, async (response) => {
      const declared = contentLength(response.headers);
      if (declared !== null && declared !== artifact.bytes) {
        throw new Error("release_artifact_size_mismatch");
      }
      for await (const chunk of response.body) {
        total += chunk.byteLength;
        if (total > options.maxBytes || total > artifact.bytes) {
          throw new Error("release_response_too_large");
        }
        digest.update(chunk);
        await writeAll(file, chunk);
      }
    });
  } finally {
    await file.close();
  }
  if (total !== artifact.bytes)
    throw new Error("release_artifact_size_mismatch");
  if (digest.digest("hex") !== artifact.sha256) {
    throw new Error("release_artifact_hash_mismatch");
  }
}

async function forResponse(
  initial: URL,
  options: {
    httpGet: HttpGet;
    allowedHosts: Set<string>;
    timeoutMs: number;
    maxRedirects: number;
  },
  consume: (response: HttpGetResponse) => Promise<void>,
): Promise<void> {
  let current = initial;
  for (let redirect = 0; ; redirect += 1) {
    if (!options.allowedHosts.has(normalizedHost(current.hostname))) {
      throw new Error("release_url_untrusted");
    }
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), options.timeoutMs);
    try {
      const response = await options.httpGet(current.href, {
        signal: controller.signal,
        timeoutMs: options.timeoutMs,
      });
      if (REDIRECTS.has(response.status)) {
        if (redirect >= options.maxRedirects) {
          throw new Error("release_redirect_limit");
        }
        const location = response.headers.location;
        if (!location) throw new Error("release_redirect_invalid");
        current = trustedUrl(new URL(location, current).href);
        continue;
      }
      if (response.status !== 200) {
        throw new Error(`release_http_status_${response.status}`);
      }
      await consume(response);
      return;
    } catch (error) {
      if (controller.signal.aborted)
        throw new Error("release_download_timeout");
      throw error;
    } finally {
      clearTimeout(timeout);
    }
  }
}

async function extractRuntimeArchive(
  archive: string,
  destination: string,
  maxUnpackedBytes: number,
): Promise<void> {
  const seen = new Set<string>();
  let total = 0;
  let validationError: Error | null = null;
  await tar.t({
    file: archive,
    gzip: true,
    strict: true,
    onentry(entry) {
      if (validationError !== null) return;
      try {
        validateTarEntry(entry.path, entry.type, entry.linkpath);
        if (seen.has(entry.path)) {
          throw new Error("release_archive_duplicate_path");
        }
        seen.add(entry.path);
        total += entry.size;
        if (total > maxUnpackedBytes) {
          throw new Error("release_archive_too_large");
        }
      } catch (error) {
        validationError =
          error instanceof Error ? error : new Error("release_archive_invalid");
      }
    },
  });
  if (validationError !== null) throw validationError;
  await tar.x({
    file: archive,
    cwd: destination,
    gzip: true,
    strict: true,
    preservePaths: false,
    noChmod: false,
    preserveOwner: false,
  });
}

async function writeAll(
  file: Awaited<ReturnType<typeof open>>,
  chunk: Uint8Array,
): Promise<void> {
  let offset = 0;
  while (offset < chunk.byteLength) {
    const { bytesWritten } = await file.write(
      chunk,
      offset,
      chunk.byteLength - offset,
    );
    if (bytesWritten <= 0) throw new Error("release_artifact_write_failed");
    offset += bytesWritten;
  }
}

export function validateTarEntry(
  entryPath: string,
  type: string,
  linkPath: string | undefined,
): void {
  if (!safeArchivePath(entryPath))
    throw new Error("release_archive_path_invalid");
  if (!["File", "Directory", "SymbolicLink", "Link"].includes(type)) {
    throw new Error("release_archive_type_invalid");
  }
  if (type === "SymbolicLink" || type === "Link") {
    if (
      !linkPath ||
      path.posix.isAbsolute(linkPath) ||
      linkPath.includes("\\")
    ) {
      throw new Error("release_archive_link_escapes");
    }
    const base = type === "Link" ? "." : path.posix.dirname(entryPath);
    const resolved = path.posix.normalize(path.posix.join(base, linkPath));
    if (!safeArchivePath(resolved))
      throw new Error("release_archive_link_escapes");
  }
}

async function locateDistribution(extracted: string): Promise<string> {
  if (await regularFile(path.join(extracted, "manifest.json")))
    return extracted;
  const entries = await readdir(extracted, { withFileTypes: true });
  const directories = entries.filter((entry) => entry.isDirectory());
  if (entries.length !== 1 || directories.length !== 1) {
    throw new Error("release_archive_layout_invalid");
  }
  const candidate = await realpath(path.join(extracted, directories[0].name));
  if (!(await regularFile(path.join(candidate, "manifest.json")))) {
    throw new Error("release_archive_layout_invalid");
  }
  return candidate;
}

async function regularFile(file: string): Promise<boolean> {
  const info = await lstat(file).catch(() => null);
  return info?.isFile() === true && !info.isSymbolicLink();
}

async function assertDiskSpace(
  directory: string,
  required: number,
  statfsImpl: StatfsAvailable,
): Promise<void> {
  const stats = await statfsImpl(directory);
  const available = Number(stats.bavail) * Number(stats.bsize);
  if (!Number.isSafeInteger(required) || required < 0 || available < required) {
    throw new Error("release_disk_space_insufficient");
  }
}

function parseArtifact(value: unknown): ReleaseArtifact {
  if (!isObject(value)) throw new Error("release_manifest_invalid");
  if (value.kind === "desktop") {
    if (
      value.platform !== "darwin" ||
      (value.arch !== "x64" && value.arch !== "arm64") ||
      value.format !== "dmg" ||
      typeof value.filename !== "string" ||
      !SAFE_FILE_NAME.test(value.filename) ||
      typeof value.url !== "string" ||
      !isPositiveSafeInteger(value.bytes) ||
      typeof value.sha256 !== "string" ||
      !SHA256.test(value.sha256) ||
      typeof value.minimum_system_version !== "string" ||
      !/^\d+\.\d+$/.test(value.minimum_system_version)
    ) {
      throw new Error("release_manifest_invalid");
    }
    trustedUrl(value.url);
    return value as unknown as DesktopReleaseArtifact;
  }
  if (value.kind === "metadata") {
    if (
      (value.role !== "sbom" && value.role !== "notices") ||
      (value.format !== "spdx-json" && value.format !== "markdown") ||
      typeof value.filename !== "string" ||
      !SAFE_FILE_NAME.test(value.filename) ||
      typeof value.url !== "string" ||
      !isPositiveSafeInteger(value.bytes) ||
      typeof value.sha256 !== "string" ||
      !SHA256.test(value.sha256)
    ) {
      throw new Error("release_manifest_invalid");
    }
    trustedUrl(value.url);
    return value as unknown as MetadataReleaseArtifact;
  }
  if (
    value.kind !== "runtime" ||
    (value.platform !== "darwin" && value.platform !== "linux") ||
    (value.arch !== "x64" && value.arch !== "arm64") ||
    value.format !== "tar.gz" ||
    typeof value.filename !== "string" ||
    !SAFE_FILE_NAME.test(value.filename) ||
    typeof value.url !== "string" ||
    !isPositiveSafeInteger(value.bytes) ||
    !isPositiveSafeInteger(value.unpacked_bytes) ||
    typeof value.sha256 !== "string" ||
    !SHA256.test(value.sha256) ||
    value.runtime_manifest_schema !== 1
  ) {
    throw new Error("release_manifest_invalid");
  }
  trustedUrl(value.url);
  return value as unknown as RuntimeReleaseArtifact;
}

function trustedUrl(raw: string): URL {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new Error("release_url_invalid");
  }
  if (
    url.protocol !== "https:" ||
    url.username !== "" ||
    url.password !== "" ||
    url.hash !== ""
  ) {
    throw new Error("release_url_invalid");
  }
  return url;
}

function safeArchivePath(value: string): boolean {
  if (
    value.length === 0 ||
    value.includes("\0") ||
    value.includes("\\") ||
    path.posix.isAbsolute(value)
  ) {
    return false;
  }
  const withoutTrailingSlash = value.endsWith("/") ? value.slice(0, -1) : value;
  const segments = withoutTrailingSlash.split("/");
  if (
    withoutTrailingSlash.length === 0 ||
    segments.some(
      (segment) => segment === "" || segment === "." || segment === "..",
    )
  ) {
    return false;
  }
  return path.posix.normalize(withoutTrailingSlash) === withoutTrailingSlash;
}

function contentLength(
  headers: Record<string, string | undefined>,
): number | null {
  const raw = headers["content-length"];
  if (raw === undefined) return null;
  if (!/^\d+$/.test(raw)) throw new Error("release_content_length_invalid");
  const value = Number(raw);
  if (!Number.isSafeInteger(value))
    throw new Error("release_content_length_invalid");
  return value;
}

function normalizedHost(value: string): string {
  return value.trim().toLowerCase();
}

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isBuildNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

function isPositiveSafeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0;
}

function isProtocolVersion(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 1;
}

function validTimestamp(value: string): boolean {
  return (
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/.test(value) &&
    !Number.isNaN(Date.parse(value))
  );
}

export async function sha256File(file: string): Promise<string> {
  const digest = createHash("sha256");
  for await (const chunk of createReadStream(file)) digest.update(chunk);
  return digest.digest("hex");
}

export async function readReleaseManifestFile(
  file: string,
): Promise<RuntimeReleaseManifest> {
  return parseRuntimeReleaseManifest(JSON.parse(await readFile(file, "utf8")));
}
