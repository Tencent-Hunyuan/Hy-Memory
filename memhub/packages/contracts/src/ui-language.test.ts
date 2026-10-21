import { describe, expect, it } from "vitest";
import {
  formatUiDate,
  formatUiNumber,
  isUiLanguage,
  translate,
} from "./ui-language";

describe("UI language resources", () => {
  it("accepts only supported languages, keeping arbitrary strings out of the UI", () => {
    expect(isUiLanguage("zh-CN")).toBe(true);
    expect(isUiLanguage("en")).toBe(true);
    for (const value of ["en-US", "../../memory", null, {}, "<script>"]) {
      expect(isUiLanguage(value)).toBe(false);
    }
  });

  it("switches copy without changing placeholders or user content", () => {
    expect(translate("zh-CN", "打开 {name}", { name: "MemHub" })).toBe(
      "打开 MemHub",
    );
    expect(translate("en", "打开 {name}", { name: "<user text>" })).toBe(
      "Open <user text>",
    );
    expect(translate("en", "连接 Core…")).toBe("Connecting to Core…");
  });

  it("localizes preset data recipients while preserving endpoint values and the local-only destination", () => {
    const key =
      "生成接收方：{generation}；向量接收方：{embedding}。启用此配置后按上方选择处理历史及未来允许来源。";
    const generation = "https://fixture.example/v1";
    expect(
      translate("zh-CN", key, {
        generation,
        embedding: translate("zh-CN", "本机"),
      }),
    ).toBe(
      "生成接收方：https://fixture.example/v1；向量接收方：本机。启用此配置后按上方选择处理历史及未来允许来源。",
    );
    expect(
      translate("en", key, {
        generation,
        embedding: translate("en", "本机"),
      }),
    ).toBe(
      "Generation endpoint: https://fixture.example/v1; embedding endpoint: On this device. After enabling, process history and future permitted sources according to your selection above.",
    );
    expect(translate("en", "接口地址待核对")).toBe("Endpoint needs review");
  });

  it("formats dates and numbers using the selected language", () => {
    expect(formatUiNumber("en", 1234)).toBe("1,234");
    expect(
      formatUiDate("en", new Date("2026-10-19T00:00:00Z"), {
        timeZone: "UTC",
        month: "short",
        day: "numeric",
      }),
    ).toContain("Oct");
  });
});
