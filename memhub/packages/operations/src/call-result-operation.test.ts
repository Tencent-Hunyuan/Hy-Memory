import { describe, expect, it } from "vitest";

import { classifyCallResult } from "./call-result-operation.js";

describe("classifyCallResult", () => {
  it.each(["timed_out", "unavailable", "denied", "empty"] as const)(
    "gives failure state %s precedence over returned counts",
    (state) => {
      expect(
        classifyCallResult({
          state,
          tool: "om_recall",
          raw_count: 2,
          memory_count: 3,
        }),
      ).toBe(state);
    },
  );

  it.each([
    [1, 0, "raw-only"],
    [0, 1, "memory-only"],
    [1, 1, "mixed"],
    [0, 0, "unclassified"],
  ] as const)(
    "classifies grounded counts %i/%i as %s",
    (raw_count, memory_count, expected) => {
      expect(
        classifyCallResult({
          state: "grounded",
          tool: "om_conversations",
          raw_count,
          memory_count,
        }),
      ).toBe(expected);
    },
  );

  it("uses the same content-plane classification for partial calls", () => {
    expect(
      classifyCallResult({
        state: "partial",
        tool: "om_ask",
        raw_count: 4,
        memory_count: 2,
      }),
    ).toBe("mixed");
  });

  it("leaves a successful zero-result matters call unclassified", () => {
    expect(
      classifyCallResult({
        state: "grounded",
        tool: "om_matters",
        raw_count: 0,
        memory_count: 0,
      }),
    ).toBe("unclassified");
  });
});
