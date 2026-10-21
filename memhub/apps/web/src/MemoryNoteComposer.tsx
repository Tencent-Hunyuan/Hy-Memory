import type {
  MemoryNoteReceipt,
  ModelProfile,
  UiMessage,
} from "@one-memory/contracts";
import { FilePlus2, RefreshCw, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  getMemoryNoteOperation,
  getMemoryNotes,
  getModelProfiles,
  processMemoryNote,
  saveMemoryNote,
} from "./api";
import { memoryWriteErrorText } from "./model-error-text";
import { subscribePoll } from "./poll-scheduler";
import { useTranslate, useUiLanguage } from "./ui-language";
import { traceUiAction } from "./ui-trace";
import { isDefiniteRefusal } from "./write-outcome";

const states: Record<MemoryNoteReceipt["state"], UiMessage> = {
  waiting_for_model: "原话已保存，等待可用模型",
  waiting_for_permission: "原话已保存，等待整理",
  pending: "原话已保存，等待整理",
  running: "正在整理",
  failed: "原话已保存，整理未完成",
  ready: "整理已完成",
  no_memories: "原话已保存，未提取出可用记忆",
};

export interface PendingMemoryNote {
  evidenceId: string;
  text: string;
  state: Exclude<MemoryNoteReceipt["state"], "ready" | "no_memories">;
}

export function MemoryNoteComposer({
  onSaved,
  onOpenMemory,
  onConfigureModel,
  onPendingChange,
  suspended = false,
}: {
  onSaved(): void;
  onOpenMemory(id: string): void;
  onConfigureModel(): void;
  onPendingChange?(note: PendingMemoryNote | null): void;
  suspended?: boolean;
}) {
  const t = useTranslate();
  const language = useUiLanguage();
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [notes, setNotes] = useState<MemoryNoteReceipt[]>([]);
  const [model, setModel] = useState<ModelProfile | null>(null);
  // Before the read settles, `model` is null for the same reason it is null
  // when nothing is configured. Showing the configured-model copy anyway made
  // the line flicker from 尚未配置 to the model name on every open.
  const [modelRead, setModelRead] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<UiMessage | "">("");
  const [notice, setNotice] = useState<UiMessage | "">("");
  const [savedToast, setSavedToast] = useState<UiMessage | "">("");
  const panel = useRef<HTMLElement>(null);
  const isOpen = useRef(false);
  const notesRequest = useRef(0);
  const intent = useRef<{ text: string; requestId: string } | null>(null);
  const completed = useRef(new Set<string>());
  const lastSaved = useRef<string | null>(null);
  const onSavedRef = useRef(onSaved);
  onSavedRef.current = onSaved;
  const onPendingChangeRef = useRef(onPendingChange);
  onPendingChangeRef.current = onPendingChange;

  const reportPending = useCallback((value: MemoryNoteReceipt[]) => {
    const note = value.find(
      (
        item,
      ): item is MemoryNoteReceipt & {
        state: PendingMemoryNote["state"];
      } => item.state !== "ready" && item.state !== "no_memories",
    );
    onPendingChangeRef.current?.(
      note
        ? {
            evidenceId: note.evidence_id,
            text: note.text,
            state: note.state,
          }
        : null,
    );
  }, []);

  const refreshNotes = useCallback(
    async (failure: UiMessage, notifyResults = false) => {
      const request = ++notesRequest.current;
      try {
        const value = await getMemoryNotes();
        if (!isOpen.current || request !== notesRequest.current) return;
        setNotes(value);
        reportPending(value);
        if (!notifyResults) return;
        const latest = value.find(
          (note) => note.evidence_id === lastSaved.current,
        );
        if (latest) setNotice(states[latest.state]);
        const newResults = value.filter(
          (note) =>
            note.state === "ready" && !completed.current.has(note.evidence_id),
        );
        if (newResults.length) {
          for (const note of newResults)
            completed.current.add(note.evidence_id);
          onSavedRef.current();
        }
      } catch {
        if (isOpen.current && request === notesRequest.current)
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
    void refreshNotes("最近添加的内容读取失败。");
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
      notesRequest.current += 1;
      if (before instanceof HTMLElement && before.isConnected) before.focus();
    };
  }, [open, refreshNotes, suspended]);

  useEffect(() => {
    if (open || suspended || !onPendingChange) return;
    void getMemoryNotes()
      .then((value) => reportPending(value))
      .catch(() => {
        // The page already keeps the last known state when a background read
        // fails; do not turn an unknown pending count into zero here.
      });
  }, [open, onPendingChange, reportPending, suspended]);

  const working = notes.some(
    (note) => note.state === "pending" || note.state === "running",
  );
  useEffect(() => {
    if (!open || suspended || !working) return;
    const stop = subscribePoll({
      // The note list and the processing status are two surfaces; sharing one
      // key would make one tick read the wrong endpoint for the other caller.
      key: "note-processing",
      intervalMs: 1_500,
      run: () => refreshNotes("整理状态暂时不可用，原话已保存。", true),
    });
    return stop;
  }, [open, working, refreshNotes, suspended]);

  useEffect(() => {
    if (!savedToast) return;
    const timer = window.setTimeout(() => setSavedToast(""), 3200);
    return () => window.clearTimeout(timer);
  }, [savedToast]);

  function close() {
    if (busy) return;
    isOpen.current = false;
    notesRequest.current += 1;
    setOpen(false);
  }

  function remember(receipt: MemoryNoteReceipt) {
    setNotes((current) => {
      const next = [
        receipt,
        ...current.filter((note) => note.evidence_id !== receipt.evidence_id),
      ];
      reportPending(next);
      return next;
    });
  }

  async function save() {
    if (busy || !text.trim()) return;
    if (intent.current?.text !== text)
      intent.current = { text, requestId: crypto.randomUUID() };
    setBusy(true);
    setError("");
    setNotice("");
    const trace = traceUiAction("memory", "submit");
    const completeSave = (receipt: MemoryNoteReceipt) => {
      remember(receipt);
      lastSaved.current = receipt.evidence_id;
      setText("");
      intent.current = null;
      setNotice("");
      setSavedToast("已保存，正在后台沉淀记忆");
      isOpen.current = false;
      notesRequest.current += 1;
      setOpen(false);
      onSaved();
    };
    try {
      const receipt = await saveMemoryNote(
        {
          text,
          scope: "all-apps",
          request_id: intent.current.requestId,
        },
        trace.traceId,
      );
      completeSave(receipt);
      trace.done("succeeded");
    } catch {
      try {
        completeSave(await getMemoryNoteOperation(intent.current.requestId));
        // The save landed but its response was lost; the note is still saved,
        // so the interaction succeeded even though this attempt did not.
        trace.done("succeeded", { reason: "recovered_from_operation" });
      } catch {
        setError("未能确认保存结果，输入已保留。重试不会重复添加。");
        // The copy already says the result is *unconfirmed*: neither the POST
        // nor the receipt re-read came back. Only a definite refusal is a
        // failure, and here there is none, so the log must not claim one.
        trace.done("unknown", { reason: "save_result_unconfirmed" });
      }
    } finally {
      setBusy(false);
    }
  }

  async function retry(note: MemoryNoteReceipt) {
    if (busy) return;
    setBusy(true);
    setError("");
    // Starting processing is a real write, so it needs its own chain.
    const trace = traceUiAction("memory", "retry");
    try {
      remember(await processMemoryNote(note.evidence_id, trace.traceId));
      trace.done("succeeded");
    } catch (reason) {
      const refused = isDefiniteRefusal(reason);
      trace.done(refused ? "failed" : "unknown", {
        reason: refused ? "note_process_failed" : "note_process_result_unknown",
      });
      // Only a definite refusal may be phrased as "did not start"; a timeout or
      // 5xx means the outcome is unknown, and saying it failed would be a claim
      // the UI cannot support.
      setError(
        refused
          ? "整理未能开始，原话仍已保存。"
          : "整理结果尚未确认，原话仍已保存。请刷新后核对。",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button
        type="button"
        className="secondary memory-note-trigger"
        data-tour-id="memory-note-trigger"
        onClick={() => setOpen(true)}
      >
        <FilePlus2 size={16} aria-hidden="true" /> {t("添加记忆")}
      </button>
      {savedToast && (
        <p className="memory-note-toast" role="status">
          {t(savedToast)}
        </p>
      )}
      {open && (
        <div className="consent-backdrop" hidden={suspended}>
          <section
            ref={panel}
            className="consent-sheet memory-note-sheet"
            {...(suspended ? {} : { role: "dialog", "aria-modal": true })}
            aria-labelledby="memory-note-title"
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
            <header className="memory-note-heading">
              <h2 id="memory-note-title">{t("添加记忆")}</h2>
              <button
                className="quiet"
                type="button"
                aria-label={t("关闭新增记忆")}
                title={t("关闭")}
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
              <label className="memory-note-label" htmlFor="memory-note-text">
                {t("记下这件事")}
              </label>
              <textarea
                id="memory-note-text"
                rows={5}
                maxLength={12000}
                value={text}
                disabled={busy}
                placeholder={t("例如：项目周会不排在周五。")}
                onChange={(event) => setText(event.target.value)}
              />
              <p className="memory-note-model">
                {t("整理模型：")}
                {model?.name ??
                  (modelRead ? t("尚未配置，原话仍可保存") : t("正在读取"))}
              </p>
              {error && (
                <p className="error-banner" role="alert">
                  {t(error)}
                </p>
              )}
              {notice && <p role="status">{t(notice)}</p>}
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
                    {t("配置模型")}
                  </button>
                )}
                <button type="submit" disabled={busy || !text.trim()}>
                  {busy ? t("保存中…") : t("保存原话")}
                </button>
              </div>
            </form>
            {notes.length > 0 && (
              <section
                className="memory-note-recent"
                aria-label={t("最近添加")}
              >
                <h3>{t("最近添加")}</h3>
                <ul>
                  {notes.map((note) => {
                    const writeError = memoryWriteErrorText(note.error);
                    return (
                      <li key={note.evidence_id}>
                        <p>{note.text}</p>
                        <small>{t(states[note.state])}</small>
                        {writeError && (
                          <p className="error-banner">{t(writeError)}</p>
                        )}
                        {note.error === "hy_memory_model_payment_required" && (
                          <p className="error-banner">
                            {t(
                              "模型服务要求检查余额或付款状态，原话仍已保存。",
                            )}
                          </p>
                        )}
                        <div className="memory-note-actions">
                          {note.memory_ids.map((id, index) => (
                            <button
                              key={id}
                              type="button"
                              className="quiet"
                              onClick={() => {
                                close();
                                onOpenMemory(id);
                              }}
                            >
                              {t("查看记忆")}
                              {note.memory_ids.length > 1
                                ? ` ${(index + 1).toLocaleString(language)}`
                                : ""}
                            </button>
                          ))}
                          {(note.state === "failed" ||
                            note.state === "waiting_for_model" ||
                            note.state === "waiting_for_permission") && (
                            <button
                              type="button"
                              className="quiet"
                              disabled={busy}
                              onClick={() => void retry(note)}
                            >
                              <RefreshCw size={14} aria-hidden="true" />
                              {t("重新整理")}
                            </button>
                          )}
                        </div>
                      </li>
                    );
                  })}
                </ul>
              </section>
            )}
          </section>
        </div>
      )}
    </>
  );
}
