import type { SdkRawContextItem, SdkRawRecord } from "@one-memory/contracts";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { getSdkRaw, searchSdkRaw } from "./api";
import { useGuiLanguage } from "./gui-language";

export function SdkRawHistory() {
  const { locale, t, tn } = useGuiLanguage();
  const translation = useRef(t);
  translation.current = t;
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
        if (current) setError(translation.current("sdk.raw.loadFailed"));
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
      setError(t("sdk.raw.searchFailed"));
    } finally {
      setBusy(false);
    }
  }

  const items = results ?? recent.map(asContextItem);
  return (
    <section aria-label={t("sdk.raw.title")}>
      <form onSubmit={(event) => void submit(event)}>
        <label>
          {t("sdk.raw.searchLabel")}
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <button type="submit" disabled={busy || !query.trim()}>
          {busy ? t("sdk.raw.searching") : t("sdk.raw.search")}
        </button>
      </form>
      {error ? <p role="alert">{error}</p> : null}
      <p aria-live="polite">
        {results === null
          ? t("sdk.raw.recent")
          : tn("sdk.raw.found", items.length)}
      </p>
      <ol>
        {items.map((item) => (
          <li key={item.raw_id}>
            <p>{item.content}</p>
            <small>
              {item.source} · {item.session_id}
              {item.memory_at
                ? ` · ${new Date(item.memory_at).toLocaleString(locale)}`
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
