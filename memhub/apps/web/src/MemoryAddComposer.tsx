import type { MemoryAddReceipt, ModelProfile } from "@one-memory/contracts";
import { FilePlus2, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { addMemory, getMemoryAddStatus, getModelProfiles } from "./api";
import { useMemoryLanguage } from "./memory-language";
import { memoryWriteErrorText } from "./model-error-text";
import { subscribePoll } from "./poll-scheduler";
import { traceUiAction } from "./ui-trace";

const states: Record<MemoryAddReceipt["state"], string> = {
  waiting_for_model: "原话已保存，等待可用模型",
  waiting_for_permission: "正在等待整理",
  pending: "正在等待整理",
  running: "正在整理",
  failed: "原话已保存，整理未完成",
  ready: "整理已完成",
  no_memories: "原话已保存，未提取出可用记忆",
};

export interface PendingMemoryAdd {
  evidenceId: string;
  text: string;
  state: Exclude<MemoryAddReceipt["state"], "ready" | "no_memories">;
}

/** One addition this device made, with the request id its status is read by. */
interface Addition {
  requestId: string;
  receipt: MemoryAddReceipt;
}

/** Core has no list of additions: this device remembers the request ids it
 * submitted and reads each one's status from
 * `GET /api/v2/memories/additions/{request_id}`. */
export const RECENT_ADDITIONS_KEY = "memhub.memory-additions.recent";
const RECENT_ADDITIONS_MAX = 20;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

function readRecentAdditions(): string[] {
  try {
    const value: unknown = JSON.parse(
      window.localStorage.getItem(RECENT_ADDITIONS_KEY) ?? "[]",
    );
    if (!Array.isArray(value)) return [];
    return value
      .filter((id): id is string => typeof id === "string" && UUID.test(id))
      .slice(0, RECENT_ADDITIONS_MAX);
  } catch {
    return [];
  }
}

function writeRecentAdditions(ids: string[]) {
  try {
    window.localStorage.setItem(
      RECENT_ADDITIONS_KEY,
      JSON.stringify(ids.slice(0, RECENT_ADDITIONS_MAX)),
    );
  } catch {
    // Storage is a convenience for the recent list; the memory is saved.
  }
}

function trackRecentAddition(requestId: string) {
  writeRecentAdditions([
    requestId,
    ...readRecentAdditions().filter((id) => id !== requestId),
  ]);
}

function isNotFound(reason: unknown): boolean {
  return (
    !!reason &&
    typeof reason === "object" &&
    (reason as { response?: { status?: unknown } }).response?.status === 404
  );
}

/** Read the status of every remembered addition. Unknown ids (404) are
 * forgotten; any other failure throws so the caller keeps its last state. */
async function loadRecentAdditions(): Promise<Addition[]> {
  const ids = readRecentAdditions();
  const results = await Promise.allSettled(
    ids.map((id) => getMemoryAddStatus(id)),
  );
  const additions: Addition[] = [];
  const gone = new Set<string>();
  results.forEach((result, index) => {
    const requestId = ids[index] as string;
    if (result.status === "fulfilled")
      additions.push({ requestId, receipt: result.value });
    else if (isNotFound(result.reason)) gone.add(requestId);
    else throw result.reason;
  });
  if (gone.size)
    writeRecentAdditions(readRecentAdditions().filter((id) => !gone.has(id)));
  return additions;
}

export function MemoryAddComposer({
  onSaved,
  onOpenMemory,
  onConfigureModel,
  onPendingChange,
  suspended = false,
}: {
  onSaved(): void;
  onOpenMemory(id: string): void;
  onConfigureModel(): void;
  onPendingChange?(addition: PendingMemoryAdd | null): void;
  suspended?: boolean;
}) {
  const { text: copy } = useMemoryLanguage();
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [additions, setAdditions] = useState<Addition[]>([]);
  const [model, setModel] = useState<ModelProfile | null>(null);
  // Before the read settles, `model` is null for the same reason it is null
  // when nothing is configured. Showing the configured-model copy anyway made
  // the line flicker from 尚未配置 to the model name on every open.
  const [modelRead, setModelRead] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [savedToast, setSavedToast] = useState("");
  const panel = useRef<HTMLElement>(null);
  const isOpen = useRef(false);
  const additionsRequest = useRef(0);
  const intent = useRef<{ text: string; requestId: string } | null>(null);
  const completed = useRef(new Set<string>());
  const lastSaved = useRef<string | null>(null);
  const onSavedRef = useRef(onSaved);
  onSavedRef.current = onSaved;
  const onPendingChangeRef = useRef(onPendingChange);
  onPendingChangeRef.current = onPendingChange;

  const reportPending = useCallback((value: Addition[]) => {
    const pending = value
      .map((item) => item.receipt)
      .find(
        (
          item,
        ): item is MemoryAddReceipt & {
          state: PendingMemoryAdd["state"];
        } => item.state !== "ready" && item.state !== "no_memories",
      );
    onPendingChangeRef.current?.(
      pending
        ? {
            evidenceId: pending.evidence_id,
            text: pending.text,
            state: pending.state,
          }
        : null,
    );
  }, []);

  const refreshAdditions = useCallback(
    async (failure: string, notifyResults = false) => {
      const request = ++additionsRequest.current;
      try {
        const value = await loadRecentAdditions();
        if (!isOpen.current || request !== additionsRequest.current) return;
        setAdditions(value);
        reportPending(value);
        if (!notifyResults) return;
        const latest = value.find(
          (item) => item.receipt.evidence_id === lastSaved.current,
        );
        if (latest) setNotice(states[latest.receipt.state]);
        const newResults = value.filter(
          ({ receipt }) =>
            receipt.state === "ready" &&
            !completed.current.has(receipt.evidence_id),
        );
        if (newResults.length) {
          for (const { receipt } of newResults)
            completed.current.add(receipt.evidence_id);
          onSavedRef.current();
        }
      } catch {
        if (isOpen.current && request === additionsRequest.current)
          setError(failure);
      }
    },
    [reportPending],
  );

  useEffect(() => {
    if (!open || suspended) return;
    isOpen.current = true;
    let cancelled = false;
    const before = document.activeElement;
    panel.current?.querySelector("textarea")?.focus();
    void refreshAdditions("最近添加的内容读取失败。");
    setModelRead(false);
    getModelProfiles()
      .then((value) => {
        if (cancelled) return;
        setModel(value.find((p) => p.is_active) ?? null);
        setModelRead(true);
      })
      .catch(() => {
        if (cancelled) return;
        setModel(null);
        setModelRead(true);
      });
    return () => {
      cancelled = true;
      isOpen.current = false;
      additionsRequest.current += 1;
      if (before instanceof HTMLElement && before.isConnected) before.focus();
    };
  }, [open, refreshAdditions, suspended]);

  useEffect(() => {
    if (open || suspended || !onPendingChange) return;
    void loadRecentAdditions()
      .then((value) => reportPending(value))
      .catch(() => {
        // The page already keeps the last known state when a background read
        // fails; do not turn an unknown pending count into zero here.
      });
  }, [open, onPendingChange, reportPending, suspended]);

  const working = additions.some(
    ({ receipt }) => receipt.state === "pending" || receipt.state === "running",
  );
  useEffect(() => {
    if (!open || suspended || !working) return;
    const stop = subscribePoll({
      // A key of its own: sharing one with another surface would make one
      // tick read the wrong endpoint for the other caller.
      key: "memory-add-processing",
      intervalMs: 1_500,
      run: () => refreshAdditions("整理状态暂时不可用，原话已保存。", true),
    });
    return stop;
  }, [open, working, refreshAdditions, suspended]);

  useEffect(() => {
    if (!savedToast) return;
    const timer = window.setTimeout(() => setSavedToast(""), 3200);
    return () => window.clearTimeout(timer);
  }, [savedToast]);

  function close() {
    if (busy) return;
    isOpen.current = false;
    additionsRequest.current += 1;
    setOpen(false);
  }

  function remember(addition: Addition) {
    trackRecentAddition(addition.requestId);
    setAdditions((current) => {
      const next = [
        addition,
        ...current.filter((item) => item.requestId !== addition.requestId),
      ];
      reportPending(next);
      return next;
    });
  }

  async function save() {
    if (busy || !text.trim()) return;
    if (intent.current?.text !== text)
      intent.current = { text, requestId: crypto.randomUUID() };
    const requestId = intent.current.requestId;
    setBusy(true);
    setError("");
    setNotice("");
    const trace = traceUiAction("memory", "submit");
    const completeSave = (receipt: MemoryAddReceipt) => {
      remember({ requestId, receipt });
      lastSaved.current = receipt.evidence_id;
      setText("");
      intent.current = null;
      setNotice("");
      setSavedToast("已保存，正在后台沉淀记忆");
      isOpen.current = false;
      additionsRequest.current += 1;
      setOpen(false);
      onSaved();
    };
    try {
      const receipt = await addMemory(
        {
          text,
          scope: "all-apps",
          request_id: requestId,
        },
        trace.traceId,
      );
      completeSave(receipt);
      trace.done("succeeded");
    } catch {
      try {
        completeSave(await getMemoryAddStatus(requestId));
        // The save landed but its response was lost; the memory is still
        // saved, so the interaction succeeded even though this attempt did not.
        trace.done("succeeded", { reason: "recovered_from_operation" });
      } catch {
        setError("未能确认保存结果，输入已保留。重试不会重复添加。");
        // The copy already says the result is *unconfirmed*: neither the POST
        // nor the status re-read came back. Only a definite refusal is a
        // failure, and here there is none, so the log must not claim one.
        trace.done("unknown", { reason: "save_result_unconfirmed" });
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button
        type="button"
        className="secondary memory-add-trigger"
        data-tour-id="memory-add-trigger"
        onClick={() => setOpen(true)}
      >
        <FilePlus2 size={16} aria-hidden="true" /> {copy("添加记忆")}
      </button>
      {savedToast && (
        <p className="memory-add-toast" role="status">
          {copy(savedToast)}
        </p>
      )}
      {open && (
        <div className="consent-backdrop" hidden={suspended}>
          <section
            ref={panel}
            className="consent-sheet memory-add-sheet"
            {...(suspended ? {} : { role: "dialog", "aria-modal": true })}
            aria-labelledby="memory-add-title"
            onKeyDown={(event) => {
              if (suspended) return;
              if (event.key === "Escape") {
                event.preventDefault();
                event.stopPropagation();
                close();
              }
              if (event.key !== "Tab") return;
              const controls = [
                ...(panel.current?.querySelectorAll<HTMLElement>(
                  "button:not(:disabled), textarea:not(:disabled), input:not(:disabled)",
                ) ?? []),
              ];
              const first = controls[0],
                last = controls.at(-1);
              if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last?.focus();
              } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first?.focus();
              }
            }}
          >
            <header className="memory-add-heading">
              <h2 id="memory-add-title">{copy("添加记忆")}</h2>
              <button
                className="quiet"
                type="button"
                aria-label={copy("关闭新增记忆")}
                title={copy("关闭")}
                disabled={busy}
                onClick={close}
              >
                <X size={18} />
              </button>
            </header>
            <form
              onSubmit={(event) => {
                event.preventDefault();
                void save();
              }}
            >
              <label className="memory-add-label" htmlFor="memory-add-text">
                {copy("记下这件事")}
              </label>
              <textarea
                id="memory-add-text"
                rows={5}
                maxLength={12000}
                value={text}
                disabled={busy}
                placeholder={copy("例如：项目周会不排在周五。")}
                onChange={(event) => setText(event.target.value)}
              />
              <p className="memory-add-model">
                {copy("整理模型：")}
                {model?.name ??
                  copy(modelRead ? "尚未配置，原话仍可保存" : "正在读取")}
              </p>
              {error && (
                <p className="error-banner" role="alert">
                  {copy(error)}
                </p>
              )}
              {notice && <p role="status">{copy(notice)}</p>}
              <div className="consent-actions">
                {modelRead && !model && (
                  <button
                    type="button"
                    className="secondary"
                    disabled={busy}
                    onClick={() => {
                      close();
                      onConfigureModel();
                    }}
                  >
                    {copy("配置模型")}
                  </button>
                )}
                <button type="submit" disabled={busy || !text.trim()}>
                  {copy(busy ? "保存中…" : "保存")}
                </button>
              </div>
            </form>
            {additions.length > 0 && (
              <section
                className="memory-add-recent"
                aria-label={copy("最近添加")}
              >
                <h3>{copy("最近添加")}</h3>
                <ul>
                  {additions.map(({ requestId, receipt }) => (
                    <li key={requestId}>
                      <p>{receipt.text}</p>
                      <small>{copy(states[receipt.state])}</small>
                      {memoryWriteErrorText(receipt.error) && (
                        <p className="error-banner">
                          {copy(memoryWriteErrorText(receipt.error) ?? "")}
                        </p>
                      )}
                      {receipt.error === "hy_memory_model_payment_required" && (
                        <p className="error-banner">
                          {copy(
                            "模型服务要求检查余额或付款状态，原话仍已保存。",
                          )}
                        </p>
                      )}
                      {receipt.memory_ids.length > 0 && (
                        <div className="memory-add-actions">
                          {receipt.memory_ids.map((id, index) => (
                            <button
                              key={id}
                              type="button"
                              className="quiet"
                              onClick={() => {
                                close();
                                onOpenMemory(id);
                              }}
                            >
                              {copy("查看记忆")}
                              {receipt.memory_ids.length > 1
                                ? ` ${index + 1}`
                                : ""}
                            </button>
                          ))}
                        </div>
                      )}
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </section>
        </div>
      )}
    </>
  );
}
