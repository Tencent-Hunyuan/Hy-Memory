import { translate } from "@one-memory/contracts";
import { describe, expect, it } from "vitest";

import { modelErrorText, modelProfileDisplayText } from "./model-error-text";

describe("模型配置的报错要说人话", () => {
  it("把后端的代号翻成一句能照着做的话", () => {
    // 真实发生过：粘完 key 点「设为当前模型」，界面上出现的就是这串英文。
    const text = modelErrorText("model_profile_probe_required");
    expect(text).not.toContain("model_profile_probe_required");
    expect(text).toContain("测");
  });

  it("每个已知代号都有对应的话，且没有一句是代号原文", () => {
    const codes = [
      "model_profile_probe_required",
      "model_provider_not_supported",
      "embedding_model_required",
      "model_profile_not_found",
      "model_profile_conflict",
    ];
    for (const code of codes) {
      const text = modelErrorText(code);
      expect(text, code).not.toBe(code);
      expect(text, code).not.toContain("_");
    }
  });

  it("只翻译本地固定提示，不翻译外部错误和错误码", () => {
    const local = "创建结果尚未确认，请核对已保存结果，不要重复提交。";
    expect(
      modelProfileDisplayText(local, (key) => translate("zh-CN", key)),
    ).toBe(local);
    expect(modelProfileDisplayText(local, (key) => translate("en", key))).toBe(
      "The creation result is unconfirmed. Review saved results; do not submit again.",
    );
    for (const raw of [
      "provider failed: bad-model",
      "model_profile_conflict",
    ]) {
      expect(modelProfileDisplayText(raw, (key) => translate("en", key))).toBe(
        raw,
      );
    }
  });

  it("不认识的代号照原样带出来，不许吞掉", () => {
    // 正向对照：没有这条，上一条可以被「一律回一句『出错了』」骗过 ——
    // 那样每个代号都不等于原文，测试全绿，而排查线索全丢了。
    const text = modelErrorText("some_code_nobody_mapped_yet");
    expect(text).toContain("some_code_nobody_mapped_yet");
  });
});
