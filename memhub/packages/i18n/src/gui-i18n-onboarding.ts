/** Onboarding copy is also its stable Chinese fallback key. Never use these keys as workflow state. */
export const onboardingSpanishMessages = {
  "把熟悉的背景，带到这里": "Trae aquí el contexto que ya tienes",
  "你的记忆，从这里接上": "Tus memorias empiezan aquí",
  "这张卡还没测过。点一次「设为当前模型」会先自动测，如果还是不行，说明端点、key 或模型名有一处不对。":
    "Esta configuración aún no se ha probado. Al seleccionarla como modelo actual se probará automáticamente. Si falla, comprueba el servicio, la clave y el nombre del modelo.",
  "这类模型接口暂时用不了，换一张卡。":
    "Este tipo de servicio de modelos no está disponible. Elige otra configuración.",
  "这张卡缺了向量化模型，补上才能启用。":
    "Falta el modelo de embeddings. Añádelo antes de habilitar esta configuración.",
  "这张卡已经不在了，可能刚被删掉。刷新一下。":
    "Esta configuración ya no existe; puede que se haya eliminado. Actualiza la lista.",
  "这张卡刚被改过。刷新一下再试，免得覆盖掉别处的改动。":
    "Esta configuración cambió. Actualiza antes de reintentar para no sobrescribir otros cambios.",
  onboardingModelOperationFailed: "La operación no se completó: {code}",
  onboardingModelSkipDestination: "Omitir y entrar a Aplicaciones",
  首次设置进度: "Progreso de la configuración inicial",
  导入历史: "Importar historial",
  连接应用: "Conectar apps",
  沉淀记忆: "Crear memorias",
  本地存储: "Almacenamiento local",
  "对话与记忆保存在本机；使用云模型或已授权的助手时，相关内容会发送给对应服务。":
    "Las conversaciones y memorias se guardan en este dispositivo. Al usar modelos en la nube o asistentes autorizados, el contenido pertinente se envía al servicio correspondiente.",
  "来源位置、身份或状态已变化，请重新选择并核对。尚未提交本次授权。":
    "La ubicación, identidad o estado de la fuente ha cambiado. Vuelve a seleccionarla y comprobarla. Todavía no se ha enviado esta autorización.",
  "来源位置、身份或状态已变化，正在等待本次提交回执。不会自动扩大已提交范围。":
    "La ubicación, identidad o estado de la fuente ha cambiado. Se espera la confirmación del envío; su alcance no se ampliará automáticamente.",
  测试实例不接入应用: "La instancia de prueba no conecta aplicaciones",
  "请先把 MemHub 拖到“应用程序”并从那里重新打开，才能接入应用。":
    "Para conectar aplicaciones, arrastra MemHub a Aplicaciones y vuelve a abrirlo desde allí.",
  "部分应用未能完成接入，已成功的设置会保留，可进入应用后单独处理。":
    "No se pudieron conectar algunas aplicaciones. Se conservará la configuración completada y podrás resolver las demás desde la aplicación.",
  onboardingConnectionPartial:
    "Aplicaciones conectadas: {success}; pendientes: {failed}: {names}. Puedes resolverlas por separado desde la aplicación.",
  "首次设置的完成状态未能保存。已确认的导入回执不受影响；下次启动可能再次出现此页。":
    "No se pudo guardar la finalización de la configuración inicial. Las importaciones confirmadas no se ven afectadas; esta página podría reaparecer al iniciar.",
  "首次设置的完成状态尚未确认。已确认的导入回执不受影响；下次启动可能再次出现此页。":
    "La finalización de la configuración inicial aún no está confirmada. Las importaciones confirmadas no se ven afectadas; esta página podría reaparecer al iniciar.",
  "位置检测未完成，尚未读取历史。可以重新检测，或先进入工作台。":
    "No se completó la detección de ubicaciones y no se ha leído el historial. Puedes volver a detectar o entrar al espacio de trabajo.",
  "暂时无法核对来源，尚未提交授权。选择已保留，可以重试。":
    "No se pueden comprobar las fuentes por ahora. No se ha enviado la autorización. Tu selección se conserva; puedes reintentar.",
  "本次没有来源获准导入。请核对下方原因后重试，或到应用页查看状态。":
    "No se autorizó la importación de ninguna fuente. Comprueba los motivos y reintenta, o consulta el estado en Aplicaciones.",
  "开始导入的结果尚未确认，选择已保留。可以重试，或进入应用页核对。":
    "El inicio de la importación aún no está confirmado. Tu selección se conserva. Puedes reintentar o comprobarlo en Aplicaciones.",
  未完成: "Sin completar",
  核对中: "Comprobando",
  仍进入应用页: "Entrar igualmente a Aplicaciones",
  仍进入工作台: "Entrar igualmente al espacio de trabajo",
  仍进入设置: "Entrar igualmente a Configuración",
  重试保存并进入: "Reintentar guardar y entrar",
  "稍后添加，先进入": "Entrar y añadir más tarde",
  "正在开始导入…": "Iniciando importación…",
  开始导入: "Iniciar importación",
  "不用从头介绍自己。从常用的 App 开始，把已有背景带过来。":
    "No necesitas presentarte de nuevo. Trae el contexto de las aplicaciones que ya utilizas.",
  "确认前只检查应用信息，不读取对话，也不安装连接。":
    "Antes de confirmar, solo se comprueba la información de las aplicaciones. No se leen conversaciones ni se instalan conexiones.",
  "本机 AI 应用": "Aplicaciones de IA en este dispositivo",
  重新检测位置: "Volver a detectar ubicaciones",
  "检测中…": "Detectando…",
  重新检测: "Volver a detectar",
  候选应用: "Aplicaciones disponibles",
  "正在检测其他应用…": "Detectando otras aplicaciones…",
  检测未完成: "Detección sin completar",
  已全选: "Todas seleccionadas",
  全选: "Seleccionar todas",
  onboardingImportHistory: "Importar el historial de {name}",
  正在核对本机保存位置: "Comprobando la ubicación de almacenamiento local",
  已发现: "Encontrada",
  位置待核对: "Ubicación pendiente de comprobar",
  "当前没有候选来源。可以稍后在应用页添加历史包。":
    "No hay fuentes disponibles. Puedes añadir un archivo de historial más tarde en Aplicaciones.",
  "候选来源尚未加载。可以检测位置，或先进入工作台。":
    "Las fuentes aún no se han cargado. Puedes detectar ubicaciones o entrar al espacio de trabajo.",
  本次导入: "Esta importación",
  "个 App 的历史对话": "aplicaciones con historial de conversaciones",
  "把零散的会话，": "Reúne conversaciones dispersas",
  汇成完整的记忆: "en memorias completas",
  "少一些重复交代，多一些理解。":
    "Menos explicaciones repetidas, más comprensión.",
  onboardingMoreApps: "Desplázate para ver más · Quedan {count} aplicaciones",
  "onboardingMoreApps.one":
    "Desplázate para ver más · Queda {count} aplicación",
  onboardingAllApps: "Se muestran las {count} aplicaciones",
  默认位置: "Ubicación predeterminada",
  自定义位置: "Ubicación personalizada",
  尚未检测: "Sin detectar",
  检测中: "Detectando",
  未发现: "No encontrada",
  无法访问: "Sin acceso",
  位置无效: "Ubicación no válida",
  "需要更新 Core": "Es necesario actualizar Core",
  "候选位置不存在，可能尚无历史或路径不同。":
    "La ubicación no existe. Puede que no haya historial o que la ruta sea diferente.",
  "无法检查此位置，请核对目录访问权限后重试。":
    "No se puede comprobar esta ubicación. Revisa los permisos de acceso a la carpeta y reintenta.",
  "此位置未通过安全检查，不能读取。":
    "Esta ubicación no superó la comprobación de seguridad y no se puede leer.",
  "当前 Core 未提供可确认的位置身份，需更新后再选择。":
    "Core no proporcionó una identidad de ubicación verificable. Actualízalo antes de seleccionar.",
  暂不支持历史导入: "La importación de historial aún no está disponible",
  "当前来源不可读取，请重新检测。":
    "No se puede leer esta fuente. Vuelve a detectarla.",
  "来源目录暂时不可读取，请核对位置和权限。":
    "No se puede leer la carpeta de la fuente. Comprueba su ubicación y permisos.",
  "来源目录身份已变化，请返回重新检测。":
    "La identidad de la carpeta ha cambiado. Vuelve atrás y detecta de nuevo.",
  "来源权限已变化，请返回重新确认。":
    "Los permisos de la fuente han cambiado. Vuelve atrás y confírmalos de nuevo.",
  "此来源尚未获得读取授权。":
    "Esta fuente aún no tiene autorización de lectura.",
  "自定义来源位置需要重新确认。":
    "Debes volver a confirmar la ubicación personalizada de la fuente.",
  "当前 Core 未启用历史导入。":
    "Core no tiene habilitada la importación de historial.",
  onboardingImportRefused:
    "La importación aún no está autorizada ({reason}). Comprueba la fuente y reintenta.",
  确认自定义位置: "Confirmar ubicación personalizada",
  关闭位置确认: "Cerrar confirmación de ubicación",
  "此位置来自当前配置，并非应用默认目录。确认位置不会开始读取历史。":
    "Esta ubicación procede de la configuración actual y no es la carpeta predeterminada de la aplicación. Confirmarla no inicia la lectura del historial.",
  onboardingConfirmHistory:
    "Confirmo que esta es la ubicación del historial de {name}",
  取消: "Cancelar",
  确认此位置: "Confirmar esta ubicación",
  "对话正在汇入，记忆从这里开始":
    "Tus conversaciones llegan; tus memorias empiezan aquí",
  "导入进行中 · 顺手连接应用":
    "Importación en curso · Conecta tus aplicaciones",
  "对话已保存，记忆尚待生成。":
    "Las conversaciones se han guardado. Las memorias aún no se han generado.",
  "导入在后台继续，无需等待。":
    "La importación continúa en segundo plano; no necesitas esperar.",
  "正在重新核对来源；已保存数量仍按任务回执显示。":
    "Comprobando de nuevo las fuentes. Las cantidades guardadas siguen basándose en las confirmaciones de las tareas.",
  "本次历史导入已暂停，已保存的对话还在。":
    "Esta importación de historial está pausada. Las conversaciones guardadas se conservan.",
  "导入等待处理，已保存的对话还在。":
    "La importación está pendiente. Las conversaciones guardadas se conservan.",
  停止后续操作: "Detener las operaciones siguientes",
  暂停导入: "Pausar importación",
  继续导入: "Reanudar importación",
  "配置模型，沉淀记忆": "Configurar un modelo para crear memorias",
  导入与原文: "Importación y contenido original",
  "本次导入已结束。已保存的对话无需重复导入。":
    "Esta importación ha terminado. No es necesario volver a importar las conversaciones guardadas.",
  "对话先保存在本机。你可以同时设置后续连接。":
    "Primero se guardan las conversaciones en este dispositivo. Mientras tanto, puedes configurar las conexiones.",
  "导入不会自动开放应用读取权限。配置模型后，才会开始生成记忆。":
    "Importar no concede automáticamente acceso de lectura a las aplicaciones. Las memorias comenzarán a generarse cuando configures un modelo.",
  onboardingHistoryWindow:
    "Se ha detectado un gran volumen de conversaciones. Para controlar el uso y mantener coherentes las cronologías, solo se importarán los últimos 3 días de todas las aplicaciones.",
  导入说明: "Información de importación",
  正在导入你的会话: "Importando tus conversaciones",
  "后台将持续为你导入，可以放心进入下一步":
    "La importación continuará en segundo plano; puedes pasar al siguiente paso",
  未开始的导入: "Importaciones no iniciadas",
  "未能开始导入，其他已接受任务不受影响。":
    "No se pudo iniciar la importación. Las demás tareas aceptadas no se ven afectadas.",
  查看未导入的应用: "Ver aplicaciones sin importar",
  让记忆继续生长: "Haz crecer tus memorias",
  连接你的应用: "Conecta tus aplicaciones",
  "适用于本次选择且支持的应用。":
    "Solo para las apps seleccionadas y compatibles.",
  一键接入应用: "Conectar con un clic",
  "安装记忆连接，让所选应用用上 MemHub 中的相关记忆。":
    "Instala la conexión para que las apps elegidas usen memorias relevantes de MemHub.",
  "当前使用独立测试数据目录，不会修改本机应用的连接配置。":
    "Se utiliza una carpeta independiente de datos de prueba. No se modificará la configuración de conexión de las aplicaciones locales.",
  "点击底部按钮后，后台分别接入各应用；某个应用失败不影响其他应用。":
    "Al continuar, cada app se conecta en segundo plano. Si una falla, las demás siguen.",
  "暂时无法连接应用，当前不会安装连接。":
    "No se pueden conectar aplicaciones por ahora. No se instalará ninguna conexión.",
  onboardingBatchStopped:
    "Se detuvieron las operaciones siguientes; las ya enviadas se comprobarán según sus confirmaciones.",
  onboardingBatchResult: "Confirmadas: {confirmed}; sin confirmar: {failed}.",
  "已确认项保留，请核对未确认任务后重试。":
    "Se conservan las operaciones confirmadas. Comprueba las tareas sin confirmar antes de reintentar.",
  本次应用导入进度: "Progreso de importación de las aplicaciones",
  正在导入: "Importando",
  已暂停: "En pausa",
  已中断: "Interrumpida",
  导入遇到问题: "Problema de importación",
  导入完成: "Importación completada",
  已取消: "Cancelada",
  已保存: "Guardada",
  onboardingAppProgress: "Progreso de importación de {name}",
  onboardingSavedMessages: "Mensajes guardados: {count}",
  onboardingScannedOutsideRange:
    "Mensajes examinados: {count}; no hay conversaciones dentro del periodo elegido",
  onboardingScannedNothingToSave:
    "Mensajes examinados: {count}; no hay conversaciones para guardar",
  onboardingScannedFilteringRecent:
    "Mensajes examinados: {count}; filtrando los últimos 3 días",
  onboardingScannedFiltering: "Mensajes examinados: {count}; filtrando",
  onboardingScannedNotSaved: "Mensajes examinados: {count}; aún sin guardar",
  没有可保存的历史对话: "No hay conversaciones del historial para guardar",
  状态待核对: "Estado pendiente de comprobar",
  读取授权不可用: "Autorización de lectura no disponible",
  onboardingReadErrors: " · Errores de lectura: {count}",
  "配置详情，按需要调整": "Ajusta la configuración según tus necesidades",
  "连接未通过，配置已保留": "La conexión falló; tu configuración se conserva",
  "选好模型，让记忆开始沉淀": "Elige un modelo para crear memorias",
  "连接设置已保存\n成功项保留，不重复执行。":
    "Se guardaron los ajustes de conexión.\nSe conservan los cambios completados, sin repetirlos.",
  "已选择：持续同步 · 一键接入":
    "Seleccionado: sincronización continua · Conexión con un clic",
  "沉淀记忆 · 使用你选择的模型": "Crea memorias · Con el modelo que elijas",
  "当前无法启用模型，已填写内容保留。":
    "No se puede habilitar el modelo por ahora. Los datos introducidos se conservan.",
  "模型连接测试通过，可以启用并开始沉淀记忆。":
    "La prueba de conexión fue satisfactoria. Puedes habilitar el modelo y empezar a crear memorias.",
  "先测试模型连通性；测试通过后再启用。":
    "Prueba primero la conexión del modelo. Habilítalo cuando la prueba sea satisfactoria.",
  "跳过，先进入应用": "Omitir por ahora",
  "正在测试模型…": "Probando el modelo…",
  测试模型连通性: "Probar conexión",
  "正在启用…": "Habilitando…",
  确认启用: "Confirmar y habilitar",
  核对提交状态: "Comprobar estado del envío",
  模型配置: "Configuración del modelo",
  模型参数: "Parámetros del modelo",
  "选择服务商，填入 API Key。核对已有配置后再继续。":
    "Selecciona un proveedor e introduce su API Key. Comprueba la configuración antes de continuar.",
  模型服务商: "Proveedor del modelo",
  选择服务商: "Seleccionar proveedor",
  "已安全保存，留空保留":
    "Guardada de forma segura; deja el campo vacío para conservarla",
  "输入服务商提供的 API Key": "Introduce la API Key del proveedor",
  "连接未通过，请检查 API Key、模型与接口地址后重试。配置已保留。":
    "La conexión falló. Comprueba la API Key, el modelo y la dirección del servicio antes de reintentar. La configuración se conserva.",
  "需要允许 MemHub 访问钥匙串。系统询问时请选择“始终允许”。":
    "MemHub necesita acceso al llavero. Cuando el sistema lo solicite, selecciona «Permitir siempre».",
  "留空保留已保存密钥；填写新密钥后需重新测试。":
    "Deja el campo vacío para conservar la clave guardada. Si introduces otra clave, deberás repetir la prueba.",
  "密钥保存在系统安全存储中，不写入对话或日志。":
    "La clave se guarda en el almacenamiento seguro del sistema, no en conversaciones ni registros.",
  配置详情与高级设置: "Detalles y configuración avanzada",
  服务商待选择: "Proveedor pendiente de seleccionar",
  模型待填写: "Modelo pendiente de introducir",
  本地向量模型: "Modelo de embeddings local",
  向量模型待填写: "Modelo de embeddings pendiente de introducir",
  思考已关闭: "Razonamiento desactivado",
  思考未关闭: "Razonamiento no desactivado",
  "预设已填好，只有自定义服务或特殊需求才需要修改。":
    "Los valores predefinidos ya están completos. Solo necesitas modificarlos para servicios personalizados o necesidades específicas.",
  返回服务商选择: "Volver a seleccionar proveedor",
  配置名称: "Nombre de la configuración",
  模型名称: "Nombre del modelo",
  协议: "Protocolo",
  "Anthropic（暂不支持）": "Anthropic (aún no compatible)",
  "HY 托管（当前不可用）": "HY administrado (no disponible)",
  向量模型: "Modelo de embeddings",
  "本地 ONNX · 内置": "ONNX local · Integrado",
  自定义向量: "Embeddings personalizados",
  "超时（秒）": "Tiempo de espera (segundos)",
  最大并发: "Concurrencia máxima",
  上下文窗口: "Ventana de contexto",
  "自动 / 可选": "Automática / Opcional",
  关闭思考模式: "Desactivar el modo de razonamiento",
  "自定义向量配置可展开；远程向量服务须遵守凭据边界。":
    "Puedes desplegar la configuración de embeddings personalizados. Los servicios remotos deben respetar los límites de las credenciales.",
  "切换服务商或模型后，需要重新测试并启用。":
    "Si cambias de proveedor o modelo, deberás repetir la prueba y habilitarlo de nuevo.",
  向量模型名称: "Nombre del modelo de embeddings",
  向量协议: "Protocolo de embeddings",
  "同源 OpenAI-compatible（共用 Key）":
    "OpenAI-compatible del mismo origen (clave compartida)",
  "本机 Ollama": "Ollama local",
  "向量 Base URL": "Base URL de embeddings",
  "远程向量地址须与生成服务同源；本机地址仍需校验。":
    "La dirección remota de embeddings debe compartir origen con el servicio de generación. Las direcciones locales también deben validarse.",
  核对创建结果: "Comprobar resultado de la creación",
  "放弃旧记录，重新配置":
    "Descartar el registro anterior y configurar de nuevo",
  模型提交状态: "Estado del envío del modelo",
  "正在测试连接…": "Probando la conexión…",
  "正在提交保存与启用，请等待回执…":
    "Enviando la solicitud de guardado y habilitación. Espera la confirmación…",
  "操作结果未确认，已填写内容保留，请核对后重试。":
    "El resultado de la operación no está confirmado. Los datos introducidos se conservan. Compruébalo antes de reintentar.",
  本次沉淀: "Creación de memorias",
  让记忆从这里开始: "Tus memorias empiezan aquí",
  对话来源: "Fuentes de conversaciones",
  尚无已选来源: "No hay fuentes seleccionadas",
  onboardingSourcesSummary: "{names} y otras aplicaciones ({count} en total)",
  onboardingImportedConversations:
    "Conversaciones importadas: {count}. Los controles de las fuentes siguen determinando qué contenido se sincroniza después en este dispositivo.",
  "处理服务：": "Servicio de procesamiento: ",
  待选择: "Pendiente de seleccionar",
  "相关对话会发送给所选服务商。":
    "Las conversaciones pertinentes se enviarán al proveedor seleccionado.",
  "测试连接只发送测试内容。启用后会使用此模型整理已导入和之后同步的会话。":
    "La prueba de conexión solo envía contenido de prueba. Al habilitarlo, este modelo procesará las conversaciones importadas y las que se sincronicen después.",
  "当前不能在此启用模型。": "No se puede habilitar el modelo aquí por ahora.",
  接口地址待核对: "Dirección del servicio pendiente de comprobar",
  "配置已修改，需要重新测试。":
    "La configuración cambió. Debes repetir la prueba.",
  onboardingIntegerRange: "Introduce un número entero entre {min} y {max}.",
  "请填写配置名称。": "Introduce un nombre para la configuración.",
  "请填写实际可用的模型 ID。": "Introduce un ID de modelo disponible.",
  "请填写有效的模型接口地址。":
    "Introduce una dirección válida para el servicio del modelo.",
  "请填写 API Key。": "Introduce una API Key.",
  "请填写向量模型 ID。": "Introduce el ID del modelo de embeddings.",
  "配置结果尚未确认，请在模型设置中核对后重试。":
    "El resultado de la configuración no está confirmado. Compruébalo en la configuración del modelo antes de reintentar.",
  "模型连接测试通过，可以启用。":
    "La prueba de conexión fue satisfactoria. Puedes habilitar el modelo.",
  "连接未通过，请核对模型、密钥和接收方。":
    "La conexión falló. Comprueba el modelo, la clave y el destinatario.",
  "配置或测试结果暂未确认，请核对后重试。":
    "El resultado de la configuración o la prueba aún no está confirmado. Compruébalo antes de reintentar.",
  自定义兼容服务: "Servicio compatible personalizado",
  所选连接已启用: "Se habilitaron las conexiones seleccionadas",
  本次未选择应用连接: "No se seleccionaron conexiones de aplicaciones",
  尚未填写接收方: "Destinatario pendiente de introducir",
  本机内置向量模型: "Modelo de embeddings integrado en este dispositivo",
  "操作未确认，草稿已保留。":
    "La operación no está confirmada. El borrador se conserva.",
  "操作已完成，但配置列表暂未刷新。已保留操作回执，请刷新核对，不要重复保存。":
    "La operación terminó, pero la lista no se actualizó. Se conservó la confirmación. Actualiza para comprobarla; no vuelvas a guardar.",
  "暂时无法刷新模型配置，已确认的配置仍保留。请重试刷新。":
    "No se puede actualizar la configuración de modelos. La configuración confirmada se conserva. Vuelve a actualizar.",
  "请先核对上次模型测试结果，暂不修改或启用配置。":
    "Comprueba primero el resultado de la última prueba. No modifiques ni habilites la configuración todavía.",
  "未找到上次创建记录。确认放弃旧记录后可重新配置；系统不会自动重试。":
    "No se encontró el registro de creación anterior. Puedes configurar de nuevo tras confirmar que lo descartas. No se reintentará automáticamente.",
  "创建结果仍待核对，不会重复创建。请稍后再次核对。":
    "El resultado de la creación sigue pendiente. No se repetirá la creación. Vuelve a comprobarlo más tarde.",
  "本地恢复记录已变化，仍保持锁定。请重新打开后核对。":
    "El registro local de recuperación cambió y sigue bloqueado. Vuelve a abrir para comprobarlo.",
  "已发现模型配置，旧创建记录仍保留。请先核对已有配置。":
    "Se encontró una configuración de modelo y se conserva el registro de creación anterior. Comprueba primero la configuración existente.",
  "旧创建记录尚未安全解除，配置仍保持锁定。请重新核对。":
    "El registro anterior aún no se ha liberado de forma segura. La configuración sigue bloqueada. Vuelve a comprobarlo.",
  "配置未保存，请检查模型地址和参数后重试；已填写内容保留。":
    "No se guardó la configuración. Comprueba la dirección y los parámetros del modelo antes de reintentar. Los datos introducidos se conservan.",
  "创建结果尚未确认，请核对已保存结果，不要重复提交。":
    "El resultado de la creación no está confirmado. Comprueba lo guardado; no repitas el envío.",
  "模型配置已变化，旧测试结果不会用于启用，请重新核对配置。":
    "La configuración del modelo cambió. No se usará la prueba anterior para habilitarlo. Comprueba de nuevo la configuración.",
  "暂时无法核对测试服务，本次未发送测试，请恢复连接后重试。":
    "No se puede comprobar el servicio de prueba. No se envió ninguna prueba. Restablece la conexión antes de reintentar.",
  "另一个模型的测试结果尚未确认，请先在该模型上核对测试结果。":
    "La prueba de otro modelo sigue sin confirmar. Comprueba primero el resultado de ese modelo.",
  "测试结果尚未确认。再次点击测试连接将核对原操作，不会重复发送测试。":
    "La prueba sigue sin confirmar. Al pulsar de nuevo se comprobará la operación original, sin repetir el envío.",
  "请先点击测试连接，通过后再设为当前模型。":
    "Prueba primero la conexión. Cuando sea satisfactoria, podrás seleccionar el modelo actual.",
  "这是当前正在使用的模型，请先切换到别的模型再删除。":
    "Este es el modelo en uso. Cambia a otro antes de eliminarlo.",
  "模型配置操作未成功，请核对输入后重试。":
    "La operación de configuración del modelo no se completó. Comprueba los datos antes de reintentar.",
  "它没有 MCP 通道，无法发放可撤销的记忆凭证":
    "No dispone de un canal MCP para emitir credenciales revocables de memorias",
  当前安装不支持一键插入: "Esta instalación no admite la conexión con un clic",
  桌面端的连接组件不可用:
    "El componente de conexión de escritorio no está disponible",
  安装后没有拿到安装回执: "No se recibió una confirmación después de instalar",
  记忆使用权限没有获批: "No se aprobó el permiso para usar memorias",
  配置文件被改过: "Se modificó el archivo de configuración",
  "有无法确认的残留，需要人工核对":
    "Quedan elementos sin confirmar que requieren revisión manual",
  权限存储暂不可用: "El almacenamiento de permisos no está disponible",
  应用控制通道暂不可用:
    "El canal de control de la aplicación no está disponible",
  "请先把 MemHub 拖到“应用程序”文件夹，再接入应用":
    "Arrastra primero MemHub a la carpeta Aplicaciones para conectar aplicaciones",
  本机运行时暂不可用: "El entorno de ejecución local no está disponible",
  桌面端读取接入状态超时:
    "Se agotó el tiempo de lectura del estado de conexión de escritorio",
} as const;

export const onboardingChineseMessages = {
  ...(Object.fromEntries(
    Object.keys(onboardingSpanishMessages).map((key) => [key, key]),
  ) as Record<keyof typeof onboardingSpanishMessages, string>),
  onboardingConnectionPartial:
    "{success} 个应用已接入，{failed} 个未完成：{names}。可进入应用后单独处理。",
  onboardingImportHistory: "导入 {name} 历史",
  onboardingMoreApps: "向下查看更多 · 还有 {count} 个应用",
  "onboardingMoreApps.one": "向下查看更多 · 还有 {count} 个应用",
  onboardingAllApps: "已显示全部 {count} 个应用",
  onboardingImportRefused: "尚未获准导入（{reason}）。请核对来源后重试。",
  onboardingConfirmHistory: "确认这是 {name} 的历史位置",
  onboardingHistoryWindow:
    "检测到会话数据量较大。为控制用量并保持各应用时间线一致，本次所有应用仅导入最近 3 天的对话。",
  onboardingBatchStopped: "已停止后续操作；已发出的操作仍按回执核对。",
  onboardingBatchResult: "已确认 {confirmed} 项，{failed} 项未确认。",
  onboardingAppProgress: "{name} 导入进度",
  onboardingSavedMessages: "已保存 {count} 条消息",
  onboardingScannedOutsideRange:
    "已扫描 {count} 条，本次没有符合时间范围的对话",
  onboardingScannedNothingToSave: "已扫描 {count} 条，本次没有可保存的对话",
  onboardingScannedFilteringRecent: "已扫描 {count} 条，正在筛选最近 3 天",
  onboardingScannedFiltering: "已扫描 {count} 条，正在筛选",
  onboardingScannedNotSaved: "已扫描 {count} 条，尚未保存",
  onboardingReadErrors: " · {count} 条读取异常",
  onboardingSourcesSummary: "{names} 等 {count} 个应用",
  onboardingImportedConversations:
    "已导入 {count} 条对话；来源开关继续控制之后同步到本机的内容。",
  onboardingIntegerRange: "请输入 {min} 至 {max} 的整数。",
  onboardingModelOperationFailed: "操作没成功：{code}",
  onboardingModelSkipDestination: "跳过后进入应用",
} as const;
