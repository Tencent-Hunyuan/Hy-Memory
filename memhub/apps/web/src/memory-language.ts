import type { GuiLocale } from "@one-memory/contracts/gui-i18n";
import { useGuiLanguage } from "./gui-language";

// Only explicit interface copy belongs here. Imported text is never passed in.
const spanish: Record<string, string> = {
  记忆抽取: "Extracción de memorias",
  关系归纳: "Síntesis de relaciones",
  深度整理: "Procesamiento profundo",
  跨域整理: "Procesamiento entre áreas",
  当前模型已变化: "El modelo actual cambió",
  本地会话内容已变化: "El contenido de las conversaciones locales cambió",
  "这段对话含过长原文，暂未整理；原文已保留":
    "Esta conversación contiene texto demasiado largo y no se procesó; el original se conservó",
  记忆库分页: "Paginación de la biblioteca de memorias",
  上一页: "Página anterior",
  下一页: "Página siguiente",
  等待可用生成模型: "Esperando un modelo disponible",
  还没有启用生成模型: "No hay un modelo de generación activo",
  "缺少生成模型 API Key": "Falta la clave API del modelo",
  模型凭据暂不可用: "Credenciales del modelo no disponibles",
  本机凭据存储暂不可用: "Almacén local de credenciales no disponible",
  "需要授权访问本机钥匙串。请重新打开 App 并完成一次授权":
    "Se necesita permiso para acceder al llavero. Vuelve a abrir la aplicación y completa la autorización",
  "读取本机钥匙串超时。重新保存一遍模型配置即可恢复":
    "La lectura del llavero agotó el tiempo. Vuelve a guardar la configuración del modelo",
  "写入本机钥匙串超时。重新保存一遍模型配置即可恢复":
    "La escritura del llavero agotó el tiempo. Vuelve a guardar la configuración del modelo",
  "模型认证失败。更新 key 后，到设置里点一次检测，会自动接着处理":
    "Falló la autenticación. Actualiza la clave y comprueba la conexión en Configuración para continuar",
  "模型账户余额不足。充值或换 key 后，到设置里点一次检测，会自动接着处理":
    "Saldo del proveedor insuficiente. Recarga o cambia la clave y comprueba la conexión en Configuración",
  "模型服务拒绝访问。更新权限后，到设置里点一次检测，会自动接着处理":
    "El proveedor rechazó el acceso. Actualiza los permisos y comprueba la conexión en Configuración",
  模型服务限流: "Límite de solicitudes del proveedor",
  模型服务暂不可用: "Servicio del modelo no disponible",
  模型服务响应超时: "El modelo agotó el tiempo de respuesta",
  "模型请求不兼容，请核对配置":
    "Solicitud incompatible; revisa la configuración",
  模型请求未成功: "La solicitud al modelo no se completó",
  "HY Memory 不可用": "HY Memory no está disponible",
  "HY Memory 启动失败": "HY Memory no pudo iniciarse",

  查看已读内容: "Ver contenido leído",
  已保存原文: "Texto original guardado",
  刷新已读内容: "Actualizar contenido leído",
  "正在核对本机已保存记录…": "Verificando registros guardados en este equipo…",
  原始消息: "Mensajes originales",
  会话: "Conversaciones",
  用户发言: "Mensajes del usuario",
  "Agent 发言": "Mensajes del agente",
  作者未知: "Autor desconocido",
  原始时间未知: "Fecha original desconocida",
  工具记录: "Registros de herramientas",
  "原始时间来自已保存记录。":
    "Las fechas originales proceden de los registros guardados.",
  已保存的原文样例: "Ejemplos de texto original guardado",
  "最多展示 3 段，不代表全部历史或推荐记忆。":
    "Se muestran hasta 3 fragmentos, no el historial completo ni memorias recomendadas.",
  查看完整对话: "Ver conversación completa",
  "原文较长，此处仅展示前 600 个字符。":
    "Texto largo: se muestran los primeros 600 caracteres.",
  "这个来源还没有保存原文。":
    "Aún no hay texto original guardado de este origen.",
  完整对话: "Conversación completa",
  关闭完整对话: "Cerrar conversación completa",
  用户: "Usuario",
  "暂时无法读取已保存内容，旧回执已收起。请检查授权或重试。":
    "No se puede leer el contenido guardado. Se ocultó el informe anterior; revisa los permisos o reintenta.",
  "这段原文暂不可读，可能已被撤回授权。请刷新回执。":
    "Este texto no está disponible; es posible que se hayan revocado sus permisos. Actualiza el informe.",

  外部记忆召回范围: "Ámbito de consulta externa",
  外部记忆召回: "Consulta externa de memorias",
  调整召回范围: "Ajustar ámbito de consulta",
  所有关联工具: "Todas las herramientas vinculadas",
  已停止外部记忆召回: "Consulta externa desactivada",
  原有限定范围: "Ámbito limitado original",
  选择召回范围: "Elegir ámbito de consulta",
  停止外部记忆召回: "Desactivar consulta externa",
  "正在保存…": "Guardando…",
  保存召回范围: "Guardar ámbito de consulta",
  取消范围修改: "Cancelar cambio de ámbito",
  "原文检索仍按来源授权；已带出的内容不会撤回。":
    "La búsqueda del texto original sigue los permisos del origen. El contenido ya enviado no se retira.",
  "记忆已更新，请重新打开这条记忆后再调整范围。":
    "La memoria cambió. Vuelve a abrirla antes de ajustar el ámbito.",
  "范围修改未提交，当前范围保持不变。请修正后重试。":
    "No se envió el cambio; se conserva el ámbito actual. Corrige el problema y reintenta.",
  "保存结果尚未确认，请重试。": "El guardado no está confirmado. Reinténtalo.",
  "原话已保存在本机；当前内容、模型接收方或授权版本尚未通过核对，未继续整理。":
    "El texto está guardado en este equipo. No se continuó el procesamiento porque falta verificar el contenido, el modelo receptor o la versión de autorización.",
  "HY 未返回可核对的已有记忆，未创建替代内容；原始记录已保留，可重试处理。":
    "HY no devolvió memorias verificables. No se creó contenido sustituto; los originales se conservan y puedes reintentar.",
  "历史引用尚未绑定到原始来源，当前批次未完成，原话已保留。":
    "Las referencias históricas aún no están vinculadas a su origen. El lote está incompleto; el texto se conserva.",
  "记忆内容未通过来源与作者核验，当前批次未完成，原话已保留。":
    "No se pudo verificar el origen y autor de la memoria. El lote está incompleto; el texto se conserva.",
  "所选记忆已不可读取，请重新读取记忆库。":
    "La memoria seleccionada ya no está disponible. Actualiza la biblioteca.",
  "还没连上本地 Core，正在重试…": "Conectando con Core local; reintentando…",
  "记忆内容或使用权限已变化，回答已停止。请重新提问。":
    "El contenido o los permisos cambiaron. La respuesta se detuvo; vuelve a preguntar.",
  "这条记忆的来源授权已撤回，暂时不能读取。":
    "Se revocó el permiso del origen de esta memoria; no se puede leer.",
  "记忆已被更新，请刷新后重试。":
    "La memoria cambió. Actualiza antes de reintentar.",
  "已删除的记忆不能修改。": "No se puede editar una memoria eliminada.",
  "这条记忆已经删除。": "Esta memoria ya se eliminó.",
  "这条记忆已被更新的记忆取代，只能查看或删除。":
    "Esta memoria fue sustituida; solo puedes verla o eliminarla.",
  "没有更早的修改可以撤销。": "No hay cambios anteriores que deshacer.",
  "来源原话已不存在，无法建立这条记忆。":
    "El texto original ya no existe; no se puede crear la memoria.",
  "这次操作与已完成的请求不一致。":
    "La operación no coincide con la solicitud ya completada.",
  "输入内容不符合要求。": "El contenido no cumple los requisitos.",
  "本地记忆库正忙，请稍后重试。":
    "La biblioteca local está ocupada. Reinténtalo más tarde.",

  记忆: "Memorias",
  记忆空间: "Espacio de memorias",
  全部应用: "Todas las aplicaciones",
  来源: "Origen",
  主题: "Temas",
  归纳: "Patrones",
  全部记忆: "Todas las memorias",
  来源待核对: "Origen sin verificar",
  所选来源待核对: "Origen seleccionado sin verificar",
  未知来源: "Origen desconocido",
  记忆读取失败: "Error al leer las memorias",
  "正在读取…": "Leyendo…",
  "正在更新…": "Actualizando…",
  读取失败: "Error de lectura",
  "正在核对来源…": "Verificando orígenes…",
  "正在导入历史会话，记忆将陆续出现":
    "Importando el historial; las memorias aparecerán progresivamente",
  记忆工作台: "Panel de memorias",
  来源与主题: "Orígenes y temas",
  记忆或原始对话: "Memorias o conversaciones originales",
  原始对话: "Conversaciones originales",
  地图组织方式: "Organización del mapa",
  筛选地图记忆: "Filtrar memorias del mapa",
  搜索原始对话: "Buscar conversaciones originales",
  搜索记忆: "Buscar memorias",
  "筛选记忆…": "Filtrar memorias…",
  刷新地图记忆: "Actualizar memorias",
  关闭新增记忆提示: "Cerrar aviso de nuevas memorias",
  地图记忆列表: "Lista de memorias del mapa",
  "记忆读取失败，请重试。": "No se pudieron leer las memorias. Reinténtalo.",
  重新读取地图记忆: "Volver a leer las memorias",
  没有匹配的记忆: "No hay memorias que coincidan",
  本页没有匹配的主题记忆: "No hay memorias de este tema en la página",
  还没有已生成的记忆: "Aún no hay memorias generadas",
  地图记忆分页: "Paginación de memorias",
  上一页记忆: "Página anterior de memorias",
  下一页记忆: "Página siguiente de memorias",
  记忆关系图: "Grafo de memorias",
  "主题视角（非事项）": "Vista por temas",
  来源视角: "Vista por origen",
  记忆关联: "Relaciones entre memorias",
  "刚导入的原话，还不是沉淀后的记忆":
    "Conversaciones importadas, aún sin convertir en memorias",
  来源归属: "Relación con el origen",
  可在沉淀记忆后查看视图:
    "La vista estará disponible cuando se generen memorias",
  查看选中来源原文: "Ver texto del origen seleccionado",
  查看选中记忆原文: "Ver texto de la memoria seleccionada",
  查看原文: "Ver texto original",
  整理已有关系: "Organizar relaciones existentes",
  "正在读取原文…": "Leyendo texto original…",
  选择一条原始对话: "Selecciona una conversación original",
  记忆暂时无法读取: "Las memorias no están disponibles",
  当前筛选没有可显示的记忆: "No hay memorias para este filtro",
  "正在读取记忆…": "Leyendo memorias…",
  已保存的对话还未生成记忆:
    "Las conversaciones guardadas aún no han generado memorias",
  还没有可显示的记忆: "Aún no hay memorias para mostrar",
  连线表示来源归属: "Las líneas indican el origen",
  记忆详情: "Detalles de la memoria",
  来源原文: "Texto original",
  关联节点: "Nodo relacionado",
  关闭记忆详情: "Cerrar detalles de la memoria",
  关闭详情: "Cerrar detalles",
  "正在读取这条记忆…": "Leyendo esta memoria…",
  "这条记忆的原文暂时无法读取，选择已保留。":
    "No se puede leer el texto original. La selección se conserva.",
  重试读取原文: "Reintentar lectura del original",
  已沉淀的记忆: "Memorias guardadas",
  "正在读取记忆详情…": "Leyendo detalles…",
  沉淀记忆后查看详情:
    "Los detalles estarán disponibles cuando se generen memorias",
  时间未知: "Hora desconocida",
  来源原文已不可读取: "El texto original ya no está disponible",
  关联记忆: "Memorias relacionadas",
  "正在读取完整对话…": "Leyendo conversación completa…",
  重试读取完整对话: "Reintentar lectura de la conversación",
  看完整对话: "Ver conversación completa",
  今天: "Hoy",
  你: "Tú",
  助手: "Asistente",
  工具: "Herramienta",
  未知: "Desconocido",
  作者待核对: "Autor sin verificar",
  你说: "Tus palabras",
  助手原话: "Palabras del asistente",
  已沉淀: "Guardada",
  已删除: "Eliminada",
  已被取代: "Sustituida",
  状态待核对: "Estado sin verificar",
  编辑: "Editar",
  原始依据: "Evidencia original",
  "这段原文当前不可查看。": "Este texto original no está disponible.",
  版本记录: "Historial de versiones",
  更多管理: "Más opciones",
  撤销上次修改: "Deshacer último cambio",
  删除记忆: "Eliminar memoria",
  编辑记忆: "Editar memoria",
  返回记忆: "Volver a la memoria",
  "修改这条记忆，原始对话保持不变。":
    "Edita esta memoria sin modificar la conversación original.",
  记忆内容: "Contenido de la memoria",
  使用范围保持不变: "El ámbito de uso no cambia",
  已有新版本: "Hay una nueva versión",
  "你的草稿已保留，先查看新版本再决定如何修改。":
    "Tu borrador se conserva. Revisa la nueva versión antes de editar.",
  查看新版本: "Ver nueva versión",
  基于此版本继续编辑: "Continuar editando esta versión",
  取消: "Cancelar",
  保存修改: "Guardar cambios",
  对应记忆: "Memoria correspondiente",
  查看记忆: "Ver memoria",
  "删除这条记忆？": "¿Eliminar esta memoria?",
  "删除后不再用于新的召回，来源 App 中的原始对话不会删除。":
    "Dejará de usarse en nuevas consultas. La conversación original de la aplicación no se eliminará.",
  "已经发送给应用的内容无法远程收回。":
    "No es posible retirar el contenido ya enviado a otras aplicaciones.",
  "放弃尚未保存的修改？": "¿Descartar los cambios sin guardar?",
  "来源权限已变化或原文不可用。":
    "Los permisos del origen han cambiado o el texto no está disponible.",
  "暂时无法读取新版本，草稿已保留。":
    "No se pudo leer la nueva versión. Tu borrador se conserva.",
  "修改未提交；草稿已保留。请按提示修正后重试。":
    "Los cambios no se enviaron. Tu borrador se conserva; corrígelo y reinténtalo.",
  "修改尚未确认，草稿已保留。请先刷新核对，避免重复提交。":
    "Los cambios no están confirmados. Tu borrador se conserva; actualiza antes de volver a enviarlo.",
  "删除未提交。请核对这条记忆的状态后重试。":
    "La eliminación no se envió. Verifica el estado de la memoria antes de reintentar.",
  "删除尚未确认。请重新核对记忆状态，不会自动扩大删除范围。":
    "La eliminación no está confirmada. Verifica el estado; no se eliminará ningún contenido adicional automáticamente.",
  "撤销未提交。请按提示修正后重试。":
    "No se envió la acción de deshacer. Corrige el problema y reintenta.",
  "撤销尚未确认，请先核对当前记忆版本，避免重复撤销。":
    "La acción de deshacer no está confirmada. Verifica la versión antes de reintentar.",
  添加记忆: "Añadir memoria",
  关闭新增记忆: "Cerrar nueva memoria",
  关闭: "Cerrar",
  记下这件事: "¿Qué quieres recordar?",
  "例如：项目周会不排在周五。":
    "Por ejemplo: no programar reuniones del proyecto los viernes.",
  "整理模型：": "Modelo: ",
  "尚未配置，原话仍可保存": "Sin configurar; puedes guardar el texto",
  正在读取: "Leyendo",
  配置模型: "Configurar modelo",
  "保存中…": "Guardando…",
  保存原话: "Guardar texto",
  最近添加: "Añadidas recientemente",
  重新整理: "Volver a procesar",
  "原话已保存，等待可用模型": "Texto guardado; esperando un modelo",
  "原话已保存，等待整理": "Texto guardado; pendiente de procesar",
  正在整理: "Procesando",
  "原话已保存，整理未完成": "Texto guardado; procesamiento incompleto",
  整理已完成: "Procesamiento completado",
  "原话已保存，未提取出可用记忆":
    "Texto guardado; no se extrajeron memorias utilizables",
  "最近添加的内容读取失败。": "No se pudieron leer las notas recientes.",
  "整理状态暂时不可用，原话已保存。":
    "Estado del procesamiento no disponible. El texto está guardado.",
  "已保存，正在后台沉淀记忆": "Guardado; generando memorias en segundo plano",
  "未能确认保存结果，输入已保留。重试不会重复添加。":
    "No se pudo confirmar el guardado. Tu texto se conserva; reintentar no lo duplicará.",
  "整理未能开始，原话仍已保存。":
    "No se pudo iniciar el procesamiento. El texto sigue guardado.",
  "整理结果尚未确认，原话仍已保存。请刷新后核对。":
    "El resultado no está confirmado. El texto sigue guardado; actualiza para verificarlo.",
  "模型服务要求检查余额或付款状态，原话仍已保存。":
    "Revisa el saldo o los pagos del proveedor. El texto sigue guardado.",
  "这台设备打不开 3D 视图，已显示关联列表。":
    "La vista 3D no está disponible en este equipo. Se muestran las relaciones en una lista.",
  图谱工具: "Herramientas del grafo",
  放大: "Acercar",
  放大图谱: "Acercar grafo",
  缩小: "Alejar",
  缩小图谱: "Alejar grafo",
  重置视角: "Restablecer vista",
  重置图谱视角: "Restablecer vista del grafo",
  查看读取详情: "Ver detalles de lectura",
  配置整理模型: "Configurar modelo",
  原文暂时无法读取: "Texto original no disponible",
  "暂时无法读取这段对话，已保留当前记忆。":
    "No se puede leer la conversación. La memoria actual se conserva.",
  "已清除筛选，定位到关联记忆。":
    "Filtros eliminados; memoria relacionada localizada.",
  "所选记忆不在当前筛选内，已取消选择。":
    "La memoria seleccionada no está en este filtro. Se canceló la selección.",
};
export function memoryText(locale: GuiLocale, message: string): string {
  return locale === "es" ? (spanish[message] ?? message) : message;
}
export function useMemoryLanguage() {
  const { locale } = useGuiLanguage();
  return {
    locale,
    text: (message: string) => memoryText(locale, message),
    count: (value: number) => new Intl.NumberFormat(locale).format(value),
  };
}
