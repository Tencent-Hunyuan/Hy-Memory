import { beforeEach, expect, it, vi } from "vitest";

const { get, post } = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
}));
vi.mock("axios", () => ({
  default: {
    create: () => ({
      get,
      post,
      interceptors: { request: { use: vi.fn() } },
    }),
  },
}));

import { getSdkRaw, getSdkRawGraph, searchSdkRaw } from "./api";

beforeEach(() => vi.clearAllMocks());

it("lists raw conversations for the selected application", async () => {
  get.mockResolvedValue({
    data: { items: [], total: 0, offset: 0, limit: 20 },
  });
  const signal = new AbortController().signal;

  await getSdkRaw({ source: "cursor", offset: 20, limit: 20, signal });

  expect(get).toHaveBeenCalledWith("/api/v2/raw", {
    params: {
      source: "cursor",
      session_id: undefined,
      offset: 20,
      limit: 20,
      unit: "turn",
      paging: undefined,
      cursor: undefined,
    },
    signal,
  });
});

it("keeps the application filter when searching raw conversations", async () => {
  post.mockResolvedValue({ data: { state: "empty", items: [] } });
  const signal = new AbortController().signal;

  await searchSdkRaw("release plan", { source: "claude-code", signal });

  expect(post).toHaveBeenCalledWith(
    "/api/v2/context/search",
    {
      query: "release plan",
      kinds: ["raw"],
      include_sources: ["claude-code"],
      raw_unit: "turn",
      limit: 20,
      character_budget: 12_000,
    },
    { signal, timeout: 10_000 },
  );
});

it("does not present an unavailable raw search as no matches", async () => {
  post.mockResolvedValue({
    data: { state: "unavailable", items: [] },
  });

  await expect(searchSdkRaw("3d")).rejects.toThrow("raw_search_unavailable");
});

it("requests one Pro-sized raw graph page", async () => {
  get.mockResolvedValue({
    data: {
      user_id: "local",
      items: [],
      total: 1_600,
      offset: 0,
      limit: 300,
      has_more: true,
    },
  });
  const signal = new AbortController().signal;

  await getSdkRawGraph({
    source: "cursor",
    offset: 0,
    limit: 300,
    signal,
  });

  expect(get).toHaveBeenCalledWith("/api/v2/raw/graph", {
    params: {
      source: "cursor",
      offset: 0,
      limit: 300,
    },
    signal,
  });
});
