import type { SdkRawContextItem, SdkRawRecord } from "@one-memory/contracts";
import { type FormEvent, useEffect, useState } from "react";
import { getSdkRaw, searchSdkRaw } from "./api";

export function SdkRawHistory() {
  const [recent, setRecent] = useState<SdkRawRecord[]>([]);
  const [results, setResults] = useState<SdkRawContextItem[] | null>(null);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let current = true;
    void getSdkRaw({ limit: 100 }).then(
      (page) => {
        if (current) setRecent(page.items);
      },
      () => {
        if (current) setError("原始对话暂时无法读取。");
      },
    );
    return () => {
      current = false;
    };
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!query.trim()) return;
    setBusy(true);
    setError("");
    try {
      setResults(await searchSdkRaw(query.trim()));
    } catch {
      setError("原始对话搜索失败，请重试。");
    } finally {
      setBusy(false);
    }
  }

  const items = results ?? recent.map(asContextItem);
  return (
    <section aria-label="SDK 原始对话">
      <form onSubmit={(event) => void submit(event)}>
        <label>
          搜索原始对话
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <button type="submit" disabled={busy || !query.trim()}>
          {busy ? "搜索中…" : "搜索"}
        </button>
      </form>
      {error ? <p role="alert">{error}</p> : null}
      <p aria-live="polite">
        {results === null ? "最近原始对话" : `找到 ${items.length} 条`}
      </p>
      <ol>
        {items.map((item) => (
          <li key={item.raw_id}>
            <p>{item.content}</p>
            <small>
              {item.source} · {item.session_id}
              {item.memory_at
                ? ` · ${new Date(item.memory_at).toLocaleString("zh-CN")}`
                : ""}
            </small>
          </li>
        ))}
      </ol>
    </section>
  );
}

function asContextItem(item: SdkRawRecord): SdkRawContextItem {
  return {
    kind: "raw",
    raw_id: item.raw_id,
    content: item.content,
    source: item.source,
    session_id: item.session_id,
    memory_at:
      item.memory_at === null
        ? null
        : new Date(item.memory_at * 1000).toISOString(),
    score: 0,
    truncated: false,
  };
}
