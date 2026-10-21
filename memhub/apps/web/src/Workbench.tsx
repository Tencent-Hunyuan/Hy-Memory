import {
  type AskMemoryResponse,
  ConversationTranscript,
  GovernedMemory,
  MemoryDetail,
  MemoryEvidenceExcerpt,
  MemoryGraphEdge,
  MemoryGraphNode,
  MemoryGraphResponse,
  MemoryProcessingSummary,
  SourceResponse,
  translate,
  UiLanguage,
  UiMessage,
} from "@one-memory/contracts";
import { Check, PencilLine, RotateCcw, Trash2 } from "lucide-react";
import {
  type FormEvent,
  type RefObject,
  useId,
  useMemo,
  useState,
} from "react";
import { getConversation } from "./api";
import { HandoffBar } from "./HandoffBar";
import { MemoryGraph3D } from "./MemoryGraph3D";
import { MemoryRecallScope, memoryProvenanceLabel } from "./MemoryRecallScope";
import { extractionStateLabel } from "./memory-processing-labels";
import { useTranslate, useUiLanguage } from "./ui-language";
import {
  type Finding,
  findingsOf,
  mattersOf,
  memoriesInMatter,
} from "./workbench-findings";
import { singletonTags, tagCounts } from "./workbench-tags";

/** The memory tab: three columns over one graph.
 *
 * Left is what the memories are about, centre is how they relate and where
 * questions get asked, right is the words they were taken from. The previous
 * layout put a graph on a near-black field with a strip of memory buttons
 * under it and no way to reach the original conversation, so a memory was
 * something you could read but never check. */
export function Workbench({
  memories,
  graph,
  sources,
  processing,
  loaded,
  detail,
  draft,
  scopes,
  busy,
  answer,
  streaming,
  onDraft,
  onScopes,
  onOpenMemory,
  onCloseDetail,
  onSave,
  onSaveScopes,
  onAction,
  onSyncIndex,
  onAsk,
  onSaveAnswer,
  onBuildRelations,
  onConnect,
  onConfigureModel,
  requestIds,
}: {
  memories: GovernedMemory[];
  graph: MemoryGraphResponse | null;
  sources: SourceResponse[];
  processing: MemoryProcessingSummary | null;
  loaded: boolean;
  detail: MemoryDetail | null;
  detailLoad?: {
    memoryId: string;
    status: "loading" | "failed";
    error?: string;
  } | null;
  overlayBlocked?: boolean;
  draft: string;
  scopes: string;
  busy: boolean;
  answer: AskMemoryResponse | null;
  streaming: { text: string; memories: GovernedMemory[] } | null;
  onDraft(next: string): void;
  onScopes(next: string): void;
  onOpenMemory(memoryId: string): void;
  onCloseDetail(): void;
  onSave(event: FormEvent<HTMLFormElement>): void;
  onSaveScopes?(scopes: string[]): Promise<void>;
  onAction(action: "undo" | "delete"): void;
  onSyncIndex(): void;
  onAsk(question: string): void;
  onSaveAnswer(draft: SavedAnswer): void;
  onBuildRelations(): void;
  onConnect(): void;
  onConfigureModel(): void;
  /** Cleared whenever the draft changes, so a retried correction never
   * reuses the request id prepared for text the user has since replaced. */
  requestIds: RefObject<Map<string, string>>;
}) {
  const t = useTranslate();
  const language = useUiLanguage();
  const [matter, setMatter] = useState("");
  const [selectedNode, setSelectedNode] = useState<MemoryGraphNode | null>(
    null,
  );
  const [highlighted, setHighlighted] = useState<string[] | null>(null);
  const [question, setQuestion] = useState("");
  const [openEvidence, setOpenEvidence] = useState<string | null>(null);
  const [transcript, setTranscript] = useState<ConversationTranscript | null>(
    null,
  );
  const [transcriptError, setTranscriptError] = useState<string | null>(null);
  // Which matter the answer on screen is a synthesis of, if any. Held here
  // rather than asked of the answer, because the answer cannot tell: it is
  // the same endpoint either way, and the difference is what was asked.
  const [pageOf, setPageOf] = useState<string | null>(null);

  const matters = useMemo(
    () => mattersOf(memories, sources),
    [memories, sources],
  );
  const inMatter = useMemo(
    () => memoriesInMatter(memories, matter),
    [memories, matter],
  );
  const findings = useMemo(
    () =>
      findingsOf({ memories: inMatter, processing, now: new Date(), language }),
    [inMatter, processing, language],
  );
  // Counted across the whole library, not this matter. A tag used once here
  // and once elsewhere is a tag used twice; scoping this to the matter would
  // report a split that is not there.
  const onceOnly = useMemo(() => new Set(singletonTags(memories)), [memories]);
  const allTags = useMemo(() => tagCounts(memories), [memories]);
  const shown = useMemo(() => {
    // Not `graph === null`. Before extraction has run the graph request
    // succeeds and comes back with nothing in it, which is the state a new
    // install is actually in — the null case only happens when the request
    // fails. Keying the skeleton on null meant the one screen it existed for
    // never saw it.
    const drawn = graph ? narrow(graph, inMatter, matter) : null;
    return drawn?.nodes.length ? drawn : skeleton(sources, graph, language);
  }, [graph, inMatter, matter, sources, language]);
  const evidence = useMemo(
    () => orderEvidence(detail, highlighted, memories),
    [detail, highlighted, memories],
  );

  function pickFinding(finding: Finding) {
    setHighlighted(finding.memoryIds.length ? finding.memoryIds : null);
    setTranscript(null);
    setOpenEvidence(null);
    if (finding.memoryIds.length) onOpenMemory(finding.memoryIds[0]);
  }

  async function openTranscript(evidenceId: string) {
    setTranscriptError(null);
    try {
      setTranscript(await getConversation(evidenceId));
    } catch {
      setTranscript(null);
      setTranscriptError("这段对话的原文已经不在库里了。");
    }
  }

  const nameOf = (sourceId: string | null) =>
    sources.find((item) => item.source_id === sourceId)?.display_name ??
    sourceId ??
    t("未知来源");

  return (
    <div className="workbench">
      <Findings findings={findings} onPick={pickFinding} />

      <div className="workbench-grid">
        {/* 主题, not 事项. What this column lists is the tags two or more
            memories share. A real matter — the project or task a memory
            belongs to — is a workspace scope, and its plaintext name lives on
            the evidence rows in Core; nothing here can recover it from the
            hash, so calling this column 事项 named something it was not. */}
        <nav className="wb-matters" aria-label={t("主题")}>
          <p className="wb-column-label">{t("主题")}</p>
          {allTags.length ? (
            // Says the two numbers the column actually shows: how many
            // topics took shape, and how many tags did not. Reporting one
            // total meant reading "22 个主题标签" above six rows.
            <p className="wb-tag-tally">
              {onceOnly.size === 0
                ? t("{count} 个主题成形", {
                    count: allTags.length - onceOnly.size,
                  })
                : t("{count} 个主题成形，另有 {single} 个标签只出现过一次", {
                    count: allTags.length - onceOnly.size,
                    single: onceOnly.size,
                  })}
            </p>
          ) : null}
          {matters.map((item) => (
            <div key={item.scope || "all"}>
              <button
                type="button"
                className={item.scope === matter ? "wb-matter on" : "wb-matter"}
                aria-current={item.scope === matter ? "true" : undefined}
                onClick={() => {
                  setMatter(item.scope);
                  setSelectedNode(null);
                  setHighlighted(null);
                  setTranscript(null);
                }}
              >
                {item.scope ? item.label : t("全部")}
                <small>
                  {t("{count} 条记忆", { count: item.memoryCount })}
                  {item.sourceCount
                    ? t(" · {count} 个来源", { count: item.sourceCount })
                    : ""}
                </small>
              </button>
              {/* The graph is a canvas: clicking a circle is a mouse-only
                  gesture, and it is the only way in to a memory. This list is
                  the same door for a keyboard, a screen reader, and anyone
                  who would rather read than aim. */}
              {item.scope === matter && inMatter.length ? (
                <ul className="wb-matter-memories">
                  {inMatter.map((memory) => (
                    <li key={memory.memory_id}>
                      <button
                        type="button"
                        className={
                          detail?.memory.memory_id === memory.memory_id
                            ? "wb-memory on"
                            : "wb-memory"
                        }
                        data-status={memory.status}
                        aria-current={
                          detail?.memory.memory_id === memory.memory_id
                            ? "true"
                            : undefined
                        }
                        onClick={() => {
                          setSelectedNode(null);
                          setHighlighted([memory.memory_id]);
                          setTranscript(null);
                          setOpenEvidence(null);
                          onOpenMemory(memory.memory_id);
                        }}
                      >
                        {memory.content}
                      </button>
                      {/* Outside the button on purpose: the content is
                          clamped to two lines, and a tag row inside it gets
                          clamped away with the overflow. */}
                      {memory.tags.length ? (
                        <p className="wb-tags">
                          {memory.tags.map((tag) => (
                            <span
                              key={tag}
                              className="wb-tag"
                              data-once={onceOnly.has(tag) ? "true" : undefined}
                              title={
                                onceOnly.has(tag)
                                  ? t(
                                      "这个主题只出现在这一条上。可能是它只发生过一次，也可能是同一个主题被拆成了两个名字。",
                                    )
                                  : undefined
                              }
                            >
                              {tag}
                            </span>
                          ))}
                        </p>
                      ) : null}
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          ))}
        </nav>

        <div className="wb-stage">
          <div className="wb-graph" data-testid="workbench-graph">
            <p className="wb-graph-title">
              {matter
                ? (matters.find((item) => item.scope === matter)?.label ??
                  t("全部"))
                : t("全部")}
              {shown.nodes.length ? t(" 的关系图") : ""}
            </p>
            <div className="wb-graph-legend">
              <span data-kind="memory">{t("记忆")}</span>
              <span data-kind="schema">{t("HY 归纳")}</span>
              <span data-kind="topic">{t("主题")}</span>
              <span data-kind="source">{t("来源 App")}</span>
            </div>
            {/* Laid out by the row, not by offsets. Both were absolutely
                positioned with a right edge each, and measuring them found
                eight pixels of overlap — the relations button is a hundred
                and fifty wide until it says 正在归纳…, at which point it is
                not. A guessed offset cannot hold against a label that
                changes. */}
            <div className="wb-graph-actions">
              {inMatter.length ? (
                <button
                  type="button"
                  className="quiet"
                  disabled={busy}
                  onClick={() => {
                    const label = matter
                      ? (matters.find((item) => item.scope === matter)?.label ??
                        t("全部"))
                      : t("全部");
                    setPageOf(matter);
                    onAsk(
                      t(
                        "把「{name}」这件事讲清楚：现在到哪一步了、已经定下来的有哪些、还有什么没定。",
                        { name: label },
                      ),
                    );
                  }}
                >
                  {t("读这个事项的综述")}
                </button>
              ) : null}
              <button
                type="button"
                className="quiet"
                disabled={busy || !graph?.nodes.length}
                onClick={onBuildRelations}
              >
                {busy ? t("正在归纳…") : t("用 HY Memory 生成关系")}
              </button>
            </div>
            {shown.nodes.length ? (
              <MemoryGraph3D
                graph={shown}
                selectedNodeId={
                  selectedNode?.node_id ??
                  (detail ? `memory:${detail.memory.memory_id}` : null)
                }
                onSelectNode={(node) => {
                  setSelectedNode(node);
                  setTranscript(null);
                  setOpenEvidence(null);
                  if (node.memory_id) {
                    setHighlighted([node.memory_id]);
                    onOpenMemory(node.memory_id);
                  } else {
                    setHighlighted(null);
                  }
                }}
              />
            ) : (
              <EmptyStage loaded={loaded} onConnect={onConnect} />
            )}
          </div>
          {graph?.nodes.length ? (
            <p className="wb-receipt">
              {t("画的是 {shown} / 全部 {total} 条记忆", {
                shown: shown.nodes.filter((node) => node.node_type === "memory")
                  .length,
                total: graph.total_memories,
              })}
              <span>{graphReceipt(shown, t)}</span>
            </p>
          ) : null}

          <NodeDetail
            node={selectedNode}
            graph={shown}
            detail={detail}
            draft={draft}
            scopes={scopes}
            busy={busy}
            matterLabel={
              matter
                ? (matters.find((item) => item.scope === matter)?.label ??
                  t("全部"))
                : t("全部")
            }
            onDraft={onDraft}
            onScopes={onScopes}
            onSave={onSave}
            onSaveScopes={onSaveScopes}
            onAction={onAction}
            onSyncIndex={onSyncIndex}
            requestIds={requestIds}
            onClose={() => {
              setSelectedNode(null);
              onCloseDetail();
            }}
          />

          <form
            className="wb-ask"
            onSubmit={(event) => {
              event.preventDefault();
              if (!question.trim()) return;
              setPageOf(null);
              onAsk(question.trim());
              setQuestion("");
            }}
          >
            <div className="wb-chips">
              {chipsFor(inMatter, language).map((chip) => (
                <button
                  key={chip}
                  type="button"
                  className="wb-chip"
                  onClick={() => {
                    setPageOf(null);
                    onAsk(chip);
                  }}
                >
                  {chip}
                </button>
              ))}
            </div>
            <div className="wb-ask-row">
              <input
                aria-label={t("就着这个事项提问")}
                placeholder={t("就着这个事项问，回答只用已确认的记忆")}
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
              />
              <button type="submit" disabled={!question.trim()}>
                {t("问")}
              </button>
            </div>
            {answer || streaming ? (
              <Answer
                onSaveAnswer={onSaveAnswer}
                asPage={pageOf !== null}
                answer={answer}
                streaming={streaming}
                onOpenMemory={onOpenMemory}
              />
            ) : null}
          </form>

          <HandoffBar
            memories={inMatter.filter(
              (memory) => memory.memory_id === detail?.memory.memory_id,
            )}
            contextId={matter ?? "all"}
            matterLabel={
              matter
                ? (matters.find((item) => item.scope === matter)?.label ??
                  t("全部"))
                : t("全部")
            }
          />
        </div>

        <aside className="wb-evidence" aria-label={t("原始对话")}>
          <p className="wb-column-label">{t("原始对话")}</p>
          <p className="wb-evidence-note">
            {detail
              ? t("这条记忆出自下面 {count} 段原话，按时间排", {
                  count: evidence.length,
                })
              : t("点图上任意一个圈，这里显示它出自哪些原话")}
          </p>
          {evidence.map((item) => (
            <article
              key={item.evidence_id}
              className="wb-evidence-card"
              data-open={openEvidence === item.evidence_id ? "true" : undefined}
            >
              <button
                type="button"
                className="wb-evidence-head"
                onClick={() => {
                  const next =
                    openEvidence === item.evidence_id ? null : item.evidence_id;
                  setOpenEvidence(next);
                  setTranscript(null);
                  setTranscriptError(null);
                }}
              >
                <span className="wb-evidence-meta">
                  <b>{nameOf(item.source_id)}</b>
                  <span>{when(item.created_at, language)}</span>
                </span>
                {item.extraction_state &&
                item.extraction_state !== "extracted" ? (
                  <small className="wb-evidence-state">
                    {item.extraction_state === "outside_window"
                      ? t("未提炼 · 在窗口外")
                      : item.extraction_state === "queued"
                        ? t("未提炼 · 排队中")
                        : extractionStateLabel(item.extraction_state)}
                  </small>
                ) : null}
                <span className="wb-evidence-excerpt">
                  {item.available ? item.verbatim : t("来源原话已不可读取")}
                </span>
              </button>
              {openEvidence === item.evidence_id ? (
                <div className="wb-evidence-body">
                  <button
                    type="button"
                    className="secondary"
                    onClick={() => void openTranscript(item.evidence_id)}
                  >
                    {t("看完整对话")}
                  </button>
                  {transcriptError ? (
                    <p className="wb-transcript-error">
                      {t("这段对话的原文已经不在库里了。")}
                    </p>
                  ) : null}
                  {transcript ? (
                    <ol className="wb-transcript">
                      {transcript.messages.map((message) => (
                        <li key={message.evidence_id}>
                          <span className="wb-transcript-role">
                            {message.written_by === "user"
                              ? t("你")
                              : t("助手")}
                          </span>
                          {message.verbatim}
                        </li>
                      ))}
                    </ol>
                  ) : null}
                </div>
              ) : null}
            </article>
          ))}
          {!detail && !memories.length && loaded ? (
            <p className="wb-evidence-note">
              {t("还没有记忆。已经导入的原话可以在「历史对话」里直接搜。")}
            </p>
          ) : null}
        </aside>
      </div>

      {processing?.blocked_stages.length ? (
        <p className="wb-floor">
          {t("有对话排在队里等一个条件。原文已经在库里，随时搜得到。")}
          <button type="button" className="quiet" onClick={onConfigureModel}>
            {t("去看看缺什么")}
          </button>
        </p>
      ) : null}
    </div>
  );
}

function Findings({
  findings,
  onPick,
}: {
  findings: Finding[];
  onPick(finding: Finding): void;
}) {
  const t = useTranslate();
  if (!findings.length) return null;
  return (
    <section className="wb-findings" aria-label={t("我替你看出这几件事")}>
      <div className="wb-findings-head">
        <b>{t("我替你看出这几件事")}</b>
        <span>{t("都是本机记录算出来的，点一条会在图上标出它依据的记忆")}</span>
      </div>
      {findings.map((finding) => (
        <div key={finding.id} className="wb-finding">
          <span className={`wb-dot k-${finding.kind}`} aria-hidden="true" />
          <div>
            <span className="wb-finding-label">{finding.label}</span>
            {finding.text}
            {finding.memoryIds.length ? (
              <button
                type="button"
                className="quiet wb-finding-link"
                onClick={() => onPick(finding)}
              >
                {t("在图上看")}
              </button>
            ) : null}
          </div>
        </div>
      ))}
    </section>
  );
}

/** The centre card: what the selected circle is, and — for a memory — the
 * only place its wording can be changed or the memory dropped. */
export function NodeDetail({
  node,
  graph,
  detail,
  draft,
  busy,
  matterLabel,
  onDraft,
  onSave,
  onSaveScopes,
  onAction,
  onSyncIndex,
  requestIds,
  onClose,
  compact = false,
}: {
  node: MemoryGraphNode | null;
  graph: MemoryGraphResponse | null;
  detail: MemoryDetail | null;
  draft: string;
  scopes: string;
  busy: boolean;
  matterLabel: string;
  onDraft(next: string): void;
  onScopes(next: string): void;
  onSave(event: FormEvent<HTMLFormElement>): void;
  onSaveScopes?(scopes: string[]): Promise<void>;
  onAction(action: "undo" | "delete"): void;
  onSyncIndex(): void;
  requestIds: RefObject<Map<string, string>>;
  onClose(): void;
  compact?: boolean;
}) {
  const t = useTranslate();
  const language = useUiLanguage();
  const [editing, setEditing] = useState(!compact);
  const formId = useId();
  if (detail) {
    const memory = detail.memory;
    return (
      <article className="wb-detail" aria-label={t("这条记忆")}>
        <div className="wb-detail-head">
          <span className={`wb-status s-${memory.status}`}>
            {t(statusLabel(memory.status))}
          </span>
          <span className="wb-detail-rev">
            {t("第 {revision} 版", { revision: memory.revision })}
          </span>
          <button type="button" className="quiet" onClick={onClose}>
            {t("收起")}
          </button>
        </div>
        {detail.index_sync?.status === "pending" ? (
          // Said plainly, because the consequence is not in the word "index":
          // the memory here reads correctly and search does not, and nobody
          // would guess that from a sync badge.
          <p className="notice" role="status">
            {t("搜索里还是改之前那句话。")}
            {detail.index_sync.last_error_code ===
            "hy_memory_update_rejected" ? (
              // HY has no such memory to re-embed, so retrying cannot end
              // differently. Offering the button anyway would have the user
              // pressing it until they gave up.
              t("HY Memory 里没有这条记忆，重试也补不上。")
            ) : (
              <button
                type="button"
                className="quiet"
                disabled={busy}
                onClick={onSyncIndex}
              >
                {t("重试同步")}
              </button>
            )}
          </p>
        ) : null}
        {compact && !editing && (
          <p className="map-memory-reading">{memory.content}</p>
        )}
        {compact && !editing && (
          <p className="map-origin">
            {memoryProvenanceLabel(detail, language)}
          </p>
        )}
        {onSaveScopes && (
          <MemoryRecallScope
            key={`${memory.memory_id}:${memory.revision}`}
            detail={detail}
            busy={busy}
            onSave={onSaveScopes}
          />
        )}
        {editing && memory.status === "confirmed" ? (
          <form id={formId} onSubmit={onSave} aria-label={t("纠正记忆内容")}>
            <label>
              {t("记忆内容")}
              <textarea
                name="memory-content"
                aria-label={t("记忆内容")}
                autoComplete="off"
                required
                rows={3}
                value={draft}
                onChange={(event) => {
                  requestIds.current?.clear();
                  onDraft(event.target.value);
                }}
              />
            </label>
          </form>
        ) : null}
        <div className="wb-detail-actions">
          {!editing && memory.status === "confirmed" && (
            <button
              type="button"
              className="secondary"
              onClick={() => setEditing(true)}
            >
              <PencilLine size={14} aria-hidden="true" />
              {t("纠正")}
            </button>
          )}
          {editing && memory.status === "confirmed" ? (
            <button type="submit" form={formId} disabled={busy}>
              <Check size={14} aria-hidden="true" />
              {t("保存纠正")}
            </button>
          ) : null}
          {compact && editing && (
            <button
              type="button"
              className="secondary"
              disabled={busy}
              onClick={() => {
                onDraft(memory.content);
                requestIds.current?.clear();
                setEditing(false);
              }}
            >
              {t("取消编辑")}
            </button>
          )}
          {detail.history.length > 1 && memory.status !== "deleted" ? (
            <button
              type="button"
              className="secondary"
              disabled={busy}
              onClick={() => onAction("undo")}
              title={t("撤销上次修改")}
              aria-label={t("撤销上次修改")}
            >
              <RotateCcw size={15} aria-hidden="true" />
            </button>
          ) : null}
          {memory.status !== "deleted" ? (
            <button
              type="button"
              className="danger"
              disabled={busy}
              onClick={() => onAction("delete")}
            >
              <Trash2 size={14} aria-hidden="true" />
              {t("删除")}
            </button>
          ) : null}
        </div>
      </article>
    );
  }

  if (node) {
    const trace = graph ? traceFrom(graph, node.node_id, 2) : [];
    const direct = trace.filter((step) => step.hop === 1);
    return (
      <article className="wb-detail" aria-label={t("这个节点")}>
        <div className="wb-detail-head">
          <span className="wb-status">{t(nodeTypeLabel(node.node_type))}</span>
          <strong>{node.label}</strong>
        </div>
        <p className="wb-detail-note">
          {t(
            "这不是一条记忆，是记忆挂靠的东西。直接关系 {direct} 条，两跳可达 {indirect} 条。",
            {
              direct: direct.length,
              indirect: trace.length - direct.length,
            },
          )}
        </p>
        <ul className="wb-trace">
          {trace.slice(0, 8).map((step) => (
            <li key={`${step.edge.edge_id}:${step.hop}`}>
              <span className="wb-trace-hop">
                {step.hop === 1 ? t("直接") : t("两跳")}
              </span>
              {relationLabel(step.edge.relation, t)} · {step.node.label}
              <span className="wb-trace-origin">
                {t(edgeOriginLabel(step.edge.origin))}
              </span>
            </li>
          ))}
        </ul>
      </article>
    );
  }

  return (
    <article className="wb-detail wb-detail-idle" aria-label={t("这个事项")}>
      <div className="wb-detail-head">
        <strong>{matterLabel}</strong>
      </div>
      <p className="wb-detail-note">
        {t(
          "图上的圈是记忆，线是它们被同一段对话提到过。选中一个，这里会变成它的来历和改法。左边换一个事项，图跟着换。",
        )}
      </p>
    </article>
  );
}

/** What gets filed back when an answer is worth keeping. */
export type SavedAnswer = { content: string; evidence_ids: string[] };

/** Past this, an answer is a page and not a memory.
 *
 * The reason for a ceiling at all is what recall does with what it finds:
 * it hands memories to another AI as the person's own words, and a thousand
 * characters of model prose arriving that way is the failure the llm-wiki
 * write-ups describe — a lossy summary that outlives the thing it summarised.
 * A long answer is not refused, it is sent to the matter page, which is
 * rebuilt from the memories every time and never stands in for one. */
const ANSWER_AS_MEMORY_LIMIT = 600;

export function Answer({
  answer,
  streaming,
  onOpenMemory,
  onSaveAnswer,
  asPage,
  suspended = false,
}: {
  answer: AskMemoryResponse | null;
  streaming: { text: string; memories: GovernedMemory[] } | null;
  onOpenMemory(memoryId: string): void;
  onSaveAnswer(draft: SavedAnswer): void;
  asPage: boolean;
  suspended?: boolean;
}) {
  const t = useTranslate();
  const shownMemories = (answer ?? streaming)?.memories ?? [];
  // Only a finished answer, and only one that stands on something. Filing
  // back what a stream is halfway through writing would keep a sentence that
  // was never true, and an answer with no memories under it has nothing to
  // trace to — it would be the one kind of memory that cannot be checked.
  const groundedAnswer =
    !asPage && answer && shownMemories.length > 0 && answer.mode !== "empty"
      ? answer
      : null;
  const tooLong =
    groundedAnswer !== null &&
    groundedAnswer.answer.length > ANSWER_AS_MEMORY_LIMIT;
  return (
    <div className="wb-answer" aria-live="polite">
      <p>{answer ? answer.answer : streaming?.text || t("正在检索记忆…")}</p>
      <p className="wb-answer-source">
        {answer
          ? t("{mode} · 依据 {count} 条已确认记忆", {
              mode:
                answer.mode === "generated"
                  ? t("模型综合")
                  : answer.mode === "extractive"
                    ? t("原文摘录")
                    : t("没有命中"),
              count: shownMemories.length,
            })
          : t("已召回 {count} 条", { count: shownMemories.length })}
      </p>
      {groundedAnswer && !tooLong ? (
        <button
          type="button"
          className="quiet"
          onClick={() =>
            onSaveAnswer({
              content: groundedAnswer.answer,
              evidence_ids: [
                ...new Set(shownMemories.flatMap((item) => item.evidence_ids)),
              ],
            })
          }
        >
          {t("存为记忆")}
        </button>
      ) : null}
      {tooLong ? (
        <p className="wb-answer-source">
          {t("这个回答太长，超过当前保存上限，暂不能存为记忆。")}
        </p>
      ) : null}
      {asPage ? (
        // Not offered as a memory, and said out loud why. A synthesis is
        // rebuilt from the memories under it every time it is read, which is
        // the only reason it is safe to let a model write it: a stored
        // summary drifts from what it summarised and nothing notices, and a
        // rebuilt one cannot.
        <p className="wb-answer-source">
          {t(
            "这是综述，不是记忆。每次都会重新生成，所以它不会和下面这些记忆走散。",
          )}
        </p>
      ) : null}
      <ol>
        {shownMemories.map((memory) => (
          <li key={memory.memory_id}>
            <button
              type="button"
              className="quiet"
              onClick={() => onOpenMemory(memory.memory_id)}
            >
              {memory.content}
            </button>
          </li>
        ))}
      </ol>
      {answer && answer.mode !== "empty" && !streaming && (
        <HandoffBar
          memories={shownMemories}
          suspended={suspended}
          contextId="answer"
          matterLabel={t("这次回答")}
          answerText={answer.answer}
        />
      )}
    </div>
  );
}

function EmptyStage({
  loaded,
  onConnect,
}: {
  loaded: boolean;
  onConnect(): void;
}) {
  const t = useTranslate();
  if (!loaded) {
    return (
      <div className="wb-graph-empty">
        <p>{t("正在读取记忆…")}</p>
        <small>{t("本地 Core 启动后会自动出现，不用刷新。")}</small>
      </div>
    );
  }
  return (
    <div className="wb-graph-empty">
      <p>{t("这里还没有东西可以连起来。")}</p>
      <small>
        {t("授权一个来源之后，先出现的是来源和主题的骨架，记忆会陆续挂上去。")}
      </small>
      <button type="button" onClick={onConnect}>
        {t("去授权一个来源")}
      </button>
    </div>
  );
}

/** Keep only what belongs to the chosen matter.
 *
 * Nodes that are not memories stay: a source or a topic is what the memories
 * in this matter hang off, and dropping them would leave the circles floating
 * with nothing to be attached to. */
function narrow(
  graph: MemoryGraphResponse,
  inMatter: GovernedMemory[],
  matter: string,
): MemoryGraphResponse {
  if (!matter) return graph;
  const keep = new Set(inMatter.map((item) => item.memory_id));
  const nodes = graph.nodes.filter(
    (node) => node.node_type !== "memory" || keep.has(node.memory_id ?? ""),
  );
  const ids = new Set(nodes.map((node) => node.node_id));
  return {
    ...graph,
    nodes,
    edges: graph.edges.filter(
      (edge) => ids.has(edge.source_node_id) && ids.has(edge.target_node_id),
    ),
  };
}

/** A graph before there are any memories.
 *
 * Drawn from sources that actually have conversations imported, so the first
 * thing a new user sees has a shape rather than a sentence explaining why
 * there is no shape. Every circle here is a real, countable import. */
function skeleton(
  sources: SourceResponse[],
  graph: MemoryGraphResponse | null,
  language: UiLanguage,
): MemoryGraphResponse {
  const withHistory = sources.filter(
    (item) => item.authorized && item.imported_conversations > 0,
  );
  return {
    total_memories: graph?.total_memories ?? 0,
    projected_memories: 0,
    hy_graph_available: graph?.hy_graph_available ?? false,
    nodes: withHistory.map((item) => ({
      node_id: `source:${item.source_id}`,
      node_type: "source" as const,
      label: translate(language, "{name} · {count} 段", {
        name: item.display_name,
        count: item.imported_conversations,
      }),
      memory_id: null,
      source_id: item.source_id,
      kind: null,
      status: null,
      confidence: null,
    })),
    edges: [],
  };
}

/** Newest first, and filtered to the highlighted memories when a finding put
 * some on screen. */
function orderEvidence(
  detail: MemoryDetail | null,
  highlighted: string[] | null,
  memories: GovernedMemory[],
): MemoryEvidenceExcerpt[] {
  if (!detail) return [];
  const relevant =
    highlighted && !highlighted.includes(detail.memory.memory_id)
      ? evidenceOf(highlighted, memories)
      : null;
  const items = detail.evidence.filter(
    (item) => !relevant || relevant.has(item.evidence_id),
  );
  return [...items].sort((left, right) => {
    const a = left.created_at ? Date.parse(left.created_at) : 0;
    const b = right.created_at ? Date.parse(right.created_at) : 0;
    return b - a;
  });
}

function evidenceOf(
  memoryIds: string[],
  memories: GovernedMemory[],
): Set<string> {
  const wanted = new Set(memoryIds);
  const out = new Set<string>();
  for (const memory of memories) {
    if (!wanted.has(memory.memory_id)) continue;
    for (const id of memory.evidence_ids) out.add(id);
  }
  return out;
}

/** Questions built from what is actually in this matter, so a chip never
 * offers to answer something there is no record of. */
function chipsFor(memories: GovernedMemory[], language: UiLanguage): string[] {
  if (!memories.length) return [];
  const chips = [translate(language, "这里最近有什么变化")];
  if (memories.length >= 3) chips.push(translate(language, "把这些归纳成几条"));
  return chips;
}

function when(iso: string | null, language: UiLanguage): string {
  if (!iso) return translate(language, "时间不明");
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return translate(language, "时间不明");
  return `${at.getMonth() + 1}/${at.getDate()} ${String(at.getHours()).padStart(2, "0")}:${String(at.getMinutes()).padStart(2, "0")}`;
}

function statusLabel(status: GovernedMemory["status"]): UiMessage {
  if (status === "confirmed") return "已可用";
  if (status === "superseded") return "已被取代";
  return "已删除";
}

function nodeTypeLabel(type: MemoryGraphNode["node_type"]): UiMessage {
  if (type === "source") return "来源 App";
  if (type === "topic") return "主题";
  if (type === "schema") return "HY 归纳";
  return "记忆";
}

function graphReceipt(
  graph: MemoryGraphResponse,
  t: ReturnType<typeof useTranslate>,
): string {
  const counts = {
    provenance: 0,
    same_conversation: 0,
    shared_tag: 0,
    hy_relation: 0,
  };
  for (const edge of graph.edges) counts[edge.origin] += 1;
  const parts = [
    t("原话来源 {count} 条", { count: counts.provenance }),
    t("同一次对话 {count} 条", { count: counts.same_conversation }),
  ];
  // Only mentioned once there is one. A young library has no shared tag yet,
  // and a line reading 0 would be one more thing to read past.
  if (counts.shared_tag > 0)
    parts.push(t("同一个主题 {count} 条", { count: counts.shared_tag }));
  if (counts.hy_relation > 0) {
    parts.push(t("HY Memory 归纳 {count} 条", { count: counts.hy_relation }));
    return parts.join(" · ");
  }
  parts.push(
    graph.hy_graph_available
      ? t("HY Memory 还没归纳出关系，点击上方按钮运行 System 2")
      : t("还没配置可用模型，无法读取 HY Memory 的归纳关系"),
  );
  return parts.join(" · ");
}
function edgeOriginLabel(origin: MemoryGraphEdge["origin"]): UiMessage {
  if (origin === "hy_relation") return "HY Memory 归纳";
  if (origin === "same_conversation") return "同一次对话";
  // Named before the fallthrough. The chain ends in 原话来源, so a missing
  // case here is not an error — it is a line that says the wrong thing.
  if (origin === "shared_tag") return "同一个主题";
  return "原话来源";
}

/** Walk out from one node so each drawn line can be checked by hand. */
function relationLabel(
  relation: string,
  t: ReturnType<typeof useTranslate>,
): string {
  const labels: Record<string, UiMessage> = {
    evidence_source: "来源于",
    same_conversation: "同一次对话",
    shared_tag: "同一个主题",
    derived_from: "归纳自",
    tagged_with: "属于主题",
    related_to: "相关",
  };
  return labels[relation] ? t(labels[relation]) : relation.replaceAll("_", " ");
}

/** Two numbers rather than one total.
 *
 * 675 waiting reads as "this is stuck". "3 new, 672 historical" says what is
 * actually true: the ones from this morning are minutes away, and the rest is
 * the import catching up. Same total, entirely different feeling (R-02). */
function traceFrom(
  graph: MemoryGraphResponse,
  nodeId: string,
  hops: number,
): { edge: MemoryGraphEdge; node: MemoryGraphNode; hop: number }[] {
  const byId = new Map(graph.nodes.map((node) => [node.node_id, node]));
  const seen = new Set([nodeId]);
  const trace: { edge: MemoryGraphEdge; node: MemoryGraphNode; hop: number }[] =
    [];
  let frontier = [nodeId];
  for (let hop = 1; hop <= hops; hop += 1) {
    const next: string[] = [];
    for (const current of frontier) {
      for (const edge of graph.edges) {
        const otherId =
          edge.source_node_id === current
            ? edge.target_node_id
            : edge.target_node_id === current
              ? edge.source_node_id
              : null;
        if (otherId === null || seen.has(otherId)) continue;
        const node = byId.get(otherId);
        if (!node) continue;
        seen.add(otherId);
        next.push(otherId);
        trace.push({ edge, node, hop });
      }
    }
    frontier = next;
    if (frontier.length === 0) break;
  }
  return trace;
}
