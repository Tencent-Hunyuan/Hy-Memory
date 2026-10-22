import { beforeEach, describe, expect, it, vi } from "vitest";

const { get } = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("axios", () => ({
  default: {
    create: () => ({
      get,
      interceptors: { request: { use: vi.fn() } },
    }),
  },
}));

import { getMemories, getMemoryLibrary, getSdkMemoryLibrary } from "./api";

describe("paged memory library client", () => {
  beforeEach(() => vi.clearAllMocks());

  it("reads an explicit page without changing legacy array semantics", async () => {
    const page = { items: [], total: 1607, offset: 1500, limit: 100 };
    get.mockResolvedValueOnce({ data: page });
    expect(await getMemoryLibrary({ offset: 1500, limit: 100 })).toBe(page);
    expect(get).toHaveBeenCalledWith("/api/v1/memory-library", {
      params: { offset: 1500, limit: 100 },
      signal: undefined,
    });
    get.mockResolvedValueOnce({ data: [] });
    expect(await getMemories()).toEqual([]);
    expect(get.mock.calls[1][0]).toBe("/api/v1/memories");
  });

  it("passes literal query, source and status without client-side filtering", async () => {
    const params = {
      query: "100%_\\ exact",
      source: "workbuddy",
      status: "deleted" as const,
      offset: 0,
      limit: 50,
    };
    get.mockResolvedValueOnce({
      data: { items: [], total: 0, offset: 0, limit: 50 },
    });
    await getMemoryLibrary(params);
    expect(get).toHaveBeenCalledWith("/api/v1/memory-library", {
      params,
      signal: undefined,
    });
  });

  it("forwards cancellation for inactive or superseded views", async () => {
    const controller = new AbortController();
    get.mockResolvedValueOnce({
      data: { items: [], total: 0, offset: 0, limit: 100 },
    });
    await getMemoryLibrary({}, controller.signal);
    expect(get.mock.calls[0][1].signal).toBe(controller.signal);
  });

  it("does not turn a failed query into an empty library or call the old endpoint", async () => {
    const error = new Error("memory_library_unavailable");
    get.mockRejectedValueOnce(error);
    await expect(getMemoryLibrary()).rejects.toBe(error);
    expect(get).toHaveBeenCalledTimes(1);
  });

  it("sends SDK lifecycle filters as repeated status parameters", async () => {
    const page = { memories: [], total: 0, offset: 0, limit: 25 };
    get.mockResolvedValueOnce({ data: page });
    expect(
      await getSdkMemoryLibrary({
        statuses: ["active", "superseded"],
        offset: 0,
        limit: 25,
      }),
    ).toBe(page);
    expect(get.mock.calls[0][0]).toBe("/api/v2/memory-library");
    const params = get.mock.calls[0][1].params as URLSearchParams;
    expect(params.getAll("statuses")).toEqual(["active", "superseded"]);
    expect(params.get("limit")).toBe("25");
  });
});
