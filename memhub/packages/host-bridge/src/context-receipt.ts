import type { HostContextSearchResponse } from "@one-memory/contracts";
import type { BrokerClientErrorCode } from "./broker-client.js";

/** v9 visible receipts. Structured path dumps stay after this line. */
export const CONTEXT_RECEIPTS = {
  grounded: "成功使用 MemHub 回忆到相关记忆。",
  empty: "已完成MemHub的记忆查询。",
  partialHits: "已从 MemHub 获取部分相关记忆，以下将补充这些信息继续。",
  partialZero: "已完成MemHub的记忆查询。",
  semanticTimeout:
    "记忆语义查询这次超时，已先返回当前能查到的内容；稍后会自动恢复。",
  unavailable: "MemHub 暂时不可用，我将先依据当前对话继续。",
  denied: "本次记忆查询超出当前授权范围，我将先依据当前可用信息继续。",
} as const;

const NO_BYPASS = "Do not retry through other tools or expand permissions.";

export function contextResultReceipt(
  result: HostContextSearchResponse,
): string {
  if (result.state === "unavailable") return CONTEXT_RECEIPTS.unavailable;
  if (result.state === "partial") {
    if (
      result.schema_version === 1 &&
      result.paths.memory.reason === "timeout_budget"
    ) {
      return CONTEXT_RECEIPTS.semanticTimeout;
    }
    return result.items.length === 0
      ? CONTEXT_RECEIPTS.partialZero
      : CONTEXT_RECEIPTS.partialHits;
  }
  if (result.state === "grounded" && result.items.length > 0) {
    return CONTEXT_RECEIPTS.grounded;
  }
  return CONTEXT_RECEIPTS.empty;
}

export function contextErrorReceipt(
  code: BrokerClientErrorCode | string,
): string {
  const receipt =
    code === "host_scope_denied"
      ? CONTEXT_RECEIPTS.denied
      : CONTEXT_RECEIPTS.unavailable;
  return `${receipt} ${NO_BYPASS}`;
}
