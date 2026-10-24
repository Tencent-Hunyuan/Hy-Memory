import type { SdkMemoryRecord, SdkMemoryStatus } from "@one-memory/contracts";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  deleteSdkMemory,
  getSdkMemory,
  getSdkMemoryLibrary,
  updateSdkMemory,
} from "./api";
import { useGuiLanguage } from "./gui-language";
import { R6Button } from "./R6Controls";

type Translate = ReturnType<typeof useGuiLanguage>["t"];

const PAGE_SIZE = 100;
/** `/api/v2/memory-library` takes no text query, so a search walks the pages
 * once and filters here. 500 rows is a single ~0.3 s call; the ceiling keeps a
 * large library from turning one keystroke into a hundred requests. */
const SCAN_PAGE = 500;
const SCAN_CEILING = 5_000;
const MATCH_CEILING = 200;

function kindOf(layer: string) {
  if (layer === "l6_schema") return "schema";
  if (layer === "l7_intention") return "intention";
  if (layer.startsWith("coding")) return "coding";
  if (layer.startsWith("working")) return "working";
  return "chat";
}

function kindLabel(layer: string, t: Translate) {
  const labels: Record<string, Parameters<Translate>[0]> = {
    chat: "memory:对话",
    working: "memory:项目记忆",
    coding: "memory:编码偏好",
    schema: "memory:长期模式",
    intention: "memory:意图",
  };
  const kind = kindOf(layer);
  return labels[kind] ? t(labels[kind]) : layer;
}

function statusLabel(status: SdkMemoryStatus, t: Translate) {
  const labels: Record<SdkMemoryStatus, Parameters<Translate>[0]> = {
    active: "sdk.memory.status.active",
    superseded: "sdk.memory.status.superseded",
    shadow: "sdk.memory.status.active",
    negated: "sdk.memory.status.superseded",
    conflicted: "sdk.memory.status.active",
    archived: "sdk.memory.status.superseded",
  };
  return t(labels[status]);
}

function matchesQuery(record: SdkMemoryRecord, needle: string) {
  return `${record.content}\n${record.layer}\n${record.tags.join(" ")}`
    .toLowerCase()
    .includes(needle);
}

/** The memory library on SDK authority.
 *
 * Reads, edits and deletes go through v2 (`/api/v2/memory-library`). The list
 * draws the same ledger rows as the governed page so a memory reads the same
 * way on either authority; the header search scans the pages once and filters
 * locally, because the endpoint has no query parameter.
 */
export function SdkMemoryLibrary({
  active,
  onChanged,
  focusedMemoryId,
  query = "",
}: {
  active: boolean;
  onChanged?(): void;
  focusedMemoryId?: string | null;
  query?: string;
}) {
  const { t, tn, locale } = useGuiLanguage();
  const translation = useRef(t);
  translation.current = t;
  const [status, setStatus] = useState<SdkMemoryStatus>("active");
  const [offset, setOffset] = useState(0);
  const [rows, setRows] = useState<SdkMemoryRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [selected, setSelected] = useState<SdkMemoryRecord | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [scan, setScan] = useState<{
    state: "scanning" | "done";
    scanned: number;
    matches: SdkMemoryRecord[];
  } | null>(null);
  const cache = useRef<{
    status: SdkMemoryStatus;
    revision: number;
    rows: SdkMemoryRecord[];
  } | null>(null);
  const scanToken = useRef(0);

  const refresh = useCallback(
    async (_revision: number) => {
      if (!active) return;
      setBusy(true);
      setError("");
      try {
        const page = await getSdkMemoryLibrary({
          statuses: [status],
          offset,
          limit: PAGE_SIZE,
        });
        setRows(page.memories);
        setTotal(page.total);
        if (offset >= page.total && offset > 0)
          setOffset(Math.max(0, offset - PAGE_SIZE));
      } catch {
        setError(translation.current("sdk.memory.loadFailed"));
      } finally {
        setBusy(false);
      }
    },
    [active, offset, status],
  );

  useEffect(() => {
    void refresh(revision);
  }, [refresh, revision]);

  const open = useCallback(async (memoryId: string) => {
    setBusy(true);
    setError("");
    try {
      const memory = await getSdkMemory(memoryId);
      setSelected(memory);
      setDraft(memory.content);
    } catch {
      setError(translation.current("sdk.memory.detailLoadFailed"));
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    if (active && focusedMemoryId) void open(focusedMemoryId);
  }, [active, focusedMemoryId, open]);

  const readAll = useCallback(async (): Promise<SdkMemoryRecord[]> => {
    if (cache.current?.status === status && cache.current.revision === revision)
      return cache.current.rows;
    const collected: SdkMemoryRecord[] = [];
    let cursor = 0;
    while (collected.length < SCAN_CEILING) {
      const page = await getSdkMemoryLibrary({
        statuses: [status],
        offset: cursor,
        limit: SCAN_PAGE,
      });
      collected.push(...page.memories);
      cursor += page.memories.length;
      if (!page.memories.length || cursor >= page.total) break;
    }
    cache.current = { status, revision, rows: collected };
    return collected;
  }, [status, revision]);

  useEffect(() => {
    const needle = query.trim().toLowerCase();
    if (!needle || !active) {
      scanToken.current++;
      setScan(null);
      return;
    }
    const token = ++scanToken.current;
    setScan({ state: "scanning", scanned: 0, matches: [] });
    const timer = setTimeout(() => {
      void readAll()
        .then((all) => {
          if (token !== scanToken.current) return;
          setScan({
            state: "done",
            scanned: all.length,
            matches: all
              .filter((record) => matchesQuery(record, needle))
              .slice(0, MATCH_CEILING),
          });
        })
        .catch(() => {
          if (token !== scanToken.current) return;
          setError(translation.current("sdk.memory.loadFailed"));
          setScan(null);
        });
    }, 250);
    return () => clearTimeout(timer);
  }, [query, active, readAll]);

  async function save() {
    if (!selected || !draft.trim()) return;
    setBusy(true);
    setError("");
    try {
      const memory = await updateSdkMemory(selected.memory_id, draft);
      setSelected(memory);
      cache.current = null;
      setRevision((value) => value + 1);
      onChanged?.();
    } catch {
      setError(t("sdk.memory.updateFailed"));
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
      cache.current = null;
      setRevision((value) => value + 1);
      onChanged?.();
    } catch {
      setError(t("sdk.memory.deleteFailed"));
    } finally {
      setBusy(false);
    }
  }

  const shown = scan ? scan.matches : rows;
  const dateOf = (record: SdkMemoryRecord) => {
    const at = record.memory_at ?? record.gmt_created ?? null;
    return at === null
      ? t("memory:时间未知")
      : new Intl.DateTimeFormat(locale, { dateStyle: "medium" }).format(
          new Date(at * 1_000),
        );
  };

  return (
    <>
      <div>
        <nav className="memory-status-tabs" aria-label={t("sdk.memory.status")}>
          {(
            [
              ["active", t("sdk.memory.status.active")],
              ["superseded", t("sdk.memory.status.superseded")],
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
                cache.current = null;
              }}
            >
              {label}
            </button>
          ))}
        </nav>

        <nav aria-label={t("memory:记忆库分页")}>
          <span aria-live="polite">
            {tn("sdk.memory.total", scan ? scan.matches.length : total)}
          </span>{" "}
          {scan ? (
            <span role="status">
              {scan.state === "scanning" ? t("sdk.memory.loading") : null}
            </span>
          ) : (
            <>
              <span>
                {t("memory:第 {page} 页", {
                  page: new Intl.NumberFormat(locale).format(
                    Math.floor(offset / PAGE_SIZE) + 1,
                  ),
                })}
              </span>{" "}
              <R6Button
                variant="quiet"
                title={t("memory:上一页")}
                aria-label={t("memory:上一页")}
                disabled={busy || offset === 0}
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              >
                {t("memory:上一页")}
              </R6Button>
              <R6Button
                variant="quiet"
                title={t("memory:下一页")}
                aria-label={t("memory:下一页")}
                disabled={busy || offset + PAGE_SIZE >= total}
                onClick={() => setOffset(offset + PAGE_SIZE)}
              >
                {t("memory:下一页")}
              </R6Button>
            </>
          )}
        </nav>

        {error ? (
          <p className="error-banner" role="alert">
            {error}
          </p>
        ) : null}

        {busy && !shown.length && !scan ? (
          <p role="status">{t("sdk.memory.loading")}</p>
        ) : null}

        {shown.length ? (
          <ul className="memory-ledger" aria-label={t("sdk.memory.title")}>
            {shown.map((memory) => (
              <li key={memory.memory_id}>
                <button
                  type="button"
                  className={`memory-ledger-row memory-${memory.status}`}
                  aria-current={
                    selected?.memory_id === memory.memory_id
                      ? "true"
                      : undefined
                  }
                  onClick={() => void open(memory.memory_id)}
                >
                  <span>{memory.content}</span>
                  <small>
                    {kindLabel(memory.layer, t)} ·{" "}
                    {statusLabel(memory.status, t)} · {dateOf(memory)}
                    {memory.tags.length
                      ? ` · ${memory.tags.slice(0, 3).join(" / ")}`
                      : ""}
                  </small>
                </button>
              </li>
            ))}
          </ul>
        ) : !busy && (scan === null || scan.state === "done") ? (
          <p className="memory-status-empty" role="status">
            {t("memory:没有匹配的记忆")}
          </p>
        ) : null}
      </div>

      {selected ? (
        <aside className="memory-inspector" aria-label={t("memory:记忆详情")}>
          <button
            type="button"
            className="drawer-close"
            aria-label={t("memory:关闭记忆详情")}
            onClick={() => setSelected(null)}
          >
            ×
          </button>
          <p className="status-label">
            {kindLabel(selected.layer, t)} · {statusLabel(selected.status, t)}
          </p>
          <p className="map-origin">
            {t("memory:来源")}：{selected.source ?? t("memory:未知来源")} ·{" "}
            {dateOf(selected)}
          </p>
          {selected.status === "active" ? (
            <form
              onSubmit={(event) => {
                event.preventDefault();
                void save();
              }}
            >
              <label>
                {t("sdk.memory.content")}
                <textarea
                  value={draft}
                  disabled={busy}
                  onChange={(event) => setDraft(event.target.value)}
                />
              </label>
              <R6Button
                variant="primary"
                type="submit"
                pending={busy}
                disabled={!draft.trim()}
              >
                {t("sdk.memory.save")}
              </R6Button>
              <R6Button pending={busy} onClick={() => void remove()}>
                {t("sdk.memory.delete")}
              </R6Button>
            </form>
          ) : (
            <p>{selected.content}</p>
          )}
        </aside>
      ) : (
        <aside className="memory-inspector memory-inspector-empty">
          <p>{t("memory:选择一条记忆")}</p>
          <small>{t("memoryInspector.emptyHint")}</small>
        </aside>
      )}
    </>
  );
}
