import type {
  SourceImportReceipt as Receipt,
  SourceResponse,
} from "@one-memory/contracts";
import type { GuiLocale } from "@one-memory/i18n";
import { RefreshCw } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { getSourceImportReceipt } from "./api";
import { memoryText, useMemoryLanguage } from "./memory-language";

export function SourceImportReceipt({ source }: { source: SourceResponse }) {
  return source.authorized ? (
    <ReceiptBody key={source.source_id} source={source} />
  ) : null;
}

function ReceiptBody({ source }: { source: SourceResponse }) {
  const { locale, text, count } = useMemoryLanguage();
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [opened, setOpened] = useState(false);
  const generation = useRef(0);
  useEffect(
    () => () => {
      generation.current++;
    },
    [],
  );

  async function load() {
    const current = ++generation.current;
    setReceipt(null);
    setError("");
    setLoading(true);
    try {
      const result = await getSourceImportReceipt(source.source_id);
      if (generation.current !== current) return;
      if (result.source_id !== source.source_id)
        throw new Error("source mismatch");
      setReceipt(result);
    } catch {
      if (generation.current === current)
        setError("暂时无法读取已保存内容，旧回执已收起。请检查授权或重试。");
    } finally {
      if (generation.current === current) setLoading(false);
    }
  }

  return (
    <details
      className="source-import-receipt"
      onToggle={(event) => {
        const next = event.currentTarget.open;
        setOpened(next);
        if (next) void load();
        else {
          generation.current++;
          setReceipt(null);
          setLoading(false);
        }
      }}
    >
      <summary>{text("查看已读内容")}</summary>
      {opened && (
        <div className="source-receipt-body">
          <header>
            <span>
              {text("已保存原文")} · {source.display_name}
            </span>
            <button
              type="button"
              className="quiet"
              title={text("刷新已读内容")}
              aria-label={text("刷新已读内容")}
              disabled={loading}
              onClick={() => void load()}
            >
              <RefreshCw size={15} />
            </button>
          </header>
          {loading && <p role="status">{text("正在核对本机已保存记录…")}</p>}
          {error && <p role="alert">{text(error)}</p>}
          {receipt && (
            <>
              <dl className="source-receipt-counts">
                <div>
                  <dt>{text("原始消息")}</dt>
                  <dd>{receipt.messages.toLocaleString(locale)}</dd>
                </div>
                <div>
                  <dt>{text("会话")}</dt>
                  <dd>{receipt.conversations.toLocaleString(locale)}</dd>
                </div>
                <div>
                  <dt>{text("用户发言")}</dt>
                  <dd>{receipt.person_messages.toLocaleString(locale)}</dd>
                </div>
                <div>
                  <dt>{text("Agent 发言")}</dt>
                  <dd>{receipt.agent_messages.toLocaleString(locale)}</dd>
                </div>
                <div>
                  <dt>{text("作者未知")}</dt>
                  <dd>
                    {receipt.unknown_author_messages.toLocaleString(locale)}
                  </dd>
                </div>
                <div>
                  <dt>{text("原始时间未知")}</dt>
                  <dd>
                    {receipt.unknown_time_messages.toLocaleString(locale)}
                  </dd>
                </div>
                {receipt.tool_messages > 0 && (
                  <div>
                    <dt>{text("工具记录")}</dt>
                    <dd>{receipt.tool_messages.toLocaleString(locale)}</dd>
                  </div>
                )}
              </dl>
              <p className="source-receipt-note">
                {receipt.unknown_time_messages > 0
                  ? text("{count} 条记录没有原始时间，未用导入时间代替。", {
                      count: count(receipt.unknown_time_messages),
                    })
                  : text("原始时间来自已保存记录。")}{" "}
                {text(
                  "作者数量按当前记录分类统计，不代表记忆抽取已完成或语义已核实。",
                )}
              </p>
              <p className="source-receipt-meta">
                {text("统计于 ")}
                {time(receipt.measured_at, locale)}
                {receipt.messages !== source.imported_messages
                  ? text(" · 导入数量仍在更新，可刷新核对")
                  : ""}
              </p>
              {receipt.examples.length > 0 ? (
                <section
                  className="source-receipt-examples"
                  aria-label={text("已保存的原文样例")}
                >
                  <h3>{text("已保存的原文样例")}</h3>
                  <p className="source-receipt-meta">
                    {text("最多展示 3 段，不代表全部历史或推荐记忆。")}
                  </p>
                  {receipt.examples.map((example) => (
                    <article key={example.evidence_id}>
                      <header>
                        <span>
                          {text(author(example.written_by))} ·{" "}
                          {time(example.created_at, locale)}
                        </span>
                      </header>
                      <p className="source-receipt-quote">{example.excerpt}</p>
                      {example.truncated && (
                        <small>
                          {text("原文较长，此处仅展示前 600 个字符。")}
                        </small>
                      )}
                    </article>
                  ))}
                </section>
              ) : (
                <p>{text("这个来源还没有保存原文。")}</p>
              )}
            </>
          )}
        </div>
      )}
    </details>
  );
}

function author(value: string) {
  return value === "person"
    ? "用户"
    : value === "agent"
      ? "Agent"
      : value === "tool"
        ? "工具"
        : "作者未知";
}
function time(value: string | null, locale: GuiLocale) {
  return value && Number.isFinite(Date.parse(value))
    ? new Date(value).toLocaleString(locale)
    : memoryText(locale, "时间未知");
}
