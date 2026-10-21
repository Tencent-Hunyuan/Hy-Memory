import type { SdkMemoryRecord, SdkMemoryStatus } from "@one-memory/contracts";
import { useCallback, useEffect, useState } from "react";
import {
  deleteSdkMemory,
  getSdkMemory,
  getSdkMemoryLibrary,
  updateSdkMemory,
} from "./api";
import { R6Button } from "./R6Controls";

export function SdkMemoryLibrary({
  active,
  onChanged,
}: {
  active: boolean;
  onChanged?(): void;
}) {
  const [status, setStatus] = useState<SdkMemoryStatus>("active");
  const [offset, setOffset] = useState(0);
  const [rows, setRows] = useState<SdkMemoryRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [selected, setSelected] = useState<SdkMemoryRecord | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const limit = 100;

  const refresh = useCallback(async () => {
    if (!active) return;
    setBusy(true);
    setError("");
    try {
      const page = await getSdkMemoryLibrary({
        statuses: [status],
        offset,
        limit,
      });
      setRows(page.memories);
      setTotal(page.total);
      if (offset >= page.total && offset > 0)
        setOffset(Math.max(0, offset - limit));
    } catch {
      setError("记忆库读取失败，请重试。");
    } finally {
      setBusy(false);
    }
  }, [active, offset, status]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function open(memoryId: string) {
    setBusy(true);
    setError("");
    try {
      const memory = await getSdkMemory(memoryId);
      setSelected(memory);
      setDraft(memory.content);
    } catch {
      setError("记忆详情读取失败。");
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    if (!selected || !draft.trim()) return;
    setBusy(true);
    setError("");
    try {
      const memory = await updateSdkMemory(selected.memory_id, draft);
      setSelected(memory);
      await refresh();
      onChanged?.();
    } catch {
      setError("修改未完成，请重试。");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!selected) return;
    setBusy(true);
    setError("");
    try {
      await deleteSdkMemory(selected.memory_id);
      setSelected(null);
      await refresh();
      onChanged?.();
    } catch {
      setError("删除未完成，请重试。");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="sdk-memory-library" aria-label="SDK 记忆库">
      <nav aria-label="记忆状态">
        {(
          [
            ["active", "已可用"],
            ["superseded", "已被取代"],
          ] as const
        ).map(([value, label]) => (
          <button
            key={value}
            type="button"
            aria-current={status === value ? "page" : undefined}
            disabled={busy}
            onClick={() => {
              setStatus(value);
              setOffset(0);
              setSelected(null);
            }}
          >
            {label}
          </button>
        ))}
      </nav>
      <p aria-live="polite">共 {total} 条</p>
      {error && <p role="alert">{error}</p>}
      {busy && !rows.length ? <p role="status">正在读取记忆库…</p> : null}
      <ul>
        {rows.map((memory) => (
          <li key={memory.memory_id}>
            <button type="button" onClick={() => void open(memory.memory_id)}>
              {memory.content}
            </button>
          </li>
        ))}
      </ul>
      <nav aria-label="SDK 记忆库分页">
        <R6Button
          variant="quiet"
          disabled={busy || offset === 0}
          onClick={() => setOffset(Math.max(0, offset - limit))}
        >
          上一页
        </R6Button>
        <R6Button
          variant="quiet"
          disabled={busy || offset + limit >= total}
          onClick={() => setOffset(offset + limit)}
        >
          下一页
        </R6Button>
      </nav>
      {selected ? (
        <aside aria-label="SDK 记忆详情">
          <p>{selected.layer}</p>
          {selected.status === "active" ? (
            <>
              <textarea
                aria-label="记忆内容"
                value={draft}
                disabled={busy}
                onChange={(event) => setDraft(event.target.value)}
              />
              <R6Button
                variant="primary"
                disabled={!draft.trim()}
                pending={busy}
                onClick={() => void save()}
              >
                保存修改
              </R6Button>
              <R6Button pending={busy} onClick={() => void remove()}>
                删除记忆
              </R6Button>
            </>
          ) : (
            <p>{selected.content}</p>
          )}
        </aside>
      ) : null}
    </section>
  );
}
