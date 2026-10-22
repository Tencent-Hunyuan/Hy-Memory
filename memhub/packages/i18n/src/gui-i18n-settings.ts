/** GUI copy only: never apply this catalog to persisted memories or provider data. */
export const settingsSpanishMessages = {
  "这里需要 API Key，不是网址。请填写服务方提供的密钥。":
    "Aquí se necesita una clave API, no una URL. Introduce la clave del proveedor.",
  "服务商拒绝了这个 API Key（401）。请确认密钥属于当前填写的服务地址。":
    "El proveedor rechazó la clave API (401). Comprueba que corresponda a la dirección del servicio.",
  "服务商没有这个模型名，请对照服务商的模型列表核对。":
    "El proveedor no reconoce este modelo. Comprueba su lista de modelos.",
  "连不上接口地址，请检查网络和服务地址是否正确。":
    "No se pudo conectar al servicio. Comprueba la red y la dirección.",
  未提供接口地址: "No se indicó una dirección del servicio",
  "请填写生成模型名。": "Introduce el identificador del modelo de generación.",
  "请填写完整的 http:// 或 https:// 接口地址。":
    "Introduce una dirección completa del servicio con http:// o https://.",
  "请填写向量模型名。": "Introduce el identificador del modelo de embeddings.",
  "请填写完整的向量模型接口地址。":
    "Introduce la dirección completa del servicio de embeddings.",
  "这份配置在测试期间被改过，刚才测的是改动后的版本。配置已保存但未启用，请重新测试并启用。":
    "La configuración cambió durante la prueba; se probó la versión modificada. Está guardada, pero no habilitada. Vuelve a probarla y habilitarla.",
  "测试结果来自这份配置更早的版本。配置已保存但未启用，请重新测试并启用。":
    "La prueba corresponde a una versión anterior. La configuración está guardada, pero no habilitada. Vuelve a probarla y habilitarla.",
  "配置已保存，连接测试未通过。请核对配置后重试，历史对话尚未发送。":
    "Configuración guardada; la prueba de conexión falló. Compruébala y reintenta. No se han enviado conversaciones anteriores.",
  "配置已保存，尚未确认启用成功。请核对配置后重试。":
    "Configuración guardada; la activación aún no está confirmada. Compruébala y reintenta.",
  "配置尚未完成测试和启用，请核对后重试。":
    "La prueba y activación no se completaron. Comprueba la configuración y reintenta.",
  "配置尚未保存成功，已填写内容保留，可核对后重试。":
    "No se confirmó el guardado. Se conserva lo introducido; compruébalo y reintenta.",
  "只需配置生成模型。向量嵌入已内置在本机，不为 System 1、System 2 分开选模型。":
    "Solo necesitas configurar el modelo de generación. Los embeddings están integrados localmente; System 1 y System 2 no requieren modelos separados.",
  "无法读取测试恢复记录，暂不发送新的测试。":
    "No se pudo leer el registro de recuperación de pruebas. No se enviarán nuevas pruebas por ahora.",
  "上次模型测试结果待核对，暂不修改或启用配置。":
    "El resultado de la última prueba está pendiente. No se modificará ni habilitará la configuración por ahora.",
  核对测试结果: "Comprobar resultado de la prueba",
  "上次模型创建结果待核对，暂不开始新的操作。":
    "La última creación está pendiente de comprobar. No se iniciarán nuevas operaciones por ahora.",
  核对创建结果: "Comprobar resultado de la creación",
  "配置已保存，正在测试连接…": "Configuración guardada. Probando conexión…",
  "连接通过，正在启用模型…": "Conexión correcta. Habilitando modelo…",
  刷新配置列表: "Actualizar configuraciones",
  自定义一个模型: "Configurar un modelo personalizado",
  已保存的模型配置: "Configuración de modelo guardada",
  "已保存，已启用": "Guardada y habilitada",
  "已保存，连接通过": "Guardada; conexión correcta",
  "已保存，测试未通过": "Guardada; prueba fallida",
  "已保存，待测试": "Guardada; pendiente de prueba",
  版本: "Versión",
  "需要允许 MemHub 访问钥匙串。系统询问时请选择“始终允许”；已保存的配置不会丢失。":
    "MemHub necesita acceso al llavero. Cuando el sistema lo solicite, selecciona «Permitir siempre». La configuración guardada se conservará.",
  "测试只向此接口发送测试内容，不发送历史对话。":
    "La prueba solo envía contenido de prueba a este servicio, no conversaciones anteriores.",
  重试测试并启用: "Reintentar prueba y habilitar",
  测试并启用: "Probar y habilitar",
  编辑配置: "Editar configuración",
  新增配置: "Añadir configuración",
  编辑: "Editar",
  "。修改后需重新测试并设为当前模型，内容授权不会自动更新。":
    ". Después de modificar, vuelve a probar y seleccionar el modelo actual. La autorización de contenido no se actualizará automáticamente.",
  "配置已在其他位置变化，草稿已保留。请取消编辑后重新打开核对。":
    "La configuración cambió en otro lugar. Se conserva el borrador. Cancela la edición y vuelve a abrirla para comprobarla.",
  服务商预设: "Preajuste del proveedor",
  自定义兼容服务: "Servicio compatible personalizado",
  "只是帮你填好接口地址和一个模型名，不代表这个模型当前可用。 请对照服务商的模型列表核对后再保存。":
    "Solo completa la dirección y un identificador de modelo; no garantiza su disponibilidad. Comprueba la lista del proveedor antes de guardar.",
  协议: "Protocolo",
  "与模型服务提供的接口协议保持一致。":
    "Debe coincidir con el protocolo del servicio del modelo.",
  配置名称: "Nombre de la configuración",
  "用于区分配置，不影响模型请求。":
    "Sirve para distinguir configuraciones; no afecta a las solicitudes al modelo.",
  生成模型名: "Identificador del modelo de generación",
  "例如：gpt-4o-mini…": "Por ejemplo: gpt-4o-mini…",
  "填写服务方提供的完整模型标识。":
    "Introduce el identificador completo que proporciona el proveedor.",
  "高级：自行配置向量模型": "Avanzado: configurar embeddings",
  向量配置模式: "Modo de configuración de embeddings",
  本地内置: "Integrado localmente",
  自定义向量模型: "Modelo de embeddings personalizado",
  向量模型名: "Identificador del modelo de embeddings",
  "例如：text-embedding-3-small…": "Por ejemplo: text-embedding-3-small…",
  向量模型来源: "Proveedor de embeddings",
  "本机 Ollama": "Ollama local",
  "同源 OpenAI-compatible（共用 API Key）":
    "OpenAI-compatible del mismo origen (comparte clave API)",
  "向量模型 Base URL": "Base URL de embeddings",
  "为避免凭据外泄，向量地址必须与生成模型同源。":
    "Para evitar filtraciones de credenciales, la dirección de embeddings debe tener el mismo origen que la del modelo de generación.",
  "使用内置本地向量模型；不会发送内容给远程向量服务。":
    "Usa los embeddings locales integrados. No se envía contenido a un servicio remoto de embeddings.",
  自定义向量: "Embeddings personalizados",
  尚未填写模型: "Modelo sin indicar",
  尚未填写地址: "Dirección sin indicar",
  "填写服务接口地址，不是聊天网页地址。":
    "Introduce la dirección del servicio, no la página web del chat.",
  "（可选）": " (opcional)",
  "保存后不会再次显示…": "No volverá a mostrarse después de guardar…",
  "留空保留已保存密钥；填写新密钥后需重新测试和启用。":
    "Déjala vacía para conservar la clave guardada. Si introduces otra, vuelve a probar y habilitar.",
  "换了服务商后需要填写新的 API Key，已保存的密钥不会带到新地址。":
    "Al cambiar de proveedor necesitas otra clave API. La clave guardada no se enviará a la nueva dirección.",
  "保存在系统安全存储中，不随安装包分发。":
    "Se guarda en el almacén seguro del sistema; no se distribuye con la aplicación.",
  "关闭思考链（向支持的厂商下发 thinking-off 参数）":
    "Desactivar razonamiento (envía thinking-off a proveedores compatibles)",
  保存修改: "Guardar cambios",
  保存配置: "Guardar configuración",
  "正在启用配置…": "Habilitando configuración…",
  测试并完成配置: "Probar y completar configuración",
  保存并测试启用: "Guardar, probar y habilitar",
  取消编辑: "Cancelar edición",
  "密钥保存在系统安全存储；测试仅发送测试内容。":
    "La clave se guarda en el almacén seguro del sistema. La prueba solo envía contenido de prueba.",
  当前使用: "En uso",
  未启用: "No habilitada",
  测试目标: "Destino de prueba",
  "。仅发送测试内容，不发送历史对话。":
    ". Solo se envía contenido de prueba, no conversaciones anteriores.",
  测试连接: "Probar conexión",
  设为当前模型: "Seleccionar como modelo actual",
  删除: "Eliminar",
  "粘贴 API Key": "Pegar clave API",
  "密钥尚未保存成功，已填写内容保留，可重试。":
    "No se confirmó el guardado de la clave. Se conserva lo introducido; puedes reintentar.",
  生成接收方: "Destinatario de generación",
  向量接收方: "Destinatario de embeddings",
  本机: "Este dispositivo",
  "。启用此配置后按上方选择处理历史及未来允许来源。":
    ". Al habilitar, se procesarán las fuentes históricas y futuras autorizadas según la selección anterior.",
  "保存 key": "Guardar clave",
  "还没有 key？去申请": "¿No tienes clave? Solicítala",
  删除这张卡: "Eliminar esta configuración",
  "密钥已配置，当前读取状态待确认。":
    "Clave configurada; su lectura está pendiente de confirmar.",
  "等待 key：其余都已经填好了，贴上 key 就能用。":
    "Falta la clave; los demás campos ya están completos.",
  "key 已保存，还没测过这个模型能不能用。":
    "Clave guardada; el modelo todavía no se ha probado.",
  "Ollama（本机）": "Ollama (local)",
  "内置 bge-small-zh-v1.5": "Integrado: bge-small-zh-v1.5",
  通过: "Correcta",
  "HY 托管模型尚不可用": "El modelo administrado de HY no está disponible",
  "缺少 API Key": "Falta la clave API",
  服务不可达: "Servicio inaccesible",
  "认证失败；更新 key 后再检测，会自动接着处理":
    "Autenticación fallida. Actualiza la clave y vuelve a comprobar para continuar el procesamiento",
  请求频率受限: "Límite de solicitudes alcanzado",
  "模型账户余额不足；充值或换 key 后再检测，会自动接着处理":
    "Saldo insuficiente. Recarga o cambia la clave y comprueba de nuevo para continuar el procesamiento",
  没有找到模型: "Modelo no encontrado",
  "该网关不提供模型列表，已改由生成调用验证":
    "Este servicio no ofrece una lista de modelos; se verificó mediante generación",
  服务返回不符合协议: "Respuesta incompatible con el protocolo",
  结构化输出未通过: "La salida estructurada no superó la prueba",
  "需要填写 Embedding 模型": "Es necesario indicar un modelo de embeddings",
  "Embedding 模型不可用": "Modelo de embeddings no disponible",
  "请先核对上次模型测试结果，暂不修改或启用配置。":
    "Comprueba primero el resultado de la última prueba. No modifiques ni habilites la configuración todavía.",
  "本地恢复记录已变化，仍保持锁定。请重新打开后核对。":
    "El registro local de recuperación cambió y sigue bloqueado. Vuelve a abrir para comprobarlo.",
  测试结果: "Resultado de la prueba",
  "暂不支持一键插入：它没有 MCP 通道，无法发放可撤销的记忆凭证。":
    "No admite la conexión con un clic: no tiene canal MCP y no se pueden emitir credenciales revocables de memoria.",
  应用控制通道暂不可用:
    "El canal de control de la aplicación no está disponible",
  // Fragments appended to an app failure; Spanish carries its own separator.
  appFailureReason: " Motivo: {reason}.",
  appFailureReread:
    " Tampoco se pudo volver a leer la conexión; el estado mostrado podría no estar actualizado.",
  模型: "Modelo",
  连接其他应用: "Conectar otras aplicaciones",
  帮助与诊断: "Ayuda y diagnóstico",
  设置分组: "Secciones de configuración",
  记忆模型: "Modelo de memorias",
  当前模型: "Modelo actual",
  "正在读取模型…": "Leyendo el modelo…",
  模型状态暂不可读: "Estado del modelo no disponible",
  尚未配置模型: "No hay un modelo configurado",
  修改模型: "Modificar modelo",
  配置模型: "Configurar modelo",
  刷新模型状态: "Actualizar estado del modelo",
  "本机 / 受管端点": "Servicio local / administrado",
  接口地址待核对: "Dirección del servicio pendiente de comprobar",
  "模型状态未能读取，请重试。":
    "No se pudo leer el estado del modelo. Reintenta.",
  "模型状态刷新未成功，仍显示上次确认的模型状态；请重试。":
    "No se pudo actualizar el estado. Se muestra el último estado confirmado; reintenta.",
  "上次模型测试或创建还待核对，配置暂时不能改动。请先在下方模型配置区核对结果； 现有配置和原文不会被改动。":
    "La última prueba o creación del modelo está pendiente de comprobar, así que la configuración no se puede modificar por ahora. Compruébala primero en la sección de configuración del modelo, más abajo; no se modificarán la configuración ni los textos originales.",
  用模型沉淀记忆: "Generar memorias con el modelo",
  "开启后，导入和同步到本机的会话会交给当前模型整理成记忆，会产生模型调用。 停止后不再调用模型、不再整理记忆，但会话仍会照常导入和保存；已排队的内容会留着，重新开启后继续。 模型配置始终保留，重新开启不需要重新填写。":
    "Al activarlo, las conversaciones importadas y sincronizadas en este equipo se envían al modelo actual para convertirlas en memorias, lo que genera llamadas al modelo. Al detenerlo, ya no se llama al modelo ni se procesan memorias, pero las conversaciones se siguen importando y guardando; lo que está en cola se conserva y continúa al volver a activarlo. La configuración del modelo se conserva siempre; no hace falta volver a introducirla.",
  "启用未能确认，仍显示上次确认的状态；请重试。":
    "No se pudo confirmar la activación; se muestra el último estado confirmado. Reinténtalo.",
  "停止未能确认，模型可能仍在整理记忆，请重试。":
    "No se pudo confirmar la detención; es posible que el modelo siga procesando memorias. Reinténtalo.",
  "当前安装还不能读取这个开关，请确认后台服务已启动后重试。":
    "Esta instalación aún no puede leer este interruptor. Comprueba que el servicio en segundo plano esté activo y reinténtalo.",
  关于: "Acerca de",
  "此应用还没有读取授权，请先授权它的对话目录。":
    "Esta aplicación aún no tiene permiso de lectura. Autoriza primero su carpeta de conversaciones.",
  "此应用的读取授权已撤销，请重新授权。":
    "Se revocó el permiso de lectura de esta aplicación. Vuelve a autorizarla.",
  "此应用的对话目录已被重建，请重新授权。":
    "La carpeta de conversaciones de esta aplicación se volvió a crear. Vuelve a autorizarla.",
  "此应用的对话目录现在读不到，请确认应用已安装并产生过对话。":
    "No se puede leer la carpeta de conversaciones de esta aplicación. Comprueba que esté instalada y que tenga conversaciones.",
  "此应用的对话目录已变更，请按最新目录重新授权。":
    "La carpeta de conversaciones de esta aplicación cambió. Vuelve a autorizarla con la carpeta actual.",
  "此应用还没有读取授权，请先授权它的对话目录。完成后再开启持续同步。":
    "Esta aplicación aún no tiene permiso de lectura. Autoriza primero su carpeta de conversaciones y después activa la sincronización continua.",
  "此应用的读取授权已撤销，请重新授权。完成后再开启持续同步。":
    "Se revocó el permiso de lectura de esta aplicación. Vuelve a autorizarla y después activa la sincronización continua.",
  "此应用的对话目录已被重建，请重新授权。完成后再开启持续同步。":
    "La carpeta de conversaciones de esta aplicación se volvió a crear. Vuelve a autorizarla y después activa la sincronización continua.",
  "此应用的对话目录现在读不到，请确认应用已安装并产生过对话。完成后再开启持续同步。":
    "No se puede leer la carpeta de conversaciones de esta aplicación. Comprueba que esté instalada y que tenga conversaciones, y después activa la sincronización continua.",
  "此应用的对话目录已变更，请按最新目录重新授权。完成后再开启持续同步。":
    "La carpeta de conversaciones de esta aplicación cambió. Vuelve a autorizarla con la carpeta actual y después activa la sincronización continua.",
  "此应用的读取授权已失效，请重新授权后再开启持续同步。":
    "El permiso de lectura de esta aplicación ya no es válido. Vuelve a autorizarla y después activa la sincronización continua.",
  "此应用的授权或目录已变化，已按最新状态显示，请确认后再试。":
    "El permiso o la carpeta de esta aplicación cambiaron. Se muestra el estado actual; compruébalo y vuelve a intentarlo.",
  模型配置表单: "Formulario de configuración del modelo",
  "部分运行状态暂时无法读取，当前显示最后读到的状态，请重试。":
    "Algunos estados no están disponibles. Se muestra la última lectura; reintenta.",
  "当前安装还不能设置登录后后台启动。":
    "Esta instalación no permite configurar el inicio en segundo plano al iniciar sesión.",
  "登录后后台启动设置未能确认，请重试。":
    "No se pudo confirmar el inicio en segundo plano. Reintenta.",
  "当前安装还不能退出界面，请从菜单栏退出。":
    "Esta instalación no permite cerrar la interfaz desde aquí. Usa la barra de menús.",
  "退出界面未能确认，请重试。":
    "No se pudo confirmar el cierre de la interfaz. Reintenta.",
  登录后启动后台服务: "Iniciar el servicio al iniciar sesión",
  "无需打开界面，持续同步、整理和提供记忆。":
    "Sincroniza, organiza y ofrece memorias sin abrir la interfaz.",
  导出日志: "Exportar registros",
  "把本机运行日志打包成一个文件，发给开发排查。":
    "Empaqueta los registros locales en un archivo para su diagnóstico.",
  "确定退出 One Memory 界面吗？": "¿Cerrar la interfaz de MemHub?",
  "后台服务会继续同步、整理和提供记忆。":
    "El servicio en segundo plano seguirá sincronizando, organizando y ofreciendo memorias.",
  取消: "Cancelar",
  退出界面: "Cerrar interfaz",
  应用详情: "Detalles de la aplicación",
  返回应用: "Volver a Aplicaciones",
  同步开关: "Sincronización",
  "开启后处理历史待办，并持续接收此应用的新会话。":
    "Al activar, procesa el historial pendiente y recibe las nuevas conversaciones de esta aplicación.",
  "正在读取已有对话，首次开启可能需要一两分钟，请不要重复点击。":
    "Leyendo conversaciones existentes. El primer inicio puede tardar uno o dos minutos; no vuelvas a pulsar.",
  "将补充冻结期间的新对话；原有 pending Job 继续处理。":
    "Se incorporarán las conversaciones del periodo pausado; los trabajos pendientes existentes seguirán procesándose.",
  一键插入应用: "Conectar con un clic",
  "安装记忆连接，并默认允许此应用使用已授权记忆。":
    "Instala la conexión y permite a esta aplicación usar las memorias autorizadas.",
  允许使用记忆: "Permitir el uso de memorias",
  连接状态: "Estado de conexión",
  已接通: "Conectada",
  "已安装，但取不到记忆": "Instalada, sin acceso a memorias",
  未接通: "Sin conexión",
  状态待核对: "Estado pendiente de comprobar",
  检测中: "Detectando",
  未开启关联: "Conexión no habilitada",
  "正在开启处理…": "Activando procesamiento…",
  "正在冻结处理…": "Pausando procesamiento…",
  处理中: "Procesando",
  已冻结: "En pausa",
  "此应用的处理开关结果未能确认，其他应用不受影响。":
    "No se pudo confirmar el cambio de procesamiento de esta aplicación. Las demás no se ven afectadas.",
  "暂时无法刷新来源状态，请重试；未更改同步或读取权限。":
    "No se pudo actualizar el estado de la fuente. Reintenta; no se modificaron los permisos de sincronización ni de lectura.",
  "暂时无法读取连接状态，当前状态待核对；请重试。":
    "No se pudo leer el estado de conexión. Está pendiente de comprobar; reintenta.",
  "已接通。已经打开的对话不会中途出现这个工具，请新开一轮。":
    "Conectada. La herramienta no aparecerá en conversaciones ya abiertas; inicia una nueva.",
  "本次连接探测失败，当前状态待核对；未把上次读取当作本次成功。":
    "La comprobación de conexión falló. El estado está pendiente; la lectura anterior no se considera un éxito actual.",
  上次读取: "Última lectura",
  appSettingsTitle: "Configuración de {name}",
  appMemoryScope:
    "Permite que {name} consulte las memorias dentro del alcance autorizado.",
  appRestart:
    "La herramienta de memorias no está en la conversación actual de {name}. Cierra completamente {name} y vuelve a abrirla; aparecerá en una conversación nueva.",
  appConnectFailed:
    "No se completó la conexión de esta aplicación. Las demás no se ven afectadas.{reason}",
  appDisconnectFailed:
    "No se pudo eliminar la conexión ni revocar el permiso. {name} aún puede usar memorias. Reintenta.{reason}",
  appPermissionFailed:
    "No se pudo confirmar el permiso de uso de memorias de esta aplicación. Reintenta.{reason}",
  appRevokedModified:
    "Se revocó el permiso; {name} ya no puede consultar memorias. El archivo de configuración cambió y MemHub no lo eliminó automáticamente. Quita manualmente la conexión de MemHub.",
  appRevokedRetained:
    "Se revocó el permiso; {name} ya no puede consultar memorias. No se eliminó el archivo de conexión. Compruébalo de nuevo.",
  appRevokedReview:
    "Se revocó el permiso; {name} ya no puede consultar memorias. No se pudo confirmar la eliminación completa de la conexión. Comprueba manualmente: {paths}.",
  appRemovalReview:
    "No se pudo confirmar la eliminación completa de la conexión. Comprueba manualmente: {paths}.",
  "Cursor 的连接凭证已更新。请完全退出并重新打开 Cursor，再开始新对话。":
    "Las credenciales de conexión de Cursor se actualizaron. Cierra completamente Cursor y vuelve a abrirlo; después, inicia una conversación nueva.",
  连接其他工具: "Conectar otras herramientas",
  "本机连接材料仅在桌面 App 中可用。":
    "El material de conexión local solo está disponible en la aplicación de escritorio.",
  "仅本机 MCP / 命令工具。纯网页聊天不支持直接连接本机。":
    "Solo herramientas MCP / comandos locales. Los chats exclusivamente web no pueden conectarse directamente a este dispositivo.",
  "列表内工具请用上方一键安装，此处仅供未列出的工具。":
    "Para las herramientas de la lista, usa la instalación directa de arriba. Esta sección es para otras herramientas.",
  "正在读取本机连接配置…": "Leyendo la configuración de conexión local…",
  "未能读取连接配置。": "No se pudo leer la configuración de conexión.",
  重试: "Reintentar",
  连接材料格式: "Formato del material de conexión",
  连接提示词: "Instrucciones de conexión",
  "MCP 配置": "Configuración MCP",
  命令: "Comando",
  本机连接材料: "Material de conexión local",
  "包含本机路径，不含记忆和密钥。片段会运行本机 MemHub 连接程序；粘贴前请确认，MemHub 需保持运行。":
    "Incluye rutas locales, pero no memorias ni claves. Los fragmentos ejecutan el conector local de MemHub. Revísalos antes de pegarlos y mantén MemHub en ejecución.",
  "已复制，尚未建立连接。":
    "Copiado. La conexión todavía no se ha establecido.",
  "复制失败，可在上方选中文本复制。":
    "No se pudo copiar. Puedes seleccionar el texto de arriba y copiarlo manualmente.",
  复制提示词: "Copiar instrucciones",
  复制配置: "Copiar configuración",
  复制命令: "Copiar comando",
  记忆库分页: "Paginación de la biblioteca de memorias",
  上一页: "Página anterior",
  下一页: "Página siguiente",
} as const;

const chineseOverrides: Partial<
  Record<keyof typeof settingsSpanishMessages, string>
> = {
  "确定退出 One Memory 界面吗？": "确定退出 MemHub 界面吗？",
  appSettingsTitle: "{name} 应用设置",
  appMemoryScope: "让 {name} 查询已授权范围内的记忆。",
  appRestart:
    "记忆工具没有进到 {name} 当前对话。完全退出 {name} 后重新打开，新对话里才会出现。",
  appConnectFailed: "此应用的一键接入未完成，其他应用不受影响。{reason}",
  appDisconnectFailed:
    "连接未能移除，记忆权限也未能撤销，{name} 仍可使用记忆，请重试。{reason}",
  appPermissionFailed: "此应用的记忆使用权限未能确认，请重试。{reason}",
  appFailureReason: "原因：{reason}。",
  appFailureReread: "当前接入状态也未能重新读取，显示的可能不是最新状态。",
  appRevokedModified:
    "已撤销记忆权限，{name} 已查不到记忆。配置文件被改过，MemHub 没有自动删除；请手动移除其中的 MemHub 连接。",
  appRevokedRetained:
    "已撤销记忆权限，{name} 已查不到记忆。连接文件没有移除，请重新核对。",
  appRevokedReview:
    "已撤销记忆权限，{name} 已查不到记忆。但无法确认连接已全部移除，需要你手动核对：{paths}。",
  appRemovalReview: "无法确认连接已全部移除，需要你手动核对：{paths}。",
};
export const settingsChineseMessages = Object.fromEntries(
  Object.keys(settingsSpanishMessages).map((key) => [
    key,
    chineseOverrides[key as keyof typeof settingsSpanishMessages] ?? key,
  ]),
) as Record<keyof typeof settingsSpanishMessages, string>;
