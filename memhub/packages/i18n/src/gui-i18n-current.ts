/**
 * Semantic messages for the current main-page surfaces that are not owned by
 * the established applications, workbench or memory catalogs.
 */
export const currentChineseMessages = {
  "connectionFallback.title": "连接其他工具",
  "connectionFallback.desktopOnly": "本机配对仅在桌面 App 中可用。",
  "connectionFallback.description":
    "为未列出的本机 Harness 生成一次性配对 Skill。纯网页聊天、远端容器或另一台机器不能直接连接本机。",
  "connectionFallback.name": "Harness 名称",
  "connectionFallback.namePlaceholder": "例如 Kimi Code",
  "connectionFallback.generating": "正在生成…",
  "connectionFallback.generate": "生成配对 Skill",
  "connectionFallback.generateFailed": "未能生成配对 Skill。",
  "connectionFallback.retry": "重试",
  "connectionFallback.generated":
    "已为 {name} 生成一次性配对 Skill，有效期至 {time}。",
  "connectionFallback.skillLabel": "配对 Skill",
  "connectionFallback.copy": "复制 Skill",
  "connectionFallback.copied":
    "已复制配对 Skill。请粘贴到对应 Harness 中执行。",
  "connectionFallback.copyFailed": "复制失败，可在上方选中文本复制。",
  "connectionFallback.localDescription":
    "仅限本机 MCP 或命令工具。纯网页聊天不能直接连接本机。",
  "connectionFallback.loading": "正在读取本机连接配置…",
  "connectionFallback.loadFailed": "未能读取连接配置。",
  "connectionFallback.format": "连接材料格式",
  "connectionFallback.promptTab": "连接提示词",
  "connectionFallback.mcpTab": "MCP 配置",
  "connectionFallback.commandTab": "命令",
  "connectionFallback.materialLabel": "本机连接材料",
  "connectionFallback.materialBoundary":
    "包含本机路径，不含记忆和密钥。粘贴前请核对，并保持 MemHub 运行。",
  "connectionFallback.materialCopied": "已复制，尚未建立连接。",
  "connectionFallback.copyPrompt": "复制提示词",
  "connectionFallback.copyConfig": "复制配置",
  "connectionFallback.copyCommand": "复制命令",
  "connectionFallback.promptTemplate":
    "请按以下 MCP 配置连接本机 MemHub：\n```json\n{mcp}```\n{shellBlock}\n连接后先执行一次真实记忆查询，并如实报告结果。",
  "connectionFallback.promptShell":
    "\n如果只支持本机 shell，请使用：\n```bash\n{shell}```\n",

  "sdk.memory.title": "SDK 记忆库",
  "sdk.memory.status": "记忆状态",
  "sdk.memory.status.active": "已可用",
  "sdk.memory.status.superseded": "已被取代",
  "sdk.memory.total": "共 {count} 条",
  "sdk.memory.total.one": "共 {count} 条",
  "sdk.memory.loadFailed": "记忆库读取失败，请重试。",
  "sdk.memory.detailLoadFailed": "记忆详情读取失败。",
  "sdk.memory.updateFailed": "修改未完成，请重试。",
  "sdk.memory.deleteFailed": "删除未完成，请重试。",
  "sdk.memory.loading": "正在读取记忆库…",
  "sdk.memory.pagination": "SDK 记忆库分页",
  "sdk.memory.previous": "上一页",
  "sdk.memory.next": "下一页",
  "sdk.memory.detail": "SDK 记忆详情",
  "sdk.memory.content": "记忆内容",
  "sdk.memory.save": "保存修改",
  "sdk.memory.delete": "删除记忆",

  "sdk.raw.title": "SDK 原始对话",
  "sdk.raw.searchLabel": "搜索原始对话",
  "sdk.raw.searching": "搜索中…",
  "sdk.raw.search": "搜索",
  "sdk.raw.loadFailed": "原始对话暂时无法读取。",
  "sdk.raw.searchFailed": "原始对话搜索失败，请重试。",
  "sdk.raw.recent": "最近原始对话",
  "sdk.raw.found": "找到 {count} 条",
  "sdk.raw.found.one": "找到 {count} 条",

  "memoryInspector.emptyHint": "查看它的来源、内容和修改历史。",
  "memory.operationFailed": "记忆操作失败（{code}）",
  "memorySpace.tabs.workbench": "工作台",

  "selectedMemory.status.confirmed": "已沉淀",
  "selectedMemory.status.active": "已生效",
  "selectedMemory.status.deleted": "已删除",
  "selectedMemory.status.superseded": "已被取代",
  "selectedMemory.status.shadow": "已隐藏",
  "selectedMemory.status.negated": "已否定",
  "selectedMemory.status.conflicted": "有冲突",
  "selectedMemory.status.archived": "已归档",
  "selectedMemory.status.unknown": "状态待核对",
  "selectedMemory.sourceUnknown": "来源待核对",
  "selectedMemory.discardDraft": "放弃尚未保存的修改？",
  "selectedMemory.newVersionReadFailed": "暂时无法读取新版本，草稿已保留。",
  "selectedMemory.updateRejected":
    "修改未提交；草稿已保留。请按提示修正后重试。",
  "selectedMemory.updateUnknown":
    "修改尚未确认，草稿已保留。请先刷新核对，避免重复提交。",
  "selectedMemory.deleteRejected": "删除未提交。请核对这条记忆的状态后重试。",
  "selectedMemory.deleteUnknown":
    "删除尚未确认。请重新核对记忆状态，不会自动扩大删除范围。",
  "selectedMemory.undoRejected": "撤销未提交。请按提示修正后重试。",
  "selectedMemory.undoUnknown":
    "撤销尚未确认，请先核对当前记忆版本，避免重复撤销。",
  "selectedMemory.author.you": "你说",
  "selectedMemory.author.assistant": "助手原话",
  "selectedMemory.author.unknown": "作者待核对",
  "selectedMemory.version": "第 {revision} 版",
  "selectedMemory.sourceLine": "来源 {source} · {time}",
  "selectedMemory.timestamps": "对话时间 {conversation} · 沉淀时间 {created}",
  "selectedMemory.edit": "编辑",
  "selectedMemory.evidence": "原始依据",
  "selectedMemory.evidenceLine": "{author} · 原对话 {time}：",
  "selectedMemory.evidenceUnavailable": "这段原文当前不可查看。",
  "selectedMemory.viewOriginal": "查看原文",
  "selectedMemory.history": "版本记录",
  "selectedMemory.more": "更多管理",
  "selectedMemory.undo": "撤销上次修改",
  "selectedMemory.delete": "删除记忆",
  "selectedMemory.editTitle": "编辑记忆",
  "selectedMemory.originalTitle": "原始对话",
  "selectedMemory.back": "返回记忆",
  "selectedMemory.editDescription": "修改这条记忆，原始对话保持不变。",
  "selectedMemory.content": "记忆内容",
  "selectedMemory.scopeUnchanged": "使用范围保持不变",
  "selectedMemory.conflictTitle": "已有新版本",
  "selectedMemory.conflictDescription":
    "你的草稿已保留，先查看新版本再决定如何修改。",
  "selectedMemory.viewNewVersion": "查看新版本",
  "selectedMemory.continueFromVersion": "基于此版本继续编辑",
  "selectedMemory.cancel": "取消",
  "selectedMemory.save": "保存修改",
  "selectedMemory.author.user": "你",
  "selectedMemory.author.agent": "助手",
  "selectedMemory.sourceUnavailable": "来源权限已变化或原文不可用。",
  "selectedMemory.related": "对应记忆",
  "selectedMemory.viewMemory": "查看记忆",
  "selectedMemory.deleteTitle": "删除这条记忆？",
  "selectedMemory.deleteDescription":
    "删除后不再用于新的召回，来源 App 中的原始对话不会删除。",
  "selectedMemory.sentCannotRecall": "已经发送给应用的内容无法远程收回。",
} as const;

export const currentEnglishMessages: Record<
  keyof typeof currentChineseMessages,
  string
> = {
  "connectionFallback.title": "Connect other tools",
  "connectionFallback.desktopOnly":
    "Local pairing is available only in the desktop app.",
  "connectionFallback.description":
    "Generate a one-time pairing Skill for a local Harness that is not listed. Browser chats, remote containers and other computers cannot connect directly to this device.",
  "connectionFallback.name": "Harness name",
  "connectionFallback.namePlaceholder": "For example, Kimi Code",
  "connectionFallback.generating": "Generating…",
  "connectionFallback.generate": "Generate pairing Skill",
  "connectionFallback.generateFailed": "Could not generate the pairing Skill.",
  "connectionFallback.retry": "Retry",
  "connectionFallback.generated":
    "Generated a one-time pairing Skill for {name}. It expires at {time}.",
  "connectionFallback.skillLabel": "Pairing Skill",
  "connectionFallback.copy": "Copy Skill",
  "connectionFallback.copied":
    "Pairing Skill copied. Paste and run it in the matching Harness.",
  "connectionFallback.copyFailed":
    "Copy failed. Select and copy the text above instead.",
  "connectionFallback.localDescription":
    "For local MCP or command-line tools only. Browser chats cannot connect directly to this device.",
  "connectionFallback.loading": "Loading local connection details…",
  "connectionFallback.loadFailed": "Could not load connection details.",
  "connectionFallback.format": "Connection details format",
  "connectionFallback.promptTab": "Connection prompt",
  "connectionFallback.mcpTab": "MCP configuration",
  "connectionFallback.commandTab": "Command",
  "connectionFallback.materialLabel": "Local connection details",
  "connectionFallback.materialBoundary":
    "Contains local paths, but no memories or keys. Review it before pasting and keep MemHub running.",
  "connectionFallback.materialCopied":
    "Copied. No connection has been made yet.",
  "connectionFallback.copyPrompt": "Copy prompt",
  "connectionFallback.copyConfig": "Copy configuration",
  "connectionFallback.copyCommand": "Copy command",
  "connectionFallback.promptTemplate":
    "Connect this local Harness to MemHub with the following MCP configuration:\n```json\n{mcp}```\n{shellBlock}\nAfter connecting, run a real memory query and report the result accurately.",
  "connectionFallback.promptShell":
    "\nIf only local shell commands are supported, use:\n```bash\n{shell}```\n",

  "sdk.memory.title": "SDK memory library",
  "sdk.memory.status": "Memory status",
  "sdk.memory.status.active": "Available",
  "sdk.memory.status.superseded": "Superseded",
  "sdk.memory.total": "{count} memories",
  "sdk.memory.total.one": "{count} memory",
  "sdk.memory.loadFailed": "Could not load the memory library. Please retry.",
  "sdk.memory.detailLoadFailed": "Could not load the memory details.",
  "sdk.memory.updateFailed": "Could not save the change. Please retry.",
  "sdk.memory.deleteFailed": "Could not delete the memory. Please retry.",
  "sdk.memory.loading": "Loading memory library…",
  "sdk.memory.pagination": "SDK memory library pages",
  "sdk.memory.previous": "Previous page",
  "sdk.memory.next": "Next page",
  "sdk.memory.detail": "SDK memory details",
  "sdk.memory.content": "Memory content",
  "sdk.memory.save": "Save changes",
  "sdk.memory.delete": "Delete memory",

  "sdk.raw.title": "SDK original conversations",
  "sdk.raw.searchLabel": "Search original conversations",
  "sdk.raw.searching": "Searching…",
  "sdk.raw.search": "Search",
  "sdk.raw.loadFailed": "Original conversations are temporarily unavailable.",
  "sdk.raw.searchFailed":
    "Could not search original conversations. Please retry.",
  "sdk.raw.recent": "Recent original conversations",
  "sdk.raw.found": "{count} results",
  "sdk.raw.found.one": "{count} result",

  "memoryInspector.emptyHint": "See its sources, content and change history.",
  "memory.operationFailed": "Memory operation failed ({code})",
  "memorySpace.tabs.workbench": "Workbench",

  "selectedMemory.status.confirmed": "Created",
  "selectedMemory.status.active": "Active",
  "selectedMemory.status.deleted": "Deleted",
  "selectedMemory.status.superseded": "Superseded",
  "selectedMemory.status.shadow": "Hidden",
  "selectedMemory.status.negated": "Negated",
  "selectedMemory.status.conflicted": "Conflicted",
  "selectedMemory.status.archived": "Archived",
  "selectedMemory.status.unknown": "Status needs review",
  "selectedMemory.sourceUnknown": "Source needs review",
  "selectedMemory.discardDraft": "Discard unsaved changes?",
  "selectedMemory.newVersionReadFailed":
    "Could not load the new version. Your draft is retained.",
  "selectedMemory.updateRejected":
    "Changes were not submitted. Your draft is retained; fix the issue and retry.",
  "selectedMemory.updateUnknown":
    "Changes are unconfirmed. Your draft is retained; refresh before retrying.",
  "selectedMemory.deleteRejected":
    "Deletion was not submitted. Review this memory and retry.",
  "selectedMemory.deleteUnknown":
    "Deletion is unconfirmed. Review the memory; the deletion scope will not expand.",
  "selectedMemory.undoRejected":
    "Undo was not submitted. Fix the issue and retry.",
  "selectedMemory.undoUnknown":
    "Undo is unconfirmed. Review the current version before retrying.",
  "selectedMemory.author.you": "You said",
  "selectedMemory.author.assistant": "Original assistant message",
  "selectedMemory.author.unknown": "Author needs review",
  "selectedMemory.version": "Version {revision}",
  "selectedMemory.sourceLine": "Source {source} · {time}",
  "selectedMemory.timestamps":
    "Conversation time {conversation} · Created {created}",
  "selectedMemory.edit": "Edit",
  "selectedMemory.evidence": "Original evidence",
  "selectedMemory.evidenceLine": "{author} · Original conversation {time}:",
  "selectedMemory.evidenceUnavailable":
    "This original text is currently unavailable.",
  "selectedMemory.viewOriginal": "View original",
  "selectedMemory.history": "Version history",
  "selectedMemory.more": "More actions",
  "selectedMemory.undo": "Undo last change",
  "selectedMemory.delete": "Delete memory",
  "selectedMemory.editTitle": "Edit memory",
  "selectedMemory.originalTitle": "Original conversation",
  "selectedMemory.back": "Back to memory",
  "selectedMemory.editDescription":
    "Edit this memory; the original conversation stays unchanged.",
  "selectedMemory.content": "Memory content",
  "selectedMemory.scopeUnchanged": "Access scope stays unchanged",
  "selectedMemory.conflictTitle": "A newer version exists",
  "selectedMemory.conflictDescription":
    "Your draft is retained. Review the new version before editing.",
  "selectedMemory.viewNewVersion": "View new version",
  "selectedMemory.continueFromVersion": "Continue editing from this version",
  "selectedMemory.cancel": "Cancel",
  "selectedMemory.save": "Save changes",
  "selectedMemory.author.user": "You",
  "selectedMemory.author.agent": "Assistant",
  "selectedMemory.sourceUnavailable":
    "Source permissions changed or the original text is unavailable.",
  "selectedMemory.related": "Related memory",
  "selectedMemory.viewMemory": "View memory",
  "selectedMemory.deleteTitle": "Delete this memory?",
  "selectedMemory.deleteDescription":
    "It will no longer be used for new lookups. The source app conversation will not be deleted.",
  "selectedMemory.sentCannotRecall":
    "Content already sent to apps cannot be recalled remotely.",
};

export const currentSpanishMessages: Record<
  keyof typeof currentChineseMessages,
  string
> = {
  "connectionFallback.title": "Conectar otras herramientas",
  "connectionFallback.desktopOnly":
    "El emparejamiento local solo está disponible en la aplicación de escritorio.",
  "connectionFallback.description":
    "Genera una Skill de emparejamiento de un solo uso para un Harness local que no aparece en la lista. Los chats web, contenedores remotos y otros equipos no pueden conectarse directamente a este dispositivo.",
  "connectionFallback.name": "Nombre del Harness",
  "connectionFallback.namePlaceholder": "Por ejemplo, Kimi Code",
  "connectionFallback.generating": "Generando…",
  "connectionFallback.generate": "Generar Skill de emparejamiento",
  "connectionFallback.generateFailed":
    "No se pudo generar la Skill de emparejamiento.",
  "connectionFallback.retry": "Reintentar",
  "connectionFallback.generated":
    "Se generó una Skill de emparejamiento de un solo uso para {name}. Caduca a las {time}.",
  "connectionFallback.skillLabel": "Skill de emparejamiento",
  "connectionFallback.copy": "Copiar Skill",
  "connectionFallback.copied":
    "Skill copiada. Pégala y ejecútala en el Harness correspondiente.",
  "connectionFallback.copyFailed":
    "No se pudo copiar. Selecciona y copia el texto de arriba.",
  "connectionFallback.localDescription":
    "Solo para herramientas MCP o comandos locales. Los chats web no pueden conectarse directamente a este dispositivo.",
  "connectionFallback.loading": "Leyendo la configuración de conexión local…",
  "connectionFallback.loadFailed":
    "No se pudo leer la configuración de conexión.",
  "connectionFallback.format": "Formato del material de conexión",
  "connectionFallback.promptTab": "Instrucciones de conexión",
  "connectionFallback.mcpTab": "Configuración MCP",
  "connectionFallback.commandTab": "Comando",
  "connectionFallback.materialLabel": "Material de conexión local",
  "connectionFallback.materialBoundary":
    "Incluye rutas locales, pero no memorias ni claves. Revísalo antes de pegarlo y mantén MemHub en ejecución.",
  "connectionFallback.materialCopied":
    "Copiado. La conexión todavía no se ha establecido.",
  "connectionFallback.copyPrompt": "Copiar instrucciones",
  "connectionFallback.copyConfig": "Copiar configuración",
  "connectionFallback.copyCommand": "Copiar comando",
  "connectionFallback.promptTemplate":
    "Conecta este Harness local con MemHub mediante la siguiente configuración MCP. No crees un proxy público:\n```json\n{mcp}```\n{shellBlock}\nTras conectar, realiza una consulta real de memoria.\n\nVer una herramienta no equivale a tener autorización. Ante una denegación, no reintentes ni amplíes permisos. No cambies de herramienta, transporte, configuración o alcance para eludir una denegación.\n\nSi MemHub está desconectado o falla, indica que no está disponible, sin afirmar éxito ni inventar citas; si la cobertura es parcial, dilo y conserva la limitación. No presentes una cobertura parcial sin resultados como recuperación exitosa.\n\nSi ambas rutas están vacías, indica únicamente «He completado la consulta de memorias de MemHub». Un resultado vacío no demuestra que se haya buscado en todo el historial.\n\nCita solo fuentes devueltas y realmente usadas, atribuyendo fuente, autor, fecha e identificadores. Recuperar resultados no demuestra que la respuesta los haya utilizado.\n\nEn chino, usa «正在使用 MemHub 查询记忆».",
  "connectionFallback.promptShell":
    "\nSi solo admites shell local, usa este comando:\n```bash\n{shell}```\n",

  "sdk.memory.title": "Biblioteca de memorias del SDK",
  "sdk.memory.status": "Estado de la memoria",
  "sdk.memory.status.active": "Disponible",
  "sdk.memory.status.superseded": "Sustituida",
  "sdk.memory.total": "{count} memorias",
  "sdk.memory.total.one": "{count} memoria",
  "sdk.memory.loadFailed":
    "No se pudo cargar la biblioteca de memorias. Reinténtalo.",
  "sdk.memory.detailLoadFailed":
    "No se pudieron cargar los detalles de la memoria.",
  "sdk.memory.updateFailed": "No se pudo guardar el cambio. Reinténtalo.",
  "sdk.memory.deleteFailed": "No se pudo eliminar la memoria. Reinténtalo.",
  "sdk.memory.loading": "Cargando la biblioteca de memorias…",
  "sdk.memory.pagination": "Páginas de la biblioteca de memorias del SDK",
  "sdk.memory.previous": "Página anterior",
  "sdk.memory.next": "Página siguiente",
  "sdk.memory.detail": "Detalles de la memoria del SDK",
  "sdk.memory.content": "Contenido de la memoria",
  "sdk.memory.save": "Guardar cambios",
  "sdk.memory.delete": "Eliminar memoria",

  "sdk.raw.title": "Conversaciones originales del SDK",
  "sdk.raw.searchLabel": "Buscar conversaciones originales",
  "sdk.raw.searching": "Buscando…",
  "sdk.raw.search": "Buscar",
  "sdk.raw.loadFailed":
    "Las conversaciones originales no están disponibles por ahora.",
  "sdk.raw.searchFailed":
    "No se pudieron buscar las conversaciones originales. Reinténtalo.",
  "sdk.raw.recent": "Conversaciones originales recientes",
  "sdk.raw.found": "{count} resultados",
  "sdk.raw.found.one": "{count} resultado",

  "memoryInspector.emptyHint":
    "Consulta su origen, contenido e historial de cambios.",
  "memory.operationFailed": "Falló la operación de memoria ({code})",
  "memorySpace.tabs.workbench": "Espacio de trabajo",

  "selectedMemory.status.confirmed": "Guardada",
  "selectedMemory.status.active": "Activa",
  "selectedMemory.status.deleted": "Eliminada",
  "selectedMemory.status.superseded": "Sustituida",
  "selectedMemory.status.shadow": "Oculta",
  "selectedMemory.status.negated": "Negada",
  "selectedMemory.status.conflicted": "En conflicto",
  "selectedMemory.status.archived": "Archivada",
  "selectedMemory.status.unknown": "Estado pendiente de comprobar",
  "selectedMemory.sourceUnknown": "Origen sin verificar",
  "selectedMemory.discardDraft": "¿Descartar los cambios sin guardar?",
  "selectedMemory.newVersionReadFailed":
    "No se pudo leer la nueva versión. Tu borrador se conserva.",
  "selectedMemory.updateRejected":
    "Los cambios no se enviaron. Tu borrador se conserva; corrige el problema y reintenta.",
  "selectedMemory.updateUnknown":
    "Los cambios no están confirmados. Tu borrador se conserva; actualiza antes de reintentar.",
  "selectedMemory.deleteRejected":
    "La eliminación no se envió. Revisa esta memoria y reintenta.",
  "selectedMemory.deleteUnknown":
    "La eliminación no está confirmada. Revisa la memoria; el alcance no se ampliará.",
  "selectedMemory.undoRejected":
    "No se envió la acción de deshacer. Corrige el problema y reintenta.",
  "selectedMemory.undoUnknown":
    "La acción de deshacer no está confirmada. Revisa la versión actual antes de reintentar.",
  "selectedMemory.author.you": "Tus palabras",
  "selectedMemory.author.assistant": "Palabras originales del asistente",
  "selectedMemory.author.unknown": "Autor sin verificar",
  "selectedMemory.version": "Versión {revision}",
  "selectedMemory.sourceLine": "Origen {source} · {time}",
  "selectedMemory.timestamps":
    "Conversación {conversation} · Guardada {created}",
  "selectedMemory.edit": "Editar",
  "selectedMemory.evidence": "Evidencia original",
  "selectedMemory.evidenceLine": "{author} · Conversación original {time}:",
  "selectedMemory.evidenceUnavailable":
    "Este texto original no está disponible.",
  "selectedMemory.viewOriginal": "Ver texto original",
  "selectedMemory.history": "Historial de versiones",
  "selectedMemory.more": "Más opciones",
  "selectedMemory.undo": "Deshacer último cambio",
  "selectedMemory.delete": "Eliminar memoria",
  "selectedMemory.editTitle": "Editar memoria",
  "selectedMemory.originalTitle": "Conversación original",
  "selectedMemory.back": "Volver a la memoria",
  "selectedMemory.editDescription":
    "Edita esta memoria sin modificar la conversación original.",
  "selectedMemory.content": "Contenido de la memoria",
  "selectedMemory.scopeUnchanged": "El ámbito de uso no cambia",
  "selectedMemory.conflictTitle": "Hay una nueva versión",
  "selectedMemory.conflictDescription":
    "Tu borrador se conserva. Revisa la nueva versión antes de editar.",
  "selectedMemory.viewNewVersion": "Ver nueva versión",
  "selectedMemory.continueFromVersion": "Continuar editando desde esta versión",
  "selectedMemory.cancel": "Cancelar",
  "selectedMemory.save": "Guardar cambios",
  "selectedMemory.author.user": "Tú",
  "selectedMemory.author.agent": "Asistente",
  "selectedMemory.sourceUnavailable":
    "Los permisos del origen cambiaron o el texto no está disponible.",
  "selectedMemory.related": "Memoria correspondiente",
  "selectedMemory.viewMemory": "Ver memoria",
  "selectedMemory.deleteTitle": "¿Eliminar esta memoria?",
  "selectedMemory.deleteDescription":
    "Dejará de usarse en nuevas consultas. La conversación de la aplicación de origen no se eliminará.",
  "selectedMemory.sentCannotRecall":
    "No es posible retirar el contenido ya enviado a las aplicaciones.",
};
