import type {
  GraphWidthDecision,
  GraphWidthStatus,
  UiMessage,
} from "@one-memory/contracts";
import { useEffect, useRef, useState } from "react";
import { apiErrorCode, decideGraphWidth, getGraphWidth } from "./api";
import { useTranslate } from "./ui-language";
import { traceUiAction } from "./ui-trace";
import { isDefiniteRefusal, writeTraceStatus } from "./write-outcome";

/** Ask before the widths cost anything, and default to changing nothing.
 *
 * Without this the app installs, opens, looks healthy, and quietly fails
 * every extraction with `graph_width_mismatch:...` — the failure is in the
 * task record and nowhere the user looks. The three options are the ones the
 * core already implements; the only judgement encoded here is which one is
 * preselected, and that has to be the one that touches nothing.
 */
export function GraphWidthPrompt({
  onResolved = () => undefined,
}: {
  onResolved?(): void;
}) {
  const t = useTranslate();
  const [status, setStatus] = useState<GraphWidthStatus | null>(null);
  const [decision, setDecision] = useState<GraphWidthDecision>("keep_existing");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{
    key: UiMessage;
    values?: Readonly<Record<string, string | number>>;
  } | null>(null);
  const [settled, setSettled] = useState(false);
  const [exportedPath, setExportedPath] = useState<string | null>(null);
  const panel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const result = await getGraphWidth();
        if (!cancelled) setStatus(result);
      } catch {
        // A core that is not up yet is not a mismatch. Staying silent is the
        // right answer here; extraction has not started either.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (settled || !status?.mismatch) return;
    const element = panel.current;
    if (!element) return;
    const before = document.activeElement;
    element.querySelector<HTMLInputElement>("input:checked")?.focus();
    return () => {
      if (before instanceof HTMLElement && before.isConnected) before.focus();
    };
  }, [settled, status]);

  if (settled || !status?.mismatch) return null;
  const { stored_width, expected_width } = status.mismatch;

  async function confirm() {
    setBusy(true);
    setError(null);
    const trace = traceUiAction("memory", "confirm");
    try {
      const receipt = await decideGraphWidth(decision, trace.traceId);
      if (receipt.exported_path) setExportedPath(receipt.exported_path);
      // The decision reached Core: that request succeeded whether or not the
      // graph still reports a mismatch.
      trace.done("succeeded");
      // Exporting is preparation, not a decision: the core still reports the
      // mismatch, and closing here would strand the user one step short of
      // the choice they exported in order to make.
      if (receipt.mismatch) return;
      setSettled(true);
      onResolved();
    } catch (reason) {
      const refused = isDefiniteRefusal(reason);
      trace.done(writeTraceStatus(reason), {
        reason: refused
          ? "graph_width_decision_failed"
          : "graph_width_decision_result_unknown",
      });
      // A refused request may show its external detail verbatim; an unknown
      // write outcome must not claim the graph or export was left unchanged.
      const code = apiErrorCode(reason);
      if (code === "graph_export_failed") {
        setError({ key: "导出失败，未生成备份；请不要将其视为已备份。" });
      } else if (refused && code === "export_root_required") {
        setError({ key: "缺少导出位置，未生成备份；请检查设置后重试。" });
      } else if (refused) {
        setError({
          key: "宽度选择未能完成：{reason}",
          values: {
            reason: reason instanceof Error ? reason.message : String(reason),
          },
        });
      } else {
        setError({
          key: "宽度选择结果尚未确认，请核对现有记忆库状态后再重试。",
        });
      }
    } finally {
      setBusy(false);
    }
  }

  const choices: { value: GraphWidthDecision; label: string }[] = [
    {
      value: "keep_existing",
      label: t(
        "保留现有记忆库（默认）：继续用原来的 embedding 配置，{count} 条记忆和关系图都不动",
        { count: status.memory_count },
      ),
    },
    {
      value: "rebuild_with_new",
      label: t(
        "改用内置模型：现有关系图会被归档（不删除），{count} 条记忆的关系需要重新建一遍",
        { count: status.memory_count },
      ),
    },
    {
      value: "export_first",
      label: t("先导出再决定：现在什么都不动，导出之后再回来选"),
    },
  ];

  return (
    <div
      className="graph-width-prompt"
      role="dialog"
      aria-modal="true"
      aria-label={t("现有记忆库和内置模型对不上")}
      ref={panel}
      onKeyDown={(event) => {
        if (event.key !== "Tab" || !panel.current) return;
        const controls = [
          ...panel.current.querySelectorAll<HTMLElement>(
            "button:not(:disabled), input:not(:disabled)",
          ),
        ];
        const first = controls[0];
        const last = controls.at(-1);
        if (!first || !last) return;
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }}
    >
      <h2>{t("现有记忆库和内置模型对不上")}</h2>
      <p>
        {t(
          "你现在的记忆库按 {stored} 维建的，内置模型出的是 {expected} 维。两者接不上，不选一个的话抽取会一直失败。",
          {
            stored: stored_width,
            expected: expected_width,
          },
        )}
      </p>
      <ul>
        {choices.map((choice) => (
          <li key={choice.value}>
            <label>
              <input
                type="radio"
                name="graph-width-decision"
                value={choice.value}
                aria-label={choice.label}
                checked={decision === choice.value}
                onChange={() => setDecision(choice.value)}
              />
              <span>{choice.label}</span>
            </label>
          </li>
        ))}
      </ul>
      {exportedPath ? (
        <p className="notice">
          {t("已导出到")} <code>{exportedPath}</code>
          {t("。原库没有动，现在可以再选一次。")}
        </p>
      ) : null}
      {error ? <p className="notice">{t(error.key, error.values)}</p> : null}
      <button type="button" disabled={busy} onClick={() => void confirm()}>
        {t("就这么办")}
      </button>
    </div>
  );
}
