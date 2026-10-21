import type {
  MemoryDetail,
  SourceResponse,
  UiLanguage,
} from "@one-memory/contracts";
import type { RecallHostStatus } from "./global";
import { promptFrom } from "./HandoffBar";

export type GuideScenario = "same" | "switch" | "new";
export type GuideRoute = "context" | "query";

export const GUIDE_SCENARIOS = [
  {
    id: "same",
    label: "新对话接着做",
    description: "同一个应用，换个会话，仍能找回之前的决定。",
  },
  {
    id: "switch",
    label: "换个应用继续",
    description: "工作没有换，只是换了一个 AI。先把相关背景接上。",
  },
  {
    id: "new",
    label: "让新工具了解我",
    description: "从你愿意提供的一条背景开始，不必从头自我介绍。",
  },
] as const;

export function guideDetailAvailable(
  detail: MemoryDetail,
  sources: SourceResponse[],
): boolean {
  const memory = detail.memory;
  if (
    memory.status !== "confirmed" ||
    memory.deleted_at ||
    !memory.evidence_ids.length
  )
    return false;
  return memory.evidence_ids.every((id) => {
    const evidence = detail.evidence.find((item) => item.evidence_id === id);
    if (!evidence?.available || !evidence.verbatim || !evidence.source_id)
      return false;
    const source = sources.find(
      (item) => item.source_id === evidence.source_id,
    );
    return !source || source.authorized;
  });
}

export function sameGuideMemory(
  previous: MemoryDetail,
  next: MemoryDetail,
): boolean {
  const a = previous.memory,
    b = next.memory;
  return (
    a.memory_id === b.memory_id &&
    a.revision === b.revision &&
    a.status === b.status &&
    a.content === b.content &&
    a.deleted_at === b.deleted_at &&
    JSON.stringify(a.scopes) === JSON.stringify(b.scopes) &&
    JSON.stringify(a.evidence_ids) === JSON.stringify(b.evidence_ids) &&
    JSON.stringify(previous.evidence) === JSON.stringify(next.evidence)
  );
}

export function guideHosts(
  scene: GuideScenario,
  detail: MemoryDetail | null,
  hosts: RecallHostStatus[],
): RecallHostStatus[] {
  const sourceIds = new Set(
    detail?.evidence.map((evidence) => evidence.source_id),
  );
  return hosts.filter(
    (host) =>
      host.present &&
      (scene === "new" ||
        (scene === "same"
          ? sourceIds.has(host.hostId)
          : !sourceIds.has(host.hostId))),
  );
}

export function draftForScenario(
  scene: GuideScenario,
  route: GuideRoute,
  topic: string,
  detail: MemoryDetail,
  sourceLabel: string,
  language: UiLanguage = "zh-CN",
): string {
  if (route === "context") {
    if (language === "en") {
      const introduction =
        scene === "new"
          ? "This is background I chose to share with you, not a complete profile of me. Do not treat project constraints as long-term personal preferences."
          : scene === "switch"
            ? `I want to continue the work we discussed in ${sourceLabel}.`
            : "I am continuing earlier work in a new conversation.";
      return `${introduction}\n\n${promptFrom([detail.memory], topic.trim() || "this work", undefined, language)}\n\nScope (not expanded): ${detail.memory.scopes.join(", ")}\nFirst check the scope and the current task. Do not act on this background alone.`;
    }
    const introduction =
      scene === "new"
        ? "这是我主动选择给你的背景，不代表完整个人画像；项目约束不要推广成长期个人偏好。"
        : scene === "switch"
          ? `我想接着在 ${sourceLabel} 讨论的工作继续。`
          : "我在新会话里接着之前的工作继续。";
    return `${introduction}\n\n${promptFrom([detail.memory], topic.trim() || "这次工作")}\n\n适用范围（未扩大）：${detail.memory.scopes.join("、")}\n请先核对适用范围和当前任务，不要仅凭这段背景执行任何操作。`;
  }
  if (language === "en") {
    const subject = topic.trim() || "the work we discussed";
    const opening =
      scene === "same"
        ? "This is a new conversation. "
        : scene === "switch"
          ? `I want to continue the work we discussed in ${sourceLabel}. `
          : "This is my first time working on this here. ";
    return `${opening}First use MemHub to look up prior decisions, constraints and open questions related to “${subject}”. List the sources and citations actually returned, then explain your understanding of the background. Distinguish project requirements from personal preferences; do not infer a complete personal profile. If nothing is found or the connection is unavailable, say so explicitly. Do not guess, expand access permissions, or start a new task yet.`;
  }
  const subject = topic.trim() || "之前讨论的工作";
  const opening =
    scene === "same"
      ? "这是一个新会话。"
      : scene === "switch"
        ? `我想接着在 ${sourceLabel} 讨论的工作继续。`
        : "这是我第一次在这里开始这项工作。";
  return `${opening}请先用 MemHub 查询与「${subject}」相关的已有决定、约束和未决问题，列出实际返回的来源与引用，再说明你理解的背景。区分项目要求和个人偏好，不要推断完整个人画像。没有找到或无法连接时请明确说明，不猜测、不扩大授权，也先不要执行新任务。`;
}
