import type {
  SdkRawContextItem,
  SdkRawRecord,
  SourceResponse,
} from "@one-memory/contracts";
import { clockStamp } from "./clock-stamp";
import { useGuiLanguage } from "./gui-language";
import { rawRecordId, rawRecordTime } from "./sdk-raw-map";
import { sourceNameOr, UNATTRIBUTED_SOURCE_ID } from "./source-label";

type RawRecord = SdkRawRecord | SdkRawContextItem;

function authorLabel(role: string, t: ReturnType<typeof useGuiLanguage>["t"]) {
  if (role === "user") return t("selectedMemory.author.user");
  if (role === "assistant" || role === "agent")
    return t("selectedMemory.author.agent");
  if (role === "tool") return t("selectedMemory.author.tool");
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
    <article className="map-dialogue-reading" aria-label={t("memory:原始对话")}>
      <ul className="map-transcript">
        {messages.map((message) => (
          <li key={`${rawRecordId(record)}:${message.role}:${message.content}`}>
            <small>{authorLabel(message.role, t)}</small>
            <p>{message.content}</p>
          </li>
        ))}
      </ul>
      <p className="selected-memory-source">
        {t("selectedMemory.sourceLine", {
          source,
          time: clockStamp(rawRecordTime(record), locale),
        })}
      </p>
    </article>
  );
}
