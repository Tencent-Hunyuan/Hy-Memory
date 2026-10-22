import type {
  GovernedMemory,
  MemoryDetail,
  SdkMemoryRecord,
} from "@one-memory/contracts";

export function MatterMemoryPanel({
  memories,
  selectedMemoryId,
  detail,
  loading,
  onSelect,
}: {
  memories: (GovernedMemory | SdkMemoryRecord)[];
  selectedMemoryId: string | null;
  detail: MemoryDetail | null;
  loading: boolean;
  onSelect(memoryId: string): void;
}) {
  return (
    <section className="matter-memory-panel" aria-label="事项基础记忆">
      <header>
        <h3>基础记忆</h3>
        <span>{memories.length}</span>
      </header>
      <div className="matter-memory-list">
        {memories.map((memory) => (
          <button
            type="button"
            key={memory.memory_id}
            aria-label={`选择记忆 ${memory.content}`}
            aria-pressed={selectedMemoryId === memory.memory_id}
            onClick={() => onSelect(memory.memory_id)}
          >
            <span>{memory.content}</span>
            <small>
              {"revision" in memory
                ? `基础记忆 · ${memory.evidence_ids.length} 条原话 · r${memory.revision}`
                : `SDK 记忆 · ${memory.layer} · ${memory.status}`}
            </small>
          </button>
        ))}
      </div>
      {loading && <p role="status">正在读取基础记忆与原话…</p>}
      {detail && !loading && (
        <section className="matter-memory-detail" aria-label="基础记忆原文">
          <h3>来源原话</h3>
          {detail.evidence.map((item) => (
            <section key={item.evidence_id} className="matter-memory-excerpt">
              <p>
                {item.source_id ?? "来源未知"} ·{" "}
                {item.written_by === "person"
                  ? "用户原话"
                  : item.written_by === "agent"
                    ? "Agent 发言"
                    : "作者未知"}
              </p>
              <small>
                {item.created_at
                  ? new Date(item.created_at).toLocaleString("zh-CN")
                  : "消息时间未知"}
              </small>
              <blockquote>
                {item.available ? item.verbatim : "来源原话已不可读取"}
              </blockquote>
            </section>
          ))}
        </section>
      )}
    </section>
  );
}
