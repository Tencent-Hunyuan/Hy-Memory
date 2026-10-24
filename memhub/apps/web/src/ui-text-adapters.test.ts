import type { SourceResponse } from "@one-memory/contracts";
import { isMessageKey, translate } from "@one-memory/i18n";
import { describe, expect, it } from "vitest";

import { applicationsText } from "./applications-language";
import { memoryText } from "./memory-language";
import {
  blockReasonLabel,
  extractionStateLabel,
  memoryHistoryActionLabel,
} from "./memory-processing-labels";
import { sourceAuthorizationNote } from "./source-state";

const unknown = [
  "一段导入的对话内容",
  "/Users/synthetic/.cursor/projects",
  "E_SYNTHETIC_CODE",
  "toString",
  "__proto__",
  "",
];

describe("presentation adapters", () => {
  it.each(unknown)("memoryText returns unknown text %j untouched", (value) => {
    expect(memoryText("es", value)).toBe(value);
    expect(memoryText("en", value)).toBe(value);
    expect(memoryText("zh-CN", value)).toBe(value);
  });

  it.each(unknown)(
    "applicationsText returns an unknown key %j untouched",
    (value) => {
      // Simulates a key that drifted out of the catalog at runtime.
      const key = value as Parameters<typeof applicationsText>[1];
      expect(applicationsText("es", key)).toBe(value);
      expect(applicationsText("en", key)).toBe(value);
      expect(applicationsText("zh-CN", key)).toBe(value);
    },
  );

  it("still translates known interface copy", () => {
    expect(applicationsText("es", "viewAll", { count: 3 })).toBe(
      "Ver las 3 aplicaciones",
    );
    expect(applicationsText("es", "viewAll", { count: 1 })).toBe(
      "Ver la aplicación",
    );
    expect(applicationsText("zh-CN", "viewAll", { count: 1 })).toBe(
      "查看全部 1 个应用",
    );
    expect(applicationsText("en", "viewAll", { count: 3 })).not.toMatch(
      /[一-鿿]/,
    );
  });

  // Labels and literals that screens now route through `text()`: a missing
  // catalog entry would silently stay Chinese in the Spanish interface.
  it.each([
    ...[
      "waiting_for_model",
      "failed",
      "model_profile_superseded",
      "ingest_policy_superseded",
      "unrecognised_reason",
    ].map(blockReasonLabel),
    ...["extracted", "outside_window", "queued"].map(extractionStateLabel),
    ...[
      "legacy",
      "create",
      "correct",
      "set_scopes",
      "undo",
      "hy_reconcile",
      "hy_supersede",
      "delete",
      "unrecognised_action",
    ].map(memoryHistoryActionLabel),
    "向记忆提问",
    "询问全部已授权可用记忆…",
    "发送问题",
    "保存",
    "重试",
    "删除",
    "对话",
    "修改已保存，但读取最新列表失败。已停止使用旧回答，请重新读取。",
    "纠正已保存，但读取最新列表失败。已停止使用旧回答，请重新读取。",
    "未收到操作结果，请重试。",
    "批量处理结果已保留，但列表刷新失败，请重新读取记忆库。",
    "批量处理结果已保留，但列表刷新失败，稍后会自动重新读取。",
    "召回范围已保存，但列表刷新失败。请稍后重新打开记忆页。",
    "重新整理可能再次调用模型。只会重跑结果不确定的对话，是否继续？",
    "原始对话暂时无法读取",
    "没有匹配的原始对话",
    "还没有导入的原始对话",
  ])("memoryText translates %j and keeps the Chinese as is", (label) => {
    expect(memoryText("zh-CN", label)).toBe(label);
    const spanish = memoryText("es", label);
    const english = memoryText("en", label);
    expect(spanish).not.toBe(label);
    expect(spanish).not.toMatch(/[一-鿿]/);
    expect(english).not.toBe(label);
    expect(english).not.toMatch(/[一-鿿]/);
  });

  it("words an action as a verb inside sentences and as a noun in history", () => {
    const action = (locale: "zh-CN" | "en" | "es") =>
      memoryText(locale, "删除（操作）");
    expect(action("zh-CN")).toBe("删除");
    expect(
      memoryText("zh-CN", "重试{action}", { action: action("zh-CN") }),
    ).toBe("重试删除");
    expect(memoryText("es", "重试{action}", { action: action("es") })).toBe(
      "Volver a eliminar",
    );
    expect(
      memoryText("es", "未能{action}：{error}", {
        action: action("es"),
        error: "E",
      }),
    ).toBe("No se pudo eliminar: E");
    expect(memoryText("zh-CN", "删除（记录）")).toBe("删除");
    expect(memoryText("es", "删除（记录）")).toBe("Eliminación");
    expect(memoryText("es", "删除")).toBe("Eliminar");
    expect(memoryText("en", "删除（记录）")).toBe("Deletion");
    expect(memoryText("en", "删除")).toBe("Delete");
  });
});

describe("source authorization notes", () => {
  const states = [
    "missing_grant",
    "revoked_grant",
    "source_identity_changed",
    "source_root_unavailable",
    "candidate_changed",
  ];
  it.each(states)("translates the %s note and its sync follow-up", (state) => {
    const note = sourceAuthorizationNote({
      authorization_state: state,
    } as unknown as SourceResponse);
    expect(note).not.toBeNull();
    // The sync rejection appends this follow-up to the same note.
    for (const message of [note as string, `${note}完成后再开启持续同步。`]) {
      expect(isMessageKey(message), message).toBe(true);
      if (!isMessageKey(message)) continue;
      expect(translate("zh-CN", message)).toBe(message);
      expect(translate("es", message)).not.toMatch(/[一-鿿]/);
      expect(translate("en", message)).not.toMatch(/[一-鿿]/);
    }
  });
});
