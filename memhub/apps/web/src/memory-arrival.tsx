import { type GuiLocale, translate } from "@one-memory/i18n";
import { FileText, Link2, LoaderCircle, Settings2 } from "lucide-react";
import { useGuiLanguage } from "./gui-language";
import {
  type MemoryArrival,
  memoryArrivalTitle,
  resolveMemoryArrival,
} from "./memory-arrival-state";
import { useMemoryLanguage } from "./memory-language";
import { blockReasonLabel, taskKindLabel } from "./memory-processing-labels";
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
  const { t, tn, locale } = useGuiLanguage();
  const { text } = useMemoryLanguage();
  const reduceMotion = useMediaPreference("(prefers-reduced-motion: reduce)");
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
      ? t("arrival.importing")
      : memoryArrivalTitle(arrival, locale);
  const description = arrivalDescription(arrival, locale);
  return (
    <section
      className="memory-arrival memory-arrival-neutral"
      data-phase={arrival.phase}
      data-compact={String(hasMemories)}
      aria-label={t("arrival.region")}
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
        <strong>{title}</strong>
      </div>
      {(hasOriginals || arrival.importing) && (
        <p className="memory-arrival-counts">
          <span>{tn("arrival.messages", arrival.importedMessages)}</span>
          {" · "}
          <span>
            {tn("arrival.conversations", arrival.importedConversations)}
          </span>
        </p>
      )}
      {arrival.importing &&
        arrival.phase !== "importing" &&
        arrival.phase !== "idle" && (
          <p className="memory-arrival-secondary">{t("arrival.importing")}</p>
        )}
      {(!hasMemories ||
        active ||
        ["failed", "unavailable", "stopped", "checking"].includes(
          arrival.phase,
        )) && <p className="memory-arrival-description">{description}</p>}
      {showDetails &&
        ((arrival.blockedStages?.length ?? 0) > 0 ||
          (arrival.phase === "processing" &&
            (arrival.backlogPending ?? 0) > 0)) && (
          <details className="memory-feedback-details">
            <summary>{t("arrival.details")}</summary>
            {arrival.phase === "processing" &&
              (arrival.backlogPending ?? 0) > 0 && (
                <p>{t("arrival.backlog")}</p>
              )}
            <ul>
              {arrival.blockedStages?.map((block) => (
                <li key={`${block.kind}:${block.reason}`}>
                  {text(taskKindLabel(block.kind))} ·{" "}
                  {text(blockReasonLabel(block.reason))} ·{" "}
                  {tn("arrival.items", block.count)}
                </li>
              ))}
            </ul>
          </details>
        )}
      <div className="memory-arrival-actions">
        {arrival.phase === "model-required" && (
          <button type="button" onClick={onConfigureModel}>
            <Settings2 size={15} aria-hidden="true" />
            {t("arrival.configure")}
          </button>
        )}
        {["failed", "unavailable", "stopped"].includes(arrival.phase) && (
          <button
            type="button"
            className="secondary"
            onClick={onConfigureModel}
          >
            <Settings2 size={15} aria-hidden="true" />
            {t("arrival.status")}
          </button>
        )}
        {hasOriginals && onViewHistory && (
          <button type="button" className="secondary" onClick={onViewHistory}>
            <FileText size={15} aria-hidden="true" />
            {t("arrival.history")}
          </button>
        )}
        {!hasOriginals && !arrival.importing && arrival.phase === "idle" && (
          <button type="button" onClick={onConnect}>
            <Link2 size={15} aria-hidden="true" />
            {t("arrival.connect")}
          </button>
        )}
      </div>
    </section>
  );
}

function arrivalDescription(arrival: MemoryArrival, locale: GuiLocale): string {
  const t = (key: Parameters<typeof translate>[1]) => translate(locale, key);
  const retained = arrival.importedMessages > 0 ? t("arrival.retained") : "";
  switch (arrival.phase) {
    case "model-required":
      return `${retained}${t("arrival.description.model-required")}`;
    case "processing":
      return t("arrival.description.processing");
    case "importing":
      return t("arrival.description.importing");
    case "failed":
      return `${retained}${t("arrival.description.failed")}`;
    case "unavailable":
      return t("arrival.description.unavailable");
    case "checking":
      return arrival.refreshing
        ? t("arrival.description.refreshing")
        : t("arrival.description.checking");
    case "stopped":
      return `${retained}${t("arrival.description.stopped")}`;
    default:
      return arrival.importedMessages > 0
        ? t("arrival.description.idle")
        : t("arrival.description.empty");
  }
}
