import { describe, expect, it } from "vitest";
import {
  isMessageKey,
  type MessageKey,
  translate,
  translateCount,
} from "./gui-i18n.js";
import {
  applicationsChineseMessages,
  applicationsSpanishMessages,
} from "./gui-i18n-applications.js";
import {
  callsChineseMessages,
  callsSpanishMessages,
} from "./gui-i18n-calls.js";
import {
  currentChineseMessages,
  currentEnglishMessages,
  currentSpanishMessages,
} from "./gui-i18n-current.js";
import { englishMessages } from "./gui-i18n-english.js";
import {
  memoryChineseMessages,
  memorySpanishMessages,
} from "./gui-i18n-memory.js";
import {
  chineseMessages,
  guiCatalogs,
  mergeCatalogs,
  spanishMessages,
} from "./gui-i18n-messages.js";

const placeholders = (text: string) =>
  [...text.matchAll(/\{([A-Za-z_][A-Za-z0-9_]*)\}/g)].map((m) => m[1]).sort();

import {
  workbenchChineseMessages,
  workbenchSpanishMessages,
} from "./gui-i18n-workbench.js";

const migrated = [
  ["current", currentChineseMessages, currentSpanishMessages],
  ["workbench", workbenchChineseMessages, workbenchSpanishMessages],
  ["applications", applicationsChineseMessages, applicationsSpanishMessages],
  ["memory", memoryChineseMessages, memorySpanishMessages],
  ["calls", callsChineseMessages, callsSpanishMessages],
] as const;

// The parent integration merges this shard into gui-i18n-english.ts. Keep the
// shard independently type-safe without mutating that shared file here.
const completeEnglishMessages = {
  ...englishMessages,
  ...currentEnglishMessages,
};

describe("mergeCatalogs", () => {
  it("throws when two catalogs give one key divergent texts", () => {
    expect(() =>
      mergeCatalogs([
        { name: "a", chinese: { k: "甲" }, spanish: { k: "A" } },
        { name: "b", chinese: { k: "甲" }, spanish: { k: "B" } },
      ]),
    ).toThrow(/"k" differs between a and b/);
    expect(() =>
      mergeCatalogs([
        { name: "a", chinese: { k: "甲" }, spanish: {} },
        { name: "b", chinese: { k: "乙" }, spanish: {} },
      ]),
    ).toThrow(/differs/);
  });

  it("accepts an identical key repeated across catalogs", () => {
    const merged = mergeCatalogs([
      { name: "a", chinese: { k: "甲", x: "一" }, spanish: { k: "A" } },
      { name: "b", chinese: { k: "甲" }, spanish: { k: "A" } },
    ]);
    expect(merged).toEqual({
      chinese: { k: "甲", x: "一" },
      spanish: { k: "A" },
    });
  });

  it("merges the shipped catalogs without collisions", () => {
    expect(() => mergeCatalogs(guiCatalogs)).not.toThrow();
  });
});

describe.each(migrated)("%s catalog", (_name, chinese, spanish) => {
  it("translates every key with the same parameters", () => {
    expect(Object.keys(spanish).sort()).toEqual(Object.keys(chinese).sort());
    for (const [key, text] of Object.entries(chinese)) {
      const translated = placeholders(spanish[key as keyof typeof spanish]);
      const expected = placeholders(text);
      if (key.endsWith(".one")) {
        // A Spanish singular may drop {count} ("Ver la aplicación"), never invent one.
        for (const name of translated) expect(expected, key).toContain(name);
      } else {
        expect(translated, key).toEqual(expected);
      }
    }
  });

  it("is merged verbatim into the global catalog", () => {
    for (const [key, text] of Object.entries(chinese)) {
      expect(chineseMessages[key as MessageKey]).toBe(text);
      expect(spanishMessages[key as MessageKey]).toBe(
        spanish[key as keyof typeof spanish],
      );
    }
  });

  it("has no Chinese punctuation in Spanish", () => {
    for (const [key, text] of Object.entries(spanish))
      expect(text, key).not.toMatch(/[。，、；：！？（）【】“”]/);
  });
});

describe("isMessageKey", () => {
  it("accepts real keys", () => {
    expect(isMessageKey("settings")).toBe(true);
    expect(isMessageKey("calls.directHost")).toBe(true);
  });

  it.each([
    "toString",
    "__proto__",
    "constructor",
    "hasOwnProperty",
    "",
    "apps.",
    "memory:",
    "/Users/synthetic/.cursor",
    "E_SYNTHETIC_CODE",
    "一段导入的对话",
  ])("rejects %j", (value) => {
    expect(isMessageKey(value)).toBe(false);
  });
});

describe("translateCount", () => {
  it("uses the Spanish singular only for exactly one", () => {
    const key = "calls.headline.found";
    const at = (count: number) =>
      translateCount("es", key, count, { host: "Cursor", count });
    expect(at(0)).toBe("Cursor encontró 0 contenidos relacionados");
    expect(at(1)).toBe("Cursor encontró 1 contenido relacionado");
    expect(at(2)).toBe("Cursor encontró 2 contenidos relacionados");
    expect(at(25)).toBe("Cursor encontró 25 contenidos relacionados");
  });

  it("keeps the Chinese wording for every count", () => {
    for (const count of [0, 1, 3])
      expect(
        translateCount("zh-CN", "calls.headline.found", count, {
          host: "Cursor",
          count,
        }),
      ).toBe(`Cursor 查到 ${count} 条相关内容`);
  });

  it("gives every singular a Chinese twin identical to its plural", () => {
    for (const [key, text] of Object.entries(chineseMessages)) {
      if (!key.endsWith(".one")) continue;
      expect(text, key).toBe(chineseMessages[key.slice(0, -4) as MessageKey]);
    }
    expect(translateCount("es", "debugCount", 1, { count: 1 })).toBe(
      "1 registro en este inicio",
    );
    expect(
      translateCount("es", "importUserProgress", 1, { count: 1 }),
    ).toContain("1 conversación importada");
  });

  it("falls back to the plain key when no singular exists", () => {
    expect(translateCount("es", "clock.today", 1, { clock: "09:05" })).toBe(
      "Hoy 09:05",
    );
  });
});

describe("translate interpolation", () => {
  it("replaces once and never re-reads substituted values", () => {
    expect(
      translate("es", "calls.detail.delivered", { host: "{host} $& $1" }),
    ).toBe("Entregado a {host} $& $1");
  });

  it("leaves missing and inherited parameters untouched", () => {
    expect(translate("es", "calls.detail.delivered", {})).toBe(
      "Entregado a {host}",
    );
    const inherited = Object.create({ host: "Inherited" }) as Record<
      string,
      string
    >;
    expect(translate("es", "calls.detail.delivered", inherited)).toBe(
      "Entregado a {host}",
    );
  });
});

describe("English catalog", () => {
  it("covers every shipped key with matching placeholders", () => {
    expect(Object.keys(completeEnglishMessages).sort()).toEqual(
      Object.keys(chineseMessages).sort(),
    );
    for (const [key, text] of Object.entries(chineseMessages)) {
      expect(
        placeholders(completeEnglishMessages[key as MessageKey]),
        key,
      ).toEqual(placeholders(text));
      expect(completeEnglishMessages[key as MessageKey], key).not.toMatch(
        /[一-鿿]/,
      );
    }
  });

  it("uses CLDR English singular and plural forms", () => {
    expect(
      translateCount("en", "calls.headline.found", 1, {
        host: "Cursor",
        count: 1,
      }),
    ).toContain("1");
    expect(
      translateCount("en", "calls.headline.found", 2, {
        host: "Cursor",
        count: 2,
      }),
    ).toContain("2");
  });
});

describe("Spanish catalog", () => {
  it("covers every shipped key with matching placeholders", () => {
    expect(Object.keys(spanishMessages).sort()).toEqual(
      Object.keys(chineseMessages).sort(),
    );
    for (const [key, text] of Object.entries(chineseMessages)) {
      const spanish = spanishMessages[key as MessageKey];
      expect(spanish, key).toBeDefined();
      if (spanish === undefined) continue;
      const translated = placeholders(spanish);
      const expected = placeholders(text);
      if (key !== "connectionFallback.promptTemplate")
        expect(spanish, key).not.toMatch(/[一-鿿]/);
      if (key.endsWith(".one")) {
        for (const name of translated) expect(expected, key).toContain(name);
      } else {
        expect(translated, key).toEqual(expected);
      }
    }
  });
});
