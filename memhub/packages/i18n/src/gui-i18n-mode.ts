const modeChineseMessages = {
  "模式未能保存。你的选择已保留，请重试。":
    "模式未能保存。你的选择已保留，请重试。",
  选择记忆方式: "选择记忆方式",
  "最后一步 · 决定如何处理对话": "最后一步 · 决定如何处理对话",
  返回导入: "返回导入",
  继续: "继续",
  "正在保存…": "正在保存…",
  "当前模型已经启用，可以继续完成首次设置。":
    "当前模型已经启用，可以继续完成首次设置。",
  继续使用当前模型: "继续使用当前模型",
  "你希望 MemHub 如何形成记忆？": "你希望 MemHub 如何形成记忆？",
  "需要明确选择一种方式；之后仍可在设置中更改。":
    "需要明确选择一种方式；之后仍可在设置中更改。",
  记忆模式: "记忆模式",
  推荐: "推荐",
  "使用模型理解对话，提取并整理可持续生长的记忆。":
    "使用模型理解对话，提取并整理可持续生长的记忆。",
  "无需模型，只保存并检索原始对话，不生成提炼记忆。":
    "无需模型，只保存并检索原始对话，不生成提炼记忆。",
  "基础记忆（lite）": "基础记忆（lite）",
  "不调用生成模型。保存并检索原始对话，不再生成高级记忆。":
    "不调用生成模型。保存并检索原始对话，不再生成高级记忆。",
  "智能记忆（pro）": "智能记忆（pro）",
  "使用当前模型从新对话中提取事实、偏好和计划。":
    "使用当前模型从新对话中提取事实、偏好和计划。",
  "深度记忆（ultra）": "深度记忆（ultra）",
  "在智能记忆之上使用知识图谱，并默认每天 03:00 自动整合记忆。":
    "在智能记忆之上使用知识图谱，并默认每天 03:00 自动整合记忆。",
  "lite 下已经处理完成的原始对话不会补提取；排队中和之后的新对话会生成高级记忆。":
    "lite 下已经处理完成的原始对话不会补提取；排队中和之后的新对话会生成高级记忆。",
  "原始对话检索会停止，改为使用高级记忆。":
    "原始对话检索会停止，改为使用高级记忆。",
  "以前生成的高级记忆会继续保留，但在 lite 下不会显示或参与记忆召回。":
    "以前生成的高级记忆会继续保留，但在 lite 下不会显示或参与记忆召回。",
  "每天自动整合会停止，已经生成的数据不会删除。":
    "每天自动整合会停止，已经生成的数据不会删除。",
  "已有高级记忆会在后台加入知识图谱，期间可以继续使用 MemHub。":
    "已有高级记忆会在后台加入知识图谱，期间可以继续使用 MemHub。",
  "知识图谱仍在补建，不会重复启动。": "知识图谱仍在补建，不会重复启动。",
  "记忆模式状态暂时无法读取，请重试。": "记忆模式状态暂时无法读取，请重试。",
  "使用 pro 或 ultra 前需要先配置并启用生成模型。":
    "使用 pro 或 ultra 前需要先配置并启用生成模型。",
  "正在检查或切换向量模型，完成后才能切换记忆模式。":
    "正在检查或切换向量模型，完成后才能切换记忆模式。",
  "当前安装缺少知识图谱组件，无法开启 ultra。":
    "当前安装缺少知识图谱组件，无法开启 ultra。",
  "模式切换未能确认，仍显示上次确认的模式。":
    "模式切换未能确认，仍显示上次确认的模式。",
  "自动整合设置未能保存，请重试。": "自动整合设置未能保存，请重试。",
  "正在检查或切换向量模型，完成后才能整合记忆。":
    "正在检查或切换向量模型，完成后才能整合记忆。",
  "未能开始整合记忆，请重试。": "未能开始整合记忆，请重试。",
  "模式决定新对话如何保存，以及记忆页面展示什么。":
    "模式决定新对话如何保存，以及记忆页面展示什么。",
  刷新记忆模式: "刷新记忆模式",
  当前模式: "当前模式",
  "开发环境当前强制使用 ultra，保存的选择暂不会生效。":
    "开发环境当前强制使用 ultra，保存的选择暂不会生效。",
  "正在检查或切换向量模型，完成前不能切换模式或手动整合。":
    "正在检查或切换向量模型，完成前不能切换模式或手动整合。",
  "正在把已有高级记忆加入知识图谱…": "正在把已有高级记忆加入知识图谱…",
  每天自动整合: "每天自动整合",
  "每天 {time} 自动运行 evolve，设备休眠错过后会补跑。":
    "每天 {time} 自动运行 evolve，设备休眠错过后会补跑。",
  自动整合时间: "自动整合时间",
  "使用这台设备的本地时间。": "使用这台设备的本地时间。",
  立即整合记忆: "立即整合记忆",
  "正在整合记忆…": "正在整合记忆…",
  "上次整合：{time}": "上次整合：{time}",
  尚未整合: "尚未整合",
  立即整合: "立即整合",
  "切换到 {mode}？": "切换到 {mode}？",
  "原始对话在所有模式下都会保留。": "原始对话在所有模式下都会保留。",
  切换模式: "切换模式",
} as const;

export { modeChineseMessages };
export type ModeMessageKey = keyof typeof modeChineseMessages;
type ModeMessages = Record<ModeMessageKey, string>;

export const modeEnglishMessages: ModeMessages = {
  "模式未能保存。你的选择已保留，请重试。":
    "Could not save the mode. Your selection is retained; please retry.",
  选择记忆方式: "Choose how memory works",
  "最后一步 · 决定如何处理对话":
    "Final step · Choose how conversations are handled",
  返回导入: "Back to import",
  继续: "Continue",
  "正在保存…": "Saving…",
  "当前模型已经启用，可以继续完成首次设置。":
    "The current model is already enabled. Continue to finish setup.",
  继续使用当前模型: "Continue with current model",
  "你希望 MemHub 如何形成记忆？": "How should MemHub create memories?",
  "需要明确选择一种方式；之后仍可在设置中更改。":
    "Choose one option. You can change it later in Settings.",
  记忆模式: "Memory mode",
  推荐: "Recommended",
  "使用模型理解对话，提取并整理可持续生长的记忆。":
    "Use a model to understand conversations and build memories that grow over time.",
  "无需模型，只保存并检索原始对话，不生成提炼记忆。":
    "Save and search original conversations without a model or generated memories.",
  "基础记忆（lite）": "Basic memory (lite)",
  "不调用生成模型。保存并检索原始对话，不再生成高级记忆。":
    "Save and search original conversations without using a generation model or creating advanced memories.",
  "智能记忆（pro）": "Smart memory (pro)",
  "使用当前模型从新对话中提取事实、偏好和计划。":
    "Use the current model to extract facts, preferences and plans from new conversations.",
  "深度记忆（ultra）": "Deep memory (ultra)",
  "在智能记忆之上使用知识图谱，并默认每天 03:00 自动整合记忆。":
    "Add a knowledge graph to smart memory and consolidate memories daily at 03:00 by default.",
  "lite 下已经处理完成的原始对话不会补提取；排队中和之后的新对话会生成高级记忆。":
    "Original conversations already processed in lite will not be extracted retroactively. Queued and new conversations will create advanced memories.",
  "原始对话检索会停止，改为使用高级记忆。":
    "Original-conversation search will stop and advanced memories will be used instead.",
  "以前生成的高级记忆会继续保留，但在 lite 下不会显示或参与记忆召回。":
    "Existing advanced memories will be kept, but lite will not display or recall them.",
  "每天自动整合会停止，已经生成的数据不会删除。":
    "Daily consolidation will stop. Existing data will not be deleted.",
  "已有高级记忆会在后台加入知识图谱，期间可以继续使用 MemHub。":
    "Existing advanced memories will be added to the knowledge graph in the background. You can keep using MemHub.",
  "知识图谱仍在补建，不会重复启动。":
    "The knowledge graph is still being built; it will not be started again.",
  "记忆模式状态暂时无法读取，请重试。":
    "Memory mode is temporarily unavailable. Please retry.",
  "使用 pro 或 ultra 前需要先配置并启用生成模型。":
    "Configure and enable a generation model before using pro or ultra.",
  "正在检查或切换向量模型，完成后才能切换记忆模式。":
    "The embedding model is being checked or changed. Wait for it to finish before switching memory modes.",
  "当前安装缺少知识图谱组件，无法开启 ultra。":
    "This installation lacks the knowledge graph component required for ultra.",
  "模式切换未能确认，仍显示上次确认的模式。":
    "The mode change could not be confirmed. The last confirmed mode is still shown.",
  "自动整合设置未能保存，请重试。":
    "Could not save automatic consolidation settings. Please retry.",
  "正在检查或切换向量模型，完成后才能整合记忆。":
    "The embedding model is being checked or changed. Wait for it to finish before consolidating memories.",
  "未能开始整合记忆，请重试。":
    "Could not start memory consolidation. Please retry.",
  "模式决定新对话如何保存，以及记忆页面展示什么。":
    "The mode controls how new conversations are saved and what appears on the Memory page.",
  刷新记忆模式: "Refresh memory mode",
  当前模式: "Current mode",
  "开发环境当前强制使用 ultra，保存的选择暂不会生效。":
    "This development environment forces ultra. Saved selections will not take effect yet.",
  "正在检查或切换向量模型，完成前不能切换模式或手动整合。":
    "The embedding model is being checked or changed. Modes and manual consolidation are unavailable until it finishes.",
  "正在把已有高级记忆加入知识图谱…":
    "Adding existing advanced memories to the knowledge graph…",
  每天自动整合: "Daily automatic consolidation",
  "每天 {time} 自动运行 evolve，设备休眠错过后会补跑。":
    "Run evolve daily at {time}. Missed runs start after the device wakes.",
  自动整合时间: "Automatic consolidation time",
  "使用这台设备的本地时间。": "Uses this device's local time.",
  立即整合记忆: "Consolidate memories now",
  "正在整合记忆…": "Consolidating memories…",
  "上次整合：{time}": "Last consolidated: {time}",
  尚未整合: "Not yet consolidated",
  立即整合: "Consolidate now",
  "切换到 {mode}？": "Switch to {mode}?",
  "原始对话在所有模式下都会保留。":
    "Original conversations are kept in every mode.",
  切换模式: "Switch mode",
};

export const modeSpanishMessages: ModeMessages = {
  "模式未能保存。你的选择已保留，请重试。":
    "No se pudo guardar el modo. Tu selección se conserva; reinténtalo.",
  选择记忆方式: "Elegir cómo funciona la memoria",
  "最后一步 · 决定如何处理对话":
    "Último paso · Decide cómo tratar las conversaciones",
  返回导入: "Volver a la importación",
  继续: "Continuar",
  "正在保存…": "Guardando…",
  "当前模型已经启用，可以继续完成首次设置。":
    "El modelo actual ya está activo. Continúa para terminar la configuración.",
  继续使用当前模型: "Continuar con el modelo actual",
  "你希望 MemHub 如何形成记忆？": "¿Cómo quieres que MemHub cree memorias?",
  "需要明确选择一种方式；之后仍可在设置中更改。":
    "Elige una opción. Podrás cambiarla después en Configuración.",
  记忆模式: "Modo de memoria",
  推荐: "Recomendado",
  "使用模型理解对话，提取并整理可持续生长的记忆。":
    "Usa un modelo para comprender las conversaciones y crear memorias que evolucionan con el tiempo.",
  "无需模型，只保存并检索原始对话，不生成提炼记忆。":
    "Guarda y busca las conversaciones originales sin modelo ni memorias generadas.",
  "基础记忆（lite）": "Memoria básica (lite)",
  "不调用生成模型。保存并检索原始对话，不再生成高级记忆。":
    "Guarda y busca las conversaciones originales sin usar un modelo generativo ni crear memorias avanzadas.",
  "智能记忆（pro）": "Memoria inteligente (pro)",
  "使用当前模型从新对话中提取事实、偏好和计划。":
    "Usa el modelo actual para extraer hechos, preferencias y planes de las conversaciones nuevas.",
  "深度记忆（ultra）": "Memoria profunda (ultra)",
  "在智能记忆之上使用知识图谱，并默认每天 03:00 自动整合记忆。":
    "Añade un grafo de conocimiento a la memoria inteligente y consolida las memorias cada día a las 03:00.",
  "lite 下已经处理完成的原始对话不会补提取；排队中和之后的新对话会生成高级记忆。":
    "Las conversaciones ya procesadas en lite no se extraerán de forma retroactiva. Las que estén en cola y las nuevas crearán memorias avanzadas.",
  "原始对话检索会停止，改为使用高级记忆。":
    "Se dejarán de buscar conversaciones originales y se usarán memorias avanzadas.",
  "以前生成的高级记忆会继续保留，但在 lite 下不会显示或参与记忆召回。":
    "Las memorias avanzadas existentes se conservarán, pero lite no las mostrará ni las usará en consultas.",
  "每天自动整合会停止，已经生成的数据不会删除。":
    "La consolidación diaria se detendrá. Los datos existentes no se eliminarán.",
  "已有高级记忆会在后台加入知识图谱，期间可以继续使用 MemHub。":
    "Las memorias avanzadas se añadirán al grafo en segundo plano. Puedes seguir usando MemHub.",
  "知识图谱仍在补建，不会重复启动。":
    "El grafo de conocimiento aún se está creando y no se iniciará de nuevo.",
  "记忆模式状态暂时无法读取，请重试。":
    "El modo de memoria no está disponible por ahora. Reinténtalo.",
  "使用 pro 或 ultra 前需要先配置并启用生成模型。":
    "Configura y activa un modelo generativo antes de usar pro o ultra.",
  "正在检查或切换向量模型，完成后才能切换记忆模式。":
    "Se está comprobando o cambiando el modelo de embeddings. Espera para cambiar el modo de memoria.",
  "当前安装缺少知识图谱组件，无法开启 ultra。":
    "Esta instalación no incluye el componente de grafo necesario para ultra.",
  "模式切换未能确认，仍显示上次确认的模式。":
    "No se pudo confirmar el cambio. Se sigue mostrando el último modo confirmado.",
  "自动整合设置未能保存，请重试。":
    "No se pudo guardar la consolidación automática. Reinténtalo.",
  "正在检查或切换向量模型，完成后才能整合记忆。":
    "Se está comprobando o cambiando el modelo de embeddings. Espera para consolidar memorias.",
  "未能开始整合记忆，请重试。":
    "No se pudo iniciar la consolidación. Reinténtalo.",
  "模式决定新对话如何保存，以及记忆页面展示什么。":
    "El modo determina cómo se guardan las conversaciones nuevas y qué aparece en Memorias.",
  刷新记忆模式: "Actualizar modo de memoria",
  当前模式: "Modo actual",
  "开发环境当前强制使用 ultra，保存的选择暂不会生效。":
    "Este entorno de desarrollo fuerza ultra. La opción guardada aún no tendrá efecto.",
  "正在检查或切换向量模型，完成前不能切换模式或手动整合。":
    "Se está comprobando o cambiando el modelo de embeddings. No puedes cambiar de modo ni consolidar manualmente hasta que termine.",
  "正在把已有高级记忆加入知识图谱…":
    "Añadiendo memorias avanzadas al grafo de conocimiento…",
  每天自动整合: "Consolidación automática diaria",
  "每天 {time} 自动运行 evolve，设备休眠错过后会补跑。":
    "Ejecuta evolve cada día a las {time}. Si el dispositivo está suspendido, se ejecutará al reactivarse.",
  自动整合时间: "Hora de consolidación automática",
  "使用这台设备的本地时间。": "Usa la hora local de este dispositivo.",
  立即整合记忆: "Consolidar memorias ahora",
  "正在整合记忆…": "Consolidando memorias…",
  "上次整合：{time}": "Última consolidación: {time}",
  尚未整合: "Aún sin consolidar",
  立即整合: "Consolidar ahora",
  "切换到 {mode}？": "¿Cambiar a {mode}?",
  "原始对话在所有模式下都会保留。":
    "Las conversaciones originales se conservan en todos los modos.",
  切换模式: "Cambiar modo",
};

export const modeTraditionalChineseMessages: ModeMessages = {
  ...modeChineseMessages,
  "模式未能保存。你的选择已保留，请重试。":
    "模式無法儲存。你的選擇已保留，請重試。",
  选择记忆方式: "選擇記憶方式",
  "最后一步 · 决定如何处理对话": "最後一步 · 決定如何處理對話",
  返回导入: "返回匯入",
  继续: "繼續",
  "正在保存…": "儲存中…",
  "当前模型已经启用，可以继续完成首次设置。":
    "目前模型已啟用，可以繼續完成首次設定。",
  继续使用当前模型: "繼續使用目前模型",
  "你希望 MemHub 如何形成记忆？": "你希望 MemHub 如何形成記憶？",
  "需要明确选择一种方式；之后仍可在设置中更改。":
    "請選擇一種方式；之後仍可在設定中更改。",
  记忆模式: "記憶模式",
  推荐: "推薦",
  "使用模型理解对话，提取并整理可持续生长的记忆。":
    "使用模型理解對話，擷取並整理可持續成長的記憶。",
  "无需模型，只保存并检索原始对话，不生成提炼记忆。":
    "不需模型，只儲存及搜尋原始對話，不產生提煉記憶。",
  "基础记忆（lite）": "基礎記憶（lite）",
  "不调用生成模型。保存并检索原始对话，不再生成高级记忆。":
    "不呼叫生成模型。儲存並搜尋原始對話，不再產生進階記憶。",
  "智能记忆（pro）": "智慧記憶（pro）",
  "使用当前模型从新对话中提取事实、偏好和计划。":
    "使用目前模型從新對話中擷取事實、偏好和計畫。",
  "深度记忆（ultra）": "深度記憶（ultra）",
  "在智能记忆之上使用知识图谱，并默认每天 03:00 自动整合记忆。":
    "在智慧記憶之上使用知識圖譜，並預設每天 03:00 自動整合記憶。",
  "lite 下已经处理完成的原始对话不会补提取；排队中和之后的新对话会生成高级记忆。":
    "lite 下已處理完成的原始對話不會補擷取；佇列中及之後的新對話會產生進階記憶。",
  "原始对话检索会停止，改为使用高级记忆。":
    "原始對話搜尋會停止，改為使用進階記憶。",
  "以前生成的高级记忆会继续保留，但在 lite 下不会显示或参与记忆召回。":
    "過去產生的進階記憶會保留，但在 lite 下不會顯示或參與記憶召回。",
  "每天自动整合会停止，已经生成的数据不会删除。":
    "每日自動整合會停止，已產生的資料不會刪除。",
  "已有高级记忆会在后台加入知识图谱，期间可以继续使用 MemHub。":
    "現有進階記憶會在背景加入知識圖譜，期間仍可繼續使用 MemHub。",
  "知识图谱仍在补建，不会重复启动。": "知識圖譜仍在補建，不會重複啟動。",
  "记忆模式状态暂时无法读取，请重试。": "暫時無法讀取記憶模式狀態，請重試。",
  "使用 pro 或 ultra 前需要先配置并启用生成模型。":
    "使用 pro 或 ultra 前，請先設定並啟用生成模型。",
  "正在检查或切换向量模型，完成后才能切换记忆模式。":
    "正在檢查或切換向量模型，完成後才能切換記憶模式。",
  "当前安装缺少知识图谱组件，无法开启 ultra。":
    "目前安裝缺少知識圖譜元件，無法啟用 ultra。",
  "模式切换未能确认，仍显示上次确认的模式。":
    "無法確認模式切換，仍顯示上次確認的模式。",
  "自动整合设置未能保存，请重试。": "自動整合設定無法儲存，請重試。",
  "正在检查或切换向量模型，完成后才能整合记忆。":
    "正在檢查或切換向量模型，完成後才能整合記憶。",
  "未能开始整合记忆，请重试。": "無法開始整合記憶，請重試。",
  "模式决定新对话如何保存，以及记忆页面展示什么。":
    "模式決定新對話如何儲存，以及記憶頁面顯示的內容。",
  刷新记忆模式: "重新整理記憶模式",
  当前模式: "目前模式",
  "开发环境当前强制使用 ultra，保存的选择暂不会生效。":
    "開發環境目前強制使用 ultra，儲存的選擇暫時不會生效。",
  "正在检查或切换向量模型，完成前不能切换模式或手动整合。":
    "正在檢查或切換向量模型，完成前無法切換模式或手動整合。",
  "正在把已有高级记忆加入知识图谱…": "正在將現有進階記憶加入知識圖譜…",
  每天自动整合: "每日自動整合",
  "每天 {time} 自动运行 evolve，设备休眠错过后会补跑。":
    "每天 {time} 自動執行 evolve；裝置休眠錯過後會補跑。",
  自动整合时间: "自動整合時間",
  "使用这台设备的本地时间。": "使用這台裝置的本地時間。",
  立即整合记忆: "立即整合記憶",
  "正在整合记忆…": "正在整合記憶…",
  "上次整合：{time}": "上次整合：{time}",
  尚未整合: "尚未整合",
  立即整合: "立即整合",
  "切换到 {mode}？": "切換到 {mode}？",
  "原始对话在所有模式下都会保留。": "原始對話會在所有模式下保留。",
  切换模式: "切換模式",
};

export const modeJapaneseMessages: ModeMessages = {
  ...modeEnglishMessages,
  "模式未能保存。你的选择已保留，请重试。":
    "モードを保存できませんでした。選択内容は保持されています。もう一度お試しください。",
  选择记忆方式: "記憶方法を選択",
  "最后一步 · 决定如何处理对话": "最後のステップ · 会話の処理方法を選択",
  返回导入: "インポートに戻る",
  继续: "続ける",
  "正在保存…": "保存中…",
  "当前模型已经启用，可以继续完成首次设置。":
    "現在のモデルはすでに有効です。このまま初期設定を完了できます。",
  继续使用当前模型: "現在のモデルで続ける",
  "你希望 MemHub 如何形成记忆？": "MemHub でどのように記憶を作成しますか？",
  "需要明确选择一种方式；之后仍可在设置中更改。":
    "いずれかを選択してください。後から設定で変更できます。",
  记忆模式: "記憶モード",
  推荐: "おすすめ",
  "使用模型理解对话，提取并整理可持续生长的记忆。":
    "モデルで会話を理解し、継続的に成長する記憶として整理します。",
  "无需模型，只保存并检索原始对话，不生成提炼记忆。":
    "モデルを使わず、元の会話の保存と検索のみを行います。",
  "基础记忆（lite）": "基本記憶（lite）",
  "智能记忆（pro）": "スマート記憶（pro）",
  "深度记忆（ultra）": "高度な記憶（ultra）",
  "模式决定新对话如何保存，以及记忆页面展示什么。":
    "モードによって、新しい会話の保存方法と記憶ページの表示内容が決まります。",
  刷新记忆模式: "記憶モードを更新",
  当前模式: "現在のモード",
  每天自动整合: "毎日自動で統合",
  自动整合时间: "自動統合の時刻",
  "使用这台设备的本地时间。": "このデバイスの現地時刻を使用します。",
  立即整合记忆: "今すぐ記憶を統合",
  "正在整合记忆…": "記憶を統合中…",
  "上次整合：{time}": "前回の統合：{time}",
  尚未整合: "まだ統合されていません",
  立即整合: "今すぐ統合",
  "切换到 {mode}？": "{mode} に切り替えますか？",
  "原始对话在所有模式下都会保留。": "元の会話はどのモードでも保持されます。",
  切换模式: "モードを切り替える",
  "不调用生成模型。保存并检索原始对话，不再生成高级记忆。":
    "生成モデルを使わず、元の会話を保存・検索します。高度な記憶は生成しません。",
  "使用当前模型从新对话中提取事实、偏好和计划。":
    "現在のモデルで新しい会話から事実、好み、予定を抽出します。",
  "在智能记忆之上使用知识图谱，并默认每天 03:00 自动整合记忆。":
    "スマート記憶に知識グラフを追加し、既定では毎日 03:00 に記憶を自動統合します。",
  "lite 下已经处理完成的原始对话不会补提取；排队中和之后的新对话会生成高级记忆。":
    "lite で処理済みの会話はさかのぼって抽出されません。待機中および今後の会話からは高度な記憶が生成されます。",
  "原始对话检索会停止，改为使用高级记忆。":
    "元の会話の検索を停止し、代わりに高度な記憶を使用します。",
  "以前生成的高级记忆会继续保留，但在 lite 下不会显示或参与记忆召回。":
    "既存の高度な記憶は保持されますが、lite では表示も参照もされません。",
  "每天自动整合会停止，已经生成的数据不会删除。":
    "毎日の自動統合は停止します。既存のデータは削除されません。",
  "已有高级记忆会在后台加入知识图谱，期间可以继续使用 MemHub。":
    "既存の高度な記憶はバックグラウンドで知識グラフに追加されます。その間も MemHub を使用できます。",
  "知识图谱仍在补建，不会重复启动。":
    "知識グラフは引き続き構築中です。重複して開始されません。",
  "记忆模式状态暂时无法读取，请重试。":
    "記憶モードの状態を読み込めません。もう一度お試しください。",
  "使用 pro 或 ultra 前需要先配置并启用生成模型。":
    "pro または ultra を使う前に、生成モデルを設定して有効にしてください。",
  "正在检查或切换向量模型，完成后才能切换记忆模式。":
    "埋め込みモデルを確認または切り替えています。完了後に記憶モードを変更できます。",
  "当前安装缺少知识图谱组件，无法开启 ultra。":
    "この環境には知識グラフのコンポーネントがないため、ultra を有効にできません。",
  "模式切换未能确认，仍显示上次确认的模式。":
    "モード変更を確認できませんでした。最後に確認されたモードを表示しています。",
  "自动整合设置未能保存，请重试。":
    "自動統合の設定を保存できませんでした。もう一度お試しください。",
  "正在检查或切换向量模型，完成后才能整合记忆。":
    "埋め込みモデルを確認または切り替えています。完了後に記憶を統合できます。",
  "未能开始整合记忆，请重试。":
    "記憶の統合を開始できませんでした。もう一度お試しください。",
  "开发环境当前强制使用 ultra，保存的选择暂不会生效。":
    "この開発環境では ultra が強制されています。保存した選択はまだ反映されません。",
  "正在检查或切换向量模型，完成前不能切换模式或手动整合。":
    "埋め込みモデルを確認または切り替えています。完了するまでモード変更と手動統合はできません。",
  "正在把已有高级记忆加入知识图谱…":
    "既存の高度な記憶を知識グラフに追加しています…",
  "每天 {time} 自动运行 evolve，设备休眠错过后会补跑。":
    "毎日 {time} に evolve を実行します。スリープ中に実行できなかった場合は、復帰後に実行します。",
};

export const modeGermanMessages: ModeMessages = {
  ...modeEnglishMessages,
  "模式未能保存。你的选择已保留，请重试。":
    "Der Modus konnte nicht gespeichert werden. Ihre Auswahl bleibt erhalten. Bitte erneut versuchen.",
  选择记忆方式: "Erinnerungsmodus wählen",
  "最后一步 · 决定如何处理对话":
    "Letzter Schritt · Verarbeitung der Unterhaltungen wählen",
  返回导入: "Zurück zum Import",
  继续: "Weiter",
  "正在保存…": "Wird gespeichert…",
  "当前模型已经启用，可以继续完成首次设置。":
    "Das aktuelle Modell ist bereits aktiviert. Fahre fort, um die Einrichtung abzuschließen.",
  继续使用当前模型: "Mit aktuellem Modell fortfahren",
  "你希望 MemHub 如何形成记忆？": "Wie soll MemHub Erinnerungen erstellen?",
  "需要明确选择一种方式；之后仍可在设置中更改。":
    "Wählen Sie eine Option. Sie können sie später in den Einstellungen ändern.",
  记忆模式: "Erinnerungsmodus",
  推荐: "Empfohlen",
  "使用模型理解对话，提取并整理可持续生长的记忆。":
    "Ein Modell versteht Unterhaltungen und erstellt daraus Erinnerungen, die sich weiterentwickeln.",
  "无需模型，只保存并检索原始对话，不生成提炼记忆。":
    "Originalunterhaltungen ohne Modell speichern und durchsuchen; keine abgeleiteten Erinnerungen.",
  "基础记忆（lite）": "Basiserinnerung (lite)",
  "智能记忆（pro）": "Intelligente Erinnerung (pro)",
  "深度记忆（ultra）": "Tiefe Erinnerung (ultra)",
  "模式决定新对话如何保存，以及记忆页面展示什么。":
    "Der Modus legt fest, wie neue Unterhaltungen gespeichert werden und was auf der Erinnerungsseite erscheint.",
  刷新记忆模式: "Erinnerungsmodus aktualisieren",
  当前模式: "Aktueller Modus",
  每天自动整合: "Täglich automatisch konsolidieren",
  自动整合时间: "Zeit der automatischen Konsolidierung",
  "使用这台设备的本地时间。": "Verwendet die lokale Zeit dieses Geräts.",
  立即整合记忆: "Erinnerungen jetzt konsolidieren",
  "正在整合记忆…": "Erinnerungen werden konsolidiert…",
  "上次整合：{time}": "Zuletzt konsolidiert: {time}",
  尚未整合: "Noch nicht konsolidiert",
  立即整合: "Jetzt konsolidieren",
  "切换到 {mode}？": "Zu {mode} wechseln?",
  "原始对话在所有模式下都会保留。":
    "Originalunterhaltungen bleiben in jedem Modus erhalten.",
  切换模式: "Modus wechseln",
  "不调用生成模型。保存并检索原始对话，不再生成高级记忆。":
    "Originalunterhaltungen ohne generatives Modell speichern und durchsuchen. Es werden keine erweiterten Erinnerungen erstellt.",
  "使用当前模型从新对话中提取事实、偏好和计划。":
    "Mit dem aktuellen Modell Fakten, Vorlieben und Pläne aus neuen Unterhaltungen extrahieren.",
  "在智能记忆之上使用知识图谱，并默认每天 03:00 自动整合记忆。":
    "Ergänzt die intelligente Erinnerung um einen Wissensgraphen und konsolidiert Erinnerungen standardmäßig täglich um 03:00 Uhr.",
  "lite 下已经处理完成的原始对话不会补提取；排队中和之后的新对话会生成高级记忆。":
    "Bereits in lite verarbeitete Unterhaltungen werden nicht nachträglich extrahiert. Wartende und neue Unterhaltungen erzeugen erweiterte Erinnerungen.",
  "原始对话检索会停止，改为使用高级记忆。":
    "Die Suche in Originalunterhaltungen wird beendet; stattdessen werden erweiterte Erinnerungen verwendet.",
  "以前生成的高级记忆会继续保留，但在 lite 下不会显示或参与记忆召回。":
    "Vorhandene erweiterte Erinnerungen bleiben erhalten, werden in lite aber weder angezeigt noch abgerufen.",
  "每天自动整合会停止，已经生成的数据不会删除。":
    "Die tägliche Konsolidierung wird beendet. Vorhandene Daten werden nicht gelöscht.",
  "已有高级记忆会在后台加入知识图谱，期间可以继续使用 MemHub。":
    "Vorhandene erweiterte Erinnerungen werden im Hintergrund dem Wissensgraphen hinzugefügt. MemHub bleibt nutzbar.",
  "知识图谱仍在补建，不会重复启动。":
    "Der Wissensgraph wird noch aufgebaut und nicht erneut gestartet.",
  "记忆模式状态暂时无法读取，请重试。":
    "Der Erinnerungsmodus ist vorübergehend nicht verfügbar. Bitte erneut versuchen.",
  "使用 pro 或 ultra 前需要先配置并启用生成模型。":
    "Konfigurieren und aktivieren Sie ein generatives Modell, bevor Sie pro oder ultra verwenden.",
  "正在检查或切换向量模型，完成后才能切换记忆模式。":
    "Das Embedding-Modell wird geprüft oder gewechselt. Danach kann der Erinnerungsmodus geändert werden.",
  "当前安装缺少知识图谱组件，无法开启 ultra。":
    "Dieser Installation fehlt die für ultra benötigte Wissensgraph-Komponente.",
  "模式切换未能确认，仍显示上次确认的模式。":
    "Der Moduswechsel konnte nicht bestätigt werden. Der zuletzt bestätigte Modus wird weiterhin angezeigt.",
  "自动整合设置未能保存，请重试。":
    "Die Einstellungen zur automatischen Konsolidierung konnten nicht gespeichert werden. Bitte erneut versuchen.",
  "正在检查或切换向量模型，完成后才能整合记忆。":
    "Das Embedding-Modell wird geprüft oder gewechselt. Danach können Erinnerungen konsolidiert werden.",
  "未能开始整合记忆，请重试。":
    "Die Konsolidierung konnte nicht gestartet werden. Bitte erneut versuchen.",
  "开发环境当前强制使用 ultra，保存的选择暂不会生效。":
    "Diese Entwicklungsumgebung erzwingt ultra. Die gespeicherte Auswahl wird noch nicht angewendet.",
  "正在检查或切换向量模型，完成前不能切换模式或手动整合。":
    "Das Embedding-Modell wird geprüft oder gewechselt. Bis zum Abschluss sind Moduswechsel und manuelle Konsolidierung nicht verfügbar.",
  "正在把已有高级记忆加入知识图谱…":
    "Vorhandene erweiterte Erinnerungen werden dem Wissensgraphen hinzugefügt…",
  "每天 {time} 自动运行 evolve，设备休眠错过后会补跑。":
    "evolve wird täglich um {time} ausgeführt. Verpasste Läufe starten nach dem Aufwachen des Geräts.",
};

export const modePortugueseMessages: ModeMessages = {
  ...modeEnglishMessages,
  "模式未能保存。你的选择已保留，请重试。":
    "Não foi possível guardar o modo. A sua escolha foi mantida; tente novamente.",
  选择记忆方式: "Escolher o modo de memória",
  "最后一步 · 决定如何处理对话":
    "Último passo · Decida como tratar as conversas",
  返回导入: "Voltar à importação",
  继续: "Continuar",
  "正在保存…": "A guardar…",
  "当前模型已经启用，可以继续完成首次设置。":
    "O modelo atual já está ativo. Continua para concluir a configuração.",
  继续使用当前模型: "Continuar com o modelo atual",
  "你希望 MemHub 如何形成记忆？": "Como deve o MemHub criar memórias?",
  "需要明确选择一种方式；之后仍可在设置中更改。":
    "Escolha uma opção. Pode alterá-la mais tarde nas Definições.",
  记忆模式: "Modo de memória",
  推荐: "Recomendado",
  "使用模型理解对话，提取并整理可持续生长的记忆。":
    "Use um modelo para compreender conversas e criar memórias que evoluem com o tempo.",
  "无需模型，只保存并检索原始对话，不生成提炼记忆。":
    "Guarde e pesquise conversas originais sem modelo nem memórias geradas.",
  "基础记忆（lite）": "Memória básica (lite)",
  "智能记忆（pro）": "Memória inteligente (pro)",
  "深度记忆（ultra）": "Memória profunda (ultra)",
  "模式决定新对话如何保存，以及记忆页面展示什么。":
    "O modo determina como as novas conversas são guardadas e o que aparece na página Memórias.",
  刷新记忆模式: "Atualizar modo de memória",
  当前模式: "Modo atual",
  每天自动整合: "Consolidação automática diária",
  自动整合时间: "Hora da consolidação automática",
  "使用这台设备的本地时间。": "Usa a hora local deste dispositivo.",
  立即整合记忆: "Consolidar memórias agora",
  "正在整合记忆…": "A consolidar memórias…",
  "上次整合：{time}": "Última consolidação: {time}",
  尚未整合: "Ainda não consolidado",
  立即整合: "Consolidar agora",
  "切换到 {mode}？": "Mudar para {mode}?",
  "原始对话在所有模式下都会保留。":
    "As conversas originais são mantidas em todos os modos.",
  切换模式: "Mudar de modo",
  "不调用生成模型。保存并检索原始对话，不再生成高级记忆。":
    "Guarda e pesquisa conversas originais sem usar um modelo generativo nem criar memórias avançadas.",
  "使用当前模型从新对话中提取事实、偏好和计划。":
    "Usa o modelo atual para extrair factos, preferências e planos de novas conversas.",
  "在智能记忆之上使用知识图谱，并默认每天 03:00 自动整合记忆。":
    "Acrescenta um grafo de conhecimento à memória inteligente e consolida memórias diariamente às 03:00 por predefinição.",
  "lite 下已经处理完成的原始对话不会补提取；排队中和之后的新对话会生成高级记忆。":
    "As conversas já processadas em lite não serão extraídas retroativamente. As que estão em fila e as novas criarão memórias avançadas.",
  "原始对话检索会停止，改为使用高级记忆。":
    "A pesquisa em conversas originais será interrompida e passará a usar memórias avançadas.",
  "以前生成的高级记忆会继续保留，但在 lite 下不会显示或参与记忆召回。":
    "As memórias avançadas existentes serão mantidas, mas o lite não as mostrará nem usará na recuperação.",
  "每天自动整合会停止，已经生成的数据不会删除。":
    "A consolidação diária será interrompida. Os dados existentes não serão eliminados.",
  "已有高级记忆会在后台加入知识图谱，期间可以继续使用 MemHub。":
    "As memórias avançadas existentes serão adicionadas ao grafo em segundo plano. Pode continuar a usar o MemHub.",
  "知识图谱仍在补建，不会重复启动。":
    "O grafo de conhecimento ainda está a ser criado e não será iniciado novamente.",
  "记忆模式状态暂时无法读取，请重试。":
    "O modo de memória está temporariamente indisponível. Tente novamente.",
  "使用 pro 或 ultra 前需要先配置并启用生成模型。":
    "Configure e ative um modelo generativo antes de usar pro ou ultra.",
  "正在检查或切换向量模型，完成后才能切换记忆模式。":
    "O modelo de embeddings está a ser verificado ou alterado. Aguarde para mudar o modo de memória.",
  "当前安装缺少知识图谱组件，无法开启 ultra。":
    "Esta instalação não inclui o componente de grafo necessário para ultra.",
  "模式切换未能确认，仍显示上次确认的模式。":
    "Não foi possível confirmar a mudança. Continua a ser mostrado o último modo confirmado.",
  "自动整合设置未能保存，请重试。":
    "Não foi possível guardar a consolidação automática. Tente novamente.",
  "正在检查或切换向量模型，完成后才能整合记忆。":
    "O modelo de embeddings está a ser verificado ou alterado. Aguarde para consolidar memórias.",
  "未能开始整合记忆，请重试。":
    "Não foi possível iniciar a consolidação. Tente novamente.",
  "开发环境当前强制使用 ultra，保存的选择暂不会生效。":
    "Este ambiente de desenvolvimento força ultra. A opção guardada ainda não terá efeito.",
  "正在检查或切换向量模型，完成前不能切换模式或手动整合。":
    "O modelo de embeddings está a ser verificado ou alterado. Não pode mudar de modo nem consolidar manualmente até terminar.",
  "正在把已有高级记忆加入知识图谱…":
    "A adicionar memórias avançadas ao grafo de conhecimento…",
  "每天 {time} 自动运行 evolve，设备休眠错过后会补跑。":
    "Executa evolve diariamente às {time}. Se o dispositivo estiver suspenso, será executado ao reativar.",
};

export const modeFrenchMessages: ModeMessages = {
  ...modeEnglishMessages,
  "模式未能保存。你的选择已保留，请重试。":
    "Impossible d’enregistrer le mode. Votre choix est conservé. Réessayez.",
  选择记忆方式: "Choisir le mode de mémoire",
  "最后一步 · 决定如何处理对话":
    "Dernière étape · Choisissez le traitement des conversations",
  返回导入: "Retour à l’importation",
  继续: "Continuer",
  "正在保存…": "Enregistrement…",
  "当前模型已经启用，可以继续完成首次设置。":
    "Le modèle actuel est déjà activé. Continuez pour terminer la configuration.",
  继续使用当前模型: "Continuer avec le modèle actuel",
  "你希望 MemHub 如何形成记忆？": "Comment MemHub doit-il créer des mémoires ?",
  "需要明确选择一种方式；之后仍可在设置中更改。":
    "Choisissez une option. Vous pourrez la modifier plus tard dans les réglages.",
  记忆模式: "Mode de mémoire",
  推荐: "Recommandé",
  "使用模型理解对话，提取并整理可持续生长的记忆。":
    "Utilisez un modèle pour comprendre les conversations et créer des mémoires qui évoluent.",
  "无需模型，只保存并检索原始对话，不生成提炼记忆。":
    "Enregistrez et recherchez les conversations originales sans modèle ni mémoire générée.",
  "基础记忆（lite）": "Mémoire de base (lite)",
  "智能记忆（pro）": "Mémoire intelligente (pro)",
  "深度记忆（ultra）": "Mémoire approfondie (ultra)",
  "模式决定新对话如何保存，以及记忆页面展示什么。":
    "Le mode détermine comment les nouvelles conversations sont enregistrées et ce qui apparaît dans Mémoires.",
  刷新记忆模式: "Actualiser le mode de mémoire",
  当前模式: "Mode actuel",
  每天自动整合: "Consolidation automatique quotidienne",
  自动整合时间: "Heure de consolidation automatique",
  "使用这台设备的本地时间。": "Utilise l’heure locale de cet appareil.",
  立即整合记忆: "Consolider les mémoires maintenant",
  "正在整合记忆…": "Consolidation des mémoires…",
  "上次整合：{time}": "Dernière consolidation : {time}",
  尚未整合: "Pas encore consolidé",
  立即整合: "Consolider maintenant",
  "切换到 {mode}？": "Passer à {mode} ?",
  "原始对话在所有模式下都会保留。":
    "Les conversations originales sont conservées dans tous les modes.",
  切换模式: "Changer de mode",
  "不调用生成模型。保存并检索原始对话，不再生成高级记忆。":
    "Enregistre et recherche les conversations originales sans modèle génératif ni création de mémoires avancées.",
  "使用当前模型从新对话中提取事实、偏好和计划。":
    "Utilise le modèle actuel pour extraire les faits, préférences et projets des nouvelles conversations.",
  "在智能记忆之上使用知识图谱，并默认每天 03:00 自动整合记忆。":
    "Ajoute un graphe de connaissances à la mémoire intelligente et consolide les mémoires chaque jour à 03:00 par défaut.",
  "lite 下已经处理完成的原始对话不会补提取；排队中和之后的新对话会生成高级记忆。":
    "Les conversations déjà traitées en lite ne seront pas extraites rétroactivement. Celles en attente et les nouvelles créeront des mémoires avancées.",
  "原始对话检索会停止，改为使用高级记忆。":
    "La recherche dans les conversations originales s’arrêtera au profit des mémoires avancées.",
  "以前生成的高级记忆会继续保留，但在 lite 下不会显示或参与记忆召回。":
    "Les mémoires avancées existantes seront conservées, mais lite ne les affichera ni ne les utilisera pour les recherches.",
  "每天自动整合会停止，已经生成的数据不会删除。":
    "La consolidation quotidienne s’arrêtera. Les données existantes ne seront pas supprimées.",
  "已有高级记忆会在后台加入知识图谱，期间可以继续使用 MemHub。":
    "Les mémoires avancées existantes seront ajoutées au graphe en arrière-plan. Vous pouvez continuer à utiliser MemHub.",
  "知识图谱仍在补建，不会重复启动。":
    "Le graphe de connaissances est toujours en cours de création et ne sera pas relancé.",
  "记忆模式状态暂时无法读取，请重试。":
    "Le mode de mémoire est temporairement indisponible. Réessayez.",
  "使用 pro 或 ultra 前需要先配置并启用生成模型。":
    "Configurez et activez un modèle génératif avant d’utiliser pro ou ultra.",
  "正在检查或切换向量模型，完成后才能切换记忆模式。":
    "Le modèle d’embeddings est en cours de vérification ou de changement. Patientez avant de changer de mode.",
  "当前安装缺少知识图谱组件，无法开启 ultra。":
    "Cette installation ne contient pas le composant de graphe requis pour ultra.",
  "模式切换未能确认，仍显示上次确认的模式。":
    "Le changement n’a pas pu être confirmé. Le dernier mode confirmé reste affiché.",
  "自动整合设置未能保存，请重试。":
    "Impossible d’enregistrer la consolidation automatique. Réessayez.",
  "正在检查或切换向量模型，完成后才能整合记忆。":
    "Le modèle d’embeddings est en cours de vérification ou de changement. Patientez avant de consolider les mémoires.",
  "未能开始整合记忆，请重试。":
    "Impossible de lancer la consolidation. Réessayez.",
  "开发环境当前强制使用 ultra，保存的选择暂不会生效。":
    "Cet environnement de développement impose ultra. Le choix enregistré ne s’applique pas encore.",
  "正在检查或切换向量模型，完成前不能切换模式或手动整合。":
    "Le modèle d’embeddings est en cours de vérification ou de changement. Le changement de mode et la consolidation manuelle sont indisponibles jusqu’à la fin.",
  "正在把已有高级记忆加入知识图谱…":
    "Ajout des mémoires avancées au graphe de connaissances…",
  "每天 {time} 自动运行 evolve，设备休眠错过后会补跑。":
    "Exécute evolve chaque jour à {time}. Les exécutions manquées démarrent au réveil de l’appareil.",
};

export const modeKoreanMessages: ModeMessages = {
  ...modeEnglishMessages,
  "模式未能保存。你的选择已保留，请重试。":
    "모드를 저장하지 못했습니다. 선택은 유지됩니다. 다시 시도하세요.",
  选择记忆方式: "메모리 방식 선택",
  "最后一步 · 决定如何处理对话": "마지막 단계 · 대화 처리 방식 선택",
  返回导入: "가져오기로 돌아가기",
  继续: "계속",
  "正在保存…": "저장 중…",
  "当前模型已经启用，可以继续完成首次设置。":
    "현재 모델이 이미 활성화되어 있습니다. 계속해서 초기 설정을 완료하세요.",
  继续使用当前模型: "현재 모델로 계속",
  "你希望 MemHub 如何形成记忆？": "MemHub가 메모리를 어떻게 만들까요?",
  "需要明确选择一种方式；之后仍可在设置中更改。":
    "한 가지 방식을 선택하세요. 나중에 설정에서 변경할 수 있습니다.",
  记忆模式: "메모리 모드",
  推荐: "추천",
  "使用模型理解对话，提取并整理可持续生长的记忆。":
    "모델이 대화를 이해하고 시간이 지날수록 발전하는 메모리로 정리합니다.",
  "无需模型，只保存并检索原始对话，不生成提炼记忆。":
    "모델 없이 원본 대화만 저장하고 검색하며, 정리된 메모리는 만들지 않습니다.",
  "基础记忆（lite）": "기본 메모리(lite)",
  "智能记忆（pro）": "스마트 메모리(pro)",
  "深度记忆（ultra）": "심층 메모리(ultra)",
  "模式决定新对话如何保存，以及记忆页面展示什么。":
    "모드에 따라 새 대화의 저장 방식과 메모리 페이지의 표시 내용이 결정됩니다.",
  刷新记忆模式: "메모리 모드 새로고침",
  当前模式: "현재 모드",
  每天自动整合: "매일 자동 통합",
  自动整合时间: "자동 통합 시간",
  "使用这台设备的本地时间。": "이 기기의 현지 시간을 사용합니다.",
  立即整合记忆: "지금 메모리 통합",
  "正在整合记忆…": "메모리 통합 중…",
  "上次整合：{time}": "마지막 통합: {time}",
  尚未整合: "아직 통합되지 않음",
  立即整合: "지금 통합",
  "切换到 {mode}？": "{mode}(으)로 전환할까요?",
  "原始对话在所有模式下都会保留。": "원본 대화는 모든 모드에서 유지됩니다.",
  切换模式: "모드 전환",
  "不调用生成模型。保存并检索原始对话，不再生成高级记忆。":
    "생성 모델을 사용하지 않고 원본 대화를 저장하고 검색합니다. 고급 메모리는 만들지 않습니다.",
  "使用当前模型从新对话中提取事实、偏好和计划。":
    "현재 모델로 새 대화에서 사실, 선호도, 계획을 추출합니다.",
  "在智能记忆之上使用知识图谱，并默认每天 03:00 自动整合记忆。":
    "스마트 메모리에 지식 그래프를 추가하고 기본적으로 매일 03:00에 메모리를 자동 통합합니다.",
  "lite 下已经处理完成的原始对话不会补提取；排队中和之后的新对话会生成高级记忆。":
    "lite에서 이미 처리된 대화는 소급 추출되지 않습니다. 대기 중인 대화와 새 대화에서는 고급 메모리가 생성됩니다.",
  "原始对话检索会停止，改为使用高级记忆。":
    "원본 대화 검색을 중지하고 고급 메모리를 사용합니다.",
  "以前生成的高级记忆会继续保留，但在 lite 下不会显示或参与记忆召回。":
    "기존 고급 메모리는 유지되지만 lite에서는 표시되거나 검색에 사용되지 않습니다.",
  "每天自动整合会停止，已经生成的数据不会删除。":
    "매일 자동 통합이 중지됩니다. 기존 데이터는 삭제되지 않습니다.",
  "已有高级记忆会在后台加入知识图谱，期间可以继续使用 MemHub。":
    "기존 고급 메모리가 백그라운드에서 지식 그래프에 추가됩니다. 그동안 MemHub를 계속 사용할 수 있습니다.",
  "知识图谱仍在补建，不会重复启动。":
    "지식 그래프가 아직 구축 중이며 다시 시작되지 않습니다.",
  "记忆模式状态暂时无法读取，请重试。":
    "메모리 모드를 일시적으로 읽을 수 없습니다. 다시 시도하세요.",
  "使用 pro 或 ultra 前需要先配置并启用生成模型。":
    "pro 또는 ultra를 사용하기 전에 생성 모델을 구성하고 활성화하세요.",
  "正在检查或切换向量模型，完成后才能切换记忆模式。":
    "임베딩 모델을 확인하거나 전환 중입니다. 완료된 후 메모리 모드를 변경할 수 있습니다.",
  "当前安装缺少知识图谱组件，无法开启 ultra。":
    "현재 설치에 ultra에 필요한 지식 그래프 구성 요소가 없습니다.",
  "模式切换未能确认，仍显示上次确认的模式。":
    "모드 전환을 확인하지 못했습니다. 마지막으로 확인된 모드가 계속 표시됩니다.",
  "自动整合设置未能保存，请重试。":
    "자동 통합 설정을 저장하지 못했습니다. 다시 시도하세요.",
  "正在检查或切换向量模型，完成后才能整合记忆。":
    "임베딩 모델을 확인하거나 전환 중입니다. 완료된 후 메모리를 통합할 수 있습니다.",
  "未能开始整合记忆，请重试。":
    "메모리 통합을 시작하지 못했습니다. 다시 시도하세요.",
  "开发环境当前强制使用 ultra，保存的选择暂不会生效。":
    "현재 개발 환경에서는 ultra가 강제됩니다. 저장한 선택은 아직 적용되지 않습니다.",
  "正在检查或切换向量模型，完成前不能切换模式或手动整合。":
    "임베딩 모델을 확인하거나 전환 중입니다. 완료될 때까지 모드 전환과 수동 통합을 사용할 수 없습니다.",
  "正在把已有高级记忆加入知识图谱…":
    "기존 고급 메모리를 지식 그래프에 추가하는 중…",
  "每天 {time} 自动运行 evolve，设备休眠错过后会补跑。":
    "매일 {time}에 evolve를 실행합니다. 절전 중 놓친 실행은 기기가 깨어난 후 시작됩니다.",
};

export const modeRussianMessages: ModeMessages = {
  ...modeEnglishMessages,
  "模式未能保存。你的选择已保留，请重试。":
    "Не удалось сохранить режим. Ваш выбор сохранён. Повторите попытку.",
  选择记忆方式: "Выберите режим памяти",
  "最后一步 · 决定如何处理对话":
    "Последний шаг · Выберите способ обработки диалогов",
  返回导入: "Назад к импорту",
  继续: "Продолжить",
  "正在保存…": "Сохранение…",
  "当前模型已经启用，可以继续完成首次设置。":
    "Текущая модель уже включена. Продолжите, чтобы завершить настройку.",
  继续使用当前模型: "Продолжить с текущей моделью",
  "你希望 MemHub 如何形成记忆？": "Как MemHub должен создавать воспоминания?",
  "需要明确选择一种方式；之后仍可在设置中更改。":
    "Выберите один вариант. Его можно изменить позже в настройках.",
  记忆模式: "Режим памяти",
  推荐: "Рекомендуется",
  "使用模型理解对话，提取并整理可持续生长的记忆。":
    "Модель анализирует диалоги и создаёт воспоминания, которые развиваются со временем.",
  "无需模型，只保存并检索原始对话，不生成提炼记忆。":
    "Сохраняйте и ищите исходные диалоги без модели и создания воспоминаний.",
  "基础记忆（lite）": "Базовая память (lite)",
  "智能记忆（pro）": "Интеллектуальная память (pro)",
  "深度记忆（ultra）": "Глубокая память (ultra)",
  "模式决定新对话如何保存，以及记忆页面展示什么。":
    "Режим определяет, как сохраняются новые диалоги и что отображается на странице памяти.",
  刷新记忆模式: "Обновить режим памяти",
  当前模式: "Текущий режим",
  每天自动整合: "Ежедневное автообъединение",
  自动整合时间: "Время автообъединения",
  "使用这台设备的本地时间。": "Используется местное время этого устройства.",
  立即整合记忆: "Объединить воспоминания сейчас",
  "正在整合记忆…": "Объединение воспоминаний…",
  "上次整合：{time}": "Последнее объединение: {time}",
  尚未整合: "Ещё не объединялись",
  立即整合: "Объединить сейчас",
  "切换到 {mode}？": "Переключиться на {mode}?",
  "原始对话在所有模式下都会保留。":
    "Исходные диалоги сохраняются во всех режимах.",
  切换模式: "Переключить режим",
  "不调用生成模型。保存并检索原始对话，不再生成高级记忆。":
    "Сохраняет и ищет исходные диалоги без генеративной модели. Расширенные воспоминания не создаются.",
  "使用当前模型从新对话中提取事实、偏好和计划。":
    "Использует текущую модель для извлечения фактов, предпочтений и планов из новых диалогов.",
  "在智能记忆之上使用知识图谱，并默认每天 03:00 自动整合记忆。":
    "Добавляет граф знаний к интеллектуальной памяти и по умолчанию объединяет воспоминания ежедневно в 03:00.",
  "lite 下已经处理完成的原始对话不会补提取；排队中和之后的新对话会生成高级记忆。":
    "Уже обработанные в lite диалоги не будут извлечены задним числом. Диалоги в очереди и новые диалоги создадут расширенные воспоминания.",
  "原始对话检索会停止，改为使用高级记忆。":
    "Поиск в исходных диалогах прекратится; вместо него будут использоваться расширенные воспоминания.",
  "以前生成的高级记忆会继续保留，但在 lite 下不会显示或参与记忆召回。":
    "Существующие расширенные воспоминания сохранятся, но lite не будет показывать или использовать их при поиске.",
  "每天自动整合会停止，已经生成的数据不会删除。":
    "Ежедневное объединение прекратится. Существующие данные не будут удалены.",
  "已有高级记忆会在后台加入知识图谱，期间可以继续使用 MemHub。":
    "Существующие расширенные воспоминания будут добавлены в граф знаний в фоновом режиме. MemHub останется доступен.",
  "知识图谱仍在补建，不会重复启动。":
    "Граф знаний ещё строится и не будет запущен повторно.",
  "记忆模式状态暂时无法读取，请重试。":
    "Режим памяти временно недоступен. Повторите попытку.",
  "使用 pro 或 ultra 前需要先配置并启用生成模型。":
    "Настройте и включите генеративную модель перед использованием pro или ultra.",
  "正在检查或切换向量模型，完成后才能切换记忆模式。":
    "Модель эмбеддингов проверяется или меняется. После завершения можно будет сменить режим памяти.",
  "当前安装缺少知识图谱组件，无法开启 ultra。":
    "В этой установке нет компонента графа знаний, необходимого для ultra.",
  "模式切换未能确认，仍显示上次确认的模式。":
    "Не удалось подтвердить смену режима. По-прежнему показан последний подтверждённый режим.",
  "自动整合设置未能保存，请重试。":
    "Не удалось сохранить настройки автообъединения. Повторите попытку.",
  "正在检查或切换向量模型，完成后才能整合记忆。":
    "Модель эмбеддингов проверяется или меняется. После завершения можно будет объединить воспоминания.",
  "未能开始整合记忆，请重试。":
    "Не удалось начать объединение воспоминаний. Повторите попытку.",
  "开发环境当前强制使用 ultra，保存的选择暂不会生效。":
    "В этой среде разработки принудительно используется ultra. Сохранённый выбор пока не применяется.",
  "正在检查或切换向量模型，完成前不能切换模式或手动整合。":
    "Модель эмбеддингов проверяется или меняется. До завершения смена режима и ручное объединение недоступны.",
  "正在把已有高级记忆加入知识图谱…":
    "Расширенные воспоминания добавляются в граф знаний…",
  "每天 {time} 自动运行 evolve，设备休眠错过后会补跑。":
    "evolve запускается ежедневно в {time}. Пропущенный запуск начнётся после выхода устройства из сна.",
};

export const modeIndonesianMessages: ModeMessages = {
  ...modeEnglishMessages,
  "模式未能保存。你的选择已保留，请重试。":
    "Mode tidak dapat disimpan. Pilihan Anda tetap tersimpan; coba lagi.",
  选择记忆方式: "Pilih cara kerja memori",
  "最后一步 · 决定如何处理对话":
    "Langkah terakhir · Tentukan cara memproses percakapan",
  返回导入: "Kembali ke impor",
  继续: "Lanjutkan",
  "正在保存…": "Menyimpan…",
  "当前模型已经启用，可以继续完成首次设置。":
    "Model saat ini sudah aktif. Lanjutkan untuk menyelesaikan penyiapan.",
  继续使用当前模型: "Lanjutkan dengan model saat ini",
  "你希望 MemHub 如何形成记忆？": "Bagaimana MemHub membuat memori?",
  "需要明确选择一种方式；之后仍可在设置中更改。":
    "Pilih satu opsi. Anda dapat mengubahnya nanti di Pengaturan.",
  记忆模式: "Mode memori",
  推荐: "Disarankan",
  "使用模型理解对话，提取并整理可持续生长的记忆。":
    "Gunakan model untuk memahami percakapan dan membuat memori yang terus berkembang.",
  "无需模型，只保存并检索原始对话，不生成提炼记忆。":
    "Simpan dan cari percakapan asli tanpa model atau memori hasil olahan.",
  "基础记忆（lite）": "Memori dasar (lite)",
  "智能记忆（pro）": "Memori cerdas (pro)",
  "深度记忆（ultra）": "Memori mendalam (ultra)",
  "模式决定新对话如何保存，以及记忆页面展示什么。":
    "Mode menentukan cara percakapan baru disimpan dan apa yang tampil di halaman Memori.",
  刷新记忆模式: "Muat ulang mode memori",
  当前模式: "Mode saat ini",
  每天自动整合: "Konsolidasi otomatis harian",
  自动整合时间: "Waktu konsolidasi otomatis",
  "使用这台设备的本地时间。": "Menggunakan waktu lokal perangkat ini.",
  立即整合记忆: "Konsolidasikan memori sekarang",
  "正在整合记忆…": "Mengonsolidasikan memori…",
  "上次整合：{time}": "Terakhir dikonsolidasikan: {time}",
  尚未整合: "Belum dikonsolidasikan",
  立即整合: "Konsolidasikan sekarang",
  "切换到 {mode}？": "Beralih ke {mode}?",
  "原始对话在所有模式下都会保留。": "Percakapan asli disimpan di semua mode.",
  切换模式: "Beralih mode",
  "不调用生成模型。保存并检索原始对话，不再生成高级记忆。":
    "Simpan dan cari percakapan asli tanpa model generatif. Memori lanjutan tidak dibuat.",
  "使用当前模型从新对话中提取事实、偏好和计划。":
    "Gunakan model saat ini untuk mengekstrak fakta, preferensi, dan rencana dari percakapan baru.",
  "在智能记忆之上使用知识图谱，并默认每天 03:00 自动整合记忆。":
    "Tambahkan knowledge graph ke memori cerdas dan konsolidasikan memori setiap hari pukul 03.00 secara default.",
  "lite 下已经处理完成的原始对话不会补提取；排队中和之后的新对话会生成高级记忆。":
    "Percakapan yang sudah diproses dalam lite tidak akan diekstrak ulang. Percakapan dalam antrean dan percakapan baru akan membuat memori lanjutan.",
  "原始对话检索会停止，改为使用高级记忆。":
    "Pencarian percakapan asli akan dihentikan dan diganti dengan memori lanjutan.",
  "以前生成的高级记忆会继续保留，但在 lite 下不会显示或参与记忆召回。":
    "Memori lanjutan yang ada tetap disimpan, tetapi lite tidak akan menampilkan atau menggunakannya dalam pencarian.",
  "每天自动整合会停止，已经生成的数据不会删除。":
    "Konsolidasi harian akan dihentikan. Data yang ada tidak akan dihapus.",
  "已有高级记忆会在后台加入知识图谱，期间可以继续使用 MemHub。":
    "Memori lanjutan yang ada akan ditambahkan ke knowledge graph di latar belakang. MemHub tetap dapat digunakan.",
  "知识图谱仍在补建，不会重复启动。":
    "Knowledge graph masih dibuat dan tidak akan dimulai ulang.",
  "记忆模式状态暂时无法读取，请重试。":
    "Mode memori untuk sementara tidak tersedia. Coba lagi.",
  "使用 pro 或 ultra 前需要先配置并启用生成模型。":
    "Konfigurasikan dan aktifkan model generatif sebelum menggunakan pro atau ultra.",
  "正在检查或切换向量模型，完成后才能切换记忆模式。":
    "Model embedding sedang diperiksa atau diganti. Tunggu hingga selesai sebelum mengganti mode memori.",
  "当前安装缺少知识图谱组件，无法开启 ultra。":
    "Instalasi ini tidak memiliki komponen knowledge graph yang diperlukan untuk ultra.",
  "模式切换未能确认，仍显示上次确认的模式。":
    "Perubahan mode tidak dapat dikonfirmasi. Mode terakhir yang dikonfirmasi tetap ditampilkan.",
  "自动整合设置未能保存，请重试。":
    "Pengaturan konsolidasi otomatis tidak dapat disimpan. Coba lagi.",
  "正在检查或切换向量模型，完成后才能整合记忆。":
    "Model embedding sedang diperiksa atau diganti. Tunggu hingga selesai sebelum mengonsolidasikan memori.",
  "未能开始整合记忆，请重试。":
    "Konsolidasi memori tidak dapat dimulai. Coba lagi.",
  "开发环境当前强制使用 ultra，保存的选择暂不会生效。":
    "Lingkungan pengembangan ini memaksa penggunaan ultra. Pilihan yang disimpan belum berlaku.",
  "正在检查或切换向量模型，完成前不能切换模式或手动整合。":
    "Model embedding sedang diperiksa atau diganti. Mode dan konsolidasi manual tidak tersedia hingga selesai.",
  "正在把已有高级记忆加入知识图谱…":
    "Menambahkan memori lanjutan ke knowledge graph…",
  "每天 {time} 自动运行 evolve，设备休眠错过后会补跑。":
    "Jalankan evolve setiap hari pukul {time}. Proses yang terlewat akan dimulai setelah perangkat aktif kembali.",
};
