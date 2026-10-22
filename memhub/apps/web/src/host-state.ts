import { MutationObserver, useQuery } from "@tanstack/react-query";
import { connectionHostId, hostMatchesSource } from "./connection-hosts";
import type {
  RecallHostStatus,
  RecallPermissionStatus,
  RecallRemovalFinding,
  RevokeRecallPermissionInput,
  SetRecallPermissionInput,
} from "./global";
import { onClientReset, queryClient } from "./query-client";

const HOSTS_KEY = ["recall-hosts"] as const;
const HOST_WRITE = ["host-write"] as const;
const FRESH_MS = 5_000;
export const HOST_POLL_MS = 3_000;
/** The bridge is IPC with no deadline of its own; a read that never answers
 * must not hold the query (and every later poll) forever. */
export const HOST_READ_TIMEOUT_MS = 10_000;
const ALL_HOSTS = "*";

queryClient.setQueryDefaults(HOSTS_KEY, {
  gcTime: Number.POSITIVE_INFINITY,
  staleTime: FRESH_MS,
});

let readsStarted = 0;
let lastLandedRead = 0;
let writeClock = 0;
const landedAt = new Map<string, number>();
const queues = new Map<string, Promise<unknown>>();
const permissionBridges = new WeakMap<object, HostPermissionBridge>();

onClientReset(() => {
  readsStarted = 0;
  lastLandedRead = 0;
  writeClock = 0;
  landedAt.clear();
  queues.clear();
});

function bridge() {
  return window.oneMemory;
}

export function hostBridgeAvailable() {
  return Boolean(bridge()?.getRecallHosts);
}

/** Which host operations this desktop shell carries at all. */
export function hostCapabilities() {
  const api = bridge();
  return {
    read: Boolean(api?.getRecallHosts),
    install: Boolean(api?.previewRecallInstall),
    readPermission: Boolean(api?.getRecallPermission),
    permission: Boolean(api?.getRecallPermission && api.setRecallPermission),
    revoke: Boolean(api?.revokeRecallPermission),
    probe: Boolean(api?.probeRecallHost),
    skill: Boolean(api?.previewSkillSync && api.applySkillSync),
  };
}

function deadline<T>(read: Promise<T>, signal?: AbortSignal): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const stop = () => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
    };
    const abort = () => {
      stop();
      reject(signal?.reason ?? Error("host_read_aborted"));
    };
    const timer = setTimeout(() => {
      stop();
      reject(Error("host_read_timeout"));
    }, HOST_READ_TIMEOUT_MS);
    if (signal?.aborted) return abort();
    signal?.addEventListener("abort", abort, { once: true });
    read.then(
      (value) => {
        stop();
        resolve(value);
      },
      (reason) => {
        stop();
        reject(reason);
      },
    );
  });
}

/** Revisions only compare within one installation; otherwise there is no order. */
function olderPermission(
  given: RecallPermissionStatus | undefined,
  known: RecallPermissionStatus | undefined,
) {
  const next = given?.receipt;
  const last = known?.receipt;
  return Boolean(
    next &&
      last &&
      next.installationId === last.installationId &&
      next.revision < last.revision,
  );
}

/** A read cannot undo a write that landed after the read began, nor bring back
 * a lower revision of the same installation. */
function keepNewer(
  read: RecallHostStatus,
  known: RecallHostStatus | undefined,
  began: number,
): RecallHostStatus {
  if (!known) return read;
  let host = read;
  if ((landedAt.get(read.hostId) ?? 0) > began)
    host = {
      ...host,
      installed: known.installed,
      ...(known.permission &&
      !olderPermission(known.permission, read.permission)
        ? { permission: known.permission }
        : {}),
    };
  if (olderPermission(host.permission, known.permission))
    host = { ...host, permission: known.permission };
  return host;
}

async function readHosts(signal?: AbortSignal): Promise<RecallHostStatus[]> {
  const api = bridge();
  if (!api?.getRecallHosts) return [];
  const order = ++readsStarted;
  const began = writeClock;
  const next = await deadline(api.getRecallHosts(), signal);
  const current = cachedRecallHosts();
  if (current && order < lastLandedRead) return current;
  lastLandedRead = order;
  if (!current) return next;
  return next.map((host) =>
    keepNewer(
      host,
      current.find((item) => item.hostId === host.hostId),
      began,
    ),
  );
}

/** Always a new read, committed as soon as it lands. */
export async function refreshRecallHosts(signal?: AbortSignal) {
  const next = await readHosts(signal);
  queryClient.setQueryData<RecallHostStatus[]>(HOSTS_KEY, next);
  return next;
}

export function cachedRecallHosts() {
  return queryClient.getQueryData<RecallHostStatus[]>(HOSTS_KEY);
}

export function prefetchRecallHosts() {
  return queryClient.prefetchQuery({
    queryKey: HOSTS_KEY,
    queryFn: ({ signal }) => refreshRecallHosts(signal),
  });
}

/**
 * `poll` opts this page into the one host poll; it pauses while hidden.
 * `read` reads on mount unless a read younger than FRESH_MS is cached.
 */
export function useRecallHosts(
  options: { poll?: boolean; read?: boolean } = {},
) {
  const available = hostBridgeAvailable();
  const poll = Boolean(options.poll && available);
  return useQuery(
    {
      queryKey: HOSTS_KEY,
      queryFn: ({ signal }) => refreshRecallHosts(signal),
      enabled: poll || Boolean(options.read && available),
      refetchInterval: poll ? HOST_POLL_MS : false,
      refetchIntervalInBackground: false,
    },
    queryClient,
  );
}

export function hostConnectionApproved(
  host: Pick<RecallHostStatus, "installed" | "permission"> | null | undefined,
) {
  return Boolean(host?.installed && host.permission?.state === "approved");
}

/** Installed, but whether it may use memory was not read: neither yes nor no. */
export function hostConnectionUnknown(
  host: Pick<RecallHostStatus, "installed" | "permission"> | null | undefined,
) {
  return Boolean(
    host?.installed &&
      (!host.permission || host.permission.state === "unavailable"),
  );
}

/** No revocable credential can be issued to this host (Pi has no MCP channel). */
export function hostConnectionUnsupported(
  host: Pick<RecallHostStatus, "permission"> | null | undefined,
) {
  return host?.permission?.state === "unsupported";
}

export const UNSUPPORTED_NOTE =
  "暂不支持一键插入：它没有 MCP 通道，无法发放可撤销的记忆凭证。";

export function matchHost(
  hosts: readonly RecallHostStatus[] | undefined,
  sourceId: string,
) {
  if (!hosts?.length) return null;
  const hostId = connectionHostId(sourceId) ?? sourceId;
  return (
    hosts.find((host) => host.hostId === hostId) ??
    hosts.find((host) => hostMatchesSource(sourceId, host.hostId)) ??
    null
  );
}

const REASONS: Record<string, string> = {
  host_unsupported: "它没有 MCP 通道，无法发放可撤销的记忆凭证",
  unsupported_host: "当前安装不支持一键插入",
  connection_client_unavailable: "桌面端的连接组件不可用",
  noncanonical_data_dir_host_integration_disabled: "测试实例不接入应用",
  installation_receipt_missing: "安装后没有拿到安装回执",
  permission_not_approved: "记忆使用权限没有获批",
  host_uninstall_modified_files: "配置文件被改过",
  host_uninstall_needs_review: "有无法确认的残留，需要人工核对",
  host_permission_store_unavailable: "权限存储暂不可用",
  host_control_unavailable: "应用控制通道暂不可用",
  host_runtime_app_location_unsafe:
    "请先把 MemHub 拖到“应用程序”文件夹，再接入应用",
  runtime_broker_unavailable: "本机运行时暂不可用",
  host_read_timeout: "桌面端读取接入状态超时",
};

/** Desktop errors arrive as IPC text ending in a code; name the known ones. */
export function hostWriteReason(error: unknown): string | null {
  const text = error instanceof Error ? error.message : String(error ?? "");
  const code = Object.keys(REASONS).find((item) =>
    new RegExp(`\\b${item}\\b`).test(text),
  );
  return code ? (REASONS[code] ?? null) : null;
}

const unverified = new WeakSet<object>();

/** The write failed and the one re-read after it failed too. */
export function hostRereadFailed(error: unknown) {
  return typeof error === "object" && error !== null && unverified.has(error);
}

type HostPatch = Partial<
  Pick<RecallHostStatus, "installed" | "permission" | "selfCheck">
>;

function landHost(hostId: string, patch: HostPatch) {
  landedAt.set(hostId, ++writeClock);
  queryClient.setQueryData<RecallHostStatus[]>(HOSTS_KEY, (hosts) =>
    hosts?.map((host) => {
      if (host.hostId !== hostId) return host;
      const { permission, ...rest } = patch;
      return {
        ...host,
        ...rest,
        ...(permission && !olderPermission(permission, host.permission)
          ? { permission }
          : {}),
      };
    }),
  );
}

async function rereadOnce() {
  try {
    await refreshRecallHosts();
    return true;
  } catch {
    return false;
  }
}

async function rereadAfterFailure(error: Error) {
  if (!(await rereadOnce())) unverified.add(error);
}

/** Writes to one host wait for that host's earlier writes; a write naming no
 * single host waits for, and holds up, every host. */
function serial<T>(keys: string[], run: () => Promise<T>): Promise<T> {
  const waits = keys.includes(ALL_HOSTS)
    ? [...queues.values()]
    : keys.map((key) => queues.get(key));
  const all = queues.get(ALL_HOSTS);
  if (all && !keys.includes(ALL_HOSTS)) waits.push(all);
  const result = Promise.all(waits.map((wait) => wait?.catch(() => {}))).then(
    run,
  );
  const tail = result.then(
    () => {},
    () => {},
  );
  for (const key of keys) queues.set(key, tail);
  void tail.then(() => {
    for (const key of keys) if (queues.get(key) === tail) queues.delete(key);
  });
  return result;
}

async function execute<TVars, TData>(
  write: (vars: TVars) => Promise<TData>,
  land: (data: TData) => void,
  failed: (error: Error) => Promise<void>,
  vars: TVars,
) {
  const observer = new MutationObserver<TData, Error, TVars>(queryClient, {
    mutationKey: HOST_WRITE,
    mutationFn: write,
    onSuccess: land,
    onError: failed,
    gcTime: 0,
  });
  try {
    return await observer.mutate(vars);
  } finally {
    observer.reset();
  }
}

/** Every host write is one mutation, queued behind earlier writes to the same
 * host: receipts land in the cache; a failure re-reads once so pages show what
 * is really there. */
function hostWrite<TVars, TData>(
  keys: (vars: TVars) => string[],
  write: (vars: TVars) => Promise<TData>,
  land: (data: TData) => void,
  failed: (error: Error) => Promise<void> = rereadAfterFailure,
) {
  return (vars: TVars) =>
    serial(keys(vars), () => execute(write, land, failed, vars));
}

export function hostWriteInFlight() {
  return queryClient.isMutating({ mutationKey: HOST_WRITE }) > 0;
}

function sourceHost(sourceId: string) {
  return [connectionHostId(sourceId) ?? sourceId];
}

type Connected = {
  hostId: string;
  installed: boolean;
  permission?: RecallPermissionStatus;
};

async function connect(sourceId: string): Promise<Connected | null> {
  const hostId = connectionHostId(sourceId);
  const api = bridge();
  if (
    !hostId ||
    !api?.previewRecallInstall ||
    !api.installRecall ||
    !api.getRecallPermission ||
    !api.setRecallPermission
  )
    throw Error("unsupported_host");
  const existing = matchHost(await refreshRecallHosts(), sourceId);
  if (hostConnectionUnsupported(existing)) throw Error("host_unsupported");
  if (hostConnectionApproved(existing)) {
    await api.repairRecallCommands?.();
    return null;
  }
  const preview = await api.previewRecallInstall(hostId);
  const installed = await api.installRecall(hostId, preview.fingerprint);
  let permission =
    installed.permission ?? (await api.getRecallPermission(hostId));
  if (permission.receipt?.status !== "approved") {
    const receipt = permission.receipt;
    if (!receipt) throw Error("installation_receipt_missing");
    permission = await api.setRecallPermission(hostId, {
      installationId: receipt.installationId,
      expectedRevision: receipt.revision,
      allowedTools: ["om_recall", "om_conversations", "om_diagnostic_trace"],
      allowedScopes: ["all-apps", `source:${sourceId}`],
      userConfirmed: true,
    });
  }
  if (permission.receipt?.status !== "approved")
    throw Error("permission_not_approved");
  await api.repairRecallCommands?.();
  return { hostId, installed: true, permission };
}

function landConnected(result: Connected | null) {
  if (result)
    landHost(result.hostId, {
      installed: result.installed,
      permission: result.permission,
    });
}

/** Installs and grants one host; resolves only once the grant is approved. */
export const enableHostConnection = hostWrite(
  sourceHost,
  connect,
  landConnected,
);

export type DisconnectOutcome =
  | { state: "removed"; permission?: RecallPermissionStatus }
  | {
      /** Neither off nor failed: something may be left that nobody could
       * settle. The grant is revoked when there was one. */
      state: "review";
      findings: RecallRemovalFinding[];
      permission?: RecallPermissionStatus;
    }
  | {
      state: "revoked";
      configEdited: boolean;
      permission?: RecallPermissionStatus;
    }
  | { state: "failed"; reason: string | null; rereadFailed: boolean };

type Disconnected =
  | Exclude<DisconnectOutcome, { state: "failed" }>
  | { state: "failed"; reason: string | null };

/** Remove the connection; when the files cannot go, still revoke the grant so
 * the host's credential stops reading memory. */
async function disconnect(hostId: string): Promise<Disconnected> {
  const api = bridge();
  if (!api) return { state: "failed", reason: null };
  let removal: unknown;
  let findings: RecallRemovalFinding[] = [];
  try {
    const preview = await api.previewRecallUninstall(hostId);
    findings = preview.review ?? [];
    if (!findings.length) {
      const removed = await api.uninstallRecall(hostId, preview.fingerprint);
      return { state: "removed", permission: removed?.permission };
    }
  } catch (error) {
    removal = error;
  }
  const configEdited =
    removal instanceof Error &&
    /\bhost_uninstall_modified_files\b/.test(removal.message);
  const failed = (error?: unknown) => ({
    state: "failed" as const,
    reason: hostWriteReason(error) ?? hostWriteReason(removal),
  });
  const settled = (permission?: RecallPermissionStatus): Disconnected =>
    findings.length
      ? { state: "review", findings, permission }
      : { state: "revoked", configEdited, permission };
  try {
    const permission = await api.getRecallPermission?.(hostId);
    const receipt = permission?.receipt;
    if (findings.length && permission && !receipt) return settled(permission);
    if (!permission || !receipt || !api.revokeRecallPermission) return failed();
    if (receipt.status === "revoked") return settled(permission);
    const saved = await api.revokeRecallPermission(hostId, {
      installationId: receipt.installationId,
      expectedRevision: receipt.revision,
      userConfirmed: true,
    });
    if (saved.receipt?.status !== "revoked") return failed();
    return settled(saved);
  } catch (error) {
    return failed(error);
  }
}

export const disconnectHost = hostWrite(
  (hostId: string) => [hostId],
  async (
    hostId: string,
  ): Promise<{ hostId: string; outcome: DisconnectOutcome }> => {
    const result = await disconnect(hostId);
    if (result.state !== "failed") return { hostId, outcome: result };
    const rereadFailed = !(await rereadOnce());
    return { hostId, outcome: { ...result, rereadFailed } };
  },
  ({ hostId, outcome }) => {
    if (outcome.state === "removed")
      landHost(hostId, {
        installed: false,
        ...(outcome.permission ? { permission: outcome.permission } : {}),
      });
    else if (outcome.state !== "failed" && outcome.permission)
      landHost(hostId, { permission: outcome.permission });
  },
);

export type MemoryPermissionWrite = {
  hostId: string;
  sourceId: string;
  enabled: boolean;
};

async function writeMemoryPermission({
  hostId,
  sourceId,
  enabled,
}: MemoryPermissionWrite) {
  const api = bridge();
  if (!api) throw Error("bridge_unavailable");
  let installed: boolean | undefined;
  let permission = await api.getRecallPermission?.(hostId);
  if (enabled && !permission?.receipt) {
    const preview = await api.previewRecallInstall(hostId);
    const result = await api.installRecall(hostId, preview.fingerprint);
    installed = result.installed;
    permission = result.permission ?? (await api.getRecallPermission?.(hostId));
  }
  const receipt = permission?.receipt;
  if (!receipt) throw Error("permission_unavailable");
  if (enabled) {
    if (!api.setRecallPermission) throw Error("grant_unavailable");
    const saved = await api.setRecallPermission(hostId, {
      installationId: receipt.installationId,
      expectedRevision: receipt.revision,
      allowedTools: ["om_recall", "om_conversations", "om_diagnostic_trace"],
      allowedScopes: ["all-apps", `source:${sourceId}`],
      userConfirmed: true,
    });
    if (saved.receipt?.status !== "approved") throw Error("grant_failed");
    return { hostId, installed, permission: saved };
  }
  if (!api.revokeRecallPermission) throw Error("revoke_unavailable");
  const saved = await api.revokeRecallPermission(hostId, {
    installationId: receipt.installationId,
    expectedRevision: receipt.revision,
    userConfirmed: true,
  });
  if (saved.receipt?.status === "approved") throw Error("revoke_failed");
  return { hostId, installed, permission: saved };
}

export const setHostMemoryPermission = hostWrite(
  ({ hostId }: MemoryPermissionWrite) => [hostId],
  writeMemoryPermission,
  ({ hostId, installed, permission }) =>
    landHost(hostId, {
      permission,
      ...(installed === undefined ? {} : { installed }),
    }),
);

export function previewHostInstall(hostId: string) {
  const api = bridge();
  if (!api?.previewRecallInstall) return Promise.reject(Error("unsupported"));
  return api.previewRecallInstall(hostId);
}

export function previewHostUninstall(hostId: string) {
  const api = bridge();
  if (!api?.previewRecallUninstall) return Promise.reject(Error("unsupported"));
  return api.previewRecallUninstall(hostId);
}

type ApprovedChange = { hostId: string; fingerprint: string };

/** Applies a previewed install; the receipt lands, the old self-check is void. */
export const installHost = hostWrite(
  ({ hostId }: ApprovedChange) => [hostId],
  async ({ hostId, fingerprint }: ApprovedChange) => {
    const api = bridge();
    if (!api?.installRecall) throw Error("unsupported");
    return api.installRecall(hostId, fingerprint);
  },
  (result) =>
    landHost(result.hostId, {
      installed: result.installed,
      permission: result.permission,
      selfCheck: null,
    }),
);

export const uninstallHost = hostWrite(
  ({ hostId }: ApprovedChange) => [hostId],
  async ({ hostId, fingerprint }: ApprovedChange) => {
    const api = bridge();
    if (!api?.uninstallRecall) throw Error("unsupported");
    return api.uninstallRecall(hostId, fingerprint);
  },
  (result) =>
    landHost(result.hostId, {
      installed: result.installed,
      permission: result.permission,
    }),
);

export function probeHost(hostId: string) {
  const api = bridge();
  if (!api?.probeRecallHost) return Promise.reject(Error("unsupported"));
  return api.probeRecallHost(hostId);
}

export function previewHostSkill(hostId: string) {
  const api = bridge();
  if (!api?.previewSkillSync) return Promise.reject(Error("unsupported"));
  return api.previewSkillSync(hostId);
}

/** The skill receipt says nothing about drift; the host list is re-read. */
export const applyHostSkill = hostWrite(
  ({ hostId }: { hostId: string; fingerprint: string; overwrite: boolean }) => [
    hostId,
  ],
  async ({
    hostId,
    fingerprint,
    overwrite,
  }: {
    hostId: string;
    fingerprint: string;
    overwrite: boolean;
  }) => {
    const api = bridge();
    if (!api?.applySkillSync) throw Error("unsupported");
    const result = await api.applySkillSync(hostId, fingerprint, overwrite);
    await refreshRecallHosts();
    return result;
  },
  () => {},
);

export const repairHostCommands = hostWrite(
  () => [ALL_HOSTS],
  async () => (await bridge()?.repairRecallCommands?.()) ?? [],
  () => {},
  async () => {},
);

/** A fresh permission read; it lands in the shared host state. */
export async function readHostPermission(hostId: string) {
  const read = bridge()?.getRecallPermission;
  if (!read) throw Error("unsupported");
  const permission = await read(hostId);
  if (permission?.hostId === hostId) landHost(hostId, { permission });
  return permission;
}

export const grantHostPermission = hostWrite(
  ({ hostId }: { hostId: string; input: SetRecallPermissionInput }) => [hostId],
  async ({
    hostId,
    input,
  }: {
    hostId: string;
    input: SetRecallPermissionInput;
  }) => {
    const write = bridge()?.setRecallPermission;
    if (!write) throw Error("unsupported");
    return { hostId, permission: await write(hostId, input) };
  },
  ({ hostId, permission }) => {
    if (permission?.hostId === hostId) landHost(hostId, { permission });
  },
);

export const revokeHostPermission = hostWrite(
  ({ hostId }: { hostId: string; input: RevokeRecallPermissionInput }) => [
    hostId,
  ],
  async ({
    hostId,
    input,
  }: {
    hostId: string;
    input: RevokeRecallPermissionInput;
  }) => {
    const write = bridge()?.revokeRecallPermission;
    if (!write) throw Error("unsupported");
    return { hostId, permission: await write(hostId, input) };
  },
  ({ hostId, permission }) => {
    if (permission?.hostId === hostId) landHost(hostId, { permission });
  },
);

export type HostPermissionBridge = {
  read(hostId: string): Promise<RecallPermissionStatus>;
  grant(
    hostId: string,
    input: SetRecallPermissionInput,
  ): Promise<RecallPermissionStatus>;
  revoke(
    hostId: string,
    input: RevokeRecallPermissionInput,
  ): Promise<RecallPermissionStatus>;
};

/** The permission calls, routed through this layer; one object per desktop
 * bridge, so a component can tell when the bridge itself changed. */
export function hostPermissionBridge(): HostPermissionBridge | null {
  const api = bridge();
  if (
    !api?.getRecallPermission ||
    !api.setRecallPermission ||
    !api.revokeRecallPermission
  )
    return null;
  let facade = permissionBridges.get(api);
  if (!facade) {
    facade = {
      read: readHostPermission,
      grant: async (hostId, input) =>
        (await grantHostPermission({ hostId, input })).permission,
      revoke: async (hostId, input) =>
        (await revokeHostPermission({ hostId, input })).permission,
    };
    permissionBridges.set(api, facade);
  }
  return facade;
}
