import { afterEach, describe, expect, it, vi } from "vitest";
import { BrokerClientError } from "./broker-client.js";
import { runContextCli } from "./context-cli.js";

const { contextThroughBroker } = vi.hoisted(() => ({
  contextThroughBroker: vi.fn(),
}));

vi.mock("./broker-client.js", async () => {
  const actual =
    await vi.importActual<typeof import("./broker-client.js")>(
      "./broker-client.js",
    );
  return { ...actual, contextThroughBroker };
});

afterEach(() => {
  contextThroughBroker.mockReset();
});

const empty = {
  schema_version: 2 as const,
  request_id: "fixture-request",
  state: "empty" as const,
  items: [] as const,
  used_characters: 0,
  truncated: false,
  total_ms: 1,
};

const raw = {
  kind: "raw" as const,
  raw_id: "e1",
  content: "original",
  source: "codex",
  session_id: "fixture-conversation",
  memory_at: null,
  score: 0.5,
  truncated: false,
};

const env = {
  ONE_MEMORY_BROKER_DISCOVERY: "/fixture/discovery.json",
  ONE_MEMORY_HOST_CREDENTIAL_FILE: "/fixture/credential",
};

describe("om_context CLI receipts", () => {
  it("renders SDK-native context without legacy path or adoption fields", async () => {
    contextThroughBroker.mockResolvedValue({
      schema_version: 2,
      request_id: "sdk-request",
      state: "grounded",
      items: [
        {
          kind: "memory",
          memory_id: "memory-1",
          content: "native memory",
          layer: "l2_fact",
          status: "active",
          source: "codex",
          updated_at: "2026-10-21T00:00:00Z",
          score: 0.9,
          truncated: false,
        },
      ],
      used_characters: 13,
      truncated: false,
      total_ms: 1,
    });

    const result = await runContextCli(["native"], env);

    expect(result.exitCode).toBe(0);
    expect(result.stdout).toContain("MemHub context v2: grounded");
    expect(result.stdout).toContain("native memory");
    expect(result.stdout).not.toMatch(/adoption|paths|revision|evidence_ids/);
  });

  it("announces a complete hit without treating it as adoption", async () => {
    contextThroughBroker.mockResolvedValue({
      ...empty,
      state: "grounded",
      items: [raw],
      used_characters: 8,
    });

    const result = await runContextCli(["original"], env);

    expect(result.exitCode).toBe(0);
    expect(result.stdout).toContain("成功使用 MemHub 回忆到相关记忆。");
    expect(result.stdout).not.toContain("获取部分相关记忆");
    expect(result.stdout).toContain("e1");
    expect(result.stdout).not.toContain("adoption");
  });

  it("keeps an ordinary empty distinct from success, partial, and outage", async () => {
    contextThroughBroker.mockResolvedValue(empty);

    const result = await runContextCli(["original"], env);

    expect(result.exitCode).toBe(0);
    expect(result.stdout).toContain("已完成MemHub的记忆查询。");
    expect(result.stdout).not.toContain("成功使用 MemHub");
    expect(result.stdout).not.toContain("获取部分相关记忆");
    expect(result.stdout).not.toContain("暂时不可用");
    expect(result.stdout).not.toContain("覆盖有限");
  });

  it("uses the partial-memory receipt only when items were returned", async () => {
    contextThroughBroker.mockResolvedValue({
      ...empty,
      state: "partial",
      items: [raw],
      truncated: true,
      used_characters: 8,
    });

    const result = await runContextCli(["original"], env);

    expect(result.stdout).toContain(
      "已从 MemHub 获取部分相关记忆，以下将补充这些信息继续。",
    );
    expect(result.stdout).not.toContain("成功使用 MemHub");
    expect(result.stdout).toContain("e1");
  });

  it("does not call a zero-hit partial empty or successful", async () => {
    contextThroughBroker.mockResolvedValue({
      ...empty,
      state: "partial",
      truncated: true,
    });

    const result = await runContextCli(["original"], env);

    expect(result.stdout).toContain("已完成MemHub的记忆查询。");
    expect(result.stdout).not.toContain("获取部分相关记忆");
    expect(result.stdout).not.toContain("成功使用 MemHub");
    expect(result.stdout).not.toContain("成功使用 MemHub 回忆到相关记忆。");
  });

  it("says unavailable for a returned outage, not a permission denial", async () => {
    contextThroughBroker.mockResolvedValue({
      ...empty,
      state: "unavailable",
    });

    const result = await runContextCli(["original"], env);

    expect(result.exitCode).toBe(4);
    expect(result.stdout).toContain(
      "MemHub 暂时不可用，我将先依据当前对话继续。",
    );
    expect(result.stdout).not.toContain("超出当前授权范围");
    expect(result.stdout).not.toContain("成功使用 MemHub");
  });

  it("uses the denial receipt for host_scope_denied and does not invite a bypass", async () => {
    contextThroughBroker.mockRejectedValue(
      new BrokerClientError("host_scope_denied"),
    );

    const result = await runContextCli(["original"], env);

    expect(result.exitCode).toBe(4);
    expect(result.stdout).toContain(
      "本次记忆查询超出当前授权范围，我将先依据当前可用信息继续。",
    );
    expect(result.stdout).toContain("Do not retry through other tools");
    expect(result.stdout).not.toContain("暂时不可用");
    expect(result.stdout).not.toContain("成功使用 MemHub");
  });

  it("does not describe an upgrade requirement as a permission denial", async () => {
    contextThroughBroker.mockRejectedValue(
      new BrokerClientError("context_upgrade_required"),
    );

    const result = await runContextCli(["original"], env);

    expect(result.exitCode).toBe(5);
    expect(result.stdout).toContain(
      "MemHub 暂时不可用，我将先依据当前对话继续。",
    );
    expect(result.stdout).toContain("Do not retry through other tools");
    expect(result.stdout).not.toContain("超出当前授权范围");
  });

  it("leaves --json as the structured envelope, without rewriting receipts", async () => {
    const payload = {
      ...empty,
      state: "grounded",
      items: [raw],
      used_characters: 8,
    };
    contextThroughBroker.mockResolvedValue(payload);

    const result = await runContextCli(["original", "--json"], env);

    expect(JSON.parse(result.stdout)).toEqual(payload);
    expect(result.stdout).not.toContain("成功使用 MemHub");
  });
});
