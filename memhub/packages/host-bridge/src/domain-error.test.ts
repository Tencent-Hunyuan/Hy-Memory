import { describe, expect, it } from "vitest";
import {
  DOMAIN_CODES,
  DomainError,
  diagnosticDomainError,
  domainCodeOf,
  isDomainCode,
  isDomainError,
} from "./domain-error.js";

describe("domain errors", () => {
  it("carries the code the thrower chose", () => {
    const error = new DomainError("grant_required");

    expect(error.code).toBe("grant_required");
    expect(isDomainError(error)).toBe(true);
    expect(domainCodeOf(error)).toBe("grant_required");
  });

  it("keeps a human message out of anything a log would see", () => {
    // The message is what the UI shows, and it is where host paths and user
    // content end up. The code is what a diagnostic keeps.
    const error = new DomainError(
      "host_uninstall_modified_managed_value",
      "WorkBuddy settings.json 中 /Users/someone/.workbuddy 的条目已被改动",
    );

    const diagnostic = diagnosticDomainError(error);
    expect(diagnostic.error_code).toBe("host_uninstall_modified_managed_value");
    expect(JSON.stringify(diagnostic)).not.toContain("someone");
    expect(JSON.stringify(diagnostic)).not.toContain("settings.json");
  });

  it("says nothing rather than guessing when a failure was never classified", () => {
    expect(domainCodeOf(new Error("something entirely unforeseen"))).toBeNull();
    expect(domainCodeOf("not even an error")).toBeNull();
    expect(diagnosticDomainError(new Error("boom")).error_code).toBeUndefined();
  });

  it("refuses to read a code out of free text", () => {
    // A message that merely mentions a code is not a classification: if it
    // were, any string could change how a failure is filed.
    expect(isDomainCode("grant_required")).toBe(true);
    expect(domainCodeOf(new Error("grant_required happened"))).toBeNull();
    expect(domainCodeOf({ code: "grant_required" })).toBe("grant_required");
    expect(domainCodeOf({ code: "not_a_real_code" })).toBeNull();
  });

  it("keeps the allowlist closed", () => {
    for (const code of DOMAIN_CODES) expect(isDomainCode(code)).toBe(true);
    expect(isDomainCode("desktop_action_failed")).toBe(false);
    expect(isDomainCode("")).toBe(false);
    expect(isDomainCode(undefined)).toBe(false);
  });
});
