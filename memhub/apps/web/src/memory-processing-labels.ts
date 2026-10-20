/** Chinese wording for the memory pipeline's own status codes.
 *
 * Lives on its own because three screens now read the same summary — the
 * memory space, the onboarding completion page, and the health panel — and a
 * second copy of this table is how the same blocked pipeline ends up
 * described two different ways on two screens.
 */

import {
  type MemoryProcessingBlock,
  translate,
  type UiLanguage,
  type UiMessage,
} from "@one-memory/contracts";

export function taskKindLabel(
  kind: string,
  language: UiLanguage = "zh-CN",
): string {
  const labels: Record<string, UiMessage> = {
    extract: "记忆抽取",
    system2: "关系归纳",
    ultra: "深度整理",
    sweeper: "跨域整理",
  };
  return labels[kind] ? translate(language, labels[kind]) : kind;
}

/** What a stall means, in the reader's own language.
 *
 * The fallback is deliberately not the code. `hy_memory_write_verification_
 * clause_incomplete` in a red line told a reader nothing and read as a crash;
 * the code stays on the task row in Core, which is where it is actually
 * useful. Anything this build does not recognise is described by what it does
 * to the reader, not by what it is called. */
export function blockReasonLabel(
  reason: string,
  language: UiLanguage = "zh-CN",
): string {
  const known = BLOCK_REASON_LABELS[reason];
  if (known) return translate(language, known);
  if (reason.startsWith("hy_memory_write_verification_"))
    return translate(language, RETRYING);
  return translate(language, "暂时没能整理，稍后会自动重试");
}

const RETRYING = "这次没能整理成功，正在重试" as const;

const BLOCK_REASON_LABELS: Record<string, UiMessage> = {
  // Waiting on the reader, or on something they can go and check.
  waiting_for_model: "等待可用生成模型",
  model_profile_not_active: "还没有启用生成模型",
  model_api_key_missing: "缺少生成模型 API Key",
  model_secret_unavailable: "模型凭据暂不可用",
  secure_storage_unavailable: "本机凭据存储暂不可用",
  // Keychain failures are not all the same ask. macOS asking for permission is
  // answered by authorizing, not by reconfiguring a model, so each code says
  // the one thing the reader can actually go and do.
  secure_storage_authorization_required:
    "需要授权访问本机钥匙串。请重新打开 App 并完成一次授权",
  secure_storage_read_timeout:
    "读取本机钥匙串超时。重新保存一遍模型配置即可恢复",
  secure_storage_write_timeout:
    "写入本机钥匙串超时。重新保存一遍模型配置即可恢复",
  hy_memory_model_authentication_failed:
    "模型认证失败。更新 key 后，到设置里点一次检测，会自动接着处理",
  hy_memory_model_payment_required:
    "模型账户余额不足。充值或换 key 后，到设置里点一次检测，会自动接着处理",
  hy_memory_model_access_denied:
    "模型服务拒绝访问。更新权限后，到设置里点一次检测，会自动接着处理",
  hy_memory_model_rate_limited: "模型服务限流",
  hy_memory_model_service_unavailable: "模型服务暂不可用",
  hy_memory_model_timeout: "模型服务响应超时",
  hy_memory_model_request_incompatible: "模型请求不兼容，请核对配置",
  hy_memory_model_request_failed: "模型请求未成功",
  hy_memory_unavailable: "HY Memory 不可用",
  hy_memory_init_failed: "HY Memory 启动失败",
  model_content_profile_changed: "当前模型已变化",
  model_content_content_scope_changed: "本地会话内容已变化",
  // Named rather than summed up as a retry: Core never picks this one up
  // again (`_guard_task`), so calling it "正在重试" would be a lie.
  hy_memory_input_exceeds_window: "这段对话含过长原文，暂未整理；原文已保留",
  // The pipeline's own retries, kept here so a health panel can say what it
  // is doing without a code.
  failed: RETRYING,
  hy_memory_update_rejected: RETRYING,
  model_profile_superseded: "生成模型换过了，这批会重新整理",
  ingest_policy_superseded: "整理方式换过了，这批会重新整理",
};

/** The stalls worth naming to the reader.
 *
 * Everything Core reports here is picked up and retried on the next pass —
 * `_guard_task` excludes only `hy_memory_input_exceeds_window`. So a stage
 * that is merely being retried is not news, and listing those by name turned
 * a working library into a red line of internal guard codes. Only a stall the
 * reader can do something about, or one Core will never retry, earns a name.
 */
const NAMED_BLOCK_REASONS = new Set([
  "waiting_for_model",
  "model_profile_not_active",
  "model_api_key_missing",
  "model_secret_unavailable",
  "secure_storage_unavailable",
  "hy_memory_model_authentication_failed",
  "hy_memory_model_payment_required",
  "hy_memory_model_access_denied",
  "hy_memory_model_rate_limited",
  "hy_memory_model_service_unavailable",
  "hy_memory_model_timeout",
  "hy_memory_model_request_incompatible",
  "hy_memory_model_request_failed",
  "hy_memory_unavailable",
  "hy_memory_init_failed",
  "model_content_profile_changed",
  "model_content_content_scope_changed",
  // Never retried, and the one stall whose answer is "nothing to do, the
  // original is still there" — worth saying out loud.
  "hy_memory_input_exceeds_window",
]);

export function worthNamingBlock(reason: string): boolean {
  return NAMED_BLOCK_REASONS.has(reason);
}

/** What a blocked stage actually costs the reader. */
export interface BlockedStages {
  /** Stalls worth naming, in the order Core reported them. */
  named: MemoryProcessingBlock[];
  /** How many conversations the pipeline is retrying on its own. */
  retrying: number;
}

export function splitBlockedStages(
  blocks: readonly MemoryProcessingBlock[],
): BlockedStages {
  const named: MemoryProcessingBlock[] = [];
  let retrying = 0;
  for (const block of blocks) {
    if (block.count <= 0) continue;
    if (worthNamingBlock(block.reason)) named.push(block);
    else retrying += block.count;
  }
  return { named, retrying };
}

/** The one line a reader gets for work the pipeline is handling by itself. */
export function retryingBlockLabel(
  count: number,
  language: UiLanguage = "zh-CN",
): string {
  return translate(language, "{count} 条这次没能整理成功，正在重试", {
    count: new Intl.NumberFormat(language).format(count),
  });
}

/** The reasons that mean "no usable model", as the pipeline reports them.
 *
 * `blocked_stages` carries every stall reason, most of which say nothing
 * about the model. Screens that ask "is a model configured" have to match on
 * this set rather than on the list being non-empty.
 */
const MODEL_BLOCK_REASONS = new Set([
  "waiting_for_model",
  "model_profile_not_active",
  "model_api_key_missing",
  "model_secret_unavailable",
  "secure_storage_unavailable",
  "hy_memory_model_authentication_failed",
  "hy_memory_model_payment_required",
  "hy_memory_model_access_denied",
  "hy_memory_model_request_incompatible",
]);

export function modelBlock(
  blocks: readonly MemoryProcessingBlock[],
): MemoryProcessingBlock | null {
  return (
    blocks.find(
      (block) => block.count > 0 && MODEL_BLOCK_REASONS.has(block.reason),
    ) ?? null
  );
}

/** What went wrong reading a source, said without the code.
 *
 * These are `ScanIssueCode` plus the one access-level refusal, and they land
 * in the import receipts where a reader is deciding whether anything was lost.
 * `invalid_json` told them nothing; "format damaged, this record was not read"
 * tells them what it cost. An unrecognised code still gets a sentence rather
 * than itself, for the same reason the block reasons do. */
export function importIssueLabel(
  reason: string,
  language: UiLanguage = "zh-CN",
): string {
  return translate(language, SCAN_ISSUE_LABELS[reason] ?? "这条记录没读出来");
}

const SCAN_ISSUE_LABELS: Record<string, UiMessage> = {
  invalid_json: "内容损坏，这条没读出来",
  record_too_large: "内容过长，超出单条上限",
  unknown_record: "记录类型不认识，这条没读出来",
  no_text: "这条没有可整理的文字",
  tool_content_excluded: "工具输出不计入原文",
  internal_reasoning_excluded: "模型的内部思考不计入原文",
  not_written_by_person: "不是本人写的，未计入",
  source_busy: "来源正被占用，稍后会自动补读",
  unsupported_schema: "这个来源的文件格式暂不支持",
  budget_exceeded: "超出本次处理上限，其余未读",
  source_permission_changed: "这个来源的读取授权变了，需要重新核对",
};

export function extractionStateLabel(state: string): string {
  const labels: Record<string, string> = {
    extracted: "已提炼",
    outside_window: "未提炼 · 在窗口外",
    queued: "未提炼 · 排队中",
  };
  return labels[state] ?? state;
}

// The history API names the step and nothing else, so a delete carried over
// from an older version's rejection reads as a plain delete.
export function memoryHistoryActionLabel(action: string): string {
  const labels: Record<string, string> = {
    legacy: "旧版确认记录",
    create: "创建",
    correct: "纠正",
    set_scopes: "调整使用范围",
    undo: "撤销修改",
    hy_reconcile: "模型更新",
    hy_supersede: "被新记忆取代",
    delete: "删除",
  };
  return labels[action] ?? "其他操作";
}

export function extractionWindowPresetLabel(preset: string): string {
  const labels: Record<string, string> = {
    "3m": "近 3 个月",
    "6m": "近 6 个月",
    "1y": "近 1 年",
    all: "全部历史",
  };
  return labels[preset] ?? preset;
}
