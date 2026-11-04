export const usageChineseMessages = {
  "dailyUsage.balanceTitle": "官方账户余额",
  "dailyUsage.refreshBalance": "刷新余额",
  "dailyUsage.totalBalance": "可用余额",
  "dailyUsage.grantedBalance": "赠金余额",
  "dailyUsage.toppedUpBalance": "充值余额",
  "dailyUsage.balanceLoading": "正在查询官方余额…",
  "dailyUsage.noBalanceInfo": "厂商未返回余额明细。",
  "dailyUsage.balanceCheckedAt": "查询时间：{time}",
  "dailyUsage.balanceUnsupported":
    "此厂商暂未接入余额 API，可通过账单链接查看。",
  "dailyUsage.balanceCredentials": "无法读取模型凭证，请检查模型设置。",
  "dailyUsage.balanceUnavailable":
    "官方余额查询失败，请稍后刷新；这不代表余额为零。",
  "dailyUsage.balanceNote":
    "余额由厂商 API 返回，属于整个账户，可能包含其他应用的消耗；每日项目费用仍按 token 和单价估算。",
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
  "dailyUsage.balanceTitle": "Official account balance",
  "dailyUsage.refreshBalance": "Refresh balance",
  "dailyUsage.totalBalance": "Available balance",
  "dailyUsage.grantedBalance": "Granted balance",
  "dailyUsage.toppedUpBalance": "Top-up balance",
  "dailyUsage.balanceLoading": "Reading provider balance…",
  "dailyUsage.noBalanceInfo": "The provider returned no balance details.",
  "dailyUsage.balanceCheckedAt": "Checked at: {time}",
  "dailyUsage.balanceUnsupported":
    "Balance API is not integrated for this provider. Use its billing link.",
  "dailyUsage.balanceCredentials":
    "Could not read model credentials. Check model settings.",
  "dailyUsage.balanceUnavailable":
    "Provider balance is unavailable. Refresh later; this does not mean zero balance.",
  "dailyUsage.balanceNote":
    "The provider API reports the entire account balance, which may include other apps. Daily project costs still use tokens and prices.",
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
  "dailyUsage.balanceTitle": "Saldo oficial de la cuenta",
  "dailyUsage.refreshBalance": "Actualizar saldo",
  "dailyUsage.totalBalance": "Saldo disponible",
  "dailyUsage.grantedBalance": "Saldo promocional",
  "dailyUsage.toppedUpBalance": "Saldo recargado",
  "dailyUsage.balanceLoading": "Consultando el saldo oficial…",
  "dailyUsage.noBalanceInfo": "El proveedor no devolvió detalles del saldo.",
  "dailyUsage.balanceCheckedAt": "Consultado: {time}",
  "dailyUsage.balanceUnsupported":
    "La API de saldo no está integrada para este proveedor. Usa el enlace de facturación.",
  "dailyUsage.balanceCredentials":
    "No se pudieron leer las credenciales. Revisa el modelo.",
  "dailyUsage.balanceUnavailable":
    "No se pudo consultar el saldo. Inténtalo de nuevo; no significa saldo cero.",
  "dailyUsage.balanceNote":
    "La API informa del saldo de toda la cuenta y puede incluir otras aplicaciones. El coste diario del proyecto sigue calculándose con tokens y precios.",
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
