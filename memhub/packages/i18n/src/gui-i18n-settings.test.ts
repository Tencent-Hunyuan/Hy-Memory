import { expect, it } from "vitest";
import { translate } from "./gui-i18n.js";
import {
  settingsChineseMessages,
  settingsSpanishMessages,
} from "./gui-i18n-settings.js";

it("provides Spanish settings copy with matching interpolation parameters", () => {
  for (const key of Object.keys(
    settingsSpanishMessages,
  ) as (keyof typeof settingsSpanishMessages)[]) {
    const params = (value: string) =>
      [...value.matchAll(/\{([A-Za-z_][A-Za-z0-9_]*)\}/g)]
        .map((match) => match[1])
        .sort();
    expect(settingsSpanishMessages[key], key).toBeTruthy();
    expect(params(settingsSpanishMessages[key]), key).toEqual(
      params(settingsChineseMessages[key]),
    );
    expect(translate("es", key), key).not.toMatch(/[\u3400-\u9fff]/);
  }
});
