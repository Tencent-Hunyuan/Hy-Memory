import type { MemoryDetail } from "@one-memory/contracts";
import { Check, Globe2, LockKeyhole, Settings2 } from "lucide-react";
import { useId, useRef, useState } from "react";
import { apiErrorCode } from "./api";
import { useMemoryLanguage } from "./memory-language";
import { isDefiniteRefusal } from "./write-outcome";

type Mode = "limited" | "shared" | "disabled";
const limits = (scopes: string[]) =>
  scopes.filter((scope) => !["all-apps", "private", "*"].includes(scope));
const modeOf = (scopes: string[]): Mode =>
  scopes.includes("all-apps")
    ? "shared"
    : scopes.includes("private")
      ? "disabled"
      : "limited";

export function memoryProvenanceLabel(detail: MemoryDetail): string {
  const evidence = detail.evidence.filter((item) => item.available);
  if (
    evidence.length &&
    evidence.every((item) => item.source_id === "memory-content-export")
  ) {
    const authors = new Set(
      evidence.map((item) => item.written_by ?? "unknown"),
    );
    const author =
      authors.size !== 1
        ? "多位作者"
        : authors.has("person")
          ? "用户原文"
          : authors.has("agent")
            ? "Agent 原文"
            : "作者未知";
    return `导入的已有记忆 · ${author}`;
  }
  return detail.memory.origin === "manual"
    ? "手动添加"
    : detail.memory.origin === "answered"
      ? "问答生成"
      : detail.memory.origin === "agent"
        ? "由 AI 回复整理"
        : "由原始对话整理";
}

export function MemoryRecallScope({
  detail,
  busy,
  onSave,
}: {
  detail: MemoryDetail;
  busy: boolean;
  onSave(scopes: string[]): Promise<void>;
}) {
  const { text } = useMemoryLanguage();
  const id = useId();
  const editButton = useRef<HTMLButtonElement>(null);
  const [editing, setEditing] = useState(false);
  const current = modeOf(detail.memory.scopes);
  const [mode, setMode] = useState<Mode>(current);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const previous =
    [
      detail.memory,
      ...[...detail.history].sort((a, b) => b.revision - a.revision),
    ]
      .map((item) => limits(item.scopes))
      .find((items) => items.length) ?? [];
  const disabled = busy || saving;
  const editable = detail.memory.status === "confirmed";
  const summary =
    current === "shared"
      ? "所有关联工具"
      : current === "disabled"
        ? "已停止外部记忆召回"
        : "原有限定范围";
  const close = () => {
    setEditing(false);
    setMode(current);
    setError("");
    queueMicrotask(() => editButton.current?.focus());
  };
  const save = async () => {
    setSaving(true);
    setError("");
    try {
      await onSave(
        mode === "disabled"
          ? ["private"]
          : mode === "shared"
            ? ["all-apps", ...previous]
            : previous,
      );
      close();
    } catch (reason) {
      // The trace (in `MemorySpace.saveScopes`) logs a definite refusal as
      // `failed` and everything else as `unknown`; the sentence the user reads
      // has to match, or the log and the screen disagree about the same write.
      setError(
        apiErrorCode(reason) === "memory_revision_conflict"
          ? "记忆已更新，请重新打开这条记忆后再调整范围。"
          : isDefiniteRefusal(reason)
            ? "范围修改未提交，当前范围保持不变。请修正后重试。"
            : "保存结果尚未确认，请重试。",
      );
    } finally {
      setSaving(false);
    }
  };
  return (
    <section
      className="memory-recall-scope"
      aria-label={text("外部记忆召回范围")}
    >
      <div className="recall-scope-heading">
        <span>
          <LockKeyhole size={15} aria-hidden="true" />
          {text("外部记忆召回")}
        </span>
        {editable && !editing && (
          <button
            type="button"
            className="quiet map-tool"
            title={text("调整召回范围")}
            aria-label={text("调整召回范围")}
            ref={editButton}
            disabled={disabled}
            onClick={() => {
              setMode(current);
              setEditing(true);
            }}
          >
            <Settings2 size={16} aria-hidden="true" />
          </button>
        )}
      </div>
      <p className="recall-scope-summary">{text(summary)}</p>
      {editing && (
        <>
          <fieldset disabled={disabled}>
            <legend className="sr-only">{text("选择召回范围")}</legend>
            {(
              [
                ...(previous.length ? [["limited", "原有限定范围"]] : []),
                ["shared", "所有关联工具"],
                ["disabled", "停止外部记忆召回"],
              ] as [Mode, string][]
            ).map(([value, label]) => (
              <label key={value} className="recall-scope-option">
                <input
                  type="radio"
                  name={id}
                  value={value}
                  checked={mode === value}
                  onChange={() => {
                    setMode(value);
                    setError("");
                  }}
                />
                {value === "shared" ? (
                  <Globe2 size={16} aria-hidden="true" />
                ) : (
                  <LockKeyhole size={16} aria-hidden="true" />
                )}
                <span>{text(label)}</span>
              </label>
            ))}
          </fieldset>
          <div className="recall-scope-actions">
            <button
              type="button"
              disabled={disabled || mode === current}
              onClick={() => void save()}
            >
              <Check size={15} aria-hidden="true" />
              {text(saving ? "正在保存…" : "保存召回范围")}
            </button>
            <button
              type="button"
              className="quiet"
              aria-label={text("取消范围修改")}
              disabled={disabled}
              onClick={close}
            >
              {text("取消")}
            </button>
          </div>
        </>
      )}
      {error && (
        <p className="notice danger" role="alert">
          {text(error)}
        </p>
      )}
      <p className="recall-scope-boundary">
        {text("原文检索仍按来源授权；已带出的内容不会撤回。")}
      </p>
    </section>
  );
}
