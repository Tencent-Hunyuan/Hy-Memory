import type { GovernedMemory, MemoryDetail } from "@one-memory/contracts";
import { useGuiLanguage } from "./gui-language";
import { useMemoryLanguage } from "./memory-language";

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
  const { text, locale } = useMemoryLanguage();
  const { tnPlain } = useGuiLanguage();
  return (
    <section className="matter-memory-panel" aria-label={text("事项基础记忆")}>
      <header>
        <h3>{text("基础记忆")}</h3>
        <span>{memories.length}</span>
      </header>
      <div className="matter-memory-list">
        {memories.map((memory) => (
          <button
            type="button"
            key={memory.memory_id}
            aria-label={text("选择记忆 {content}", { content: memory.content })}
            aria-pressed={selectedMemoryId === memory.memory_id}
            onClick={() => onSelect(memory.memory_id)}
          >
            <span>{memory.content}</span>
            <small>
              {text("基础记忆")} ·{" "}
              {tnPlain("matter.originals", memory.evidence_ids.length)} · r
              {memory.revision}
            </small>
          </button>
        ))}
      </div>
      {loading && <p role="status">{text("正在读取基础记忆与原话…")}</p>}
      {detail && !loading && (
        <section
          className="matter-memory-detail"
          aria-label={text("基础记忆原文")}
        >
          <h3>{text("来源原话")}</h3>
          {detail.evidence.map((item) => (
            <section key={item.evidence_id} className="matter-memory-excerpt">
              <p>
                {item.source_id ?? text("来源未知")} ·{" "}
                {text(
                  item.written_by === "person"
                    ? "用户原话"
                    : item.written_by === "agent"
                      ? "Agent 发言"
                      : "作者未知",
                )}
              </p>
              <small>
                {item.created_at
                  ? new Date(item.created_at).toLocaleString(locale)
                  : text("消息时间未知")}
              </small>
              <blockquote>
                {item.available ? item.verbatim : text("来源原话已不可读取")}
              </blockquote>
            </section>
          ))}
        </section>
      )}
    </section>
  );
}
