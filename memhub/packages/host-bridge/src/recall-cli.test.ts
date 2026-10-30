import { mkdtemp, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";

import { runRecallCli } from "./recall-cli.js";

const { recallThroughBroker } = vi.hoisted(() => ({
  recallThroughBroker: vi.fn(),
}));

vi.mock("./broker-client.js", () => ({ recallThroughBroker }));

afterEach(() => {
  recallThroughBroker.mockReset();
});

async function discovery(status: "running" | "stopped") {
  const root = await mkdtemp(path.join(os.tmpdir(), "recall-cli-"));
  const file = path.join(root, "broker.json");
  await writeFile(
    file,
    JSON.stringify({
      schema_version: 1,
      service: "one-memory-recall-broker",
      status,
      host: "127.0.0.1",
      port: status === "running" ? 18765 : null,
      instance_id: "instance",
      pid: 1234,
      updated_at: new Date().toISOString(),
    }),
    { encoding: "utf8" },
  );
  return { ONE_MEMORY_BROKER_DISCOVERY: file };
}

const HIT = {
  memory_id: "m1",
  content: "用户要求统一用 axios 封装，不用 fetch 裸调",
  kind: "chat" as const,
  scopes: ["workspace:v1:abc"],
  evidence_ids: ["e1"],
  updated_at: "2026-08-08T03:16:40.000Z",
  score: 0.8,
};

describe("recall cli", () => {
  it("writes the memories as prose a model can just read", async () => {
    recallThroughBroker.mockResolvedValue({
      state: "grounded",
      query: "代码风格偏好",
      scope: "all-apps",
      hits: [HIT],
      used_characters: 40,
      truncated: false,
    });

    const result = await runRecallCli(
      ["代码风格偏好"],
      await discovery("running"),
      "/Users/someone",
      "/Users/someone/work/api-service",
    );

    expect(result.exitCode).toBe(0);
    // Says which project was included; the receipt's own `scope` is the one
    // that was asked for, which would read "all-apps" over project memories.
    expect(result.stdout).toContain("含当前工程 api-service");
    expect(result.stdout).toContain(
      "1. 用户要求统一用 axios 封装，不用 fetch 裸调",
    );
    // Recalled memory arrives in the same channel as instructions, so the text
    // states the boundary rather than leaving the model to assume it.
    expect(result.stdout).toContain("当前用户的请求优先");
  });

  it("does not append governance updated_at to SDK-formatted memory", async () => {
    const formatted =
      "Memory: 用户要求统一用 axios 封装，不用 fetch 裸调\n" +
      "When: 2026-08-08 03:16:40 UTC";
    recallThroughBroker.mockResolvedValue({
      state: "grounded",
      query: "代码风格偏好",
      scope: "all-apps",
      hits: [{ ...HIT, content: formatted, content_format: "hy-sdk" }],
      used_characters: formatted.length,
      truncated: false,
    });

    const result = await runRecallCli(
      ["代码风格偏好"],
      await discovery("running"),
      "/Users/someone",
      "/Users/someone/work/api-service",
    );

    expect(result.stdout).toContain(formatted);
    expect(result.stdout).not.toContain("更新于 2026-08-08");
  });

  it("labels a raw-mode result as original conversation excerpts, not confirmed memories", async () => {
    recallThroughBroker.mockResolvedValue({
      state: "grounded",
      query: "发布日",
      scope: "all-apps",
      mode: "raw",
      fallback_reason: "model_unavailable",
      hits: [
        {
          memory_id: "raw-1",
          content: "我们把发布日改到周四。",
          content_format: "raw",
          kind: "raw",
          source: "cursor",
          session_id: "s1",
          scopes: [],
          evidence_ids: [],
          updated_at: "2026-09-13T00:00:00Z",
          score: 0.5,
        },
      ],
      used_characters: 11,
      truncated: false,
    });

    const result = await runRecallCli(
      ["发布日"],
      await discovery("running"),
      "/Users/someone",
      "/Users/someone",
    );

    expect(result.exitCode).toBe(0);
    expect(result.stdout).toContain("1 条原始对话片段");
    expect(result.stdout).toContain("尚未配置生成模型");
    expect(result.stdout).not.toContain("条已确认记忆");
    expect(result.stdout).toContain("1. 我们把发布日改到周四。");
    expect(result.stdout).toContain("原始对话 · cursor · 2026-09-13");
    expect(result.stdout).toContain("不代表已确认的记忆");
  });

  it("takes an unquoted sentence as one query", async () => {
    recallThroughBroker.mockResolvedValue({
      state: "empty",
      query: "",
      scope: "all-apps",
      hits: [],
      used_characters: 0,
      truncated: false,
    });

    await runRecallCli(
      ["我", "对", "请求库", "的", "偏好"],
      await discovery("running"),
      "/Users/someone",
      "/Users/someone",
    );

    // A shell-command skill interpolates the user's sentence, so dropping
    // everything after the first word would silently narrow the query.
    expect(recallThroughBroker.mock.calls[0][1]).toEqual({
      query: "我 对 请求库 的 偏好",
    });
  });

  it("reports no memory as an answer, not as an error", async () => {
    recallThroughBroker.mockResolvedValue({
      state: "empty",
      query: "x",
      scope: "all-apps",
      hits: [],
      used_characters: 0,
      truncated: false,
    });

    const result = await runRecallCli(["x"], await discovery("running"));

    expect(result.exitCode).toBe(0);
    // The product's committed wording, shared with the context path: an empty
    // lookup is a completed lookup. "No related memories" reads to a model as
    // evidence about the user — that they have no preferences — which is
    // exactly what it must not invent.
    expect(result.stdout).toContain("已完成MemHub的记忆查询。");
    // Saying so is not enough; a model told only "nothing found" will often
    // fill the gap itself.
    expect(result.stdout).toContain("不要凭空编造");
    // And it must not fall back to the older "found nothing" phrasing.
    expect(result.stdout).not.toContain("没有与这个问题相关的已确认记忆");
  });

  it("treats a closed app as ordinary and never calls the broker", async () => {
    const result = await runRecallCli(["x"], await discovery("stopped"));

    // The app is not always open. A host that abandons the turn on a nonzero
    // exit would turn that into a broken conversation.
    expect(result.exitCode).toBe(0);
    expect(result.stdout).toContain("没有在运行");
    expect(recallThroughBroker).not.toHaveBeenCalled();
  });

  it("does fail when the broker claims to be running and is not", async () => {
    recallThroughBroker.mockRejectedValue(new Error("connect ECONNREFUSED"));

    const result = await runRecallCli(["x"], await discovery("running"));

    // This one is a real fault: the discovery file says the app is up.
    expect(result.exitCode).toBe(4);
    expect(result.stdout).toContain("无法连接");
  });

  it("treats a missing discovery file as a closed app", async () => {
    const result = await runRecallCli(["x"], {
      ONE_MEMORY_BROKER_DISCOVERY: path.join(
        os.tmpdir(),
        "absent",
        "broker.json",
      ),
    });

    expect(result.exitCode).toBe(0);
    expect(result.stdout).toContain("没有在运行");
  });

  it("gives scripts the fields instead of the prose", async () => {
    const receipt = {
      state: "grounded",
      query: "x",
      scope: "all-apps",
      hits: [HIT],
      used_characters: 40,
      truncated: false,
    };
    recallThroughBroker.mockResolvedValue(receipt);

    const result = await runRecallCli(
      ["x", "--json"],
      await discovery("running"),
    );

    expect(JSON.parse(result.stdout)).toEqual(receipt);
  });

  it("passes the options through as the broker expects them", async () => {
    recallThroughBroker.mockResolvedValue({
      state: "empty",
      query: "x",
      scope: "workspace:v1:abc",
      hits: [],
      used_characters: 0,
      truncated: false,
    });

    await runRecallCli(
      [
        "x",
        "--scope",
        "workspace:v1:abc",
        "--limit",
        "3",
        "--character-budget",
        "500",
      ],
      await discovery("running"),
      "/Users/someone",
      "/Users/someone",
    );

    expect(recallThroughBroker.mock.calls[0][1]).toEqual({
      query: "x",
      scope: "workspace:v1:abc",
      limit: 3,
      character_budget: 500,
    });
  });

  it("refuses a query it would otherwise have to invent", async () => {
    const empty = await runRecallCli([], await discovery("running"));
    expect(empty.exitCode).toBe(2);
    expect(empty.stdout).toContain("query is required");

    const bad = await runRecallCli(
      ["x", "--limit", "0"],
      await discovery("running"),
    );
    expect(bad.exitCode).toBe(2);

    const unknown = await runRecallCli(
      ["x", "--wat"],
      await discovery("running"),
    );
    expect(unknown.exitCode).toBe(2);
    expect(unknown.stdout).toContain("Unknown option");

    expect(recallThroughBroker).not.toHaveBeenCalled();
  });

  it("says when it had more to say, without inventing a budget", async () => {
    recallThroughBroker.mockResolvedValue({
      state: "grounded",
      query: "x",
      scope: "all-apps",
      hits: [HIT],
      used_characters: 500,
      truncated: true,
    });

    const result = await runRecallCli(
      ["x"],
      await discovery("running"),
      "/Users/someone",
      "/Users/someone",
    );

    // Core may omit truncated_by on older receipts; do not invent a cause.
    expect(result.stdout).toContain("还有更多相关记忆未列出");
  });

  it("names the budget when Core says which one fired", async () => {
    recallThroughBroker.mockResolvedValue({
      state: "grounded",
      query: "x",
      scope: "all-apps",
      hits: [HIT],
      used_characters: 500,
      truncated: true,
      truncated_by: "character_budget",
      unexamined_count: 3,
    });

    const result = await runRecallCli(
      ["x"],
      await discovery("running"),
      "/Users/someone",
      "/Users/someone",
    );

    expect(result.stdout).toContain("另有 3 条相关记忆未检视");
    expect(result.stdout).toContain("字符预算");
  });
});

describe("recall cli and the project it is in", () => {
  it("names the shell's directory so project memories are reachable", async () => {
    recallThroughBroker.mockResolvedValue({
      state: "empty",
      query: "x",
      scope: "all-apps",
      hits: [],
      used_characters: 0,
      truncated: false,
    });

    await runRecallCli(
      ["x"],
      await discovery("running"),
      "/Users/someone",
      "/Users/someone/work/api-service",
    );

    // Without this, a workspace-scoped memory can never be recalled: its
    // scope hashes an identity only Core can produce.
    expect(recallThroughBroker.mock.calls[0][1]).toEqual({
      query: "x",
      workspace_path: "/Users/someone/work/api-service",
    });
  });

  it("lets a skill state the directory itself", async () => {
    recallThroughBroker.mockResolvedValue({
      state: "empty",
      query: "x",
      scope: "all-apps",
      hits: [],
      used_characters: 0,
      truncated: false,
    });

    await runRecallCli(
      ["x", "--workspace-path", "/Users/someone/work/billing"],
      await discovery("running"),
      "/Users/someone",
      "/Users/someone/work/api-service",
    );

    expect(recallThroughBroker.mock.calls[0][1]).toMatchObject({
      workspace_path: "/Users/someone/work/billing",
    });
  });

  it("says nothing about a project when run from home", async () => {
    recallThroughBroker.mockResolvedValue({
      state: "empty",
      query: "x",
      scope: "all-apps",
      hits: [],
      used_characters: 0,
      truncated: false,
    });

    await runRecallCli(
      ["x"],
      await discovery("running"),
      "/Users/someone",
      "/Users/someone",
    );

    expect(recallThroughBroker.mock.calls[0][1]).toEqual({ query: "x" });
  });
});
