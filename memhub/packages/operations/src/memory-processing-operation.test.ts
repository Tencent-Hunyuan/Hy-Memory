import { describe, expect, it } from "vitest";
import {
  MemoryProcessingInputError,
  normalizeManualExtractEvidence,
  parseExtractionSettingsReceipt,
  parseGraphWidthDecisionReceipt,
  parseManualExtractReceipt,
} from "./memory-processing-operation";

const EVIDENCE = "11111111-2222-4333-8444-555555555555";

describe("extraction window receipt", () => {
  it("accepts a body echoing the requested preset", () => {
    expect(
      parseExtractionSettingsReceipt({ window_preset: "6m" }, "6m"),
    ).toEqual({ window_preset: "6m" });
  });

  // A receipt for a different preset than the one requested means the
  // response belongs to another write.
  it("rejects a preset that is not the one requested", () => {
    expect(
      parseExtractionSettingsReceipt({ window_preset: "all" }, "6m"),
    ).toBeNull();
  });

  it("rejects a malformed body", () => {
    for (const bad of [null, [], "6m", {}, { window_preset: "2y" }]) {
      expect(parseExtractionSettingsReceipt(bad, "6m")).toBeNull();
    }
  });
});

describe("manual extract receipt", () => {
  it("accepts three integer counters", () => {
    expect(
      parseManualExtractReceipt({
        promoted: 2,
        batches_planned: 3,
        batches_current: 1,
      }),
    ).toEqual({ promoted: 2, batches_planned: 3, batches_current: 1 });
  });

  // A missing counter must not be read as 0; the CLI requires all three.
  it("rejects a missing or non-integer counter", () => {
    const base = { promoted: 0, batches_planned: 0, batches_current: 0 };
    const { batches_current, ...without } = base;
    expect(parseManualExtractReceipt(without)).toBeNull();
    for (const bad of [
      { ...base, promoted: undefined },
      { ...base, promoted: 1.5 },
      { ...base, promoted: "1" },
      { ...base, batches_current: Infinity },
    ]) {
      expect(parseManualExtractReceipt(bad)).toBeNull();
    }
  });

  it("rejects a body that is not an object", () => {
    for (const bad of [null, [], "ok", 3]) {
      expect(parseManualExtractReceipt(bad)).toBeNull();
    }
  });
});

describe("manual extract evidence list", () => {
  it("deduplicates nothing: duplicates are rejected, like the CLI plan", () => {
    expect(normalizeManualExtractEvidence([])).toEqual([]);
    expect(normalizeManualExtractEvidence([EVIDENCE])).toEqual([EVIDENCE]);
    expect(() => normalizeManualExtractEvidence([EVIDENCE, EVIDENCE])).toThrow(
      MemoryProcessingInputError,
    );
  });

  it("rejects a non-UUID id", () => {
    for (const bad of [["evidence"], [""], ["1111"]]) {
      expect(() => normalizeManualExtractEvidence(bad)).toThrow(
        "manual_extract_evidence_ids_invalid",
      );
    }
  });
});

describe("graph width decision receipt", () => {
  it("accepts a receipt carrying the mismatch key", () => {
    expect(
      parseGraphWidthDecisionReceipt({
        archived_path: null,
        exported_path: null,
        mismatch: null,
      }),
    ).toEqual({ archived_path: null, exported_path: null, mismatch: null });
  });

  it("coerces absent or non-string paths to null, like the CLI", () => {
    expect(
      parseGraphWidthDecisionReceipt({
        archived_path: 5,
        exported_path: "/tmp/export.json",
        mismatch: null,
      }),
    ).toEqual({
      archived_path: null,
      exported_path: "/tmp/export.json",
      mismatch: null,
    });
  });

  // This is the truthfulness case: without the key `GraphWidthPrompt` would
  // treat the decision as resolved and close, hiding the unresolved mismatch.
  it("rejects a body with no mismatch key", () => {
    expect(
      parseGraphWidthDecisionReceipt({
        archived_path: null,
        exported_path: null,
      }),
    ).toBeNull();
  });

  it("rejects a body that is not an object", () => {
    for (const bad of [null, [], "resolved", 1]) {
      expect(parseGraphWidthDecisionReceipt(bad)).toBeNull();
    }
  });
});
