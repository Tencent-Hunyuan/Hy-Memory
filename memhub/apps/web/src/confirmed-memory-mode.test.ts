import { beforeEach, expect, it } from "vitest";
import {
  commitConfirmedMemoryMode,
  confirmedMemoryModeSnapshot,
  issueConfirmedModeTicket,
  resetConfirmedMemoryModeForTests,
} from "./confirmed-memory-mode";

beforeEach(resetConfirmedMemoryModeForTests);

it("does not let an older GET overwrite a confirmed PUT", () => {
  const oldGet = issueConfirmedModeTicket("settings");
  const put = issueConfirmedModeTicket("settings");

  expect(commitConfirmedMemoryMode("lite", put)).toBe(true);
  expect(commitConfirmedMemoryMode("pro", oldGet)).toBe(false);
  expect(confirmedMemoryModeSnapshot().mode).toBe("lite");
});

it("keeps projection capabilities with the accepted mode", () => {
  const ticket = issueConfirmedModeTicket("home");
  commitConfirmedMemoryMode("ultra", ticket, {
    content_plane: "memory",
    generation_required: true,
    graph_enabled: true,
    evolve_available: true,
  });
  expect(confirmedMemoryModeSnapshot()).toMatchObject({
    mode: "ultra",
    capabilities: { graph_enabled: true, evolve_available: true },
  });
});
