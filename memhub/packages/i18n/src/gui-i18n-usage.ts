export const usageChineseMessages = {
  "dailyUsage.title": "记忆模型用量",
  "dailyUsage.today": "今日",
  "dailyUsage.sevenDays": "近 7 天",
  "dailyUsage.thirtyDays": "近 30 天",
  "dailyUsage.input": "输入 token",
  "dailyUsage.output": "输出 token",
  "dailyUsage.cached": "缓存输入 token",
  "dailyUsage.cost": "预估费用",
  "dailyUsage.unknown": "未记录",
  "dailyUsage.allModels": "全部模型",
  "dailyUsage.date": "日期",
  "dailyUsage.pricing": "单价与额度入口",
  "dailyUsage.currency": "币种",
  "dailyUsage.perMillion": "单价（每百万 token）",
  "dailyUsage.billing": "厂商用量 / 账单地址",
  "dailyUsage.openBilling": "查询厂商额度与账单",
  "dailyUsage.save": "保存单价",
  "dailyUsage.saved": "已保存",
  "dailyUsage.saveError": "单价未能保存，请检查输入后重试。",
  "dailyUsage.noPrice": "费用待配置",
  "dailyUsage.partial": "用量或价格不完整",
  "dailyUsage.history":
    "从 {date} 开始记录。此前未记录的日期不代表没有消耗；当日可能仅有部分记录。",
  "dailyUsage.note":
    "按请求开始时间及所选时区分日，包含失败与重试。费用按当前单价估算，范围包含缓存未知或 DeepSeek 节假日峰谷价差；最终扣费与余额以厂商账单为准。不同币种分别显示。",
  "dailyUsage.status": "成功 / 失败 / 进行中",
  "dailyUsage.missing": "有 {count} 次请求未返回完整用量。",
  "dailyUsage.freeRates":
    "留空使用支持模型的内置价格；其他模型显示费用待配置。填 0 表示免费。缓存单价留空时使用输入单价。",
} as const;

type Copy = Record<keyof typeof usageChineseMessages, string>;
export const usageEnglishMessages: Copy = {
  "dailyUsage.title": "Memory model usage",
  "dailyUsage.today": "Today",
  "dailyUsage.sevenDays": "Last 7 days",
  "dailyUsage.thirtyDays": "Last 30 days",
  "dailyUsage.input": "Input tokens",
  "dailyUsage.output": "Output tokens",
  "dailyUsage.cached": "Cached input tokens",
  "dailyUsage.cost": "Estimated cost",
  "dailyUsage.unknown": "Not recorded",
  "dailyUsage.allModels": "All models",
  "dailyUsage.date": "Date",
  "dailyUsage.pricing": "Pricing and balance links",
  "dailyUsage.currency": "Currency",
  "dailyUsage.perMillion": "Price per million tokens",
  "dailyUsage.billing": "Provider usage / billing URL",
  "dailyUsage.openBilling": "View provider balance and billing",
  "dailyUsage.save": "Save pricing",
  "dailyUsage.saved": "Saved",
  "dailyUsage.saveError": "Could not save pricing. Check the values and retry.",
  "dailyUsage.noPrice": "Pricing needed",
  "dailyUsage.partial": "Usage or pricing incomplete",
  "dailyUsage.history":
    "Recording since {date}. Earlier unrecorded days do not mean no spending; the first day may be partial.",
  "dailyUsage.note":
    "Grouped by request start and selected timezone, including failures and retries. Costs use current prices; ranges account for unknown cache usage or DeepSeek holiday peak rates. Provider bills determine actual charges and balance. Currencies are kept separate.",
  "dailyUsage.status": "Succeeded / failed / in progress",
  "dailyUsage.missing": "{count} requests did not return complete usage.",
  "dailyUsage.freeRates":
    "Leave blank for built-in prices on supported models, otherwise costs stay unknown. Enter 0 for free. Blank cached pricing uses the input rate.",
};
export const usageSpanishMessages: Copy = {
  "dailyUsage.title": "Uso del modelo de memoria",
  "dailyUsage.today": "Hoy",
  "dailyUsage.sevenDays": "Últimos 7 días",
  "dailyUsage.thirtyDays": "Últimos 30 días",
  "dailyUsage.input": "Tokens de entrada",
  "dailyUsage.output": "Tokens de salida",
  "dailyUsage.cached": "Tokens de entrada en caché",
  "dailyUsage.cost": "Coste estimado",
  "dailyUsage.unknown": "Sin registrar",
  "dailyUsage.allModels": "Todos los modelos",
  "dailyUsage.date": "Fecha",
  "dailyUsage.pricing": "Precios y saldo",
  "dailyUsage.currency": "Moneda",
  "dailyUsage.perMillion": "Precio por millón de tokens",
  "dailyUsage.billing": "URL de uso / facturación del proveedor",
  "dailyUsage.openBilling": "Ver saldo y facturación del proveedor",
  "dailyUsage.save": "Guardar precios",
  "dailyUsage.saved": "Guardado",
  "dailyUsage.saveError":
    "No se pudieron guardar los precios. Revisa los valores e inténtalo de nuevo.",
  "dailyUsage.noPrice": "Falta el precio",
  "dailyUsage.partial": "Uso o precios incompletos",
  "dailyUsage.history":
    "Registro desde {date}. Los días anteriores sin registrar no indican gasto cero; el primer día puede ser parcial.",
  "dailyUsage.note":
    "Agrupado por inicio de solicitud y zona horaria, incluidos fallos y reintentos. Costes con precios actuales; los intervalos reflejan caché desconocida o tarifas punta en festivos de DeepSeek. La factura determina los cargos y el saldo reales. Las monedas se muestran por separado.",
  "dailyUsage.status": "Correctas / fallidas / en curso",
  "dailyUsage.missing": "{count} solicitudes no devolvieron el uso completo.",
  "dailyUsage.freeRates":
    "Deja en blanco para usar precios integrados cuando existan; de lo contrario el coste es desconocido. Introduce 0 si es gratuito. La caché sin precio usa la tarifa de entrada.",
};

// Existing locales fall back to English for new explanatory copy, as elsewhere in the GUI.
export const usageTranslations: Record<string, Copy> = {
  en: usageEnglishMessages,
  "zh-Hans": usageChineseMessages,
  es: usageSpanishMessages,
};
