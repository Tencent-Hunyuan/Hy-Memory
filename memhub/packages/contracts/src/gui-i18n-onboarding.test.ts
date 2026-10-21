import { expect, it } from "vitest";
import { translate } from "./gui-i18n.js";
import {
  onboardingChineseMessages,
  onboardingSpanishMessages,
} from "./gui-i18n-onboarding.js";

it("provides Spanish copy for every onboarding message with identical interpolation parameters", () => {
  for (const key of Object.keys(
    onboardingChineseMessages,
  ) as (keyof typeof onboardingChineseMessages)[]) {
    const chinese = onboardingChineseMessages[key];
    const spanish = onboardingSpanishMessages[key];
    expect(spanish, key).toBeTruthy();
    expect(
      [...spanish.matchAll(/\{([A-Za-z_][A-Za-z0-9_]*)\}/g)]
        .map((match) => match[1])
        .sort(),
      key,
    ).toEqual(
      [...chinese.matchAll(/\{([A-Za-z_][A-Za-z0-9_]*)\}/g)]
        .map((match) => match[1])
        .sort(),
    );
    expect(translate("zh-CN", key)).toBe(chinese);
    expect(translate("es", key)).toBe(spanish);
  }
});
it("keeps product names, paths and error codes intact during interpolation", () => {
  expect(translate("es", "onboardingImportHistory", { name: "Cursor" })).toBe(
    "Importar el historial de Cursor",
  );
  expect(
    translate("zh-CN", "onboardingImportHistory", { name: "Cursor" }),
  ).toBe("导入 Cursor 历史");
  expect(
    translate("es", "onboardingModelOperationFailed", {
      code: "fixture_error",
    }),
  ).toBe("La operación no se completó: fixture_error");
  expect(
    translate("es", "onboardingConnectionPartial", {
      success: 1,
      failed: 2,
      names: "Cursor, /fixture/source",
    }),
  ).toContain("Cursor, /fixture/source");
});
