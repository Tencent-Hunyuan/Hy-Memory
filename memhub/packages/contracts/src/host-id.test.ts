import { describe, expect, it } from "vitest";

import { HOST_ID_MAX_LENGTH, HOST_ID_PATTERN, isHostId } from "./index";

/**
 * The grammar is shared by the CLI, the permission layer and the grant store,
 * so the boundary cases are pinned once here. The optional profile segment
 * exists because a host can be configured per profile
 * (`deepseek-harness:desktop`).
 */
const VALID: readonly string[] = [
  "codex",
  "claude-code",
  "pi",
  "a1",
  "9lives", // the CLI has always accepted a digit-leading host id
  "codex-",
  "deepseek-harness:desktop",
  "deepseek-harness:web",
  "a:b",
  "2code:9profile",
  "a".repeat(64), // host segment at its maximum
  `a:${"b".repeat(32)}`, // profile segment at its maximum
  `${"a".repeat(64)}:${"b".repeat(32)}`, // the whole id at its maximum
];

const INVALID: readonly string[] = [
  "",
  " codex",
  "codex ",
  "DeepSeek-Harness:web", // upper case
  "deepseek harness:web", // space
  "-codex", // hyphen-first host
  "deepseek-harness:", // empty profile
  "deepseek-harness:a:b", // more than one profile segment
  ":web",
  "a:",
  "a::b",
  "a:b:c",
  `a${"b".repeat(64)}`, // host segment one over the maximum
  `a:${"b".repeat(33)}`, // profile segment one over the maximum
  "deepseek_harness:web", // underscore
  "deepseek-harness:web\n", // `$` alone would accept this
];

describe("host id grammar", () => {
  it.each(VALID)("accepts %j", (value) => {
    expect(isHostId(value)).toBe(true);
    expect(HOST_ID_PATTERN.test(value)).toBe(true);
    expect(value.length).toBeLessThanOrEqual(HOST_ID_MAX_LENGTH);
  });

  it.each(INVALID)("rejects %j", (value) => {
    expect(isHostId(value)).toBe(false);
    expect(HOST_ID_PATTERN.test(value)).toBe(false);
  });

  it("is a type guard for strings only", () => {
    for (const value of [undefined, null, 7, {}, []]) {
      expect(isHostId(value)).toBe(false);
    }
    expect(isHostId("codex")).toBe(true);
  });
});
