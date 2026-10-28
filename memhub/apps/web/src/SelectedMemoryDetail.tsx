import type {
  MemoryDetail,
  SdkMemoryStatus,
  SourceResponse,
} from "@one-memory/contracts";
import { clockStamp } from "./clock-stamp";
import { useGuiLanguage } from "./gui-language";
import { sourceNameOr, UNATTRIBUTED_SOURCE_ID } from "./source-label";
import "./selected-memory.css";

const HEADING_LIMIT = 72;

/** One memory, read-only. Editing and deleting live in the memory library. */
export function SelectedMemoryDetail({
  detail,
  sources,
  conversationAt = null,
}: {
  detail: MemoryDetail;
  sources: SourceResponse[];
  /** D99: when the cited conversation happened, in the list's own terms. */
  conversationAt?: {
    at: string;
    basis: "conversation" | "import" | "write";
  } | null;
}) {
  const { locale, t } = useGuiLanguage();
  const memory = detail.memory;
  const evidence = detail.evidence[0];
  const sdkStatusLabels: Record<SdkMemoryStatus, Parameters<typeof t>[0]> = {
    active: "selectedMemory.status.active",
    superseded: "selectedMemory.status.superseded",
    shadow: "selectedMemory.status.shadow",
    negated: "selectedMemory.status.negated",
    conflicted: "selectedMemory.status.conflicted",
    archived: "selectedMemory.status.archived",
  };
  const statusLabel = detail.sdk_status
    ? t(sdkStatusLabels[detail.sdk_status])
    : memory.status === "confirmed"
      ? t("selectedMemory.status.confirmed")
      : memory.status === "deleted"
        ? t("selectedMemory.status.deleted")
        : memory.status === "superseded"
          ? t("selectedMemory.status.superseded")
          : t("selectedMemory.status.unknown");
  const sourceId = detail.source_id ?? evidence?.source_id;
  const created = clockStamp(
    detail.sdk_created_at ?? memory.updated_at,
    locale,
  );
  // An SDK experience or fact stored before hy-memory 2.0.7 has no
  // conversation time. It shows when it was written, named as such, instead
  // of "时间未知".
  const writtenOnly =
    detail.sdk_ref !== undefined &&
    detail.sdk_ref.kind !== "chat" &&
    !detail.sdk_memory_at;
  const source =
    detail.sdk_status && sourceId === UNATTRIBUTED_SOURCE_ID
      ? t("selectedMemory.sourceUnattributed")
      : sourceNameOr(sources, sourceId, t("selectedMemory.sourceUnknown"));
  // The heading is the first sentence when it fits in HEADING_LIMIT
  // characters; otherwise the text breaks at the last space within the limit
  // (CJK text has none, so it breaks at the limit itself).
  const punctuation = memory.content.search(/[。！？!?\n]/);
  const space = memory.content.lastIndexOf(" ", HEADING_LIMIT);
  const split =
    punctuation > 0 && punctuation < HEADING_LIMIT
      ? punctuation + 1
      : memory.content.length > HEADING_LIMIT
        ? space > HEADING_LIMIT / 2
          ? space
          : HEADING_LIMIT
        : -1;
  const heading =
    split > 0 ? memory.content.slice(0, split).trim() : memory.content;
  const body = split > 0 ? memory.content.slice(split).trim() : "";
  return (
    <article className="selected-memory-detail">
      <p className="selected-memory-status">
        {statusLabel}
        {!detail.sdk_status &&
          ` · ${t("selectedMemory.version", {
            revision: new Intl.NumberFormat(locale).format(memory.revision),
          })}`}
      </p>
      <h3>{heading}</h3>
      {body && <p className="selected-memory-body">{body}</p>}
      {detail.source_id || detail.evidence.length ? (
        <p className="selected-memory-source">
          {t("selectedMemory.sourceLine", {
            source,
            time: writtenOnly
              ? t("selectedMemory.writeTime", { created })
              : clockStamp(
                  detail.sdk_memory_at ?? evidence?.created_at,
                  locale,
                ),
          })}
        </p>
      ) : null}
      {writtenOnly ? null : (
        <p className="selected-memory-times">
          {t("selectedMemory.timestamps", {
            conversation: clockStamp(
              conversationAt?.at ??
                detail.sdk_memory_at ??
                evidence?.created_at,
              locale,
            ),
            created,
          })}
        </p>
      )}
    </article>
  );
}
