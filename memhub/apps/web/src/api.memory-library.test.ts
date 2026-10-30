import { beforeEach, describe, expect, it, vi } from "vitest";

const { get, put, del } = vi.hoisted(() => ({
  get: vi.fn(),
  put: vi.fn(),
  del: vi.fn(),
}));
vi.mock("axios", () => ({
  default: {
    create: () => ({
      get,
      put,
      delete: del,
      interceptors: { request: { use: vi.fn() } },
    }),
  },
}));

import {
  deleteSdkMemory,
  getHomeSummary,
  getSdkMemory,
  getSdkMemoryLibrary,
  updateSdkMemory,
} from "./api";

describe("paged memory library client", () => {
  beforeEach(() => vi.clearAllMocks());

  it("sends SDK lifecycle filters as repeated status parameters", async () => {
    const page = { memories: [], total: 0, offset: 0, limit: 25 };
    get.mockResolvedValueOnce({ data: page });
    expect(
      await getSdkMemoryLibrary({
        statuses: ["active", "superseded"],
        source: "cursor",
        query: "release",
        offset: 0,
        limit: 25,
      }),
    ).toBe(page);
    expect(get.mock.calls[0][0]).toBe("/api/v2/memory-library");
    const params = get.mock.calls[0][1].params as URLSearchParams;
    expect(params.getAll("statuses")).toEqual(["active", "superseded"]);
    expect(params.get("limit")).toBe("25");
    expect(params.get("source")).toBe("cursor");
    expect(params.get("query")).toBe("release");
  });

  it("pages 20 at a time and sends the search window only with a query", async () => {
    get.mockResolvedValue({
      data: { memories: [], total: 0, offset: 0, limit: 20 },
    });
    await getSdkMemoryLibrary({ query: "deploy", offset: 20, windowId: "w1" });
    let params = get.mock.calls[0][1].params as URLSearchParams;
    expect(params.get("limit")).toBe("20");
    expect(params.get("offset")).toBe("20");
    expect(params.get("window_id")).toBe("w1");

    await getSdkMemoryLibrary({ windowId: "w1" });
    params = get.mock.calls[1][1].params as URLSearchParams;
    expect(params.has("window_id")).toBe(false);
    expect(params.get("limit")).toBe("20");
  });

  it("names an experience's kind and variant on get, update and delete", async () => {
    get.mockResolvedValue({ data: { memory_id: "e1" } });
    put.mockResolvedValue({ data: { memory_id: "e1" } });
    del.mockResolvedValue({});
    const namespace = { kind: "experience", variant: "coding" } as const;

    await getSdkMemory("e1", namespace);
    expect(get).toHaveBeenCalledWith("/api/v2/memories/e1", {
      params: { kind: "experience", variant: "coding" },
    });
    await getSdkMemory("c1");
    expect(get).toHaveBeenLastCalledWith("/api/v2/memories/c1");

    await updateSdkMemory("e1", "new solution", "trace-1", {
      ...namespace,
      task: "new task",
    });
    expect(put).toHaveBeenCalledWith(
      "/api/v2/memories/e1",
      {
        content: "new solution",
        kind: "experience",
        variant: "coding",
        task: "new task",
      },
      { headers: { "X-One-Memory-Trace-Id": "trace-1" } },
    );

    await deleteSdkMemory("e1", "trace-2", namespace);
    expect(del).toHaveBeenCalledWith("/api/v2/memories/e1", {
      headers: { "X-One-Memory-Trace-Id": "trace-2" },
      params: { kind: "experience", variant: "coding" },
    });
    await deleteSdkMemory("c1");
    expect(del).toHaveBeenLastCalledWith("/api/v2/memories/c1", undefined);
  });

  it("forwards cancellation for inactive or superseded views", async () => {
    const controller = new AbortController();
    get.mockResolvedValueOnce({
      data: { memories: [], total: 0, offset: 0, limit: 100 },
    });
    await getSdkMemoryLibrary({}, controller.signal);
    expect(get.mock.calls[0][1].signal).toBe(controller.signal);
  });

  it("does not turn a failed query into an empty library or fall back", async () => {
    const error = new Error("hy_memory_list_failed");
    get.mockRejectedValueOnce(error);
    await expect(getSdkMemoryLibrary()).rejects.toBe(error);
    expect(get).toHaveBeenCalledTimes(1);
  });
});

describe("home summary client", () => {
  beforeEach(() => vi.clearAllMocks());

  it("reads the SDK summary directly, with no authority request first", async () => {
    const summary = {
      schema_version: 2,
      memory_count: 3,
      latest: null,
      observed_at: "2026-10-26T00:00:00Z",
    };
    get.mockResolvedValueOnce({ data: summary });
    expect(await getHomeSummary()).toBe(summary);
    expect(get).toHaveBeenCalledTimes(1);
    expect(get).toHaveBeenCalledWith("/api/v2/home-summary");
  });
});
