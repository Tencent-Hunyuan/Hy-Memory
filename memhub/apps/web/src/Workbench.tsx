import type {
  GovernedMemory,
  HostAskMemoryResponse,
  MemoryDetail,
  MemoryGraphEdge,
  MemoryGraphNode,
  MemoryGraphResponse,
  SdkAskMemory,
} from "@one-memory/contracts";
import { Check, PencilLine, Trash2 } from "lucide-react";
import { type FormEvent, type RefObject, useId, useState } from "react";
import { useGuiLanguage } from "./gui-language";
import { HandoffBar } from "./HandoffBar";

type Translate = ReturnType<typeof useGuiLanguage>["t"];

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
  onDelete,
  requestIds,
  onClose,
  compact = false,
}: {
  node: MemoryGraphNode | null;
  graph: MemoryGraphResponse | null;
  detail: MemoryDetail | null;
  draft: string;
  busy: boolean;
  matterLabel: string;
  onDraft(next: string): void;
  onSave(event: FormEvent<HTMLFormElement>): void;
  onDelete(): void;
  requestIds: RefObject<Map<string, string>>;
  onClose(): void;
  compact?: boolean;
}) {
  const { locale, t } = useGuiLanguage();
  const [editing, setEditing] = useState(!compact);
  const formId = useId();
  if (detail) {
    const memory = detail.memory;
    return (
      <article className="wb-detail" aria-label={t("wb.memory")}>
        <div className="wb-detail-head">
          <span className={`wb-status s-${memory.status}`}>
            {statusLabel(memory.status, t)}
          </span>
          <span className="wb-detail-rev">
            {t("wb.revision", {
              revision: new Intl.NumberFormat(locale).format(memory.revision),
            })}
          </span>
          <button type="button" className="quiet" onClick={onClose}>
            {t("wb.collapse")}
          </button>
        </div>
        {compact && !editing && (
          <p className="map-memory-reading">{memory.content}</p>
        )}
        {compact && !editing && (
          <p className="map-origin">{localizedProvenance(detail, t)}</p>
        )}
        {editing && memory.status === "confirmed" ? (
          <form id={formId} onSubmit={onSave} aria-label={t("wb.correctForm")}>
            <label>
              {t("wb.content")}
              <textarea
                name="memory-content"
                aria-label={t("wb.content")}
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
              {t("wb.correct")}
            </button>
          )}
          {editing && memory.status === "confirmed" ? (
            <button type="submit" form={formId} disabled={busy}>
              <Check size={14} aria-hidden="true" />
              {t("wb.saveCorrection")}
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
              {t("wb.cancelEdit")}
            </button>
          )}
          {memory.status !== "deleted" ? (
            <button
              type="button"
              className="danger"
              disabled={busy}
              onClick={onDelete}
            >
              <Trash2 size={14} aria-hidden="true" />
              {t("wb.delete")}
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
      <article className="wb-detail" aria-label={t("wb.node")}>
        <div className="wb-detail-head">
          <span className="wb-status">{nodeTypeLabel(node.node_type, t)}</span>
          <strong>{node.label}</strong>
        </div>
        <p className="wb-detail-note">
          {t("wb.nodeNote", {
            direct: new Intl.NumberFormat(locale).format(direct.length),
            indirect: new Intl.NumberFormat(locale).format(
              trace.length - direct.length,
            ),
          })}
        </p>
        <ul className="wb-trace">
          {trace.slice(0, 8).map((step) => (
            <li key={`${step.edge.edge_id}:${step.hop}`}>
              <span className="wb-trace-hop">
                {step.hop === 1 ? t("wb.direct") : t("wb.twoHops")}
              </span>
              {relationLabel(step.edge.relation, t)} · {step.node.label}
              <span className="wb-trace-origin">
                {edgeOriginLabel(step.edge.origin, t)}
              </span>
            </li>
          ))}
        </ul>
      </article>
    );
  }

  return (
    <article className="wb-detail wb-detail-idle" aria-label={t("wb.matter")}>
      <div className="wb-detail-head">
        <strong>{matterLabel}</strong>
      </div>
      <p className="wb-detail-note">{t("wb.idle")}</p>
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
  answer: HostAskMemoryResponse | null;
  streaming: {
    text: string;
    memories: (GovernedMemory | SdkAskMemory)[];
  } | null;
  onOpenMemory(memoryId: string): void;
  onSaveAnswer(draft: SavedAnswer): void;
  asPage: boolean;
  suspended?: boolean;
}) {
  const { t, tn } = useGuiLanguage();
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
      <p>{answer ? answer.answer : streaming?.text || t("wb.searching")}</p>
      <p className="wb-answer-source">
        {answer
          ? tn("wb.answerSource", shownMemories.length, {
              mode:
                answer.mode === "generated"
                  ? t("wb.generated")
                  : answer.mode === "extractive"
                    ? t("wb.extractive")
                    : t("wb.noHit"),
            })
          : tn("wb.recalled", shownMemories.length)}
      </p>
      {groundedAnswer && !tooLong ? (
        <button
          type="button"
          className="quiet"
          onClick={() =>
            onSaveAnswer({
              content: groundedAnswer.answer,
              evidence_ids: [
                ...new Set(
                  shownMemories.flatMap((item) =>
                    "evidence_ids" in item ? item.evidence_ids : [],
                  ),
                ),
              ],
            })
          }
        >
          {t("wb.saveMemory")}
        </button>
      ) : null}
      {tooLong ? <p className="wb-answer-source">{t("wb.tooLong")}</p> : null}
      {asPage ? (
        // Not offered as a memory, and said out loud why. A synthesis is
        // rebuilt from the memories under it every time it is read, which is
        // the only reason it is safe to let a model write it: a stored
        // summary drifts from what it summarised and nothing notices, and a
        // rebuilt one cannot.
        <p className="wb-answer-source">{t("wb.summary")}</p>
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
          matterLabel={t("wb.thisAnswer")}
          answerText={answer.answer}
        />
      )}
    </div>
  );
}

function statusLabel(status: GovernedMemory["status"], t: Translate): string {
  if (status === "confirmed") return t("wb.available");
  if (status === "superseded") return t("wb.superseded");
  return t("wb.deleted");
}

function nodeTypeLabel(
  type: MemoryGraphNode["node_type"],
  t: Translate,
): string {
  if (type === "source") return t("wb.type.source");
  if (type === "topic") return t("wb.type.topic");
  if (type === "schema") return t("wb.type.schema");
  return t("wb.type.memory");
}

function edgeOriginLabel(
  origin: MemoryGraphEdge["origin"],
  t: Translate,
): string {
  if (origin === "hy_relation") return t("wb.origin.hy_relation");
  if (origin === "same_conversation") return t("wb.origin.same_conversation");
  // Named before the fallthrough. The chain ends in 原话来源, so a missing
  // case here is not an error — it is a line that says the wrong thing.
  if (origin === "shared_tag") return t("wb.origin.shared_tag");
  return t("wb.origin.provenance");
}

/** Walk out from one node so each drawn line can be checked by hand. */
function relationLabel(relation: string, t: Translate): string {
  const labels: Record<string, Parameters<Translate>[0]> = {
    evidence_source: "wb.relation.evidence_source",
    same_conversation: "wb.relation.same_conversation",
    shared_tag: "wb.relation.shared_tag",
    derived_from: "wb.relation.derived_from",
    tagged_with: "wb.relation.tagged_with",
    related_to: "wb.relation.related_to",
  };
  const key = labels[relation];
  return key ? t(key) : relation.replaceAll("_", " ");
}

function localizedProvenance(detail: MemoryDetail, t: Translate): string {
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
        ? t("provenance.multiple")
        : authors.has("person")
          ? t("provenance.person")
          : authors.has("agent")
            ? t("provenance.agent")
            : t("provenance.unknown");
    return t("provenance.imported", { author });
  }
  return detail.memory.origin === "manual"
    ? t("provenance.manual")
    : detail.memory.origin === "answered"
      ? t("provenance.answered")
      : detail.memory.origin === "agent"
        ? t("provenance.generated")
        : t("provenance.conversation");
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
