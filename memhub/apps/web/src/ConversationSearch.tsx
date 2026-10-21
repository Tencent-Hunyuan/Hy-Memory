import type {
  ConversationBrowserPage,
  ConversationSearchEmptyReason,
  ConversationSearchHit,
  ConversationTranscript,
  GovernedMemory,
  UiMessage,
} from "@one-memory/contracts";
import { isAxiosError } from "axios";
import { ChevronRight, RefreshCw, Search } from "lucide-react";
import type { ReactNode } from "react";
import { useEffect, useId, useRef, useState } from "react";

import {
  apiErrorCode,
  getConversation,
  getConversationBrowser,
  getConversationMemories,
  searchConversations,
} from "./api";
import { AppMark } from "./app-mark";
import "./conversation-search.css";
import { useTranslate, useUiLanguage } from "./ui-language";

type OpenedConversation = {
  hit: ConversationSearchHit;
  phase: "loading" | "ready" | "error";
  transcript: ConversationTranscript | null;
  memories: GovernedMemory[];
  error: UiMessage | null;
};

type ConversationListHit = ConversationSearchHit;

/**
 * Searching imported history before any generation model exists.
 *
 * Original user, assistant and unknown-author turns remain distinct from
 * governed memories. Each asynchronous result belongs to its initiating view.
 */
export function ConversationSearch({
  onOpenMemory,
}: {
  onOpenMemory?(memoryId: string): void;
} = {}) {
  const t = useTranslate();
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<ConversationSearchHit[] | null>(null);
  const [browser, setBrowser] = useState<ConversationBrowserPage | null>(null);
  const [sourceFilter, setSourceFilter] = useState("");
  const [sourceOptions, setSourceOptions] = useState<string[]>([]);
  const [emptyReason, setEmptyReason] =
    useState<ConversationSearchEmptyReason | null>(null);
  const [error, setError] = useState<UiMessage | null>(null);
  const [busy, setBusy] = useState(false);
  const [mobileMemories, setMobileMemories] = useState(false);
  const [opened, setOpened] = useState<OpenedConversation | null>(null);
  const id = useId();
  const mounted = useRef(false);
  const searchVersion = useRef(0);
  const readVersion = useRef(0);
  const searchFlight = useRef<string | null>(null);
  const activeHit = useRef<ConversationSearchHit | null>(null);
  const readFlight = useRef<number | null>(null);
  const lastQuery = useRef("");
  const matchedTurn = useRef<HTMLElement | null>(null);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      searchVersion.current += 1;
      readVersion.current += 1;
      searchFlight.current = null;
      readFlight.current = null;
      activeHit.current = null;
    };
  }, []);

  useEffect(() => {
    if (!mounted.current) return;
    let cancelled = false;
    void Promise.resolve()
      .then(() =>
        getConversationBrowser({ sourceId: sourceFilter || undefined }),
      )
      .then(
        (page) => {
          if (cancelled || !mounted.current) return;
          setBrowser(page);
          setSourceOptions((current) => [
            ...new Set([
              ...current,
              ...page.items.map((item) => item.source_id),
            ]),
          ]);
        },
        () => {
          if (!cancelled && mounted.current)
            setError("对话列表暂时无法读取，请重试。");
        },
      );
    return () => {
      cancelled = true;
    };
  }, [sourceFilter]);

  function closeOriginal() {
    readVersion.current += 1;
    readFlight.current = null;
    activeHit.current = null;
    setOpened(null);
  }

  async function run(text: string) {
    if (!text || searchFlight.current === text) return;
    const request = ++searchVersion.current;
    searchFlight.current = text;
    lastQuery.current = text;
    setBusy(true);
    setError(null);
    setHits(null);
    setEmptyReason(null);
    closeOriginal();
    try {
      const found = await searchConversations(text);
      if (!mounted.current || request !== searchVersion.current) return;
      setHits(found.hits);
      setEmptyReason(found.hits.length ? null : found.empty_reason);
    } catch (reason) {
      if (!mounted.current || request !== searchVersion.current) return;
      setHits(null);
      setEmptyReason(null);
      setError(failureWording(reason));
    } finally {
      if (mounted.current && request === searchVersion.current) {
        searchFlight.current = null;
        setBusy(false);
      }
    }
  }

  async function read(hit: ConversationSearchHit) {
    if (sameHit(activeHit.current, hit) && readFlight.current !== null) return;
    const request = ++readVersion.current;
    const searchRequest = searchVersion.current;
    readFlight.current = request;
    activeHit.current = hit;
    setMobileMemories(false);
    setOpened({
      hit,
      phase: "loading",
      transcript: null,
      memories: [],
      error: null,
    });
    const isCurrent = () =>
      mounted.current &&
      request === readVersion.current &&
      searchRequest === searchVersion.current &&
      sameHit(activeHit.current, hit);
    try {
      const [result, related] = await Promise.all([
        hit.turn_offset
          ? getConversation(hit.evidence_id, {
              offset: hit.turn_offset,
              limit: 20,
            })
          : getConversation(hit.evidence_id),
        getConversationMemories(hit.evidence_id),
      ]);
      if (!isCurrent()) return;
      if (!matchesTranscript(result, hit))
        throw new Error("conversation_response_mismatch");
      setOpened({
        hit,
        phase: "ready",
        transcript: result,
        memories: related.items,
        error: null,
      });
    } catch (reason) {
      if (!isCurrent()) return;
      if (permissionUnavailable(reason)) {
        closeOriginal();
        setHits(null);
        setEmptyReason(null);
        setError(
          "原文已不可读取，可能已撤回授权或记录已不可用。旧内容已收起，请重新搜索核对。",
        );
      } else {
        setOpened({
          hit,
          phase: "error",
          transcript: null,
          memories: [],
          error: "原文读取未完成，请重试。",
        });
      }
    } finally {
      if (request === readVersion.current) readFlight.current = null;
    }
  }

  function toggle(hit: ConversationSearchHit) {
    if (sameHit(activeHit.current, hit)) closeOriginal();
    else void read(hit);
  }

  async function loadOlder() {
    if (!opened?.transcript?.has_more || readFlight.current !== null) return;
    const request = ++readVersion.current;
    readFlight.current = request;
    try {
      const older = await getConversation(opened.hit.evidence_id, {
        offset:
          (opened.transcript.offset ?? 0) + (opened.transcript.limit ?? 20),
        limit: opened.transcript.limit ?? 20,
      });
      if (!mounted.current || request !== readVersion.current) return;
      setOpened((current) =>
        current?.transcript
          ? {
              ...current,
              transcript: {
                ...older,
                messages: [...older.messages, ...current.transcript.messages],
              },
            }
          : current,
      );
    } catch {
      if (mounted.current && request === readVersion.current) {
        setOpened((current) =>
          current
            ? { ...current, error: "更早的对话暂时无法读取，请重试。" }
            : current,
        );
      }
    } finally {
      if (request === readVersion.current) readFlight.current = null;
    }
  }

  const listedHits: ConversationListHit[] =
    hits ??
    browser?.items.map((item) => ({
      evidence_id: item.evidence_id,
      conversation_id: item.conversation_id,
      source_id: item.source_id,
      workspace: item.workspace,
      project: item.project,
      created_at: item.latest_at,
      excerpt: item.title,
      turn_count: item.turn_count,
      memory_count: item.memory_count,
      turn_offset: 0,
    })) ??
    [];
  const turns = opened?.transcript ? groupTurns(opened.transcript) : [];
  const matchId = opened?.phase === "ready" ? opened.hit.evidence_id : null;
  useEffect(() => {
    if (!matchId) return;
    const turn = matchedTurn.current;
    // A hit can sit far down a long transcript. Opening it has to bring the
    // matching turn into view, not only render it somewhere below the fold.
    // Layout-less hosts (jsdom) have no scrollIntoView to call.
    if (typeof turn?.scrollIntoView === "function")
      turn.scrollIntoView({ block: "center" });
  }, [matchId]);

  return (
    <section className="conversation-search">
      <form
        className="memory-query-bar"
        onSubmit={(event) => {
          event.preventDefault();
          if (!query.trim()) return;
          void run(query.trim());
        }}
      >
        <label htmlFor={`${id}-query`}>{t("搜索历史对话")}</label>
        <div>
          <input
            id={`${id}-query`}
            name="conversation-query"
            autoComplete="off"
            value={query}
            placeholder={t("例如：打包、抽取、周末")}
            onChange={(event) => setQuery(event.target.value)}
          />
          <button
            type="submit"
            disabled={
              !query.trim() || (busy && searchFlight.current === query.trim())
            }
          >
            <Search size={16} aria-hidden="true" />
            {busy ? t("搜索中…") : t("搜索")}
          </button>
        </div>
      </form>

      <p className="conversation-search-note">
        {t(
          "这里搜的是你导入的原始对话，不是记忆。设置模型后，才会开始生成记忆。",
        )}
      </p>

      <label className="conversation-source-filter">
        <span>{t("应用")}</span>
        <select
          value={sourceFilter}
          onChange={(event) => setSourceFilter(event.target.value)}
        >
          <option value="">{t("全部应用")}</option>
          {sourceOptions.map((source) => (
            <option value={source} key={source}>
              {source}
            </option>
          ))}
        </select>
      </label>

      {error ? (
        <div className="conversation-search-error">
          <p role="alert">{t(error)}</p>
          <button
            type="button"
            className="quiet"
            disabled={busy}
            onClick={() => void run(lastQuery.current)}
          >
            <RefreshCw size={16} aria-hidden="true" />
            {t("重新搜索")}
          </button>
        </div>
      ) : null}

      {emptyReason ? (
        <p className="conversation-search-empty" role="status">
          {emptyReason === "nothing_imported"
            ? t("先导入历史，然后就能搜了")
            : t("没有找到相关对话")}
        </p>
      ) : null}

      {listedHits.length ? (
        <section
          className="conversation-browser"
          aria-label={t("历史对话搜索结果")}
        >
          <section
            className="conversation-browser-list"
            aria-label={t("会话列表")}
          >
            {listedHits.map((hit) => (
              <article
                key={`${hit.source_id}:${hit.conversation_id}:${hit.workspace ?? ""}`}
                className="conversation-hit"
                data-selected={sameHit(opened?.hit, hit)}
                data-evidence-id={hit.evidence_id}
              >
                <button
                  type="button"
                  className="conversation-hit-open"
                  aria-label={
                    sameHit(opened?.hit, hit) ? t("收起原文") : t("查看原文")
                  }
                  aria-expanded={sameHit(opened?.hit, hit)}
                  aria-controls={`${id}-transcript`}
                  onClick={() => toggle(hit)}
                >
                  <span>
                    <strong>{hit.title ?? hit.excerpt}</strong>
                    {hit.title && hit.title !== hit.excerpt ? (
                      <span className="conversation-hit-match">
                        {hit.excerpt}
                      </span>
                    ) : null}
                    <small className="conversation-hit-meta">
                      <span className="conversation-source-name">
                        <SourceIcon sourceId={hit.source_id} />
                        {sourceLabel(hit.source_id)}
                      </span>
                      {hit.project ? <span>{hit.project}</span> : null}
                      {hit.turn_count != null ? (
                        <span>
                          {t("{count} 轮", { count: hit.turn_count })}
                        </span>
                      ) : null}
                      {hit.memory_count ? (
                        <span>
                          {t("已沉淀 {count} 条记忆", {
                            count: hit.memory_count,
                          })}
                        </span>
                      ) : null}
                      <OriginalTime value={hit.created_at} />
                    </small>
                  </span>
                  <ChevronRight size={16} aria-hidden="true" />
                  <span className="sr-only">{t("查看原文")}</span>
                </button>
              </article>
            ))}
          </section>
          <section
            id={`${id}-transcript`}
            className="conversation-transcript"
            aria-label={t("完整原始对话")}
            aria-busy={opened?.phase === "loading"}
          >
            {!opened ? (
              <p className="conversation-search-empty">{t("选择一段对话")}</p>
            ) : null}
            {opened?.phase === "ready" ? (
              <button
                type="button"
                className="conversation-memory-toggle quiet"
                aria-expanded={mobileMemories}
                aria-controls={`${id}-memories`}
                onClick={() => setMobileMemories((visible) => !visible)}
              >
                {t("关联记忆 {count}", { count: opened.memories.length })}
              </button>
            ) : null}
            {opened?.phase === "loading" ? (
              <p role="status">{t("正在读取原文…")}</p>
            ) : null}
            {opened?.error ? (
              <div className="conversation-read-error">
                <p role="alert">{t(opened.error)}</p>
                <button
                  type="button"
                  className="quiet"
                  onClick={() => void read(opened.hit)}
                >
                  <RefreshCw size={16} aria-hidden="true" />
                  {t("重试读取原文")}
                </button>
              </div>
            ) : null}
            {opened?.transcript?.has_more ? (
              <button
                type="button"
                className="quiet"
                onClick={() => void loadOlder()}
              >
                {t("加载更早的对话")}
              </button>
            ) : null}
            {opened?.phase === "ready" ? (
              <ol className="conversation-turns">
                {turns.map((turn, index) => (
                  <li key={turn.key} className="conversation-round">
                    {turn.system.map((item) => (
                      <details
                        key={item.evidence_id}
                        className="conversation-system"
                      >
                        <summary>{t("系统上下文")}</summary>
                        <p>{item.verbatim}</p>
                      </details>
                    ))}
                    {turn.messages.map((item) => {
                      const matched =
                        item.evidence_id === opened.hit.evidence_id;
                      return item.role === "tool" ? (
                        <details
                          key={item.evidence_id}
                          className="conversation-tool"
                        >
                          <summary>{t("工具记录")}</summary>
                          <p>{item.verbatim}</p>
                        </details>
                      ) : (
                        <section
                          key={item.evidence_id}
                          ref={matched ? matchedTurn : undefined}
                          data-role={item.role}
                          data-match={matched}
                        >
                          <header className="conversation-turn-meta">
                            <span>{t(authorLabel(item.written_by))}</span>
                            <OriginalTime value={item.created_at} />
                          </header>
                          <p className="conversation-turn-verbatim">
                            {matched
                              ? markTerm(item.verbatim, lastQuery.current)
                              : item.verbatim}
                          </p>
                        </section>
                      );
                    })}
                    {index === turns.length - 1 &&
                    opened.transcript?.trailing_turn_unanswered ? (
                      <p className="conversation-round-unanswered">
                        {t("这一回合还没等到回答，暂不参与记忆整理。")}
                      </p>
                    ) : null}
                  </li>
                ))}
              </ol>
            ) : null}
          </section>
          <aside
            id={`${id}-memories`}
            className="conversation-memories"
            aria-label={t("关联记忆")}
            data-mobile-visible={mobileMemories}
          >
            <h3>{t("这段对话沉淀的记忆")}</h3>
            {!opened ? <p>{t("选择一段对话后查看")}</p> : null}
            {opened?.phase === "ready" && opened.memories.length === 0 ? (
              <p>{t("这段对话还没有沉淀成记忆")}</p>
            ) : null}
            {opened?.memories.map((memory) => (
              <article key={memory.memory_id}>
                <button
                  type="button"
                  onClick={() => onOpenMemory?.(memory.memory_id)}
                >
                  {memory.content}
                  <ChevronRight size={14} aria-hidden="true" />
                </button>
              </article>
            ))}
          </aside>
        </section>
      ) : null}
    </section>
  );
}

/** Wraps every occurrence of the searched term so the hit is visible in place. */
function markTerm(text: string, term: string): ReactNode {
  const needle = term.trim();
  if (!needle) return text;
  const haystack = text.toLowerCase();
  const target = needle.toLowerCase();
  const parts: ReactNode[] = [];
  let from = 0;
  for (
    let at = haystack.indexOf(target);
    at !== -1;
    at = haystack.indexOf(target, from)
  ) {
    if (at > from) parts.push(text.slice(from, at));
    parts.push(
      <mark className="conversation-hit-term" key={at}>
        {text.slice(at, at + needle.length)}
      </mark>,
    );
    from = at + needle.length;
  }
  if (!parts.length) return text;
  if (from < text.length) parts.push(text.slice(from));
  return parts;
}

function groupTurns(transcript: ConversationTranscript) {
  const grouped = new Map<
    number,
    {
      key: string;
      messages: ConversationTranscript["messages"];
      system: ConversationTranscript["messages"];
    }
  >();
  let fallback = 0;
  for (const message of transcript.messages) {
    if (
      message.role === "user" &&
      message.written_by === "person" &&
      !message.system_context
    ) {
      fallback += 1;
    }
    const index = message.turn_index ?? fallback;
    const turn = grouped.get(index) ?? {
      key: `turn-${index}`,
      messages: [],
      system: [],
    };
    (message.system_context ? turn.system : turn.messages).push(message);
    grouped.set(index, turn);
  }
  return [...grouped.values()];
}

function sourceLabel(sourceId: string) {
  const labels: Record<string, string> = {
    workbuddy: "WorkBuddy",
    cursor: "Cursor",
    codex: "Codex",
    "claude-code": "Claude Code",
  };
  return labels[sourceId] ?? sourceId;
}

function SourceIcon({ sourceId }: { sourceId: string }) {
  return <AppMark id={sourceId} size={16} />;
}

function failureWording(reason: unknown): UiMessage {
  if (permissionUnavailable(reason))
    return "历史对话读取权限或记录已不可用，旧内容已收起。请重新搜索核对。";
  if (apiErrorCode(reason) === "conversation_search_unavailable") {
    return "搜索索引不可用，这不是没有搜到：本地检索没有建起来，请到运行状况里看看 Core 的状态。";
  }
  return "搜索没能完成，请再试一次。";
}

function OriginalTime({ value }: { value: string | null }) {
  const language = useUiLanguage();
  const t = useTranslate();
  return value && Number.isFinite(Date.parse(value)) ? (
    <time dateTime={value}>
      {new Date(value).toLocaleString(language, { hour12: false })}
    </time>
  ) : (
    <span>{t("时间未知")}</span>
  );
}

function authorLabel(value: string): UiMessage {
  return value === "person"
    ? "用户原话"
    : value === "agent"
      ? "助手原话"
      : value === "tool"
        ? "工具记录"
        : "作者未知";
}

function sameHit(
  left: ConversationSearchHit | null | undefined,
  right: ConversationSearchHit,
) {
  return (
    !!left &&
    left.evidence_id === right.evidence_id &&
    left.source_id === right.source_id &&
    left.conversation_id === right.conversation_id
  );
}

function matchesTranscript(
  result: ConversationTranscript,
  hit: ConversationSearchHit,
) {
  return (
    result?.source_id === hit.source_id &&
    result.conversation_id === hit.conversation_id &&
    Array.isArray(result.messages) &&
    result.messages.some((item) => item.evidence_id === hit.evidence_id) &&
    result.messages.every((item) => typeof item.verbatim === "string")
  );
}

function permissionUnavailable(reason: unknown) {
  return (
    [
      "source_not_authorized",
      "conversation_not_found",
      "invalid_local_token",
    ].includes(apiErrorCode(reason) ?? "") ||
    (isAxiosError(reason) && [401, 403].includes(reason.response?.status ?? 0))
  );
}
