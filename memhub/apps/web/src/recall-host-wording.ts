import type { UiMessage } from "@one-memory/contracts";

/** How a host's link and call-rule states are said to the user.
 *
 * Two screens now report the same host — the source list and the health
 * panel — and a second copy of these tables is how one screen ends up
 * calling a host 已安装 while the other calls it 未接通.
 */

/** The three verdicts, said as what the user would do about them.
 *
 * "已安装" on its own is the word that hid the recorded failure — three hosts
 * were installed, and installed was all it said. So none of these three is
 * that word.
 */
export const LINK_WORDING: Record<string, UiMessage> = {
  wired: "已接通",
  reachable_but_blind: "已安装，但取不到记忆",
  unreachable: "未接通",
};

/** Call-rule skill drift, said as what the user would do about it (R-33).
 *
 * Kept beside the link wording on purpose: a host can be wired and still be
 * running on yesterday's instructions, and the reverse. Different failure
 * modes, different actions.
 */
export const SKILL_WORDING: Record<string, UiMessage> = {
  in_sync: "最新",
  stale: "需更新",
  modified: "你手动改过",
};
