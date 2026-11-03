import type {
  SdkRawContextItem,
  SdkRawRecord,
  SourceResponse,
} from "@one-memory/contracts";
import { clockStamp } from "./clock-stamp";
import { useGuiLanguage } from "./gui-language";
import { sourceNameOr, UNATTRIBUTED_SOURCE_ID } from "./source-label";

type RawRecord = SdkRawRecord | SdkRawContextItem;

function recordTime(record: RawRecord): string | null {
  if (typeof record.memory_at === "number")
    return new Date(record.memory_at * 1_000).toISOString();
  return record.memory_at;
}

function authorLabel(
  role: string,
  t: ReturnType<typeof useGuiLanguage>["t"],
) {
  if (role === "user") return t("selectedMemory.author.user");
  if (role === "assistant" || role === "agent")
    return t("selectedMemory.author.agent");
  return t("selectedMemory.author.unknown");
}

export function SelectedRawDetail({
  record,
  sources,
}: {
  record: RawRecord;
  sources: SourceResponse[];
}) {
  const { locale, t } = useGuiLanguage();
  const messages =
    "messages" in record && record.messages?.length
      ? record.messages
      : [
          {
            role:
              "role" in record && typeof record.role === "string"
                ? record.role
                : "unknown",
            content: record.content,
          },
        ];
  const source =
    record.source === UNATTRIBUTED_SOURCE_ID
      ? t("selectedMemory.sourceUnattributed")
      : sourceNameOr(
          sources,
          record.source,
          record.source || t("selectedMemory.sourceUnknown"),
        );
  return (
    <article className="map-dialogue-reading">
      <p className="selected-memory-status">{t("memory:原始对话")}</p>
      <ul className="map-transcript">
        {messages.map((message, index) => (
          <li key={`${record.raw_id}:${index}`}>
            <small>{authorLabel(message.role, t)}</small>
            <p>{message.content}</p>
          </li>
        ))}
      </ul>
      <p className="selected-memory-source">
        {t("selectedMemory.sourceLine", {
          source,
          time: clockStamp(recordTime(record), locale),
        })}
      </p>
    </article>
  );
}
