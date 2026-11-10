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
import {
  modeChineseMessages,
  modeEnglishMessages,
  modeFrenchMessages,
  modeGermanMessages,
  modeIndonesianMessages,
  modeJapaneseMessages,
  modeKoreanMessages,
  modePortugueseMessages,
  modeRussianMessages,
  modeSpanishMessages,
  modeTraditionalChineseMessages,
} from "./gui-i18n-mode.js";

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
        translateCount("zh-Hans", "calls.headline.found", count, {
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
    expect(translateCount("en", "sdk.raw.total", 1, { count: 1 })).toBe(
      "1 original conversation",
    );
    expect(translateCount("en", "sdk.raw.total", 2, { count: 2 })).toBe(
      "2 original conversations",
    );
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

describe("Mode catalog", () => {
  it("does not leave non-English locales on English fallback copy", () => {
    const locales = [
      modeSpanishMessages,
      modeTraditionalChineseMessages,
      modeJapaneseMessages,
      modeGermanMessages,
      modePortugueseMessages,
      modeFrenchMessages,
      modeKoreanMessages,
      modeRussianMessages,
      modeIndonesianMessages,
    ];
    for (const messages of locales)
      for (const key of Object.keys(modeEnglishMessages) as Array<
        keyof typeof modeEnglishMessages
      >)
        expect(messages[key], key).not.toBe(modeEnglishMessages[key]);
  });

  it("keeps new plan and blocker copy in Traditional Chinese", () => {
    const keys = [
      "新对话会整理成记忆并写入知识图谱。",
      "新对话会由模型整理成记忆。",
      "会调用当前生成模型；自动整合可能产生额外费用。切换本身不会立即整合。",
      "会调用当前生成模型，可能产生服务商费用。",
      "不调用生成模型，不再产生这部分费用。",
      "切换后搜索原始对话。",
      "搜索沉淀记忆，并用知识图谱补充关联。",
      "搜索沉淀后的记忆。",
      "已经处理完的旧对话不会补做；原始对话全部保留。",
      "知识图谱",
      "普通沉淀记忆仍可使用；深度记忆新增的更高层记忆和图谱数据继续保留，但在智能记忆下不显示、不参与召回。原始对话不会删除。",
      "已有记忆会在后台加入知识图谱，期间仍可使用。",
      "每天自动整合会停止，已经生成的数据不会删除。",
      "记忆维护正在进行，完成后才能切换模式。",
      "模式切换影响暂时无法读取，请重试。",
    ] as const;
    for (const key of keys) {
      expect(modeTraditionalChineseMessages[key], key).not.toBe(
        modeChineseMessages[key],
      );
      expect(modeTraditionalChineseMessages[key], key).toMatch(
        /[記憶對話會圖譜費續讀]/,
      );
    }
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
