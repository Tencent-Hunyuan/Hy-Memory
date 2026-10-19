import {
  createHash,
  randomBytes,
  randomUUID,
  timingSafeEqual,
} from "node:crypto";
import { constants, type Stats } from "node:fs";
import {
  chmod,
  lstat,
  mkdir,
  open,
  rename,
  rmdir,
  unlink,
} from "node:fs/promises";
import path from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { inspect } from "node:util";

/**
 * Trusted local control plane, not an MCP tool or an HTTP authentication route.
 * Only the installer may create/rotate credentials; only confirmed user actions
 * may approve/revoke grants. A host label is metadata, never authentication.
 *
 * Use a dedicated directory under a trusted parent on a local filesystem that
 * honors atomic rename and POSIX mode bits. Broader ACLs must be excluded by the
 * installer. This does not defend against malicious same-user processes, a
 * compromised installer/broker, or restoring an old store across restarts.
 * Windows requires an ACL-backed implementation and is deliberately unsupported.
 *
 * Broker integration must authorize every request with its transport secret and
 * the complete effective scopes (including any Core workspace expansion). Core
 * must still enforce source/content permissions. Do not cache these receipts as
 * authority: recheck before publishing long-running results after grant changes.
 * Exact source:<source_id> scopes are independent of all-apps and tool grants.
 * Core requires om_conversations + all-apps + exact source grants for ordinary
 * raw reads. Only the fixed generic-local-mcp grant may use raw-imported-all;
 * Core still checks active sources before releasing raw content.
 * No secret belongs in tool arguments, prompts, URLs, discovery, or logs.
 */
export type HostReadTool =
  | "om_recall"
  | "om_ask"
  | "om_conversations"
  | "om_matters"
  | "om_diagnostic_trace";

export interface HostGrantReceipt {
  readonly installationId: string;
  readonly hostId: string;
  readonly revision: number;
  readonly status: "install-only" | "approved" | "revoked";
  readonly allowedTools: readonly HostReadTool[];
  readonly allowedScopes: readonly string[];
}

export interface IssuedHostCredential {
  readonly receipt: HostGrantReceipt;
  /** Returned once, non-enumerable and redacted by normal JSON/Node inspection. */
  readonly secret: string;
}

export interface VerifiedHostIdentity extends HostGrantReceipt {
  readonly status: "approved";
  readonly storeRevision: number;
}

/** Authenticated installation only; this is not authority to read content. */
export interface VerifiedInstallationIdentity extends HostGrantReceipt {
  readonly status: "install-only" | "approved";
  readonly storeRevision: number;
}

export interface HostGrantMutation {
  installationId: string;
  expectedRevision: number;
}

export interface HostGrantApproval extends HostGrantMutation {
  allowedTools: readonly HostReadTool[];
  allowedScopes: readonly string[];
}

export interface HostGrantStoreOptions {
  lockTimeoutMs?: number;
}

export type HostGrantErrorCode =
  | "host_grant_invalid_input"
  | "host_grant_authentication_failed"
  | "host_grant_not_approved"
  | "host_grant_tool_denied"
  | "host_grant_scope_denied"
  | "host_grant_not_found"
  | "host_grant_revoked"
  | "host_grant_revision_conflict"
  | "host_grant_store_exists"
  | "host_grant_store_unavailable"
  | "host_grant_store_corrupt"
  | "host_grant_store_version_unsupported"
  | "host_grant_store_unsafe"
  | "host_grant_store_busy"
  | "host_grant_store_conflict"
  | "host_grant_store_full"
  | "host_grant_platform_unsupported";

export class HostGrantError extends Error {
  readonly code: HostGrantErrorCode;

  constructor(code: HostGrantErrorCode) {
    // Never attach input, JSON parser errors, filesystem paths, or a raw cause.
    super(code);
    this.name = "HostGrantError";
    this.code = code;
  }
}

interface StoredInstallation {
  installationId: string;
  hostId: string;
  revision: number;
  status: HostGrantReceipt["status"];
  allowedTools: HostReadTool[];
  allowedScopes: string[];
  secretHash: string;
}

interface StoreData {
  schemaVersion: 1;
  storeId: string;
  revision: number;
  installations: StoredInstallation[];
}

const FILE_NAME = "host-grants.json";
const LOCK_NAME = ".mutation-lock";
const MAX_STORE_BYTES = 4 * 1024 * 1024;
const MAX_INSTALLATIONS = 1024;
const MAX_SCOPES = 128;
const GENERIC_LOCAL_MCP_HOST_ID = "generic-local-mcp";
const GENERIC_LOCAL_MCP_TOOLS: readonly HostReadTool[] = [
  "om_recall",
  "om_conversations",
];
const GENERIC_LOCAL_MCP_SCOPES = ["all-apps", "raw-imported-all"];
const TOOL_IDS = new Set<HostReadTool>([
  "om_recall",
  "om_ask",
  "om_conversations",
  "om_matters",
  "om_diagnostic_trace",
]);

export class HostGrantStore {
  readonly #directory: string;
  readonly #lockTimeoutMs: number;
  #directoryIdentity: { dev: number; ino: number } | undefined;
  #storeId: string | undefined;
  #observedRevision = -1;
  #observedDigest: string | undefined;

  private constructor(directory: string, options: HostGrantStoreOptions) {
    if (process.platform === "win32" || typeof process.getuid !== "function") {
      fail("host_grant_platform_unsupported");
    }
    if (
      typeof directory !== "string" ||
      !directory ||
      directory.includes("\0")
    ) {
      fail("host_grant_invalid_input");
    }
    const timeout = options.lockTimeoutMs ?? 2_000;
    if (!Number.isSafeInteger(timeout) || timeout < 0 || timeout > 60_000) {
      fail("host_grant_invalid_input");
    }
    this.#directory = path.resolve(directory);
    this.#lockTimeoutMs = timeout;
  }

  /** Explicit first setup only. An existing directory, even empty, is not reset. */
  static async initialize(
    directory: string,
    options: HostGrantStoreOptions = {},
  ): Promise<HostGrantStore> {
    return guarded(async () => {
      const store = new HostGrantStore(directory, options);
      try {
        await mkdir(store.#directory, { mode: 0o700 });
      } catch (error) {
        if (hasCode(error, "EEXIST")) fail("host_grant_store_exists");
        throw error;
      }
      await chmod(store.#directory, 0o700);
      await store.#checkDirectory();
      await store.#commit({
        schemaVersion: 1,
        storeId: randomUUID(),
        revision: 0,
        installations: [],
      });
      return store;
    });
  }

  /** Normal startup: missing, unreadable, invalid, or old-format stores deny. */
  static async open(
    directory: string,
    options: HostGrantStoreOptions = {},
  ): Promise<HostGrantStore> {
    return guarded(async () => {
      const store = new HostGrantStore(directory, options);
      await store.#read();
      return store;
    });
  }

  async listInstallations(): Promise<readonly HostGrantReceipt[]> {
    return guarded(async () =>
      Object.freeze((await this.#read()).installations.map(receipt)),
    );
  }

  async createInstallation(input: {
    hostId: string;
  }): Promise<IssuedHostCredential> {
    if (!hasKeys(input, ["hostId"]) || !validHostId(input.hostId)) {
      fail("host_grant_invalid_input");
    }
    const hostId = input.hostId;
    return this.#mutate((data) => {
      if (data.installations.length >= MAX_INSTALLATIONS)
        fail("host_grant_store_full");
      let installationId: string;
      do {
        installationId = randomUUID();
      } while (
        data.installations.some(
          (item) => item.installationId === installationId,
        )
      );
      const secret = newSecret(data);
      const item: StoredInstallation = {
        installationId,
        hostId,
        revision: 1,
        status: "install-only",
        allowedTools: [],
        allowedScopes: [],
        secretHash: hashSecret(secret),
      };
      data.installations.push(item);
      return issuedCredential(item, secret);
    });
  }

  /** Initial approval or full replacement, never an implicit union of rights. */
  async approveGrants(input: HostGrantApproval): Promise<HostGrantReceipt> {
    if (
      !hasKeys(input, [
        "installationId",
        "expectedRevision",
        "allowedTools",
        "allowedScopes",
      ]) ||
      !validMutation(input) ||
      !validTools(input.allowedTools) ||
      !validScopes(input.allowedScopes)
    ) {
      fail("host_grant_invalid_input");
    }
    const { installationId, expectedRevision } = input;
    const tools = [...input.allowedTools];
    const scopes = [...input.allowedScopes];
    return this.#mutate((data) => {
      const item = currentInstallation(data, {
        installationId,
        expectedRevision,
      });
      if (item.status === "revoked") fail("host_grant_revoked");
      if (!validApprovedGrant(item.hostId, tools, scopes))
        fail("host_grant_invalid_input");
      item.allowedTools = tools;
      item.allowedScopes = scopes;
      item.status = "approved";
      item.revision = increment(item.revision);
      return receipt(item);
    });
  }

  /** Revocation is terminal. Re-link by creating a new install-only identity. */
  async revoke(input: HostGrantMutation): Promise<HostGrantReceipt> {
    const mutation = mutationInput(input);
    return this.#mutate((data) => {
      const item = currentInstallation(data, mutation);
      item.status = "revoked";
      item.allowedTools = [];
      item.allowedScopes = [];
      item.revision = increment(item.revision);
      return receipt(item);
    });
  }

  /** Replaces just the secret and revision; preserves the exact approved rights. */
  async rotateSecret(input: HostGrantMutation): Promise<IssuedHostCredential> {
    const mutation = mutationInput(input);
    return this.#mutate((data) => {
      const item = currentInstallation(data, mutation);
      if (item.status === "revoked") fail("host_grant_revoked");
      const secret = newSecret(data);
      item.secretHash = hashSecret(secret);
      item.revision = increment(item.revision);
      return issuedCredential(item, secret);
    });
  }

  /** Resolves identity from a random transport credential, not a claimed host ID. */
  async authenticate(secret: string): Promise<VerifiedHostIdentity> {
    const identity = await this.authenticateInstallation(secret);
    if (identity.status !== "approved") fail("host_grant_not_approved");
    return Object.freeze({ ...identity, status: "approved" });
  }

  /** Notification identity only. Install-only never bypasses authenticate/authorize. */
  async authenticateInstallation(
    secret: string,
  ): Promise<VerifiedInstallationIdentity> {
    if (
      typeof secret !== "string" ||
      secret.length !== 51 ||
      !/^omhg_v1_[A-Za-z0-9_-]{43}$/.test(secret)
    ) {
      fail("host_grant_authentication_failed");
    }
    return guarded(async () => {
      const data = await this.#read();
      const digest = Buffer.from(hashSecret(secret), "hex");
      let found: StoredInstallation | undefined;
      for (const item of data.installations) {
        if (timingSafeEqual(digest, Buffer.from(item.secretHash, "hex")))
          found = item;
      }
      if (!found || found.status === "revoked")
        fail("host_grant_authentication_failed");
      return Object.freeze({
        ...receipt(found),
        status: found.status,
        storeRevision: data.revision,
      });
    });
  }

  /** No defaults: scopes must enumerate every scope the downstream read may use. */
  async authorize(
    secret: string,
    request: { tool: string; scopes: readonly string[] },
  ): Promise<VerifiedHostIdentity> {
    if (!hasOnlyKeys(request, ["tool", "scopes"]))
      fail("host_grant_invalid_input");
    if (!isTool(request.tool)) fail("host_grant_tool_denied");
    if (!validScopes(request.scopes)) fail("host_grant_scope_denied");
    const tool = request.tool;
    const scopes = [...request.scopes];
    const identity = await this.authenticate(secret);
    if (!identity.allowedTools.includes(tool)) fail("host_grant_tool_denied");
    if (!scopes.every((scope) => identity.allowedScopes.includes(scope)))
      fail("host_grant_scope_denied");
    if (
      scopes.includes("raw-imported-all") &&
      (tool !== "om_conversations" || !isGenericLocalMcpGrant(identity))
    )
      fail("host_grant_scope_denied");
    return identity;
  }

  async #checkDirectory(): Promise<void> {
    const info = await lstat(this.#directory);
    checkPrivate(info, "directory");
    if (
      this.#directoryIdentity &&
      (info.dev !== this.#directoryIdentity.dev ||
        info.ino !== this.#directoryIdentity.ino)
    ) {
      fail("host_grant_store_conflict");
    }
    this.#directoryIdentity = { dev: info.dev, ino: info.ino };
  }

  async #read(): Promise<StoreData> {
    const deadline = performance.now() + this.#lockTimeoutMs;
    for (;;) {
      await this.#checkDirectory();
      const file = path.join(this.#directory, FILE_NAME);
      checkPrivate(await lstat(file), "file");
      const handle = await open(
        file,
        constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK,
      );
      try {
        const info = await handle.stat();
        // On Linux an atomic rename over this path sets the old, still-open
        // inode's link count to zero. That is a normal concurrent commit, not
        // an unsafe store. Close this stale handle and validate the new path.
        if (info.nlink === 0) {
          if (performance.now() >= deadline) fail("host_grant_store_busy");
          continue;
        }
        checkPrivate(info, "file");
        if (info.size > MAX_STORE_BYTES) fail("host_grant_store_corrupt");
        const data = parseStore(await handle.readFile("utf8"));
        this.#observe(data);
        return data;
      } finally {
        await handle.close();
      }
    }
  }

  #observe(data: StoreData): void {
    const digest = createHash("sha256")
      .update(JSON.stringify(data))
      .digest("hex");
    if (
      (this.#storeId !== undefined && data.storeId !== this.#storeId) ||
      data.revision < this.#observedRevision ||
      (data.revision === this.#observedRevision &&
        digest !== this.#observedDigest)
    ) {
      fail("host_grant_store_conflict");
    }
    this.#storeId = data.storeId;
    this.#observedRevision = data.revision;
    this.#observedDigest = digest;
  }

  async #mutate<T>(operation: (data: StoreData) => T): Promise<T> {
    return guarded(async () => {
      await this.#acquireLock();
      try {
        const data = await this.#read();
        const previousRevision = data.revision;
        const result = operation(data);
        data.revision = increment(previousRevision);
        await this.#commit(data, previousRevision);
        return result;
      } finally {
        await rmdir(path.join(this.#directory, LOCK_NAME));
      }
    });
  }

  async #acquireLock(): Promise<void> {
    const deadline = performance.now() + this.#lockTimeoutMs;
    const lock = path.join(this.#directory, LOCK_NAME);
    for (;;) {
      await this.#checkDirectory();
      try {
        await mkdir(lock, { mode: 0o700 });
        return;
      } catch (error) {
        if (!hasCode(error, "EEXIST")) throw error;
      }
      // Never steal a timed-out lock: a paused writer may still own it. A crash
      // requires explicit recovery with all writers stopped, not a store reset.
      try {
        checkPrivate(await lstat(lock), "directory");
      } catch (error) {
        if (!hasCode(error, "ENOENT")) throw error;
      }
      if (performance.now() >= deadline) fail("host_grant_store_busy");
      await delay(10);
    }
  }

  async #commit(data: StoreData, previousRevision?: number): Promise<void> {
    const text = `${JSON.stringify(data)}\n`;
    if (Buffer.byteLength(text) > MAX_STORE_BYTES)
      fail("host_grant_store_full");
    const temporary = path.join(
      this.#directory,
      `.host-grants-${randomUUID()}.tmp`,
    );
    let created = false;
    try {
      const handle = await open(temporary, "wx", 0o600);
      created = true;
      try {
        await handle.chmod(0o600);
        await handle.writeFile(text, "utf8");
        await handle.sync();
      } finally {
        await handle.close();
      }
      if (previousRevision !== undefined) {
        const current = await this.#read();
        if (
          current.revision !== previousRevision ||
          current.storeId !== data.storeId
        )
          fail("host_grant_store_conflict");
      }
      await rename(temporary, path.join(this.#directory, FILE_NAME));
      created = false;
      const directory = await open(
        this.#directory,
        constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW,
      );
      try {
        await directory.sync();
      } finally {
        await directory.close();
      }
      this.#observe(data);
    } finally {
      if (created) await unlink(temporary);
    }
  }
}

function receipt(item: StoredInstallation): HostGrantReceipt {
  return Object.freeze({
    installationId: item.installationId,
    hostId: item.hostId,
    revision: item.revision,
    status: item.status,
    allowedTools: Object.freeze([...item.allowedTools]),
    allowedScopes: Object.freeze([...item.allowedScopes]),
  });
}

function issuedCredential(
  item: StoredInstallation,
  secret: string,
): IssuedHostCredential {
  const result = { receipt: receipt(item) } as IssuedHostCredential;
  Object.defineProperty(result, "secret", { value: secret, enumerable: false });
  Object.defineProperty(result, inspect.custom, {
    value: () => ({ receipt: result.receipt, secret: "[REDACTED]" }),
  });
  return Object.freeze(result);
}

function hashSecret(secret: string): string {
  // High-entropy random credentials, not human passwords; no recoverable secret.
  return createHash("sha256").update(secret).digest("hex");
}

function newSecret(data: StoreData): string {
  let secret: string;
  do {
    secret = `omhg_v1_${randomBytes(32).toString("base64url")}`;
  } while (
    data.installations.some((item) => item.secretHash === hashSecret(secret))
  );
  return secret;
}

function currentInstallation(
  data: StoreData,
  input: HostGrantMutation,
): StoredInstallation {
  const item = data.installations.find(
    (entry) => entry.installationId === input.installationId,
  );
  if (!item) fail("host_grant_not_found");
  if (item.revision !== input.expectedRevision)
    fail("host_grant_revision_conflict");
  return item;
}

function mutationInput(input: HostGrantMutation): HostGrantMutation {
  if (
    !hasKeys(input, ["installationId", "expectedRevision"]) ||
    !validMutation(input)
  )
    fail("host_grant_invalid_input");
  return {
    installationId: input.installationId,
    expectedRevision: input.expectedRevision,
  };
}

function validMutation(input: HostGrantMutation): boolean {
  return isUuid(input.installationId) && isRevision(input.expectedRevision, 1);
}

function validHostId(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length <= 64 &&
    value === value.trim() &&
    /^[a-z][a-z0-9-]*$/.test(value)
  );
}

function isUuid(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length === 36 &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(
      value,
    )
  );
}

function isRevision(value: unknown, minimum: number): value is number {
  return (
    typeof value === "number" && Number.isSafeInteger(value) && value >= minimum
  );
}

function increment(value: number): number {
  if (value >= Number.MAX_SAFE_INTEGER) fail("host_grant_store_conflict");
  return value + 1;
}

function isTool(value: unknown): value is HostReadTool {
  return typeof value === "string" && TOOL_IDS.has(value as HostReadTool);
}

function validTools(value: unknown): value is HostReadTool[] {
  return (
    Array.isArray(value) &&
    value.length > 0 &&
    value.length <= TOOL_IDS.size &&
    [...value].every(isTool) &&
    new Set(value).size === value.length
  );
}

export function isGenericLocalMcpGrant(
  identity: Pick<HostGrantReceipt, "hostId" | "allowedTools" | "allowedScopes">,
): boolean {
  return (
    identity.hostId === GENERIC_LOCAL_MCP_HOST_ID &&
    identity.allowedTools.length === GENERIC_LOCAL_MCP_TOOLS.length &&
    GENERIC_LOCAL_MCP_TOOLS.every((tool) =>
      identity.allowedTools.includes(tool),
    ) &&
    identity.allowedScopes.length === GENERIC_LOCAL_MCP_SCOPES.length &&
    GENERIC_LOCAL_MCP_SCOPES.every((scope) =>
      identity.allowedScopes.includes(scope),
    )
  );
}

function validApprovedGrant(
  hostId: string,
  tools: readonly HostReadTool[],
  scopes: readonly string[],
): boolean {
  if (hostId === GENERIC_LOCAL_MCP_HOST_ID)
    return isGenericLocalMcpGrant({
      hostId,
      allowedTools: tools,
      allowedScopes: scopes,
    });
  return !scopes.includes("raw-imported-all");
}

function validScopes(value: unknown): value is string[] {
  return (
    Array.isArray(value) &&
    value.length > 0 &&
    value.length <= MAX_SCOPES &&
    [...value].every(
      (scope) =>
        typeof scope === "string" &&
        (scope === "all-apps" ||
          scope === "raw-imported-all" ||
          (scope.length === 77 && /^workspace:v1:[0-9a-f]{64}$/.test(scope)) ||
          (scope.length >= 8 &&
            scope.length <= 135 &&
            scope === scope.trim() &&
            /^source:[a-z0-9][a-z0-9-]{0,127}$/.test(scope))),
    ) &&
    new Set(value).size === value.length
  );
}

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function hasOnlyKeys(
  value: unknown,
  keys: string[],
): value is Record<string, unknown> {
  return (
    isObject(value) && Object.keys(value).every((key) => keys.includes(key))
  );
}

function hasKeys(
  value: unknown,
  keys: string[],
): value is Record<string, unknown> {
  return (
    hasOnlyKeys(value, keys) && keys.every((key) => Object.hasOwn(value, key))
  );
}

function parseStore(raw: string): StoreData {
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    fail("host_grant_store_corrupt");
  }
  if (
    isObject(data) &&
    typeof data.schemaVersion === "number" &&
    data.schemaVersion !== 1
  )
    fail("host_grant_store_version_unsupported");
  if (
    !hasKeys(data, ["schemaVersion", "storeId", "revision", "installations"]) ||
    data.schemaVersion !== 1 ||
    !isUuid(data.storeId) ||
    !isRevision(data.revision, 0) ||
    !Array.isArray(data.installations) ||
    data.installations.length > MAX_INSTALLATIONS ||
    data.installations.length > data.revision
  )
    fail("host_grant_store_corrupt");
  const ids = new Set<string>();
  const hashes = new Set<string>();
  for (const item of data.installations) {
    if (
      !hasKeys(item, [
        "installationId",
        "hostId",
        "revision",
        "status",
        "allowedTools",
        "allowedScopes",
        "secretHash",
      ]) ||
      !isUuid(item.installationId) ||
      !validHostId(item.hostId) ||
      !isRevision(item.revision, 1) ||
      item.revision > data.revision ||
      typeof item.secretHash !== "string" ||
      item.secretHash.length !== 64 ||
      !/^[0-9a-f]{64}$/.test(item.secretHash) ||
      ids.has(item.installationId) ||
      hashes.has(item.secretHash)
    )
      fail("host_grant_store_corrupt");
    if (item.status === "approved") {
      if (
        !validTools(item.allowedTools) ||
        !validScopes(item.allowedScopes) ||
        !validApprovedGrant(item.hostId, item.allowedTools, item.allowedScopes)
      )
        fail("host_grant_store_corrupt");
    } else if (item.status === "install-only" || item.status === "revoked") {
      if (
        !Array.isArray(item.allowedTools) ||
        item.allowedTools.length !== 0 ||
        !Array.isArray(item.allowedScopes) ||
        item.allowedScopes.length !== 0
      )
        fail("host_grant_store_corrupt");
    } else {
      fail("host_grant_store_corrupt");
    }
    ids.add(item.installationId);
    hashes.add(item.secretHash);
  }
  return data as unknown as StoreData;
}

function checkPrivate(info: Stats, kind: "directory" | "file"): void {
  if (
    (kind === "directory"
      ? !info.isDirectory()
      : !info.isFile() || info.nlink !== 1) ||
    info.uid !== process.getuid?.() ||
    (info.mode & 0o7777) !== (kind === "directory" ? 0o700 : 0o600)
  )
    fail("host_grant_store_unsafe");
}

function hasCode(error: unknown, code: string): boolean {
  return isObject(error) && error.code === code;
}

function fail(code: HostGrantErrorCode): never {
  throw new HostGrantError(code);
}

async function guarded<T>(operation: () => Promise<T>): Promise<T> {
  try {
    return await operation();
  } catch (error) {
    if (error instanceof HostGrantError) throw error;
    fail("host_grant_store_unavailable");
  }
}
