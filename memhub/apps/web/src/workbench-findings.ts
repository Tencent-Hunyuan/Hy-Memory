import type {
  GovernedMemory,
  MemoryProcessingSummary,
  SourceResponse,
} from "@one-memory/contracts";

import {
  type GuiLocale,
  translate,
  translateCount,
} from "@one-memory/i18n";

/** One row of the left column: a topic, with what is actually under it. */
export interface Matter {
  /** `topic:<tag>`, or `""` for the everything row. */
  scope: string;
  label: string;
  memoryCount: number;
  sourceCount: number;
}

/** A finding the app made on its own, stated so the user can check it.
 *
 * `memoryIds` is what the claim rests on: the interface highlights those
 * nodes and filters the evidence column to them, which is how a reader gets
 * from a sentence to the original words behind it. */
export interface Finding {
  id: string;
  kind: "duplicate" | "dormant" | "blocked";
  label: string;
  text: string;
  memoryIds: string[];
}

const EVERYTHING = "";
const TOPIC = "topic:";

/** Group memories into the topics they carry.
 *
 * Grouped by tag, not by scope. A scope is an authorisation — which apps may
 * read the memory — and in a real library it reads
 * `workspace:v1:<sha256>`, so a column built on it listed nine rows of hash
 * under the heading 事项. It was the wrong axis wearing the right label.
 *
 * A memory with several tags belongs under each of them: a tag is what the
 * memory is about, not a folder it was filed into. */
export function mattersOf(
  memories: GovernedMemory[],
  sources: SourceResponse[],
): Matter[] {
  const byTag = new Map<string, GovernedMemory[]>();
  for (const memory of memories) {
    for (const tag of memory.tags) {
      const held = byTag.get(tag);
      if (held) held.push(memory);
      else byTag.set(tag, [memory]);
    }
  }

  const matters: Matter[] = [
    {
      scope: EVERYTHING,
      label: "全部",
      memoryCount: memories.length,
      sourceCount: countSources(memories, sources),
    },
  ];
  for (const [tag, held] of byTag) {
    // Only a tag two memories share. One memory alone under a heading is
    // that memory listed twice, and a young library is mostly tags like
    // that — sixteen of twenty-two on the first real import.
    if (held.length < 2) continue;
    matters.push({
      scope: `${TOPIC}${tag}`,
      label: tag,
      memoryCount: held.length,
      sourceCount: countSources(held, sources),
    });
  }
  // Biggest first: the topic with the most in it is the one the user is most
  // likely to have come here for.
  return [
    matters[0],
    ...matters
      .slice(1)
      .sort((left, right) => right.memoryCount - left.memoryCount),
  ];
}

export function memoriesInMatter(
  memories: GovernedMemory[],
  scope: string,
): GovernedMemory[] {
  if (scope === EVERYTHING) return memories;
  if (scope.startsWith(TOPIC)) {
    const tag = scope.slice(TOPIC.length);
    return memories.filter((item) => item.tags.includes(tag));
  }
  // Still answered, because a scope can arrive from a saved selection made
  // before this column was a topic column.
  return memories.filter((item) => item.scopes.includes(scope));
}

function countSources(
  memories: GovernedMemory[],
  sources: SourceResponse[],
): number {
  // Evidence ids do not name their source, so the only honest count available
  // here is how many authorised sources have anything imported at all.
  if (!memories.length) return 0;
  return sources.filter(
    (item) => item.authorized && item.imported_conversations > 0,
  ).length;
}

/** What the app noticed, without a generation model.
 *
 * Deliberately not the four in the prototype: "矛盾" needs a model to read
 * two memories and judge that they disagree, and asserting it from text
 * overlap alone would be a guess dressed as a finding. These four are each
 * computable from records that are already local, so every one of them can be
 * checked by clicking it. */
export function findingsOf({
  memories,
  processing,
  now,
  locale = "zh-CN",
}: {
  memories: GovernedMemory[];
  processing: MemoryProcessingSummary | null;
  now: Date;
  locale?: GuiLocale;
}): Finding[] {
  const findings: Finding[] = [];

  for (const pair of sharingEvidence(memories)) {
    findings.push({
      id: `duplicate:${pair[0].memory_id}:${pair[1].memory_id}`,
      kind: "duplicate",
      label: translate(locale, "wb.finding.duplicate"),
      text: translate(locale, "wb.finding.duplicateText", {
        first: clip(pair[0].content),
        second: clip(pair[1].content),
        firstScope: scopesText(pair[0], locale),
        secondScope: scopesText(pair[1], locale),
      }),
      memoryIds: [pair[0].memory_id, pair[1].memory_id],
    });
  }

  const dormant = memories.filter(
    (item) =>
      item.status === "confirmed" && daysSince(item.updated_at, now) >= 60,
  );
  if (dormant.length >= 3) {
    findings.push({
      id: "dormant",
      kind: "dormant",
      label: translate(locale, "wb.finding.dormant"),
      text: translate(locale, "wb.finding.dormantText", {
        count: dormant.length,
      }),
      memoryIds: dormant.map((item) => item.memory_id),
    });
  }

  const blocked = processing?.blocked_stages ?? [];
  const blockedCount = blocked.reduce((total, item) => total + item.count, 0);
  if (blockedCount) {
    findings.push({
      id: "blocked",
      kind: "blocked",
      label: translate(locale, "wb.finding.blocked"),
      text: translateCount(locale, "wb.finding.blockedText", blockedCount, {
        count: blockedCount,
      }),
      memoryIds: [],
    });
  }

  return findings;
}

/** Pairs whose wording overlaps enough that they are almost certainly the
 * same statement recorded twice.
 *
 * Character trigrams rather than words: Chinese has no spaces, so a word
 * split would need a tokeniser to say anything at all. */
/** Memories extracted from the very same original sentence.
 *
 * The first version of this compared the memories' own wording — character
 * trigrams, Jaccard over 0.6 — and measuring it on real sentences killed it.
 * Four pairs that are plainly the same instruction scored 0.28 to 0.57, while
 * 「PersonaMem 的失败主要卡在提取层」and「PersonaMem 的检索层先放着不动」, which
 * say opposite things, scored 0.346. Any cutoff that catches the first group
 * also catches that one, so no threshold exists. Chinese without spaces makes
 * the surface too similar; deciding two sentences mean the same thing needs a
 * model, and guessing it from overlap would be a guess wearing a finding's
 * clothes.
 *
 * Shared evidence is not a guess. Two memories citing one sentence were pulled
 * out of the same words, which is worth a second look — and the wording asks
 * rather than asserts, because the same sentence can carry two real facts. */
function sharingEvidence(
  memories: GovernedMemory[],
): Array<[GovernedMemory, GovernedMemory]> {
  const live = memories.filter((item) => item.status === "confirmed");
  const pairs: Array<[GovernedMemory, GovernedMemory]> = [];
  for (let i = 0; i < live.length; i += 1) {
    for (let j = i + 1; j < live.length; j += 1) {
      const shared = live[i].evidence_ids.some((id) =>
        live[j].evidence_ids.includes(id),
      );
      if (!shared) continue;
      pairs.push([live[i], live[j]]);
      // Two of these is already enough to act on; a wall of them would bury
      // the other three findings.
      if (pairs.length === 2) return pairs;
    }
  }
  return pairs;
}

function daysSince(iso: string, now: Date): number {
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return 0;
  return (now.getTime() - then) / 86_400_000;
}

function clip(text: string, limit = 18): string {
  return text.length > limit ? `${text.slice(0, limit)}…` : text;
}

function scopesText(memory: GovernedMemory, locale: GuiLocale): string {
  return memory.scopes.length
    ? memory.scopes
        .map((scope) => scopeLabel(scope, locale))
        .join(locale === "zh-CN" ? "、" : ", ")
    : translate(locale, "wb.unclassified");
}

/** Say where a memory is authorised, in words.
 *
 * A workspace scope is a one-way hash of a directory, so its real name is
 * not recoverable here — the plaintext lives on the evidence rows in Core.
 * The short id is printed instead of the whole digest: it cannot name the
 * project, but it does tell two projects apart, which is what this sentence
 * is for. Printing `v1:0027abad1745330f1b57671635e0e9b3bd0cc3d2…` told the
 * reader nothing and filled the line. */
function scopeLabel(scope: string, locale: GuiLocale): string {
  if (scope === "all-apps") return translate(locale, "wb.profile");
  if (scope.startsWith("workspace:v1:")) {
    return translate(locale, "wb.project", {
      id: scope.slice("workspace:v1:".length, "workspace:v1:".length + 8),
    });
  }
  const [head, ...rest] = scope.split(":");
  if (rest.length) return rest.join(":");
  return head;
}
