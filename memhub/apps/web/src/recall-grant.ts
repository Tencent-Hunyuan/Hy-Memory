/** What "allow this app to use memory" means when nobody narrows it.
 *
 * The scope of a recall grant is a list of tools plus a list of memory scopes,
 * and until now every one of them had to be ticked by hand: an app that had
 * just been connected showed an empty sheet, so the fastest path to a working
 * connection was to click each box and then walk through a second screen to
 * confirm. Reported from real use as "I have to click through them one by one,
 * and then confirm the same thing again".
 *
 * This module owns the answer to "everything", so the permission sheet, the
 * one-click allow, and the bulk allow for every app cannot drift apart. It is
 * deliberately a pure function of what the caller can see, because the same
 * value has to be reproducible by the desktop-side guard that decides whether
 * a submitted revision is the one it expected.
 */
import type { SourceResponse } from "@one-memory/contracts";
import type { RecallPermissionTool } from "./global";

export interface GrantSelection {
  allowedTools: RecallPermissionTool[];
  allowedScopes: string[];
}

/** Every tool the sheet can offer this installation.
 *
 * `om_ask` and `om_matters` are only in the list when a receipt already
 * carried them: their MCP tools are gone, but an older grant can still name
 * them, and dropping them here would silently change a permission nobody
 * asked to change.
 */
export function selectableTools(
  existing: readonly RecallPermissionTool[] = [],
): RecallPermissionTool[] {
  const tools: RecallPermissionTool[] = [
    "om_recall",
    "om_conversations",
    "om_diagnostic_trace",
  ];
  if (existing.includes("om_ask")) tools.push("om_ask");
  if (existing.includes("om_matters")) tools.push("om_matters");
  return tools;
}

/** Sources whose raw conversations a grant may name.
 *
 * Kept in step with the desktop's own eligibility rule: an unauthorized
 * source, a source without a root, or the same source reported twice cannot be
 * named in a scope, because the guard on the other side would refuse it.
 */
export function grantableSources(
  sources: readonly SourceResponse[],
): SourceResponse[] {
  return sources
    .filter(
      (source) =>
        eligibleSource(source) &&
        sources.filter((item) => item.source_id === source.source_id).length ===
          1,
    )
    .map((source) => ({ ...source }));
}

export function eligibleSource(source: SourceResponse): boolean {
  return (
    source.authorized &&
    /^[a-z0-9][a-z0-9-]{0,127}$/.test(source.source_id) &&
    typeof source.root_id === "string" &&
    !!source.root_id.trim() &&
    Number.isSafeInteger(source.grant_revision) &&
    (source.grant_revision ?? 0) >= 1 &&
    (!source.candidate_root_id || source.candidate_root_id === source.root_id)
  );
}

/** Everything on offer, for a caller that has not narrowed anything.
 *
 * Reading raw conversations is the one tool that needs company: it is only
 * meaningful beside `all-apps` and at least one named source, so it is left out
 * when there is no eligible source to name rather than submitted as a selection
 * the guard would reject. `keep` carries the tools a previous receipt already
 * had, so re-granting cannot quietly shrink a permission.
 */
export function fullGrantSelection(input: {
  sources: readonly SourceResponse[];
  workspaces?: readonly { scope: string; label: string }[];
  keep?: readonly RecallPermissionTool[];
}): GrantSelection {
  const sources = grantableSources(input.sources);
  const tools = selectableTools(input.keep).filter(
    (tool) => tool !== "om_conversations" || sources.length > 0,
  );
  const scopes = [
    "all-apps",
    ...sources.map((source) => `source:${source.source_id}`),
    ...(input.workspaces ?? []).map((option) => option.scope),
  ];
  return {
    allowedTools: tools,
    allowedScopes: [...new Set(scopes)].sort(),
  };
}

/** Whether a selection is one the desktop guard accepts.
 *
 * Mirrors the sheet's own rule so a bulk allow cannot offer a combination the
 * single-app path would have greyed out.
 */
export function selectionIsValid(selection: GrantSelection): boolean {
  const scopes = selection.allowedScopes;
  return (
    selection.allowedTools.length > 0 &&
    scopes.some(
      (scope) => scope === "all-apps" || scope.startsWith("workspace:v1:"),
    ) &&
    (!selection.allowedTools.includes("om_conversations") ||
      (scopes.includes("all-apps") &&
        scopes.some((scope) => scope.startsWith("source:"))))
  );
}
