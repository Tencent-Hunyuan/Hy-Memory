// @vitest-environment jsdom
import { beforeEach, expect, it, vi } from "vitest";

const fake = vi.hoisted(() => ({
  list: vi.fn(),
  clear: vi.fn(),
  clearAll: vi.fn(),
  references: vi.fn(),
}));
vi.mock("./api", () => ({
  getCallHistoryRequest: fake.list,
  clearCallEventsRequest: fake.clear,
  clearAllCallEventsRequest: fake.clearAll,
  getCallReferencesRequest: fake.references,
}));

import {
  type CallEvent,
  callDeliveryLabel,
  callDetailHeadline,
  callEventHeadline,
  callHostName,
  callReturnDeliveryLabel,
  callRowDetail,
  callRowHeadline,
  clearAllCallEvents,
  clearCallEvents,
  getCallHistory,
  getCallReferences,
} from "./call-history-client";

const event: CallEvent = {
  event_id: "11111111-1111-4111-8111-111111111111",
  occurred_at: "2026-09-20T00:00:00Z",
  host_id: "codex",
  installation_id: null,
  tool: "om_recall",
  state: "grounded",
  stage: "core_result",
  delivery: "unknown",
  adoption: "unknown",
  host_session_id: null,
  injection_elapsed_ms: null,
  elapsed_ms: 12,
  raw_count: 1,
  memory_count: 0,
  references: [{ kind: "raw", id: "e1", revision: 1, source_id: "codex" }],
  feedback: null,
};
const page = {
  schema_version: 1,
  available: true,
  recording_error: false,
  total: 1,
  offset: 0,
  limit: 20,
  items: [event],
  retention_days: 30,
  max_events: 10000,
};
beforeEach(() => {
  vi.resetAllMocks();
  fake.list.mockResolvedValue(structuredClone(page));
});
it("traduce entrega y límites sin atribuir adopción ni alterar anfitriones desconocidos", () => {
  const delivered = {
    ...event,
    host_id: "Aplicación 中文",
    delivery: "delivered" as const,
  };
  expect(callDeliveryLabel(delivered, "es")).toBe(
    "Entregado a Aplicación 中文",
  );
  expect(callDeliveryLabel(delivered)).toBe("已提供给 Aplicación 中文");
  expect(callDeliveryLabel(event, "es")).toBe("");
  expect(callReturnDeliveryLabel(event, "es")).toBe("");
  expect(
    callReturnDeliveryLabel({ ...delivered, state: "timed_out" }, "es"),
  ).toContain("Aplicación 中文 respondió sin memorias");
  expect(
    callReturnDeliveryLabel({ ...event, state: "denied" }, "es"),
  ).toContain("No se entregaron memorias");
  expect(callReturnDeliveryLabel({ ...event, state: "partial" }, "es")).toBe(
    "Parte del contenido sigue procesándose.",
  );
  expect(callReturnDeliveryLabel({ ...event, state: "unavailable" })).toBe(
    "MemHub 暂时不可用，这次回答没有带上记忆。",
  );
});

it("reads exact bounded history and current reference metadata", async () => {
  expect(await getCallHistory()).toEqual(page);
  expect(fake.list).toHaveBeenCalledExactlyOnceWith({ limit: 20, offset: 0 });
  const refs = [
    {
      ...event.references[0],
      available: true,
      title: "current permitted title",
      reason: null,
    },
  ];
  fake.references.mockResolvedValue(refs);
  expect(await getCallReferences(event)).toEqual(refs);
});
it.each([
  { total: -1 },
  { limit: 99 },
  { items: [{ ...event, adoption: "adopted" }] },
  { items: [{ ...event, delivery: "sent" }] },
  { items: [{ ...event, injection_elapsed_ms: Infinity }] },
  { items: [{ ...event, host_session_id: "\u0000bad" }] },
  { items: [{ ...event, feedback: "feedback-event" }] },
  { items: [{ ...event, query: "SECRET" }] },
  { items: [{ ...event, host_id: "forged" }] },
  { items: [{ ...event, elapsed_ms: Infinity }] },
  { items: [{ ...event, occurred_at: "Sep 20, 2026" }] },
  { items: [{ ...event, raw_count: 0 }] },
  {
    items: [
      { ...event, references: [{ ...event.references[0], title: "SECRET" }] },
    ],
  },
  { available: false },
])(
  "refuses malformed responses without projecting arbitrary data %#",
  async (patch) => {
    fake.list.mockResolvedValue({ ...page, ...patch });
    await expect(getCallHistory()).rejects.toThrow("call_history_unavailable");
  },
);
it("accepts every host the backend records, including ZCode", async () => {
  // The recorder already files zcode call events; the page contract refused
  // to name one, which turned a single ZCode recall into a whole-page
  // failure instead of a listing.
  fake.list.mockResolvedValue({
    ...page,
    items: [{ ...event, host_id: "zcode" }],
  });
  await expect(getCallHistory()).resolves.toMatchObject({
    items: [{ host_id: "zcode" }],
  });
  expect(callHostName("zcode")).toBe("ZCode");
});
it("does not call transport with invalid filters or clear IDs", async () => {
  await expect(getCallHistory({ hostId: "../host" })).rejects.toThrow();
  await expect(clearCallEvents(["not-an-id"])).rejects.toThrow();
  expect(fake.list).not.toHaveBeenCalled();
  expect(fake.clear).not.toHaveBeenCalled();
});
it("clear targets exactly the displayed IDs", async () => {
  fake.clear.mockResolvedValue({ removed: 1 });
  await clearCallEvents([event.event_id]);
  expect(fake.clear).toHaveBeenCalledTimes(1);
  expect(fake.clear.mock.calls[0][0]).toEqual([event.event_id]);
  fake.clear.mockResolvedValue({ removed: 2 });
  await expect(clearCallEvents([event.event_id])).rejects.toThrow();
});
it("clear-all uses the dedicated transport and never reuses page IDs", async () => {
  fake.clearAll.mockResolvedValue({ removed: 3 });
  await clearAllCallEvents();
  expect(fake.clearAll).toHaveBeenCalledTimes(1);
  expect(fake.clear).not.toHaveBeenCalled();
  fake.clearAll.mockResolvedValue({ removed: -1 });
  await expect(clearAllCallEvents()).rejects.toThrow();
});
it("uses one plain-language lookup vocabulary across summary surfaces", () => {
  expect(callEventHeadline(event)).toBe("Codex 查到 1 条相关内容");
  expect(callDetailHeadline({ ...event, memory_count: 2 })).toBe(
    "Codex 查到 3 条相关内容",
  );
  expect(callDetailHeadline({ ...event, state: "empty" })).toBe(
    "Codex 没有查到相关记忆",
  );
  expect(callDetailHeadline({ ...event, state: "timed_out" })).toBe(
    "Codex 这次没来得及查到记忆",
  );
  expect(callDetailHeadline({ ...event, state: "unavailable" })).toBe(
    "Codex 暂时无法查记忆",
  );
  expect(callDetailHeadline({ ...event, state: "denied" })).toBe(
    "Codex 未获准查记忆",
  );
  expect(callRowHeadline(event)).toBe("Codex 查到 1 条相关内容");
  expect(callRowHeadline({ ...event, state: "unavailable" })).toBe(
    "Codex 暂时无法查记忆",
  );
  expect(callRowDetail({ ...event, state: "timed_out" })).toBe(
    "后台正在整理记忆，查询超时，这次回答没有带上记忆",
  );
});
it("keeps delivery and adoption separate for call history", async () => {
  fake.list.mockResolvedValue({
    ...page,
    items: [
      {
        ...event,
        delivery: "delivered",
        host_session_id: "session-1",
        injection_elapsed_ms: 12.5,
      },
    ],
  });
  const result = await getCallHistory();
  expect(result.items[0]).toMatchObject({
    delivery: "delivered",
    adoption: "unknown",
    host_session_id: "session-1",
    injection_elapsed_ms: 12.5,
  });
});
it.each(
  [
    [
      {
        ...event.references[0],
        id: "wrong",
        available: true,
        title: "secret",
        reason: null,
      },
    ],
    [
      {
        ...event.references[0],
        available: false,
        title: "secret",
        reason: "unavailable",
      },
    ],
    [
      {
        ...event.references[0],
        available: true,
        title: "x".repeat(121),
        reason: null,
      },
    ],
    [],
  ].map((refs) => ({ refs })),
)("rejects unrelated or unauthorized reference titles %#", async ({ refs }) => {
  fake.references.mockResolvedValue(refs);
  await expect(getCallReferences(event)).rejects.toThrow();
});
it("names a direct (host_id null) call in the locale it receives", () => {
  const direct: CallEvent = { ...event, host_id: null, state: "empty" };
  expect(callHostName(null, "es")).toBe("Consulta directa (diagnóstico)");
  expect(callHostName(null)).toBe("直接调用（诊断）");
  expect(callEventHeadline(direct, undefined, "es")).toBe(
    "Consulta directa (diagnóstico) no encontró memorias relacionadas",
  );
  expect(callDetailHeadline(direct, undefined, "es")).toBe(
    "Consulta directa (diagnóstico) no encontró memorias relacionadas",
  );
  const delivered: CallEvent = {
    ...direct,
    state: "grounded",
    delivery: "delivered",
  };
  expect(callRowDetail(delivered, "es")).toBe(
    "Entregado a Consulta directa (diagnóstico)",
  );
  for (const text of [
    callEventHeadline(direct, undefined, "es"),
    callRowDetail(delivered, "es"),
  ])
    expect(text).not.toMatch(/[\u4e00-\u9fff。（）]/);
  // Unknown host ids are names, never catalog keys.
  expect(callHostName("toString", "es")).toBe("toString");
});
