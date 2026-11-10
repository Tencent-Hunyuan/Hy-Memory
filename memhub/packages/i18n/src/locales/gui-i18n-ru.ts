import type { MessageKey } from "../gui-i18n.js";
import { embeddingSwitchRussianMessages } from "../gui-i18n-embedding-switch.js";
import { modeRussianMessages } from "../gui-i18n-mode.js";

/** Russian (ru) GUI copy. Machine-assisted; native-speaker review recommended. */
export const russianMessages: Record<MessageKey, string> = {
  ...modeRussianMessages,
  ...embeddingSwitchRussianMessages,
  "connectionFallback.title": "Подключить другие инструменты",
  "connectionFallback.desktopOnly":
    "Локальное сопряжение доступно только в настольном приложении.",
  "connectionFallback.description":
    "Создайте одноразовый Skill сопряжения для локального Harness, которого нет в списке. Браузерные чаты, удалённые контейнеры и другие компьютеры не могут подключаться к этому устройству напрямую.",
  "connectionFallback.name": "Название Harness",
  "connectionFallback.namePlaceholder": "Например, Kimi Code",
  "connectionFallback.generating": "Создание…",
  "connectionFallback.generate": "Создать Skill сопряжения",
  "connectionFallback.generateFailed": "Не удалось создать Skill сопряжения.",
  "connectionFallback.retry": "Повторить",
  "connectionFallback.generated":
    "Для {name} создан одноразовый Skill сопряжения. Срок действия истекает: {time}.",
  "connectionFallback.skillLabel": "Skill сопряжения",
  "connectionFallback.copy": "Копировать Skill",
  "connectionFallback.copied":
    "Skill сопряжения скопирован. Вставьте и запустите его в соответствующем Harness.",
  "connectionFallback.copyFailed":
    "Не удалось скопировать. Выделите и скопируйте текст выше вручную.",
  "connectionFallback.localDescription":
    "Только для локальных инструментов MCP и командной строки. Браузерные чаты не могут подключаться к этому устройству напрямую.",
  "connectionFallback.loading": "Загрузка данных локального подключения…",
  "connectionFallback.loadFailed": "Не удалось загрузить данные подключения.",
  "connectionFallback.format": "Формат данных подключения",
  "connectionFallback.promptTab": "Промпт подключения",
  "connectionFallback.mcpTab": "Конфигурация MCP",
  "connectionFallback.commandTab": "Команда",
  "connectionFallback.materialLabel": "Данные локального подключения",
  "connectionFallback.materialBoundary":
    "Содержит локальные пути, но не записи памяти и не ключи. Проверьте перед вставкой и не закрывайте MemHub.",
  "connectionFallback.materialCopied":
    "Скопировано. Подключение пока не установлено.",
  "connectionFallback.copyPrompt": "Копировать промпт",
  "connectionFallback.copyConfig": "Копировать конфигурацию",
  "connectionFallback.copyCommand": "Копировать команду",
  "connectionFallback.promptTemplate":
    "Подключите этот локальный Harness к MemHub с помощью следующей конфигурации MCP:\n```json\n{mcp}```\n{shellBlock}\nПосле подключения выполните реальный запрос к памяти и точно сообщите результат.",
  "connectionFallback.promptShell":
    "\nЕсли поддерживаются только локальные команды оболочки, используйте:\n```bash\n{shell}```\n",
  "sdk.memory.title": "Библиотека памяти SDK",
  "sdk.memory.status": "Статус записи памяти",
  "sdk.memory.status.active": "Доступна",
  "sdk.memory.status.superseded": "Заменена",
  "sdk.memory.total": "Записей памяти: {count}",
  "sdk.memory.total.one": "{count} запись памяти",
  "sdk.memory.loadFailed":
    "Не удалось загрузить библиотеку памяти. Повторите попытку.",
  "sdk.memory.detailLoadFailed":
    "Не удалось загрузить сведения о записи памяти.",
  "sdk.memory.updateFailed":
    "Не удалось сохранить изменения. Повторите попытку.",
  "sdk.memory.deleteFailed":
    "Не удалось удалить запись памяти. Повторите попытку.",
  "sdk.memory.loading": "Загрузка библиотеки памяти…",
  "sdk.memory.pagination": "Страницы библиотеки памяти SDK",
  "sdk.memory.previous": "Предыдущая страница",
  "sdk.memory.next": "Следующая страница",
  "sdk.memory.detail": "Сведения о записи памяти SDK",
  "sdk.memory.content": "Содержимое записи памяти",
  "sdk.memory.save": "Сохранить изменения",
  "sdk.memory.delete": "Удалить запись памяти",
  "sdk.memory.scene.chat": "Чат",
  "sdk.memory.scene.working": "Работа",
  "sdk.memory.scene.coding": "Программирование",
  "sdk.memory.task": "Задача",
  "sdk.memory.solution": "Решение",
  "sdk.memory.page": "Страница {page} из {pages}",
  "sdk.raw.title": "Исходные диалоги SDK",
  "sdk.raw.searchLabel": "Поиск по исходным диалогам",
  "sdk.raw.searching": "Поиск…",
  "sdk.raw.search": "Найти",
  "sdk.raw.loadFailed": "Исходные диалоги временно недоступны.",
  "sdk.raw.searchFailed":
    "Не удалось выполнить поиск по исходным диалогам. Повторите попытку.",
  "sdk.raw.recent": "Недавние исходные диалоги",
  "sdk.raw.empty":
    "Исходных диалогов пока нет. Подключите источник, и они появятся здесь.",
  "sdk.raw.total": "Исходных диалогов: {count}",
  "sdk.raw.total.one": "{count} исходный диалог",
  "memory:当前使用基础记忆。新对话会保存为原始记录，不会生成高级记忆；以前生成的高级记忆仍会保留，但当前不会显示或参与记忆召回。":
    "Включена базовая память. Новые диалоги сохраняются в исходном виде и не создают расширенные воспоминания. Существующие расширенные воспоминания сохраняются, но не показываются и не используются при поиске.",
  "memory:当前使用智能记忆。新对话会由模型整理为高级记忆。":
    "Включена интеллектуальная память. Модель преобразует новые диалоги в расширенные воспоминания.",
  "memory:当前使用深度记忆。新对话会生成高级记忆，并由知识图谱和 evolve 继续整合。":
    "Включена глубокая память. Новые диалоги создают расширенные воспоминания, которые продолжают объединяться графом знаний и evolve.",
  "sdk.raw.found": "Результатов: {count}",
  "sdk.raw.found.one": "{count} результат",
  "memoryInspector.emptyHint":
    "Просмотрите источники, содержимое и историю изменений.",
  "memory.operationFailed": "Сбой операции с памятью ({code})",
  "memorySpace.tabs.workbench": "Рабочая область",
  "selectedMemory.status.confirmed": "Создана",
  "selectedMemory.status.active": "Активна",
  "selectedMemory.status.deleted": "Удалена",
  "selectedMemory.status.superseded": "Заменена",
  "selectedMemory.status.shadow": "Скрыта",
  "selectedMemory.status.negated": "Опровергнута",
  "selectedMemory.status.conflicted": "Есть конфликт",
  "selectedMemory.status.archived": "В архиве",
  "selectedMemory.status.unknown": "Нужно проверить статус",
  "selectedMemory.sourceUnknown": "Нужно проверить источник",
  "selectedMemory.sourceUnattributed": "Источник не определён",
  "selectedMemory.discardDraft": "Отменить несохранённые изменения?",
  "selectedMemory.newVersionReadFailed":
    "Не удалось загрузить новую версию. Черновик сохранён.",
  "selectedMemory.updateRejected":
    "Изменения не отправлены. Черновик сохранён: устраните проблему и повторите попытку.",
  "selectedMemory.updateUnknown":
    "Изменения не подтверждены. Черновик сохранён: обновите данные перед повторной попыткой.",
  "selectedMemory.deleteRejected":
    "Удаление не отправлено. Проверьте эту запись памяти и повторите попытку.",
  "selectedMemory.deleteUnknown":
    "Удаление не подтверждено. Проверьте запись памяти: область удаления не будет расширена.",
  "selectedMemory.undoRejected":
    "Отмена не отправлена. Устраните проблему и повторите попытку.",
  "selectedMemory.undoUnknown":
    "Отмена не подтверждена. Проверьте текущую версию перед повторной попыткой.",
  "selectedMemory.author.you": "Вы сказали",
  "selectedMemory.author.assistant": "Исходное сообщение ассистента",
  "selectedMemory.author.unknown": "Нужно проверить автора",
  "selectedMemory.version": "Версия {revision}",
  "selectedMemory.sourceLine": "Источник {source} · {time}",
  "selectedMemory.timestamps":
    "Время диалога: {conversation} · Создано: {created}",
  "selectedMemory.writeTime": "Записано: {created}",
  "selectedMemory.edit": "Изменить",
  "selectedMemory.evidence": "Исходное подтверждение",
  "selectedMemory.evidenceLine": "{author} · Исходный диалог, {time}:",
  "selectedMemory.evidenceUnavailable":
    "Этот исходный текст сейчас недоступен.",
  "selectedMemory.viewOriginal": "Показать исходный текст",
  "selectedMemory.history": "История версий",
  "selectedMemory.more": "Другие действия",
  "selectedMemory.undo": "Отменить последнее изменение",
  "selectedMemory.delete": "Удалить запись памяти",
  "selectedMemory.editTitle": "Изменение записи памяти",
  "selectedMemory.originalTitle": "Исходный диалог",
  "selectedMemory.back": "Назад к записи памяти",
  "selectedMemory.editDescription":
    "Измените эту запись памяти: исходный диалог останется без изменений.",
  "selectedMemory.content": "Содержимое записи памяти",
  "selectedMemory.scopeUnchanged": "Область доступа не изменится",
  "selectedMemory.conflictTitle": "Есть более новая версия",
  "selectedMemory.conflictDescription":
    "Черновик сохранён. Просмотрите новую версию перед редактированием.",
  "selectedMemory.viewNewVersion": "Показать новую версию",
  "selectedMemory.continueFromVersion": "Продолжить с этой версии",
  "selectedMemory.cancel": "Отмена",
  "selectedMemory.save": "Сохранить изменения",
  "selectedMemory.author.user": "Вы",
  "selectedMemory.author.agent": "Ассистент",
  "selectedMemory.author.tool": "Инструмент",
  "selectedMemory.sourceUnavailable":
    "Разрешения источника изменились, или исходный текст недоступен.",
  "selectedMemory.related": "Связанная запись памяти",
  "selectedMemory.viewMemory": "Показать запись памяти",
  "selectedMemory.deleteTitle": "Удалить эту запись памяти?",
  "selectedMemory.deleteDescription":
    "Она больше не будет использоваться в новых запросах. Диалог в приложении-источнике удалён не будет.",
  "selectedMemory.sentCannotRecall":
    "Содержимое, уже отправленное в приложения, нельзя отозвать удалённо.",
  "memory:{count} 条": "Записей: {count}",
  "memory:已可用 · {count} 段原文": "Доступно · исходных фрагментов: {count}",
  "memory:主题视角（非事项）": "По темам (не по делам)",
  "memory:来源视角": "По источникам",
  "memory:{count} 条记忆 · 当前已加载": "Записей памяти: {count} · загружено",
  "memory:最近 {count} 条": "Недавние диалоги: {count}",
  "memory:已确认": "Подтверждено",
  "memory:记忆库": "Библиотека памяти",
  "memory:问记忆": "Спросить память",
  "memory:历史对话": "История диалогов",
  "memory:项目记忆": "Память проекта",
  "memory:编码偏好": "Предпочтения в коде",
  "memory:长期模式": "Долгосрочный паттерн",
  "memory:意图": "Намерение",
  "memory:近 3 个月": "За 3 месяца",
  "memory:近 6 个月": "За 6 месяцев",
  "memory:近 1 年": "За год",
  "memory:全部历史": "Вся история",
  "memory:{count} 条这次没能整理成功，正在重试": "Повтор обработки · {count}",
  "memory:{count} 处理中": "Обработка: {count}",
  "memory: · 处理中 {running} / 待派发 {active}":
    " · обработка: {running} / в очереди: {active}",
  "memory: · {count} 条任务状态待核对":
    " · задач с непроверенным статусом: {count}",
  "memory: · {count} 条任务状态异常，正在恢复":
    " · задач с проблемным статусом: {count}; идёт восстановление",
  "memory:{count} 等待": "Ожидают: {count}",
  "memory:最新 {count} 条待处理": "Новых в ожидании: {count}",
  "memory:历史积压 {count} 条": "Накоплено: {count}",
  "memory:（约 {count} 分钟）": " (около {count} мин)",
  "memory:（约 {count} 小时）": " (около {count} ч)",
  "memory:（约 {count} 天）": " (около {count} дн.)",
  "memory:同步尝试 {count} 次": "Попыток синхронизации: {count}",
  "memory:尚未尝试同步": "Синхронизация не выполнялась",
  "memory: · 状态更新于 {date}": " · Обновлено: {date}",
  "memory:HY Memory 当前没有这条记忆，仅重试同步不能补齐。":
    "В HY Memory нет этой записи памяти; повторная синхронизация её не восстановит.",
  "memory:同步需要可用的本机整理模型或本地嵌入授权。":
    "Для синхронизации нужна доступная локальная модель обработки или разрешение на локальные эмбеддинги.",
  "memory:这条记忆或模型版本已经变化，先刷新核对再决定下一步。":
    "Эта запись памяти или версия модели изменилась. Обновите данные и проверьте их, прежде чем продолжать.",
  "memory:这条记忆已改，搜索索引还没开始同步。":
    "Запись памяти изменена, но синхронизация поискового индекса ещё не началась.",
  "memory:最近一次同步未成功；可以先重试，结果仍不确定时再刷新核对。":
    "Последняя синхронизация не удалась. Сначала повторите её; если результат останется неясным, обновите данные и проверьте их.",
  "memory:这条记忆已改，搜索索引还没跟上。":
    "Запись памяти изменена, но поисковый индекс ещё не обновился.",
  "memory:搜索里还是改之前那句话。":
    "В поиске по-прежнему виден текст до исправления.",
  "memory:重试同步": "Повторить синхронизацию",
  "memory:配置整理模型": "Настроить модель обработки",
  "memory:未提炼 · 在窗口外": "Не извлечено · вне окна",
  "memory:未提炼 · 排队中": "Не извлечено · в очереди",
  "wb.select": "Выбрать",
  "memory:模型综合": "Сводка модели",
  "memory:原文摘录": "Исходный фрагмент",
  "memory:没有命中": "Нет совпадений",
  "memory:正在生成": "Создание",
  "memory:正在检索记忆": "Поиск в памяти",
  "memory: · 基于 {count} 条已确认记忆":
    " · На основе подтверждённых записей памяти: {count}",
  "memory: · 基于 {count} 条已确认记忆.one":
    " · На основе {count} подтверждённой записи памяти",
  "memory:整理需要处理": "Обработка требует внимания",
  "memory:正在整理记忆": "Обработка записей памяти",
  "memory:整理状态": "Статус обработки",
  "memory:自动提炼范围": "Окно автоматического извлечения",
  "memory:手动提炼下一批": "Извлечь следующий пакет вручную",
  "memory:刷新核对": "Обновить и проверить",
  "memory:正在导入历史会话，记忆库暂时只显示一部分；导入完成后会自动补全，不需要重试。":
    "Идёт импорт истории диалогов. Видна только часть записей памяти; после импорта список дополнится автоматически.",
  "memory:选择：{content}": "Выбрать: {content}",
  "memory:处理失败：{content}": "Не удалось обработать: {content}",
  "memory:已选 {count} 条": "Выбрано: {count}",
  "memory:已选 {count} 条.one": "Выбрано: {count}",
  "memory:删除所选 {count} 条": "Удалить выбранные: {count}",
  "memory:删除所选 {count} 条.one": "Удалить выбранные: {count}",
  "memory:重试失败的 {count} 条": "Повторить неудачные: {count}",
  "memory:重试失败的 {count} 条.one": "Повторить неудачные: {count}",
  "memory:没有{status}的记忆。": "Нет записей памяти со статусом «{status}».",
  "memory:已{action} {count} 条": "Операция «{action}» выполнена: {count}",
  "memory:已{action} {count} 条.one": "Операция «{action}» выполнена: {count}",
  "memory:，{count} 条未完成": ", не завершено: {count}",
  "memory:，{count} 条未完成.one": ", не завершено: {count}",
  "memory:未能{action}：{error}": "Сбой операции «{action}»: {error}",
  "memory:重试{action}": "Повторить операцию «{action}»",
  "memory:删除（操作）": "Удаление",
  "memory:处理（操作）": "Обработка",
  "memory:{count} 条原话": "Исходных фрагментов: {count}",
  "memory:{count} 条原话.one": "{count} исходный фрагмент",
  "memory:{count} 次整理已完成": "Завершено запусков обработки: {count}",
  "memory:{count} 次整理已完成.one": "Завершено запусков обработки: {count}",
  "memory: · {count} 条在窗口外（原文可搜）":
    " · вне окна: {count} (исходный текст доступен для поиска)",
  "memory: · {count} 条在窗口外（原文可搜）.one":
    " · вне окна: {count} (исходный текст доступен для поиска)",
  "memory: · 已规划 {planned} 批，当前第 {current} 批":
    " · запланировано пакетов: {planned}, текущий: {current}",
  "memory:{kind}卡在 {reason}（{count} 条）":
    "{kind}: препятствие — {reason} ({count})",
  "memory:已加载 {count} 条记忆": "Загружено записей памяти: {count}",
  "memory:已加载 {count} 条记忆.one": "Загружено записей памяти: {count}",
  "memory:回答只使用已确认记忆。每个答案都可以回到对应记忆和来源原话。":
    "В ответах используются только подтверждённые записи памяти. Каждый ответ ссылается на запись памяти и исходный текст.",
  "memory:选择一条记忆": "Выберите запись памяти",
  "memory:查看它的来源、使用范围和修改历史。":
    "Просмотрите источники, область доступа и историю изменений.",
  "memory:查看替代它的记忆": "Показать заменившую запись памяти",
  "memory:查看它取代的旧记忆": "Показать заменённую запись памяти",
  "memory:保存纠正": "Сохранить исправление",
  "memory:修改历史": "История изменений",
  "memory:已可用": "Доступно",
  "memory:处理中…": "Обработка…",
  "memory:搜索": "Найти",
  "memory:记忆治理状态": "Статус памяти",
  "memory:记忆空间模式": "Режимы пространства памяти",
  "memory:来源状态待核对，记忆库暂不可读取。":
    "Нужно проверить статус источника; библиотека памяти пока недоступна для чтения.",
  "memory:记忆库读取失败，请重试。":
    "Не удалось загрузить библиотеку памяти. Повторите попытку.",
  "memory:重新读取记忆库": "Перезагрузить библиотеку памяти",
  "memory:正在读取记忆库…": "Загрузка библиотеки памяти…",
  "memory:批量操作结果": "Результат массового действия",
  "memory:批量操作": "Массовые действия",
  "memory:取消选择": "Снять выделение",
  "memory:向已确认记忆提问": "Спросить подтверждённую память",
  "memory:搜索记忆库": "Поиск в библиотеке памяти",
  "memory:问答记忆范围": "Область вопроса к памяти",
  "memory:只问个人画像": "Только личный профиль",
  "memory:问全部记忆（含项目）": "Вся память (включая проекты)",
  "memory:例如：我对真实数据有什么要求？":
    "Например: какие у меня требования к реальным данным?",
  "memory:搜索偏好、决定或经历": "Поиск предпочтений, решений или опыта",
  "memory:根据记忆回答": "Ответить на основе памяти",
  "memory:清除搜索": "Очистить поиск",
  "wb.finding.duplicate": "Из одного исходного сообщения",
  "wb.finding.duplicateText":
    "«{first}» и «{second}» получены из одного исходного сообщения и отнесены к {firstScope} и {secondScope}. Проверьте, не об одном ли они.",
  "wb.finding.dormant": "Давно не обновлялось",
  "wb.finding.dormantText":
    "Записей памяти, которые не менялись и не упоминались два месяца: {count}. Возможно, их стоит проверить.",
  "wb.finding.blocked": "Ожидается условие",
  "wb.finding.blockedText":
    "Диалогов в очереди, ожидающих выполнения условия: {count}. Их исходный текст уже сохранён и доступен для поиска.",
  "wb.finding.blockedText.one":
    "Диалогов в очереди, ожидающих выполнения условия: {count}. Исходный текст уже сохранён и доступен для поиска.",
  "wb.unclassified": "Без категории",
  "wb.profile": "Личный профиль",
  "wb.project": "Проект {id}",
  "wb.topics": "Тема",
  "wb.all": "Все",
  "wb.topicCount": "Сформировано тем: {count}",
  "wb.topicCount.one": "Сформировано тем: {count}",
  "wb.singletons": "; тегов, встречающихся один раз: {count}",
  "wb.singletons.one": "; тегов, встречающихся один раз: {count}",
  "wb.memories": "Записей памяти: {count}",
  "wb.memories.one": "{count} запись памяти",
  "wb.sources": "Источников: {count}",
  "wb.sources.one": "{count} источник",
  "wb.once":
    "Эта тема встречается только здесь. Возможно, это разовый случай, или одна тема разбита на разные названия.",
  "wb.graphTitle": "Граф связей: {label}",
  "wb.readSummary": "Прочитать сводку по этому делу",
  "wb.summaryPrompt":
    "Объясните суть «{label}»: каково текущее положение, что уже решено и что остаётся открытым.",
  "wb.building": "Составление сводки…",
  "wb.build": "Построить связи с HY Memory",
  "wb.drawn": "Показано записей памяти: {shown} из {total}",
  "wb.askLabel": "Вопрос по этому делу",
  "wb.askPlaceholder":
    "Спросите об этом деле; в ответах используются только подтверждённые записи памяти",
  "wb.ask": "Спросить",
  "wb.originals": "Исходные диалоги",
  "wb.evidenceNote":
    "Исходные фрагменты этой записи памяти ниже, по времени: {count}",
  "wb.evidenceNote.one":
    "Эта запись памяти основана на исходном фрагменте ниже",
  "wb.pickCircle":
    "Выберите круг на графе, чтобы увидеть здесь его исходный текст",
  "wb.unavailable": "Исходный текст источника недоступен",
  "wb.fullConversation": "Показать весь диалог",
  "wb.you": "Вы",
  "wb.assistant": "Ассистент",
  "wb.noMemories":
    "Записей памяти пока нет. Импортированные исходные диалоги можно искать в разделе «История диалогов».",
  "wb.waiting":
    "Некоторые диалоги в очереди ожидают выполнения условия. Их исходный текст сохранён и доступен для поиска.",
  "wb.conditions": "Посмотреть, чего не хватает",
  "wb.findings": "Что показывают ваши данные",
  "wb.findingsNote":
    "Вычислено по локальным данным. Выберите пункт, чтобы выделить на графе подтверждающие записи памяти.",
  "wb.showGraph": "Показать на графе",
  "wb.loading": "Загрузка записей памяти…",
  "wb.coreStarting":
    "Появится автоматически после запуска локального Core; обновлять не нужно.",
  "wb.nothingToConnect": "Пока нечего связывать.",
  "wb.skeletonNote":
    "Разрешите доступ к источнику, чтобы увидеть структуру источников и тем; записи памяти будут появляться по мере создания.",
  "wb.authorize": "Разрешить доступ к источнику",
  "wb.conversations": "Диалогов: {count}",
  "wb.conversations.one": "{count} диалог",
  "wb.chipChanges": "Что здесь недавно изменилось?",
  "wb.chipSummarize": "Кратко изложите это в нескольких пунктах",
  "wb.unknownTime": "Время неизвестно",
  "wb.unknownSource": "Неизвестный источник",
  "wb.transcriptGone": "Этого диалога больше нет в библиотеке.",
  "wb.edgeCount": "{label}: связей — {count}",
  "wb.edgeCount.one": "{label}: {count} связь",
  "wb.noRelations":
    "HY Memory пока не нашла связей. Запустите System 2 кнопкой выше.",
  "wb.noModelRelations":
    "Нет настроенной модели, поэтому связи HY Memory недоступны.",
  "provenance.multiple": "Несколько авторов",
  "provenance.person": "Исходное сообщение пользователя",
  "provenance.agent": "Исходное сообщение агента",
  "provenance.unknown": "Автор неизвестен",
  "provenance.imported": "Импортированная запись памяти · {author}",
  "provenance.manual": "Добавлено вручную",
  "provenance.answered": "Создано из ответа",
  "provenance.generated": "Создано из ответа ИИ",
  "provenance.conversation": "Создано из исходных диалогов",
  "arrival.region": "Статус обработки памяти",
  "arrival.importing": "Исходные тексты ещё импортируются",
  "arrival.messages": "Сохранено сообщений: {count}",
  "arrival.messages.one": "Сохранено сообщений: {count}",
  "arrival.conversations": "Диалогов: {count}",
  "arrival.conversations.one": "{count} диалог",
  "arrival.details": "Показать ход и подробности",
  "arrival.backlog":
    "Много существующих данных: ради качества обработка идёт пакетами в фоне",
  "arrival.items": "Элементов: {count}",
  "arrival.items.one": "{count} элемент",
  "arrival.configure": "Настроить обработку памяти",
  "arrival.status": "Статус обработки",
  "arrival.history": "Сохранённые диалоги",
  "arrival.connect": "Подключить приложение",
  "arrival.retained":
    "Сохранённые исходные тексты остаются на этом устройстве.",
  "arrival.description.model-required":
    "Проверьте модель обработки, прежде чем продолжить.",
  "arrival.description.processing":
    "Сначала обрабатываются недавние диалоги; новые записи памяти будут появляться постепенно",
  "arrival.description.importing":
    "Исходные тексты сохраняются; ход импорта не отражает ход создания записей памяти.",
  "arrival.description.failed":
    "Выясните причину сбоя, прежде чем повторять попытку.",
  "arrival.description.unavailable":
    "Последние подтверждённые данные сохранены; проверьте текущий ход ещё раз.",
  "arrival.description.refreshing":
    "Проверка локальных данных; неподтверждённый прогресс не учитывается.",
  "arrival.description.checking":
    "Сохранённые данные на месте; прогресс нужно обновить.",
  "arrival.description.stopped":
    "Обработка остановлена; проверьте её статус, прежде чем продолжить.",
  "arrival.description.idle": "Сейчас нет запущенных задач обработки.",
  "arrival.description.empty":
    "Подключите приложение или импортируйте файл диалога; исходные тексты сначала сохранятся на этом устройстве.",
  "arrival.title.refreshing": "Сохранённые данные на месте; проверка прогресса",
  "arrival.title.checking": "Нужно обновить прогресс",
  "arrival.title.importing": "Импорт диалогов",
  "arrival.title.processing": "Создание записей памяти",
  "arrival.title.continue": "Требует внимания",
  "arrival.title.model": "Модель обработки не настроена",
  "arrival.title.empty": "Сохранённых диалогов пока нет",
  "handoff.region": "Перенос памяти в другое приложение",
  "handoff.answer": "Перенести ответ и ссылки в другое приложение",
  "handoff.selected": "Перенести выбранные записи памяти в другое приложение",
  "handoff.title": "Перенос в другое приложение",
  "handoff.closeTitle": "Закрыть предпросмотр",
  "handoff.close": "Закрыть предпросмотр переноса",
  "handoff.stats":
    "Записей памяти: {count} · Символов: {characters} · Без сокращений",
  "handoff.stats.one":
    "{count} запись памяти · Символов: {characters} · Без сокращений",
  "handoff.content": "Полное содержимое для переноса",
  "handoff.target": "Приложение назначения",
  "handoff.other": "Другое приложение · только копирование",
  "handoff.copyOnly": " · только копирование",
  "handoff.boundary":
    "Включается только содержимое выше, без исходных диалогов. Копирование не разрешает подключение; копии в других приложениях нельзя отозвать.",
  "handoff.copy": "Копировать",
  "handoff.open": "Скопировать и открыть {host}",
  "handoff.copyFailed":
    "Не удалось скопировать; приложение не открыто. Выделите и скопируйте текст в предпросмотре вручную.",
  "handoff.opened":
    "Скопировано, запрошено открытие {host}. Ничего не отправлено; проверьте поле ввода в приложении назначения.",
  "handoff.notPrefilled":
    "Скопировано полностью. Не удалось предзаполнить {host}: вставьте текст в приложении назначения. Ничего не отправлено.",
  "handoff.openFailed":
    "Скопировано полностью, но не удалось открыть {host}. Ничего не отправлено.",
  "handoff.copied": "Скопировано полностью: {target}. Ничего не отправлено.",
  "handoff.pasteTarget": ", можно вставить в {host}",
  "wb.memory": "Эта запись памяти",
  "wb.node": "Этот узел",
  "wb.matter": "Это дело",
  "wb.available": "Доступно",
  "wb.superseded": "Заменено",
  "wb.deleted": "Удалено",
  "wb.revision": "Версия {revision}",
  "wb.collapse": "Свернуть",
  "wb.oldSearch": "В поиске по-прежнему виден текст до исправления.",
  "wb.missingIndex":
    "В HY Memory нет этой записи памяти; повторная попытка её не восстановит.",
  "wb.retrySync": "Повторить синхронизацию",
  "wb.correctForm": "Исправить содержимое записи памяти",
  "wb.content": "Содержимое записи памяти",
  "wb.correct": "Исправить",
  "wb.saveCorrection": "Сохранить исправление",
  "wb.cancelEdit": "Отменить изменения",
  "wb.undo": "Отменить последнее изменение",
  "wb.delete": "Удалить",
  "wb.nodeNote":
    "Это не запись памяти: записи памяти связываются с этим узлом. Прямых связей: {direct}; достижимо за два шага: {indirect}.",
  "wb.direct": "Прямые",
  "wb.twoHops": "Два шага",
  "wb.idle":
    "Круги — это записи памяти, линии соединяют упомянутые в одном диалоге. Выберите круг, чтобы увидеть его происхождение и изменить его. Выбор другого дела слева обновляет граф.",
  "wb.searching": "Поиск в памяти…",
  "wb.generated": "Сводка модели",
  "wb.extractive": "Исходный фрагмент",
  "wb.noHit": "Нет совпадений",
  "wb.answerSource":
    "{mode} · На основе подтверждённых записей памяти: {count}",
  "wb.answerSource.one":
    "{mode} · На основе подтверждённых записей памяти: {count}",
  "wb.recalled": "Найдено записей памяти: {count}",
  "wb.recalled.one": "Найдено записей памяти: {count}",
  "wb.saveMemory": "Сохранить как запись памяти",
  "wb.tooLong":
    "Этот ответ превышает текущий лимит и не может быть сохранён как запись памяти.",
  "wb.summary":
    "Это сводка, а не запись памяти. Она создаётся заново каждый раз, поэтому всегда соответствует записям памяти ниже.",
  "wb.thisAnswer": "Этот ответ",
  "wb.type.source": "Приложение-источник",
  "wb.type.topic": "Тема",
  "wb.type.schema": "Сводка HY",
  "wb.type.memory": "Записи памяти",
  "wb.origin.hy_relation": "Сводка HY Memory",
  "wb.origin.same_conversation": "Один диалог",
  "wb.origin.shared_tag": "Одна тема",
  "wb.origin.provenance": "Источник исходного текста",
  "wb.relation.evidence_source": "Из",
  "wb.relation.same_conversation": "Один диалог",
  "wb.relation.shared_tag": "Одна тема",
  "wb.relation.derived_from": "Обобщено из",
  "wb.relation.tagged_with": "С тегом",
  "wb.relation.related_to": "Связано",
  guideEyebrow: "Попробуйте",
  guideTitle: "Продолжайте с того места, где остановились",
  guideBack: "Назад к памяти",
  guideChooseScene: "Выберите сценарий",
  guideSame: "Продолжить в новом диалоге",
  guideSameDescription:
    "Найдите прежние решения в новом диалоге в том же приложении.",
  guideSwitch: "Продолжить в другом приложении",
  guideSwitchDescription:
    "Та же работа, другой ИИ. Сначала перенесите нужный контекст.",
  guideNew: "Помочь новому инструменту узнать меня",
  guideNewDescription:
    "Начните с одной подробности, которой готовы поделиться, вместо того чтобы снова представляться.",
  guideReadError:
    "Сейчас не удалось прочитать записи памяти. Повторите попытку позже.",
  guideDetailError:
    "Эту запись памяти сейчас нельзя прочитать. Выберите другую или обновите страницу.",
  guideChanged:
    "Запись памяти или её разрешения изменились. Обновите данные и проверьте ещё раз.",
  guideConnectionChanged:
    "Статус подключения изменился; ничего не скопировано. Выберите способ снова.",
  guideCopiedOpened:
    "Скопировано, запрошено открытие {host}. Ничего не отправлено; проверьте поле ввода.",
  guideCopiedNotFilled:
    "Скопировано полностью, но предзаполнить не удалось. Вставьте текст в приложении назначения; ничего не отправлено.",
  guideCopiedNotOpened:
    "Скопировано полностью, но не удалось открыть приложение назначения. Ничего не отправлено.",
  guideCopied:
    "Скопировано полностью. Ничего не отправлено, новые разрешения на чтение не выданы.",
  guideValidationError:
    "Не удалось проверить текущую запись памяти или разрешения. Старый предпросмотр скрыт; обновите данные и повторите попытку.",
  guideCopyError:
    "Не удалось проверить актуальное содержимое или завершить копирование. Повторите попытку; приложение назначения не открыто.",
  guideManual: "Добавлено в приложении",
  guideOriginalApp: "Приложение-источник",
  guideListSeparator: ", ",
  guideSaved: "Исходных сообщений на этом устройстве: {count} · ",
  "guideSaved.one": "Исходных сообщений на этом устройстве: {count} · ",
  guideReadingMemories: "Загрузка обработанных записей памяти",
  guideLoaded: "Доступных записей памяти на этой странице: {count}",
  "guideLoaded.one": "Доступных записей памяти на этой странице: {count}",
  guideSeparate: ". Эти этапы считаются отдельно.",
  guideChooseBackground: "Выбор контекста",
  guideCheckSources: "Проверка источников",
  guideTry: "Попробовать в новом диалоге",
  guideLoading: "Загрузка записей памяти с этого устройства…",
  guideEmptyTitle: "Сначала добавьте полезный контекст",
  guideEmptyDescription:
    "Для этой пробы нет доступных записей памяти. Импортированные исходные тексты — ещё не обработанные записи памяти, а предложения автоматически не передаются.",
  guideAdd: "Добавить запись памяти",
  guideConnections: "Импорт и подключения",
  guideRefresh: "Обновить",
  guideBackgroundTitle: "Контекст для пробы",
  guideRefreshExample: "Обновить пример",
  guideChooseMemory: "Выберите запись памяти",
  guideLoadedScope:
    "Выберите из загруженных здесь доступных записей памяти: {count}. Это не вся ваша история.",
  "guideLoadedScope.one":
    "Выберите из загруженных здесь доступных записей памяти: {count}. Это не вся ваша история.",
  guideCheckingEvidence: "Проверка исходного подтверждения…",
  guideRevision:
    "{source} · Версия {revision} · Создано системой, требует вашей проверки",
  guideScope: "Область: ",
  guideAllApps:
    "Доступно во всех приложениях; не обязательно личное предпочтение",
  guidePrivate:
    "Внешний вызов отключён; ручная передача требует отдельного решения",
  guideLimited: "Ограниченная область; внешний вызов не расширен",
  guideEvidence: "Исходное подтверждение · фрагментов: {count}",
  "guideEvidence.one": "Исходное подтверждение · фрагментов: {count}",
  guideUnknownSource: "Неизвестный источник",
  guideYou: "Вы",
  guideUnknownAuthor: "Автор неизвестен",
  guideUnknownTime: "Время неизвестно",
  guideUnavailable:
    "Исходный текст или текущее разрешение недоступны. Выберите другую запись памяти или обновите данные; это содержимое передано не будет.",
  guideDraftTitle: "Начало следующего диалога",
  guideChooseApp: "Выберите приложение для пробы",
  guideCopyOnly: "Выбрать приложение самостоятельно · только копирование",
  guideHostsError:
    "Не удалось прочитать статус подключения. Повторите попытку или скопируйте только контекст.",
  guideNoApp:
    "Подключённое приложение не выбрано. Ручная передача не устанавливает подключение.",
  guideWired:
    "Последняя самопроверка прошла успешно; убедитесь, что всё работает для этой задачи.",
  guideInstalled: "Подключение установлено; доступ на чтение не проверен.",
  guideNotInstalled: "Подключение для чтения не установлено.",
  guideManage: "Управление подключениями приложений",
  guideChooseRoute: "Выберите способ передачи контекста",
  guideContext: "Передать контекст",
  guideQuery: "Пусть приложение найдёт",
  guideTopic: "Тема для продолжения",
  guideQueryHint:
    "Отправляется только запрос; не помещайте ожидаемый ответ выше в новый диалог.",
  guideNewHint:
    "Передаётся только выбранная вами запись памяти; личный профиль автоматически не создаётся.",
  guideContextHint:
    "Контекст передаётся со ссылками; это не проверка автоматического вызова.",
  guidePreview: "Полный предпросмотр начала",
  guidePreviewPlaceholder:
    "Начало появится, когда будет доступно исходное подтверждение",
  guideChecking: "Проверка…",
  guideCopy: "Копировать начало",
  guideCopyOpen: "Скопировать и открыть {host}",
  guideNoReceipt:
    "Подтверждения внешнего вызова на этот раз нет. В новом диалоге использован верный контекст?",
  guideVerified: "Я проверил(а) результат",
  guideMissing: "Не удалось найти",
  guideFeedbackOk:
    "Ваш отзыв: проверено. Это не запись автоматической проверки.",
  guideFeedbackMissing:
    "Ваш отзыв: не найдено. Сначала проверьте импорт источника, область памяти и подключения приложений.",
  guideInvitation: "Приглашение попробовать",
  guideInvitationHint:
    "Попробуйте продолжить новый диалог с помощью одной из своих записей памяти.",
  guideTryOnce: "Попробовать",
  guideLater: "Позже",
  guideDismiss: "Закрыть приглашение",
  healthTitle: "Диагностика работы",
  healthLocal: "Локальная служба",
  healthBackground: "Фоновая служба",
  healthSources: "Итоги по источникам",
  healthProcessing: "Обработка памяти",
  healthHosts: "Подключения ИИ",
  healthDetailed: "Подробный журнал",
  healthDetailedTitle: "Подробный журнал (для устранения неполадок)",
  healthDetailedHint:
    "По умолчанию выключено. Включайте, только если об этом просит поддержка.",
  healthDetailedError:
    "Не удалось изменить настройку подробного журнала. Повторите попытку.",
  healthRefresh: "Обновить статус",
  healthIncident: "Отметить время проблемы",
  healthIncidentDesktop: "Отметьте время проблемы в настольном приложении.",
  healthIncidentPrompt: "Кратко опишите проблему (необязательно)",
  healthIncidentCopied: "Идентификатор проблемы скопирован: {id}",
  healthIncidentError: "Не удалось отметить время проблемы. Повторите попытку.",
  healthReadFailed: "Временно недоступно",
  healthStale: " · Данные устарели; показан последний результат.",
  healthReading: "Чтение статуса…",
  healthRereading: "Обновление; показаны последние успешно прочитанные данные…",
  healthReadSuccessTime: "Последнее успешное чтение",
  healthReadFailedTime: "Время сбоя чтения",
  healthReadingSources: "Чтение статуса локальной службы и источников…",
  healthPrevious: "Последнее чтение",
  healthPreviousPrefix: "Последнее чтение: ",
  healthRunning: "Работает",
  healthCoreVersion: "{state} · Версия {version} · Экземпляр {instance}",
  healthCoreDisconnected: "Локальный Core пока не подключён.",
  healthBackgroundUnavailable:
    "Здесь нельзя проверить фоновую службу. Используйте поддерживаемое настольное приложение.",
  healthRuntime: "Core: {core} · Мост хоста: {broker}",
  healthSourceCount: "Диалогов: {conversations} · Сообщений: {messages}",
  healthNoSources: "Подключённых источников пока нет.",
  healthTaskCounts:
    "Обработка: {running} · В очереди: {pending} · Завершено: {completed} · Сбоев: {failed}",
  healthImportDetails: "Сведения об импорте и обработке",
  healthRawTurns: "Исходный текст и полные ходы диалога",
  healthRawCounts: "Исходных текстов: {raw} · Полных ходов: {turns}",
  healthHy: "Обработка HY",
  healthHyCounts:
    "Вызовов: {calls} · Завершено: {completed} · Неясно: {unknown} · Сбоев: {failed} · Отменено: {cancelled}",
  healthOutput: "Результат",
  healthMemories: "Записей памяти: {count}",
  "healthMemories.one": "{count} запись памяти",
  healthWorkbuddy: "Запросы памяти WorkBuddy",
  healthWorkbuddyCounts:
    "Вставок: {injected} · Тайм-аутов: {timedOut} · Недоступно: {unavailable}",
  healthBlocked: "{kind}: препятствие — {reason} ({count})",
  healthRetrying: "Повтор обработки · {count}",
  "healthRetrying.one": "Повтор обработки · {count}",
  healthNoIssues: "Нет проблем, требующих внимания.",
  healthHostsUnavailable:
    "В браузере это недоступно; проверьте в настольном приложении.",
  healthNoHosts: "На этом устройстве не найдены поддерживаемые инструменты ИИ.",
  healthBackgroundHealthy: "Фоновая служба доступна",
  healthBackgroundStopped: "Фоновая служба остановлена",
  healthBackgroundDegraded: "Фоновая служба частично недоступна",
  healthBackgroundUntrusted: "Не удалось проверить подлинность фоновой службы",
  healthBackgroundUnknown: "Статус фоновой службы не подтверждён",
  healthStopped: "Остановлено",
  healthUnhealthy: "Нет подключения",
  healthUntrusted: "Подлинность не проверена",
  healthUnknown: "Не подтверждено",
  healthNotChecked: "Не проверено",
  healthWired: "Подключено",
  healthBlind: "Установлено, но не получает записи памяти",
  healthUnreachable: "Не подключено",
  startupReadError: "Не удалось прочитать статус запуска. Повторите попытку.",
  startupRetry: "Повторить",
  startupOpening: "Открытие MemHub…",
  nativeHelp: "Справка",
  nativeExportLogs: "Экспорт журналов для поддержки",
  nativeExportError:
    "Не удалось экспортировать журналы. Повторите попытку позже.",
  nativeLogin: "Запускать {product} при входе в систему",
  nativeTrialUnavailable: " (недоступно в этой сборке)",
  nativeCliManaged: " (управляется через CLI)",
  nativeStatusUnavailable: " (статус недоступен)",
  nativeDevelopmentUnavailable: " (недоступно в режиме разработки)",
  nativeOpen: "Открыть {product}",
  nativeQuit: "Выйти из {product} (фоновая служба продолжит работу)",
  nativeQuitStopsService: "Выйти из {product} (служба памяти тоже остановится)",
  nativeLoginError:
    "Не удалось изменить настройку запуска при входе. Повторите попытку.",
  nativeMoveTitle: "Сначала переместите MemHub в «Программы»",
  nativeMoveDetail:
    "Откройте MemHub из папки «Программы», чтобы подключить другие приложения и установить инструмент командной строки.",
  nativeAcknowledge: "ОК",
  graphWidthTitle: "Библиотека памяти не соответствует встроенной модели",
  graphWidthDescription:
    "Ваша библиотека памяти использует размерность {stored}, а встроенная модель — {expected}. Они несовместимы: извлечение будет завершаться ошибкой, пока вы не выберете вариант.",
  graphWidthKeep:
    "Оставить библиотеку (по умолчанию): продолжить с прежней конфигурацией эмбеддингов; записи памяти ({count}) и граф не изменятся",
  graphWidthRebuild:
    "Использовать встроенную модель: текущий граф будет архивирован (не удалён); связи записей памяти ({count}) придётся построить заново",
  graphWidthExport:
    "Сначала экспорт: пока ничего не менять и выбрать после экспорта",
  graphWidthExported:
    "Экспортировано в {path}. Библиотека не изменена; можно выбрать снова.",
  graphWidthUnknown:
    "Выбор размерности не подтверждён. Библиотека памяти не изменена; повторите попытку.",
  graphWidthConfirm: "Подтвердить выбор",
  importUserProgress:
    "Импорт истории диалогов. Импортировано диалогов: {count}. Записи памяти будут появляться постепенно; приложением можно пользоваться.",
  "importUserProgress.one":
    "Импорт истории диалогов. Импортировано диалогов: {count}. Записи памяти будут появляться постепенно; приложением можно пользоваться.",
  importUserModel:
    "Модель ещё не настроена. Новые диалоги пока не будут превращаться в записи памяти.",
  importUserIncomplete:
    "Часть данных обработана не полностью. Проверьте настройки.",
  importUserSettings: "Открыть настройки",
  diagnosticExportDesktop:
    "Экспортируйте диагностику из настольного приложения.",
  diagnosticExported: "Журналы экспортированы: {filename}",
  diagnosticFile: "файл диагностики",
  diagnosticExportFailed:
    "Не удалось экспортировать диагностику. Повторите попытку.",
  debugTitle: "Журналы отладки",
  debugCount: "Журналов за этот сеанс: {count}",
  "debugCount.one": "Журналов за этот сеанс: {count}",
  debugDiagnostics: "Диагностика",
  debugUsageSubtitle: "Использование модели обработки памяти",
  debugTabs: "Вкладки диагностики",
  debugLogsTab: "Журналы",
  debugUsageTab: "Использование модели",
  debugFullscreen: "Во весь экран",
  debugExitFullscreen: "Выйти из полноэкранного режима",
  debugFullscreenLabel: "Показать журналы во весь экран",
  debugExitFullscreenLabel: "Выйти из полноэкранного просмотра журналов",
  usageRange15m: "Последние 15 минут",
  usageRange1h: "Последний час",
  usageRange6h: "Последние 6 часов",
  usageRange24h: "Последние 24 часа",
  usageRange7d: "Последние 7 дней",
  usageRange: "Период",
  usageRangeLabel: "Период использования модели",
  usageLoading: "Загрузка…",
  usageReadError: "Не удалось загрузить",
  usageLive: "Обновление каждые 5 с",
  usageTotalTokens: "Всего токенов",
  usageRequests: "Запросы",
  usageChartRegion: "Токены в минуту по промптам",
  usageChartTitle: "Расход токенов по промптам",
  usageChartSubtitle: "В минуту · всего токенов",
  usageBreakdown: "Сведения о промптах",
  usageCurrentRange: "Текущий период",
  usageRequestCount: "Запросов: {count}",
  "usageRequestCount.one": "Запросов: {count}",
  usageEmpty: "За этот период вызовов модели обработки памяти не было",
  usageNoChart: "Данных для графика пока нет",
  usageChartAria: "Расход токенов в минуту по промптам",
  debugPause: "Приостановить обновление",
  debugResume: "Возобновить обновление",
  debugRefresh: "Обновить сейчас",
  debugClose: "Закрыть журналы отладки",
  debugSearchPlaceholder:
    "Поиск по событиям, компонентам, кодам ошибок или содержимому",
  debugSearch: "Поиск в журналах",
  debugSource: "Источник журнала",
  debugAllSources: "Все источники",
  debugCapture: "Уровень сбора",
  debugDisplay: "Уровень отображения",
  debugAll: "Все",
  debugMatches: "Совпадений: {count}",
  "debugMatches.one": "Совпадений: {count}",
  debugLive: "Обновление в реальном времени",
  debugPaused: "Приостановлено",
  debugTruncated: "Слишком много журналов; список обрезан",
  debugReadError: "Не удалось загрузить",
  debugLoading: "Загрузка журналов…",
  debugEmpty: "Нет подходящих журналов",
  debugOpened: "Журналы отладки открыты",
  debugOpen: "Открыть журналы отладки",
  "这里需要 API Key，不是网址。请填写服务方提供的密钥。":
    "Введите ключ API, выданный сервисом, а не URL.",
  "服务商拒绝了这个 API Key（401）。请确认密钥属于当前填写的服务地址。":
    "Провайдер отклонил этот ключ API (401). Убедитесь, что ключ относится к указанному адресу.",
  "服务商没有这个模型名，请对照服务商的模型列表核对。":
    "Провайдер не знает такую модель. Сверьтесь со списком моделей провайдера.",
  "连不上接口地址，请检查网络和服务地址是否正确。":
    "Не удалось подключиться к адресу API. Проверьте сеть и адрес сервиса.",
  未提供接口地址: "Адрес API не указан",
  "请填写生成模型名。": "Введите название генеративной модели.",
  "请填写完整的 http:// 或 https:// 接口地址。":
    "Введите полный адрес API, начиная с http:// или https://.",
  "请填写向量模型名。": "Введите название модели эмбеддингов.",
  "请填写完整的向量模型接口地址。":
    "Введите полный адрес API модели эмбеддингов.",
  "这份配置在测试期间被改过，刚才测的是改动后的版本。配置已保存但未启用，请重新测试并启用。":
    "Конфигурация изменилась во время теста; проверена новая версия. Она сохранена, но не включена. Протестируйте и включите её снова.",
  "测试结果来自这份配置更早的版本。配置已保存但未启用，请重新测试并启用。":
    "Результат теста относится к более ранней версии конфигурации. Она сохранена, но не включена. Протестируйте и включите её снова.",
  "配置已保存，连接测试未通过。请核对配置后重试，历史对话尚未发送。":
    "Конфигурация сохранена, но тест подключения не пройден. Проверьте её и повторите попытку. История диалогов не отправлялась.",
  "配置已保存，尚未确认启用成功。请核对配置后重试。":
    "Конфигурация сохранена, но включение не подтверждено. Проверьте её и повторите попытку.",
  "配置尚未完成测试和启用，请核对后重试。":
    "Тестирование и включение не завершены. Проверьте конфигурацию и повторите попытку.",
  "配置尚未保存成功，已填写内容保留，可核对后重试。":
    "Не удалось подтвердить сохранение конфигурации. Введённые данные сохранены; проверьте их и повторите попытку.",
  "只需配置生成模型。向量嵌入已内置在本机，不为 System 1、System 2 分开选模型。":
    "Нужно настроить только генеративную модель. Векторные эмбеддинги встроены локально; отдельные модели для System 1 и System 2 не нужны.",
  "无法读取测试恢复记录，暂不发送新的测试。":
    "Не удалось прочитать запись восстановления теста; новый тест отправлен не будет.",
  "上次模型测试结果待核对，暂不修改或启用配置。":
    "Нужно проверить результат предыдущего теста модели; конфигурацию пока нельзя изменить или включить.",
  核对测试结果: "Проверить результат теста",
  "上次模型创建结果待核对，暂不开始新的操作。":
    "Нужно проверить результат предыдущего создания модели; новые операции пока не начнутся.",
  核对创建结果: "Проверить результат создания",
  "配置已保存，正在测试连接…": "Конфигурация сохранена; проверка подключения…",
  "连接通过，正在启用模型…": "Подключение успешно; включение модели…",
  刷新配置列表: "Обновить список конфигураций",
  自定义一个模型: "Настроить свою модель",
  已保存的模型配置: "Сохранённая конфигурация модели",
  "已保存，已启用": "Сохранена и включена",
  "已保存，连接通过": "Сохранена; подключение успешно",
  "已保存，测试未通过": "Сохранена; тест не пройден",
  "已保存，待测试": "Сохранена; ожидает теста",
  版本: "Версия",
  "需要允许 MemHub 访问钥匙串。系统询问时请选择“始终允许”；已保存的配置不会丢失。":
    "Разрешите MemHub доступ к Связке ключей. При запросе выберите «Всегда разрешать»; сохранённые конфигурации не пропадут.",
  "测试只向此接口发送测试内容，不发送历史对话。":
    "Тест отправляет на этот адрес только тестовые данные, без истории диалогов.",
  重试测试并启用: "Повторить тест и включить",
  测试并启用: "Протестировать и включить",
  编辑配置: "Изменить конфигурацию",
  新增配置: "Добавить конфигурацию",
  编辑: "Изменить",
  "。修改后需重新测试并设为当前模型，内容授权不会自动更新。":
    ". После изменений протестируйте снова и сделайте её текущей моделью. Разрешения на содержимое не обновятся автоматически.",
  "配置已在其他位置变化，草稿已保留。请取消编辑后重新打开核对。":
    "Конфигурация изменена в другом месте; черновик сохранён. Отмените редактирование и откройте её снова для проверки.",
  服务商预设: "Шаблон провайдера",
  自定义兼容服务: "Свой совместимый сервис",
  "只是帮你填好接口地址和一个模型名，不代表这个模型当前可用。 请对照服务商的模型列表核对后再保存。":
    "Подставляются адрес API и название модели, но доступность модели не гарантируется. Сверьтесь со списком моделей провайдера перед сохранением.",
  协议: "Протокол",
  "与模型服务提供的接口协议保持一致。":
    "Должен совпадать с протоколом сервиса модели.",
  配置名称: "Название конфигурации",
  "用于区分配置，不影响模型请求。":
    "Помогает различать конфигурации; на запросы к модели не влияет.",
  生成模型名: "Название генеративной модели",
  "例如：gpt-4o-mini…": "Например: gpt-4o-mini…",
  "填写服务方提供的完整模型标识。":
    "Введите полный идентификатор модели, выданный сервисом.",
  "高级：自行配置向量模型": "Дополнительно: своя модель эмбеддингов",
  向量配置模式: "Режим настройки эмбеддингов",
  本地内置: "Встроенная локальная модель",
  自定义向量模型: "Своя модель эмбеддингов",
  向量模型名: "Название модели эмбеддингов",
  "例如：text-embedding-3-small…": "Например: text-embedding-3-small…",
  向量模型来源: "Провайдер модели эмбеддингов",
  "本机 Ollama": "Локальный Ollama",
  "同源 OpenAI-compatible（共用 API Key）":
    "OpenAI-compatible того же источника (общий ключ API)",
  "向量模型 Base URL": "Base URL модели эмбеддингов",
  "为避免凭据外泄，向量地址必须与生成模型同源。":
    "Для защиты учётных данных адрес эмбеддингов должен иметь тот же источник, что и генеративная модель.",
  "使用内置本地向量模型；不会发送内容给远程向量服务。":
    "Используется встроенная локальная модель эмбеддингов; данные не отправляются во внешний сервис эмбеддингов.",
  自定义向量: "Свои эмбеддинги",
  尚未填写模型: "Модель не указана",
  尚未填写地址: "Адрес не указан",
  "填写服务接口地址，不是聊天网页地址。":
    "Введите адрес API, а не адрес веб-чата.",
  "（可选）": " (необязательно)",
  "保存后不会再次显示…": "После сохранения больше не отображается…",
  "留空保留已保存密钥；填写新密钥后需重新测试和启用。":
    "Оставьте пустым, чтобы сохранить текущий ключ; после ввода нового ключа протестируйте и включите снова.",
  "换了服务商后需要填写新的 API Key，已保存的密钥不会带到新地址。":
    "После смены провайдера введите новый ключ API; сохранённый ключ не передаётся на новый адрес.",
  "保存在系统安全存储中，不随安装包分发。":
    "Хранится в защищённом хранилище системы; не входит в дистрибутив приложения.",
  "关闭思考链（向支持的厂商下发 thinking-off 参数）":
    "Отключить цепочку рассуждений (передавать thinking-off поддерживающим провайдерам)",
  保存修改: "Сохранить изменения",
  保存配置: "Сохранить конфигурацию",
  "正在启用配置…": "Включение конфигурации…",
  测试并完成配置: "Протестировать и завершить настройку",
  保存并测试启用: "Сохранить, протестировать и включить",
  取消编辑: "Отменить изменения",
  "密钥保存在系统安全存储；测试仅发送测试内容。":
    "Ключ хранится в защищённом хранилище; тесты отправляют только тестовые данные.",
  当前使用: "Используется",
  未启用: "Не включена",
  测试目标: "Цель теста",
  "。仅发送测试内容，不发送历史对话。":
    ". Отправляются только тестовые данные, без истории диалогов.",
  测试连接: "Проверить подключение",
  设为当前模型: "Сделать текущей моделью",
  删除: "Удалить",
  "粘贴 API Key": "Вставьте ключ API",
  "密钥尚未保存成功，已填写内容保留，可重试。":
    "Не удалось сохранить ключ. Введённые данные сохранены; можно повторить попытку.",
  生成接收方: "Получатель генерации",
  向量接收方: "Получатель эмбеддингов",
  本机: "На этом устройстве",
  "。启用此配置后按上方选择处理历史及未来允许来源。":
    ". После включения этой конфигурации выбранные выше параметры определяют, какие прошлые и будущие разрешённые источники обрабатываются.",
  "保存 key": "Сохранить ключ",
  "还没有 key？去申请": "Нет ключа? Получить",
  删除这张卡: "Удалить эту карточку",
  "密钥已配置，当前读取状态待确认。":
    "Ключ настроен; статус доступа нужно подтвердить.",
  "等待 key：其余都已经填好了，贴上 key 就能用。":
    "Ожидается ключ. Остальное уже настроено — вставьте ключ, чтобы продолжить.",
  "key 已保存，还没测过这个模型能不能用。":
    "Ключ сохранён; модель ещё не протестирована.",
  "Ollama（本机）": "Ollama (локально)",
  "内置 bge-small-zh-v1.5": "Встроенная bge-small-zh-v1.5",
  通过: "Пройдено",
  "HY 托管模型尚不可用": "Управляемая модель HY недоступна",
  "缺少 API Key": "Нет ключа API",
  服务不可达: "Сервис недоступен",
  "认证失败；更新 key 后再检测，会自动接着处理":
    "Ошибка аутентификации; обновите ключ и проверьте снова — обработка продолжится автоматически.",
  请求频率受限: "Превышен лимит запросов",
  "模型账户余额不足；充值或换 key 后再检测，会自动接着处理":
    "Недостаточно средств на счёте модели; пополните баланс или смените ключ и проверьте снова — обработка продолжится.",
  没有找到模型: "Модель не найдена",
  "该网关不提供模型列表，已改由生成调用验证":
    "Шлюз не предоставляет список моделей; проверка выполнена через запрос генерации.",
  服务返回不符合协议: "Ответ сервиса не соответствует протоколу",
  结构化输出未通过: "Тест структурированного вывода не пройден",
  "需要填写 Embedding 模型": "Укажите модель эмбеддингов",
  "Embedding 模型不可用": "Модель эмбеддингов недоступна",
  "请先核对上次模型测试结果，暂不修改或启用配置。":
    "Сначала проверьте результат предыдущего теста модели; конфигурацию пока нельзя изменить или включить.",
  "本地恢复记录已变化，仍保持锁定。请重新打开后核对。":
    "Локальная запись восстановления изменилась. Конфигурация по-прежнему заблокирована; откройте её снова и проверьте.",
  测试结果: "Результаты теста",
  "暂不支持一键插入：它没有 MCP 通道，无法发放可撤销的记忆凭证。":
    "Подключение в один клик недоступно: у этого приложения нет канала MCP для отзываемых учётных данных памяти.",
  应用控制通道暂不可用: "Канал управления приложением недоступен",
  appFailureReason: " Причина: {reason}. ",
  appFailureReread:
    "Статус подключения также не удалось перезагрузить; отображаемое состояние может быть устаревшим.",
  模型: "Модель",
  连接其他应用: "Подключить другие приложения",
  帮助与诊断: "Справка и диагностика",
  设置分组: "Разделы настроек",
  记忆模型: "Модель памяти",
  当前模型: "Текущая модель",
  "分别管理记忆整理与搜索所使用的模型。":
    "Отдельно управляйте моделями для обработки и поиска записей памяти.",
  记忆整理: "Обработка памяти",
  "把会话理解并整理成可长期使用的记忆。":
    "Преобразует диалоги в записи памяти для долгосрочного использования.",
  记忆搜索: "Поиск по памяти",
  "在需要时找到与你的问题最相关的记忆。":
    "При необходимости находит записи памяти, наиболее релевантные вашему вопросу.",
  "本地内置（推荐）": "Встроенная локальная модель (рекомендуется)",
  "Embedding · 配置记忆整理后可自定义":
    "Embedding · Настраивается после конфигурации обработки памяти",
  "Embedding · 本机处理，无需额外配置":
    "Embedding · Локальная обработка, дополнительная настройка не требуется",
  "正在读取模型…": "Загрузка модели…",
  模型状态暂不可读: "Статус модели недоступен",
  尚未配置模型: "Модель не настроена",
  修改模型: "Изменить модель",
  配置模型: "Настроить модель",
  刷新模型状态: "Обновить статус модели",
  "本机 / 受管端点": "Локальный / управляемый адрес",
  接口地址待核对: "Нужно проверить адрес API",
  "模型状态未能读取，请重试。":
    "Не удалось прочитать статус модели. Повторите попытку.",
  "模型状态刷新未成功，仍显示上次确认的模型状态；请重试。":
    "Не удалось обновить статус модели. Показано последнее подтверждённое состояние; повторите попытку.",
  "上次模型测试或创建还待核对，配置暂时不能改动。请先在下方模型配置区核对结果； 现有配置和原文不会被改动。":
    "Нужно проверить результат предыдущего теста или создания модели. Проверьте его ниже, прежде чем вносить изменения. Конфигурация и исходный текст не изменены.",
  用模型沉淀记忆: "Создавать записи памяти с помощью модели",
  "开启后，导入和同步到本机的会话会交给当前模型整理成记忆，会产生模型调用。 停止后不再调用模型、不再整理记忆，但会话仍会照常导入和保存；已排队的内容会留着，重新开启后继续。 模型配置始终保留，重新开启不需要重新填写。":
    "Когда включено, импортированные и синхронизированные диалоги обрабатываются текущей моделью в записи памяти — это расходует вызовы модели. Когда выключено, диалоги по-прежнему импортируются и сохраняются; очередь продолжит обрабатываться после повторного включения. Конфигурация модели сохраняется.",
  "启用未能确认，仍显示上次确认的状态；请重试。":
    "Не удалось подтвердить включение. Показано последнее подтверждённое состояние; повторите попытку.",
  "停止未能确认，模型可能仍在整理记忆，请重试。":
    "Не удалось подтвердить остановку. Модель, возможно, ещё обрабатывает записи памяти; повторите попытку.",
  "当前安装还不能读取这个开关，请确认后台服务已启动后重试。":
    "Эта установка не может прочитать переключатель. Убедитесь, что фоновая служба запущена, и повторите попытку.",
  关于: "О программе",
  "此应用还没有读取授权，请先授权它的对话目录。":
    "У этого приложения ещё нет доступа на чтение. Сначала разрешите доступ к папке его диалогов.",
  "此应用的读取授权已撤销，请重新授权。":
    "Доступ на чтение для этого приложения отозван. Разрешите его снова.",
  "此应用的对话目录已被重建，请重新授权。":
    "Папка диалогов этого приложения была пересоздана. Разрешите доступ снова.",
  "此应用的对话目录现在读不到，请确认应用已安装并产生过对话。":
    "Папку диалогов этого приложения не удаётся прочитать. Убедитесь, что приложение установлено и в нём есть диалоги.",
  "此应用的对话目录已变更，请按最新目录重新授权。":
    "Папка диалогов этого приложения изменилась. Разрешите доступ к новой папке.",
  "此应用还没有读取授权，请先授权它的对话目录。完成后再开启持续同步。":
    "У этого приложения ещё нет доступа на чтение. Разрешите доступ к папке его диалогов, затем включите постоянную синхронизацию.",
  "此应用的读取授权已撤销，请重新授权。完成后再开启持续同步。":
    "Доступ на чтение для этого приложения отозван. Разрешите его снова, затем включите постоянную синхронизацию.",
  "此应用的对话目录已被重建，请重新授权。完成后再开启持续同步。":
    "Папка диалогов этого приложения была пересоздана. Разрешите доступ снова, затем включите постоянную синхронизацию.",
  "此应用的对话目录现在读不到，请确认应用已安装并产生过对话。完成后再开启持续同步。":
    "Папку диалогов этого приложения не удаётся прочитать. Убедитесь, что приложение установлено и в нём есть диалоги, затем включите постоянную синхронизацию.",
  "此应用的对话目录已变更，请按最新目录重新授权。完成后再开启持续同步。":
    "Папка диалогов этого приложения изменилась. Разрешите доступ к новой папке, затем включите постоянную синхронизацию.",
  "此应用的读取授权已失效，请重新授权后再开启持续同步。":
    "Доступ на чтение для этого приложения истёк. Разрешите его снова, прежде чем включать постоянную синхронизацию.",
  "此应用的授权或目录已变化，已按最新状态显示，请确认后再试。":
    "Разрешения или папка этого приложения изменились. Проверьте обновлённый статус и повторите попытку.",
  模型配置表单: "Форма конфигурации модели",
  "部分运行状态暂时无法读取，当前显示最后读到的状态，请重试。":
    "Часть сведений о статусе недоступна. Показано последнее известное состояние; повторите попытку.",
  "当前安装还不能设置登录后后台启动。":
    "Эта установка не может настроить запуск при входе в систему.",
  "登录后后台启动设置未能确认，请重试。":
    "Не удалось подтвердить настройку запуска при входе. Повторите попытку.",
  "当前安装还不能退出界面，请从菜单栏退出。":
    "Эта установка не может закрыть окно. Используйте строку меню.",
  "退出界面未能确认，请重试。":
    "Не удалось подтвердить выход. Повторите попытку.",
  登录后启动后台服务: "Запускать фоновую службу при входе",
  "无需打开界面，持续同步、整理和提供记忆。":
    "Синхронизация, обработка и предоставление памяти без открытия приложения.",
  导出日志: "Экспорт журналов",
  "把本机运行日志打包成一个文件，发给开发排查。":
    "Упаковывает все локальные журналы. Они могут содержать текст диалогов или локальные пути; отправляйте их только доверенным разработчикам.",
  "确定退出 One Memory 界面吗？": "Закрыть окно MemHub?",
  "后台服务会继续同步、整理和提供记忆。":
    "Фоновая служба продолжит синхронизацию, обработку и предоставление памяти.",
  取消: "Отмена",
  退出界面: "Закрыть окно",
  应用详情: "Сведения о приложении",
  返回应用: "Назад к приложениям",
  同步开关: "Синхронизация",
  "开启后处理历史待办，并持续接收此应用的新会话。":
    "Когда включено, обрабатывается накопленная история и принимаются новые диалоги из этого приложения.",
  "正在读取已有对话，首次开启可能需要一两分钟，请不要重复点击。":
    "Чтение существующих диалогов. Первый запуск может занять минуту-другую; не нажимайте повторно.",
  "将补充冻结期间的新对话；原有 pending Job 继续处理。":
    "Диалоги, полученные во время заморозки, будут добавлены; существующие задачи в очереди продолжат обрабатываться.",
  一键插入应用: "Подключение в один клик",
  "安装记忆连接，并默认允许此应用使用已授权记忆。":
    "Установить подключение к памяти и по умолчанию разрешить этому приложению использовать разрешённые записи памяти.",
  允许使用记忆: "Разрешить доступ к памяти",
  连接状态: "Статус подключения",
  已接通: "Подключено",
  "已安装，但取不到记忆": "Установлено, но не получает записи памяти",
  未接通: "Не подключено",
  状态待核对: "Нужно проверить статус",
  检测中: "Проверка",
  未开启关联: "Связь выключена",
  "正在开启处理…": "Запуск обработки…",
  "正在冻结处理…": "Заморозка обработки…",
  处理中: "Обработка",
  已冻结: "Заморожено",
  "此应用的处理开关结果未能确认，其他应用不受影响。":
    "Не удалось подтвердить переключение обработки для этого приложения. Другие приложения не затронуты.",
  "暂时无法刷新来源状态，请重试；未更改同步或读取权限。":
    "Не удалось обновить статус источника. Повторите попытку; разрешения на синхронизацию и чтение не изменены.",
  "暂时无法读取连接状态，当前状态待核对；请重试。":
    "Не удалось прочитать статус подключения. Текущее состояние нужно проверить; повторите попытку.",
  "已接通。已经打开的对话不会中途出现这个工具，请新开一轮。":
    "Подключено. В уже открытом диалоге инструмент не появится; начните новый.",
  "本次连接探测失败，当前状态待核对；未把上次读取当作本次成功。":
    "Проверка подключения не удалась. Текущее состояние нужно проверить; прошлый результат не засчитан как новый успех.",
  上次读取: "Последнее чтение",
  appSettingsTitle: "Настройки приложения {name}",
  appMemoryScope:
    "Разрешить {name} искать записи памяти в пределах разрешённой области.",
  appRestart:
    "Инструмента памяти нет в текущем диалоге {name}. Полностью закройте и снова откройте {name}; инструмент появится в новом диалоге.",
  appConnectFailed:
    "Подключение этого приложения в один клик не завершено. Другие приложения не затронуты. {reason}",
  appDisconnectFailed:
    "Не удалось удалить подключение и отозвать доступ к памяти. {name} по-прежнему может использовать память; повторите попытку. {reason}",
  appPermissionFailed:
    "Не удалось подтвердить доступ к памяти для этого приложения. Повторите попытку. {reason}",
  appRevokedModified:
    "Доступ к памяти отозван: {name} больше не может искать записи памяти. Файл конфигурации был изменён, поэтому MemHub не удалил его автоматически; удалите подключение MemHub вручную.",
  appRevokedRetained:
    "Доступ к памяти отозван: {name} больше не может искать записи памяти. Файл подключения не удалён; проверьте его ещё раз.",
  appRevokedReview:
    "Доступ к памяти отозван: {name} больше не может искать записи памяти. Полное удаление не подтверждено; проверьте эти пути вручную: {paths}.",
  appRemovalReview:
    "Полное удаление подключения не подтверждено. Проверьте эти пути вручную: {paths}.",
  appProfilesLabel: "Профили {name}",
  appProfileOn: "{profile}: подключён",
  appProfileOff: "{profile}: не подключён",
  appProfilesHint:
    "Переключатель устанавливает подключение к памяти и выдаёт доступ каждому найденному профилю, а при выключении отзывает доступ у всех. Пока он включён, новые профили подключаются автоматически.",
  appProfilesNone:
    "Профили {name} не найдены. Создайте профиль и вернитесь на эту страницу.",
  appProfilesConnectFailed:
    "Не удалось подключить профили: {profiles}. Остальные профили сохранены. {reason}",
  appProfilesDisconnectFailed:
    "Не удалось отключить профили, они всё ещё могут использовать память: {profiles}. Повторите попытку. {reason}",
  appProfilesRevokedRetained:
    "Доступ к памяти для этих профилей отозван, но не все файлы подключения удалены; проверьте их: {profiles}.",
  appProfileNameUnsupported:
    "имя профиля может содержать только строчные буквы, цифры и дефисы, не более 32 символов",
  "apps.autoHostProfilesFailed":
    "Не удалось автоматически подключить эти профили {name} ({profiles}){reason}.",
  "Cursor 的连接凭证已更新。请完全退出并重新打开 Cursor，再开始新对话。":
    "Учётные данные подключения Cursor изменились. Полностью закройте и снова откройте Cursor, прежде чем начинать новый диалог.",
  连接其他工具: "Подключить другие инструменты",
  "本机连接材料仅在桌面 App 中可用。":
    "Данные локального подключения доступны только в настольном приложении.",
  "仅本机 MCP / 命令工具。纯网页聊天不支持直接连接本机。":
    "Только для локальных инструментов MCP и командной строки. Браузерный чат не может подключаться к этому устройству напрямую.",
  "列表内工具请用上方一键安装，此处仅供未列出的工具。":
    "Для инструментов из списка используйте настройку в один клик выше. Этот раздел — для инструментов, которых нет в списке.",
  "正在读取本机连接配置…": "Загрузка данных локального подключения…",
  "未能读取连接配置。": "Не удалось загрузить данные подключения.",
  重试: "Повторить",
  连接材料格式: "Формат данных подключения",
  连接提示词: "Промпт подключения",
  "MCP 配置": "Конфигурация MCP",
  命令: "Команда",
  本机连接材料: "Данные локального подключения",
  "包含本机路径，不含记忆和密钥。片段会运行本机 MemHub 连接程序；粘贴前请确认，MemHub 需保持运行。":
    "Содержит локальные пути, но не записи памяти и не ключи. Фрагмент запускает локальный коннектор MemHub; проверьте его перед вставкой и не закрывайте MemHub.",
  "已复制，尚未建立连接。": "Скопировано; подключение пока не установлено.",
  "复制失败，可在上方选中文本复制。":
    "Не удалось скопировать. Выделите и скопируйте текст выше вручную.",
  复制提示词: "Копировать промпт",
  复制配置: "Копировать конфигурацию",
  复制命令: "Копировать команду",
  记忆库分页: "Страницы библиотеки памяти",
  上一页: "Предыдущая страница",
  下一页: "Следующая страница",
  "把熟悉的背景，带到这里": "Перенесите сюда свой контекст",
  "你的记忆，从这里接上": "Соберите свою память вместе",
  "这张卡还没测过。点一次「设为当前模型」会先自动测，如果还是不行，说明端点、key 或模型名有一处不对。":
    "Эта конфигурация не тестировалась. При выборе её текущей моделью сначала запустится тест; если он не пройдёт, проверьте адрес, ключ и название модели.",
  "这类模型接口暂时用不了，换一张卡。":
    "Этот тип адреса модели пока не поддерживается. Используйте другую конфигурацию.",
  "这张卡缺了向量化模型，补上才能启用。":
    "Для включения этой конфигурации нужна модель эмбеддингов.",
  "这张卡已经不在了，可能刚被删掉。刷新一下。":
    "Эта конфигурация больше недоступна — возможно, её удалили. Обновите список.",
  "这张卡刚被改过。刷新一下再试，免得覆盖掉别处的改动。":
    "Эта конфигурация только что изменилась. Обновите данные перед повторной попыткой, чтобы не перезаписать чужие изменения.",
  onboardingModelOperationFailed: "Операция не удалась: {code}",
  onboardingModelSkipDestination: "Пропустить и перейти к приложениям",
  首次设置进度: "Ход первоначальной настройки",
  导入历史: "Импорт истории",
  连接应用: "Подключение приложений",
  沉淀记忆: "Создание памяти",
  本地存储: "Хранится локально",
  "对话与记忆保存在本机；使用云模型或已授权的助手时，相关内容会发送给对应服务。":
    "Диалоги и записи памяти хранятся на этом устройстве. При использовании облачных моделей или разрешённых ассистентов соответствующие данные отправляются в эти сервисы.",
  "来源位置、身份或状态已变化，请重新选择并核对。尚未提交本次授权。":
    "Расположение, идентификатор или статус источника изменились. Выберите и проверьте его снова. Это разрешение не отправлено.",
  "来源位置、身份或状态已变化，正在等待本次提交回执。不会自动扩大已提交范围。":
    "Расположение, идентификатор или статус источника изменились. Ожидается подтверждение отправки; отправленная область не будет расширена автоматически.",
  测试实例不接入应用: "Тестовые экземпляры не подключаются к приложениям",
  "请先把 MemHub 拖到“应用程序”并从那里重新打开，才能接入应用。":
    "Переместите MemHub в «Программы» и откройте его оттуда, чтобы подключить приложения.",
  "部分应用未能完成接入，已成功的设置会保留，可进入应用后单独处理。":
    "Некоторые приложения не удалось подключить. Успешные настройки сохранены; остальные можно настроить по отдельности в разделе «Приложения».",
  onboardingConnectionPartial:
    "Подключено приложений: {success}; не завершено: {failed} ({names}). Их можно настроить по отдельности в разделе «Приложения».",
  "首次设置的完成状态未能保存。已确认的导入回执不受影响；下次启动可能再次出现此页。":
    "Не удалось сохранить завершение первоначальной настройки. Подтверждённые импорты не затронуты; эта страница может появиться при следующем запуске.",
  "首次设置的完成状态尚未确认。已确认的导入回执不受影响；下次启动可能再次出现此页。":
    "Завершение первоначальной настройки не подтверждено. Подтверждённые импорты не затронуты; эта страница может появиться при следующем запуске.",
  "位置检测未完成，尚未读取历史。可以重新检测，或先进入工作台。":
    "Определение расположения не завершено, история не прочитана. Проверьте снова или перейдите в рабочую область.",
  "暂时无法核对来源，尚未提交授权。选择已保留，可以重试。":
    "Сейчас нельзя проверить источники, поэтому разрешение не отправлено. Выбор сохранён; можно повторить попытку.",
  "本次没有来源获准导入。请核对下方原因后重试，或到应用页查看状态。":
    "Ни один источник не получил разрешения на импорт. Проверьте причины ниже и повторите попытку или посмотрите статус в разделе «Приложения».",
  "开始导入的结果尚未确认，选择已保留。可以重试，或进入应用页核对。":
    "Начало импорта не подтверждено. Выбор сохранён; повторите попытку или проверьте в разделе «Приложения».",
  未完成: "Не завершено",
  核对中: "Проверка",
  仍进入应用页: "Всё равно перейти к приложениям",
  仍进入工作台: "Всё равно перейти в рабочую область",
  仍进入设置: "Всё равно перейти в настройки",
  重试保存并进入: "Повторить сохранение и продолжить",
  "稍后添加，先进入": "Добавить позже и продолжить",
  "正在开始导入…": "Запуск импорта…",
  开始导入: "Начать импорт",
  "不用从头介绍自己。从常用的 App 开始，把已有背景带过来。":
    "Не нужно начинать с нуля. Перенесите контекст из часто используемых приложений.",
  "确认前只检查应用信息，不读取对话，也不安装连接。":
    "До подтверждения проверяются только сведения о приложениях. Диалоги не читаются, подключения не устанавливаются.",
  "本机 AI 应用": "ИИ-приложения на этом устройстве",
  重新检测位置: "Проверить расположения снова",
  "检测中…": "Проверка…",
  重新检测: "Проверить снова",
  候选应用: "Доступные приложения",
  "正在检测其他应用…": "Поиск других приложений…",
  检测未完成: "Проверка не завершена",
  已全选: "Выбраны все",
  全选: "Выбрать все",
  onboardingImportHistory: "Импортировать историю из {name}",
  正在核对本机保存位置: "Проверка расположения локального хранилища",
  已发现: "Найдено",
  位置待核对: "Нужно проверить расположение",
  "当前没有候选来源。可以稍后在应用页添加历史包。":
    "Источники не найдены. Архив истории можно добавить позже в разделе «Приложения».",
  "候选来源尚未加载。可以检测位置，或先进入工作台。":
    "Источники не загружены. Проверьте расположения или перейдите в рабочую область.",
  本次导入: "Этот импорт",
  "个 App 的历史对话": "прил. — история диалогов",
  "把零散的会话，": "Соберите разрозненные диалоги",
  汇成完整的记忆: "в единую память",
  "少一些重复交代，多一些理解。": "Меньше повторений. Больше понимания.",
  onboardingMoreApps:
    "Прокрутите, чтобы увидеть больше · осталось приложений: {count}",
  "onboardingMoreApps.one":
    "Прокрутите, чтобы увидеть больше · осталось приложений: {count}",
  onboardingMoreImportApps:
    "Прокрутите по горизонтали · осталось приложений: {count}",
  onboardingImportChecking: "Проверяем результат импорта; ваш выбор сохранён",
  onboardingAllApps: "Показаны все приложения: {count}",
  默认位置: "Расположение по умолчанию",
  自定义位置: "Своё расположение",
  尚未检测: "Не проверено",
  未发现: "Не найдено",
  无法访问: "Нет доступа",
  位置无效: "Недопустимое расположение",
  "需要更新 Core": "Нужно обновить Core",
  "候选位置不存在，可能尚无历史或路径不同。":
    "Это расположение не существует. Возможно, истории ещё нет или путь другой.",
  "无法检查此位置，请核对目录访问权限后重试。":
    "Не удалось проверить это расположение. Проверьте права доступа к папке и повторите попытку.",
  "此位置未通过安全检查，不能读取。":
    "Это расположение не прошло проверку безопасности и не может быть прочитано.",
  "当前 Core 未提供可确认的位置身份，需更新后再选择。":
    "Текущий Core не предоставляет проверяемый идентификатор расположения. Обновите его, прежде чем выбирать.",
  暂不支持历史导入: "Импорт истории пока не поддерживается",
  "当前来源不可读取，请重新检测。":
    "Текущий источник недоступен для чтения. Проверьте снова.",
  "来源目录暂时不可读取，请核对位置和权限。":
    "Папка источника временно недоступна для чтения. Проверьте её расположение и права доступа.",
  "来源目录身份已变化，请返回重新检测。":
    "Идентификатор папки источника изменился. Вернитесь и проверьте снова.",
  "来源权限已变化，请返回重新确认。":
    "Разрешения источника изменились. Вернитесь и подтвердите снова.",
  "此来源尚未获得读取授权。": "У этого источника ещё нет доступа на чтение.",
  "自定义来源位置需要重新确认。":
    "Своё расположение источника нужно подтвердить снова.",
  "当前 Core 未启用历史导入。": "В текущем Core импорт истории не включён.",
  onboardingImportRefused:
    "Импорт не разрешён ({reason}). Проверьте источник и повторите попытку.",
  确认自定义位置: "Подтвердите своё расположение",
  关闭位置确认: "Закрыть подтверждение расположения",
  "此位置来自当前配置，并非应用默认目录。确认位置不会开始读取历史。":
    "Это расположение взято из текущей конфигурации, а не из папки приложения по умолчанию. Подтверждение не запускает чтение истории.",
  onboardingConfirmHistory: "Подтвердите, что здесь хранится история {name}",
  确认此位置: "Подтвердить расположение",
  "对话正在汇入，记忆从这里开始": "Диалоги поступают. Память начинается здесь",
  "导入进行中 · 顺手连接应用": "Идёт импорт · подключите приложения",
  "对话已保存，记忆尚待生成。":
    "Диалоги сохранены; записи памяти ещё не созданы.",
  "导入在后台继续，无需等待。": "Импорт продолжается в фоне. Ждать не нужно.",
  "正在重新核对来源；已保存数量仍按任务回执显示。":
    "Повторная проверка источников; число сохранённых по-прежнему отражает подтверждения задач.",
  "本次历史导入已暂停，已保存的对话还在。":
    "Импорт истории приостановлен. Сохранённые диалоги доступны.",
  "导入等待处理，已保存的对话还在。":
    "Импорт ожидает обработки. Сохранённые диалоги доступны.",
  停止后续操作: "Остановить оставшиеся действия",
  暂停导入: "Приостановить импорт",
  继续导入: "Продолжить импорт",
  "配置模型，沉淀记忆": "Настройте модель для создания памяти",
  导入与原文: "Импорт и исходный текст",
  "本次导入已结束。已保存的对话无需重复导入。":
    "Импорт завершён. Сохранённые диалоги не нужно импортировать повторно.",
  "对话先保存在本机。你可以同时设置后续连接。":
    "Диалоги сначала сохраняются локально. Тем временем можно настроить подключения.",
  "导入不会自动开放应用读取权限。配置模型后，才会开始生成记忆。":
    "Импорт не даёт приложениям доступ на чтение автоматически. Записи памяти создаются только после настройки модели.",
  onboardingHistoryWindow:
    "Найдено много данных диалогов. Чтобы ограничить расход и выровнять хронологию приложений, в этот импорт входят только последние 3 дня по всем приложениям.",
  导入说明: "Об импорте",
  正在导入你的会话: "Импорт ваших диалогов",
  "后台将持续为你导入，可以放心进入下一步":
    "Импорт продолжится в фоне. Можно переходить дальше.",
  未开始的导入: "Импорты, которые не удалось начать",
  "未能开始导入，其他已接受任务不受影响。":
    "Не удалось начать импорт. Другие принятые задачи не затронуты.",
  查看未导入的应用: "Показать неимпортированные приложения",
  让记忆继续生长: "Пусть память продолжает расти",
  连接你的应用: "Подключите приложения",
  "适用于本次选择且支持的应用。":
    "Для выбранных приложений, поддерживающих это подключение.",
  一键接入应用: "Подключить приложения в один шаг",
  "安装记忆连接，让所选应用用上 MemHub 中的相关记忆。":
    "Установите подключения к памяти, чтобы выбранные приложения могли использовать нужные записи памяти из MemHub.",
  "当前使用独立测试数据目录，不会修改本机应用的连接配置。":
    "Этот экземпляр использует изолированную папку тестовых данных и не меняет подключения локальных приложений.",
  "点击底部按钮后，后台分别接入各应用；某个应用失败不影响其他应用。":
    "После нажатия кнопки ниже приложения подключаются по отдельности в фоне; сбой одного не влияет на другие.",
  "暂时无法连接应用，当前不会安装连接。":
    "Сейчас нельзя подключить приложения. Подключение не будет установлено.",
  onboardingBatchStopped:
    "Оставшиеся действия остановлены; проверьте подтверждения уже отправленных действий. ",
  onboardingBatchResult:
    "Подтверждено: {confirmed}, не подтверждено: {failed}.",
  "已确认项保留，请核对未确认任务后重试。":
    " Подтверждённые действия сохранены. Проверьте неподтверждённые задачи перед повторной попыткой.",
  本次应用导入进度: "Ход импорта выбранных приложений",
  正在导入: "Импорт",
  已暂停: "Приостановлено",
  已中断: "Прервано",
  导入遇到问题: "Ошибка импорта",
  导入完成: "Импорт завершён",
  已取消: "Отменено",
  已保存: "Сохранено",
  onboardingAppProgress: "Ход импорта {name}",
  onboardingSavedMessages: "Сохранено сообщений: {count}",
  onboardingScannedOutsideRange:
    "Просмотрено: {count}; нет диалогов в нужном периоде",
  onboardingScannedNothingToSave:
    "Просмотрено: {count}; нет диалогов для сохранения",
  onboardingScannedFilteringRecent:
    "Просмотрено: {count}; отбор за последние 3 дня",
  onboardingScannedFiltering: "Просмотрено: {count}; идёт отбор",
  onboardingScannedNotSaved: "Просмотрено: {count}; ещё не сохранено",
  没有可保存的历史对话: "Нет диалогов из истории для сохранения",
  读取授权不可用: "Доступ на чтение недоступен",
  onboardingReadErrors: " · ошибок чтения: {count}",
  "配置详情，按需要调整": "Сведения о конфигурации, если нужно",
  "连接未通过，配置已保留": "Подключение не удалось; конфигурация сохранена",
  "选好模型，让记忆开始沉淀":
    "Выберите модель, чтобы начать создавать записи памяти",
  "连接设置已保存\n成功项保留，不重复执行。":
    "Настройки подключения сохранены.\nУспешные шаги сохранены и не будут повторяться.",
  "已选择：持续同步 · 一键接入":
    "Выбрано: постоянная синхронизация · подключение в один шаг",
  "沉淀记忆 · 使用你选择的模型": "Создание памяти · с выбранной моделью",
  "当前无法启用模型，已填写内容保留。":
    "Сейчас нельзя включить модель. Введённые данные сохранены.",
  "模型连接测试通过，可以启用并开始沉淀记忆。":
    "Подключение к модели успешно. Можно включить её для создания записей памяти.",
  "先测试模型连通性；测试通过后再启用。":
    "Сначала проверьте подключение к модели; включите её после успешного теста.",
  "跳过，先进入应用": "Пропустить и перейти к приложениям",
  "正在测试模型…": "Тестирование модели…",
  测试模型连通性: "Проверить подключение к модели",
  "正在启用…": "Включение…",
  确认启用: "Подтвердить и включить",
  核对提交状态: "Проверить статус отправки",
  模型配置: "Конфигурация модели",
  模型参数: "Параметры модели",
  "选择服务商，填入 API Key。核对已有配置后再继续。":
    "Выберите провайдера и введите ключ API. Проверьте существующую конфигурацию, прежде чем продолжить.",
  模型服务商: "Провайдер модели",
  选择服务商: "Выберите провайдера",
  "已安全保存，留空保留": "Сохранён надёжно; оставьте пустым, чтобы не менять",
  "输入服务商提供的 API Key": "Введите ключ API от провайдера",
  "连接未通过，请检查 API Key、模型与接口地址后重试。配置已保留。":
    "Подключение не удалось. Проверьте ключ API, модель и адрес API и повторите попытку. Конфигурация сохранена.",
  "需要允许 MemHub 访问钥匙串。系统询问时请选择“始终允许”。":
    "Разрешите MemHub доступ к Связке ключей. При запросе выберите «Всегда разрешать».",
  "留空保留已保存密钥；填写新密钥后需重新测试。":
    "Оставьте пустым, чтобы сохранить текущий ключ; после ввода нового ключа протестируйте снова.",
  "密钥保存在系统安全存储中，不写入对话或日志。":
    "Ключ хранится в защищённом хранилище системы, а не в диалогах или журналах.",
  配置详情与高级设置: "Сведения о конфигурации и дополнительные настройки",
  服务商待选择: "Провайдер не выбран",
  模型待填写: "Модель не указана",
  本地向量模型: "Локальная модель эмбеддингов",
  向量模型待填写: "Модель эмбеддингов не указана",
  思考已关闭: "Рассуждения отключены",
  思考未关闭: "Рассуждения не отключены",
  "预设已填好，只有自定义服务或特殊需求才需要修改。":
    "Шаблон заполнен. Меняйте его только для своих сервисов или особых требований.",
  返回服务商选择: "Назад к выбору провайдера",
  模型名称: "Название модели",
  "Anthropic（暂不支持）": "Anthropic (пока не поддерживается)",
  "HY 托管（当前不可用）": "Управляемая HY (сейчас недоступна)",
  向量模型: "Модель эмбеддингов",
  "本地 ONNX · 内置": "Локальная ONNX · встроенная",
  "超时（秒）": "Тайм-аут (с)",
  最大并发: "Макс. параллельность",
  上下文窗口: "Контекстное окно",
  "自动 / 可选": "Автоматически / необязательно",
  关闭思考模式: "Отключить режим рассуждений",
  "自定义向量配置可展开；远程向量服务须遵守凭据边界。":
    "Можно развернуть настройки своих эмбеддингов. Внешние сервисы эмбеддингов должны соблюдать границы учётных данных.",
  "切换服务商或模型后，需要重新测试并启用。":
    "После смены провайдера или модели протестируйте и включите снова.",
  向量模型名称: "Название модели эмбеддингов",
  向量协议: "Протокол эмбеддингов",
  "同源 OpenAI-compatible（共用 Key）":
    "OpenAI-compatible того же источника (общий ключ)",
  "向量 Base URL": "Base URL эмбеддингов",
  "远程向量地址须与生成服务同源；本机地址仍需校验。":
    "Внешний адрес эмбеддингов должен иметь тот же источник, что и генеративный сервис; локальные адреса тоже проверяются.",
  "放弃旧记录，重新配置": "Отбросить старую запись и настроить заново",
  模型提交状态: "Статус отправки модели",
  "正在测试连接…": "Проверка подключения…",
  "正在提交保存与启用，请等待回执…":
    "Сохранение и включение; ожидание подтверждения…",
  "操作结果未确认，已填写内容保留，请核对后重试。":
    "Результат не подтверждён. Введённые данные сохранены; проверьте их перед повторной попыткой.",
  本次沉淀: "Настройка памяти",
  让记忆从这里开始: "Начните создавать память здесь",
  对话来源: "Источники диалогов",
  尚无已选来源: "Источники не выбраны",
  onboardingSourcesSummary: "{names} — всего приложений: {count}",
  onboardingImportedConversations:
    "Импортировано диалогов: {count}; переключатели источников по-прежнему определяют, что синхронизируется на это устройство дальше.",
  "处理服务：": "Сервис обработки:",
  待选择: "Не выбрано",
  "相关对话会发送给所选服务商。":
    "Соответствующие диалоги будут отправлены выбранному провайдеру.",
  "测试连接只发送测试内容。启用后会使用此模型整理已导入和之后同步的会话。":
    "Тест подключения отправляет только тестовые данные. После включения модель обрабатывает импортированные и последующие синхронизированные диалоги.",
  "当前不能在此启用模型。": "Сейчас здесь нельзя включить модель.",
  "配置已修改，需要重新测试。": "Конфигурация изменена. Протестируйте снова.",
  onboardingIntegerRange: "Введите целое число от {min} до {max}.",
  "请填写配置名称。": "Введите название конфигурации.",
  "请填写实际可用的模型 ID。": "Введите идентификатор доступной модели.",
  "请填写有效的模型接口地址。": "Введите корректный адрес API модели.",
  "请填写 API Key。": "Введите ключ API.",
  "请填写向量模型 ID。": "Введите идентификатор модели эмбеддингов.",
  "配置结果尚未确认，请在模型设置中核对后重试。":
    "Результат настройки не подтверждён. Проверьте его в настройках модели перед повторной попыткой.",
  "模型连接测试通过，可以启用。":
    "Тест подключения к модели пройден. Её можно включить.",
  "连接未通过，请核对模型、密钥和接收方。":
    "Подключение не удалось. Проверьте модель, ключ и получателя.",
  "配置或测试结果暂未确认，请核对后重试。":
    "Не удалось подтвердить конфигурацию или результат теста. Проверьте их перед повторной попыткой.",
  "索引更新没有完成，仍在使用原来的搜索模型。请检查连接后重试。":
    "Обновление индекса не завершено. По-прежнему используется прежняя модель поиска. Проверьте подключение и повторите попытку.",
  "上次模型测试结果仍待核对，请稍后重试。":
    "Результат последнего теста модели ещё нужно проверить. Повторите попытку позже.",
  "模型写入暂时关闭。请等待索引维护完成后重试。":
    "Запись модели временно приостановлена. Дождитесь завершения обслуживания индекса и повторите попытку.",
  "模型未能确认启用，请在设置中核对。":
    "Не удалось подтвердить включение модели. Проверьте это в настройках.",
  "已保存的模型配置暂时无法读取，不会重复创建。请重试刷新。":
    "Сохранённая конфигурация модели временно недоступна. Дубликат создан не будет. Попробуйте обновить снова.",
  所选连接已启用: "Выбранные подключения приложений включены",
  本次未选择应用连接: "Подключения приложений не выбраны",
  尚未填写接收方: "Получатель не указан",
  本机内置向量模型: "Встроенная локальная модель эмбеддингов",
  "操作未确认，草稿已保留。":
    "Не удалось подтвердить действие. Черновик сохранён.",
  "操作已完成，但配置列表暂未刷新。已保留操作回执，请刷新核对，不要重复保存。":
    "Операция выполнена, но список конфигураций не обновился. Подтверждение сохранено; обновите список для проверки. Не сохраняйте повторно.",
  "暂时无法刷新模型配置，已确认的配置仍保留。请重试刷新。":
    "Не удалось обновить конфигурации моделей. Подтверждённые конфигурации сохранены; попробуйте обновить снова.",
  "未找到上次创建记录。确认放弃旧记录后可重新配置；系统不会自动重试。":
    "Предыдущая запись о создании не найдена. Отбросьте её, чтобы настроить заново; автоматического повтора не будет.",
  "创建结果仍待核对，不会重复创建。请稍后再次核对。":
    "Результат создания по-прежнему нужно проверить. Дубликат создан не будет; проверьте позже.",
  "已发现模型配置，旧创建记录仍保留。请先核对已有配置。":
    "Найдена конфигурация модели. Старая запись о создании сохранена; сначала проверьте существующую конфигурацию.",
  "旧创建记录尚未安全解除，配置仍保持锁定。请重新核对。":
    "Старую запись о создании не удалось безопасно снять. Конфигурация по-прежнему заблокирована; проверьте снова.",
  "配置未保存，请检查模型地址和参数后重试；已填写内容保留。":
    "Конфигурация не сохранена. Проверьте адрес и параметры модели и повторите попытку; введённые данные сохранены.",
  "创建结果尚未确认，请核对已保存结果，不要重复提交。":
    "Результат создания не подтверждён. Проверьте сохранённые результаты и не отправляйте повторно.",
  "模型配置已变化，旧测试结果不会用于启用，请重新核对配置。":
    "Конфигурация модели изменилась. Старый результат теста не будет использован для включения; проверьте конфигурацию снова.",
  "暂时无法核对测试服务，本次未发送测试，请恢复连接后重试。":
    "Не удалось проверить тестовый сервис. Тест не отправлен; восстановите подключение и повторите попытку.",
  "另一个模型的测试结果尚未确认，请先在该模型上核对测试结果。":
    "Результат теста другой модели не подтверждён. Сначала проверьте результат этой модели.",
  "测试结果尚未确认。再次点击测试连接将核对原操作，不会重复发送测试。":
    "Результат теста не подтверждён. Повторное нажатие «Проверить подключение» проверит исходную операцию без нового теста.",
  "请先点击测试连接，通过后再设为当前模型。":
    "Сначала проверьте подключение; сделайте модель текущей только после успешного теста.",
  "这是当前正在使用的模型，请先切换到别的模型再删除。":
    "Эта модель сейчас используется. Переключитесь на другую модель, прежде чем удалять её.",
  "模型配置操作未成功，请核对输入后重试。":
    "Операция с конфигурацией модели не удалась. Проверьте введённые данные и повторите попытку.",
  "它没有 MCP 通道，无法发放可撤销的记忆凭证":
    "У этого приложения нет канала MCP для отзываемых учётных данных памяти",
  当前安装不支持一键插入:
    "Подключение в один клик не поддерживается в этой установке",
  桌面端的连接组件不可用:
    "Компонент подключения настольного приложения недоступен",
  安装后没有拿到安装回执: "Подтверждение установки не получено",
  记忆使用权限没有获批: "Доступ к памяти не одобрен",
  配置文件被改过: "Файл конфигурации был изменён",
  "有无法确认的残留，需要人工核对": "Некоторые остатки требуют ручной проверки",
  权限存储暂不可用: "Хранилище разрешений недоступно",
  "请先把 MemHub 拖到“应用程序”文件夹，再接入应用":
    "Переместите MemHub в «Программы», прежде чем подключать приложения",
  本机运行时暂不可用: "Локальная среда выполнения недоступна",
  桌面端读取接入状态超时:
    "Истекло время чтения статуса подключения настольного приложения",
  "apps.apps": "Приложения",
  "apps.allApps": "Все приложения",
  "apps.home": "Главная приложений",
  "apps.back": "На главную",
  "apps.overview": "Обзор памяти",
  "apps.memories": "Сохранённые записи памяти",
  "apps.rawConversations": "Исходные разговоры",
  "apps.rawSaving": "Сохранение",
  "apps.rawSavingCompact": "Сохранение",
  "apps.rawSavingStatus": "Статус сохранения",
  "apps.rawSavingDetail":
    "Исходные разговоры сохраняются без генеративной модели",
  "apps.rawNoExcerpt": "Нет отображаемого фрагмента текста пользователя",
  "apps.rawConversation": "Исходный разговор",
  "apps.rawSaved": "Исходный диалог сохранён",
  "apps.rawEmpty": "Сохранённых исходных диалогов пока нет",
  "apps.rawCheckedEmpty":
    "Проверено: нет обновлений исходных диалогов для показа",
  "apps.memoryCheckedEmpty": "Проверено: нет обновлений памяти для показа",
  "apps.recentEmptyCompact": "Нет обновлений",
  "apps.rawWaiting": "Сохранённые исходные диалоги появятся здесь",
  "apps.checkedRecent": "Текущее содержимое проверено",
  "apps.currentMode": "Текущий режим",
  "apps.mode.lite": "Базовая память (lite)",
  "apps.mode.pro": "Умная память (pro)",
  "apps.mode.ultra": "Глубокая память (ultra)",
  "apps.readingCount": "Проверка локальной памяти…",
  "apps.rawReadingCount": "Проверка исходных диалогов…",
  "apps.startingRetry":
    "Фоновая служба запускается; данные обновятся автоматически",
  "apps.countWillUpdate": "Число записей памяти обновится автоматически",
  "apps.rawCountWillUpdate": "Число исходных диалогов обновится автоматически",
  "apps.showingPrevious":
    "Показаны прежние данные; они обновятся автоматически",
  "apps.unreadable": "Недоступно",
  "apps.unit": "шт.",
  "apps.scope": "Используется в пределах разрешений приложений",
  "apps.processing": "Статус обработки",
  "apps.running": "Обработка",
  "apps.paused": "Приостановлено",
  "apps.stoppedNew": "Новые записи памяти не создаются",
  "apps.checkingProcessing": "Проверка статуса обработки",
  "apps.checkingStatus": "Проверка",
  "apps.unchecked": "Нужно проверить",
  "apps.organizingNew": "Обработка новых диалогов",
  "apps.recentUpdate": "Последнее обновление",
  "apps.checkingRecent": "Проверка",
  "apps.recentWillUpdate": "Последнее обновление появится автоматически",
  "apps.notGenerated": "Ещё не создано",
  "apps.saved": "Запись памяти сохранена",
  "apps.separate": "История и записи памяти обрабатываются отдельно",
  "apps.recentCall": "Последний вызов",
  "apps.activityUnavailable": "Активность недоступна",
  "apps.firstBatch": "Первые записи памяти",
  "apps.callsUnavailable": "Не удалось прочитать историю вызовов",
  "apps.memoryUnavailable": "Обзор памяти ещё не готов",
  "apps.importing": "Импорт диалогов перед созданием записей памяти",
  "apps.firstRunning": "Создание первых записей памяти",
  "apps.firstQueued": "Подготовка первых записей памяти",
  "apps.readingActivity": "Подготовка обзора памяти",
  "apps.emptyMemories": "Записей памяти пока нет",
  "apps.callReadFailure":
    "Сохранённые записи памяти по-прежнему доступны. Повторная попытка не создаст новый вызов.",
  "apps.memoryReadFailure":
    "Данные обновятся, когда служба восстановится. Можно также повторить сейчас.",
  "apps.savingConversations": "Диалоги продолжают сохраняться в фоне.",
  "apps.waitingMemories":
    "История сохранена. Новые записи памяти появятся здесь.",
  "apps.reread": "Повторить",
  "apps.viewCalls": "Показать историю",
  "apps.viewMemory": "Показать память",
  "apps.backgroundDown":
    "Фоновая служба не запущена; приложения сейчас не могут обращаться к памяти",
  "apps.restart": "Перезапустить",
  "apps.runtime": "Состояние работы",
  "apps.backgroundRunning": "Работает в фоне",
  "apps.connectionUnchecked": "Нужно проверить подключение",
  "apps.disconnected": "Служба отключена",
  "apps.authorizedOnly": "Обрабатывается только разрешённое содержимое",
  "apps.sync": "Синхронизация диалогов",
  "apps.organizing": "Обработка памяти",
  "apps.access": "Доступ приложений",
  "apps.checkingAccess": "Проверка подключения приложений",
  "apps.checking": "Проверка…",
  "apps.recheck": "Проверить снова",
  "apps.accessState": "Статус подключения приложений",
  "apps.staleAccess":
    "Не удалось прочитать статус подключения приложений; показано последнее известное состояние.",
  "apps.reconnect": "Подключить заново",
  "apps.myApps": "Мои приложения",
  "apps.list": "Список приложений",
  "apps.syncEnabled": "Постоянная синхронизация включена",
  "apps.recallAllowed": "Может запрашивать разрешённые записи памяти",
  "apps.historyAllowed": "Доступ к истории разрешён",
  "apps.connectionPending": "Подключение не завершено",
  "apps.details": "Подробнее",
  "apps.listUnavailable": "Список приложений недоступен",
  "apps.emptyApps": "Приложений для отображения пока нет",
  "apps.checkingNow": "Проверка",
  "apps.checkingState": "Проверка статуса",
  "apps.notEnabled": "Не включено",
  "apps.enableFailed": "Не удалось включить",
  "apps.unknownTime": "Время неизвестно",
  "apps.today": "Сегодня",
  "apps.newMemory": "Новая запись памяти",
  "apps.updatedMemory": "Обновлённая запись памяти",
  "apps.unlinked": "Ещё не подключено",
  "apps.lastRead": "Последнее чтение: ",
  "apps.recentSync": "Последняя синхронизация: {time}",
  "apps.connectedCount": "Подключено приложений: {count}",
  "apps.connectedCount.one": "Подключено приложений: {count}",
  "apps.pendingCount": "Приложений требуют проверки: {count}",
  "apps.pendingCount.one": "Приложений требуют проверки: {count}",
  "apps.mixedCount":
    "Подключено приложений: {count} · ожидают проверки: {pending}",
  "apps.mixedCount.one":
    "Подключено приложений: {count} · ожидают проверки: {pending}",
  "apps.unknownAccess":
    "Статус подключения приложений недоступен; число подключённых приложений нельзя подтвердить.",
  "apps.unknownPermissions":
    "Не удалось прочитать разрешения памяти для {names}; статус подключения не подтверждён.",
  "apps.enabledCount": "Включено приложений: {count}",
  "apps.enabledCount.one": "Включено приложений: {count}",
  "apps.appCount": "Приложений: {count}",
  "apps.appCount.one": "Приложений: {count}",
  "apps.viewAll": "Все приложения ({count})",
  "apps.viewAll.one": "Все приложения ({count})",
  "apps.checkedAt": "Последняя проверка: {time}",
  "apps.revision": "Версия {count}",
  "apps.conversationTime": "Время диалога",
  "apps.importTime": "Время импорта",
  "apps.writeTime": "Время записи",
  "apps.updateTime": "Время обновления",
  "apps.incompleteCalls":
    " · Часть вызовов не записана; список может быть неполным",
  "apps.timeoutWarning":
    "{host} недавно не смог обратиться к памяти. Фоновая служба может быть занята и должна восстановиться автоматически.",
  "apps.autoHostFailed": "Не удалось автоматически подключить {name}{reason}.",
  "apps.failureReason": ": {reason}",
  "apps.autoSyncFailed":
    "Не удалось автоматически включить постоянную синхронизацию для {name}. {reason}",
  "apps.noAutoRetry": "Автоматического повтора не будет.",
  "apps.cardUnavailable":
    "{label} временно недоступно. Другие функции не затронуты; работа должна восстановиться автоматически.",
  "apps.navMemories": "Память",
  "apps.navigation": "Основная навигация",
  "apps.skip": "Перейти к основному содержимому",
  "apps.local": "Хранится локально",
  "apps.localHint":
    "Записи памяти хранятся на этом устройстве. При использовании облачной модели или разрешённого ассистента соответствующие данные отправляются в этот сервис.",
  "memory:记忆抽取": "Извлечение памяти",
  "memory:关系归纳": "Обобщение связей",
  "memory:深度整理": "Глубокая обработка",
  "memory:跨域整理": "Междоменная обработка",
  "memory:当前模型已变化": "Текущая модель изменилась",
  "memory:本地会话内容已变化": "Локальное содержимое диалога изменилось",
  "memory:这段对话含过长原文，暂未整理；原文已保留":
    "Этот диалог содержит слишком длинный текст для обработки; исходный текст сохранён",
  "memory:记忆库分页": "Страницы библиотеки памяти",
  "memory:上一页": "Предыдущая страница",
  "memory:下一页": "Следующая страница",
  "memory:等待可用生成模型": "Ожидание доступной генеративной модели",
  "memory:还没有启用生成模型": "Генеративная модель ещё не включена",
  "memory:缺少生成模型 API Key": "Нет ключа API генеративной модели",
  "memory:模型凭据暂不可用": "Учётные данные модели временно недоступны",
  "memory:本机凭据存储暂不可用":
    "Локальное хранилище учётных данных временно недоступно",
  "memory:需要授权访问本机钥匙串。请重新打开 App 并完成一次授权":
    "Нужно разрешить доступ к Связке ключей. Откройте приложение снова и подтвердите доступ.",
  "memory:读取本机钥匙串超时。重新保存一遍模型配置即可恢复":
    "Истекло время чтения Связки ключей. Сохраните конфигурацию модели ещё раз, чтобы восстановить работу.",
  "memory:写入本机钥匙串超时。重新保存一遍模型配置即可恢复":
    "Истекло время записи в Связку ключей. Сохраните конфигурацию модели ещё раз, чтобы восстановить работу.",
  "memory:模型认证失败。更新 key 后，到设置里点一次检测，会自动接着处理":
    "Ошибка аутентификации модели. Обновите ключ и запустите проверку в настройках, чтобы продолжить обработку.",
  "memory:模型账户余额不足。充值或换 key 后，到设置里点一次检测，会自动接着处理":
    "Недостаточно средств на счёте модели. Пополните баланс или смените ключ, затем запустите проверку в настройках, чтобы продолжить обработку.",
  "memory:模型服务拒绝访问。更新权限后，到设置里点一次检测，会自动接着处理":
    "Сервис модели отказал в доступе. Обновите разрешения и запустите проверку в настройках, чтобы продолжить обработку.",
  "memory:模型服务限流": "Превышен лимит запросов к сервису модели",
  "memory:模型服务暂不可用": "Сервис модели временно недоступен",
  "memory:模型服务响应超时": "Истекло время ответа сервиса модели",
  "memory:模型请求不兼容，请核对配置":
    "Запрос к модели несовместим; проверьте конфигурацию",
  "memory:模型请求未成功": "Запрос к модели не удался",
  "memory:HY Memory 不可用": "HY Memory недоступна",
  "memory:HY Memory 启动失败": "Не удалось запустить HY Memory",
  "memory:暂时没能整理，稍后会自动重试":
    "Пока не удалось обработать; повтор будет автоматически",
  "memory:这次没能整理成功，正在重试":
    "На этот раз обработать не удалось; повтор",
  "memory:生成模型换过了，这批会重新整理":
    "Генеративная модель изменилась; эти элементы будут обработаны заново",
  "memory:整理方式换过了，这批会重新整理":
    "Настройки обработки изменились; эти элементы будут обработаны заново",
  "memory:已提炼": "Извлечено",
  "memory:旧版确认记录": "Устаревшая запись подтверждения",
  "memory:创建": "Создано",
  "memory:纠正": "Исправлено",
  "memory:调整使用范围": "Изменена область доступа",
  "memory:撤销修改": "Изменение отменено",
  "memory:模型更新": "Обновление модели",
  "memory:被新记忆取代": "Заменено новой записью памяти",
  "memory:其他操作": "Другое действие",
  "memory:删除（记录）": "Удаление",
  "memory:修改已保存，但读取最新列表失败。已停止使用旧回答，请重新读取。":
    "Изменение сохранено, но не удалось загрузить актуальный список. Старые ответы больше не используются; перезагрузите список.",
  "memory:纠正已保存，但读取最新列表失败。已停止使用旧回答，请重新读取。":
    "Исправление сохранено, но не удалось загрузить актуальный список. Старые ответы больше не используются; перезагрузите список.",
  "memory:未收到操作结果，请重试。": "Результат не получен. Повторите попытку.",
  "memory:批量处理结果已保留，但列表刷新失败，请重新读取记忆库。":
    "Результаты массового действия сохранены, но список не обновился. Перезагрузите библиотеку памяти.",
  "memory:批量处理结果已保留，但列表刷新失败，稍后会自动重新读取。":
    "Результаты массового действия сохранены, но список не обновился. Он будет перезагружен автоматически позже.",
  "memory:召回范围已保存，但列表刷新失败。请稍后重新打开记忆页。":
    "Область вызова сохранена, но список не обновился. Откройте раздел «Память» позже.",
  "memory:原始对话暂时无法读取": "Не удалось загрузить исходные диалоги",
  "memory:没有匹配的原始对话": "Нет подходящих исходных диалогов",
  "memory:还没有导入的原始对话": "Импортированных исходных диалогов пока нет",
  "memory:查看已读内容": "Показать сохранённое",
  "memory:已保存原文": "Исходный текст сохранён",
  "memory:刷新已读内容": "Обновить сохранённое",
  "memory:正在核对本机已保存记录…": "Проверка сохранённых локальных данных…",
  "memory:原始消息": "Исходные сообщения",
  "memory:会话": "Диалоги",
  "memory:用户发言": "Сообщения пользователя",
  "memory:Agent 发言": "Сообщения агента",
  "memory:作者未知": "Автор неизвестен",
  "memory:原始时间未知": "Исходное время неизвестно",
  "memory:工具记录": "Запись инструмента",
  "memory:原始时间来自已保存记录。":
    "Исходное время взято из сохранённых данных.",
  "memory:已保存的原文样例": "Примеры сохранённого исходного текста",
  "memory:最多展示 3 段，不代表全部历史或推荐记忆。":
    "Не более 3 примеров; это не вся история и не рекомендуемые записи памяти.",
  "memory:查看完整对话": "Показать весь диалог",
  "memory:原文较长，此处仅展示前 600 个字符。":
    "Длинный исходный текст; здесь показаны только первые 600 символов.",
  "memory:这个来源还没有保存原文。":
    "Из этого источника исходный текст ещё не сохранён.",
  "memory:完整对话": "Весь диалог",
  "memory:关闭完整对话": "Закрыть весь диалог",
  "memory:用户": "Пользователь",
  "memory:暂时无法读取已保存内容，旧回执已收起。请检查授权或重试。":
    "Не удалось прочитать сохранённое содержимое. Прежнее подтверждение закрыто; проверьте разрешения или повторите попытку.",
  "memory:这段原文暂不可读，可能已被撤回授权。请刷新回执。":
    "Этот исходный текст недоступен; возможно, доступ отозван. Обновите подтверждение.",
  "memory:外部记忆召回范围": "Область внешнего вызова памяти",
  "memory:外部记忆召回": "Внешний вызов памяти",
  "memory:调整召回范围": "Изменить область вызова",
  "memory:所有关联工具": "Все подключённые инструменты",
  "memory:已停止外部记忆召回": "Внешний вызов памяти остановлен",
  "memory:原有限定范围": "Прежняя ограниченная область",
  "memory:选择召回范围": "Выберите область вызова",
  "memory:停止外部记忆召回": "Остановить внешний вызов памяти",
  "memory:正在保存…": "Сохранение…",
  "memory:保存召回范围": "Сохранить область вызова",
  "memory:取消范围修改": "Отменить изменение области",
  "memory:原文检索仍按来源授权；已带出的内容不会撤回。":
    "Поиск по исходному тексту по-прежнему следует разрешениям источников; уже переданное содержимое не будет отозвано.",
  "memory:记忆已更新，请重新打开这条记忆后再调整范围。":
    "Запись памяти изменилась. Откройте её снова, прежде чем менять область.",
  "memory:范围修改未提交，当前范围保持不变。请修正后重试。":
    "Изменения области не отправлены. Текущая область не изменилась; устраните проблему и повторите попытку.",
  "memory:保存结果尚未确认，请重试。":
    "Результат сохранения не подтверждён. Повторите попытку.",
  "memory:原话已保存在本机；当前内容、模型接收方或授权版本尚未通过核对，未继续整理。":
    "Исходный текст сохранён на этом устройстве; содержимое, получатель модели или версия разрешений не прошли проверку, поэтому обработка не продолжилась.",
  "memory:HY 未返回可核对的已有记忆，未创建替代内容；原始记录已保留，可重试处理。":
    "HY не вернула проверяемых существующих записей памяти, и замена не создана. Исходные данные сохранены; обработку можно повторить.",
  "memory:历史引用尚未绑定到原始来源，当前批次未完成，原话已保留。":
    "Исторические ссылки ещё не связаны с исходными источниками. Этот пакет не завершён; исходный текст сохранён.",
  "memory:记忆内容未通过来源与作者核验，当前批次未完成，原话已保留。":
    "Содержимое записи памяти не прошло проверку источника и автора. Этот пакет не завершён; исходный текст сохранён.",
  "memory:所选记忆已不可读取，请重新读取记忆库。":
    "Выбранная запись памяти больше недоступна. Перезагрузите библиотеку памяти.",
  "memory:还没连上本地 Core，正在重试…":
    "Локальный Core ещё не подключён; повторная попытка…",
  "memory:记忆内容或使用权限已变化，回答已停止。请重新提问。":
    "Содержимое записей памяти или разрешения доступа изменились, поэтому ответ остановлен. Задайте вопрос снова.",
  "memory:这条记忆的来源授权已撤回，暂时不能读取。":
    "Доступ к источнику этой записи памяти отозван, поэтому сейчас её нельзя прочитать.",
  "memory:记忆已被更新，请刷新后重试。":
    "Эта запись памяти обновлена. Обновите данные и повторите попытку.",
  "memory:已删除的记忆不能修改。": "Удалённые записи памяти нельзя изменять.",
  "memory:这条记忆已经删除。": "Эта запись памяти удалена.",
  "memory:这条记忆已被更新的记忆取代，只能查看或删除。":
    "Эта запись памяти заменена более новой; её можно только просмотреть или удалить.",
  "memory:没有更早的修改可以撤销。": "Нет более ранних изменений для отмены.",
  "memory:来源原话已不存在，无法建立这条记忆。":
    "Исходный текст источника больше не существует, поэтому эту запись памяти нельзя создать.",
  "memory:这次操作与已完成的请求不一致。":
    "Это действие не соответствует уже выполненному запросу.",
  "memory:输入内容不符合要求。":
    "Введённые данные не соответствуют требованиям.",
  "memory:本地记忆库正忙，请稍后重试。":
    "Локальная библиотека памяти занята. Повторите попытку позже.",
  "memory:记忆": "Память",
  "memory:记忆空间": "Пространство памяти",
  "memory:全部应用": "Все приложения",
  "memory:来源": "Источник",
  "memory:主题": "Тема",
  "memory:归纳": "Обобщение",
  "memory:全部记忆": "Вся память",
  "memory:来源待核对": "Нужно проверить источник",
  "memory:所选来源待核对": "Нужно проверить выбранный источник",
  "memory:未识别来源": "Источник не определён",
  "memory:这个来源下还没有记忆": "Из этого источника записей памяти пока нет",
  "memory:只在最相关的 {count} 条记忆里查找关键词":
    "Ключевые слова ищутся только среди наиболее релевантных записей памяти: {count}",
  "memory:未知来源": "Неизвестный источник",
  "memory:记忆读取失败": "Не удалось загрузить записи памяти",
  "memory:正在读取…": "Загрузка…",
  "memory:正在更新…": "Обновление…",
  "memory:读取失败": "Не удалось загрузить",
  "memory:正在核对来源…": "Проверка источников…",
  "memory:正在导入历史会话，记忆将陆续出现":
    "Импорт прежних диалогов; записи памяти будут появляться постепенно",
  "memory:记忆工作台": "Рабочая область памяти",
  "memory:来源与主题": "Источники и темы",
  "memory:记忆或原始对话": "Память или исходные диалоги",
  "memory:原始对话": "Исходные диалоги",
  "memory:地图组织方式": "Группировка карты",
  "memory:筛选地图记忆": "Фильтр записей памяти на карте",
  "memory:搜索原始对话": "Поиск по исходным диалогам",
  "memory:搜索记忆": "Поиск в памяти",
  "memory:筛选记忆…": "Фильтр записей памяти…",
  "memory:向记忆提问": "Задать вопрос памяти",
  "memory:询问全部已授权可用记忆…":
    "Спросите обо всех разрешённых и доступных записях памяти…",
  "memory:发送问题": "Отправить вопрос",
  "memory:刷新地图记忆": "Обновить записи памяти на карте",
  "memory:关闭新增记忆提示": "Закрыть уведомление о новых записях памяти",
  "memory:地图记忆列表": "Список записей памяти на карте",
  "memory:记忆读取失败，请重试。":
    "Не удалось загрузить записи памяти. Повторите попытку.",
  "memory:重新读取地图记忆": "Перезагрузить записи памяти на карте",
  "memory:没有匹配的记忆": "Нет подходящих записей памяти",
  "memory:本页没有匹配的主题记忆":
    "На этой странице нет подходящих записей памяти по теме",
  "memory:还没有已生成的记忆": "Записей памяти пока нет",
  "memory:地图记忆分页": "Страницы записей памяти на карте",
  "memory:上一页记忆": "Предыдущая страница памяти",
  "memory:下一页记忆": "Следующая страница памяти",
  "memory:记忆关系图": "Граф памяти",
  "memory:记忆关联": "Связи памяти",
  "memory:刚导入的原话，还不是沉淀后的记忆":
    "Недавно импортированный исходный текст, ещё не записи памяти",
  "memory:来源归属": "Связи с источниками",
  "memory:可在沉淀记忆后查看视图": "Создайте записи памяти, чтобы увидеть граф",
  "memory:正在读取记忆地图…": "Загрузка карты памяти…",
  "memory:查看选中来源原文": "Показать текст выбранного источника",
  "memory:查看选中记忆原文": "Показать текст выбранной записи памяти",
  "memory:查看原文": "Показать исходный диалог",
  "memory:整理已有关系": "Построить существующие связи",
  "memory:正在读取原文…": "Загрузка исходного диалога…",
  "memory:选择一条原始对话": "Выберите исходный диалог",
  "memory:记忆暂时无法读取": "Записи памяти временно недоступны",
  "memory:当前筛选没有可显示的记忆": "Для этого фильтра нет записей памяти",
  "memory:正在读取记忆…": "Загрузка записей памяти…",
  "memory:已保存的对话还未生成记忆":
    "Сохранённые диалоги ещё не превратились в записи памяти",
  "memory:还没有可显示的记忆": "Записей памяти для отображения пока нет",
  "memory:连线表示来源归属": "Линии показывают связи с источниками",
  "memory:记忆详情": "Сведения о записи памяти",
  "memory:来源原文": "Исходный текст источника",
  "memory:关联节点": "Связанный узел",
  "memory:关闭记忆详情": "Закрыть сведения о записи памяти",
  "memory:关闭详情": "Закрыть сведения",
  "memory:正在读取这条记忆…": "Загрузка записи памяти…",
  "memory:这条记忆的原文暂时无法读取，选择已保留。":
    "Не удалось загрузить исходный текст этой записи памяти. Выбор сохранён.",
  "memory:重试读取原文": "Повторить загрузку диалога",
  "memory:已沉淀的记忆": "Созданные записи памяти",
  "memory:正在读取记忆详情…": "Загрузка сведений о записи памяти…",
  "memory:沉淀记忆后查看详情": "Создайте записи памяти, чтобы увидеть сведения",
  "memory:时间未知": "Время неизвестно",
  "memory:来源原文已不可读取": "Исходный текст источника недоступен",
  "memory:关联记忆": "Связанные записи памяти",
  "memory:正在读取完整对话…": "Загрузка всего диалога…",
  "memory:重试读取完整对话": "Повторить загрузку всего диалога",
  "memory:看完整对话": "Показать весь диалог",
  "memory:今天": "Сегодня",
  "memory:你": "Вы",
  "memory:助手": "Ассистент",
  "memory:工具": "Инструмент",
  "memory:未知": "Неизвестно",
  "memory:作者待核对": "Нужно проверить автора",
  "memory:你说": "Вы сказали",
  "memory:助手原话": "Исходное сообщение ассистента",
  "memory:已沉淀": "Создана",
  "memory:已删除": "Удалена",
  "memory:已被取代": "Заменена",
  "memory:状态待核对": "Нужно проверить статус",
  "memory:编辑": "Изменить",
  "memory:原始依据": "Исходное подтверждение",
  "memory:这段原文当前不可查看。": "Этот исходный текст сейчас недоступен.",
  "memory:版本记录": "История версий",
  "memory:更多管理": "Другие действия",
  "memory:撤销上次修改": "Отменить последнее изменение",
  "memory:删除记忆": "Удалить запись памяти",
  "memory:编辑记忆": "Изменение записи памяти",
  "memory:返回记忆": "Назад к записи памяти",
  "memory:修改这条记忆，原始对话保持不变。":
    "Измените эту запись памяти: исходный диалог останется без изменений.",
  "memory:记忆内容": "Содержимое записи памяти",
  "memory:使用范围保持不变": "Область доступа не изменится",
  "memory:已有新版本": "Есть более новая версия",
  "memory:你的草稿已保留，先查看新版本再决定如何修改。":
    "Черновик сохранён. Просмотрите новую версию, прежде чем вносить изменения.",
  "memory:查看新版本": "Показать новую версию",
  "memory:基于此版本继续编辑": "Продолжить с этой версии",
  "memory:取消": "Отмена",
  "memory:保存": "Сохранить",
  "memory:重试": "Повторить",
  "memory:删除": "Удалить",
  "memory:对话": "Диалог",
  "memory:工作": "Работа",
  "memory:编码": "Программирование",
  "memory:保存修改": "Сохранить изменения",
  "memory:对应记忆": "Связанная запись памяти",
  "memory:查看记忆": "Показать память",
  "memory:删除这条记忆？": "Удалить эту запись памяти?",
  "memory:删除后不再用于新的召回，来源 App 中的原始对话不会删除。":
    "Она больше не будет использоваться в новых запросах. Исходные диалоги в приложении-источнике удалены не будут.",
  "memory:已经发送给应用的内容无法远程收回。":
    "Содержимое, уже отправленное в приложения, нельзя отозвать удалённо.",
  "memory:放弃尚未保存的修改？": "Отменить несохранённые изменения?",
  "memory:来源权限已变化或原文不可用。":
    "Разрешения источника изменились, или исходный текст недоступен.",
  "memory:暂时无法读取新版本，草稿已保留。":
    "Не удалось загрузить новую версию. Черновик сохранён.",
  "memory:修改未提交；草稿已保留。请按提示修正后重试。":
    "Изменения не отправлены; черновик сохранён. Устраните проблему и повторите попытку.",
  "memory:修改尚未确认，草稿已保留。请先刷新核对，避免重复提交。":
    "Изменения не подтверждены; черновик сохранён. Обновите данные и проверьте их перед повторной попыткой, чтобы не отправить дважды.",
  "memory:删除未提交。请核对这条记忆的状态后重试。":
    "Удаление не отправлено. Проверьте статус этой записи памяти перед повторной попыткой.",
  "memory:删除尚未确认。请重新核对记忆状态，不会自动扩大删除范围。":
    "Удаление не подтверждено. Проверьте статус записи памяти: область удаления не будет расширена автоматически.",
  "memory:撤销未提交。请按提示修正后重试。":
    "Отмена не отправлена. Устраните проблему и повторите попытку.",
  "memory:撤销尚未确认，请先核对当前记忆版本，避免重复撤销。":
    "Отмена не подтверждена. Проверьте текущую версию записи памяти перед повторной попыткой, чтобы не отменить дважды.",
  "memory:添加记忆": "Добавить запись памяти",
  "memory:关闭新增记忆": "Закрыть окно добавления записи памяти",
  "memory:关闭": "Закрыть",
  "memory:记下这件事": "Запомнить это",
  "memory:例如：项目周会不排在周五。":
    "Например: не назначать встречу по проекту на пятницу.",
  "memory:整理模型：": "Модель обработки: ",
  "memory:尚未配置，原话仍可保存":
    "Не настроена; исходный текст всё равно можно сохранить",
  "memory:正在读取": "Загрузка",
  "memory:配置模型": "Настроить модель",
  "memory:保存中…": "Сохранение…",
  "memory:保存原话": "Сохранить исходный текст",
  "memory:最近添加": "Недавно добавленные",
  "memory:原话已保存，等待可用模型":
    "Исходный текст сохранён; ожидается доступная модель",
  "memory:原话已保存，等待整理": "Исходный текст сохранён; ожидает обработки",
  "memory:正在等待整理": "Ожидает обработки",
  "memory:正在整理": "Обработка",
  "memory:原话已保存，整理未完成":
    "Исходный текст сохранён; обработка не завершена",
  "memory:整理已完成": "Обработка завершена",
  "memory:原话已保存，未提取出可用记忆":
    "Исходный текст сохранён; пригодных записей памяти не извлечено",
  "memory:最近添加的内容读取失败。":
    "Не удалось прочитать недавно добавленные записи памяти.",
  "memory:整理状态暂时不可用，原话已保存。":
    "Статус обработки временно недоступен; исходный текст сохранён.",
  "memory:已保存，正在后台沉淀记忆":
    "Сохранено; записи памяти создаются в фоне",
  "memory:未能确认保存结果，输入已保留。重试不会重复添加。":
    "Не удалось подтвердить сохранение. Текст сохранён; повторная попытка не создаст дубликат.",
  "memory:模型服务要求检查余额或付款状态，原话仍已保存。":
    "Сервис модели требует проверить баланс или статус оплаты; исходный текст по-прежнему сохранён.",
  "memory:这台设备打不开 3D 视图，已显示关联列表。":
    "3D-вид недоступен на этом устройстве. Связи показаны списком.",
  "memory:图谱工具": "Управление графом",
  "memory:放大": "Увеличить",
  "memory:放大图谱": "Увеличить граф",
  "memory:缩小": "Уменьшить",
  "memory:缩小图谱": "Уменьшить граф",
  "memory:重置视角": "Сбросить вид",
  "memory:重置图谱视角": "Сбросить вид графа",
  "memory:查看读取详情": "Сведения о чтении",
  "memory:原文暂时无法读取": "Не удалось загрузить исходный текст",
  "memory:暂时无法读取这段对话，已保留当前记忆。":
    "Не удалось загрузить этот диалог. Текущая запись памяти остаётся выбранной.",
  "memory:已清除筛选，定位到关联记忆。":
    "Фильтры сброшены, чтобы найти связанную запись памяти.",
  "memory:所选记忆不在当前筛选内，已取消选择。":
    "Выбранная запись памяти не входит в этот фильтр; выбор снят.",
  "memory:列表分隔符": ", ",
  "memory:冒号": ":",
  "memory:手动添加": "Добавлено вручную",
  "memory:导入的已有记忆": "Импортированная запись памяти",
  "memory:记忆与原文": "Память и исходный текст",
  "memory:第 {revision} 版": "Версия {revision}",
  "memory:对话时间 ": "Время диалога",
  "memory:沉淀时间 ": "Создано",
  "memory:原对话 ": "Исходный диалог ",
  "memory:{count} 条记录没有原始时间，未用导入时间代替。":
    "Записей без исходной метки времени: {count}; время импорта не подставлялось.",
  "memory:作者数量按当前记录分类统计，不代表记忆抽取已完成或语义已核实。":
    "Число авторов отражает сохранённые данные, а не завершённое извлечение памяти или проверенный смысл.",
  "memory:统计于 ": "Статистика на ",
  "memory: · 导入数量仍在更新，可刷新核对":
    " · Число импортированных ещё обновляется; обновите, чтобы проверить",
  "memory:对话于 {day}": "Диалог от {day}",
  "memory:导入于 {day}": "Импортировано {day}",
  "memory:写入于 {day}": "Записано {day}",
  "memory:已开始处理所选对话": "Обработка выбранных диалогов",
  "memory:处理请求已受理": "Запрос на обработку принят",
  "memory:模型连接已通过。首批记忆生成后，会逐步出现在记忆列表里。":
    "Подключение к модели подтверждено. Записи памяти будут появляться в списке по мере создания.",
  "memory:已导入 {count} 段对话，整理成记忆后会出现在这里。":
    "Импортировано диалогов: {count}. Записи памяти появятся здесь после обработки.",
  "memory:历史已保存在本机，记忆就绪后会出现在这里。":
    "История сохранена на этом устройстве. Записи памяти появятся здесь, когда будут готовы.",
  "memory:连接应用并导入历史后，可以开始沉淀记忆。":
    "Подключите приложение и импортируйте историю, чтобы начать создавать записи памяти.",
  "memory:当前已生成 0 条记忆。请求已受理不等于已经完成，原始对话、模型处理任务和记忆数量分别统计。":
    "Записей памяти пока не создано. Принятый запрос ещё не выполнен; исходные диалоги, задачи обработки и записи памяти считаются отдельно.",
  "memory:返回应用": "Назад к приложениям",
  "memory:共 {count} 条 · 正在核对来源…":
    "Всего: {count} · проверка источников…",
  "memory:共 {count} 条 · 正在核对来源….one":
    "Всего: {count} · проверка источников…",
  "memory:共 {count} 条": "Всего: {count}",
  "memory:共 {count} 条.one": "Всего: {count}",
  "memory: · 正在核对…": " · Проверка…",
  "memory:新增 {count} 条记忆": "Новых записей памяти: {count}",
  "memory:新增 {count} 条记忆.one": "Новых записей памяти: {count}",
  "memory:沉淀于 ": "Создано ",
  "memory:共 {count} 条，第 {page} / {pages} 页":
    "Всего: {count} · Страница {page} из {pages}",
  "memory:共 {count} 条，第 {page} / {pages} 页.one":
    "Всего: {count} · Страница {page} из {pages}",
  "memory:已沉淀 {count} 条记忆": "Создано записей памяти: {count}",
  "memory:已沉淀 {count} 条记忆.one": "Создано записей памяти: {count}",
  "memory:关键词“{query}”": "Ключевое слово «{query}»",
  "memory:已沉淀 {count} 条{filter} · 当前展示 {shown} 条":
    "Создано записей памяти: {count}{filter} · Показано: {shown}",
  "memory:已沉淀 {count} 条{filter} · 当前展示 {shown} 条.one":
    "Создано записей памяти: {count}{filter} · Показано: {shown}",
  "memory:（{filter}）": " ({filter})",
  "memory:第 {page} 页": "Страница {page}",
  "memory:正在等待整理：": "Ожидает обработки: ",
  "memory:记忆未在生成": "Записи памяти не создаются",
  "memory:还没有开始生成记忆：{reason}（{count} 条待处理）":
    "Записи памяти не создаются: {reason} (в ожидании: {count})",
  "memory:去配置生成模型": "Настроить генеративную модель",
  "memory:模型暂停处理": "Модель приостановлена",
  "memory:模型配置有问题（{reason}），记忆暂停处理，已有 {count} 条在排队":
    "Проблема с настройкой модели ({reason}). Обработка памяти приостановлена; в очереди: {count}",
  "memory:模型配置有问题（{reason}），记忆暂停处理，已有 {count} 条在排队.one":
    "Проблема с настройкой модели ({reason}). Обработка памяти приостановлена; в очереди: {count}",
  "memory:API Key 无效或没有权限": "ключ API недействителен или не имеет прав",
  "memory:找不到模型或地址": "модель или адрес не найдены",
  "memory:请求过于频繁，被服务商限流":
    "слишком много запросов, провайдер ограничил частоту",
  "memory:模型响应超时": "истекло время ответа модели",
  "memory:模型服务出错": "ошибка сервиса модели",
  "memory:无法连接模型服务": "не удаётся подключиться к сервису модели",
  "memory:连接模型服务失败": "сбой подключения к сервису модели",
  "calls.directHost": "Прямой вызов (диагностика)",
  "calls.headline.timedOut": "{host} не успел обратиться к памяти",
  "calls.headline.unavailable": "{host} сейчас не может обратиться к памяти",
  "calls.headline.denied": "{host} не имеет разрешения на доступ к памяти",
  "calls.headline.empty": "{host} не нашёл подходящих записей памяти",
  "calls.headline.found": "{host}: найдено связанных элементов — {count}",
  "calls.headline.found.one": "{host}: найден {count} связанный элемент",
  "calls.headline.rawOnly": "{host}: найдено исходных диалогов — {count}",
  "calls.headline.rawOnly.one": "{host}: найден {count} исходный диалог",
  "calls.headline.memoryOnly": "{host}: найдено воспоминаний — {count}",
  "calls.headline.memoryOnly.one": "{host}: найдено {count} воспоминание",
  "calls.headline.mixed":
    "{host}: воспоминаний — {memories} · исходных диалогов — {raw}",
  "calls.headline.mixed.one":
    "{host}: воспоминаний — {memories} · исходных диалогов — {raw}",
  "calls.headline.completed": "Вызов {host} завершён",
  "calls.detail.timedOut":
    "Записи памяти обрабатываются. Время запроса истекло, поэтому в этот ответ память не вошла.",
  "calls.detail.unavailable":
    "MemHub временно недоступен, поэтому в этот ответ память не вошла.",
  "calls.detail.denied": "Текущие разрешения не позволяют этот запрос.",
  "calls.detail.partialDelivered":
    "Передано в {host} · часть содержимого ещё обрабатывается",
  "calls.detail.partial": "Часть содержимого ещё обрабатывается",
  "calls.detail.delivered": "Передано в {host}",
  "calls.detail.notDelivered": "Не передано в приложение",
  "calls.detail.empty": "На этот раз подходящих записей памяти нет",
  "calls.detail.success": "Запрос выполнен",
  "calls.title": "История использования",
  "calls.query": "Этот запрос к памяти",
  "calls.backApps": "Назад к приложениям",
  "calls.backHistory": "Назад к истории",
  "calls.filter": "Фильтр по приложению",
  "calls.allApps": "Все приложения",
  "calls.recent": "Недавняя история",
  "calls.refresh": "Обновить историю",
  "calls.clearPage": "Очистить эту страницу",
  "calls.clearAll": "Очистить всю историю",
  "calls.clearAllShort": "Очистить всё",
  "calls.loading": "Загрузка истории использования",
  "calls.clearing": "Очистка выбранной истории",
  "calls.readFailed":
    "Не удалось загрузить историю. Обновите страницу и повторите попытку.",
  "calls.unavailable": "История использования недоступна. Обновите позже.",
  "calls.incomplete": "Часть вызовов не записана; список может быть неполным.",
  "calls.callDetail": "Сведения о вызове",
  "calls.source": " · Источник: {source}",
  "calls.stats":
    "Исходных фрагментов: {raw} · Записей памяти: {memories} · Время: {seconds} с",
  "calls.metadata": "Сведения о записи",
  "calls.privacy":
    "Ради конфиденциальности ваш исходный вопрос и ответ здесь не хранятся — только время, источник и результат.",
  "calls.exactTime": "Точное время",
  "calls.app": "Приложения",
  "calls.type": "Тип",
  "calls.result": "Статус результата",
  "calls.tool": "Запрос к памяти",
  "calls.notRecorded": "Не записано",
  "calls.missingRow":
    "Этой записи больше нет на этой странице. Вернитесь к истории, чтобы найти её.",
  "calls.list": "История вызовов",
  "calls.emptyPage": "На этой странице нет записей",
  "calls.empty": "Истории использования пока нет",
  "calls.pagination": "Страницы истории",
  "calls.previous": "Предыдущая страница",
  "calls.next": "Следующая страница",
  "calls.policy": "Хранится {days} дн., не более {count} записей",
  "calls.confirmAllTitle": "Очистить всю историю использования?",
  "calls.confirmPageTitle": "Очистить записи на этой странице ({count})?",
  "calls.confirmPageTitle.one": "Очистить записи на этой странице ({count})?",
  "calls.confirmAllDescription":
    "Будут удалены все сохранённые записи вызовов, а не только эта страница или фильтр. Исходные диалоги и записи памяти, разрешения и правила хранения не изменятся. Это действие нельзя отменить.",
  "calls.confirmPageDescription":
    "Будут удалены только эти записи вызовов. Исходные диалоги и записи памяти, разрешения и правила хранения не изменятся. Это действие нельзя отменить.",
  "calls.confirmAll": "Подтвердить очистку всего",
  "calls.confirmPage": "Подтвердить очистку",
  "calls.cancel": "Отмена",
  "calls.clearedAll": "Вся история очищена.",
  "calls.clearedPage": "Выбранная история очищена.",
  "calls.clearAllFailed":
    "Не удалось очистить историю использования. Записи не изменены; проверьте и повторите попытку.",
  "calls.clearPageFailed":
    "Не удалось очистить выбранную историю. Записи не изменены; проверьте и повторите попытку.",
  "calls.clearUnknown":
    "Не удалось подтвердить результат. Проверьте перезагруженную историю; очистка не повторялась автоматически.",
  "calls.references": "Связанное содержимое",
  "calls.referencesLoading": "Загрузка связанного содержимого",
  "calls.referencesFailed":
    "Не удалось загрузить связанное содержимое. Обновите страницу и повторите попытку.",
  "calls.raw": "Исходный диалог",
  "calls.memory": "Записи памяти",
  "calls.changed": "Версия изменилась",
  "calls.pending": "Нужно проверить",
  "calls.notAvailable": "Недоступно",
  "calls.openRaw": "Показать исходный диалог",
  "calls.openMemory": "Показать память",
  "calls.noReferences": "Связанное содержимое не найдено",
  "calls.rawBody": "Текст исходного диалога",
  "calls.rawLoading": "Загрузка исходного диалога",
  "calls.rawFailed": "Исходный диалог недоступен, или его версия изменилась.",
  "calls.state.grounded": "Запрос выполнен",
  "calls.state.empty": "Запрос выполнен; подходящих записей памяти нет",
  "calls.state.partial":
    "Запрос выполнен; часть содержимого ещё обрабатывается",
  "calls.state.timed_out": "Время запроса истекло; память не передана",
  "calls.state.unavailable": "MemHub временно недоступен",
  "calls.state.denied": "Этот запрос не разрешён",
  "calls.return.timed_out":
    "MemHub импортирует и обрабатывает записи памяти. Запрос не успел завершиться, поэтому {host} ответил без памяти. Работа возобновится после завершения обработки.",
  "calls.return.unavailable":
    "MemHub временно недоступен; в этот ответ память не вошла.",
  "calls.return.denied":
    "Текущие разрешения не позволяют этот запрос; память в приложение не передана.",
  "calls.return.partial": "Часть содержимого ещё обрабатывается.",
  "clock.unknown": "Время неизвестно",
  "clock.today": "Сегодня, {clock}",
  settings: "Настройки",
  languageTitle: "Язык интерфейса",
  languageLabel: "Язык",
  languageSystem: "Как в системе",
  languageChinese: "Китайский (упрощённый)",
  languageEnglish: "Английский",
  languageSpanish: "Испанский",
  languageDescription:
    "Выберите язык интерфейса. Эта настройка не меняет ваши диалоги и записи памяти.",
  languageSaveError: "Не удалось сохранить настройку языка. Повторите попытку.",
  languageLoadError: "Не удалось загрузить настройку языка. Повторите попытку.",
  nativeErrorTitle: "Не удалось запустить MemHub",
  startupPreparing: "Запуск и подготовка службы памяти…",
  startupUpgradeHint:
    "Первый запуск или обновление данных памяти может занять несколько минут. MemHub откроется автоматически по завершении.",
  "migration.dialogMessage":
    "Миграция данных при обновлении MemHub не завершилась.",
  "migration.dialogDetail":
    "Чтобы защитить ваши данные, MemHub не будет запущен. Приложите журнал миграции к issue на GitHub и отправьте его нам. (Код ошибки: {code})",
  "migration.dataRestored":
    "Ваши данные восстановлены до состояния перед обновлением.",
  "migration.logPath": "Журнал: {path}",
  "migration.openLog": "Открыть журнал",
  "migration.quit": "Выйти",
  "migration.issueHeading": "## Сбой миграции при обновлении",
  "migration.issueUpgrade":
    "- При обновлении MemHub с {fromVersion} (сборка {fromBuild}) до {toVersion} (сборка {toBuild}) не удалось перенести данные.",
  "migration.issueCode": "- Код ошибки: {code}",
  "migration.issueStep": "- Шаг со сбоем: {step}",
  "migration.issueRestored":
    "- Данные восстановлены до состояния перед обновлением: {restored}",
  "migration.issueSystem": "- Система: {system}",
  "migration.issueArch": " ({arch})",
  "migration.issueLog": "- Журнал миграции (локально): {path}",
  "migration.issueAttach":
    "Перетащите сюда указанный выше файл журнала как вложение, затем отправьте.",
  "migration.unknown": "неизвестно",
  "migration.yes": "да",
  "migration.no": "нет",
};
