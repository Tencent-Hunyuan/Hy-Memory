import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clockStamp } from "./clock-stamp";

// Controlled clock and zone: "now" is 2026-09-20 14:30 in Buenos Aires.
const NOW = "2026-09-20T17:30:00Z";
const previousTz = process.env.TZ;

beforeEach(() => {
  process.env.TZ = "America/Argentina/Buenos_Aires";
  vi.useFakeTimers();
  vi.setSystemTime(new Date(NOW));
});
afterEach(() => {
  vi.useRealTimers();
  if (previousTz === undefined) delete process.env.TZ;
  else process.env.TZ = previousTz;
});

describe("clockStamp", () => {
  it.each([null, undefined, "", "not a date"])(
    "reports an unknown time for %j",
    (value) => {
      expect(clockStamp(value)).toBe("时间未知");
      expect(clockStamp(value, "zh-CN")).toBe("时间未知");
      expect(clockStamp(value, "es")).toBe("Hora desconocida");
    },
  );

  it("says today in the requested language, in the local zone", () => {
    // 12:05 UTC is 09:05 local on the same day.
    const value = "2026-09-20T12:05:00Z";
    expect(clockStamp(value, "es")).toBe("Hoy 09:05");
    expect(clockStamp(value, "zh-CN")).toBe("今天 09:05");
  });

  it("defaults to Chinese", () => {
    expect(clockStamp("2026-09-20T12:05:00Z")).toBe("今天 09:05");
  });

  it("uses the local day, not the UTC day", () => {
    // 02:00 UTC on the 20th is still the 19th in Buenos Aires.
    const value = "2026-09-20T02:00:00Z";
    expect(clockStamp(value, "es")).not.toMatch(/^Hoy/);
    expect(clockStamp(value, "zh-CN")).not.toMatch(/^今天/);
    expect(clockStamp(value, "es")).toBe(
      new Intl.DateTimeFormat("es", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      }).format(new Date(value)),
    );
    expect(clockStamp(value, "es")).toContain("19");
  });

  it("names the year of a date in another year", () => {
    expect(clockStamp("2254-01-13T13:54:00Z", "zh-CN")).toContain("2254");
    expect(clockStamp("2025-12-31T12:00:00Z", "es")).toContain("2025");
    expect(clockStamp("2026-08-03T15:07:00Z", "zh-CN")).not.toContain("2026");
  });

  it("formats another date with month and day, without a today word", () => {
    const value = "2026-08-03T15:07:00Z";
    const es = clockStamp(value, "es");
    const zh = clockStamp(value, "zh-CN");
    // Spanish short date is day/month: 3 August. Padding varies across ICU
    // versions, so only the order is pinned.
    expect(es).toMatch(/^0?3\/0?8\b/);
    expect(es).toContain("12:07");
    expect(es).not.toMatch(/Hoy|今天/);
    expect(zh).toContain("12:07");
    expect(zh).not.toMatch(/Hoy|今天/);
    expect(clockStamp(value)).toBe(zh);
  });
});
