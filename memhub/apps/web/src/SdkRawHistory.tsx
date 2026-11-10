import type { SdkRawContextItem, SdkRawRecord } from "@one-memory/contracts";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { getSdkRaw, searchSdkRaw } from "./api";
import { useGuiLanguage } from "./gui-language";
import { subscribePoll } from "./poll-scheduler";
import { rawRecordId } from "./sdk-raw-map";

export function SdkRawHistory({ active = true }: { active?: boolean }) {
  const { locale, t, tn } = useGuiLanguage();
  const translation = useRef(t);
  translation.current = t;
  const [recent, setRecent] = useState<SdkRawRecord[]>([]);
  const [results, setResults] = useState<SdkRawContextItem[] | null>(null);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [errorKind, setErrorKind] = useState<"load" | "search" | null>(null);
  const errorKindRef = useRef<"load" | "search" | null>(null);
  const searchEpoch = useRef(0);
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);

  // biome-ignore lint/correctness/useExhaustiveDependencies: revision is the explicit retry trigger.
  useEffect(() => {
    if (!active) return;
    let current = true;
    const startedBeforeSearch = searchEpoch.current;
    const consume = (value: unknown) => {
      if (!current) return;
      setRecent((value as Awaited<ReturnType<typeof getSdkRaw>>).items);
      if (errorKindRef.current !== "search") {
        errorKindRef.current = null;
        setError("");
        setErrorKind(null);
      }
    };
    void getSdkRaw({ limit: 100 }).then(consume, () => {
      if (current && startedBeforeSearch === searchEpoch.current) {
        setError(translation.current("sdk.raw.loadFailed"));
        errorKindRef.current = "load";
        setErrorKind("load");
      }
    });
    const stop = subscribePoll({
      key: "sdk-raw-history",
      intervalMs: 12_000,
      fetch: () => getSdkRaw({ limit: 100 }),
      onValue: consume,
    });
    return () => {
      current = false;
      stop();
    };
  }, [active, revision]);

  async function runSearch(value: string) {
    searchEpoch.current += 1;
    setBusy(true);
    setResults(null);
    setError("");
    errorKindRef.current = null;
    setErrorKind(null);
    try {
      setResults(await searchSdkRaw(value));
    } catch {
      setError(t("sdk.raw.searchFailed"));
      errorKindRef.current = "search";
      setErrorKind("search");
    } finally {
      setBusy(false);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!query.trim()) return;
    await runSearch(query.trim());
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
      {error ? (
        <p role="alert">
          {error}{" "}
          <button
            type="button"
            disabled={busy}
            onClick={() => {
              if (errorKind === "search" && query.trim()) {
                void runSearch(query.trim());
              } else {
                setError("");
                errorKindRef.current = null;
                setErrorKind(null);
                setRevision((value) => value + 1);
              }
            }}
          >
            {t("connectionFallback.retry")}
          </button>
        </p>
      ) : null}
      <p aria-live="polite">
        {results === null
          ? t("sdk.raw.recent")
          : tn("sdk.raw.found", items.length)}
      </p>
      {!error && results === null && items.length === 0 ? (
        <p>{t("sdk.raw.empty")}</p>
      ) : null}
      <ol>
        {items.map((item) => (
          <li key={rawRecordId(item)}>
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
    turn_id: item.turn_id,
    turn_index: item.turn_index,
    turn_count: item.turn_count,
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
