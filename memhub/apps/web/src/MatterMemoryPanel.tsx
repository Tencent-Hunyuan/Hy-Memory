import {
  formatUiDate,
  formatUiNumber,
  type GovernedMemory,
  type MemoryDetail,
} from "@one-memory/contracts";
import { useTranslate, useUiLanguage } from "./ui-language";

export function MatterMemoryPanel({
  memories,
  selectedMemoryId,
  detail,
  loading,
  onSelect,
}: {
  memories: GovernedMemory[];
  selectedMemoryId: string | null;
  detail: MemoryDetail | null;
  loading: boolean;
  onSelect(memoryId: string): void;
}) {
  const t = useTranslate();
  const language = useUiLanguage();
  return (
    <section className="matter-memory-panel" aria-label={t("事项基础记忆")}>
      <header>
        <h3>{t("基础记忆")}</h3>
        <span>{formatUiNumber(language, memories.length)}</span>
      </header>
      <div className="matter-memory-list">
        {memories.map((memory) => (
          <button
            type="button"
            key={memory.memory_id}
            aria-label={t("选择记忆 {content}", { content: memory.content })}
            aria-pressed={selectedMemoryId === memory.memory_id}
            onClick={() => onSelect(memory.memory_id)}
          >
            <span>{memory.content}</span>
            <small>
              {t("基础记忆 · {count} 条原话 · r{revision}", {
                count: formatUiNumber(language, memory.evidence_ids.length),
                revision: memory.revision,
              })}
            </small>
          </button>
        ))}
      </div>
      {loading && <p role="status">{t("正在读取基础记忆与原话…")}</p>}
      {detail && !loading && (
        <section
          className="matter-memory-detail"
          aria-label={t("基础记忆原文")}
        >
          <h3>{t("来源原话")}</h3>
          {detail.evidence.map((item) => (
            <section key={item.evidence_id} className="matter-memory-excerpt">
              <p>
                {item.source_id ?? t("来源未知")} ·{" "}
                {item.written_by === "person"
                  ? t("用户原话")
                  : item.written_by === "agent"
                    ? t("Agent 发言")
                    : t("作者未知")}
              </p>
              <small>
                {item.created_at
                  ? formatUiDate(language, new Date(item.created_at), {
                      dateStyle: "short",
                      timeStyle: "short",
                    })
                  : t("消息时间未知")}
              </small>
              <blockquote>
                {item.available ? item.verbatim : t("来源原话已不可读取")}
              </blockquote>
            </section>
          ))}
        </section>
      )}
    </section>
  );
}
