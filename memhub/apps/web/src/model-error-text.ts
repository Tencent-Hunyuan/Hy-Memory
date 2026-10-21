import type { UiMessage } from "@one-memory/contracts";

/** 后端的错误代号 → 一句能照着做的话。
 *
 * 之前这一层不存在，`use-model-profiles` 直接把代号返回给界面，于是粘完 key
 * 点「设为当前模型」时，屏幕上出现的是 `model_profile_probe_required`。
 * 代号是给我们排查用的，不是给读它的人用的。
 *
 * 认不出来的代号原样带出去，不换成一句笼统的「出错了」：那样虽然屏幕上不再
 * 出现下划线，但排查线索也一起没了，而下一个人只能回来重现一次。
 */
const TEXT: Record<string, UiMessage> = {
  model_profile_probe_required:
    "这张卡还没测过。点一次「设为当前模型」会先自动测，如果还是不行，说明端点、key 或模型名有一处不对。",
  model_provider_not_supported: "这类模型接口暂时用不了，换一张卡。",
  embedding_model_required: "这张卡缺了向量化模型，补上才能启用。",
  model_profile_not_found: "这张卡已经不在了，可能刚被删掉。刷新一下。",
  model_profile_conflict:
    "这张卡刚被改过。刷新一下再试，免得覆盖掉别处的改动。",
};

export function modelErrorText(code: string): string {
  return TEXT[code] ?? `操作没成功：${code}`;
}

// 只翻译本地模型配置 hook 的固定提示；外部服务原文及错误码保持不变。
const LOCAL_MODEL_PROFILE_MESSAGES = new Set<string>([
  "操作已完成，但配置列表暂未刷新。已保留操作回执，请刷新核对，不要重复保存。",
  "暂时无法刷新模型配置，已确认的配置仍保留。请重试刷新。",
  "请先核对上次模型测试结果，暂不修改或启用配置。",
  "未找到上次创建记录。确认放弃旧记录后可重新配置；系统不会自动重试。",
  "创建结果仍待核对，不会重复创建。请稍后再次核对。",
  "本地恢复记录已变化，仍保持锁定。请重新打开后核对。",
  "已发现模型配置，旧创建记录仍保留。请先核对已有配置。",
  "旧创建记录尚未安全解除，配置仍保持锁定。请重新核对。",
  "配置未保存，请检查模型地址和参数后重试；已填写内容保留。",
  "创建结果尚未确认，请核对已保存结果，不要重复提交。",
  "模型配置已变化，旧测试结果不会用于启用，请重新核对配置。",
  "暂时无法核对测试服务，本次未发送测试，请恢复连接后重试。",
  "另一个模型的测试结果尚未确认，请先在该模型上核对测试结果。",
  "测试结果尚未确认。再次点击测试连接将核对原操作，不会重复发送测试。",
  "请先点击测试连接，通过后再设为当前模型。",
  "这是当前正在使用的模型，请先切换到别的模型再删除。",
  "模型配置操作未成功，请核对输入后重试。",
  ...Object.values(TEXT),
] satisfies UiMessage[]);

export function modelProfileDisplayText(
  value: string,
  t: (key: UiMessage) => string,
): string {
  return LOCAL_MODEL_PROFILE_MESSAGES.has(value)
    ? t(value as UiMessage)
    : value;
}

export function memoryWriteErrorText(
  code: string | null | undefined,
): UiMessage | null {
  if (code?.startsWith("model_content_")) {
    return "原话已保存在本机；当前内容、模型接收方或授权版本尚未通过核对，未继续整理。";
  }
  if (code === "hy_memory_import_no_readable_output") {
    return "HY 未返回可核对的已有记忆，未创建替代内容；原始记录已保留，可重试处理。";
  }
  if (code === "hy_memory_write_verification_historical_support_only") {
    return "历史引用尚未绑定到原始来源，当前批次未完成，原话已保留。";
  }
  if (code?.startsWith("hy_memory_write_verification_")) {
    return "记忆内容未通过来源与作者核验，当前批次未完成，原话已保留。";
  }
  return null;
}
