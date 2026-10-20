import {
  type ConversationTranscript,
  SourceImportReceipt as Receipt,
  SourceResponse,
  translate,
  UiLanguage,
  UiMessage,
} from "@one-memory/contracts";
import { FileText, RefreshCw, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { getConversation, getSourceImportReceipt } from "./api";
import { useTranslate, useUiLanguage } from "./ui-language";

export function SourceImportReceipt({ source }: { source: SourceResponse }) {
  return source.authorized ? (
    <ReceiptBody key={source.source_id} source={source} />
  ) : null;
}

function ReceiptBody({ source }: { source: SourceResponse }) {
  const t = useTranslate();
  const language = useUiLanguage();
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [error, setError] = useState<UiMessage | "">("");
  const [loading, setLoading] = useState(false);
  const [opened, setOpened] = useState(false);
  const [transcript, setTranscript] = useState<ConversationTranscript | null>(
    null,
  );
  const [reading, setReading] = useState(false);
  const [readError, setReadError] = useState<UiMessage | "">("");
  const generation = useRef(0);
  const conversationGeneration = useRef(0);
  useEffect(
    () => () => {
      generation.current++;
      conversationGeneration.current++;
    },
    [],
  );

  async function load() {
    const current = ++generation.current;
    conversationGeneration.current++;
    setReceipt(null);
    setTranscript(null);
    setReadError("");
    setError("");
    setLoading(true);
    setReading(false);
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

  async function read(evidenceId: string) {
    const current = ++conversationGeneration.current;
    setTranscript(null);
    setReadError("");
    setReading(true);
    try {
      const result = await getConversation(evidenceId);
      if (conversationGeneration.current !== current) return;
      if (
        result.source_id !== source.source_id ||
        !result.messages.some((item) => item.evidence_id === evidenceId)
      )
        throw new Error("conversation mismatch");
      setTranscript(result);
    } catch {
      if (conversationGeneration.current === current) {
        setReceipt(null);
        setReadError("这段原文暂不可读，可能已被撤回授权。请刷新回执。");
      }
    } finally {
      if (conversationGeneration.current === current) setReading(false);
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
          conversationGeneration.current++;
          setReceipt(null);
          setTranscript(null);
          setLoading(false);
          setReading(false);
        }
      }}
    >
      <summary>{t("查看已读内容")}</summary>
      {opened && (
        <div className="source-receipt-body">
          <header>
            <span>
              {t("已保存原文 · {name}", { name: source.display_name })}
            </span>
            <button
              type="button"
              className="quiet"
              title={t("刷新已读内容")}
              aria-label={t("刷新已读内容")}
              disabled={loading}
              onClick={() => void load()}
            >
              <RefreshCw size={15} />
            </button>
          </header>
          {loading && <p role="status">{t("正在核对本机已保存记录…")}</p>}
          {error && <p role="alert">{t(error)}</p>}
          {receipt && (
            <>
              <dl className="source-receipt-counts">
                <div>
                  <dt>{t("原始消息")}</dt>
                  <dd>{receipt.messages.toLocaleString(language)}</dd>
                </div>
                <div>
                  <dt>{t("会话")}</dt>
                  <dd>{receipt.conversations.toLocaleString(language)}</dd>
                </div>
                <div>
                  <dt>{t("用户发言")}</dt>
                  <dd>{receipt.person_messages.toLocaleString(language)}</dd>
                </div>
                <div>
                  <dt>{t("Agent 发言")}</dt>
                  <dd>{receipt.agent_messages.toLocaleString(language)}</dd>
                </div>
                <div>
                  <dt>{t("作者未知")}</dt>
                  <dd>
                    {receipt.unknown_author_messages.toLocaleString(language)}
                  </dd>
                </div>
                <div>
                  <dt>{t("原始时间未知")}</dt>
                  <dd>
                    {receipt.unknown_time_messages.toLocaleString(language)}
                  </dd>
                </div>
                {receipt.tool_messages > 0 && (
                  <div>
                    <dt>{t("工具记录")}</dt>
                    <dd>{receipt.tool_messages.toLocaleString(language)}</dd>
                  </div>
                )}
              </dl>
              <p className="source-receipt-note">
                {receipt.unknown_time_messages > 0
                  ? t("{count} 条记录没有原始时间，未用导入时间代替。", {
                      count:
                        receipt.unknown_time_messages.toLocaleString(language),
                    })
                  : t("原始时间来自已保存记录。")}{" "}
                {t(
                  "作者数量按当前记录分类统计，不代表记忆抽取已完成或语义已核实。",
                )}
              </p>
              <p className="source-receipt-meta">
                {t("统计于 {time}", {
                  time: time(receipt.measured_at, language),
                })}
                {receipt.messages !== source.imported_messages
                  ? t(" · 导入数量仍在更新，可刷新核对")
                  : ""}
              </p>
              {receipt.examples.length > 0 ? (
                <section
                  className="source-receipt-examples"
                  aria-label={t("已保存的原文样例")}
                >
                  <h3>{t("已保存的原文样例")}</h3>
                  <p className="source-receipt-meta">
                    {t("最多展示 3 段，不代表全部历史或推荐记忆。")}
                  </p>
                  {receipt.examples.map((example) => (
                    <article key={example.evidence_id}>
                      <header>
                        <span>
                          {t(author(example.written_by))} ·{" "}
                          {time(example.created_at, language)}
                        </span>
                        <button
                          type="button"
                          className="quiet"
                          onClick={() => void read(example.evidence_id)}
                        >
                          <FileText size={14} aria-hidden="true" />
                          {t("查看完整对话")}
                        </button>
                      </header>
                      <p className="source-receipt-quote">{example.excerpt}</p>
                      {example.truncated && (
                        <small>
                          {t("原文较长，此处仅展示前 600 个字符。")}
                        </small>
                      )}
                    </article>
                  ))}
                </section>
              ) : (
                <p>{t("这个来源还没有保存原文。")}</p>
              )}
            </>
          )}
          {reading && <p role="status">{t("正在读取完整对话…")}</p>}
          {readError && <p role="alert">{t(readError)}</p>}
          {transcript && (
            <section
              className="source-receipt-conversation"
              aria-label={t("完整对话")}
            >
              <header>
                <h3>{t("完整对话")}</h3>
                <button
                  type="button"
                  className="quiet"
                  title={t("关闭完整对话")}
                  aria-label={t("关闭完整对话")}
                  onClick={() => {
                    conversationGeneration.current++;
                    setTranscript(null);
                  }}
                >
                  <X size={15} />
                </button>
              </header>
              <ol>
                {transcript.messages.map((message) => (
                  <li key={message.evidence_id}>
                    <small>
                      {t(author(message.written_by))} ·{" "}
                      {time(message.created_at, language)}
                    </small>
                    <p>{message.verbatim}</p>
                  </li>
                ))}
              </ol>
            </section>
          )}
        </div>
      )}
    </details>
  );
}

function author(value: string): UiMessage {
  return value === "person"
    ? "用户"
    : value === "agent"
      ? "Agent"
      : value === "tool"
        ? "工具"
        : "作者未知";
}
function time(value: string | null, language: UiLanguage) {
  return value && Number.isFinite(Date.parse(value))
    ? new Date(value).toLocaleString(language)
    : translate(language, "时间未知");
}
