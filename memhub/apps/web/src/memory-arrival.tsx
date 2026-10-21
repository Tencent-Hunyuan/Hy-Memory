import type { UiMessage } from "@one-memory/contracts";
import { FileText, Link2, LoaderCircle, Settings2 } from "lucide-react";
import {
  type MemoryArrival,
  memoryArrivalTitle,
  resolveMemoryArrival,
} from "./memory-arrival-state";
import { blockReasonLabel, taskKindLabel } from "./memory-processing-labels";
import { useTranslate, useUiLanguage } from "./ui-language";
import { useMediaPreference } from "./use-media-preference";
import "./memory-arrival.css";
import "./import-progress-strip.css";

export type { MemoryArrival, MemoryArrivalPhase } from "./memory-arrival-state";

export function MemoryArrivalPanel({
  arrival: providedArrival,
  hasMemories = false,
  suspended = false,
  onViewHistory,
  onConfigureModel,
  onConnect,
  showDetails = true,
}: {
  arrival: MemoryArrival;
  hasMemories?: boolean;
  suspended?: boolean;
  onViewHistory?(): void;
  onConfigureModel(): void;
  onConnect(): void;
  showDetails?: boolean;
}) {
  const reduceMotion = useMediaPreference("(prefers-reduced-motion: reduce)");
  const t = useTranslate();
  const language = useUiLanguage();
  const arrival = resolveMemoryArrival({
    arrival: providedArrival,
    processing: null,
    sources: [],
    loaded: true,
  });
  if (hasMemories && arrival.phase === "idle" && !arrival.importing)
    return null;
  const active =
    arrival.phase === "processing" || arrival.phase === "importing";
  const hasOriginals = arrival.importedMessages > 0;
  const title =
    hasMemories && arrival.phase === "idle"
      ? "原文仍在导入"
      : memoryArrivalTitle(arrival);
  const description = arrivalDescription(arrival);
  return (
    <section
      className="memory-arrival memory-arrival-neutral"
      data-phase={arrival.phase}
      data-compact={String(hasMemories)}
      aria-label={t("记忆整理状态")}
    >
      <div className="memory-arrival-heading" role="status" aria-live="polite">
        {active && (
          <LoaderCircle
            size={16}
            aria-hidden="true"
            className="memory-arrival-spinner"
            data-moving={String(!reduceMotion && !suspended)}
          />
        )}
        <strong>{t(title)}</strong>
      </div>
      {(hasOriginals || arrival.importing) && (
        <p className="memory-arrival-counts">
          {t("已保存 {messages} 条消息 · {conversations} 个会话", {
            messages: arrival.importedMessages.toLocaleString(language),
            conversations:
              arrival.importedConversations.toLocaleString(language),
          })}
        </p>
      )}
      {arrival.importing &&
        arrival.phase !== "importing" &&
        arrival.phase !== "idle" && (
          <p className="memory-arrival-secondary">{t("原文仍在导入")}</p>
        )}
      {(!hasMemories ||
        active ||
        ["failed", "unavailable", "stopped", "checking"].includes(
          arrival.phase,
        )) && <p className="memory-arrival-description">{t(description)}</p>}
      {showDetails &&
        ((arrival.blockedStages?.length ?? 0) > 0 ||
          (arrival.phase === "processing" &&
            (arrival.backlogPending ?? 0) > 0)) && (
          <details className="memory-feedback-details">
            <summary>{t("查看进度与详情")}</summary>
            {arrival.phase === "processing" &&
              (arrival.backlogPending ?? 0) > 0 && (
                <p>{t("存量数据较大，为保证质量，正在后台分批整理")}</p>
              )}
            <ul>
              {arrival.blockedStages?.map((block) => (
                <li key={`${block.kind}:${block.reason}`}>
                  {t("{kind} · {reason} · {count} 项", {
                    kind: taskKindLabel(block.kind, language),
                    reason: blockReasonLabel(block.reason, language),
                    count: block.count.toLocaleString(language),
                  })}
                </li>
              ))}
            </ul>
          </details>
        )}
      <div className="memory-arrival-actions">
        {arrival.phase === "model-required" && (
          <button type="button" onClick={onConfigureModel}>
            <Settings2 size={15} aria-hidden="true" />
            {t("设置记忆整理")}
          </button>
        )}
        {["failed", "unavailable", "stopped"].includes(arrival.phase) && (
          <button
            type="button"
            className="secondary"
            onClick={onConfigureModel}
          >
            <Settings2 size={15} aria-hidden="true" />
            {t("查看整理状态")}
          </button>
        )}
        {hasOriginals && onViewHistory && (
          <button type="button" className="secondary" onClick={onViewHistory}>
            <FileText size={15} aria-hidden="true" />
            {t("查看已保存的对话")}
          </button>
        )}
        {!hasOriginals && !arrival.importing && arrival.phase === "idle" && (
          <button type="button" onClick={onConnect}>
            <Link2 size={15} aria-hidden="true" />
            {t("关联应用")}
          </button>
        )}
      </div>
    </section>
  );
}

function arrivalDescription(arrival: MemoryArrival): UiMessage {
  const retained = arrival.importedMessages > 0;
  switch (arrival.phase) {
    case "model-required":
      return retained
        ? "已保存的原文保留在本机。请核对整理模型后继续。"
        : "请核对整理模型后继续。";
    case "processing":
      return "先整理近期对话，更多记忆会陆续出现";
    case "importing":
      return "正在保存原文；导入进度不代表记忆生成进度。";
    case "failed":
      return retained
        ? "已保存的原文保留在本机。请先核对失败原因，再决定是否重试。"
        : "请先核对失败原因，再决定是否重试。";
    case "unavailable":
      return "最近确认的信息已保留，当前进度需要重新核对。";
    case "checking":
      return arrival.refreshing
        ? "正在核对本机记录，尚未确认的进度不会计入。"
        : "已保存内容保留，进度待更新。";
    case "stopped":
      return retained
        ? "已保存的原文保留在本机。当前整理已停止，可查看状态后继续。"
        : "当前整理已停止，可查看状态后继续。";
    default:
      return retained
        ? "当前没有正在运行的整理任务。"
        : "关联应用或导入对话文件后，原文会先保存到本机。";
  }
}
