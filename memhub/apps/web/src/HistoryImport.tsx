import type {
  ConnectSourcesResponse,
  FileImportReceipt,
  SourceResponse,
} from "@one-memory/contracts";
import { Check, Clipboard, FileUp, RefreshCw, Save, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  apiErrorCode,
  commitHistoryFile,
  discardHistoryFile,
  getHistoryFile,
  historyImportIssue,
  reconcileHistoryUpload,
  uploadHistoryFile,
} from "./api";
import { readPendingFileUpload } from "./pending-file-upload";
import { traceUiAction } from "./ui-trace";

const errors: Record<string, string> = {
  unsupported_export_schema: "暂不支持这份文件的对话格式，未导入任何内容。",
  export_size_exceeded:
    "文件或解压后的总内容超过 128 MB，请选择 conversations.json。",
  ambiguous_export_payload:
    "压缩包里没有唯一的 conversations.json，请直接选择该文件。",
  unsafe_export_archive: "压缩包未通过安全检查，未解压或导入。",
  invalid_export_json: "这份 JSON 文件不完整或编码不受支持。",
  export_has_no_text_conversations: "文件里没有可导入的文本对话。",
  duplicate_export_identity: "文件里有重复的会话或消息身份，未导入任何内容。",
  export_record_invalid: "文件中有超长或无法读取的对话记录，未导入任何内容。",
  imports_disabled: "当前实例禁用了导入，请在完整 App 中使用。",
  generic_import_invalid: "对话格式不完整，未导入任何内容。",
  unsupported_memory_content_schema:
    "这不是已支持的记忆文件格式。可选择 TXT、Markdown 或 one-memory-content-v1 JSON；ZIP 和厂商专有格式尚未接入。",
  invalid_memory_content_record:
    "已有记忆记录为空或字段不受支持，未导入任何内容。",
  invalid_memory_content_time:
    "已有记忆时间缺少日期或时区，未知时间请保留为 null。",
};

export function HistoryImport({
  onConnect,
  onRefresh,
  disabled = false,
}: {
  disabled?: boolean;
  onConnect(
    ids: string[],
    snapshot: SourceResponse[],
    options?: { retryFailed?: boolean; importUploadId?: string },
  ): Promise<ConnectSourcesResponse>;
  onRefresh(): Promise<{ sources: SourceResponse[] }>;
}) {
  const [open, setOpen] = useState(false);
  const [receipt, setReceipt] = useState<FileImportReceipt | null>(null);
  const [selectionValid, setSelectionValid] = useState(false);
  const [consent, setConsent] = useState(false);
  const [candidate, setCandidate] = useState<SourceResponse | null>(null);
  const [saveUnknown, setSaveUnknown] = useState(false);
  const [pendingUpload, setPendingUpload] = useState<string | null>(null);
  const [uploadStorageFailed, setUploadStorageFailed] = useState(false);
  const [connection, setConnection] = useState<"new" | "failed" | "unknown">(
    "new",
  );
  const [operation, setOperation] = useState<
    "upload" | "save" | "refresh" | "connect" | "discard" | null
  >(null);
  const busy = operation !== null;
  const inputLocked =
    busy || saveUnknown || !!pendingUpload || uploadStorageFailed;
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [mode, setMode] = useState<"file" | "paste">("file");
  const [inputKind, setInputKind] = useState<
    "raw_conversation" | "memory_content"
  >("raw_conversation");
  const isMemoryContent = inputKind === "memory_content";
  const [paste, setPaste] = useState("");
  const [issue, setIssue] =
    useState<ReturnType<typeof historyImportIssue>>(null);
  const panel = useRef<HTMLElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const wasOpen = useRef(false);
  const scopeConsent = useRef<HTMLInputElement>(null);
  const alive = useRef(true);
  const generation = useRef(0);
  const flight = useRef<number | null>(null);
  const closing = useRef(false);
  const discards = useRef(new Map<string, Promise<void>>());
  const tracked = useRef<{
    receipt: FileImportReceipt;
    uncertain: boolean;
  } | null>(null);
  const discardStaged = useCallback((id: string) => {
    const pending = discards.current.get(id);
    if (pending) return pending;
    const request = discardHistoryFile(id).finally(() =>
      discards.current.delete(id),
    );
    discards.current.set(id, request);
    return request;
  }, []);
  const updateUploadPending = useCallback(() => {
    try {
      const pending = readPendingFileUpload();
      setPendingUpload(pending);
      setUploadStorageFailed(false);
      return pending;
    } catch {
      setUploadStorageFailed(true);
      throw new Error("file_upload_storage_unavailable");
    }
  }, []);
  useEffect(() => {
    alive.current = true;
    try {
      updateUploadPending();
    } catch {
      /* Preserve the local recovery fence. */
    }
    return () => {
      alive.current = false;
      generation.current += 1;
      const previous = tracked.current;
      tracked.current = null;
      if (previous && !previous.receipt.committed && !previous.uncertain) {
        void discardStaged(previous.receipt.upload_id).catch(() => {});
      }
    };
  }, [discardStaged, updateUploadPending]);
  useEffect(() => {
    if (open)
      panel.current
        ?.querySelector<HTMLInputElement>("input")
        ?.focus({ preventScroll: true });
    else if (wasOpen.current) trigger.current?.focus({ preventScroll: true });
    wasOpen.current = open;
  }, [open]);
  useEffect(() => {
    if (candidate && !busy) scopeConsent.current?.focus();
  }, [candidate, busy]);

  function start(next: NonNullable<typeof operation>) {
    if (flight.current !== null || !alive.current) return null;
    const token = ++generation.current;
    flight.current = token;
    setOperation(next);
    setError("");
    setIssue(null);
    return token;
  }

  function active(token: number) {
    return alive.current && token === generation.current;
  }

  function finish(token: number) {
    if (!active(token)) return;
    flight.current = null;
    setOperation(null);
  }

  function invalidateSelection() {
    setConsent(false);
    setSelectionValid(false);
    setCandidate(null);
    setConnection("new");
  }

  function remember(next: FileImportReceipt, uncertain = false) {
    tracked.current = { receipt: next, uncertain };
    setReceipt(next);
    setSaveUnknown(uncertain);
  }

  async function discardPrevious() {
    const previous = tracked.current;
    if (previous && !previous.receipt.committed && !previous.uncertain) {
      await discardStaged(previous.receipt.upload_id);
    }
    if (tracked.current === previous) tracked.current = null;
  }

  async function reviewSource(saved: FileImportReceipt, token: number) {
    setOperation("refresh");
    const result = await onRefresh();
    if (!active(token)) return;
    const matches = result.sources.filter(
      (source) => source.source_id === saved.source_id,
    );
    const source = matches[0];
    if (
      matches.length !== 1 ||
      !source.available ||
      !source.candidate_root_id?.trim() ||
      !source.candidate_path?.trim() ||
      !["default", "environment_override"].includes(source.candidate_kind) ||
      (source.location_state && source.location_state !== "found")
    )
      throw new Error("source_identity_unavailable");
    // Copy the displayed candidate, never replace it during the later consent click.
    setCandidate({ ...source });
    setConsent(false);
    setConnection(
      candidate?.source_id === source.source_id &&
        candidate.candidate_root_id === source.candidate_root_id &&
        candidate.candidate_path === source.candidate_path &&
        candidate.candidate_kind === source.candidate_kind
        ? connection
        : "new",
    );
  }

  async function select(file: File | undefined) {
    if (!file || inputLocked) return;
    const token = start("upload");
    if (token === null) return;
    invalidateSelection();
    const trace = traceUiAction("sources", "open");
    let settled = false;
    const settle = (outcome: "succeeded" | "failed" | "unknown") => {
      if (settled) return;
      settled = true;
      trace.done(outcome);
    };
    try {
      await discardPrevious();
      if (!active(token)) return;
      setReceipt(null);
      setSaveUnknown(false);
      if (file.size > 128 * 1024 * 1024) {
        setError(errors.export_size_exceeded);
        settle("failed");
        return;
      }
      const next = await uploadHistoryFile(file, inputKind, trace.traceId);
      settle("succeeded");
      if (!active(token)) {
        if (!next.committed)
          await discardStaged(next.upload_id).catch(() => {});
        return;
      }
      remember(next);
      setSelectionValid(true);
    } catch (reason) {
      settle(apiErrorCode(reason) ? "failed" : "unknown");
      if (!active(token)) return;
      try {
        updateUploadPending();
      } catch {
        /* Keep controls locked. */
      }
      setIssue(historyImportIssue(reason));
      setError(
        errors[apiErrorCode(reason) ?? ""] ??
          "未能读取这份历史包。请检查文件后重试。",
      );
    } finally {
      // An early return before any request (e.g. the selection was invalidated)
      // would otherwise leave a `ui.action` with no matching `ui.result`, which
      // the native verifier reports as an incomplete chain. `settle` is
      // idempotent, so this is a no-op once a real outcome was recorded.
      settle("unknown");
      finish(token);
    }
  }

  async function changeInput(update: () => void) {
    if (inputLocked) return;
    const token = start("discard");
    if (token === null) return;
    invalidateSelection();
    try {
      await discardPrevious();
      if (!active(token)) return;
      setReceipt(null);
      setSaveUnknown(false);
      update();
    } catch {
      if (active(token)) setError("暂存文件未能清理，请重试切换。");
    } finally {
      finish(token);
    }
  }

  async function recoverUpload() {
    const token = start("refresh");
    if (token === null) return;
    invalidateSelection();
    try {
      const next = await reconcileHistoryUpload();
      if (!active(token)) {
        if (next && !next.committed)
          await discardStaged(next.upload_id).catch(() => {});
        return;
      }
      updateUploadPending();
      if (next) {
        remember(next);
        setInputKind(next.content_kind ?? "raw_conversation");
        setSelectionValid(true);
        setNotice("上传结果已找回，尚未新增读取授权。请核对内容后继续。");
        if (next.committed) await reviewSource(next, token);
      }
    } catch {
      if (active(token)) {
        let pending: string | null = null;
        try {
          pending = updateUploadPending();
        } catch {
          /* Storage failure is separate. */
        }
        setError(
          pending
            ? "上传结果仍待核对，不会重新上传。请稍后再次核对。"
            : "上传结果暂不可用，请核对文件状态；本次未提交新的读取授权。",
        );
      }
    } finally {
      finish(token);
    }
  }

  async function save() {
    if (
      !receipt ||
      receipt.committed ||
      saveUnknown ||
      !selectionValid ||
      !consent
    )
      return;
    const token = start("save");
    if (token === null) return;
    setConsent(false);
    // A lost POST response is not evidence that publishing failed.
    remember(receipt, true);
    let saved = false;
    const trace = traceUiAction("sources", "confirm");
    let settled = false;
    const settle = (outcome: "succeeded" | "failed" | "unknown") => {
      if (settled) return;
      settled = true;
      trace.done(outcome);
    };
    try {
      const next = await commitHistoryFile(receipt.upload_id, trace.traceId);
      if (!active(token)) return;
      assertFileReceipt(receipt, next);
      if (!next.committed) throw new Error("unconfirmed_commit");
      remember(next);
      saved = true;
      await reviewSource(next, token);
      settle("succeeded");
    } catch (reason) {
      settle(saved ? "unknown" : apiErrorCode(reason) ? "failed" : "unknown");
      if (active(token))
        setError(
          saved
            ? "文件已保存，但来源位置或身份尚未核对成功。请重新核对来源，尚未提交读取授权。"
            : "文件保存结果尚未确认。请先核对文件回执，不会直接重复保存或授权。",
        );
    } finally {
      // See `select()`: an early return after the request still needs a
      // `ui.result`, and `settle` makes this idempotent.
      settle("unknown");
      finish(token);
    }
  }

  async function recover() {
    if (!receipt) return;
    const token = start("refresh");
    if (token === null) return;
    setCandidate(null);
    setConsent(false);
    setConnection("new");
    try {
      const next = await getHistoryFile(receipt.upload_id);
      if (!active(token)) return;
      assertFileReceipt(receipt, next);
      if (receipt.committed && !next.committed)
        throw new Error("commit_receipt_regressed");
      remember(next);
      if (next.committed) await reviewSource(next, token);
    } catch {
      if (active(token))
        setError(
          receipt.committed
            ? "文件已保存，但文件回执或来源身份未能核对。保留已保存文件，请重试核对。"
            : "暂时无法核对文件保存结果。保留原回执，不会重复保存或授权。",
        );
    } finally {
      finish(token);
    }
  }

  async function confirm() {
    if (!receipt?.committed || !candidate || !selectionValid || !consent)
      return;
    const token = start("connect");
    if (token === null) return;
    setConsent(false);
    try {
      const result = await onConnect([receipt.source_id], [{ ...candidate }], {
        importUploadId: receipt.upload_id,
        ...(connection === "failed" ? { retryFailed: true } : {}),
      });
      if (!active(token)) return;
      const accepted = result.accepted_source_ids;
      const failed = Object.keys(result.failed_sources);
      if (
        accepted.length === 0 &&
        failed.length === 1 &&
        failed[0] === receipt.source_id
      ) {
        setConnection("failed");
        setError(
          "此来源未能连接，文件已保存。核对上述目录后可明确重试此来源；位置变化时请先重新核对来源。",
        );
        return;
      }
      if (
        accepted.length !== 1 ||
        accepted[0] !== receipt.source_id ||
        failed.length
      )
        throw new Error("invalid_connection_receipt");
      setNotice(
        isMemoryContent
          ? "已取得来源连接回执。已有记忆的内容核对、模型处理和使用范围仍需单独确认。"
          : "已取得来源连接回执，导入进度以对应来源任务为准。模型处理与使用权限未改变。",
      );
      tracked.current = null;
      setReceipt(null);
      invalidateSelection();
      setOpen(false);
    } catch {
      if (active(token)) {
        setConnection("unknown");
        setError(
          "连接结果尚未确认，文件已保存。请核对连接回执；来源变化时需重新核对并确认，不会自动重新授权。",
        );
      }
    } finally {
      finish(token);
    }
  }

  async function close() {
    if (closing.current || operation === "discard") return;
    closing.current = true;
    const previousOperation = operation;
    const previous = tracked.current;
    const token = ++generation.current;
    flight.current = token;
    setOperation("discard");
    invalidateSelection();
    try {
      await discardPrevious();
      if (!active(token)) return;
      setReceipt(null);
      setSaveUnknown(false);
      setError("");
      setIssue(null);
      if (previous?.receipt.committed || previous?.uncertain) {
        setNotice(
          previousOperation === "connect" || connection === "unknown"
            ? "已关闭确认。已发送的连接请求可能继续，请在对应来源核对结果；关闭不会撤销授权或取消任务。"
            : previous?.uncertain
              ? "已关闭确认。文件保存请求可能已提交，请在来源中核对；未提交新的读取授权。"
              : "文件已保留在本机，未提交新的读取授权。",
        );
      }
      setOpen(false);
    } catch {
      if (active(token)) setError("暂存文件未能清理，请重试关闭。");
    } finally {
      closing.current = false;
      finish(token);
    }
  }

  return (
    <>
      <button
        type="button"
        className="secondary history-import-trigger"
        ref={trigger}
        disabled={open || disabled}
        onClick={() => {
          try {
            updateUploadPending();
          } catch {
            /* Show the recovery state on open. */
          }
          setOpen(true);
          setNotice("");
        }}
      >
        <FileUp size={16} />
        导入历史包
      </button>
      {notice && (
        <p role="status" className="history-import-notice">
          {notice}
        </p>
      )}
      {open && (
        <div className="consent-backdrop">
          <section
            className="consent-sheet history-import-sheet"
            role="dialog"
            aria-modal="true"
            aria-labelledby="history-import-title"
            ref={panel}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                event.preventDefault();
                void close();
              }
              if (event.key !== "Tab") return;
              const controls = [
                ...(panel.current?.querySelectorAll<HTMLElement>(
                  "button:not(:disabled), input:not(:disabled), textarea:not(:disabled), summary",
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
              <h2 id="history-import-title">导入历史包</h2>
              <button
                type="button"
                className="quiet"
                aria-label="关闭历史包导入"
                title="关闭"
                disabled={operation === "discard"}
                onClick={() => void close()}
              >
                <X size={18} />
              </button>
            </header>
            {(pendingUpload || uploadStorageFailed) && (
              <div role="status" className="model-refresh-notice">
                <p>
                  {uploadStorageFailed
                    ? "无法读取本地上传恢复记录，暂不开始新的上传。"
                    : "上次上传结果待核对，不会重复上传或自动导入。"}
                </p>
                <button
                  type="button"
                  disabled={busy || uploadStorageFailed}
                  onClick={() => void recoverUpload()}
                >
                  核对上传结果
                </button>
              </div>
            )}
            <fieldset
              className="history-content-kind"
              aria-label="内容类型"
              disabled={inputLocked}
            >
              <label>
                <input
                  type="radio"
                  name="history-kind"
                  value="raw_conversation"
                  checked={!isMemoryContent}
                  onChange={() =>
                    void changeInput(() => {
                      setInputKind("raw_conversation");
                      setPaste("");
                    })
                  }
                />
                历史对话
              </label>
              <label>
                <input
                  type="radio"
                  name="history-kind"
                  value="memory_content"
                  checked={isMemoryContent}
                  onChange={() =>
                    void changeInput(() => {
                      setInputKind("memory_content");
                      setPaste("");
                    })
                  }
                />
                已有记忆
              </label>
            </fieldset>
            <fieldset
              className="map-axis import-input-mode"
              aria-label="导入方式"
            >
              <button
                type="button"
                disabled={inputLocked}
                aria-pressed={mode === "file"}
                onClick={() => {
                  if (mode !== "file") void changeInput(() => setMode("file"));
                }}
              >
                <FileUp size={15} aria-hidden="true" />
                选择文件
              </button>
              <button
                type="button"
                disabled={inputLocked}
                aria-pressed={mode === "paste"}
                onClick={() => {
                  if (mode !== "paste")
                    void changeInput(() => setMode("paste"));
                }}
              >
                <Clipboard size={15} aria-hidden="true" />
                {isMemoryContent ? "粘贴内容" : "粘贴对话"}
              </button>
            </fieldset>
            {mode === "file" ? (
              <label className="history-file-label">
                历史数据文件
                <input
                  key={inputKind}
                  type="file"
                  accept={
                    isMemoryContent ? ".json,.md,.txt" : ".json,.zip,.md,.txt"
                  }
                  name="history-file"
                  disabled={inputLocked}
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    event.target.value = "";
                    void select(file);
                  }}
                />
              </label>
            ) : selectionValid ? (
              <button
                type="button"
                className="quiet"
                disabled={inputLocked}
                onClick={() => {
                  invalidateSelection();
                }}
              >
                编辑原文
              </button>
            ) : (
              <div className="history-paste">
                <label htmlFor="history-paste-text">
                  {isMemoryContent ? "已有记忆原文" : "对话原文"}
                </label>
                <textarea
                  id="history-paste-text"
                  name="history-paste-text"
                  autoComplete="off"
                  spellCheck={false}
                  rows={7}
                  disabled={inputLocked}
                  value={paste}
                  placeholder={
                    isMemoryContent
                      ? "已有记忆内容…"
                      : "User: 原始发言…\nAssistant: 原始回复…"
                  }
                  onChange={(event) => {
                    setPaste(event.target.value);
                    setSelectionValid(false);
                    setConsent(false);
                    setError("");
                    setIssue(null);
                  }}
                />
                <button
                  type="button"
                  className="secondary"
                  disabled={inputLocked || !paste.trim()}
                  onClick={() =>
                    void select(
                      new File(
                        [paste],
                        isMemoryContent
                          ? paste.trimStart().startsWith("{")
                            ? "pasted-memory.json"
                            : "pasted-memory.txt"
                          : paste.trimStart().startsWith("[")
                            ? "pasted-conversation.json"
                            : "pasted-conversation.txt",
                        { type: "text/plain" },
                      ),
                    )
                  }
                >
                  预览内容
                </button>
              </div>
            )}
            <p className="history-format-note">
              {isMemoryContent
                ? "TXT / Markdown 或 one-memory-content-v1 JSON。原始对话未提供；导入不代表事实已确认，不新增其他应用读取权限。"
                : "ChatGPT / Claude JSON、ZIP · 带角色标记的 Markdown、TXT 或 JSON 回合。仅文本对话；附件不纳入，导入为快照。"}
            </p>
            <p className="history-format-note">
              本次确认不授予模型处理、持续同步、外部调用及安装权限；已有权限不会在此更改。
            </p>
            {busy && (
              <p role="status">
                {operation === "upload"
                  ? "正在本地读取文件…"
                  : operation === "save"
                    ? "正在保存文件…"
                    : operation === "refresh"
                      ? "正在核对文件与来源…"
                      : operation === "connect"
                        ? "正在核对连接回执…"
                        : "正在清理暂存文件…"}
              </p>
            )}
            {receipt && selectionValid && (
              <>
                <dl className="history-import-summary">
                  <div>
                    <dt>文件</dt>
                    <dd>{receipt.filename}</dd>
                  </div>
                  <div>
                    <dt>来源</dt>
                    <dd>
                      {receipt.vendor === "memory-content"
                        ? "手工提供的已有记忆"
                        : receipt.vendor === "generic"
                          ? "手工提供的对话"
                          : receipt.vendor === "chatgpt"
                            ? "ChatGPT"
                            : "Claude"}
                    </dd>
                  </div>
                  <div>
                    <dt>本文件内容</dt>
                    <dd>
                      {receipt.messages}{" "}
                      {isMemoryContent ? "条已有记忆记录" : "条文本消息"}
                    </dd>
                  </div>
                  <div>
                    <dt>作者标记</dt>
                    <dd>
                      用户 {receipt.human_messages} · Agent{" "}
                      {receipt.agent_messages}
                      {(receipt.unknown_author_messages ?? 0) > 0 &&
                        ` · 未知 ${receipt.unknown_author_messages}`}
                    </dd>
                  </div>
                  <div>
                    <dt>{isMemoryContent ? "文件快照" : "会话与分支"}</dt>
                    <dd>{receipt.conversations}</dd>
                  </div>
                  {(receipt.skipped_records > 0 ||
                    receipt.ignored_files > 0) && (
                    <div>
                      <dt>未纳入</dt>
                      <dd>
                        {receipt.skipped_records} 条非对话记录 ·{" "}
                        {receipt.ignored_files} 个其他文件
                      </dd>
                    </div>
                  )}
                </dl>
                {!!receipt.preview?.length && (
                  <details className="history-preview" open>
                    <summary>
                      原文预览 · {receipt.preview.length} / {receipt.messages}{" "}
                      条
                    </summary>
                    <ol>
                      {receipt.preview.map((item, index) => (
                        // biome-ignore lint/suspicious/noArrayIndexKey: This read-only preview is immutable and keyed by its upload identity.
                        <li key={`${receipt.upload_id}-${index}`}>
                          <div>
                            <strong>
                              {item.written_by === "unknown" ||
                              (item.role === "memory" && !item.written_by)
                                ? "作者未知"
                                : item.written_by === "person" ||
                                    item.role === "user"
                                  ? "用户"
                                  : "Agent"}
                            </strong>
                            <time>
                              {item.created_at
                                ? new Date(item.created_at).toLocaleString(
                                    "zh-CN",
                                  )
                                : "时间未知"}
                            </time>
                          </div>
                          <p>
                            {item.text}
                            {item.truncated ? "…" : ""}
                          </p>
                        </li>
                      ))}
                    </ol>
                  </details>
                )}
                {!receipt.committed && !saveUnknown && (
                  <label className="history-import-consent">
                    <input
                      type="checkbox"
                      checked={consent}
                      disabled={busy}
                      onChange={(event) => setConsent(event.target.checked)}
                    />
                    确认保存这份文件到本机，来源读取授权另行确认
                  </label>
                )}
                {receipt.committed && candidate && (
                  <>
                    <dl
                      className="history-import-summary"
                      aria-label="待授权来源范围"
                    >
                      <div>
                        <dt>来源</dt>
                        <dd>{candidate.display_name}</dd>
                      </div>
                      <div>
                        <dt>来源编号</dt>
                        <dd>{candidate.source_id}</dd>
                      </div>
                      <div>
                        <dt>实际目录</dt>
                        <dd>{candidate.candidate_path}</dd>
                      </div>
                      <div>
                        <dt>目录身份</dt>
                        <dd>{candidate.candidate_root_id}</dd>
                      </div>
                      <div>
                        <dt>位置类型</dt>
                        <dd>
                          {candidate.candidate_kind === "environment_override"
                            ? "自定义位置"
                            : "默认位置"}
                        </dd>
                      </div>
                      <div>
                        <dt>已有授权</dt>
                        <dd>{candidate.authorized ? "已授权" : "未授权"}</dd>
                      </div>
                    </dl>
                    <p className="history-format-note">
                      读取范围为上述目录中的全部已提交历史包，包括以前保存的文件，不仅是本次文件。上方消息数与原文预览仅属于本次文件，并非整个目录的内容清单。
                    </p>
                    <label className="history-import-consent">
                      <input
                        ref={scopeConsent}
                        type="checkbox"
                        checked={consent}
                        disabled={busy}
                        onChange={(event) => setConsent(event.target.checked)}
                      />
                      允许一次性读取上述来源目录中的历史包
                    </label>
                  </>
                )}
              </>
            )}
            {error && (
              <div className="error-banner" role="alert">
                <p>{error}</p>
                {issue && (
                  <>
                    <p>
                      {issueLocation(issue.location)}：
                      {issueLabels[issue.code] ?? "无法读取这段内容"}
                    </p>
                    <pre>{issue.excerpt}</pre>
                  </>
                )}
              </div>
            )}
            <div className="consent-actions" style={{ flexWrap: "wrap" }}>
              <button
                type="button"
                className="secondary"
                disabled={operation === "discard"}
                onClick={() => void close()}
              >
                <X size={16} aria-hidden="true" />
                {operation === "connect" || connection === "unknown"
                  ? "关闭"
                  : receipt?.committed
                    ? "暂不授权"
                    : "取消"}
              </button>
              {receipt &&
                selectionValid &&
                (saveUnknown || receipt.committed) && (
                  <button
                    type="button"
                    className="secondary"
                    disabled={busy}
                    onClick={() => void recover()}
                  >
                    <RefreshCw size={16} aria-hidden="true" />
                    {saveUnknown ? "核对文件保存结果" : "重新核对来源"}
                  </button>
                )}
              {!receipt?.committed && !saveUnknown && (
                <button
                  type="button"
                  disabled={busy || !receipt || !selectionValid || !consent}
                  onClick={() => void save()}
                >
                  <Save size={16} aria-hidden="true" />
                  保存文件并核对来源
                </button>
              )}
              {receipt?.committed && candidate && selectionValid && (
                <button
                  type="button"
                  disabled={busy || !consent}
                  onClick={() => void confirm()}
                >
                  <Check size={16} aria-hidden="true" />
                  {connection === "failed"
                    ? "重试此来源"
                    : connection === "unknown"
                      ? "核对连接结果"
                      : "授权并开始导入"}
                </button>
              )}
            </div>
          </section>
        </div>
      )}
    </>
  );
}

function assertFileReceipt(
  expected: FileImportReceipt,
  actual: FileImportReceipt,
) {
  if (
    !actual ||
    actual.upload_id !== expected.upload_id ||
    actual.source_id !== expected.source_id ||
    actual.checksum !== expected.checksum ||
    actual.vendor !== expected.vendor ||
    actual.filename !== expected.filename ||
    (actual.content_kind ?? "raw_conversation") !==
      (expected.content_kind ?? "raw_conversation") ||
    typeof actual.committed !== "boolean"
  ) {
    throw new Error("file_receipt_mismatch");
  }
}

const issueLabels: Record<string, string> = {
  missing_role: "缺少说话人标记",
  unknown_role: "无法确认说话人",
  empty_message: "这条消息没有正文",
  invalid_time: "时间需要明确日期与时区，未知请填 null",
  invalid_record: "消息字段应为 role、content、time",
  message_too_long: "单条消息过长",
  unclosed_code: "代码块没有结束标记",
};
function issueLocation(value: string) {
  const [kind, number] = value.split(":");
  return kind === "line" ? `第 ${number} 行` : `第 ${number} 条消息`;
}
