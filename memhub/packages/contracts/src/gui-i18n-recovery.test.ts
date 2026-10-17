import { expect, it } from "vitest";
import { translate } from "./gui-i18n.js";
import {
  recoveryChineseMessages,
  recoverySpanishMessages,
} from "./gui-i18n-recovery.js";

it("covers every recovery key in Spanish with identical interpolation parameters", () => {
  const params = (message: string) =>
    [...message.matchAll(/\{([A-Za-z_][A-Za-z0-9_]*)\}/g)]
      .map((match) => match[1])
      .sort();
  for (const key of Object.keys(
    recoveryChineseMessages,
  ) as (keyof typeof recoveryChineseMessages)[]) {
    expect(recoverySpanishMessages[key], key).toBeTruthy();
    expect(params(recoverySpanishMessages[key]), key).toEqual(
      params(recoveryChineseMessages[key]),
    );
    expect(translate("es", key), key).toBe(recoverySpanishMessages[key]);
    expect(translate("zh-CN", key), key).toBe(recoveryChineseMessages[key]);
  }
});
