import { expect, it } from "vitest";
import { type MessageKey, translate } from "./gui-i18n.js";
import {
  guideChineseMessages,
  guideSpanishMessages,
} from "./gui-i18n-guide.js";
import {
  healthChineseMessages,
  healthSpanishMessages,
} from "./gui-i18n-health.js";

it("covers every health and guide key without accidental Chinese fallback", () => {
  const params = (message: string) =>
    [...message.matchAll(/\{([A-Za-z_][A-Za-z0-9_]*)\}/g)]
      .map((match) => match[1])
      .sort();
  for (const [chinese, spanish] of [
    [healthChineseMessages, healthSpanishMessages],
    [guideChineseMessages, guideSpanishMessages],
  ]) {
    expect(Object.keys(spanish).sort()).toEqual(Object.keys(chinese).sort());
    for (const [key, value] of Object.entries(chinese)) {
      const translated = spanish[key as keyof typeof spanish];
      expect(translated, key).toBeTruthy();
      expect(params(translated), key).toEqual(params(value));
      expect(translate("es", key as MessageKey), key).toBe(translated);
      expect(translate("zh-CN", key as MessageKey), key).toBe(value);
    }
  }
});
