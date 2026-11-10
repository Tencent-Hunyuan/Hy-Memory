// @vitest-environment jsdom
import { beforeEach, expect, it, vi } from "vitest";

const fake = vi.hoisted(() => ({
  get: vi.fn(),
  put: vi.fn(),
}));
vi.mock("axios", () => ({
  default: {
    create: () => ({
      get: fake.get,
      put: fake.put,
      interceptors: { request: { use: vi.fn() } },
    }),
    isAxiosError: () => false,
  },
}));

import { getHomeProjection, getMemoryMode, setMemoryMode } from "./api";
import {
  confirmedMemoryModeSnapshot,
  resetConfirmedMemoryModeForTests,
} from "./confirmed-memory-mode";

const graph = {
  state: "idle",
  started_at: null,
  finished_at: null,
  users: {},
};

beforeEach(() => {
  resetConfirmedMemoryModeForTests();
  fake.get.mockReset();
  fake.put.mockReset();
});

it("keeps transport reads and writes free of confirmed-mode side effects", async () => {
  fake.get
    .mockResolvedValueOnce({
      data: {
        projection_version: 1,
        user_id: "local",
        effective_mode: "lite",
        capabilities: {
          content_plane: "raw",
          generation_required: false,
          graph_enabled: false,
          evolve_available: false,
        },
        visible_count: 0,
        display_state: "empty",
        latest: null,
        observed_at: "2026-11-04T00:00:00Z",
      },
    })
    .mockResolvedValueOnce({
      data: {
        mode: "pro",
        configured_mode: "pro",
        explicitly_configured: true,
        forced_by_env: false,
        modes: ["lite", "pro", "ultra"],
        graph_build: graph,
      },
    });
  fake.put.mockResolvedValue({
    data: {
      mode: "ultra",
      configured_mode: "ultra",
      explicitly_configured: true,
      forced_by_env: false,
      modes: ["lite", "pro", "ultra"],
      graph_build: graph,
      previous_mode: "pro",
      effects: [],
    },
  });

  await getHomeProjection();
  await getMemoryMode();
  await setMemoryMode("ultra");

  expect(confirmedMemoryModeSnapshot().mode).toBeNull();
});
