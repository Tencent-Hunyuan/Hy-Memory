import type { GuiLocale } from "@one-memory/contracts/gui-i18n";
import { useGuiLanguage } from "./gui-language";

// Presentation-only catalogue: never translate imported content, names or error codes.
const messages = {
  apps: ["应用", "Aplicaciones"],
  allApps: ["全部应用", "Todas las aplicaciones"],
  home: ["应用首页", "Inicio de aplicaciones"],
  back: ["返回首页", "Volver al inicio"],
  overview: ["记忆概览", "Resumen de memorias"],
  memories: ["已沉淀记忆", "Memorias guardadas"],
  readingCount: ["正在读取已沉淀记忆数量…", "Leyendo el total de memorias…"],
  unreadable: ["暂无法读取", "No disponible"],
  unit: ["条", ""],
  scope: ["按应用授权范围使用", "Según los permisos de cada aplicación"],
  processing: ["沉淀状态", "Procesamiento"],
  modelOff: ["模型读取已关闭", "Lectura desactivada"],
  running: ["整理中", "Organizando"],
  queued: ["排队处理中", "En cola"],
  waitingModel: ["等待模型", "Esperando modelo"],
  needsAttention: ["需要处理", "Requiere atención"],
  preparing: ["准备中", "Preparando"],
  ready: ["一切就绪", "Todo listo"],
  unchecked: ["待核对", "Sin verificar"],
  noNewMemories: [
    "新的对话不会再整理成记忆",
    "Las nuevas conversaciones no generarán memorias",
  ],
  organizingNew: ["新的对话正在整理", "Organizando nuevas conversaciones"],
  waitingQueue: ["等待队列开始处理", "Esperando el inicio del procesamiento"],
  currentState: ["以当前处理状态为准", "Estado actual del procesamiento"],
  modelPermission: ["打开模型内容权限", "Permisos del modelo"],
  recentUpdate: ["最近更新", "Última actualización"],
  notGenerated: ["尚未生成", "Sin generar"],
  saved: ["记忆已保存", "Memoria guardada"],
  separate: [
    "历史与记忆分别处理",
    "Historial y memorias se procesan por separado",
  ],
  recentCall: ["最近调用", "Última consulta"],
  activityUnavailable: ["动态暂不可用", "Actividad no disponible"],
  firstBatch: ["首批沉淀", "Primeras memorias"],
  callsUnavailable: ["暂时无法读取调用记录", "No se pueden leer las consultas"],
  memoryUnavailable: [
    "暂时无法读取记忆状态",
    "No se puede leer el estado de las memorias",
  ],
  importing: [
    "对话持续导入中，即将沉淀你的记忆",
    "Importando conversaciones para generar memorias",
  ],
  firstRunning: ["正在沉淀第一批记忆", "Generando las primeras memorias"],
  firstQueued: ["准备沉淀第一批记忆", "Preparando las primeras memorias"],
  readingActivity: ["正在读取动态", "Leyendo actividad"],
  emptyMemories: ["还没有已生成的记忆", "Aún no hay memorias generadas"],
  callReadFailure: [
    "已有记忆仍可查看；重试不会发起新的调用。",
    "Puedes ver las memorias guardadas. Reintentar no inicia otra consulta.",
  ],
  memoryReadFailure: [
    "读取失败不会当作没有记忆。",
    "Un error de lectura no significa que no haya memorias.",
  ],
  savingConversations: [
    "对话正在后台持续保存。",
    "Las conversaciones se guardan en segundo plano.",
  ],
  waitingMemories: [
    "历史已保存，记忆生成后会显示在这里。",
    "El historial está guardado. Las memorias aparecerán cuando se generen.",
  ],
  reread: ["重新读取", "Reintentar lectura"],
  viewCalls: ["查看记录", "Ver consultas"],
  viewMemory: ["查看记忆", "Ver memoria"],
  backgroundDown: [
    "后台服务没有运行，应用暂时查不到记忆",
    "El servicio está detenido; las aplicaciones no pueden consultar memorias",
  ],
  restart: ["重新启动", "Reiniciar"],
  runtime: ["运行状态", "Estado del servicio"],
  backgroundRunning: ["后台运行中", "Servicio activo"],
  connectionUnchecked: ["连接状态待核对", "Conexión sin verificar"],
  disconnected: ["服务未连接", "Servicio desconectado"],
  authorizedOnly: ["仅处理已授权范围", "Solo procesa contenido autorizado"],
  sync: ["对话同步", "Sincronización"],
  organizing: ["记忆整理", "Memorias"],
  access: ["应用访问", "Acceso de aplicaciones"],
  checkingAccess: ["应用接入核对", "Verificación de acceso"],
  checking: ["核对中…", "Verificando…"],
  recheck: ["重新核对", "Volver a verificar"],
  accessState: ["应用接入状态", "Estado del acceso"],
  staleAccess: [
    "应用接入状态暂时读取失败，当前为上次状态。",
    "No se pudo actualizar el acceso. Se muestra el último estado conocido.",
  ],
  reconnect: ["重新接入", "Volver a conectar"],
  myApps: ["我的应用", "Mis aplicaciones"],
  list: ["应用列表", "Lista de aplicaciones"],
  syncEnabled: ["持续同步已开启", "Sincronización continua activada"],
  recallAllowed: ["允许请求已授权记忆", "Puede consultar memorias autorizadas"],
  historyAllowed: ["历史读取已授权", "Lectura del historial autorizada"],
  connectionPending: ["连接待完成", "Conexión pendiente"],
  details: ["查看详情", "Ver detalles"],
  listUnavailable: [
    "应用列表暂时不可读取",
    "No se puede leer la lista de aplicaciones",
  ],
  emptyApps: ["还没有可显示的应用", "Aún no hay aplicaciones para mostrar"],
  checkingNow: ["正在核对中", "Verificando"],
  checkingState: ["正在检查状态", "Comprobando estado"],
  notEnabled: ["尚未开启", "Desactivada"],
  enableFailed: ["未能开启", "No se pudo activar"],
  unknownTime: ["时间未知", "Hora desconocida"],
  today: ["今天 ", "Hoy "],
  newMemory: ["新增记忆", "Nueva memoria"],
  updatedMemory: ["更新记忆", "Memoria actualizada"],
  unlinked: ["尚未关联", "Sin vincular"],
  lastRead: ["上次读取：", "Última lectura: "],
  recentSync: ["最近同步 {time}", "Última sincronización: {time}"],
  connectedCount: ["{count} 个应用已接入", "{count} aplicaciones conectadas"],
  pendingCount: ["{count} 个应用待核对", "{count} aplicaciones sin verificar"],
  mixedCount: [
    "{count} 个应用已接入，{pending} 个待核对",
    "{count} conectadas; {pending} sin verificar",
  ],
  unknownAccess: [
    "应用接入状态暂时没有读到，无法确认有几个应用已接入。",
    "No se pudo leer el acceso; no se puede confirmar cuántas aplicaciones están conectadas.",
  ],
  unknownPermissions: [
    "{names} 的记忆权限暂时没有读到，无法确认是否已接入。",
    "No se pudieron leer los permisos de {names}; su conexión no está confirmada.",
  ],
  enabledCount: ["{count} 个应用已开启", "{count} aplicaciones activadas"],
  appCount: ["{count} 个应用", "{count} aplicaciones"],
  viewAll: ["查看全部 {count} 个应用", "Ver las {count} aplicaciones"],
  checkedAt: ["上次核对：{time}", "Última verificación: {time}"],
  revision: ["版本 {count}", "Versión {count}"],
  conversationTime: ["对话时间", "Hora de conversación"],
  importTime: ["导入时间", "Hora de importación"],
  incompleteCalls: [
    " · 有几次调用没被记下来，列表可能不全",
    " · Algunas consultas no se registraron; la lista puede estar incompleta",
  ],
  timeoutWarning: [
    "{host} 最近几次没查到记忆，后台可能正忙，稍后会自动恢复",
    "Las últimas consultas de {host} no se completaron. El servicio puede estar ocupado; se recuperará automáticamente.",
  ],
  autoHostFailed: [
    "{name} 未能自动接入{reason}。",
    "No se pudo conectar {name} automáticamente{reason}. ",
  ],
  autoSyncFailed: [
    "{name} 的持续同步未能自动开启。{reason}",
    "No se pudo activar la sincronización continua de {name}. {reason}",
  ],
  noAutoRetry: ["不会自动重试。", "No se reintentará automáticamente."],
  cardUnavailable: [
    "{label}暂时显示不了，其他功能不受影响，稍后会自动恢复。",
    "{label} no está disponible. Las demás funciones siguen activas; se recuperará automáticamente.",
  ],
  navMemories: ["记忆", "Memorias"],
  navigation: ["主要导航", "Navegación principal"],
  skip: ["跳到主要内容", "Saltar al contenido principal"],
  local: ["本地存储", "Almacenamiento local"],
  localHint: [
    "记忆保存在本机；使用云模型整理或交给已授权的助手时，相关内容会发送给对应服务。",
    "Las memorias se guardan en este equipo. Al usar modelos en la nube o asistentes autorizados, el contenido pertinente se envía al servicio correspondiente.",
  ],
} as const;
export type ApplicationsMessage = keyof typeof messages;
export function applicationsText(
  locale: GuiLocale,
  key: ApplicationsMessage,
  params?: Record<string, string | number>,
): string {
  const singular: Partial<Record<ApplicationsMessage, string>> = {
    connectedCount: "{count} aplicación conectada",
    pendingCount: "{count} aplicación sin verificar",
    enabledCount: "{count} aplicación activada",
    appCount: "{count} aplicación",
    viewAll: "Ver la aplicación",
  };
  const message =
    locale === "es" && params?.count === 1 && singular[key]
      ? singular[key]
      : messages[key][locale === "es" ? 1 : 0];
  return message.replace(/\{(\w+)\}/g, (match, name: string) =>
    params && Object.hasOwn(params, name) ? String(params[name]) : match,
  );
}
export function useApplicationsLanguage() {
  const { locale } = useGuiLanguage();
  return {
    locale,
    a: (key: ApplicationsMessage, params?: Record<string, string | number>) =>
      applicationsText(locale, key, params),
  };
}
