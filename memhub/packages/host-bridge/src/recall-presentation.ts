/** Shared by MCP descriptions and installed Skills; guidance never rewrites receipts. */
export const RECALL_PRESENTATION_GUIDANCE = `
Give one brief, visible result receipt per logical lookup, not per internal step. Follow the host's language and disclosure requirements; use these exact Chinese sentences in Chinese, and equivalent wording in other languages. Select the actual state before choosing wording: denial or unavailability takes precedence over ordinary empty; partial coverage takes precedence over full success.
Search original conversations and memories together. A returned original conversation is usable even when no memory item is returned, because a new conversation may not have been settled into a memory yet. Use only source, time, raw IDs, memory IDs and evidence metadata actually returned by the active schema; never invent legacy evidence or revision fields.
For an ordinary empty lookup, or when a historical question has no returned conversation and no returned memory, say exactly "已完成MemHub的记忆查询。" Then continue with the host application's own context. Do not say that MemHub found nothing, and do not invent memories or claim that all history was searched.
Only when relevant conversations or memories were actually returned without a known coverage limitation, say "成功使用 MemHub 回忆到相关记忆。" Then use relevant evidence and cite only sources actually used. This announces retrieval, not proof that the answer adopted the memories or that every retrieved statement is verified.
For offline, unavailable or failed calls, say "MemHub 暂时不可用，我将先依据当前对话继续。" Do not present a historical answer as verified or dump technical diagnostics unless requested or needed for recovery.
For an actual scope or permission denial, say "本次记忆查询超出当前授权范围，我将先依据当前可用信息继续。" Explain a different reported cause accurately instead of calling every error a permission denial. Only suggest granting access when the reported reason explicitly says permission is missing. Do not retry or bypass a denial.
If retrieval is truncated, timed out, or explicitly degraded but some relevant conversations or memories were returned, say "已从 MemHub 获取部分相关记忆，以下将补充这些信息继续。" Retain any known coverage limitation. Zero-hit partial results must not use the partial-memory receipt; say exactly "已完成MemHub的记忆查询。" An empty lookup is not a service error, but an empty result alone does not prove that every retrieval path completed.
Only announce further retrieval when an authorized, relevant next step is actually available and will be performed. Do not invent a recommendation or promise evidence; if supplementation is unavailable, say "目前只能依据已返回的信息和当前对话继续。" If historical confirmation is still missing, state that it remains unconfirmed. Never infer low confidence from an empty result, an outage or a denial; only report an explicit confidence assessment if one was actually returned.
Never describe an error, denial, partial or unknown result as a completed successful lookup. Keep tool status, reasons, limitations and citations truthful; using another context source does not make the MemHub lookup successful.
`.trim();

export const RECALL_PRESENTATION_GUIDANCE_ZH = `
每次逻辑查询给一句简短可见的结果提示，不逐个内部步骤重复播报。先按真实状态选文案：拒绝/不可用优先于普通空结果，部分结果优先于完整成功；遵守宿主语言与披露要求。
一次查询同时检索原文会话和记忆。原文有结果时就使用，即使没有返回记忆条目；新会话可能还没有沉淀成记忆。只使用当前格式实际返回的来源、时间、raw ID、memory ID 和证据字段，不补造旧格式的 evidence 或 revision。
普通空结果，或原文和记忆都没有返回：“已完成MemHub的记忆查询。”然后继续使用宿主应用自己的上下文。不要说 MemHub 没找到，不编造记忆，不宣称查遍全部历史。
确有相关原文或记忆返回且没有已知覆盖限制：“成功使用 MemHub 回忆到相关记忆。”随后使用相关内容并引用实际采用的来源；这是检索回执，不代表回答已经采用或每条记忆已被核实。
暂时不可用或调用失败：“MemHub 暂时不可用，我将先依据当前对话继续。”不能把历史答案当作已核实；除非用户要求或恢复所需，不倾倒技术诊断。
确为权限/范围拒绝：“本次记忆查询超出当前授权范围，我将先依据当前可用信息继续。”其他原因按实际回执说明。仅当明确缺少授权时建议授权，不重试、不绕行。
只返回部分相关原文或记忆：“已从 MemHub 获取部分相关记忆，以下将补充这些信息继续。”保留截断、超时或降级等已知限制。零命中的部分查询不能使用“获取部分相关记忆”，只说“已完成MemHub的记忆查询。”空结果本身不证明所有检索路径完整完成。
只有确实存在获准且相关的后续步骤，才宣布继续检索并执行；不能编造建议或承诺证据。无法补充时说明“目前只能依据已返回的信息和当前对话继续。”历史仍未确认时须说明尚不能确认。不能从空结果、不可用或拒绝推断记忆低置信度，只有明确返回了置信度评定才可转述。
不能把错误、拒绝、部分或未知结果说成已成功完成查询；工具状态、原因、限制和引用保持真实，使用其他上下文不代表 MemHub 查询成功。
`.trim();
