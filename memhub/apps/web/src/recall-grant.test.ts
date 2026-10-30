import type { SourceResponse } from "@one-memory/contracts";
import { describe, expect, it } from "vitest";
import {
  eligibleSource,
  fullGrantSelection,
  grantableSources,
  selectableTools,
  selectionIsValid,
} from "./recall-grant";

function source(id = "workbuddy", overrides: Partial<SourceResponse> = {}) {
  return {
    source_id: id,
    display_name: id,
    authorized: true,
    available: true,
    detected: true,
    storage_kinds: ["jsonl"],
    root_label: `/root/${id}`,
    candidate_path: `/root/${id}`,
    candidate_kind: "default",
    root_id: `root-${id}`,
    grant_revision: 1,
    imported_messages: 1,
    imported_conversations: 1,
    sync_completeness: "additions_only",
    capabilities: {
      historical_import: "implemented",
      incremental_import: "implemented",
      live_sync: "implemented",
      recall: "not_implemented",
      installer: "not_implemented",
      verification: "fixture_only",
    },
    ...overrides,
  } as SourceResponse;
}

const workspace = (hex: string) => `workspace:v1:${hex.repeat(64)}`;

describe("selectableTools", () => {
  it("offers every default tool a new installation can be granted", () => {
    expect(selectableTools()).toEqual([
      "om_recall",
      "om_conversations",
      "om_diagnostic_trace",
    ]);
  });

  it("keeps matters only when an earlier grant already had it", () => {
    expect(selectableTools(["om_matters"])).toContain("om_matters");
    expect(selectableTools(["om_recall"])).not.toContain("om_matters");
  });

  it("keeps ask only when an earlier grant already had it", () => {
    expect(selectableTools(["om_ask"])).toContain("om_ask");
    expect(selectableTools(["om_recall"])).not.toContain("om_ask");
  });
});

describe("eligibleSource", () => {
  it.each([
    ["unauthorized", { authorized: false }],
    ["no root", { root_id: "" }],
    ["no grant revision", { grant_revision: undefined }],
    ["zero grant revision", { grant_revision: 0 }],
    ["replaced root", { candidate_root_id: "a-new-root" }],
    ["bad source id", { source_id: "Not A Source" }],
  ])("refuses a %s source", (_label, patch) => {
    expect(eligibleSource(source("workbuddy", patch))).toBe(false);
  });

  it("accepts a source whose pending root is the one it already has", () => {
    expect(
      eligibleSource(
        source("workbuddy", {
          candidate_root_id: "root-workbuddy",
        }),
      ),
    ).toBe(true);
  });
});

describe("grantableSources", () => {
  it("drops ineligible sources", () => {
    const list = [source("a"), source("b", { authorized: false })];
    expect(grantableSources(list).map((item) => item.source_id)).toEqual(["a"]);
  });

  it("drops a source reported twice, since one scope cannot name two", () => {
    const list = [source("a"), source("a", { root_id: "other" })];
    expect(grantableSources(list)).toEqual([]);
  });

  it("does not hand the caller the caller's own objects", () => {
    const list = [source("a")];
    expect(grantableSources(list)[0]).not.toBe(list[0]);
  });
});

describe("fullGrantSelection", () => {
  it("selects every tool, all-apps, and every eligible source", () => {
    const selection = fullGrantSelection({
      sources: [source("workbuddy"), source("cursor")],
    });
    expect(selection.allowedTools).toEqual([
      "om_recall",
      "om_conversations",
      "om_diagnostic_trace",
    ]);
    expect(selection.allowedScopes).toEqual([
      "all-apps",
      "source:cursor",
      "source:workbuddy",
    ]);
  });

  it("leaves out raw conversations when there is no source to name", () => {
    const selection = fullGrantSelection({ sources: [] });
    expect(selection.allowedTools).toEqual([
      "om_recall",
      "om_diagnostic_trace",
    ]);
    expect(selection.allowedScopes).toEqual(["all-apps"]);
  });

  it("includes the workspaces it was given", () => {
    const scope = workspace("c");
    const selection = fullGrantSelection({
      sources: [],
      workspaces: [{ scope, label: "隔离项目" }],
    });
    expect(selection.allowedScopes).toEqual(["all-apps", scope]);
  });

  it("keeps a tool the previous receipt already had", () => {
    const selection = fullGrantSelection({
      sources: [source("workbuddy")],
      keep: ["om_matters"],
    });
    expect(selection.allowedTools).toContain("om_matters");
  });

  it("de-duplicates and sorts scopes", () => {
    const scope = workspace("a");
    const selection = fullGrantSelection({
      sources: [source("workbuddy"), source("cursor")],
      workspaces: [
        { scope, label: "x" },
        { scope, label: "x" },
      ],
    });
    expect(selection.allowedScopes).toEqual([
      "all-apps",
      "source:cursor",
      "source:workbuddy",
      scope,
    ]);
  });

  it("is a selection the desktop guard accepts", () => {
    expect(
      selectionIsValid(fullGrantSelection({ sources: [source("workbuddy")] })),
    ).toBe(true);
    expect(selectionIsValid(fullGrantSelection({ sources: [] }))).toBe(true);
  });
});

describe("selectionIsValid", () => {
  it("refuses an empty tool list", () => {
    expect(
      selectionIsValid({ allowedTools: [], allowedScopes: ["all-apps"] }),
    ).toBe(false);
  });

  it("refuses a scope list with no app-wide or workspace scope", () => {
    expect(
      selectionIsValid({
        allowedTools: ["om_recall"],
        allowedScopes: ["source:workbuddy"],
      }),
    ).toBe(false);
  });

  it("accepts a workspace-only scope for recall", () => {
    expect(
      selectionIsValid({
        allowedTools: ["om_recall"],
        allowedScopes: [workspace("d")],
      }),
    ).toBe(true);
  });

  it("refuses raw conversations without both all-apps and a named source", () => {
    const tools = ["om_conversations"] as const;
    expect(
      selectionIsValid({
        allowedTools: [...tools],
        allowedScopes: ["all-apps", "source:workbuddy"],
      }),
    ).toBe(true);
    expect(
      selectionIsValid({
        allowedTools: [...tools],
        allowedScopes: ["all-apps"],
      }),
    ).toBe(false);
    expect(
      selectionIsValid({
        allowedTools: [...tools],
        allowedScopes: ["source:workbuddy", workspace("e")],
      }),
    ).toBe(false);
  });
});
