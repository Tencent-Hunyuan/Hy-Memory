export const usageChineseMessages = {
  "finance.apiKey": "账务 API Key",
  "finance.projectId": "项目 ID（可选）",
  "finance.apiKeyId": "API Key ID（可选）",
  "finance.accessKeyId": "AccessKey ID",
  "finance.accessKeySecret": "AccessKey Secret",
  "finance.securityToken": "临时安全令牌（可选）",
  "finance.secretId": "SecretId",
  "finance.secretKey": "SecretKey",
  "finance.azureToken": "Azure 账务访问令牌",
  "finance.azureResource": "Azure OpenAI 资源 ID",
  "finance.authorizationNote":
    "仅执行只读账务查询。建议使用专用凭证，凭证保存在系统钥匙串，页面不会回显，不替换模型 Key；授权范围内的费用可能包含其他应用。",
  "finance.openaiHint":
    "需要组织 Admin API Key；普通模型 Key 不能读取组织账单。可填写项目或 API Key ID 缩小查询范围。",
  "finance.openrouterHint":
    "普通模型 Key 已能查询 Key 额度和用量。要查整个账户余额，请填写独立 Management Key。",
  "finance.azureHint":
    "填写有 Cost Management 读取权限的有效访问令牌，以及当前模型对应的完整资源 ID。令牌失效后需更新。",
  "finance.configured": "已保存独立账务凭证；字段留空，不会回显。",
  "finance.saveAuthorization": "保存账务授权",
  "finance.removeAuthorization": "移除账务授权",
  "finance.authorizationDocs": "查看官方授权说明",
  "finance.authorizationError":
    "账务授权未能保存或读取，请检查输入及系统钥匙串后重试。",
  "finance.authorizationSaved": "已保存，请以刷新后的查询结果确认权限。",
  "finance.configureAuthorization": "配置账务查询",
  "finance.authorizationRequired":
    "需要独立账务授权，或已有授权不足／失效。模型 Key 不能代替管理凭证。",
  "finance.scopeKey": "范围：当前 API Key，不是账户余额。",
  "finance.scopeAccount": "范围：整个账户，可能包含其他应用。",
  "finance.scopeOrganization":
    "范围：组织账单（可按项目或 API Key ID 筛选），不是账户余额。",
  "finance.scopeResource": "范围：所配置 Azure 资源的官方费用，不是账户余额。",
  "finance.keyRemaining": "Key 剩余额度",
  "finance.noKeyLimit": "未设置 Key 限额",
  "finance.cashBalance": "现金余额",
  "finance.spendDay": "官方本日费用",
  "finance.spendWeek": "官方本周费用",
  "finance.spendMonth": "官方本月费用",
  "finance.spendMonthToDate": "官方本月至今费用",
  "finance.spendAllTime": "官方累计费用",
  "dailyUsage.balanceTitle": "官方额度与用量",
  "dailyUsage.refreshBalance": "刷新余额",
  "dailyUsage.totalBalance": "可用余额",
  "dailyUsage.grantedBalance": "赠金余额",
  "dailyUsage.toppedUpBalance": "充值余额",
  "dailyUsage.balanceLoading": "正在查询官方余额…",
  "dailyUsage.noBalanceInfo": "厂商暂未返回可用的账务明细。",
  "dailyUsage.balanceCheckedAt": "查询时间：{time}",
  "dailyUsage.balanceUnsupported":
    "此厂商暂未接入余额 API，可通过账单链接查看。",
  "dailyUsage.balanceCredentials": "无法读取模型凭证，请检查模型设置。",
  "dailyUsage.balanceUnavailable":
    "官方余额查询失败，请稍后刷新；这不代表余额为零。",
  "dailyUsage.balanceNote":
    "数据由厂商 API 返回，可能覆盖账户、组织、资源或 API Key，不等同本项目消耗。统计窗口以厂商为准；本项目费用仍按 token 和单价估算。",
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
  "finance.apiKey": "Billing API key",
  "finance.projectId": "Project ID (optional)",
  "finance.apiKeyId": "API key ID (optional)",
  "finance.accessKeyId": "AccessKey ID",
  "finance.accessKeySecret": "AccessKey Secret",
  "finance.securityToken": "Temporary security token (optional)",
  "finance.secretId": "SecretId",
  "finance.secretKey": "SecretKey",
  "finance.azureToken": "Azure billing access token",
  "finance.azureResource": "Azure OpenAI resource ID",
  "finance.authorizationNote":
    "Only read-only billing requests are made. Use dedicated credentials; they stay in the OS secret store, are never echoed, and do not replace model keys. The authorized scope may include other apps.",
  "finance.openaiHint":
    "Requires an organization Admin API key. A regular model key cannot read organization costs. Optionally filter by project or API key ID.",
  "finance.openrouterHint":
    "A model key can read its own quota and usage. Provide a separate Management Key to query account-wide credits.",
  "finance.azureHint":
    "Provide a valid token with Cost Management read access and the full resource ID matching this model endpoint. Replace the token when it expires.",
  "finance.configured":
    "Independent billing credentials are saved; values are not echoed.",
  "finance.saveAuthorization": "Save billing access",
  "finance.removeAuthorization": "Remove billing access",
  "finance.authorizationDocs": "Official authorization guide",
  "finance.authorizationError":
    "Could not read or save billing access. Check the values and OS secret store.",
  "finance.authorizationSaved":
    "Saved. Confirm access using the refreshed query result.",
  "finance.configureAuthorization": "Configure billing query",
  "finance.authorizationRequired":
    "Separate billing authorization is required or has expired/insufficient access. Model keys do not replace management credentials.",
  "finance.scopeKey": "Scope: this API key, not the account balance.",
  "finance.scopeAccount":
    "Scope: the entire account; other apps may be included.",
  "finance.scopeOrganization":
    "Scope: organization costs, optionally filtered by project/key ID; not account balance.",
  "finance.scopeResource":
    "Scope: official costs for the configured Azure resource; not account balance.",
  "finance.keyRemaining": "Key quota remaining",
  "finance.noKeyLimit": "No key limit configured",
  "finance.cashBalance": "Cash balance",
  "finance.spendDay": "Official daily spend",
  "finance.spendWeek": "Official weekly spend",
  "finance.spendMonth": "Official monthly spend",
  "finance.spendMonthToDate": "Official month-to-date spend",
  "finance.spendAllTime": "Official lifetime spend",
  "dailyUsage.balanceTitle": "Official credits and usage",
  "dailyUsage.refreshBalance": "Refresh balance",
  "dailyUsage.totalBalance": "Available balance",
  "dailyUsage.grantedBalance": "Granted balance",
  "dailyUsage.toppedUpBalance": "Top-up balance",
  "dailyUsage.balanceLoading": "Reading provider balance…",
  "dailyUsage.noBalanceInfo":
    "The provider has not returned financial details.",
  "dailyUsage.balanceCheckedAt": "Checked at: {time}",
  "dailyUsage.balanceUnsupported":
    "Balance API is not integrated for this provider. Use its billing link.",
  "dailyUsage.balanceCredentials":
    "Could not read model credentials. Check model settings.",
  "dailyUsage.balanceUnavailable":
    "Provider balance is unavailable. Refresh later; this does not mean zero balance.",
  "dailyUsage.balanceNote":
    "Provider API facts may cover an account, organization, resource or API key, not just this app. Provider reporting windows apply; local project costs still use tokens and prices.",
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
  "finance.apiKey": "Clave API de facturación",
  "finance.projectId": "ID de proyecto (opcional)",
  "finance.apiKeyId": "ID de clave API (opcional)",
  "finance.accessKeyId": "AccessKey ID",
  "finance.accessKeySecret": "AccessKey Secret",
  "finance.securityToken": "Token de seguridad temporal (opcional)",
  "finance.secretId": "SecretId",
  "finance.secretKey": "SecretKey",
  "finance.azureToken": "Token de acceso de facturación de Azure",
  "finance.azureResource": "ID del recurso Azure OpenAI",
  "finance.authorizationNote":
    "Solo se realizan consultas de lectura. Usa credenciales dedicadas; quedan en el almacén seguro del sistema, no se muestran ni sustituyen la clave del modelo. El ámbito puede incluir otras aplicaciones.",
  "finance.openaiHint":
    "Requiere una clave Admin de la organización. La clave del modelo no puede leer estos costes. Puedes filtrar por proyecto o ID de clave API.",
  "finance.openrouterHint":
    "La clave del modelo puede consultar su cuota y uso. Para el saldo de toda la cuenta, introduce una Management Key independiente.",
  "finance.azureHint":
    "Introduce un token válido con lectura de Cost Management y el ID completo del recurso de este modelo. Renueva el token cuando caduque.",
  "finance.configured":
    "Credenciales de facturación guardadas; no se muestran sus valores.",
  "finance.saveAuthorization": "Guardar autorización",
  "finance.removeAuthorization": "Eliminar autorización",
  "finance.authorizationDocs": "Guía oficial de autorización",
  "finance.authorizationError":
    "No se pudo leer o guardar la autorización. Revisa los valores y el almacén seguro.",
  "finance.authorizationSaved":
    "Guardado. Confirma los permisos con la consulta actualizada.",
  "finance.configureAuthorization": "Configurar consulta de facturación",
  "finance.authorizationRequired":
    "Se requiere autorización independiente o los permisos son insuficientes o han caducado. La clave del modelo no sustituye a las credenciales de gestión.",
  "finance.scopeKey": "Ámbito: esta clave API, no el saldo de la cuenta.",
  "finance.scopeAccount":
    "Ámbito: toda la cuenta; puede incluir otras aplicaciones.",
  "finance.scopeOrganization":
    "Ámbito: costes de la organización, con filtro opcional por proyecto o clave; no es el saldo.",
  "finance.scopeResource":
    "Ámbito: costes oficiales del recurso Azure configurado; no es el saldo.",
  "finance.keyRemaining": "Cuota restante de la clave",
  "finance.noKeyLimit": "Sin límite de clave configurado",
  "finance.cashBalance": "Saldo en efectivo",
  "finance.spendDay": "Gasto oficial diario",
  "finance.spendWeek": "Gasto oficial semanal",
  "finance.spendMonth": "Gasto oficial mensual",
  "finance.spendMonthToDate": "Gasto oficial del mes hasta hoy",
  "finance.spendAllTime": "Gasto oficial acumulado",
  "dailyUsage.balanceTitle": "Créditos y uso oficiales",
  "dailyUsage.refreshBalance": "Actualizar saldo",
  "dailyUsage.totalBalance": "Saldo disponible",
  "dailyUsage.grantedBalance": "Saldo promocional",
  "dailyUsage.toppedUpBalance": "Saldo recargado",
  "dailyUsage.balanceLoading": "Consultando el saldo oficial…",
  "dailyUsage.noBalanceInfo":
    "El proveedor no ha devuelto detalles financieros.",
  "dailyUsage.balanceCheckedAt": "Consultado: {time}",
  "dailyUsage.balanceUnsupported":
    "La API de saldo no está integrada para este proveedor. Usa el enlace de facturación.",
  "dailyUsage.balanceCredentials":
    "No se pudieron leer las credenciales. Revisa el modelo.",
  "dailyUsage.balanceUnavailable":
    "No se pudo consultar el saldo. Inténtalo de nuevo; no significa saldo cero.",
  "dailyUsage.balanceNote":
    "Los datos oficiales pueden abarcar la cuenta, organización, recurso o clave, no solo esta aplicación. Rigen los períodos del proveedor; el coste del proyecto sigue calculándose con tokens y precios.",
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
