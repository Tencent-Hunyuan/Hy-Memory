import type { MemoryIndexSync } from "@one-memory/contracts";
import type { GuiLocale } from "@one-memory/contracts/gui-i18n";
import { memoryText } from "./memory-language";

export type IndexSyncAction = "retry" | "configure" | "refresh" | "none";

export interface IndexSyncPresentation {
  attemptedLine: string;
  explanation: string;
  action: IndexSyncAction;
}

/** What a pending index sync can honestly offer, from the fields Core sends.
 *
 * `attempts === 0` means Core has not tried yet. A `last_error_code` is the
 * only evidence that the last attempt failed; an unknown code gets a neutral
 * retry plus refresh path rather than being mistaken for success. */
export function indexSyncPresentation(
  sync: MemoryIndexSync,
  updatedAt: string,
  locale: GuiLocale = "zh-CN",
): IndexSyncPresentation {
  const text = (message: string, params?: Record<string, string | number>) =>
    memoryText(locale, message, params);
  const attempted = sync.attempts > 0;
  const attemptedLine = `${attempted ? text("同步尝试 {count} 次", { count: sync.attempts }) : text("尚未尝试同步")}${text(" · 状态更新于 {date}", { date: updatedAt })}`;
  const reason = sync.last_error_code;

  if (reason === "hy_memory_update_rejected") {
    return {
      attemptedLine,
      explanation: text("HY Memory 当前没有这条记忆，仅重试同步不能补齐。"),
      action: "none",
    };
  }
  if (
    reason === "model_profile_not_active" ||
    reason === "model_api_key_missing" ||
    reason === "model_secret_unavailable" ||
    reason === "correction_embedding_consent_required"
  ) {
    return {
      attemptedLine,
      explanation: text("同步需要可用的本机整理模型或本地嵌入授权。"),
      action: "configure",
    };
  }
  if (
    reason === "context_changed" ||
    reason === "memory_revision_conflict" ||
    reason === "model_content_profile_changed"
  ) {
    return {
      attemptedLine,
      explanation: text("这条记忆或模型版本已经变化，先刷新核对再决定下一步。"),
      action: "refresh",
    };
  }
  if (!attempted) {
    return {
      attemptedLine,
      explanation: text("这条记忆已改，搜索索引还没开始同步。"),
      action: "retry",
    };
  }
  if (reason) {
    return {
      attemptedLine,
      explanation: text(
        "最近一次同步未成功；可以先重试，结果仍不确定时再刷新核对。",
      ),
      action: "retry",
    };
  }
  return {
    attemptedLine,
    explanation: text("这条记忆已改，搜索索引还没跟上。"),
    action: "retry",
  };
}
