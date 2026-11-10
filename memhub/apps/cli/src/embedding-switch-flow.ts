/**
 * Switching the embedding model from the CLI: `models activate` when the
 * embedding changes, `models switch status|cancel`, `models spaces`.
 *
 * Core owns every decision (`/api/v1/embedding-switches*`, contract
 * `docs/contracts/embedding-switch-v1.md`). A change of embedding model or
 * width is never applied by activation alone: Core plans a switch, the user
 * confirms it, Core re-embeds every memory into a new space and only then
 * makes it live. The CLI shows the plan, asks, starts the job and follows it.
 */

import { isBareNotFound } from "@one-memory/operations";
import type { CoreReader } from "./core-client.js";
import type {
  CoreDeleteOperations,
  CoreOperations,
  OperationOutcome,
} from "./core-operations.js";
import { EXIT_FAILED, EXIT_OK, EXIT_USAGE } from "./exit-codes.js";
import {
  activateModelProfile,
  type ModelWriteDocument,
  modelProfileResult,
  renderModelWriteText,
} from "./model-write-flows.js";
import { PRODUCT } from "./version.js";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const CODE = /^[a-z0-9_.:-]{1,128}$/;
const TERMINAL = new Set(["done", "rolled_back"]);
const POLL_MS = 1_000;

export interface SpaceSummary {
  space_id: string | null;
  model: string | null;
  dims: number | null;
  collection_base: string;
  vectors?: number | null;
}

export interface EmbeddingSwitchPlan {
  needed: boolean;
  reason: string;
  plan_token: string;
  source: SpaceSummary;
  target: SpaceSummary;
  target_profile_id: string;
  target_revision: number;
  target_provider_kind: string;
  target_local: boolean;
  total_vectors: number;
  users: number | null;
  graph_vectors: number | null;
  estimate_s: number | null;
  requests: number | null;
  disk_bytes_needed: number;
  disk_bytes_free: number | null;
  retention_days: number;
  blockers: string[];
}

export interface SwitchProgress {
  phase: string;
  done: number;
  total: number;
  percent: number;
  eta_s: number | null;
}

export interface EmbeddingSwitchJob {
  job_id: string;
  kind: "switch" | "merge";
  state: string;
  source: SpaceSummary;
  target: SpaceSummary;
  progress: SwitchProgress;
  cancel_requested: boolean;
  error_code: string | null;
  finished_at: string | null;
}

export interface SwitchCurrent {
  phase: "idle" | "scanning" | "running";
  writes_paused: boolean;
  job: EmbeddingSwitchJob | null;
  last_finished: EmbeddingSwitchJob | null;
}

export interface SwitchIo {
  reader: CoreReader;
  operations: CoreOperations & CoreDeleteOperations;
  emit: (text: string) => void;
  asJson: boolean;
  /** A terminal is attached: may prompt, and follows progress by default. */
  interactive: boolean;
  confirm?: (question: string) => Promise<boolean>;
  wait?: (milliseconds: number, signal: AbortSignal) => Promise<void>;
  signal?: AbortSignal;
}

export interface ActivateOptions {
  confirmed: boolean;
  /** `--wait` / `--no-wait`; undefined follows on a terminal only. */
  follow?: boolean;
}

// ---------------------------------------------------------------------------
// Parsing: bounded projections of Core's answers, never passthrough.
// ---------------------------------------------------------------------------

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function int(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0
    ? value
    : null;
}

function str(value: unknown, max = 256): string | null {
  return typeof value === "string" && value.length <= max ? value : null;
}

function space(value: unknown): SpaceSummary | null {
  const item = record(value);
  if (!item) return null;
  return {
    space_id: str(item.space_id, 64),
    model: str(item.model),
    dims: int(item.dims),
    collection_base: str(item.collection_base) ?? "",
    vectors: int(item.vectors),
  };
}

export function parsePlan(value: unknown): EmbeddingSwitchPlan | null {
  const plan = record(value);
  if (!plan || typeof plan.needed !== "boolean") return null;
  const source = space(plan.source);
  const target = space(plan.target);
  const token = str(plan.plan_token, 128);
  const profile = str(plan.target_profile_id, 128);
  const revision = int(plan.target_revision);
  if (!source || !target || !token || !profile || revision === null) {
    return null;
  }
  const blockers = Array.isArray(plan.blockers)
    ? plan.blockers.filter(
        (item): item is string => typeof item === "string" && CODE.test(item),
      )
    : [];
  return {
    needed: plan.needed,
    reason: str(plan.reason, 64) ?? "",
    plan_token: token,
    source,
    target,
    target_profile_id: profile,
    target_revision: revision,
    target_provider_kind: str(plan.target_provider_kind, 64) ?? "",
    target_local: plan.target_local === true,
    total_vectors: int(plan.total_vectors) ?? 0,
    users: int(plan.users),
    graph_vectors: int(plan.graph_vectors),
    estimate_s: int(plan.estimate_s),
    requests: int(plan.requests),
    disk_bytes_needed: int(plan.disk_bytes_needed) ?? 0,
    disk_bytes_free: int(plan.disk_bytes_free),
    retention_days: int(plan.retention_days) ?? 14,
    blockers,
  };
}

export function parseJob(value: unknown): EmbeddingSwitchJob | null {
  const job = record(value);
  if (!job) return null;
  const jobId = str(job.job_id, 64);
  const state = str(job.state, 32);
  const source = space(job.source);
  const target = space(job.target);
  const progress = record(job.progress) ?? {};
  if (!jobId || !state || !source || !target) return null;
  if (job.kind !== "switch" && job.kind !== "merge") return null;
  const percent =
    typeof progress.percent === "number" && Number.isFinite(progress.percent)
      ? Math.min(100, Math.max(0, progress.percent))
      : 0;
  return {
    job_id: jobId,
    kind: job.kind,
    state,
    source,
    target,
    progress: {
      phase: str(progress.phase, 32) ?? "prepare",
      done: int(progress.done) ?? 0,
      total: int(progress.total) ?? 0,
      percent,
      eta_s: int(progress.eta_s),
    },
    cancel_requested: job.cancel_requested === true,
    error_code:
      typeof job.error_code === "string" && CODE.test(job.error_code)
        ? job.error_code
        : null,
    finished_at: str(job.finished_at, 64),
  };
}

export function parseCurrent(value: unknown): SwitchCurrent | null {
  const current = record(value);
  if (!current) return null;
  const phase = current.phase;
  if (phase !== "idle" && phase !== "scanning" && phase !== "running") {
    return null;
  }
  return {
    phase,
    writes_paused: current.writes_paused === true,
    job: current.job == null ? null : parseJob(current.job),
    last_finished:
      current.last_finished == null ? null : parseJob(current.last_finished),
  };
}

// ---------------------------------------------------------------------------
// Wording
// ---------------------------------------------------------------------------

const BLOCKER_TEXT: Record<string, string> = {
  embedding_switch_running: "上一次向量模型切换还在进行，请等待完成或先取消。",
  embedding_probe_required: "新配置还没有通过探测；先运行 memhub models test。",
  sdk_reembed_unavailable:
    "当前运行时的 SDK 不支持重新生成向量，请先升级 MemHub。",
  embedding_switch_requires_migration:
    "还有旧版本的向量数据没有迁移完，请先完成升级迁移。",
  embedding_dimensions_unknown:
    "新配置的向量维度未知；先探测或在配置里写明 embedding_dimensions。",
  insufficient_disk_space: "磁盘空间不足，无法容纳新旧两份向量数据。",
};

const FAILURE_TEXT: Record<string, string> = {
  writes_did_not_drain: "等待正在进行的记忆整理结束超时",
  embedding_service_failed: "向量服务连续请求失败",
  embedding_dimensions_mismatch: "向量服务返回的维度与探测到的不一致",
  embedding_verification_failed: "新向量没有通过校验",
  target_profile_changed: "切换期间目标配置被修改",
  embedding_switch_cancelled: "已取消",
  model_secret_unavailable: "读不到向量服务的 API Key",
  secure_storage_unavailable: "系统钥匙串不可用",
  embedding_switch_clients_busy: "记忆图谱一直被占用，无法重新生成",
};

const STATE_TEXT: Record<string, string> = {
  preparing: "准备中（等待记忆整理暂停）",
  reembedding: "重新生成向量",
  verifying: "校验新向量",
  committing: "正式切换",
  failed: "正在回滚",
  done: "已完成",
  rolled_back: "已回滚",
};

export function blockerText(code: string): string {
  return BLOCKER_TEXT[code] ?? code;
}

function spaceLabel(item: SpaceSummary, local?: boolean): string {
  const dims = item.dims === null ? "维度未知" : `${item.dims} 维`;
  return `${item.model ?? "未知模型"}（${dims}${local ? "，本地" : ""}）`;
}

function duration(seconds: number | null): string {
  if (seconds === null) return "未知";
  if (seconds < 60) return `${Math.max(1, seconds)} 秒`;
  return `${Math.round(seconds / 60)} 分钟`;
}

function megabytes(bytes: number | null): string {
  return bytes === null ? "未知" : `${Math.ceil(bytes / (1024 * 1024))} MB`;
}

export function renderPlanText(plan: EmbeddingSwitchPlan): string {
  const lines = [
    `将更换向量模型：${spaceLabel(plan.source)} → ${spaceLabel(plan.target, plan.target_local)}`,
    `需要用新模型重新生成 ${plan.total_vectors.toLocaleString("en-US")} 条向量` +
      `${plan.users === null ? "" : `（${plan.users} 个用户）`}` +
      `，预计约 ${duration(plan.estimate_s)}` +
      `${plan.requests === null ? "，在本机计算，不产生费用" : `，约 ${plan.requests} 次请求`}` +
      `，额外磁盘约 ${megabytes(plan.disk_bytes_needed)}。`,
  ];
  if (plan.graph_vectors) {
    lines.push(`记忆图谱另有 ${plan.graph_vectors} 条向量一并重新生成。`);
  }
  if (
    plan.source.dims !== null &&
    plan.source.dims === plan.target.dims &&
    plan.source.model !== plan.target.model
  ) {
    lines.push("新旧模型维度相同，但向量不能通用，仍需重新生成。");
  }
  lines.push(
    "切换期间记忆整理暂停，检索继续使用旧模型；完成并校验通过后才会生效。",
    `旧的向量数据保留 ${plan.retention_days} 天，可用 memhub models spaces 清理。`,
  );
  for (const blocker of plan.blockers) {
    lines.push(`无法开始：${blockerText(blocker)}`);
  }
  return `${lines.join("\n")}\n`;
}

export function renderProgressLine(job: EmbeddingSwitchJob): string {
  const what = job.kind === "merge" ? "正在合并旧向量数据" : "正在切换向量模型";
  const state = STATE_TEXT[job.state] ?? job.state;
  if (job.state === "reembedding" && job.progress.total > 0) {
    const eta =
      job.progress.eta_s === null
        ? ""
        : `  预计还需 ${duration(job.progress.eta_s)}`;
    return `${what}：${state}  ${job.progress.done.toLocaleString("en-US")}/${job.progress.total.toLocaleString("en-US")}  ${Math.floor(job.progress.percent)}%${eta}`;
  }
  return `${what}：${state}`;
}

export function renderOutcomeText(job: EmbeddingSwitchJob): string {
  if (job.state === "done") {
    return job.kind === "merge"
      ? "旧向量数据已合并，记忆整理已恢复。\n"
      : `已切换到 ${job.target.model ?? "新模型"}。\n`;
  }
  const code = job.error_code ?? "embedding_switch_failed";
  if (code === "embedding_switch_cancelled") {
    return `已取消切换，继续使用 ${job.source.model ?? "旧模型"}。\n`;
  }
  return `切换未完成，已恢复使用 ${job.source.model ?? "旧模型"}，数据未受影响。原因：${FAILURE_TEXT[code] ?? code}（${code}）\n`;
}

/** One line for `memhub status`, from `memory-processing.embedding_switch`. */
export function describeEmbeddingSwitchSummary(value: unknown): string | null {
  const summary = record(value);
  if (!summary) return null;
  const percent =
    typeof summary.percent === "number" && Number.isFinite(summary.percent)
      ? Math.floor(summary.percent)
      : 0;
  if (summary.state === "scanning") return "正在检查向量数据…";
  if (summary.kind === "merge") {
    return `正在合并旧向量数据 ${percent}%，记忆整理已暂停`;
  }
  if (summary.state === "failed") return "切换未完成，正在恢复旧向量模型";
  return `切换中 ${percent}%，记忆整理已暂停`;
}

// ---------------------------------------------------------------------------
// Flows
// ---------------------------------------------------------------------------

function document(
  command: string,
  fields: Record<string, unknown>,
): Record<string, unknown> {
  return { schema_version: 1, product: PRODUCT, command, ...fields };
}

function emitDocument(io: SwitchIo, value: Record<string, unknown>): void {
  io.emit(`${JSON.stringify(value)}\n`);
}

async function requestPlan(
  profileId: string,
  io: SwitchIo,
): Promise<
  | { state: "plan"; plan: EmbeddingSwitchPlan }
  | { state: "unsupported" }
  | { state: "failed"; code: string }
> {
  const outcome = await io.operations.post<unknown>(
    "/api/v1/embedding-switches/plan",
    { profile_id: profileId },
  );
  if (outcome.state === "committed") {
    const plan = parsePlan(outcome.data);
    // Not a plan: activation itself still refuses an embedding change.
    return plan ? { state: "plan", plan } : { state: "unsupported" };
  }
  if (outcome.state === "rejected") {
    // A Core before the registry has no plan route: activation as before.
    if (
      outcome.http_status === 404 &&
      outcome.code === "http_404" &&
      isBareNotFound(outcome.body)
    ) {
      return { state: "unsupported" };
    }
    const plan = parsePlan(record(outcome.body)?.plan);
    if (plan) return { state: "plan", plan };
    return { state: "failed", code: outcome.code };
  }
  return { state: "failed", code: outcome.code };
}

function emitActivation(io: SwitchIo, result: ModelWriteDocument): number {
  io.emit(
    io.asJson ? `${JSON.stringify(result)}\n` : renderModelWriteText(result),
  );
  return result.state === "committed" ? EXIT_OK : EXIT_FAILED;
}

/**
 * `memhub models activate <id> [--yes] [--wait|--no-wait] [--json]`.
 *
 * Asks Core for the plan first. No embedding change: activate as before.
 * An embedding change: show the plan, confirm (`--yes`, or a prompt on a
 * terminal; exit 2 otherwise), start the switch and follow it.
 */
export async function activateWithEmbeddingSwitch(
  profileId: string,
  options: ActivateOptions,
  io: SwitchIo,
): Promise<number> {
  if (!UUID.test(profileId)) {
    return emitActivation(
      io,
      await activateModelProfile(profileId, io.operations),
    );
  }
  const planned = await requestPlan(profileId, io);
  if (planned.state === "unsupported") {
    const activated = await activateOrPlan(profileId, io);
    return activated.state === "plan"
      ? confirmAndSwitch(activated.plan, options, io)
      : emitActivation(io, activated.result);
  }
  if (planned.state === "failed") {
    return reject(io, "models activate", planned.code);
  }
  const plan = planned.plan;
  if (!plan.needed) {
    if (!io.asJson && plan.reason === "same_embedding") {
      io.emit("向量模型未变化，无需重新生成向量。\n");
    }
    const activated = await activateOrPlan(profileId, io);
    if (activated.state === "plan") {
      return confirmAndSwitch(activated.plan, options, io);
    }
    return emitActivation(io, activated.result);
  }
  return confirmAndSwitch(plan, options, io);
}

async function activateOrPlan(
  profileId: string,
  io: SwitchIo,
): Promise<
  | { state: "plan"; plan: EmbeddingSwitchPlan }
  | { state: "activated"; result: ModelWriteDocument }
> {
  const result: OperationOutcome<unknown> = await io.operations.post<unknown>(
    `/api/v1/model-profiles/${encodeURIComponent(profileId)}/activate`,
    {},
  );
  if (
    result.state === "rejected" &&
    result.code === "embedding_switch_required"
  ) {
    const plan = parsePlan(record(result.body)?.plan);
    if (plan) return { state: "plan", plan };
  }
  return { state: "activated", result: modelProfileResult(profileId, result) };
}

function reject(io: SwitchIo, command: string, code: string): number {
  if (io.asJson) {
    emitDocument(io, document(command, { state: "rejected", code }));
  } else {
    io.emit(`${command} 未完成：${blockerText(code)}（${code}）\n`);
  }
  return EXIT_FAILED;
}

async function confirmAndSwitch(
  plan: EmbeddingSwitchPlan,
  options: ActivateOptions,
  io: SwitchIo,
): Promise<number> {
  if (plan.blockers.length > 0) {
    if (io.asJson) {
      emitDocument(
        io,
        document("models activate", {
          state: "blocked",
          code: plan.blockers[0],
          plan,
        }),
      );
    } else {
      io.emit(renderPlanText(plan));
    }
    return EXIT_FAILED;
  }
  if (!options.confirmed) {
    const ask = io.interactive && !io.asJson ? io.confirm : undefined;
    if (ask === undefined) {
      if (io.asJson) {
        emitDocument(
          io,
          document("models activate", {
            state: "confirmation_required",
            code: "embedding_switch_required",
            plan,
            next_step: `${PRODUCT} models activate ${plan.target_profile_id} --yes`,
          }),
        );
      } else {
        io.emit(`${renderPlanText(plan)}重新运行并加上 --yes 以确认\n`);
      }
      return EXIT_USAGE;
    }
    io.emit(renderPlanText(plan));
    if (!(await ask("继续？[y/N] "))) {
      io.emit("未开始切换，仍在使用原来的向量模型。\n");
      return EXIT_OK;
    }
  }
  const started = await io.operations.post<unknown>(
    "/api/v1/embedding-switches",
    {
      profile_id: plan.target_profile_id,
      expected_revision: plan.target_revision,
      plan_token: plan.plan_token,
      requested_by: "cli",
    },
  );
  if (started.state !== "committed") {
    return reject(io, "models activate", started.code);
  }
  const job = parseJob(started.data);
  if (!job) return reject(io, "models activate", "embedding_switch_invalid");
  const follow = options.follow ?? io.interactive;
  if (!follow) {
    if (io.asJson) {
      emitDocument(io, document("models activate", progressFields(job)));
    } else {
      io.emit(
        `已开始切换向量模型（${job.job_id}）。用 memhub models switch status 查看进度。\n`,
      );
    }
    return EXIT_OK;
  }
  return followJob(job, io, "models activate");
}

function progressFields(job: EmbeddingSwitchJob): Record<string, unknown> {
  return {
    state: job.state,
    job_id: job.job_id,
    kind: job.kind,
    phase: job.progress.phase,
    percent: job.progress.percent,
    done: job.progress.done,
    total: job.progress.total,
    eta_s: job.progress.eta_s,
    ...(job.error_code ? { code: job.error_code } : {}),
  };
}

async function readCurrent(io: SwitchIo): Promise<SwitchCurrent | null> {
  const outcome = await io.reader<unknown>(
    "/api/v1/embedding-switches/current",
  );
  return outcome.state === "ok" ? parseCurrent(outcome.data) : null;
}

/** Poll `current` until the job ends or the user stops watching. */
async function followJob(
  started: EmbeddingSwitchJob,
  io: SwitchIo,
  command: string,
): Promise<number> {
  const wait = io.wait ?? defaultWait;
  const signal = io.signal ?? new AbortController().signal;
  let last = "";
  let job = started;
  const show = (value: EmbeddingSwitchJob) => {
    const line = io.asJson
      ? JSON.stringify(document(command, progressFields(value)))
      : renderProgressLine(value);
    if (line !== last) {
      io.emit(`${line}\n`);
      last = line;
    }
  };
  show(job);
  if (!io.asJson) {
    io.emit(
      "（Ctrl-C 只停止显示；用 `memhub models switch cancel` 取消切换）\n",
    );
  }
  while (!TERMINAL.has(job.state)) {
    try {
      await wait(POLL_MS, signal);
    } catch {
      // Aborted below.
    }
    if (signal.aborted) {
      if (!io.asJson) {
        io.emit(
          "已停止显示进度；切换仍在后台进行。用 `memhub models switch cancel` 取消。\n",
        );
      }
      return EXIT_OK;
    }
    const current = await readCurrent(io);
    if (current === null) continue;
    const next =
      current.job?.job_id === job.job_id
        ? current.job
        : current.last_finished?.job_id === job.job_id
          ? current.last_finished
          : null;
    if (next === null) continue;
    job = next;
    show(job);
  }
  if (io.asJson) {
    emitDocument(
      io,
      document(command, {
        ...progressFields(job),
        result: job.state === "done" ? "switched" : "rolled_back",
      }),
    );
  } else {
    io.emit(renderOutcomeText(job));
  }
  return job.state === "done" ? EXIT_OK : EXIT_FAILED;
}

function defaultWait(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new Error("aborted"));
      return;
    }
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, milliseconds);
    const onAbort = () => {
      clearTimeout(timer);
      reject(new Error("aborted"));
    };
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

/** `memhub models switch status [--json]`. */
export async function switchStatus(io: SwitchIo): Promise<number> {
  const outcome = await io.reader<unknown>(
    "/api/v1/embedding-switches/current",
  );
  if (outcome.state !== "ok")
    return reject(io, "models switch status", outcome.code);
  const current = parseCurrent(outcome.data);
  if (!current) {
    return reject(io, "models switch status", "embedding_switch_invalid");
  }
  if (io.asJson) {
    emitDocument(
      io,
      document("models switch status", {
        state: current.phase,
        writes_paused: current.writes_paused,
        job: current.job ? progressFields(current.job) : null,
        last_finished: current.last_finished
          ? progressFields(current.last_finished)
          : null,
      }),
    );
    return EXIT_OK;
  }
  if (current.job) {
    io.emit(
      `${renderProgressLine(current.job)}\n记忆整理已暂停，检索不受影响。\n`,
    );
  } else if (current.phase === "scanning") {
    io.emit("正在检查向量数据…\n");
  } else if (current.last_finished) {
    io.emit(
      `没有进行中的向量模型切换。上一次：${renderOutcomeText(current.last_finished)}`,
    );
  } else {
    io.emit("没有进行中的向量模型切换。\n");
  }
  return EXIT_OK;
}

/** `memhub models switch cancel [--json]`: cancel the running job, if any. */
export async function switchCancel(io: SwitchIo): Promise<number> {
  const current = await readCurrent(io);
  if (current === null) {
    return reject(io, "models switch cancel", "embedding_switch_unavailable");
  }
  if (!current.job) {
    return reject(io, "models switch cancel", "embedding_switch_not_found");
  }
  const outcome = await io.operations.post<unknown>(
    `/api/v1/embedding-switches/${current.job.job_id}/cancel`,
    {},
  );
  if (outcome.state !== "committed") {
    return reject(io, "models switch cancel", outcome.code);
  }
  const job = parseJob(outcome.data) ?? current.job;
  if (io.asJson) {
    emitDocument(
      io,
      document("models switch cancel", {
        ...progressFields(job),
        cancel_requested: true,
      }),
    );
  } else {
    io.emit("已请求取消；当前批次完成后回滚，继续使用原来的向量模型。\n");
  }
  return EXIT_OK;
}

interface SpaceRow {
  space_id: string;
  state: string;
  model: string | null;
  dims: number | null;
  vectors: number | null;
  keep_until: string | null;
}

function spaceRows(value: unknown): SpaceRow[] | null {
  const body = record(value);
  if (!body || !Array.isArray(body.spaces)) return null;
  const rows: SpaceRow[] = [];
  for (const item of body.spaces) {
    const row = record(item);
    const spaceId = str(row?.space_id, 64);
    const state = str(row?.state, 16);
    if (!row || !spaceId || !state) return null;
    if (state === "dropped") continue;
    rows.push({
      space_id: spaceId,
      state,
      model: str(row.model),
      dims: int(row.dims) ?? int(row.name_dims),
      vectors: int(row.vectors),
      keep_until: str(row.keep_until, 64),
    });
  }
  return rows;
}

const SPACE_STATE: Record<string, string> = {
  live: "使用中",
  building: "正在生成",
  stray: "待合并",
  retired: "已停用",
};

/** `memhub models spaces [--json]`. */
export async function listSpaces(io: SwitchIo): Promise<number> {
  const outcome = await io.reader<unknown>("/api/v1/embedding-spaces");
  if (outcome.state !== "ok") return reject(io, "models spaces", outcome.code);
  const rows = spaceRows(outcome.data);
  if (!rows) return reject(io, "models spaces", "embedding_spaces_invalid");
  const body = record(outcome.data) ?? {};
  const unregistered = Array.isArray(body.scanned)
    ? body.scanned
        .map(record)
        .filter(
          (item): item is Record<string, unknown> =>
            item !== null && item.state === "unregistered",
        )
        .map((item) => ({
          collection_base: str(item.collection_base) ?? "",
          vectors: int(item.vectors) ?? 0,
        }))
    : [];
  if (io.asJson) {
    emitDocument(
      io,
      document("models spaces", {
        state: "ok",
        spaces: rows,
        unregistered,
        scan_error:
          typeof body.scan_error === "string" && CODE.test(body.scan_error)
            ? body.scan_error
            : null,
      }),
    );
    return EXIT_OK;
  }
  const lines = ["向量空间："];
  for (const row of rows) {
    const dims = row.dims === null ? "维度未知" : `${row.dims} 维`;
    const keep =
      row.state === "retired" && row.keep_until
        ? `，保留到 ${row.keep_until.slice(0, 10)}`
        : "";
    lines.push(
      `  ${row.space_id}  ${SPACE_STATE[row.state] ?? row.state}  ${row.model ?? "未知模型"}（${dims}）  ${row.vectors ?? "?"} 条${keep}`,
    );
  }
  for (const item of unregistered) {
    lines.push(
      `  （未登记）${item.collection_base}  ${item.vectors} 条，下次启动时合并`,
    );
  }
  if (rows.some((row) => row.state === "retired")) {
    lines.push(
      "清理已停用的空间：memhub models spaces --cleanup <space-id> --yes",
    );
  }
  io.emit(`${lines.join("\n")}\n`);
  return EXIT_OK;
}

/** `memhub models spaces --cleanup <space-id> --yes`: drop a retired space now. */
export async function cleanupSpace(
  spaceId: string,
  io: SwitchIo,
): Promise<number> {
  if (!UUID.test(spaceId)) {
    return reject(io, "models spaces", "embedding_space_not_found");
  }
  const outcome = await io.operations.delete<unknown>(
    `/api/v1/embedding-spaces/${spaceId}`,
  );
  if (outcome.state !== "committed") {
    return reject(io, "models spaces", outcome.code);
  }
  if (io.asJson) {
    emitDocument(
      io,
      document("models spaces", { state: "dropped", space_id: spaceId }),
    );
  } else {
    io.emit(`已清理旧向量数据：${spaceId}\n`);
  }
  return EXIT_OK;
}
