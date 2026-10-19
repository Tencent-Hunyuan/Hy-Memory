/** Explicit, separately-confirmed deletion of the One Memory Home.
 *
 * The contract is strict and deliberately awkward, because this is the one
 * action that destroys user memory:
 *   - It is a separate command (`memhub delete-data`), never folded into
 *     `uninstall`. Uninstall keeps the data; this is the only thing that
 *     removes it.
 *   - Preview first, then two independent confirmations: `--yes` alone is not
 *     enough, the caller must also repeat the exact data directory with
 *     `--confirm <path>`.
 *   - Refuse to act unless the target is provably a One Memory Home: it must be
 *     an absolute, non-root, non-home directory that contains the runtime's own
 *     artifacts. An unknown or empty directory is never deleted.
 *   - A live Runtime must be stopped first; deletion while Core holds the
 *     SQLite file would corrupt or silently skip data.
 */

import { lstat, readdir, rm, rmdir } from "node:fs/promises";
import path from "node:path";

import type { RuntimeLockState } from "./runtime-lock-guard.js";

export interface DataDeletionPlan {
  schema_version: 1;
  product: string;
  command: "delete-data";
  state: "preview" | "blocked";
  data_dir: string;
  /** Files/dirs that would be removed, for a legible preview. */
  entries: string[];
  bytes: number;
  code?: string;
  reason?: string;
  next_step?: string;
}

export interface DataDeletionReceipt {
  schema_version: 1;
  product: string;
  command: "delete-data";
  state: "deleted";
  data_dir: string;
  removed_entries: string[];
}

/** Markers that prove a directory is a One Memory Home, not an arbitrary path.
 *
 * Only artifacts that this product alone creates count. Generic directory names
 * such as `logs/` or `integrations/` are not evidence: a normal project can
 * contain them, and accepting one would let a mistyped --data-dir recursively
 * delete unrelated files.
 */
const STRONG_HOME_MARKERS = [
  "one-memory.sqlite3",
  "runtime-service.json",
] as const;

async function directoryInfo(target: string) {
  const info = await lstat(target).catch(() => null);
  return info;
}

export async function planDataDeletion(options: {
  dataDir: string;
  home: string;
  runtimeRunning: boolean;
  /** Result of the service-lease check. A missing receipt is not proof that
   * no Runtime owns the directory, so the caller must pass this independently
   * of `runtimeRunning`. */
  runtimeLock?: RuntimeLockState;
}): Promise<DataDeletionPlan> {
  const base = (): Pick<
    DataDeletionPlan,
    "schema_version" | "product" | "command" | "entries" | "bytes"
  > => ({
    schema_version: 1,
    product: "memhub",
    command: "delete-data",
    entries: [],
    bytes: 0,
  });
  const blocked = (
    code: string,
    reason: string,
    nextStep: string,
  ): DataDeletionPlan => ({
    ...base(),
    state: "blocked",
    data_dir: options.dataDir,
    code,
    reason,
    next_step: nextStep,
  });
  const target = options.dataDir;
  if (!path.isAbsolute(target) || /[\0\r\n]/.test(target)) {
    return blocked(
      "data_dir_unusable",
      "数据目录必须是绝对且无换行的路径。",
      "传入 --data-dir 的绝对路径后重试。",
    );
  }
  const resolved = path.resolve(target);
  if (resolved === path.parse(resolved).root) {
    return blocked(
      "data_dir_refused",
      "拒绝删除文件系统根目录。",
      "指向具体的 One Memory Home 后重试。",
    );
  }
  if (resolved === path.resolve(options.home)) {
    return blocked(
      "data_dir_refused",
      "拒绝删除用户主目录本身。",
      "指向 One Memory Home（默认 ~/.one-memory）后重试。",
    );
  }
  if (options.runtimeRunning || options.runtimeLock === "live") {
    return blocked(
      "runtime_still_running",
      "Runtime 仍在运行，Core 正持有数据库文件。",
      "先运行 memhub stop 停止 Runtime，再执行 delete-data。",
    );
  }
  if (options.runtimeLock === "unknown") {
    return blocked(
      "runtime_state_unknown",
      "无法确认 Runtime 是否已退出（服务锁不可读或格式异常）。",
      "先运行 memhub stop，确认进程已退出后重试。",
    );
  }
  const info = await directoryInfo(resolved);
  if (info === null) {
    return blocked(
      "data_dir_missing",
      "数据目录不存在。",
      "确认 --data-dir 后重试；无需删除。",
    );
  }
  if (!info.isDirectory() || info.isSymbolicLink()) {
    return blocked(
      "data_dir_refused",
      "数据目录不是普通目录（或是符号链接）。",
      "指向真实的 One Memory Home 目录后重试。",
    );
  }
  const names = await readdir(resolved).catch(() => [] as string[]);
  const markers = names.filter((name) =>
    (STRONG_HOME_MARKERS as readonly string[]).includes(name),
  );
  if (markers.length === 0) {
    return blocked(
      "not_a_one_memory_home",
      "目录里没有 One Memory 运行产物，拒绝删除未知目录。",
      "确认 --data-dir 指向真正的 One Memory Home。",
    );
  }
  const entries: string[] = [];
  let bytes = 0;
  for (const name of names) {
    const entry = path.join(resolved, name);
    const entryInfo = await lstat(entry).catch(() => null);
    if (entryInfo === null) continue;
    entries.push(entry);
    bytes += await approxBytes(entry);
  }
  entries.sort();
  return { ...base(), state: "preview", data_dir: resolved, entries, bytes };
}

export async function applyDataDeletion(
  plan: DataDeletionPlan,
  confirmation: string,
): Promise<DataDeletionReceipt> {
  if (plan.state !== "preview") {
    throw new Error(plan.code ?? "data_deletion_blocked");
  }
  // The second confirmation must name the exact directory from the preview, so
  // a stale or copy-pasted `--yes` cannot trigger deletion of a different path.
  if (path.resolve(confirmation) !== path.resolve(plan.data_dir)) {
    throw new Error("data_deletion_confirmation_mismatch");
  }
  const removed: string[] = [];
  for (const entry of plan.entries) {
    // Re-check containment at apply time: a plan is data and can be stale.
    if (path.dirname(path.resolve(entry)) !== path.resolve(plan.data_dir)) {
      throw new Error("data_deletion_path_unsafe");
    }
    await rm(entry, { recursive: true, force: true });
    removed.push(entry);
  }
  // Remove the now-empty home itself, but only if it is genuinely empty.
  const remaining = await readdir(plan.data_dir).catch(() => null);
  if (remaining !== null && remaining.length === 0) {
    // `rmdir` on an empty directory; never a recursive delete here, so a race
    // that refilled the directory cannot be silently destroyed.
    await rmdir(plan.data_dir).catch(() => undefined);
  }
  return {
    schema_version: 1,
    product: "memhub",
    command: "delete-data",
    state: "deleted",
    data_dir: plan.data_dir,
    removed_entries: removed,
  };
}

async function approxBytes(target: string): Promise<number> {
  const info = await lstat(target).catch(() => null);
  if (info === null) return 0;
  if (info.isFile()) return info.size;
  if (!info.isDirectory()) return 0;
  let total = 0;
  const names = await readdir(target).catch(() => [] as string[]);
  for (const name of names) {
    total += await approxBytes(path.join(target, name));
  }
  return total;
}
