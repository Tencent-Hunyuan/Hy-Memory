import type {
  SdkMemoryRecord,
  SdkMemoryScene,
  SdkMemoryStatus,
} from "@one-memory/contracts";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  deleteSdkMemory,
  getSdkMemory,
  getSdkMemoryLibrary,
  updateSdkMemory,
} from "./api";
import { useGuiLanguage } from "./gui-language";
import { R6Button } from "./R6Controls";
import {
  SDK_MEMORY_PAGE_SIZE,
  sdkEditableText,
  sdkMemoryRef,
  sdkNamespace,
} from "./sdk-memory-map";

const SCENE_LABELS = {
  chat: "sdk.memory.scene.chat",
  working: "sdk.memory.scene.working",
  coding: "sdk.memory.scene.coding",
} as const satisfies Record<SdkMemoryScene, string>;

export function SdkMemoryLibrary({
  active,
  onChanged,
  focusedMemoryId,
}: {
  active: boolean;
  onChanged?(): void;
  focusedMemoryId?: string | null;
}) {
  const { t, tn } = useGuiLanguage();
  const translation = useRef(t);
  translation.current = t;
  const [status, setStatus] = useState<SdkMemoryStatus>("active");
  const [offset, setOffset] = useState(0);
  const [rows, setRows] = useState<SdkMemoryRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [selected, setSelected] = useState<SdkMemoryRecord | null>(null);
  const [draft, setDraft] = useState("");
  // Experiences only: the task, edited next to the solution in `draft`.
  const [taskDraft, setTaskDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const limit = SDK_MEMORY_PAGE_SIZE;

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
      setError(translation.current("sdk.memory.loadFailed"));
    } finally {
      setBusy(false);
    }
  }, [active, offset, status]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const select = useCallback((memory: SdkMemoryRecord) => {
    setSelected(memory);
    setDraft(sdkEditableText(memory));
    setTaskDraft(memory.task ?? "");
  }, []);

  const open = useCallback(
    async (memoryId: string, listed?: SdkMemoryRecord) => {
      setBusy(true);
      setError("");
      try {
        select(
          await getSdkMemory(
            memoryId,
            listed ? sdkNamespace(sdkMemoryRef(listed)) : undefined,
          ),
        );
      } catch {
        setError(translation.current("sdk.memory.detailLoadFailed"));
      } finally {
        setBusy(false);
      }
    },
    [select],
  );

  useEffect(() => {
    if (active && focusedMemoryId) void open(focusedMemoryId);
  }, [active, focusedMemoryId, open]);

  async function save() {
    if (!selected || !draft.trim()) return;
    setBusy(true);
    setError("");
    try {
      const namespace = sdkNamespace(sdkMemoryRef(selected));
      const task =
        selected.kind === "experience" &&
        taskDraft.trim() &&
        taskDraft !== (selected.task ?? "")
          ? taskDraft
          : undefined;
      const memory = await updateSdkMemory(
        selected.memory_id,
        draft,
        undefined,
        namespace && { ...namespace, ...(task ? { task } : {}) },
      );
      select(memory);
      await refresh();
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
      await deleteSdkMemory(
        selected.memory_id,
        undefined,
        sdkNamespace(sdkMemoryRef(selected)),
      );
      setSelected(null);
      await refresh();
      onChanged?.();
    } catch {
      setError(t("sdk.memory.deleteFailed"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="sdk-memory-library" aria-label={t("sdk.memory.title")}>
      <nav aria-label={t("sdk.memory.status")}>
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
            }}
          >
            {label}
          </button>
        ))}
      </nav>
      <p aria-live="polite">{tn("sdk.memory.total", total)}</p>
      {error && <p role="alert">{error}</p>}
      {busy && !rows.length ? (
        <p role="status">{t("sdk.memory.loading")}</p>
      ) : null}
      <ul>
        {rows.map((memory) => {
          const ref = sdkMemoryRef(memory);
          return (
            <li key={`${ref.scene}:${ref.kind}:${memory.memory_id}`}>
              <span className="sdk-memory-scene" data-scene={ref.scene}>
                {t(SCENE_LABELS[ref.scene])}
              </span>
              <button
                type="button"
                onClick={() => void open(memory.memory_id, memory)}
              >
                {memory.content}
              </button>
            </li>
          );
        })}
      </ul>
      <nav aria-label={t("sdk.memory.pagination")}>
        <R6Button
          variant="quiet"
          disabled={busy || offset === 0}
          onClick={() => setOffset(Math.max(0, offset - limit))}
        >
          {t("sdk.memory.previous")}
        </R6Button>
        <span>
          {t("sdk.memory.page", {
            page: total === 0 ? 0 : Math.floor(offset / limit) + 1,
            pages: Math.ceil(total / limit),
          })}
        </span>
        <R6Button
          variant="quiet"
          disabled={busy || offset + limit >= total}
          onClick={() => setOffset(offset + limit)}
        >
          {t("sdk.memory.next")}
        </R6Button>
      </nav>
      {selected ? (
        <aside aria-label={t("sdk.memory.detail")}>
          <p>
            {t(SCENE_LABELS[sdkMemoryRef(selected).scene])} · {selected.layer}
          </p>
          {selected.status === "active" ? (
            <>
              {selected.kind === "experience" ? (
                <textarea
                  aria-label={t("sdk.memory.task")}
                  value={taskDraft}
                  disabled={busy}
                  onChange={(event) => setTaskDraft(event.target.value)}
                />
              ) : null}
              <textarea
                aria-label={
                  selected.kind === "experience"
                    ? t("sdk.memory.solution")
                    : t("sdk.memory.content")
                }
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
                {t("sdk.memory.save")}
              </R6Button>
              <R6Button pending={busy} onClick={() => void remove()}>
                {t("sdk.memory.delete")}
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
