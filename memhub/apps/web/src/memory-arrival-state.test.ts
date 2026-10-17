import type {
  MemoryProcessingSummary,
  SourceResponse,
} from "@one-memory/contracts";
import { expect, it } from "vitest";
import {
  type MemoryArrival,
  type MemoryArrivalPhase,
  memoryArrivalTitle,
  resolveMemoryArrival,
} from "./memory-arrival-state";

const processing: MemoryProcessingSummary = {
  pending: 0,
  fresh_pending: 0,
  backlog_pending: 0,
  outside_window_pending: 0,
  recent_completions: [],
  running: 0,
  waiting_for_model: 0,
  completed: 0,
  failed: 0,
  blocked_stages: [],
  seconds_per_completion: null,
  extraction_window_preset: "all",
  manual_extract_batches_planned: 0,
  manual_extract_batches_current: 0,
  active_queue: 0,
  in_flight_count: 0,
};
const sources = [
  { authorized: true, imported_messages: 12, imported_conversations: 3 },
  { authorized: false, imported_messages: 100, imported_conversations: 10 },
] as SourceResponse[];
const base = { processing, sources, loaded: true };

it("uses the parent's setup snapshot, not empty task counts or fallback totals", () => {
  const arrival: MemoryArrival = {
    importing: true,
    importedMessages: 29155,
    importedConversations: 400,
    phase: "model-required",
  };
  expect(resolveMemoryArrival({ ...base, arrival })).toBe(arrival);
});

it("does not infer missing setup or running from an empty or queued task summary", () => {
  expect(resolveMemoryArrival(base)).toEqual({
    importing: false,
    importedMessages: 12,
    importedConversations: 3,
    phase: "idle",
  });
  expect(
    resolveMemoryArrival({ ...base, processing: { ...processing, pending: 9 } })
      .phase,
  ).toBe("idle");
});

it.each([
  ["running", { running: 1 }, "processing"],
  ["model count", { waiting_for_model: 1 }, "model-required"],
  [
    "model block",
    {
      blocked_stages: [
        { kind: "extract", reason: "model_profile_not_active", count: 1 },
      ],
    },
    "model-required",
  ],
  ["failure", { failed: 1 }, "failed"],
  [
    "unknown block",
    { blocked_stages: [{ kind: "extract", reason: "unknown", count: 1 }] },
    "idle",
  ],
  [
    "zero count block",
    {
      blocked_stages: [
        { kind: "extract", reason: "model_profile_not_active", count: 0 },
      ],
    },
    "idle",
  ],
] as [string, Partial<MemoryProcessingSummary>, MemoryArrivalPhase][])(
  "fallback classifies only positive known signals: %s",
  (_name, change, phase) => {
    expect(
      resolveMemoryArrival({
        ...base,
        processing: {
          ...processing,
          ...change,
          blocked_stages: [...(change.blocked_stages ?? [])],
        },
      }).phase,
    ).toBe(phase);
  },
);

it("does not claim processing before the snapshot is loaded", () => {
  expect(resolveMemoryArrival({ ...base, loaded: false }).phase).toBe(
    "checking",
  );
  expect(resolveMemoryArrival({ ...base, processing: null }).phase).toBe(
    "checking",
  );
  expect(
    resolveMemoryArrival({ ...base, sources: [], processing: null }).phase,
  ).toBe("idle");
});

it("does not promise continued processing for stale or queued-only state", () => {
  const idle = resolveMemoryArrival({
    ...base,
    processing: { ...processing, pending: 50 },
  });
  expect(memoryArrivalTitle(idle)).not.toContain("正在");
  expect(memoryArrivalTitle({ ...idle, phase: "checking" })).toBe("进度待更新");
  expect(
    memoryArrivalTitle({ ...idle, phase: "checking", refreshing: true }),
  ).toBe("已保存内容保留，正在核对进度");
});

it.each([
  ["model_profile_not_active", "model-required"],
  ["hy_memory_model_authentication_failed", "model-required"],
  ["hy_memory_model_payment_required", "model-required"],
] as const)(
  "keeps a parent-confirmed %s blocker actionable even when old running counts remain",
  (reason, phase) => {
    expect(
      resolveMemoryArrival({
        ...base,
        arrival: {
          importing: true,
          importedMessages: 12,
          importedConversations: 3,
          phase,
        },
        processing: {
          ...processing,
          running: 1,
          blocked_stages: [{ kind: "extract", reason, count: 1 }],
        },
      }).phase,
    ).toBe(phase);
  },
);

it("keeps recoverable failures in details while confirmed processing continues", () => {
  const blocks = [
    { kind: "extract", reason: "hy_memory_input_exceeds_window", count: 4 },
  ];
  const result = resolveMemoryArrival({
    ...base,
    processing: {
      ...processing,
      running: 1,
      failed: 4,
      backlog_pending: 12,
      blocked_stages: blocks,
    },
  });
  expect(result.phase).toBe("processing");
  expect(result.blockedStages).toEqual(blocks);
  expect(result.backlogPending).toBe(12);
  expect(memoryArrivalTitle(result)).toBe("正在沉淀记忆");
});

it("does not ask anyone to look at a failure Core is already retrying", () => {
  const blocks = [
    {
      kind: "extract",
      reason: "hy_memory_write_verification_clause_incomplete",
      count: 1,
    },
    {
      kind: "extract",
      reason: "hy_memory_write_verification_failed",
      count: 1,
    },
  ];
  const result = resolveMemoryArrival({
    ...base,
    processing: { ...processing, failed: 2, blocked_stages: blocks },
  });

  // "failed" shows "请先核对失败原因，再决定是否重试" and a repair button.
  // "idle" does not, and with memories already on screen the arrival panel
  // takes itself off the page entirely.
  expect(result.phase).toBe("idle");
  // Still on hand: the counts are real, they are just nobody's job.
  expect(result.blockedStages).toEqual(blocks);
});

it.each([0, 134])(
  "active import with failures and %i queued tasks is not active generation",
  (pending) => {
    const blocks = [
      { kind: "extract", reason: "hy_memory_model_request_failed", count: 1 },
      { kind: "extract", reason: "hy_memory_input_exceeds_window", count: 5 },
    ];
    const result = resolveMemoryArrival({
      ...base,
      arrival: {
        importing: true,
        importedMessages: 7865,
        importedConversations: 30,
        phase: "importing",
        memoryCount: 0,
      },
      processing: { ...processing, failed: 6, pending, blocked_stages: blocks },
    });
    expect(result.phase).toBe("failed");
    expect(result.importing).toBe(true);
    expect(result.memoryCount).toBe(0);
    expect(result.blockedStages).toEqual(blocks);
    expect(memoryArrivalTitle(result)).toBe("待继续");
  },
);

it.each([
  "hy_memory_model_request_failed",
  "hy_memory_model_rate_limited",
  "hy_memory_input_exceeds_window",
  "hy_memory_model_authentication_failed",
])("a historical %s does not hide another confirmed running task", (reason) => {
  const result = resolveMemoryArrival({
    ...base,
    arrival: {
      importing: true,
      importedMessages: 7865,
      importedConversations: 30,
      phase: "failed",
    },
    processing: {
      ...processing,
      running: 1,
      failed: 6,
      pending: 134,
      waiting_for_model: 1,
      blocked_stages: [{ kind: "extract", reason, count: 1 }],
    },
  });
  expect(result.phase).toBe("processing");
  expect(result.blockedStages?.[0].reason).toBe(reason);
  expect(
    resolveMemoryArrival({ ...base, arrival: result, processing: null }).phase,
  ).toBe("processing");
});

it.each(["checking", "unavailable", "stopped", "model-required"] as const)(
  "explicit parent %s remains authoritative over a running count",
  (phase) => {
    expect(
      resolveMemoryArrival({
        ...base,
        arrival: {
          importing: true,
          importedMessages: 12,
          importedConversations: 3,
          phase,
        },
        processing: { ...processing, running: 1 },
      }).phase,
    ).toBe(phase);
  },
);

it("raw import alone has an import title, without a generated-memory promise", () => {
  const result = resolveMemoryArrival({
    ...base,
    arrival: {
      importing: true,
      importedMessages: 12,
      importedConversations: 3,
      phase: "importing",
    },
  });
  expect(result.phase).toBe("importing");
  expect(memoryArrivalTitle(result)).toBe("正在导入对话");
});
