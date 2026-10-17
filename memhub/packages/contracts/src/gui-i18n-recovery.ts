/** Recovery, diagnostics and native chrome. Never translate diagnostic payloads. */
export const recoveryChineseMessages = {
  startupReadError: "暂时无法读取启动状态，请重试。",
  startupRetry: "重新读取",
  startupOpening: "正在打开 MemHub…",
  nativeHelp: "帮助",
  nativeExportLogs: "导出日志（发给开发排查）",
  nativeExportError: "日志未能导出，请稍后重试。",
  nativeLogin: "登录时启动 {product}",
  nativeTrialUnavailable: "（阶段包不可用）",
  nativeCliManaged: "（由 CLI 管理）",
  nativeStatusUnavailable: "（状态不可用）",
  nativeDevelopmentUnavailable: "（开发模式不可用）",
  nativeOpen: "打开 {product}",
  nativeQuit: "退出 {product}（后台服务继续运行）",
  nativeLoginError: "无法更改登录启动设置，请重试。",
  nativeMoveTitle: "请先将 MemHub 拖到“应用程序”文件夹",
  nativeMoveDetail:
    "从“应用程序”重新打开后，MemHub 才会接入其他应用并安装命令行工具。",
  nativeAcknowledge: "知道了",
  graphWidthTitle: "现有记忆库和内置模型对不上",
  graphWidthDescription:
    "你现在的记忆库按 {stored} 维建的，内置模型出的是 {expected} 维。两者接不上，不选一个的话抽取会一直失败。",
  graphWidthKeep:
    "保留现有记忆库（默认）：继续用原来的 embedding 配置，{count} 条记忆和关系图都不动",
  graphWidthRebuild:
    "改用内置模型：现有关系图会被归档（不删除），{count} 条记忆的关系需要重新建一遍",
  graphWidthExport: "先导出再决定：现在什么都不动，导出之后再回来选",
  graphWidthExported: "已导出到 {path}。原库没有动，现在可以再选一次。",
  graphWidthUnknown: "宽度选择尚未确认，现有记忆库未被改动。请重试。",
  graphWidthConfirm: "就这么办",
  importUserProgress:
    "正在导入你的历史对话，已导入 {count} 个会话。记忆会陆续生成，你可以先正常使用。",
  importUserModel: "模型还没有配置好，新的对话暂时不会整理成记忆。",
  importUserIncomplete: "有些内容没有处理完成，请前往设置查看。",
  importUserSettings: "去设置",
  diagnosticExportDesktop: "请在桌面 App 中导出诊断信息。",
  diagnosticExported: "日志已导出：{filename}",
  diagnosticFile: "诊断文件",
  diagnosticExportFailed: "诊断信息未能导出，请重试。",
  debugTitle: "Debug 日志",
  debugCount: "{count} 条，本次启动",
  debugPause: "暂停实时刷新",
  debugResume: "继续实时刷新",
  debugRefresh: "立即刷新",
  debugClose: "关闭 Debug 日志",
  debugSearchPlaceholder: "搜索事件、组件、错误码或内容",
  debugSearch: "搜索日志",
  debugSource: "日志来源",
  debugAllSources: "全部来源",
  debugCapture: "采集级别",
  debugDisplay: "显示级别",
  debugAll: "全部",
  debugMatches: "{count} 条匹配",
  debugLive: "实时刷新",
  debugPaused: "已暂停",
  debugTruncated: "日志过多，已截断",
  debugReadError: "读取失败",
  debugLoading: "正在读取日志…",
  debugEmpty: "没有匹配的日志",
  debugOpened: "Debug 日志已打开",
  debugOpen: "打开 Debug 日志",
} as const;

export const recoverySpanishMessages: Record<
  keyof typeof recoveryChineseMessages,
  string
> = {
  startupReadError: "No se pudo leer el estado de inicio. Inténtalo de nuevo.",
  startupRetry: "Volver a leer",
  startupOpening: "Abriendo MemHub…",
  nativeHelp: "Ayuda",
  nativeExportLogs: "Exportar registros (para diagnóstico)",
  nativeExportError:
    "No se pudieron exportar los registros. Inténtalo más tarde.",
  nativeLogin: "Iniciar {product} al iniciar sesión",
  nativeTrialUnavailable: " (no disponible en esta versión de prueba)",
  nativeCliManaged: " (administrado por CLI)",
  nativeStatusUnavailable: " (estado no disponible)",
  nativeDevelopmentUnavailable: " (no disponible en desarrollo)",
  nativeOpen: "Abrir {product}",
  nativeQuit: "Salir de {product} (el servicio sigue en segundo plano)",
  nativeLoginError:
    "No se pudo cambiar el inicio de sesión. Inténtalo de nuevo.",
  nativeMoveTitle: "Arrastra MemHub a la carpeta Aplicaciones primero",
  nativeMoveDetail:
    "Abre MemHub desde Aplicaciones para conectarlo con otras aplicaciones e instalar las herramientas de línea de comandos.",
  nativeAcknowledge: "Entendido",
  graphWidthTitle: "La biblioteca y el modelo integrado no coinciden",
  graphWidthDescription:
    "La biblioteca usa {stored} dimensiones y el modelo integrado produce {expected}. Son incompatibles: la extracción seguirá fallando hasta que elijas una opción.",
  graphWidthKeep:
    "Conservar la biblioteca actual (predeterminado): seguir con la configuración de embedding anterior; no se modifican las {count} memorias ni el grafo",
  graphWidthRebuild:
    "Usar el modelo integrado: el grafo actual se archivará (no se eliminará); será necesario reconstruir las relaciones de {count} memorias",
  graphWidthExport:
    "Exportar antes de decidir: no cambiar nada por ahora; volver a elegir después de exportar",
  graphWidthExported:
    "Se exportó a {path}. La biblioteca original no cambió; ahora puedes volver a elegir.",
  graphWidthUnknown:
    "La elección de dimensiones aún no está confirmada; la biblioteca actual no se ha modificado. Inténtalo de nuevo.",
  graphWidthConfirm: "Confirmar esta elección",
  importUserProgress:
    "Importando tus conversaciones anteriores: {count} conversaciones importadas. Las memorias aparecerán gradualmente; puedes seguir usando la aplicación.",
  importUserModel:
    "El modelo aún no está configurado; las conversaciones nuevas no se convertirán en memorias por ahora.",
  importUserIncomplete:
    "Parte del contenido no terminó de procesarse. Consulta la configuración.",
  importUserSettings: "Ir a configuración",
  diagnosticExportDesktop:
    "Exporta el diagnóstico desde la aplicación de escritorio.",
  diagnosticExported: "Registros exportados: {filename}",
  diagnosticFile: "archivo de diagnóstico",
  diagnosticExportFailed:
    "No se pudo exportar el diagnóstico. Inténtalo de nuevo.",
  debugTitle: "Registros de depuración",
  debugCount: "{count} registros en este inicio",
  debugPause: "Pausar actualización en vivo",
  debugResume: "Reanudar actualización en vivo",
  debugRefresh: "Actualizar ahora",
  debugClose: "Cerrar registros de depuración",
  debugSearchPlaceholder:
    "Buscar eventos, componentes, códigos de error o contenido",
  debugSearch: "Buscar registros",
  debugSource: "Origen de los registros",
  debugAllSources: "Todos los orígenes",
  debugCapture: "Nivel de captura",
  debugDisplay: "Nivel mostrado",
  debugAll: "Todos",
  debugMatches: "{count} coincidencias",
  debugLive: "Actualización en vivo",
  debugPaused: "En pausa",
  debugTruncated: "Demasiados registros; se truncaron",
  debugReadError: "No se pudieron leer los registros",
  debugLoading: "Leyendo registros…",
  debugEmpty: "No hay registros coincidentes",
  debugOpened: "Registros de depuración abiertos",
  debugOpen: "Abrir registros de depuración",
};
