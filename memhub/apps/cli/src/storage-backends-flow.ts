/**
 * `memhub settings backends [show|set|test|reset]`.
 *
 * Which graph and vector stores the SDK keeps this home's memories in
 * (docs/contracts/storage-backends-v1.md). The default is the embedded pair
 * every MemHub has always used, Kuzu + Chroma. Only the CLI and the API change
 * it; the App shows it read-only.
 *
 * Switching moves nothing: the memories already written stay in the store
 * they were written to, the new store starts as it is, and switching back
 * shows them again. So `set` and `reset` print their plan (with the
 * connection test for `set`) first and need `--yes`, like `settings mode`.
 * Secrets are read from files only (`-` = stdin), never from the command line.
 */

import type { CoreReader } from "./core-client.js";
import type {
  CoreDeleteOperations,
  CoreOperations,
  CorePutOperations,
} from "./core-operations.js";
import { PRODUCT } from "./version.js";

export const GRAPH_PROVIDERS = ["kuzu", "neo4j", "memgraph"] as const;
export const VECTOR_PROVIDERS = ["chroma", "qdrant", "faiss"] as const;
export type GraphProvider = (typeof GRAPH_PROVIDERS)[number];
export type VectorProvider = (typeof VECTOR_PROVIDERS)[number];

const PATH = "/api/v1/settings/storage-backends";
const TEST_PATH = "/api/v1/settings/storage-backends/test";

export interface GraphOptions {
  provider: GraphProvider;
  url?: string;
  username?: string;
  database?: string;
  passwordFile?: string;
}

export interface VectorOptions {
  provider: VectorProvider;
  host?: string;
  port?: number;
  collectionPrefix?: string;
  apiKeyFile?: string;
}

export type BackendsCommand =
  | { kind: "show" }
  | { kind: "reset"; confirmed: boolean }
  | {
      kind: "set" | "test";
      graph?: GraphOptions;
      vector?: VectorOptions;
      adoptExistingGraph: boolean;
      confirmed: boolean;
      /** The arguments as given, to repeat them in the `--yes` next step. */
      argv: string[];
    };

const VALUE_FLAGS = new Set([
  "--graph",
  "--graph-url",
  "--graph-user",
  "--graph-database",
  "--graph-password-file",
  "--vector",
  "--vector-host",
  "--vector-port",
  "--vector-prefix",
  "--vector-api-key-file",
]);

/** Arguments after `settings backends`; null = usage. */
export function parseBackendsCommand(
  values: readonly string[],
): BackendsCommand | null {
  const rest = values.filter((value) => value !== "--json");
  if (rest.length === 0) return { kind: "show" };
  const [verb, ...tail] = rest;
  if (verb === "show") return tail.length === 0 ? { kind: "show" } : null;
  if (verb === "reset") {
    if (tail.length === 0) return { kind: "reset", confirmed: false };
    if (tail.length === 1 && tail[0] === "--yes")
      return { kind: "reset", confirmed: true };
    return null;
  }
  if (verb !== "set" && verb !== "test") return null;
  const flags = new Map<string, string>();
  let confirmed = false;
  let adopt = false;
  for (let index = 0; index < tail.length; index += 1) {
    const flag = tail[index];
    if (flag === "--yes" && verb === "set" && !confirmed) {
      confirmed = true;
      continue;
    }
    if (flag === "--adopt-existing-graph" && verb === "set" && !adopt) {
      adopt = true;
      continue;
    }
    if (!VALUE_FLAGS.has(flag) || flags.has(flag)) return null;
    const value = tail[index + 1];
    if (value === undefined || (value.startsWith("--") && value !== "-")) {
      return null;
    }
    flags.set(flag, value);
    index += 1;
  }
  const graphProvider = flags.get("--graph");
  const graphFlags = [
    "--graph-url",
    "--graph-user",
    "--graph-database",
    "--graph-password-file",
  ].filter((flag) => flags.has(flag));
  if (graphProvider === undefined && graphFlags.length > 0) return null;
  let graph: GraphOptions | undefined;
  if (graphProvider !== undefined) {
    if (!(GRAPH_PROVIDERS as readonly string[]).includes(graphProvider))
      return null;
    graph = {
      provider: graphProvider as GraphProvider,
      url: flags.get("--graph-url"),
      username: flags.get("--graph-user"),
      database: flags.get("--graph-database"),
      passwordFile: flags.get("--graph-password-file"),
    };
  }
  const vectorProvider = flags.get("--vector");
  const vectorFlags = [
    "--vector-host",
    "--vector-port",
    "--vector-prefix",
    "--vector-api-key-file",
  ].filter((flag) => flags.has(flag));
  if (vectorProvider === undefined && vectorFlags.length > 0) return null;
  let vector: VectorOptions | undefined;
  if (vectorProvider !== undefined) {
    if (!(VECTOR_PROVIDERS as readonly string[]).includes(vectorProvider))
      return null;
    const rawPort = flags.get("--vector-port");
    let port: number | undefined;
    if (rawPort !== undefined) {
      if (!/^\d{1,5}$/.test(rawPort)) return null;
      port = Number(rawPort);
      if (port < 1 || port > 65535) return null;
    }
    vector = {
      provider: vectorProvider as VectorProvider,
      host: flags.get("--vector-host"),
      port,
      collectionPrefix: flags.get("--vector-prefix"),
      apiKeyFile: flags.get("--vector-api-key-file"),
    };
  }
  // Both secrets from stdin cannot be told apart.
  if (graph?.passwordFile === "-" && vector?.apiKeyFile === "-") return null;
  if (verb === "set" && graph === undefined && vector === undefined)
    return null;
  return {
    kind: verb,
    graph,
    vector,
    adoptExistingGraph: adopt,
    confirmed,
    argv: tail.filter((value) => value !== "--yes"),
  };
}

/** The secrets a `set`/`test` names, read by the caller (`readSecret`). */
export interface BackendSecrets {
  graphPassword?: string;
  vectorApiKey?: string;
}

/** Read the secret files a command names; null = one could not be read. */
export async function readBackendSecrets(
  command: BackendsCommand,
  readSecret: (path: string) => Promise<string | undefined>,
): Promise<BackendSecrets | null> {
  if (command.kind !== "set" && command.kind !== "test") return {};
  const secrets: BackendSecrets = {};
  if (command.graph?.passwordFile !== undefined) {
    const value = await readSecret(command.graph.passwordFile);
    if (value === undefined) return null;
    secrets.graphPassword = value;
  }
  if (command.vector?.apiKeyFile !== undefined) {
    const value = await readSecret(command.vector.apiKeyFile);
    if (value === undefined) return null;
    secrets.vectorApiKey = value;
  }
  return secrets;
}

export interface BackendProbe {
  provider: string;
  ok: boolean;
  state: "ok" | "failed" | "local";
  code?: string | null;
  error_type?: string | null;
  existing_memory_nodes?: number | null;
  collections_in_namespace?: number | null;
  latency_ms?: number | null;
}

export interface StorageProbe {
  ok: boolean;
  graph: BackendProbe;
  vector: BackendProbe;
}

export interface Capability {
  state: "works" | "skipped" | "refused";
  code?: string | null;
}

export interface BackendsState {
  graph: {
    provider: GraphProvider;
    url?: string | null;
    username?: string | null;
    database?: string | null;
    has_password: boolean;
    driver_available: boolean;
  };
  vector: {
    provider: VectorProvider;
    host?: string | null;
    port?: number | null;
    collection_prefix?: string | null;
    has_api_key: boolean;
    driver_available: boolean;
  };
  is_default: boolean;
  label: string;
  namespace?: string | null;
  capabilities: Record<string, Capability>;
  health?: StorageProbe | null;
  changed?: boolean;
  previous_label?: string;
  notice?: string | null;
}

function isProbe(value: unknown): value is StorageProbe {
  if (!value || typeof value !== "object") return false;
  const item = value as Record<string, unknown>;
  const side = (part: unknown) =>
    !!part &&
    typeof part === "object" &&
    typeof (part as BackendProbe).provider === "string" &&
    typeof (part as BackendProbe).ok === "boolean";
  return typeof item.ok === "boolean" && side(item.graph) && side(item.vector);
}

function backendsState(value: unknown): BackendsState | null {
  if (!value || typeof value !== "object") return null;
  const item = value as Record<string, unknown>;
  const graph = item.graph as Record<string, unknown> | undefined;
  const vector = item.vector as Record<string, unknown> | undefined;
  if (
    !graph ||
    !vector ||
    !(GRAPH_PROVIDERS as readonly unknown[]).includes(graph.provider) ||
    !(VECTOR_PROVIDERS as readonly unknown[]).includes(vector.provider) ||
    typeof item.is_default !== "boolean" ||
    typeof item.label !== "string" ||
    !item.capabilities ||
    typeof item.capabilities !== "object"
  ) {
    return null;
  }
  if (item.health != null && !isProbe(item.health)) return null;
  return item as unknown as BackendsState;
}

export type BackendsDocument = {
  schema_version: 1;
  product: string;
  command: "settings backends";
  verb: "show" | "set" | "test" | "reset";
} & (
  | { state: "ok"; backends: BackendsState }
  | {
      state: "tested";
      current_label: string;
      target_label: string;
      probe: StorageProbe;
      graph_adoption_required: boolean;
    }
  | {
      state: "preview";
      backends: BackendsState;
      target_label: string;
      probe: StorageProbe | null;
      notice: "existing_memories_not_moved";
      applied: false;
      next_step: string;
    }
  | { state: "unchanged"; backends: BackendsState }
  | {
      state: "committed";
      backends: BackendsState;
      changed: boolean;
      previous_label: string;
      notice: string | null;
    }
  | {
      state: "probe_failed";
      code: string;
      probe: StorageProbe;
      next_step: string;
    }
  | {
      state: "rejected" | "unknown" | "unavailable";
      code: string;
      probe?: StorageProbe;
      next_step?: string;
    }
);

type Base = Pick<
  BackendsDocument,
  "schema_version" | "product" | "command" | "verb"
>;

function base(verb: BackendsDocument["verb"]): Base {
  return {
    schema_version: 1,
    product: PRODUCT,
    command: "settings backends",
    verb,
  };
}

/** The request body Core takes for `set`/`test` (contract section 2). */
export function backendsBody(
  command: Extract<BackendsCommand, { kind: "set" | "test" }>,
  secrets: BackendSecrets,
): Record<string, unknown> {
  const body: Record<string, unknown> = {};
  if (command.graph) {
    const graph: Record<string, unknown> = {
      provider: command.graph.provider,
    };
    if (command.graph.url !== undefined) graph.url = command.graph.url;
    if (command.graph.username !== undefined)
      graph.username = command.graph.username;
    if (command.graph.database !== undefined)
      graph.database = command.graph.database;
    if (secrets.graphPassword !== undefined)
      graph.password = secrets.graphPassword;
    body.graph = graph;
  }
  if (command.vector) {
    const vector: Record<string, unknown> = {
      provider: command.vector.provider,
    };
    if (command.vector.host !== undefined) vector.host = command.vector.host;
    if (command.vector.port !== undefined) vector.port = command.vector.port;
    if (command.vector.collectionPrefix !== undefined)
      vector.collection_prefix = command.vector.collectionPrefix;
    if (secrets.vectorApiKey !== undefined)
      vector.api_key = secrets.vectorApiKey;
    body.vector = vector;
  }
  if (command.kind === "set" && command.adoptExistingGraph)
    body.adopt_existing_graph = true;
  return body;
}

function failedCode(probe: StorageProbe): string {
  const side = probe.graph.ok ? probe.vector : probe.graph;
  return side.code ?? "storage_backend_probe_failed";
}

function refusalProbe(body: unknown): StorageProbe | undefined {
  const probe =
    body && typeof body === "object"
      ? (body as Record<string, unknown>).probe
      : undefined;
  return isProbe(probe) ? probe : undefined;
}

export async function runBackendsCommand(
  command: BackendsCommand,
  reader: CoreReader,
  operations: CoreOperations & CorePutOperations & CoreDeleteOperations,
  secrets: BackendSecrets = {},
): Promise<BackendsDocument> {
  const verb = command.kind;
  const read = await reader<unknown>(PATH);
  if (read.state !== "ok") {
    return { ...base(verb), state: "unavailable", code: read.code };
  }
  const current = backendsState(read.data);
  if (current === null) {
    return { ...base(verb), state: "unavailable", code: "response_invalid" };
  }
  if (command.kind === "show") {
    return { ...base(verb), state: "ok", backends: current };
  }
  if (command.kind === "reset") {
    if (current.is_default && !current.graph.has_password) {
      return { ...base(verb), state: "unchanged", backends: current };
    }
    if (!command.confirmed) {
      return {
        ...base(verb),
        state: "preview",
        backends: current,
        target_label: "Kuzu + Chroma",
        probe: null,
        notice: "existing_memories_not_moved",
        applied: false,
        next_step: `${PRODUCT} settings backends reset --yes`,
      };
    }
    const result = await operations.delete<unknown>(PATH);
    return committedOrRefused(verb, result);
  }
  const body = backendsBody(command, secrets);
  const tested = await operations.post<unknown>(TEST_PATH, body);
  if (tested.state !== "committed") {
    return {
      ...base(verb),
      state: tested.state,
      code: tested.code,
      ...nextStepFor(tested.code),
    };
  }
  const report = tested.data as Record<string, unknown> | null;
  const probe = report?.probe;
  if (!isProbe(probe) || typeof report?.label !== "string") {
    return { ...base(verb), state: "unknown", code: "response_invalid" };
  }
  const adoption = report.graph_adoption_required === true;
  if (command.kind === "test") {
    return {
      ...base(verb),
      state: "tested",
      current_label: current.label,
      target_label: report.label,
      probe,
      graph_adoption_required: adoption,
    };
  }
  if (!probe.ok) {
    const code = failedCode(probe);
    return {
      ...base(verb),
      state: "probe_failed",
      code,
      probe,
      next_step: NEXT_STEP[code] ?? NEXT_STEP.storage_backend_probe_failed,
    };
  }
  if (adoption && !command.adoptExistingGraph) {
    return {
      ...base(verb),
      state: "rejected",
      code: "storage_backend_graph_not_empty",
      probe,
      next_step: NEXT_STEP.storage_backend_graph_not_empty,
    };
  }
  if (!command.confirmed) {
    return {
      ...base(verb),
      state: "preview",
      backends: current,
      target_label: report.label,
      probe,
      notice: "existing_memories_not_moved",
      applied: false,
      next_step: [PRODUCT, "settings", "backends", "set", ...command.argv]
        .concat("--yes")
        .join(" "),
    };
  }
  const result = await operations.put<unknown>(PATH, body);
  return committedOrRefused(verb, result);
}

function committedOrRefused(
  verb: BackendsDocument["verb"],
  result: Awaited<ReturnType<CorePutOperations["put"]>>,
): BackendsDocument {
  if (result.state !== "committed") {
    const probe =
      result.state === "rejected" ? refusalProbe(result.body) : undefined;
    return {
      ...base(verb),
      state: result.state,
      code: result.code,
      ...(probe ? { probe } : {}),
      ...nextStepFor(result.code),
    };
  }
  const changed = backendsState(result.data);
  if (changed === null) {
    return { ...base(verb), state: "unknown", code: "response_invalid" };
  }
  return {
    ...base(verb),
    state: "committed",
    backends: changed,
    changed: changed.changed === true,
    previous_label: changed.previous_label ?? changed.label,
    notice: changed.notice ?? null,
  };
}

function nextStepFor(code: string): { next_step?: string } {
  const step = NEXT_STEP[code];
  return step ? { next_step: step } : {};
}

export function backendsExitCode(document: BackendsDocument): number {
  if (document.state === "tested") return document.probe.ok ? 0 : 1;
  return ["ok", "preview", "unchanged", "committed"].includes(document.state)
    ? 0
    : 1;
}

// ---------------------------------------------------------------------------
// Wording
// ---------------------------------------------------------------------------

const NOT_MOVED = [
  "已有的记忆仍保存在原来的存储后端里，不会被迁移到新后端；新后端从它现在的内容开始。",
  "原来的数据不会被删除或改写；改回原来的后端（memhub settings backends reset 或再次 set），这些记忆就会重新出现。",
];

const NEXT_STEP: Record<string, string> = {
  storage_backend_graph_not_empty:
    "这个图数据库里已经有记忆图谱（可能属于另一个 MemHub，图谱没有按资料空间隔离）。确认要接管它，再加 --adopt-existing-graph；否则换一个专用的 database。",
  storage_backend_unreachable:
    "连接不上存储服务：检查地址、端口、防火墙和服务是否在运行，然后用 memhub settings backends test 重试。",
  storage_backend_auth_failed:
    "用户名、密码或 API key 不正确：检查 --graph-user / --graph-password-file / --vector-api-key-file。",
  storage_backend_database_not_found:
    "图数据库里没有这个 database：检查 --graph-database，或不写它使用服务器的默认 database。",
  storage_backend_driver_missing:
    "当前安装的 MemHub 没有这个存储后端的驱动；请升级 MemHub。",
  storage_backend_probe_failed:
    "连接测试没有通过；用 memhub settings backends test 查看详情。",
  storage_backend_faiss_unsupported:
    "MemHub 不支持 FAISS 向量库：多个 SDK 客户端会互相覆盖写入。请用 chroma（默认）或 qdrant。",
  storage_backend_vector_host_required:
    "qdrant 需要服务器地址：加 --vector-host <host>（可选 --vector-port，默认 6333）。MemHub 不支持 Qdrant 的嵌入式本地模式。",
  storage_backend_vector_host_invalid: "--vector-host 不是有效的主机名或地址。",
  storage_backend_vector_port_invalid: "--vector-port 必须在 1–65535 之间。",
  storage_backend_vector_prefix_invalid:
    "--vector-prefix 只能是小写字母、数字、- 和 _，以字母或数字开头，最长 32 个字符。",
  storage_backend_vector_chroma_takes_no_connection:
    "chroma 是本机内置的向量库，不需要 --vector-host / --vector-port / --vector-prefix / --vector-api-key-file。",
  storage_backend_graph_kuzu_takes_no_connection:
    "kuzu 是本机内置的图数据库，不需要 --graph-url / --graph-user / --graph-database / --graph-password-file。",
  storage_backend_graph_url_required:
    "neo4j / memgraph 需要 bolt 地址：加 --graph-url bolt://<host>:7687。",
  storage_backend_graph_url_invalid:
    "--graph-url 必须是 bolt:// 或 neo4j:// 地址，不带路径；用户名写在 --graph-user，密码放进 --graph-password-file，不要写进地址。",
  storage_backend_graph_username_invalid:
    "--graph-user 长度应为 1–128 个字符。",
  storage_backend_graph_database_invalid:
    "--graph-database 只能是字母、数字、.、- 和 _，最长 63 个字符。",
  embedding_maintenance_in_progress:
    "向量模型切换或启动时的向量空间扫描正在进行；等它结束（memhub models switch status）后再改。",
  memory_lifecycle_busy: "记忆进化或知识图谱补建正在进行；等它结束后再改。",
  secure_storage_unavailable:
    "系统钥匙串暂时无法读写密码或 API key；解锁钥匙串后重试。",
};

const CAPABILITY_LABEL: Record<string, string> = {
  embedding_switch: "更换向量模型",
  backup: "备份",
  restore: "恢复",
  graph_export: "图谱导出 / 重建（graph-width）",
  graph_view: "记忆图谱视图",
  graph_width_check: "图谱宽度检查",
  embedding_space_scan: "启动时向量空间扫描与合并",
  graph_open_guard: "图谱打开保护与隔离",
  upgrade_migration: "升级迁移",
};

const CAPABILITY_STATE: Record<Capability["state"], string> = {
  works: "可用",
  skipped: "跳过",
  refused: "不支持",
};

function probeSide(name: string, side: BackendProbe): string {
  if (side.state === "local") return `${name}（${side.provider}）：本机文件`;
  if (!side.ok) {
    return `${name}（${side.provider}）：连接失败 ${side.code ?? "storage_backend_probe_failed"}`;
  }
  const extra: string[] = [];
  if (typeof side.latency_ms === "number") extra.push(`${side.latency_ms} ms`);
  if (typeof side.existing_memory_nodes === "number")
    extra.push(`已有 ${side.existing_memory_nodes} 个记忆节点`);
  if (typeof side.collections_in_namespace === "number")
    extra.push(`本资料空间已有 ${side.collections_in_namespace} 个集合`);
  return `${name}（${side.provider}）：连接正常${extra.length ? `（${extra.join("，")}）` : ""}`;
}

function probeLines(probe: StorageProbe): string[] {
  return [
    `连接测试：${probe.ok ? "通过" : "未通过"}`,
    `- ${probeSide("图数据库", probe.graph)}`,
    `- ${probeSide("向量库", probe.vector)}`,
  ];
}

function stateLines(state: BackendsState): string[] {
  const origin = state.is_default ? "默认" : "通过 CLI 配置";
  const lines = [`存储后端：${state.label}（${origin}）`];
  const graph = state.graph;
  if (graph.provider === "kuzu") {
    lines.push("图数据库：kuzu（本机内置，ultra 模式使用）");
  } else {
    lines.push(
      `图数据库：${graph.provider} ${graph.url ?? ""}${graph.database ? `，database ${graph.database}` : ""}${graph.username ? `，用户 ${graph.username}` : ""}，密码${graph.has_password ? "已保存在钥匙串" : "未设置"}`,
    );
  }
  const vector = state.vector;
  if (vector.provider === "chroma") {
    lines.push("向量库：chroma（本机内置）");
  } else {
    lines.push(
      `向量库：${vector.provider} ${vector.host ?? ""}${vector.port ? `:${vector.port}` : ""}，集合前缀 ${vector.collection_prefix ?? "-"}，API key ${vector.has_api_key ? "已保存在钥匙串" : "未设置"}`,
    );
  }
  if (state.namespace) lines.push(`资料空间命名：${state.namespace}`);
  if (state.health) {
    lines.push(...probeLines(state.health));
  } else if (!state.is_default) {
    lines.push("连接测试：尚未运行（memhub settings backends test）");
  }
  const capabilities = Object.entries(state.capabilities);
  if (capabilities.length > 0) {
    lines.push("在这些后端上：");
    for (const [name, capability] of capabilities) {
      lines.push(
        `- ${CAPABILITY_LABEL[name] ?? name}：${CAPABILITY_STATE[capability.state] ?? capability.state}${capability.code ? `（${capability.code}）` : ""}`,
      );
    }
  }
  return lines;
}

export function renderBackendsText(document: BackendsDocument): string {
  const lines: string[] = [];
  switch (document.state) {
    case "ok":
      lines.push(...stateLines(document.backends));
      break;
    case "tested":
      lines.push(
        `当前：${document.current_label}；测试：${document.target_label}`,
      );
      lines.push(...probeLines(document.probe));
      if (!document.probe.ok) {
        const code = failedCode(document.probe);
        lines.push(`下一步：${NEXT_STEP[code] ?? code}`);
      }
      if (document.graph_adoption_required) {
        lines.push(`注意：${NEXT_STEP.storage_backend_graph_not_empty}`);
      }
      lines.push("只做了连接测试，没有保存任何设置。");
      break;
    case "preview":
      lines.push(
        `将把存储后端从 ${document.backends.label} 改为 ${document.target_label}。`,
      );
      if (document.probe) lines.push(...probeLines(document.probe));
      lines.push(...NOT_MOVED.map((line) => `- ${line}`));
      lines.push(`未执行任何改动。确认后运行：${document.next_step}`);
      break;
    case "unchanged":
      lines.push(...stateLines(document.backends));
      lines.push("已经是默认的存储后端，未做改动。");
      break;
    case "committed":
      if (!document.changed) {
        lines.push(`存储后端没有变化：${document.backends.label}。`);
        break;
      }
      lines.push(
        `已把存储后端从 ${document.previous_label} 改为 ${document.backends.label}。之后的写入和检索使用新后端；正在进行的写入在原后端完成。`,
      );
      if (document.notice === "existing_memories_not_moved") {
        lines.push(...NOT_MOVED.map((line) => `- ${line}`));
      }
      break;
    case "probe_failed":
      lines.push("连接测试没有通过，设置未保存。");
      lines.push(...probeLines(document.probe));
      lines.push(`下一步：${document.next_step}`);
      break;
    default:
      lines.push(
        `存储后端未能${document.state === "unavailable" ? "读取" : "修改"}：${document.code}`,
      );
      if (document.probe) lines.push(...probeLines(document.probe));
      if (document.next_step) lines.push(`下一步：${document.next_step}`);
  }
  return `${lines.join("\n")}\n`;
}
