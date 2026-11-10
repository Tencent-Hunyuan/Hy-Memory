import type { MessageKey } from "../gui-i18n.js";
import { embeddingSwitchGermanMessages } from "../gui-i18n-embedding-switch.js";
import { modeGermanMessages } from "../gui-i18n-mode.js";

/** German (de, formal Sie) GUI copy. Machine-assisted; native-speaker review recommended. */
export const germanMessages: Record<MessageKey, string> = {
  ...modeGermanMessages,
  ...embeddingSwitchGermanMessages,
  "connectionFallback.title": "Andere Tools verbinden",
  "connectionFallback.desktopOnly":
    "Lokale Kopplung ist nur in der Desktop-App verfügbar.",
  "connectionFallback.description":
    "Erstellen Sie einen einmaligen Kopplungs-Skill für ein nicht aufgeführtes lokales Harness. Browser-Chats, Remote-Container und andere Computer können sich nicht direkt mit diesem Gerät verbinden.",
  "connectionFallback.name": "Harness-Name",
  "connectionFallback.namePlaceholder": "Zum Beispiel Kimi Code",
  "connectionFallback.generating": "Wird erstellt…",
  "connectionFallback.generate": "Kopplungs-Skill erstellen",
  "connectionFallback.generateFailed":
    "Kopplungs-Skill konnte nicht erstellt werden.",
  "connectionFallback.retry": "Wiederholen",
  "connectionFallback.generated":
    "Einmaliger Kopplungs-Skill für {name} erstellt. Er läuft um {time} ab.",
  "connectionFallback.skillLabel": "Kopplungs-Skill",
  "connectionFallback.copy": "Skill kopieren",
  "connectionFallback.copied":
    "Kopplungs-Skill kopiert. Fügen Sie ihn im passenden Harness ein und führen Sie ihn aus.",
  "connectionFallback.copyFailed":
    "Kopieren fehlgeschlagen. Markieren und kopieren Sie stattdessen den Text oben.",
  "connectionFallback.localDescription":
    "Nur für lokale MCP- oder Befehlszeilen-Tools. Browser-Chats können sich nicht direkt mit diesem Gerät verbinden.",
  "connectionFallback.loading": "Lokale Verbindungsdaten werden geladen…",
  "connectionFallback.loadFailed":
    "Verbindungsdaten konnten nicht geladen werden.",
  "connectionFallback.format": "Format der Verbindungsdaten",
  "connectionFallback.promptTab": "Verbindungs-Prompt",
  "connectionFallback.mcpTab": "MCP-Konfiguration",
  "connectionFallback.commandTab": "Befehl",
  "connectionFallback.materialLabel": "Lokale Verbindungsdaten",
  "connectionFallback.materialBoundary":
    "Enthält lokale Pfade, aber keine Erinnerungen oder Schlüssel. Prüfen Sie den Inhalt vor dem Einfügen und lassen Sie MemHub geöffnet.",
  "connectionFallback.materialCopied":
    "Kopiert. Es wurde noch keine Verbindung hergestellt.",
  "connectionFallback.copyPrompt": "Prompt kopieren",
  "connectionFallback.copyConfig": "Konfiguration kopieren",
  "connectionFallback.copyCommand": "Befehl kopieren",
  "connectionFallback.promptTemplate":
    "Verbinde dieses lokale Harness mit der folgenden MCP-Konfiguration mit MemHub:\n```json\n{mcp}```\n{shellBlock}\nFühre nach dem Verbinden eine echte Erinnerungsabfrage aus und berichte das Ergebnis wahrheitsgemäß.",
  "connectionFallback.promptShell":
    "\nFalls nur lokale Shell-Befehle unterstützt werden, verwende:\n```bash\n{shell}```\n",
  "sdk.memory.title": "SDK-Erinnerungsbibliothek",
  "sdk.memory.status": "Erinnerungsstatus",
  "sdk.memory.status.active": "Verfügbar",
  "sdk.memory.status.superseded": "Ersetzt",
  "sdk.memory.total": "{count} Erinnerungen",
  "sdk.memory.total.one": "{count} Erinnerung",
  "sdk.memory.loadFailed":
    "Erinnerungsbibliothek konnte nicht geladen werden. Bitte wiederholen Sie den Vorgang.",
  "sdk.memory.detailLoadFailed":
    "Details der Erinnerung konnten nicht geladen werden.",
  "sdk.memory.updateFailed":
    "Änderung konnte nicht gespeichert werden. Bitte wiederholen Sie den Vorgang.",
  "sdk.memory.deleteFailed":
    "Erinnerung konnte nicht gelöscht werden. Bitte wiederholen Sie den Vorgang.",
  "sdk.memory.loading": "Erinnerungsbibliothek wird geladen…",
  "sdk.memory.pagination": "Seiten der SDK-Erinnerungsbibliothek",
  "sdk.memory.previous": "Vorherige Seite",
  "sdk.memory.next": "Nächste Seite",
  "sdk.memory.detail": "SDK-Erinnerungsdetails",
  "sdk.memory.content": "Inhalt der Erinnerung",
  "sdk.memory.save": "Speichern",
  "sdk.memory.delete": "Erinnerung löschen",
  "sdk.memory.scene.chat": "Chat",
  "sdk.memory.scene.working": "Arbeit",
  "sdk.memory.scene.coding": "Programmieren",
  "sdk.memory.task": "Aufgabe",
  "sdk.memory.solution": "Lösung",
  "sdk.memory.page": "Seite {page} von {pages}",
  "sdk.raw.title": "SDK-Originalunterhaltungen",
  "sdk.raw.searchLabel": "Originalunterhaltungen durchsuchen",
  "sdk.raw.searching": "Suche läuft…",
  "sdk.raw.search": "Suchen",
  "sdk.raw.loadFailed":
    "Originalunterhaltungen sind vorübergehend nicht verfügbar.",
  "sdk.raw.searchFailed":
    "Originalunterhaltungen konnten nicht durchsucht werden. Bitte wiederholen Sie den Vorgang.",
  "sdk.raw.recent": "Letzte Originalunterhaltungen",
  "sdk.raw.empty":
    "Noch keine Originalunterhaltungen. Verbinde eine Quelle, damit sie hier erscheinen.",
  "sdk.raw.total": "{count} Originalunterhaltungen",
  "sdk.raw.total.one": "{count} Originalunterhaltung",
  "memory:当前使用基础记忆。新对话会保存为原始记录，不会生成高级记忆；以前生成的高级记忆仍会保留，但当前不会显示或参与记忆召回。":
    "Der Basisspeicher ist aktiv. Neue Unterhaltungen werden als Originale gespeichert und erzeugen keine erweiterten Erinnerungen. Vorhandene erweiterte Erinnerungen bleiben gespeichert, werden aber nicht angezeigt oder abgerufen.",
  "memory:当前使用智能记忆。新对话会由模型整理为高级记忆。":
    "Der intelligente Speicher ist aktiv. Das Modell verarbeitet neue Unterhaltungen zu erweiterten Erinnerungen.",
  "memory:当前使用深度记忆。新对话会生成高级记忆，并由知识图谱和 evolve 继续整合。":
    "Der tiefe Speicher ist aktiv. Neue Unterhaltungen erzeugen erweiterte Erinnerungen, die Wissensgraph und evolve weiter zusammenführen.",
  "sdk.raw.found": "{count} Ergebnisse",
  "sdk.raw.found.one": "{count} Ergebnis",
  "memoryInspector.emptyHint":
    "Sehen Sie Quellen, Inhalt und Änderungsverlauf ein.",
  "memory.operationFailed": "Erinnerungsvorgang fehlgeschlagen ({code})",
  "memorySpace.tabs.workbench": "Arbeitsbereich",
  "selectedMemory.status.confirmed": "Erstellt",
  "selectedMemory.status.active": "Aktiv",
  "selectedMemory.status.deleted": "Gelöscht",
  "selectedMemory.status.superseded": "Ersetzt",
  "selectedMemory.status.shadow": "Ausgeblendet",
  "selectedMemory.status.negated": "Verneint",
  "selectedMemory.status.conflicted": "Konflikt",
  "selectedMemory.status.archived": "Archiviert",
  "selectedMemory.status.unknown": "Status prüfen",
  "selectedMemory.sourceUnknown": "Quelle prüfen",
  "selectedMemory.sourceUnattributed": "Unbekannte Quelle",
  "selectedMemory.discardDraft": "Ungespeicherte Änderungen verwerfen?",
  "selectedMemory.newVersionReadFailed":
    "Neue Version konnte nicht geladen werden. Ihr Entwurf bleibt erhalten.",
  "selectedMemory.updateRejected":
    "Änderungen wurden nicht übermittelt. Ihr Entwurf bleibt erhalten. Beheben Sie das Problem und wiederholen Sie den Vorgang.",
  "selectedMemory.updateUnknown":
    "Änderungen sind unbestätigt. Ihr Entwurf bleibt erhalten. Aktualisieren Sie vor dem erneuten Versuch.",
  "selectedMemory.deleteRejected":
    "Löschung wurde nicht übermittelt. Prüfen Sie diese Erinnerung und wiederholen Sie den Vorgang.",
  "selectedMemory.deleteUnknown":
    "Löschung ist unbestätigt. Prüfen Sie die Erinnerung; der Löschumfang wird nicht erweitert.",
  "selectedMemory.undoRejected":
    "Rückgängig wurde nicht übermittelt. Beheben Sie das Problem und wiederholen Sie den Vorgang.",
  "selectedMemory.undoUnknown":
    "Rückgängig ist unbestätigt. Prüfen Sie die aktuelle Version vor dem erneuten Versuch.",
  "selectedMemory.author.you": "Ihre Aussage",
  "selectedMemory.author.assistant": "Originalnachricht des Assistenten",
  "selectedMemory.author.unknown": "Autor prüfen",
  "selectedMemory.version": "Version {revision}",
  "selectedMemory.sourceLine": "Quelle {source} · {time}",
  "selectedMemory.timestamps":
    "Unterhaltungszeit {conversation} · Erstellt {created}",
  "selectedMemory.writeTime": "Geschrieben {created}",
  "selectedMemory.edit": "Bearbeiten",
  "selectedMemory.evidence": "Originalbeleg",
  "selectedMemory.evidenceLine": "{author} · Originalunterhaltung {time}:",
  "selectedMemory.evidenceUnavailable":
    "Dieser Originaltext ist derzeit nicht verfügbar.",
  "selectedMemory.viewOriginal": "Original anzeigen",
  "selectedMemory.history": "Versionsverlauf",
  "selectedMemory.more": "Weitere Aktionen",
  "selectedMemory.undo": "Letzte Änderung rückgängig",
  "selectedMemory.delete": "Erinnerung löschen",
  "selectedMemory.editTitle": "Erinnerung bearbeiten",
  "selectedMemory.originalTitle": "Originalunterhaltung",
  "selectedMemory.back": "Zurück zur Erinnerung",
  "selectedMemory.editDescription":
    "Bearbeiten Sie diese Erinnerung; die Originalunterhaltung bleibt unverändert.",
  "selectedMemory.content": "Inhalt der Erinnerung",
  "selectedMemory.scopeUnchanged": "Zugriffsbereich bleibt unverändert",
  "selectedMemory.conflictTitle": "Es gibt eine neuere Version",
  "selectedMemory.conflictDescription":
    "Ihr Entwurf bleibt erhalten. Prüfen Sie die neue Version, bevor Sie weiter bearbeiten.",
  "selectedMemory.viewNewVersion": "Neue Version anzeigen",
  "selectedMemory.continueFromVersion": "Ab dieser Version weiter bearbeiten",
  "selectedMemory.cancel": "Abbrechen",
  "selectedMemory.save": "Speichern",
  "selectedMemory.author.user": "Sie",
  "selectedMemory.author.agent": "Assistent",
  "selectedMemory.author.tool": "Tool",
  "selectedMemory.sourceUnavailable":
    "Quellberechtigungen haben sich geändert oder der Originaltext ist nicht verfügbar.",
  "selectedMemory.related": "Zugehörige Erinnerung",
  "selectedMemory.viewMemory": "Erinnerung anzeigen",
  "selectedMemory.deleteTitle": "Diese Erinnerung löschen?",
  "selectedMemory.deleteDescription":
    "Sie wird für neue Abfragen nicht mehr verwendet. Die Unterhaltung in der Quell-App wird nicht gelöscht.",
  "selectedMemory.sentCannotRecall":
    "Bereits an Apps gesendete Inhalte können nicht zurückgeholt werden.",
  "memory:{count} 条": "Einträge: {count}",
  "memory:已可用 · {count} 段原文": "Verfügbar · Originalauszüge: {count}",
  "memory:主题视角（非事项）": "Themenansicht (keine Vorgänge)",
  "memory:来源视角": "Quellenansicht",
  "memory:{count} 条记忆 · 当前已加载": "Erinnerungen: {count} · Geladen",
  "memory:最近 {count} 条": "Letzte Unterhaltungen: {count}",
  "memory:已确认": "Bestätigt",
  "memory:记忆库": "Erinnerungsbibliothek",
  "memory:问记忆": "Erinnerungen fragen",
  "memory:历史对话": "Unterhaltungsverlauf",
  "memory:项目记忆": "Projekterinnerung",
  "memory:编码偏好": "Programmierpräferenz",
  "memory:长期模式": "Langzeitmuster",
  "memory:意图": "Absicht",
  "memory:近 3 个月": "Letzte 3 Monate",
  "memory:近 6 个月": "Letzte 6 Monate",
  "memory:近 1 年": "Letztes Jahr",
  "memory:全部历史": "Gesamter Verlauf",
  "memory:{count} 条这次没能整理成功，正在重试":
    "Verarbeitung wird wiederholt · {count}",
  "memory:{count} 处理中": "In Verarbeitung: {count}",
  "memory: · 处理中 {running} / 待派发 {active}":
    " · {running} in Verarbeitung / {active} in Warteschlange",
  "memory: · {count} 条任务状态待核对":
    " · Aufgaben mit Statusprüfung: {count}",
  "memory: · {count} 条任务状态异常，正在恢复":
    " · Aufgaben mit Statusproblemen: {count}; Wiederherstellung läuft",
  "memory:{count} 等待": "Wartend: {count}",
  "memory:最新 {count} 条待处理": "Neueste ausstehend: {count}",
  "memory:历史积压 {count} 条": "Rückstand: {count}",
  "memory:（约 {count} 分钟）": " (etwa {count} Min.)",
  "memory:（约 {count} 小时）": " (etwa {count} Std.)",
  "memory:（约 {count} 天）": " (etwa {count} Tg.)",
  "memory:同步尝试 {count} 次": "Synchronisierungsversuche: {count}",
  "memory:尚未尝试同步": "Keine Synchronisierung versucht",
  "memory: · 状态更新于 {date}": " · Aktualisiert {date}",
  "memory:HY Memory 当前没有这条记忆，仅重试同步不能补齐。":
    "HY Memory enthält diese Erinnerung nicht; eine erneute Synchronisierung kann sie nicht wiederherstellen.",
  "memory:同步需要可用的本机整理模型或本地嵌入授权。":
    "Die Synchronisierung erfordert ein verfügbares lokales Verarbeitungsmodell oder die Berechtigung für lokale Embeddings.",
  "memory:这条记忆或模型版本已经变化，先刷新核对再决定下一步。":
    "Diese Erinnerung oder die Modellversion hat sich geändert. Aktualisieren und prüfen Sie sie, bevor Sie entscheiden, wie es weitergeht.",
  "memory:这条记忆已改，搜索索引还没开始同步。":
    "Diese Erinnerung wurde geändert, aber die Synchronisierung des Suchindex hat noch nicht begonnen.",
  "memory:最近一次同步未成功；可以先重试，结果仍不确定时再刷新核对。":
    "Die letzte Synchronisierung ist fehlgeschlagen. Versuchen Sie es erneut; bleibt das Ergebnis unklar, aktualisieren und prüfen Sie sie.",
  "memory:这条记忆已改，搜索索引还没跟上。":
    "Diese Erinnerung wurde geändert, aber der Suchindex ist noch nicht aktuell.",
  "memory:搜索里还是改之前那句话。":
    "Die Suche zeigt noch den Text von vor der Korrektur.",
  "memory:重试同步": "Erneut synchronisieren",
  "memory:配置整理模型": "Verarbeitungsmodell einrichten",
  "memory:未提炼 · 在窗口外": "Nicht extrahiert · Außerhalb des Fensters",
  "memory:未提炼 · 排队中": "Nicht extrahiert · In Warteschlange",
  "wb.select": "Auswählen",
  "memory:模型综合": "Modellsynthese",
  "memory:原文摘录": "Originalauszug",
  "memory:没有命中": "Kein Treffer",
  "memory:正在生成": "Wird erstellt",
  "memory:正在检索记忆": "Erinnerungen werden gesucht",
  "memory: · 基于 {count} 条已确认记忆":
    " · Basierend auf {count} bestätigten Erinnerungen",
  "memory: · 基于 {count} 条已确认记忆.one":
    " · Basierend auf einer bestätigten Erinnerung",
  "memory:整理需要处理": "Verarbeitung erfordert Aufmerksamkeit",
  "memory:正在整理记忆": "Erinnerungen werden verarbeitet",
  "memory:整理状态": "Verarbeitungsstatus",
  "memory:自动提炼范围": "Automatisches Extraktionsfenster",
  "memory:手动提炼下一批": "Nächsten Stapel manuell extrahieren",
  "memory:刷新核对": "Aktualisieren und prüfen",
  "memory:正在导入历史会话，记忆库暂时只显示一部分；导入完成后会自动补全，不需要重试。":
    "Unterhaltungsverlauf wird importiert. Nur ein Teil der Erinnerungen ist sichtbar; die Liste wird nach dem Import automatisch vervollständigt.",
  "memory:选择：{content}": "Auswählen: {content}",
  "memory:处理失败：{content}": "Verarbeitung fehlgeschlagen: {content}",
  "memory:已选 {count} 条": "{count} ausgewählt",
  "memory:已选 {count} 条.one": "{count} ausgewählt",
  "memory:删除所选 {count} 条": "{count} ausgewählte löschen",
  "memory:删除所选 {count} 条.one": "{count} ausgewählte löschen",
  "memory:重试失败的 {count} 条": "{count} fehlgeschlagene wiederholen",
  "memory:重试失败的 {count} 条.one": "{count} fehlgeschlagene wiederholen",
  "memory:没有{status}的记忆。": "Keine Erinnerungen mit Status „{status}“.",
  "memory:已{action} {count} 条":
    "Aktion abgeschlossen ({action}): {count} Einträge",
  "memory:已{action} {count} 条.one":
    "Aktion abgeschlossen ({action}): {count} Eintrag",
  "memory:，{count} 条未完成": ", {count} unvollständig",
  "memory:，{count} 条未完成.one": ", {count} unvollständig",
  "memory:未能{action}：{error}": "{action} fehlgeschlagen: {error}",
  "memory:重试{action}": "{action} wiederholen",
  "memory:删除（操作）": "Löschen",
  "memory:处理（操作）": "Verarbeiten",
  "memory:{count} 条原话": "{count} Originalauszüge",
  "memory:{count} 条原话.one": "{count} Originalauszug",
  "memory:{count} 次整理已完成": "{count} Verarbeitungsläufe abgeschlossen",
  "memory:{count} 次整理已完成.one": "{count} Verarbeitungslauf abgeschlossen",
  "memory: · {count} 条在窗口外（原文可搜）":
    " · {count} außerhalb des Fensters (Originaltext durchsuchbar)",
  "memory: · {count} 条在窗口外（原文可搜）.one":
    " · {count} außerhalb des Fensters (Originaltext durchsuchbar)",
  "memory: · 已规划 {planned} 批，当前第 {current} 批":
    " · {planned} Stapel geplant, aktuell Stapel {current}",
  "memory:{kind}卡在 {reason}（{count} 条）":
    "{kind} blockiert durch {reason} ({count})",
  "memory:已加载 {count} 条记忆": "{count} Erinnerungen geladen",
  "memory:已加载 {count} 条记忆.one": "{count} Erinnerung geladen",
  "memory:回答只使用已确认记忆。每个答案都可以回到对应记忆和来源原话。":
    "Antworten nutzen nur bestätigte Erinnerungen. Jede Antwort verweist auf die Erinnerung und den Originaltext.",
  "memory:选择一条记忆": "Erinnerung auswählen",
  "memory:查看它的来源、使用范围和修改历史。":
    "Sehen Sie Quellen, Zugriffsbereich und Änderungsverlauf ein.",
  "memory:查看替代它的记忆": "Ersetzende Erinnerung anzeigen",
  "memory:查看它取代的旧记忆": "Ersetzte Erinnerung anzeigen",
  "memory:保存纠正": "Korrektur speichern",
  "memory:修改历史": "Änderungsverlauf",
  "memory:已可用": "Verfügbar",
  "memory:处理中…": "Wird verarbeitet…",
  "memory:搜索": "Suchen",
  "memory:记忆治理状态": "Erinnerungsstatus",
  "memory:记忆空间模式": "Modi des Erinnerungsbereichs",
  "memory:来源状态待核对，记忆库暂不可读取。":
    "Quellstatus muss geprüft werden; die Erinnerungsbibliothek kann noch nicht gelesen werden.",
  "memory:记忆库读取失败，请重试。":
    "Erinnerungsbibliothek konnte nicht geladen werden. Bitte wiederholen Sie den Vorgang.",
  "memory:重新读取记忆库": "Bibliothek neu laden",
  "memory:正在读取记忆库…": "Erinnerungsbibliothek wird geladen…",
  "memory:批量操作结果": "Ergebnis der Stapelaktion",
  "memory:批量操作": "Stapelaktionen",
  "memory:取消选择": "Auswahl aufheben",
  "memory:向已确认记忆提问": "Bestätigte Erinnerungen fragen",
  "memory:搜索记忆库": "Erinnerungsbibliothek durchsuchen",
  "memory:问答记忆范围": "Fragebereich der Erinnerungen",
  "memory:只问个人画像": "Nur zum persönlichen Profil fragen",
  "memory:问全部记忆（含项目）": "Alle Erinnerungen fragen (inkl. Projekte)",
  "memory:例如：我对真实数据有什么要求？":
    "Zum Beispiel: Welche Anforderungen habe ich an echte Daten?",
  "memory:搜索偏好、决定或经历":
    "Präferenzen, Entscheidungen oder Erfahrungen suchen",
  "memory:根据记忆回答": "Aus Erinnerungen antworten",
  "memory:清除搜索": "Suche löschen",
  "wb.finding.duplicate": "Aus derselben Originalnachricht",
  "wb.finding.duplicateText":
    "„{first}“ und „{second}“ stammen aus derselben Originalnachricht und wurden {firstScope} und {secondScope} zugeordnet. Prüfen Sie, ob sie dasselbe meinen.",
  "wb.finding.dormant": "Länger nicht aktualisiert",
  "wb.finding.dormantText":
    "Erinnerungen, die seit zwei Monaten weder geändert noch erwähnt wurden: {count}. Sie sollten vielleicht geprüft werden.",
  "wb.finding.blocked": "Wartet auf Voraussetzung",
  "wb.finding.blockedText":
    "{count} Unterhaltungen warten auf eine Voraussetzung. Ihr Originaltext ist bereits gespeichert und durchsuchbar.",
  "wb.finding.blockedText.one":
    "{count} Unterhaltung wartet auf eine Voraussetzung. Ihr Originaltext ist bereits gespeichert und durchsuchbar.",
  "wb.unclassified": "Nicht kategorisiert",
  "wb.profile": "Persönliches Profil",
  "wb.project": "Projekt {id}",
  "wb.topics": "Thema",
  "wb.all": "Alle",
  "wb.topicCount": "{count} Themen gebildet",
  "wb.topicCount.one": "{count} Thema gebildet",
  "wb.singletons": "; {count} weitere Tags kommen nur einmal vor",
  "wb.singletons.one": "; {count} weiteres Tag kommt nur einmal vor",
  "wb.memories": "{count} Erinnerungen",
  "wb.memories.one": "{count} Erinnerung",
  "wb.sources": "{count} Quellen",
  "wb.sources.one": "{count} Quelle",
  "wb.once":
    "Dieses Thema kommt nur hier vor. Es könnte einmalig sein, oder dasselbe Thema wurde unter verschiedenen Namen aufgeteilt.",
  "wb.graphTitle": "Beziehungsgraph: {label}",
  "wb.readSummary": "Zusammenfassung dieses Vorgangs lesen",
  "wb.summaryPrompt":
    "Erkläre „{label}“ klar: Wie ist der Stand, was wurde entschieden und was ist noch offen?",
  "wb.building": "Wird zusammengeführt…",
  "wb.build": "Beziehungen mit HY Memory erstellen",
  "wb.drawn": "{shown} / {total} Erinnerungen angezeigt",
  "wb.askLabel": "Zu diesem Vorgang fragen",
  "wb.askPlaceholder":
    "Zu diesem Vorgang fragen; Antworten nutzen nur bestätigte Erinnerungen",
  "wb.ask": "Fragen",
  "wb.originals": "Originalunterhaltungen",
  "wb.evidenceNote":
    "Diese Erinnerung stammt aus den {count} Originalauszügen unten, nach Zeit sortiert",
  "wb.evidenceNote.one": "Diese Erinnerung stammt aus dem Originalauszug unten",
  "wb.pickCircle":
    "Wählen Sie einen Kreis im Graphen, um hier seinen Originaltext zu sehen",
  "wb.unavailable": "Originaltext der Quelle ist nicht verfügbar",
  "wb.fullConversation": "Ganze Unterhaltung anzeigen",
  "wb.you": "Sie",
  "wb.assistant": "Assistent",
  "wb.noMemories":
    "Noch keine Erinnerungen. Durchsuchen Sie importierte Originalunterhaltungen im Unterhaltungsverlauf.",
  "wb.waiting":
    "Einige Unterhaltungen warten auf eine Voraussetzung. Ihr Originaltext ist gespeichert und durchsuchbar.",
  "wb.conditions": "Fehlendes anzeigen",
  "wb.findings": "Was Ihre Aufzeichnungen zeigen",
  "wb.findingsNote":
    "Aus lokalen Aufzeichnungen berechnet. Wählen Sie einen Punkt aus, um die zugehörigen Erinnerungen im Graphen hervorzuheben.",
  "wb.showGraph": "Im Graphen anzeigen",
  "wb.loading": "Erinnerungen werden geladen…",
  "wb.coreStarting":
    "Erscheint automatisch, sobald der lokale Core gestartet ist; kein Aktualisieren nötig.",
  "wb.nothingToConnect": "Noch nichts zu verbinden.",
  "wb.skeletonNote":
    "Autorisieren Sie eine Quelle, um Quellen- und Themenstruktur zu sehen; Erinnerungen erscheinen, sobald sie erstellt werden.",
  "wb.authorize": "Quelle autorisieren",
  "wb.conversations": "{count} Unterhaltungen",
  "wb.conversations.one": "{count} Unterhaltung",
  "wb.chipChanges": "Was hat sich hier zuletzt geändert?",
  "wb.chipSummarize": "In wenigen Punkten zusammenfassen",
  "wb.unknownTime": "Zeit unbekannt",
  "wb.unknownSource": "Unbekannte Quelle",
  "wb.transcriptGone": "Diese Unterhaltung ist nicht mehr in der Bibliothek.",
  "wb.edgeCount": "{label}: {count} Verbindungen",
  "wb.edgeCount.one": "{label}: {count} Verbindung",
  "wb.noRelations":
    "HY Memory hat noch keine Beziehungen gefunden. Führen Sie mit der Schaltfläche oben System 2 aus.",
  "wb.noModelRelations":
    "Kein nutzbares Modell eingerichtet; HY-Memory-Beziehungen sind nicht verfügbar.",
  "provenance.multiple": "Mehrere Autoren",
  "provenance.person": "Originalnachricht des Nutzers",
  "provenance.agent": "Originalnachricht des Agenten",
  "provenance.unknown": "Autor unbekannt",
  "provenance.imported": "Importierte Erinnerung · {author}",
  "provenance.manual": "Manuell hinzugefügt",
  "provenance.answered": "Aus einer Antwort erstellt",
  "provenance.generated": "Aus einer KI-Antwort erstellt",
  "provenance.conversation": "Aus Originalunterhaltungen erstellt",
  "arrival.region": "Verarbeitungsstatus der Erinnerungen",
  "arrival.importing": "Originale werden noch importiert",
  "arrival.messages": "{count} Nachrichten gespeichert",
  "arrival.messages.one": "{count} Nachricht gespeichert",
  "arrival.conversations": "{count} Unterhaltungen",
  "arrival.conversations.one": "{count} Unterhaltung",
  "arrival.details": "Fortschritt und Details",
  "arrival.backlog":
    "Große Datenmenge; zur Sicherung der Qualität wird im Hintergrund stapelweise verarbeitet",
  "arrival.items": "{count} Einträge",
  "arrival.items.one": "{count} Eintrag",
  "arrival.configure": "Verarbeitung einrichten",
  "arrival.status": "Verarbeitungsstatus anzeigen",
  "arrival.history": "Gespeicherte Unterhaltungen anzeigen",
  "arrival.connect": "App verbinden",
  "arrival.retained": "Gespeicherte Originale bleiben auf diesem Gerät.",
  "arrival.description.model-required":
    "Prüfen Sie das Verarbeitungsmodell, bevor Sie fortfahren.",
  "arrival.description.processing":
    "Letzte Unterhaltungen werden zuerst verarbeitet; weitere Erinnerungen erscheinen nach und nach",
  "arrival.description.importing":
    "Originale werden gespeichert; der Importfortschritt zeigt nicht den Fortschritt der Erinnerungserstellung.",
  "arrival.description.failed":
    "Prüfen Sie die Fehlerursache, bevor Sie sich für einen neuen Versuch entscheiden.",
  "arrival.description.unavailable":
    "Die zuletzt bestätigten Angaben bleiben erhalten; prüfen Sie den aktuellen Fortschritt erneut.",
  "arrival.description.refreshing":
    "Lokale Aufzeichnungen werden geprüft; unbestätigter Fortschritt wird nicht gezählt.",
  "arrival.description.checking":
    "Gespeicherte Inhalte bleiben erhalten; Fortschritt muss aktualisiert werden.",
  "arrival.description.stopped":
    "Verarbeitung wurde angehalten; prüfen Sie den Status, bevor Sie fortfahren.",
  "arrival.description.idle": "Derzeit läuft keine Verarbeitungsaufgabe.",
  "arrival.description.empty":
    "Verbinden Sie eine App oder importieren Sie eine Unterhaltungsdatei; Originale werden zuerst auf diesem Gerät gespeichert.",
  "arrival.title.refreshing":
    "Gespeicherte Inhalte bleiben erhalten; Fortschritt wird geprüft",
  "arrival.title.checking": "Fortschritt muss aktualisiert werden",
  "arrival.title.importing": "Unterhaltungen werden importiert",
  "arrival.title.processing": "Erinnerungen werden erstellt",
  "arrival.title.continue": "Aktion erforderlich",
  "arrival.title.model": "Verarbeitungsmodell nicht eingerichtet",
  "arrival.title.empty": "Noch keine Unterhaltungen gespeichert",
  "handoff.region": "Erinnerungen in andere App übernehmen",
  "handoff.answer": "Antwort und Quellenangaben in andere App übernehmen",
  "handoff.selected": "Ausgewählte Erinnerungen in andere App übernehmen",
  "handoff.title": "In andere App übernehmen",
  "handoff.closeTitle": "Vorschau schließen",
  "handoff.close": "Übergabevorschau schließen",
  "handoff.stats":
    "{count} Erinnerungen · {characters} Zeichen · Nicht gekürzt",
  "handoff.stats.one":
    "{count} Erinnerung · {characters} Zeichen · Nicht gekürzt",
  "handoff.content": "Vollständiger Inhalt für die andere App",
  "handoff.target": "Ziel-App",
  "handoff.other": "Andere App · Nur kopieren",
  "handoff.copyOnly": " · Nur kopieren",
  "handoff.boundary":
    "Nur der Inhalt oben wird übernommen, nicht die Originalunterhaltungen. Kopieren autorisiert keine Verbindung; Kopien in anderen Apps können nicht zurückgeholt werden.",
  "handoff.copy": "Inhalt kopieren",
  "handoff.open": "Kopieren und {host} öffnen",
  "handoff.copyFailed":
    "Kopieren fehlgeschlagen; keine App wurde geöffnet. Markieren und kopieren Sie stattdessen den Text in der Vorschau.",
  "handoff.opened":
    "Kopiert und Öffnen von {host} angefordert. Nichts wurde gesendet; prüfen Sie das Eingabefeld im Ziel.",
  "handoff.notPrefilled":
    "Vollständig kopiert. {host} konnte nicht vorausgefüllt werden; fügen Sie den Inhalt in der Ziel-App ein. Nichts wurde gesendet.",
  "handoff.openFailed":
    "Vollständig kopiert, aber {host} konnte nicht geöffnet werden. Nichts wurde gesendet.",
  "handoff.copied": "{target} vollständig kopiert. Nichts wurde gesendet.",
  "handoff.pasteTarget": ", bereit zum Einfügen in {host}",
  "wb.memory": "Diese Erinnerung",
  "wb.node": "Dieser Knoten",
  "wb.matter": "Dieser Vorgang",
  "wb.available": "Verfügbar",
  "wb.superseded": "Ersetzt",
  "wb.deleted": "Gelöscht",
  "wb.revision": "Version {revision}",
  "wb.collapse": "Einklappen",
  "wb.oldSearch": "Die Suche zeigt noch den Text von vor der Korrektur.",
  "wb.missingIndex":
    "HY Memory enthält diese Erinnerung nicht; ein erneuter Versuch kann sie nicht wiederherstellen.",
  "wb.retrySync": "Erneut synchronisieren",
  "wb.correctForm": "Inhalt der Erinnerung korrigieren",
  "wb.content": "Inhalt der Erinnerung",
  "wb.correct": "Korrigieren",
  "wb.saveCorrection": "Korrektur speichern",
  "wb.cancelEdit": "Bearbeitung abbrechen",
  "wb.undo": "Letzte Änderung rückgängig",
  "wb.delete": "Löschen",
  "wb.nodeNote":
    "Dies ist keine Erinnerung; Erinnerungen sind damit verknüpft. Direkte Verknüpfungen: {direct}; in zwei Schritten erreichbar: {indirect}.",
  "wb.direct": "Direkt",
  "wb.twoHops": "Zwei Schritte",
  "wb.idle":
    "Kreise stehen für Erinnerungen; Linien zeigen, welche in derselben Unterhaltung vorkamen. Wählen Sie eine aus, um ihre Herkunft zu sehen und sie zu bearbeiten. Wenn Sie links einen anderen Vorgang wählen, wird der Graph aktualisiert.",
  "wb.searching": "Erinnerungen werden gesucht…",
  "wb.generated": "Modellsynthese",
  "wb.extractive": "Originalauszug",
  "wb.noHit": "Kein Treffer",
  "wb.answerSource": "{mode} · Basierend auf {count} bestätigten Erinnerungen",
  "wb.answerSource.one": "{mode} · Basierend auf einer bestätigten Erinnerung",
  "wb.recalled": "{count} Erinnerungen abgerufen",
  "wb.recalled.one": "{count} Erinnerung abgerufen",
  "wb.saveMemory": "Als Erinnerung speichern",
  "wb.tooLong":
    "Diese Antwort überschreitet das aktuelle Speicherlimit und kann nicht als Erinnerung gespeichert werden.",
  "wb.summary":
    "Dies ist eine Zusammenfassung, keine Erinnerung. Sie wird jedes Mal neu erstellt, damit sie mit den Erinnerungen unten übereinstimmt.",
  "wb.thisAnswer": "Diese Antwort",
  "wb.type.source": "Quell-App",
  "wb.type.topic": "Thema",
  "wb.type.schema": "HY-Synthese",
  "wb.type.memory": "Erinnerungen",
  "wb.origin.hy_relation": "HY-Memory-Synthese",
  "wb.origin.same_conversation": "Gleiche Unterhaltung",
  "wb.origin.shared_tag": "Gleiches Thema",
  "wb.origin.provenance": "Originaltextquelle",
  "wb.relation.evidence_source": "Aus",
  "wb.relation.same_conversation": "Gleiche Unterhaltung",
  "wb.relation.shared_tag": "Gleiches Thema",
  "wb.relation.derived_from": "Zusammengeführt aus",
  "wb.relation.tagged_with": "Getaggt mit",
  "wb.relation.related_to": "Verwandt",
  guideEyebrow: "Ausprobieren",
  guideTitle: "Machen Sie dort weiter, wo Ihre letzte Unterhaltung endete",
  guideBack: "Zurück zur Erinnerung",
  guideChooseScene: "Szenario wählen",
  guideSame: "In neuer Unterhaltung fortsetzen",
  guideSameDescription:
    "Finden Sie frühere Entscheidungen in einer neuen Unterhaltung mit derselben App wieder.",
  guideSwitch: "In anderer App fortsetzen",
  guideSwitchDescription:
    "Gleiche Arbeit, andere KI. Übernehmen Sie zuerst den relevanten Kontext.",
  guideNew: "Mich einem neuen Tool vorstellen",
  guideNewDescription:
    "Beginnen Sie mit einem Detail Ihrer Wahl, statt sich erneut vorzustellen.",
  guideReadError:
    "Erinnerungen können gerade nicht gelesen werden. Versuchen Sie es später erneut.",
  guideDetailError:
    "Diese Erinnerung kann gerade nicht gelesen werden. Wählen Sie eine andere oder aktualisieren Sie.",
  guideChanged:
    "Die Erinnerung oder ihre Berechtigungen haben sich geändert. Aktualisieren Sie und prüfen Sie sie erneut.",
  guideConnectionChanged:
    "Verbindungsstatus hat sich geändert; nichts wurde kopiert. Wählen Sie erneut eine Methode.",
  guideCopiedOpened:
    "Kopiert und Öffnen von {host} angefordert. Nichts wurde gesendet; prüfen Sie das Eingabefeld.",
  guideCopiedNotFilled:
    "Vollständig kopiert, aber Vorausfüllen nicht möglich. Fügen Sie den Inhalt in der Ziel-App ein; nichts wurde gesendet.",
  guideCopiedNotOpened:
    "Vollständig kopiert, aber die Ziel-App konnte nicht geöffnet werden. Nichts wurde gesendet.",
  guideCopied:
    "Vollständig kopiert. Nichts wurde gesendet und kein neuer Lesezugriff gewährt.",
  guideValidationError:
    "Aktuelle Erinnerung oder Berechtigungen konnten nicht geprüft werden. Die alte Vorschau wurde ausgeblendet; aktualisieren Sie und versuchen Sie es erneut.",
  guideCopyError:
    "Neuester Inhalt konnte nicht geprüft oder kopiert werden. Versuchen Sie es erneut; die Ziel-App wurde nicht geöffnet.",
  guideManual: "In der App hinzugefügt",
  guideOriginalApp: "Quell-App",
  guideListSeparator: ", ",
  guideSaved: "{count} Originalnachrichten auf diesem Gerät · ",
  "guideSaved.one": "{count} Originalnachricht auf diesem Gerät · ",
  guideReadingMemories: "Verarbeitete Erinnerungen werden geladen",
  guideLoaded: "{count} nutzbare Erinnerungen auf dieser Seite geladen",
  "guideLoaded.one": "{count} nutzbare Erinnerung auf dieser Seite geladen",
  guideSeparate: ". Diese Phasen werden getrennt gezählt.",
  guideChooseBackground: "Kontext wählen",
  guideCheckSources: "Quellen prüfen",
  guideTry: "In neuer Unterhaltung testen",
  guideLoading: "Vorhandene Erinnerungen auf diesem Gerät werden geladen…",
  guideEmptyTitle: "Fügen Sie zuerst einen nützlichen Kontext hinzu",
  guideEmptyDescription:
    "Für diesen Test sind keine Erinnerungen verfügbar. Importierte Originale sind keine verarbeiteten Erinnerungen, und Vorschläge werden nicht automatisch geteilt.",
  guideAdd: "Erinnerung hinzufügen",
  guideConnections: "Importe und Verbindungen anzeigen",
  guideRefresh: "Aktualisieren",
  guideBackgroundTitle: "Kontext für diesen Test",
  guideRefreshExample: "Beispiel aktualisieren",
  guideChooseMemory: "Erinnerung auswählen",
  guideLoadedScope:
    "Wählen Sie aus {count} hier geladenen nutzbaren Erinnerungen; das ist nicht Ihr gesamter Verlauf.",
  "guideLoadedScope.one":
    "Eine nutzbare Erinnerung hier geladen; das ist nicht Ihr gesamter Verlauf.",
  guideCheckingEvidence: "Originalbeleg wird geprüft…",
  guideRevision:
    "{source} · Version {revision} · Vom System erstellt, bitte prüfen",
  guideScope: "Bereich: ",
  guideAllApps:
    "App-übergreifend verfügbar; nicht unbedingt eine persönliche Präferenz",
  guidePrivate:
    "Externer Abruf ist aus; manuelles Teilen erfordert eine eigene Entscheidung",
  guideLimited: "Begrenzter Bereich; externer Abruf wurde nicht erweitert",
  guideEvidence: "Originalbeleg · {count} Auszüge",
  "guideEvidence.one": "Originalbeleg · {count} Auszug",
  guideUnknownSource: "Unbekannte Quelle",
  guideYou: "Sie",
  guideUnknownAuthor: "Autor unbekannt",
  guideUnknownTime: "Zeit unbekannt",
  guideUnavailable:
    "Originaltext oder aktuelle Berechtigung ist nicht verfügbar. Wählen Sie eine andere Erinnerung oder aktualisieren Sie; dieser Inhalt wird nicht geteilt.",
  guideDraftTitle: "Einstieg für die nächste Unterhaltung",
  guideChooseApp: "App zum Testen wählen",
  guideCopyOnly: "App selbst wählen · Nur kopieren",
  guideHostsError:
    "Verbindungsstatus konnte nicht gelesen werden. Wiederholen Sie den Vorgang oder kopieren Sie nur den Kontext.",
  guideNoApp:
    "Keine verbundene App ausgewählt. Manuelles Teilen richtet keine Verbindung ein.",
  guideWired:
    "Letzte Selbstprüfung erfolgreich; prüfen Sie, ob es für diesen Zweck funktioniert.",
  guideInstalled: "Verbindung eingerichtet; Lesezugriff noch nicht geprüft.",
  guideNotInstalled: "Leseverbindung nicht eingerichtet.",
  guideManage: "App-Verbindungen verwalten",
  guideChooseRoute: "Art der Kontextübernahme wählen",
  guideContext: "Kontext übernehmen",
  guideQuery: "App suchen lassen",
  guideTopic: "Fortzusetzendes Thema",
  guideQueryHint:
    "Senden Sie nur eine Abfrage; fügen Sie die erwartete Antwort von oben nicht in die neue Unterhaltung ein.",
  guideNewHint:
    "Teilen Sie nur die gewählte Erinnerung; es wird kein persönliches Profil automatisch erstellt.",
  guideContextHint:
    "Enthält diesen Kontext und seine Quellenangaben; dies ist kein automatischer Abruftest.",
  guidePreview: "Vollständige Vorschau des Einstiegs",
  guidePreviewPlaceholder:
    "Der Einstieg erscheint, sobald der Originalbeleg verfügbar ist",
  guideChecking: "Wird geprüft…",
  guideCopy: "Einstieg kopieren",
  guideCopyOpen: "Kopieren und {host} öffnen",
  guideNoReceipt:
    "Keine Bestätigung eines externen Aufrufs. Hat die neue Unterhaltung den richtigen Kontext zitiert?",
  guideVerified: "Ergebnis geprüft",
  guideMissing: "Nicht gefunden",
  guideFeedbackOk:
    "Ihr Feedback: geprüft. Dies ist kein automatischer Prüfnachweis.",
  guideFeedbackMissing:
    "Ihr Feedback: nicht gefunden. Prüfen Sie zuerst Quellenimporte, Erinnerungsbereich und App-Verbindungen.",
  guideInvitation: "Einladung zum Ausprobieren",
  guideInvitationHint:
    "Setzen Sie eine neue Unterhaltung mit einer Ihrer Erinnerungen fort.",
  guideTryOnce: "Ausprobieren",
  guideLater: "Später",
  guideDismiss: "Einladung schließen",
  healthTitle: "Systemdiagnose",
  healthLocal: "Lokaler Dienst",
  healthBackground: "Hintergrunddienst",
  healthSources: "Summen nach Quelle",
  healthProcessing: "Erinnerungsverarbeitung",
  healthHosts: "KI-Verbindungen",
  healthDetailed: "Detailprotokoll",
  healthDetailedTitle: "Detailprotokoll (zur Fehlersuche)",
  healthDetailedHint:
    "Standardmäßig aus. Aktivieren Sie es nur, wenn der Support Sie zur Fehlersuche darum bittet.",
  healthDetailedError:
    "Detailprotokoll konnte nicht geändert werden. Bitte wiederholen Sie den Vorgang.",
  healthRefresh: "Status aktualisieren",
  healthIncident: "Problemzeit markieren",
  healthIncidentDesktop: "Markieren Sie die Problemzeit in der Desktop-App.",
  healthIncidentPrompt: "Problem kurz beschreiben (optional)",
  healthIncidentCopied: "Problem-ID kopiert: {id}",
  healthIncidentError:
    "Problemzeit konnte nicht markiert werden. Bitte wiederholen Sie den Vorgang.",
  healthReadFailed: "Vorübergehend nicht verfügbar",
  healthStale: " · Daten sind veraltet; letztes Ergebnis wird angezeigt.",
  healthReading: "Status wird gelesen…",
  healthRereading:
    "Wird aktualisiert; letzter erfolgreicher Stand wird angezeigt…",
  healthReadSuccessTime: "Zuletzt erfolgreich gelesen",
  healthReadFailedTime: "Lesefehler um",
  healthReadingSources: "Status von lokalem Dienst und Quellen wird gelesen…",
  healthPrevious: "Zuletzt gelesen",
  healthPreviousPrefix: "Zuletzt gelesen: ",
  healthRunning: "Läuft",
  healthCoreVersion: "{state} · Version {version} · Instanz {instance}",
  healthCoreDisconnected: "Lokaler Core ist noch nicht verbunden.",
  healthBackgroundUnavailable:
    "Der Hintergrunddienst kann hier nicht geprüft werden. Verwenden Sie eine unterstützte Desktop-App.",
  healthRuntime: "Core: {core} · Host-Bridge: {broker}",
  healthSourceCount: "{conversations} Unterhaltungen · {messages} Nachrichten",
  healthNoSources: "Noch keine verbundenen Quellen.",
  healthTaskCounts:
    "{running} in Verarbeitung · {pending} in Warteschlange · {completed} abgeschlossen · {failed} fehlgeschlagen",
  healthImportDetails: "Import- und Verarbeitungsdetails",
  healthRawTurns: "Originaltext und vollständige Runden",
  healthRawCounts: "{raw} Originale · {turns} vollständige Runden",
  healthHy: "HY-Verarbeitung",
  healthHyCounts:
    "{calls} Aufrufe · {completed} abgeschlossen · {unknown} unklar · {failed} fehlgeschlagen · {cancelled} abgebrochen",
  healthOutput: "Ergebnis",
  healthMemories: "{count} Erinnerungen",
  "healthMemories.one": "{count} Erinnerung",
  healthWorkbuddy: "WorkBuddy-Erinnerungsabfragen",
  healthWorkbuddyCounts:
    "{injected} Injektionen · {timedOut} Timeouts · {unavailable} nicht verfügbar",
  healthBlocked: "{kind} blockiert durch {reason} ({count})",
  healthRetrying: "Verarbeitung wird wiederholt · {count}",
  "healthRetrying.one": "Verarbeitung wird wiederholt · {count}",
  healthNoIssues: "Keine Probleme erfordern Aufmerksamkeit.",
  healthHostsUnavailable:
    "Im Browser nicht lesbar; prüfen Sie die Desktop-App.",
  healthNoHosts: "Keine unterstützten KI-Tools auf diesem Gerät erkannt.",
  healthBackgroundHealthy: "Hintergrunddienst verfügbar",
  healthBackgroundStopped: "Hintergrunddienst gestoppt",
  healthBackgroundDegraded: "Hintergrunddienst teilweise nicht verfügbar",
  healthBackgroundUntrusted:
    "Identität des Hintergrunddienstes konnte nicht geprüft werden",
  healthBackgroundUnknown: "Hintergrundstatus nicht bestätigt",
  healthStopped: "Gestoppt",
  healthUnhealthy: "Keine Verbindung",
  healthUntrusted: "Identität nicht geprüft",
  healthUnknown: "Nicht bestätigt",
  healthNotChecked: "Nicht geprüft",
  healthWired: "Verbunden",
  healthBlind: "Installiert, aber Erinnerungen nicht abrufbar",
  healthUnreachable: "Nicht verbunden",
  startupReadError:
    "Startstatus konnte nicht gelesen werden. Bitte wiederholen Sie den Vorgang.",
  startupRetry: "Wiederholen",
  startupOpening: "MemHub wird geöffnet…",
  nativeHelp: "Hilfe",
  nativeExportLogs: "Protokolle für Support exportieren",
  nativeExportError:
    "Protokolle konnten nicht exportiert werden. Bitte versuchen Sie es später erneut.",
  nativeLogin: "{product} bei Anmeldung starten",
  nativeTrialUnavailable: " (in diesem Build nicht verfügbar)",
  nativeCliManaged: " (von CLI verwaltet)",
  nativeStatusUnavailable: " (Status nicht verfügbar)",
  nativeDevelopmentUnavailable: " (in Entwicklung nicht verfügbar)",
  nativeOpen: "{product} öffnen",
  nativeQuit: "{product} beenden (Hintergrunddienst läuft weiter)",
  nativeQuitStopsService:
    "{product} beenden (Gedächtnisdienst wird ebenfalls beendet)",
  nativeLoginError:
    "Starteinstellung bei Anmeldung konnte nicht geändert werden. Bitte wiederholen Sie den Vorgang.",
  nativeMoveTitle: "Verschieben Sie MemHub zuerst nach „Programme“",
  nativeMoveDetail:
    "Öffnen Sie MemHub erneut aus „Programme“, um andere Apps zu verbinden und das Befehlszeilen-Tool zu installieren.",
  nativeAcknowledge: "OK",
  graphWidthTitle:
    "Vorhandene Erinnerungsbibliothek passt nicht zum integrierten Modell",
  graphWidthDescription:
    "Ihre Erinnerungsbibliothek nutzt {stored} Dimensionen, das integrierte Modell erzeugt jedoch {expected}. Beide sind nicht kompatibel; die Extraktion schlägt so lange fehl, bis Sie eine Option wählen.",
  graphWidthKeep:
    "Vorhandene Bibliothek behalten (Standard): Bisherige Embedding-Konfiguration weiter verwenden; alle {count} Erinnerungen und der Graph bleiben unverändert",
  graphWidthRebuild:
    "Integriertes Modell verwenden: Vorhandenen Graphen archivieren (nicht löschen); Beziehungen von {count} Erinnerungen müssen neu erstellt werden",
  graphWidthExport:
    "Erst exportieren, dann entscheiden: Vorerst bleibt alles unverändert; wählen Sie nach dem Export erneut",
  graphWidthExported:
    "Nach {path} exportiert. Die vorhandene Bibliothek blieb unverändert; Sie können erneut wählen.",
  graphWidthUnknown:
    "Die Dimensionswahl ist unbestätigt. Die vorhandene Erinnerungsbibliothek blieb unverändert; bitte wiederholen Sie den Vorgang.",
  graphWidthConfirm: "Wahl bestätigen",
  importUserProgress:
    "Ihr Unterhaltungsverlauf wird importiert. {count} Unterhaltungen importiert. Erinnerungen erscheinen nach und nach; Sie können die App weiter nutzen.",
  "importUserProgress.one":
    "Ihr Unterhaltungsverlauf wird importiert. {count} Unterhaltung importiert. Erinnerungen erscheinen nach und nach; Sie können die App weiter nutzen.",
  importUserModel:
    "Das Modell ist noch nicht eingerichtet. Neue Unterhaltungen werden vorerst nicht zu Erinnerungen verarbeitet.",
  importUserIncomplete:
    "Einige Inhalte wurden nicht vollständig verarbeitet. Prüfen Sie die Einstellungen.",
  importUserSettings: "Zu Einstellungen",
  diagnosticExportDesktop:
    "Exportieren Sie die Diagnosedaten in der Desktop-App.",
  diagnosticExported: "Protokolle exportiert: {filename}",
  diagnosticFile: "Diagnosedatei",
  diagnosticExportFailed:
    "Diagnosedaten konnten nicht exportiert werden. Bitte wiederholen Sie den Vorgang.",
  debugTitle: "Debug-Protokolle",
  debugCount: "{count} Protokolleinträge dieser Sitzung",
  "debugCount.one": "{count} Protokolleintrag dieser Sitzung",
  debugDiagnostics: "Diagnose",
  debugUsageSubtitle: "Modellnutzung der Erinnerungsverarbeitung",
  debugTabs: "Diagnose-Tabs",
  debugLogsTab: "Protokolle",
  debugUsageTab: "Modellnutzung",
  debugFullscreen: "Vollbild",
  debugExitFullscreen: "Vollbild beenden",
  debugFullscreenLabel: "Protokolle im Vollbild anzeigen",
  debugExitFullscreenLabel: "Vollbild der Protokolle beenden",
  usageRange15m: "Letzte 15 Minuten",
  usageRange1h: "Letzte Stunde",
  usageRange6h: "Letzte 6 Stunden",
  usageRange24h: "Letzte 24 Stunden",
  usageRange7d: "Letzte 7 Tage",
  usageRange: "Zeitraum",
  usageRangeLabel: "Zeitraum der Modellnutzung",
  usageLoading: "Wird geladen…",
  usageReadError: "Laden fehlgeschlagen",
  usageLive: "Alle 5 Sekunden aktualisieren",
  usageTotalTokens: "Tokens gesamt",
  usageRequests: "Anfragen",
  usageChartRegion: "Tokens pro Minute nach Prompt",
  usageChartTitle: "Token-Nutzung nach Prompt",
  usageChartSubtitle: "Pro Minute · Tokens gesamt",
  usageBreakdown: "Prompt-Details",
  usageCurrentRange: "Aktueller Zeitraum",
  usageRequestCount: "{count} Anfragen",
  "usageRequestCount.one": "{count} Anfrage",
  usageEmpty:
    "Keine Modellaufrufe zur Erinnerungsverarbeitung in diesem Zeitraum",
  usageNoChart: "Noch keine Diagrammdaten",
  usageChartAria: "Token-Nutzung pro Minute nach Prompt",
  debugPause: "Live-Aktualisierung anhalten",
  debugResume: "Live-Aktualisierung fortsetzen",
  debugRefresh: "Jetzt aktualisieren",
  debugClose: "Debug-Protokolle schließen",
  debugSearchPlaceholder:
    "Ereignisse, Komponenten, Fehlercodes oder Inhalte suchen",
  debugSearch: "Protokolle durchsuchen",
  debugSource: "Protokollquelle",
  debugAllSources: "Alle Quellen",
  debugCapture: "Erfassungsstufe",
  debugDisplay: "Anzeigestufe",
  debugAll: "Alle",
  debugMatches: "{count} Treffer",
  "debugMatches.one": "{count} Treffer",
  debugLive: "Live-Aktualisierung",
  debugPaused: "Angehalten",
  debugTruncated: "Zu viele Protokolle; gekürzt",
  debugReadError: "Laden fehlgeschlagen",
  debugLoading: "Protokolle werden geladen…",
  debugEmpty: "Keine passenden Protokolle",
  debugOpened: "Debug-Protokolle sind geöffnet",
  debugOpen: "Debug-Protokolle öffnen",
  "这里需要 API Key，不是网址。请填写服务方提供的密钥。":
    "Geben Sie den API-Schlüssel des Dienstes ein, keine URL.",
  "服务商拒绝了这个 API Key（401）。请确认密钥属于当前填写的服务地址。":
    "Der Anbieter hat diesen API-Schlüssel abgelehnt (401). Prüfen Sie, ob der Schlüssel zum eingegebenen Endpunkt gehört.",
  "服务商没有这个模型名，请对照服务商的模型列表核对。":
    "Der Anbieter kennt diesen Modellnamen nicht. Prüfen Sie die Modellliste des Anbieters.",
  "连不上接口地址，请检查网络和服务地址是否正确。":
    "Keine Verbindung zum Endpunkt. Prüfen Sie Netzwerk und Dienstadresse.",
  未提供接口地址: "Kein Endpunkt angegeben",
  "请填写生成模型名。": "Geben Sie den Namen des Generierungsmodells ein.",
  "请填写完整的 http:// 或 https:// 接口地址。":
    "Geben Sie eine vollständige http://- oder https://-Endpunkt-URL ein.",
  "请填写向量模型名。": "Geben Sie den Namen des Embedding-Modells ein.",
  "请填写完整的向量模型接口地址。":
    "Geben Sie die vollständige Endpunkt-URL des Embedding-Modells ein.",
  "这份配置在测试期间被改过，刚才测的是改动后的版本。配置已保存但未启用，请重新测试并启用。":
    "Diese Konfiguration wurde während des Tests geändert; getestet wurde die neuere Version. Sie wurde gespeichert, aber nicht aktiviert. Testen und aktivieren Sie sie erneut.",
  "测试结果来自这份配置更早的版本。配置已保存但未启用，请重新测试并启用。":
    "Das Testergebnis gehört zu einer früheren Version dieser Konfiguration. Sie wurde gespeichert, aber nicht aktiviert. Testen und aktivieren Sie sie erneut.",
  "配置已保存，连接测试未通过。请核对配置后重试，历史对话尚未发送。":
    "Konfiguration gespeichert, aber Verbindungstest fehlgeschlagen. Prüfen Sie sie und wiederholen Sie den Vorgang. Der Unterhaltungsverlauf wurde nicht gesendet.",
  "配置已保存，尚未确认启用成功。请核对配置后重试。":
    "Konfiguration gespeichert, aber Aktivierung unbestätigt. Prüfen Sie sie und wiederholen Sie den Vorgang.",
  "配置尚未完成测试和启用，请核对后重试。":
    "Test und Aktivierung sind nicht abgeschlossen. Prüfen Sie die Konfiguration und wiederholen Sie den Vorgang.",
  "配置尚未保存成功，已填写内容保留，可核对后重试。":
    "Speichern der Konfiguration konnte nicht bestätigt werden. Ihre Eingaben bleiben erhalten; prüfen Sie sie und wiederholen Sie den Vorgang.",
  "只需配置生成模型。向量嵌入已内置在本机，不为 System 1、System 2 分开选模型。":
    "Nur das Generierungsmodell muss eingerichtet werden. Vektor-Embeddings sind lokal integriert; System 1 und System 2 brauchen keine eigenen Modelle.",
  "无法读取测试恢复记录，暂不发送新的测试。":
    "Testwiederherstellungsdaten konnten nicht gelesen werden; es wird kein neuer Test gesendet.",
  "上次模型测试结果待核对，暂不修改或启用配置。":
    "Der letzte Modelltest muss geprüft werden; die Konfiguration wird vorerst nicht bearbeitet oder aktiviert.",
  核对测试结果: "Testergebnis prüfen",
  "上次模型创建结果待核对，暂不开始新的操作。":
    "Die letzte Modellerstellung muss geprüft werden; vorerst wird kein neuer Vorgang gestartet.",
  核对创建结果: "Erstellungsergebnis prüfen",
  "配置已保存，正在测试连接…":
    "Konfiguration gespeichert; Verbindung wird getestet…",
  "连接通过，正在启用模型…": "Verbindung erfolgreich; Modell wird aktiviert…",
  刷新配置列表: "Konfigurationen aktualisieren",
  自定义一个模型: "Eigenes Modell einrichten",
  已保存的模型配置: "Gespeicherte Modellkonfiguration",
  "已保存，已启用": "Gespeichert und aktiviert",
  "已保存，连接通过": "Gespeichert; Verbindung erfolgreich",
  "已保存，测试未通过": "Gespeichert; Test fehlgeschlagen",
  "已保存，待测试": "Gespeichert; Test ausstehend",
  版本: "Version",
  "需要允许 MemHub 访问钥匙串。系统询问时请选择“始终允许”；已保存的配置不会丢失。":
    "Erlauben Sie MemHub den Zugriff auf den Schlüsselbund. Wählen Sie bei der Abfrage „Immer erlauben“; gespeicherte Konfigurationen gehen nicht verloren.",
  "测试只向此接口发送测试内容，不发送历史对话。":
    "Tests senden nur Testinhalte an diesen Endpunkt, keinen Unterhaltungsverlauf.",
  重试测试并启用: "Test wiederholen und aktivieren",
  测试并启用: "Testen und aktivieren",
  编辑配置: "Konfiguration bearbeiten",
  新增配置: "Konfiguration hinzufügen",
  编辑: "Bearbeiten",
  "。修改后需重新测试并设为当前模型，内容授权不会自动更新。":
    ". Testen Sie nach Änderungen erneut und legen Sie es als aktuelles Modell fest. Inhaltsberechtigungen werden nicht automatisch aktualisiert.",
  "配置已在其他位置变化，草稿已保留。请取消编辑后重新打开核对。":
    "Konfiguration wurde an anderer Stelle geändert; Ihr Entwurf bleibt erhalten. Brechen Sie die Bearbeitung ab und öffnen Sie sie zur Prüfung erneut.",
  服务商预设: "Anbietervorlage",
  自定义兼容服务: "Eigener kompatibler Dienst",
  "只是帮你填好接口地址和一个模型名，不代表这个模型当前可用。 请对照服务商的模型列表核对后再保存。":
    "Dies trägt einen Endpunkt und Modellnamen ein, garantiert aber keine Verfügbarkeit. Prüfen Sie vor dem Speichern die Modellliste des Anbieters.",
  协议: "Protokoll",
  "与模型服务提供的接口协议保持一致。":
    "Muss dem vom Modelldienst angebotenen Protokoll entsprechen.",
  配置名称: "Konfigurationsname",
  "用于区分配置，不影响模型请求。":
    "Kennzeichnet die Konfiguration; hat keinen Einfluss auf Modellanfragen.",
  生成模型名: "Name des Generierungsmodells",
  "例如：gpt-4o-mini…": "Zum Beispiel: gpt-4o-mini…",
  "填写服务方提供的完整模型标识。":
    "Geben Sie die vollständige Modell-ID des Dienstes ein.",
  "高级：自行配置向量模型": "Erweitert: Eigenes Embedding-Modell einrichten",
  向量配置模式: "Embedding-Konfigurationsmodus",
  本地内置: "Integriertes lokales Modell",
  自定义向量模型: "Eigenes Embedding-Modell",
  向量模型名: "Name des Embedding-Modells",
  "例如：text-embedding-3-small…": "Zum Beispiel: text-embedding-3-small…",
  向量模型来源: "Anbieter des Embedding-Modells",
  "本机 Ollama": "Lokales Ollama",
  "同源 OpenAI-compatible（共用 API Key）":
    "Same-Origin OpenAI-kompatibel (gemeinsamer API-Schlüssel)",
  "向量模型 Base URL": "Base URL des Embedding-Modells",
  "为避免凭据外泄，向量地址必须与生成模型同源。":
    "Zum Schutz der Anmeldedaten muss der Embedding-Endpunkt denselben Ursprung wie das Generierungsmodell haben.",
  "使用内置本地向量模型；不会发送内容给远程向量服务。":
    "Verwendet das integrierte lokale Embedding-Modell; es werden keine Inhalte an einen entfernten Embedding-Dienst gesendet.",
  自定义向量: "Eigenes Embedding",
  尚未填写模型: "Kein Modell eingegeben",
  尚未填写地址: "Keine Adresse eingegeben",
  "填写服务接口地址，不是聊天网页地址。":
    "Geben Sie den API-Endpunkt ein, nicht die URL der Chat-Website.",
  "（可选）": " (optional)",
  "保存后不会再次显示…": "Wird nach dem Speichern nicht mehr angezeigt…",
  "留空保留已保存密钥；填写新密钥后需重新测试和启用。":
    "Leer lassen, um den gespeicherten Schlüssel zu behalten; nach Eingabe eines neuen Schlüssels erneut testen und aktivieren.",
  "换了服务商后需要填写新的 API Key，已保存的密钥不会带到新地址。":
    "Geben Sie nach einem Anbieterwechsel einen neuen API-Schlüssel ein; der gespeicherte Schlüssel wird nicht an den neuen Endpunkt gesendet.",
  "保存在系统安全存储中，不随安装包分发。":
    "Wird im sicheren Systemspeicher abgelegt; nicht Teil der App-Verteilung.",
  "关闭思考链（向支持的厂商下发 thinking-off 参数）":
    "Denkkette deaktivieren (thinking-off an unterstützte Anbieter senden)",
  保存修改: "Speichern",
  保存配置: "Konfiguration speichern",
  "正在启用配置…": "Konfiguration wird aktiviert…",
  测试并完成配置: "Testen und abschließen",
  保存并测试启用: "Speichern, testen, aktivieren",
  取消编辑: "Bearbeitung abbrechen",
  "密钥保存在系统安全存储；测试仅发送测试内容。":
    "Der Schlüssel wird sicher gespeichert; Tests senden nur Testinhalte.",
  当前使用: "In Verwendung",
  未启用: "Nicht aktiviert",
  测试目标: "Testziel",
  "。仅发送测试内容，不发送历史对话。":
    ". Es werden nur Testinhalte gesendet, kein Unterhaltungsverlauf.",
  测试连接: "Verbindung testen",
  设为当前模型: "Als aktuelles Modell festlegen",
  删除: "Löschen",
  "粘贴 API Key": "API-Schlüssel einfügen",
  "密钥尚未保存成功，已填写内容保留，可重试。":
    "Schlüssel konnte nicht gespeichert werden. Ihre Eingabe bleibt erhalten; Sie können es erneut versuchen.",
  生成接收方: "Generierungsempfänger",
  向量接收方: "Embedding-Empfänger",
  本机: "Auf diesem Gerät",
  "。启用此配置后按上方选择处理历史及未来允许来源。":
    ". Nach der Aktivierung dieser Konfiguration bestimmt die Auswahl oben, welche bisherigen und künftigen autorisierten Quellen verarbeitet werden.",
  "保存 key": "Schlüssel speichern",
  "还没有 key？去申请": "Noch kein Schlüssel? Hier beantragen",
  删除这张卡: "Diese Karte entfernen",
  "密钥已配置，当前读取状态待确认。":
    "Schlüssel eingerichtet; Zugriffsstatus muss bestätigt werden.",
  "等待 key：其余都已经填好了，贴上 key 就能用。":
    "Warten auf Schlüssel. Alles andere ist bereit; fügen Sie Ihren Schlüssel ein, um fortzufahren.",
  "key 已保存，还没测过这个模型能不能用。":
    "Schlüssel gespeichert; dieses Modell wurde noch nicht getestet.",
  "Ollama（本机）": "Ollama (lokal)",
  "内置 bge-small-zh-v1.5": "Integriertes bge-small-zh-v1.5",
  通过: "Erfolgreich",
  "HY 托管模型尚不可用": "HY-gehostetes Modell nicht verfügbar",
  "缺少 API Key": "API-Schlüssel fehlt",
  服务不可达: "Dienst nicht erreichbar",
  "认证失败；更新 key 后再检测，会自动接着处理":
    "Authentifizierung fehlgeschlagen; aktualisieren Sie den Schlüssel und testen Sie erneut, um die Verarbeitung fortzusetzen.",
  请求频率受限: "Anfragelimit erreicht",
  "模型账户余额不足；充值或换 key 后再检测，会自动接着处理":
    "Guthaben des Modellkontos reicht nicht aus; laden Sie auf oder wechseln Sie den Schlüssel und testen Sie erneut, um fortzusetzen.",
  没有找到模型: "Modell nicht gefunden",
  "该网关不提供模型列表，已改由生成调用验证":
    "Das Gateway bietet keine Modellliste; stattdessen per Generierungsanfrage geprüft.",
  服务返回不符合协议: "Antwort des Dienstes entspricht nicht dem Protokoll",
  结构化输出未通过: "Test der strukturierten Ausgabe fehlgeschlagen",
  "需要填写 Embedding 模型": "Geben Sie ein Embedding-Modell ein",
  "Embedding 模型不可用": "Embedding-Modell nicht verfügbar",
  "请先核对上次模型测试结果，暂不修改或启用配置。":
    "Prüfen Sie zuerst den letzten Modelltest; die Konfiguration wird vorerst nicht geändert oder aktiviert.",
  "本地恢复记录已变化，仍保持锁定。请重新打开后核对。":
    "Die lokalen Wiederherstellungsdaten haben sich geändert. Die Konfiguration bleibt gesperrt; öffnen Sie sie erneut und prüfen Sie sie.",
  测试结果: "Testergebnisse",
  "暂不支持一键插入：它没有 MCP 通道，无法发放可撤销的记忆凭证。":
    "Ein-Klick-Verbindung nicht verfügbar: Diese App hat keinen MCP-Kanal für widerrufbare Erinnerungs-Anmeldedaten.",
  应用控制通道暂不可用: "Der App-Steuerungskanal ist nicht verfügbar",
  appFailureReason: " Grund: {reason}. ",
  appFailureReread:
    "Auch der Verbindungsstatus konnte nicht neu geladen werden; die Anzeige ist möglicherweise veraltet.",
  模型: "Modell",
  连接其他应用: "Andere Apps verbinden",
  帮助与诊断: "Hilfe und Diagnose",
  设置分组: "Einstellungsbereiche",
  记忆模型: "Erinnerungsmodell",
  当前模型: "Aktuelles Modell",
  "分别管理记忆整理与搜索所使用的模型。":
    "Verwalten Sie die Modelle für die Aufbereitung und Suche von Erinnerungen getrennt.",
  记忆整理: "Erinnerungen aufbereiten",
  "把会话理解并整理成可长期使用的记忆。":
    "Verarbeitet Unterhaltungen zu langfristig nutzbaren Erinnerungen.",
  记忆搜索: "Erinnerungen durchsuchen",
  "在需要时找到与你的问题最相关的记忆。":
    "Findet bei Bedarf die Erinnerungen, die für Ihre Frage am relevantesten sind.",
  "本地内置（推荐）": "Lokal integriert (empfohlen)",
  "Embedding · 配置记忆整理后可自定义":
    "Embedding · Nach Einrichtung der Erinnerungsaufbereitung anpassbar",
  "Embedding · 本机处理，无需额外配置":
    "Embedding · Lokale Verarbeitung, keine zusätzliche Einrichtung erforderlich",
  "正在读取模型…": "Modell wird geladen…",
  模型状态暂不可读: "Modellstatus nicht verfügbar",
  尚未配置模型: "Kein Modell eingerichtet",
  修改模型: "Modell bearbeiten",
  配置模型: "Modell einrichten",
  刷新模型状态: "Modellstatus aktualisieren",
  "本机 / 受管端点": "Lokaler / verwalteter Endpunkt",
  接口地址待核对: "Endpunkt muss geprüft werden",
  "模型状态未能读取，请重试。":
    "Modellstatus konnte nicht gelesen werden. Bitte wiederholen Sie den Vorgang.",
  "模型状态刷新未成功，仍显示上次确认的模型状态；请重试。":
    "Modellstatus konnte nicht aktualisiert werden. Der zuletzt bestätigte Status wird angezeigt; bitte wiederholen Sie den Vorgang.",
  "上次模型测试或创建还待核对，配置暂时不能改动。请先在下方模型配置区核对结果； 现有配置和原文不会被改动。":
    "Ein früherer Modelltest oder eine Erstellung muss geprüft werden. Prüfen Sie das Ergebnis unten, bevor Sie etwas ändern. Ihre Konfiguration und der Originaltext bleiben unverändert.",
  用模型沉淀记忆: "Erinnerungen mit Modell erstellen",
  "开启后，导入和同步到本机的会话会交给当前模型整理成记忆，会产生模型调用。 停止后不再调用模型、不再整理记忆，但会话仍会照常导入和保存；已排队的内容会留着，重新开启后继续。 模型配置始终保留，重新开启不需要重新填写。":
    "Wenn aktiviert, verarbeitet das aktuelle Modell importierte und synchronisierte Unterhaltungen zu Erinnerungen; dabei fallen Modellaufrufe an. Wenn deaktiviert, werden Unterhaltungen weiterhin importiert und gespeichert; Aufgaben in der Warteschlange werden nach dem erneuten Aktivieren fortgesetzt. Ihre Modellkonfiguration bleibt erhalten.",
  "启用未能确认，仍显示上次确认的状态；请重试。":
    "Aktivierung konnte nicht bestätigt werden. Der zuletzt bestätigte Status wird angezeigt; bitte wiederholen Sie den Vorgang.",
  "停止未能确认，模型可能仍在整理记忆，请重试。":
    "Stoppen konnte nicht bestätigt werden. Das Modell verarbeitet möglicherweise noch Erinnerungen; bitte wiederholen Sie den Vorgang.",
  "当前安装还不能读取这个开关，请确认后台服务已启动后重试。":
    "Diese Installation kann den Schalter nicht lesen. Prüfen Sie, ob der Hintergrunddienst läuft, und wiederholen Sie den Vorgang.",
  关于: "Über",
  "此应用还没有读取授权，请先授权它的对话目录。":
    "Diese App hat noch keine Leseberechtigung. Autorisieren Sie zuerst ihr Unterhaltungsverzeichnis.",
  "此应用的读取授权已撤销，请重新授权。":
    "Die Leseberechtigung dieser App wurde widerrufen. Autorisieren Sie sie erneut.",
  "此应用的对话目录已被重建，请重新授权。":
    "Das Unterhaltungsverzeichnis dieser App wurde neu erstellt. Autorisieren Sie es erneut.",
  "此应用的对话目录现在读不到，请确认应用已安装并产生过对话。":
    "Das Unterhaltungsverzeichnis dieser App kann nicht gelesen werden. Prüfen Sie, ob die App installiert ist und Unterhaltungen enthält.",
  "此应用的对话目录已变更，请按最新目录重新授权。":
    "Das Unterhaltungsverzeichnis dieser App hat sich geändert. Autorisieren Sie das neue Verzeichnis.",
  "此应用还没有读取授权，请先授权它的对话目录。完成后再开启持续同步。":
    "Diese App hat noch keinen Lesezugriff. Autorisieren Sie ihr Unterhaltungsverzeichnis und aktivieren Sie dann die fortlaufende Synchronisierung.",
  "此应用的读取授权已撤销，请重新授权。完成后再开启持续同步。":
    "Der Lesezugriff dieser App wurde widerrufen. Autorisieren Sie sie erneut und aktivieren Sie dann die fortlaufende Synchronisierung.",
  "此应用的对话目录已被重建，请重新授权。完成后再开启持续同步。":
    "Das Unterhaltungsverzeichnis dieser App wurde neu erstellt. Autorisieren Sie es erneut und aktivieren Sie dann die fortlaufende Synchronisierung.",
  "此应用的对话目录现在读不到，请确认应用已安装并产生过对话。完成后再开启持续同步。":
    "Das Unterhaltungsverzeichnis dieser App kann nicht gelesen werden. Stellen Sie sicher, dass die App installiert ist und Unterhaltungen enthält, und aktivieren Sie dann die fortlaufende Synchronisierung.",
  "此应用的对话目录已变更，请按最新目录重新授权。完成后再开启持续同步。":
    "Das Unterhaltungsverzeichnis dieser App hat sich geändert. Autorisieren Sie das neue Verzeichnis und aktivieren Sie dann die fortlaufende Synchronisierung.",
  "此应用的读取授权已失效，请重新授权后再开启持续同步。":
    "Der Lesezugriff dieser App ist abgelaufen. Autorisieren Sie sie erneut, bevor Sie die fortlaufende Synchronisierung aktivieren.",
  "此应用的授权或目录已变化，已按最新状态显示，请确认后再试。":
    "Berechtigungen oder Verzeichnis dieser App haben sich geändert. Prüfen Sie den aktualisierten Status und versuchen Sie es erneut.",
  模型配置表单: "Formular zur Modellkonfiguration",
  "部分运行状态暂时无法读取，当前显示最后读到的状态，请重试。":
    "Einige Statusinformationen sind nicht verfügbar. Der letzte bekannte Stand wird angezeigt; bitte wiederholen Sie den Vorgang.",
  "当前安装还不能设置登录后后台启动。":
    "Diese Installation kann den Start bei Anmeldung nicht einrichten.",
  "登录后后台启动设置未能确认，请重试。":
    "Starteinstellung bei Anmeldung konnte nicht bestätigt werden. Bitte wiederholen Sie den Vorgang.",
  "当前安装还不能退出界面，请从菜单栏退出。":
    "Diese Installation kann das Fenster nicht beenden. Verwenden Sie stattdessen die Menüleiste.",
  "退出界面未能确认，请重试。":
    "Beenden konnte nicht bestätigt werden. Bitte wiederholen Sie den Vorgang.",
  登录后启动后台服务: "Hintergrunddienst bei Anmeldung starten",
  "无需打开界面，持续同步、整理和提供记忆。":
    "Erinnerungen weiter synchronisieren, verarbeiten und bereitstellen, ohne die App zu öffnen.",
  导出日志: "Protokolle exportieren",
  "把本机运行日志打包成一个文件，发给开发排查。":
    "Packt alle lokalen Protokolle. Sie können Gesprächsinhalte oder lokale Pfade enthalten; nur mit vertrauenswürdigen Entwicklern teilen.",
  "确定退出 One Memory 界面吗？": "MemHub-Fenster beenden?",
  "后台服务会继续同步、整理和提供记忆。":
    "Der Hintergrunddienst synchronisiert, verarbeitet und liefert Erinnerungen weiter.",
  取消: "Abbrechen",
  退出界面: "Fenster beenden",
  应用详情: "App-Details",
  返回应用: "Zurück zu Apps",
  同步开关: "Synchronisierungsschalter",
  "开启后处理历史待办，并持续接收此应用的新会话。":
    "Wenn aktiviert, wird ausstehender Verlauf verarbeitet und neue Unterhaltungen dieser App werden laufend empfangen.",
  "正在读取已有对话，首次开启可能需要一两分钟，请不要重复点击。":
    "Vorhandene Unterhaltungen werden gelesen. Der erste Lauf kann ein bis zwei Minuten dauern; bitte nicht erneut klicken.",
  "将补充冻结期间的新对话；原有 pending Job 继续处理。":
    "Während der Pause eingegangene Unterhaltungen werden nachgeholt; ausstehende Jobs laufen weiter.",
  一键插入应用: "Ein-Klick-Verbindung",
  "安装记忆连接，并默认允许此应用使用已授权记忆。":
    "Installiert die Erinnerungsverbindung und erlaubt dieser App standardmäßig, autorisierte Erinnerungen zu nutzen.",
  允许使用记忆: "Erinnerungszugriff erlauben",
  连接状态: "Verbindungsstatus",
  已接通: "Verbunden",
  "已安装，但取不到记忆": "Installiert, aber Erinnerungen nicht abrufbar",
  未接通: "Nicht verbunden",
  状态待核对: "Status prüfen",
  检测中: "Wird geprüft",
  未开启关联: "Verbindung aus",
  "正在开启处理…": "Verarbeitung wird gestartet…",
  "正在冻结处理…": "Verarbeitung wird eingefroren…",
  处理中: "Wird verarbeitet",
  已冻结: "Eingefroren",
  "此应用的处理开关结果未能确认，其他应用不受影响。":
    "Verarbeitungsschalter für diese App konnte nicht bestätigt werden. Andere Apps sind nicht betroffen.",
  "暂时无法刷新来源状态，请重试；未更改同步或读取权限。":
    "Quellstatus konnte nicht aktualisiert werden. Wiederholen Sie den Vorgang; Synchronisierung und Leseberechtigungen wurden nicht geändert.",
  "暂时无法读取连接状态，当前状态待核对；请重试。":
    "Verbindungsstatus konnte nicht gelesen werden. Der aktuelle Status muss geprüft werden; bitte wiederholen Sie den Vorgang.",
  "已接通。已经打开的对话不会中途出现这个工具，请新开一轮。":
    "Verbunden. Das Tool erscheint nicht in bereits geöffneten Unterhaltungen; beginnen Sie eine neue.",
  "本次连接探测失败，当前状态待核对；未把上次读取当作本次成功。":
    "Diese Verbindungsprüfung ist fehlgeschlagen. Der aktuelle Status muss geprüft werden; das frühere Ergebnis wurde nicht als neuer Erfolg gewertet.",
  上次读取: "Zuletzt gelesen",
  appSettingsTitle: "App-Einstellungen für {name}",
  appMemoryScope:
    "{name} erlauben, Erinnerungen im autorisierten Bereich abzufragen.",
  appRestart:
    "Das Erinnerungstool ist nicht in der aktuellen Unterhaltung von {name}. Beenden Sie {name} vollständig und öffnen Sie es erneut; es erscheint dann in einer neuen Unterhaltung.",
  appConnectFailed:
    "Ein-Klick-Verbindung für diese App wurde nicht abgeschlossen. Andere Apps sind nicht betroffen. {reason}",
  appDisconnectFailed:
    "Die Verbindung konnte nicht entfernt und der Erinnerungszugriff nicht widerrufen werden. {name} kann weiterhin Erinnerungen nutzen; bitte wiederholen Sie den Vorgang. {reason}",
  appPermissionFailed:
    "Erinnerungszugriff für diese App konnte nicht bestätigt werden. Bitte wiederholen Sie den Vorgang. {reason}",
  appRevokedModified:
    "Der Erinnerungszugriff wurde widerrufen; {name} kann keine Erinnerungen mehr abfragen. Die Konfigurationsdatei wurde verändert, daher hat MemHub sie nicht automatisch gelöscht; entfernen Sie die MemHub-Verbindung manuell.",
  appRevokedRetained:
    "Der Erinnerungszugriff wurde widerrufen; {name} kann keine Erinnerungen mehr abfragen. Die Verbindungsdatei wurde nicht entfernt; prüfen Sie sie erneut.",
  appRevokedReview:
    "Der Erinnerungszugriff wurde widerrufen; {name} kann keine Erinnerungen mehr abfragen. Die vollständige Entfernung konnte nicht bestätigt werden; prüfen Sie diese Pfade manuell: {paths}.",
  appRemovalReview:
    "Vollständige Entfernung der Verbindung konnte nicht bestätigt werden. Prüfen Sie diese Pfade manuell: {paths}.",
  appProfilesLabel: "Profile von {name}",
  appProfileOn: "{profile}: verbunden",
  appProfileOff: "{profile}: nicht verbunden",
  appProfilesHint:
    "Der Schalter installiert für jedes erkannte Profil die Erinnerungsverbindung und erteilt den Zugriff; beim Ausschalten wird er für alle widerrufen. Solange er eingeschaltet ist, werden neue Profile automatisch verbunden.",
  appProfilesNone:
    "Es wurde kein Profil von {name} erkannt. Legen Sie ein Profil an und kehren Sie dann zu dieser Seite zurück.",
  appProfilesConnectFailed:
    "Diese Profile konnten nicht verbunden werden: {profiles}. Die übrigen Profile bleiben erhalten. {reason}",
  appProfilesDisconnectFailed:
    "Diese Profile konnten nicht getrennt werden und können weiterhin Erinnerungen nutzen: {profiles}. Bitte versuchen Sie es erneut. {reason}",
  appProfilesRevokedRetained:
    "Der Erinnerungszugriff dieser Profile wurde widerrufen, aber nicht alle Verbindungsdateien wurden entfernt; prüfen Sie sie: {profiles}.",
  appProfileNameUnsupported:
    "Profilnamen dürfen nur Kleinbuchstaben, Ziffern und Bindestriche enthalten, höchstens 32 Zeichen",
  "apps.autoHostProfilesFailed":
    "Diese Profile von {name} konnten nicht automatisch verbunden werden ({profiles}){reason}.",
  "Cursor 的连接凭证已更新。请完全退出并重新打开 Cursor，再开始新对话。":
    "Die Verbindungs-Anmeldedaten von Cursor haben sich geändert. Beenden Sie Cursor vollständig und öffnen Sie es erneut, bevor Sie eine neue Unterhaltung beginnen.",
  连接其他工具: "Andere Tools verbinden",
  "本机连接材料仅在桌面 App 中可用。":
    "Lokale Verbindungsdaten sind nur in der Desktop-App verfügbar.",
  "仅本机 MCP / 命令工具。纯网页聊天不支持直接连接本机。":
    "Nur für lokale MCP- oder Befehlszeilen-Tools. Browser-Chats können sich nicht direkt mit diesem Gerät verbinden.",
  "列表内工具请用上方一键安装，此处仅供未列出的工具。":
    "Nutzen Sie für aufgeführte Tools die Ein-Klick-Einrichtung oben. Dies ist für nicht aufgeführte Tools.",
  "正在读取本机连接配置…": "Lokale Verbindungsdaten werden geladen…",
  "未能读取连接配置。": "Verbindungsdaten konnten nicht geladen werden.",
  重试: "Wiederholen",
  连接材料格式: "Format der Verbindungsdaten",
  连接提示词: "Verbindungs-Prompt",
  "MCP 配置": "MCP-Konfiguration",
  命令: "Befehl",
  本机连接材料: "Lokale Verbindungsdaten",
  "包含本机路径，不含记忆和密钥。片段会运行本机 MemHub 连接程序；粘贴前请确认，MemHub 需保持运行。":
    "Enthält lokale Pfade, aber keine Erinnerungen oder Schlüssel. Das Snippet führt den lokalen MemHub-Connector aus; prüfen Sie es vor dem Einfügen und lassen Sie MemHub geöffnet.",
  "已复制，尚未建立连接。":
    "Kopiert; es wurde noch keine Verbindung hergestellt.",
  "复制失败，可在上方选中文本复制。":
    "Kopieren fehlgeschlagen. Markieren und kopieren Sie stattdessen den Text oben.",
  复制提示词: "Prompt kopieren",
  复制配置: "Konfiguration kopieren",
  复制命令: "Befehl kopieren",
  记忆库分页: "Seiten der Erinnerungsbibliothek",
  上一页: "Vorherige Seite",
  下一页: "Nächste Seite",
  "把熟悉的背景，带到这里": "Bringen Sie Ihren Kontext hierher",
  "你的记忆，从这里接上": "Führen Sie Ihre Erinnerungen zusammen",
  "这张卡还没测过。点一次「设为当前模型」会先自动测，如果还是不行，说明端点、key 或模型名有一处不对。":
    "Diese Konfiguration wurde noch nicht getestet. Beim Festlegen als aktuelles Modell wird zuerst ein Test ausgeführt; schlägt er fehl, prüfen Sie Endpunkt, Schlüssel und Modellnamen.",
  "这类模型接口暂时用不了，换一张卡。":
    "Dieser Modellendpunkt-Typ wird noch nicht unterstützt. Verwenden Sie eine andere Konfiguration.",
  "这张卡缺了向量化模型，补上才能启用。":
    "Diese Konfiguration braucht ein Embedding-Modell, bevor sie aktiviert werden kann.",
  "这张卡已经不在了，可能刚被删掉。刷新一下。":
    "Diese Konfiguration ist nicht mehr verfügbar. Sie wurde möglicherweise gelöscht; aktualisieren Sie die Liste.",
  "这张卡刚被改过。刷新一下再试，免得覆盖掉别处的改动。":
    "Diese Konfiguration wurde gerade geändert. Aktualisieren Sie vor dem erneuten Versuch, um keine andere Änderung zu überschreiben.",
  onboardingModelOperationFailed: "Vorgang fehlgeschlagen: {code}",
  onboardingModelSkipDestination: "Überspringen und zu Apps",
  首次设置进度: "Fortschritt der Ersteinrichtung",
  导入历史: "Verlauf importieren",
  连接应用: "Apps verbinden",
  沉淀记忆: "Erinnerungen erstellen",
  本地存储: "Lokal gespeichert",
  "对话与记忆保存在本机；使用云模型或已授权的助手时，相关内容会发送给对应服务。":
    "Unterhaltungen und Erinnerungen werden auf diesem Gerät gespeichert. Relevante Inhalte werden an Cloud-Modelle oder autorisierte Assistenten gesendet, wenn Sie diese nutzen.",
  "来源位置、身份或状态已变化，请重新选择并核对。尚未提交本次授权。":
    "Ort, Identität oder Status der Quelle haben sich geändert. Wählen und prüfen Sie sie erneut. Diese Autorisierung wurde nicht übermittelt.",
  "来源位置、身份或状态已变化，正在等待本次提交回执。不会自动扩大已提交范围。":
    "Ort, Identität oder Status der Quelle haben sich geändert. Warten auf Bestätigung dieser Übermittlung; der übermittelte Umfang wird nicht automatisch erweitert.",
  测试实例不接入应用: "Testinstanzen verbinden keine Apps",
  "请先把 MemHub 拖到“应用程序”并从那里重新打开，才能接入应用。":
    "Verschieben Sie MemHub nach „Programme“ und öffnen Sie es von dort erneut, bevor Sie Apps verbinden.",
  "部分应用未能完成接入，已成功的设置会保留，可进入应用后单独处理。":
    "Einige Apps konnten nicht verbunden werden. Erfolgreiche Einstellungen bleiben erhalten; bearbeiten Sie den Rest einzeln unter „Apps“.",
  onboardingConnectionPartial:
    "Verbundene Apps: {success}; unvollständig: {failed} ({names}). Sie können sie einzeln unter „Apps“ bearbeiten.",
  "首次设置的完成状态未能保存。已确认的导入回执不受影响；下次启动可能再次出现此页。":
    "Abschluss der Ersteinrichtung konnte nicht gespeichert werden. Bestätigte Importbelege sind nicht betroffen; diese Seite erscheint möglicherweise beim nächsten Mal erneut.",
  "首次设置的完成状态尚未确认。已确认的导入回执不受影响；下次启动可能再次出现此页。":
    "Abschluss der Ersteinrichtung ist unbestätigt. Bestätigte Importbelege sind nicht betroffen; diese Seite erscheint möglicherweise beim nächsten Mal erneut.",
  "位置检测未完成，尚未读取历史。可以重新检测，或先进入工作台。":
    "Ortserkennung ist unvollständig und der Verlauf wurde nicht gelesen. Prüfen Sie erneut oder fahren Sie zum Arbeitsbereich fort.",
  "暂时无法核对来源，尚未提交授权。选择已保留，可以重试。":
    "Quellen können gerade nicht geprüft werden, daher wurde die Autorisierung nicht übermittelt. Ihre Auswahl bleibt erhalten; Sie können es erneut versuchen.",
  "本次没有来源获准导入。请核对下方原因后重试，或到应用页查看状态。":
    "Für diesen Import wurden keine Quellen autorisiert. Prüfen Sie die Gründe unten und wiederholen Sie den Vorgang oder prüfen Sie den Status unter „Apps“.",
  "开始导入的结果尚未确认，选择已保留。可以重试，或进入应用页核对。":
    "Der Importstart ist unbestätigt. Ihre Auswahl bleibt erhalten; wiederholen Sie den Vorgang oder prüfen Sie ihn unter „Apps“.",
  未完成: "Unvollständig",
  核对中: "Wird geprüft",
  仍进入应用页: "Weiter zu Apps",
  仍进入工作台: "Weiter zum Arbeitsbereich",
  仍进入设置: "Weiter zu Einstellungen",
  重试保存并进入: "Erneut speichern und weiter",
  "稍后添加，先进入": "Später hinzufügen und weiter",
  "正在开始导入…": "Import wird gestartet…",
  开始导入: "Import starten",
  "不用从头介绍自己。从常用的 App 开始，把已有背景带过来。":
    "Sie müssen nicht von vorn beginnen. Übernehmen Sie Kontext aus Ihren häufig genutzten Apps.",
  "确认前只检查应用信息，不读取对话，也不安装连接。":
    "Vor der Bestätigung werden nur App-Informationen geprüft. Es werden keine Unterhaltungen gelesen und keine Verbindung installiert.",
  "本机 AI 应用": "KI-Apps auf diesem Gerät",
  重新检测位置: "Orte erneut prüfen",
  "检测中…": "Wird geprüft…",
  重新检测: "Erneut prüfen",
  候选应用: "Verfügbare Apps",
  "正在检测其他应用…": "Weitere Apps werden gesucht…",
  检测未完成: "Prüfung unvollständig",
  已全选: "Alle ausgewählt",
  全选: "Alle auswählen",
  onboardingImportHistory: "Verlauf aus {name} importieren",
  正在核对本机保存位置: "Lokaler Speicherort wird geprüft",
  已发现: "Gefunden",
  位置待核对: "Ort muss geprüft werden",
  "当前没有候选来源。可以稍后在应用页添加历史包。":
    "Keine Quellen gefunden. Sie können später unter „Apps“ ein Verlaufsarchiv hinzufügen.",
  "候选来源尚未加载。可以检测位置，或先进入工作台。":
    "Quellen wurden nicht geladen. Prüfen Sie die Orte oder fahren Sie zum Arbeitsbereich fort.",
  本次导入: "Dieser Import",
  "个 App 的历史对话": "App-Verläufe",
  "把零散的会话，": "Unterhaltungen",
  汇成完整的记忆: "zu Erinnerungen vereinen",
  "少一些重复交代，多一些理解。": "Weniger Wiederholung. Mehr Verständnis.",
  onboardingMoreApps: "Für mehr scrollen · {count} Apps verbleibend",
  "onboardingMoreApps.one": "Für mehr scrollen · {count} App verbleibend",
  onboardingMoreImportApps: "Horizontal scrollen · Noch {count} Apps",
  onboardingImportChecking:
    "Importergebnis wird geprüft; Ihre Auswahl bleibt erhalten",
  onboardingAllApps: "Alle {count} Apps angezeigt",
  默认位置: "Standardort",
  自定义位置: "Eigener Ort",
  尚未检测: "Nicht geprüft",
  未发现: "Nicht gefunden",
  无法访问: "Kein Zugriff",
  位置无效: "Ungültiger Ort",
  "需要更新 Core": "Core-Update erforderlich",
  "候选位置不存在，可能尚无历史或路径不同。":
    "Dieser Ort existiert nicht. Möglicherweise gibt es noch keinen Verlauf oder der Pfad ist anders.",
  "无法检查此位置，请核对目录访问权限后重试。":
    "Dieser Ort konnte nicht geprüft werden. Prüfen Sie den Verzeichniszugriff und wiederholen Sie den Vorgang.",
  "此位置未通过安全检查，不能读取。":
    "Dieser Ort hat die Sicherheitsprüfung nicht bestanden und kann nicht gelesen werden.",
  "当前 Core 未提供可确认的位置身份，需更新后再选择。":
    "Der aktuelle Core liefert keine prüfbare Ortsidentität. Aktualisieren Sie ihn vor der Auswahl.",
  暂不支持历史导入: "Verlaufsimport wird noch nicht unterstützt",
  "当前来源不可读取，请重新检测。":
    "Die aktuelle Quelle kann nicht gelesen werden. Prüfen Sie erneut.",
  "来源目录暂时不可读取，请核对位置和权限。":
    "Das Quellverzeichnis ist vorübergehend nicht lesbar. Prüfen Sie Ort und Berechtigungen.",
  "来源目录身份已变化，请返回重新检测。":
    "Die Identität des Quellverzeichnisses hat sich geändert. Gehen Sie zurück und prüfen Sie erneut.",
  "来源权限已变化，请返回重新确认。":
    "Quellberechtigungen haben sich geändert. Gehen Sie zurück und bestätigen Sie erneut.",
  "此来源尚未获得读取授权。": "Diese Quelle hat noch keinen Lesezugriff.",
  "自定义来源位置需要重新确认。":
    "Der eigene Quellort muss erneut bestätigt werden.",
  "当前 Core 未启用历史导入。":
    "Verlaufsimport ist im aktuellen Core nicht aktiviert.",
  onboardingImportRefused:
    "Import nicht autorisiert ({reason}). Prüfen Sie die Quelle und wiederholen Sie den Vorgang.",
  确认自定义位置: "Eigenen Ort bestätigen",
  关闭位置确认: "Ortsbestätigung schließen",
  "此位置来自当前配置，并非应用默认目录。确认位置不会开始读取历史。":
    "Dieser Ort stammt aus Ihrer aktuellen Konfiguration, nicht aus dem Standardverzeichnis der App. Die Bestätigung startet nicht das Lesen des Verlaufs.",
  onboardingConfirmHistory:
    "Bestätigen, dass dies der Verlaufsort von {name} ist",
  确认此位置: "Diesen Ort bestätigen",
  "对话正在汇入，记忆从这里开始":
    "Unterhaltungen treffen ein. Hier beginnen Erinnerungen",
  "导入进行中 · 顺手连接应用": "Import läuft · Apps verbinden",
  "对话已保存，记忆尚待生成。":
    "Unterhaltungen gespeichert; Erinnerungen werden noch erstellt.",
  "导入在后台继续，无需等待。":
    "Der Import läuft im Hintergrund weiter. Sie müssen nicht warten.",
  "正在重新核对来源；已保存数量仍按任务回执显示。":
    "Quellen werden erneut geprüft; gespeicherte Anzahlen entsprechen weiter den Job-Belegen.",
  "本次历史导入已暂停，已保存的对话还在。":
    "Dieser Verlaufsimport ist angehalten. Gespeicherte Unterhaltungen bleiben verfügbar.",
  "导入等待处理，已保存的对话还在。":
    "Import wartet auf Verarbeitung. Gespeicherte Unterhaltungen bleiben verfügbar.",
  停止后续操作: "Weitere Aktionen stoppen",
  暂停导入: "Import anhalten",
  继续导入: "Import fortsetzen",
  "配置模型，沉淀记忆": "Modell einrichten, um Erinnerungen zu erstellen",
  导入与原文: "Import und Originaltext",
  "本次导入已结束。已保存的对话无需重复导入。":
    "Dieser Import ist abgeschlossen. Gespeicherte Unterhaltungen müssen nicht erneut importiert werden.",
  "对话先保存在本机。你可以同时设置后续连接。":
    "Unterhaltungen werden zuerst lokal gespeichert. Sie können währenddessen Verbindungen einrichten.",
  "导入不会自动开放应用读取权限。配置模型后，才会开始生成记忆。":
    "Der Import gewährt Apps nicht automatisch Lesezugriff. Erinnerungen werden erst erstellt, wenn Sie ein Modell eingerichtet haben.",
  onboardingHistoryWindow:
    "Es wurde eine große Menge an Unterhaltungsdaten gefunden. Um die Nutzung zu begrenzen und App-Zeitachsen abzugleichen, umfasst dieser Import für alle Apps nur die letzten 3 Tage.",
  导入说明: "Importinformationen",
  正在导入你的会话: "Ihre Unterhaltungen werden importiert",
  "后台将持续为你导入，可以放心进入下一步":
    "Der Import läuft im Hintergrund weiter. Sie können fortfahren.",
  未开始的导入: "Nicht gestartete Importe",
  "未能开始导入，其他已接受任务不受影响。":
    "Import konnte nicht gestartet werden. Andere angenommene Jobs sind nicht betroffen.",
  查看未导入的应用: "Nicht importierte Apps anzeigen",
  让记忆继续生长: "Lassen Sie Ihre Erinnerungen wachsen",
  连接你的应用: "Ihre Apps verbinden",
  "适用于本次选择且支持的应用。":
    "Für ausgewählte Apps, die diese Verbindung unterstützen.",
  一键接入应用: "Apps in einem Schritt verbinden",
  "安装记忆连接，让所选应用用上 MemHub 中的相关记忆。":
    "Installieren Sie Erinnerungsverbindungen, damit ausgewählte Apps relevante Erinnerungen in MemHub nutzen können.",
  "当前使用独立测试数据目录，不会修改本机应用的连接配置。":
    "Diese Instanz nutzt ein isoliertes Testdatenverzeichnis und ändert keine lokalen App-Verbindungen.",
  "点击底部按钮后，后台分别接入各应用；某个应用失败不影响其他应用。":
    "Nach einem Klick auf die Schaltfläche unten werden Apps einzeln im Hintergrund verbunden; ein Fehler betrifft die anderen nicht.",
  "暂时无法连接应用，当前不会安装连接。":
    "Apps können gerade nicht verbunden werden. Es wird keine Verbindung installiert.",
  onboardingBatchStopped:
    "Weitere Aktionen gestoppt; prüfen Sie die Belege bereits gesendeter Aktionen. ",
  onboardingBatchResult: "{confirmed} bestätigt, {failed} unbestätigt.",
  "已确认项保留，请核对未确认任务后重试。":
    " Bestätigte Aktionen bleiben erhalten. Prüfen Sie unbestätigte Jobs, bevor Sie es erneut versuchen.",
  本次应用导入进度: "Importfortschritt der ausgewählten Apps",
  正在导入: "Wird importiert",
  已暂停: "Angehalten",
  已中断: "Unterbrochen",
  导入遇到问题: "Problem beim Import",
  导入完成: "Import abgeschlossen",
  已取消: "Abgebrochen",
  已保存: "Gespeichert",
  onboardingAppProgress: "Importfortschritt von {name}",
  onboardingSavedMessages: "Gespeicherte Nachrichten: {count}",
  onboardingScannedOutsideRange:
    "Gescannte Einträge: {count}; keine Unterhaltungen im Zeitraum",
  onboardingScannedNothingToSave:
    "Gescannte Einträge: {count}; keine Unterhaltungen zum Speichern",
  onboardingScannedFilteringRecent:
    "Gescannte Einträge: {count}; die letzten 3 Tage werden gefiltert",
  onboardingScannedFiltering: "Gescannte Einträge: {count}; wird gefiltert",
  onboardingScannedNotSaved:
    "Gescannte Einträge: {count}; noch nicht gespeichert",
  没有可保存的历史对话: "Keine Verlaufsunterhaltungen zum Speichern",
  读取授权不可用: "Lesezugriff nicht verfügbar",
  onboardingReadErrors: " · Lesefehler: {count}",
  "配置详情，按需要调整": "Konfigurationsdetails, falls nötig",
  "连接未通过，配置已保留":
    "Verbindung fehlgeschlagen; Konfiguration bleibt erhalten",
  "选好模型，让记忆开始沉淀":
    "Wählen Sie ein Modell, um Erinnerungen zu erstellen",
  "连接设置已保存\n成功项保留，不重复执行。":
    "Verbindungseinstellungen gespeichert.\nErfolgreiche Schritte bleiben erhalten und werden nicht wiederholt.",
  "已选择：持续同步 · 一键接入":
    "Ausgewählt: fortlaufende Synchronisierung · Verbindung in einem Schritt",
  "沉淀记忆 · 使用你选择的模型":
    "Erinnerungen erstellen · Mit Ihrem gewählten Modell",
  "当前无法启用模型，已填写内容保留。":
    "Das Modell kann gerade nicht aktiviert werden. Ihre Eingaben bleiben erhalten.",
  "模型连接测试通过，可以启用并开始沉淀记忆。":
    "Modellverbindung erfolgreich. Sie können es aktivieren, um Erinnerungen zu erstellen.",
  "先测试模型连通性；测试通过后再启用。":
    "Testen Sie zuerst die Modellverbindung; aktivieren Sie es nach bestandenem Test.",
  "跳过，先进入应用": "Überspringen und zu Apps",
  "正在测试模型…": "Modell wird getestet…",
  测试模型连通性: "Modellverbindung testen",
  "正在启用…": "Wird aktiviert…",
  确认启用: "Bestätigen und aktivieren",
  核对提交状态: "Übermittlungsstatus prüfen",
  模型配置: "Modellkonfiguration",
  模型参数: "Modellparameter",
  "选择服务商，填入 API Key。核对已有配置后再继续。":
    "Wählen Sie einen Anbieter und geben Sie einen API-Schlüssel ein. Prüfen Sie die vorhandene Konfiguration, bevor Sie fortfahren.",
  模型服务商: "Modellanbieter",
  选择服务商: "Anbieter wählen",
  "已安全保存，留空保留": "Sicher gespeichert; leer lassen zum Behalten",
  "输入服务商提供的 API Key": "API-Schlüssel Ihres Anbieters eingeben",
  "连接未通过，请检查 API Key、模型与接口地址后重试。配置已保留。":
    "Verbindung fehlgeschlagen. Prüfen Sie API-Schlüssel, Modell und Endpunkt und wiederholen Sie den Vorgang. Ihre Konfiguration bleibt erhalten.",
  "需要允许 MemHub 访问钥匙串。系统询问时请选择“始终允许”。":
    "Erlauben Sie MemHub den Zugriff auf den Schlüsselbund. Wählen Sie bei der Abfrage „Immer erlauben“.",
  "留空保留已保存密钥；填写新密钥后需重新测试。":
    "Leer lassen, um den gespeicherten Schlüssel zu behalten; nach Eingabe eines neuen Schlüssels erneut testen.",
  "密钥保存在系统安全存储中，不写入对话或日志。":
    "Der Schlüssel liegt im sicheren Systemspeicher, nicht in Unterhaltungen oder Protokollen.",
  配置详情与高级设置: "Konfigurationsdetails und erweiterte Einstellungen",
  服务商待选择: "Anbieter nicht ausgewählt",
  模型待填写: "Modell nicht eingegeben",
  本地向量模型: "Lokales Embedding-Modell",
  向量模型待填写: "Embedding-Modell nicht eingegeben",
  思考已关闭: "Denkmodus deaktiviert",
  思考未关闭: "Denkmodus nicht deaktiviert",
  "预设已填好，只有自定义服务或特殊需求才需要修改。":
    "Vorlagen sind ausgefüllt. Ändern Sie sie nur für eigene Dienste oder besondere Anforderungen.",
  返回服务商选择: "Zurück zur Anbieterauswahl",
  模型名称: "Modellname",
  "Anthropic（暂不支持）": "Anthropic (noch nicht unterstützt)",
  "HY 托管（当前不可用）": "HY-verwaltet (derzeit nicht verfügbar)",
  向量模型: "Embedding-Modell",
  "本地 ONNX · 内置": "Lokales ONNX · Integriert",
  "超时（秒）": "Timeout (Sekunden)",
  最大并发: "Maximale Parallelität",
  上下文窗口: "Kontextfenster",
  "自动 / 可选": "Automatisch / optional",
  关闭思考模式: "Denkmodus deaktivieren",
  "自定义向量配置可展开；远程向量服务须遵守凭据边界。":
    "Eigene Embedding-Einstellungen lassen sich aufklappen. Entfernte Embedding-Dienste müssen Anmeldedaten-Grenzen einhalten.",
  "切换服务商或模型后，需要重新测试并启用。":
    "Testen und aktivieren Sie erneut, nachdem Sie Anbieter oder Modell gewechselt haben.",
  向量模型名称: "Name des Embedding-Modells",
  向量协议: "Embedding-Protokoll",
  "同源 OpenAI-compatible（共用 Key）":
    "Same-Origin OpenAI-kompatibel (gemeinsamer Schlüssel)",
  "向量 Base URL": "Embedding-Base-URL",
  "远程向量地址须与生成服务同源；本机地址仍需校验。":
    "Entfernte Embedding-Endpunkte müssen denselben Ursprung wie der Generierungsdienst haben; lokale Endpunkte werden weiterhin geprüft.",
  "放弃旧记录，重新配置": "Alte Daten verwerfen und neu einrichten",
  模型提交状态: "Übermittlungsstatus des Modells",
  "正在测试连接…": "Verbindung wird getestet…",
  "正在提交保存与启用，请等待回执…":
    "Wird gespeichert und aktiviert; warten auf Bestätigung…",
  "操作结果未确认，已填写内容保留，请核对后重试。":
    "Ergebnis unbestätigt. Ihre Eingaben bleiben erhalten; prüfen Sie sie vor dem erneuten Versuch.",
  本次沉淀: "Diese Erinnerungseinrichtung",
  让记忆从这里开始: "Beginnen Sie hier mit Erinnerungen",
  对话来源: "Unterhaltungsquellen",
  尚无已选来源: "Keine Quellen ausgewählt",
  onboardingSourcesSummary: "{names} – insgesamt {count} Apps",
  onboardingImportedConversations:
    "Importierte Unterhaltungen: {count}; Quellenschalter steuern weiterhin, was später auf dieses Gerät synchronisiert wird.",
  "处理服务：": "Verarbeitungsdienst:",
  待选择: "Nicht ausgewählt",
  "相关对话会发送给所选服务商。":
    "Relevante Unterhaltungen werden an den gewählten Anbieter gesendet.",
  "测试连接只发送测试内容。启用后会使用此模型整理已导入和之后同步的会话。":
    "Verbindungstests senden nur Testinhalte. Nach der Aktivierung verarbeitet dieses Modell importierte und künftig synchronisierte Unterhaltungen.",
  "当前不能在此启用模型。":
    "Das Modell kann hier gerade nicht aktiviert werden.",
  "配置已修改，需要重新测试。": "Konfiguration geändert. Testen Sie erneut.",
  onboardingIntegerRange: "Geben Sie eine ganze Zahl von {min} bis {max} ein.",
  "请填写配置名称。": "Geben Sie einen Konfigurationsnamen ein.",
  "请填写实际可用的模型 ID。": "Geben Sie eine verfügbare Modell-ID ein.",
  "请填写有效的模型接口地址。":
    "Geben Sie eine gültige Endpunkt-URL für das Modell ein.",
  "请填写 API Key。": "Geben Sie einen API-Schlüssel ein.",
  "请填写向量模型 ID。": "Geben Sie eine Embedding-Modell-ID ein.",
  "配置结果尚未确认，请在模型设置中核对后重试。":
    "Das Konfigurationsergebnis ist unbestätigt. Prüfen Sie es in den Modelleinstellungen, bevor Sie es erneut versuchen.",
  "模型连接测试通过，可以启用。":
    "Modellverbindungstest erfolgreich. Sie können es aktivieren.",
  "连接未通过，请核对模型、密钥和接收方。":
    "Verbindung fehlgeschlagen. Prüfen Sie Modell, Schlüssel und Empfänger.",
  "配置或测试结果暂未确认，请核对后重试。":
    "Konfiguration oder Testergebnis konnte nicht bestätigt werden. Prüfen Sie sie vor dem erneuten Versuch.",
  "索引更新没有完成，仍在使用原来的搜索模型。请检查连接后重试。":
    "Die Indexaktualisierung wurde nicht abgeschlossen. Das bisherige Suchmodell wird weiterhin verwendet. Prüfen Sie die Verbindung und versuchen Sie es erneut.",
  "上次模型测试结果仍待核对，请稍后重试。":
    "Das Ergebnis des letzten Modelltests muss noch geprüft werden. Versuchen Sie es später erneut.",
  "模型写入暂时关闭。请等待索引维护完成后重试。":
    "Modellschreibvorgänge sind vorübergehend pausiert. Warten Sie, bis die Indexwartung abgeschlossen ist, und versuchen Sie es erneut.",
  "模型未能确认启用，请在设置中核对。":
    "Die Aktivierung des Modells konnte nicht bestätigt werden. Prüfen Sie dies in den Einstellungen.",
  "已保存的模型配置暂时无法读取，不会重复创建。请重试刷新。":
    "Die gespeicherte Modellkonfiguration kann vorübergehend nicht gelesen werden. Es wird kein Duplikat erstellt. Aktualisieren Sie erneut.",
  所选连接已启用: "Ausgewählte App-Verbindungen sind aktiviert",
  本次未选择应用连接: "Diesmal keine App-Verbindung ausgewählt",
  尚未填写接收方: "Kein Empfänger eingegeben",
  本机内置向量模型: "Integriertes lokales Embedding-Modell",
  "操作未确认，草稿已保留。":
    "Aktion konnte nicht bestätigt werden. Ihr Entwurf bleibt erhalten.",
  "操作已完成，但配置列表暂未刷新。已保留操作回执，请刷新核对，不要重复保存。":
    "Der Vorgang wurde abgeschlossen, aber die Konfigurationsliste wurde noch nicht aktualisiert. Der Beleg bleibt erhalten; aktualisieren Sie zur Prüfung und speichern Sie nicht erneut.",
  "暂时无法刷新模型配置，已确认的配置仍保留。请重试刷新。":
    "Modellkonfigurationen konnten nicht aktualisiert werden. Bestätigte Konfigurationen bleiben erhalten; versuchen Sie es erneut.",
  "未找到上次创建记录。确认放弃旧记录后可重新配置；系统不会自动重试。":
    "Der vorherige Erstellungseintrag wurde nicht gefunden. Verwerfen Sie ihn, um neu einzurichten; es erfolgt kein automatischer Wiederholungsversuch.",
  "创建结果仍待核对，不会重复创建。请稍后再次核对。":
    "Das Erstellungsergebnis muss noch geprüft werden. Es wird kein Duplikat erstellt; prüfen Sie später erneut.",
  "已发现模型配置，旧创建记录仍保留。请先核对已有配置。":
    "Eine Modellkonfiguration wurde gefunden. Der alte Erstellungseintrag bleibt erhalten; prüfen Sie zuerst die vorhandene Konfiguration.",
  "旧创建记录尚未安全解除，配置仍保持锁定。请重新核对。":
    "Der alte Erstellungseintrag wurde noch nicht sicher entfernt. Die Konfiguration bleibt gesperrt; prüfen Sie sie erneut.",
  "配置未保存，请检查模型地址和参数后重试；已填写内容保留。":
    "Konfiguration wurde nicht gespeichert. Prüfen Sie Modellendpunkt und Einstellungen, bevor Sie es erneut versuchen; Ihre Eingaben bleiben erhalten.",
  "创建结果尚未确认，请核对已保存结果，不要重复提交。":
    "Das Erstellungsergebnis ist unbestätigt. Prüfen Sie die gespeicherten Ergebnisse und übermitteln Sie nicht erneut.",
  "模型配置已变化，旧测试结果不会用于启用，请重新核对配置。":
    "Die Modellkonfiguration hat sich geändert. Das alte Testergebnis wird nicht zur Aktivierung verwendet; prüfen Sie die Konfiguration erneut.",
  "暂时无法核对测试服务，本次未发送测试，请恢复连接后重试。":
    "Testdienst konnte nicht geprüft werden. Es wurde kein Test gesendet; stellen Sie die Verbindung wieder her und versuchen Sie es erneut.",
  "另一个模型的测试结果尚未确认，请先在该模型上核对测试结果。":
    "Das Testergebnis eines anderen Modells ist unbestätigt. Prüfen Sie zuerst dieses Ergebnis.",
  "测试结果尚未确认。再次点击测试连接将核对原操作，不会重复发送测试。":
    "Das Testergebnis ist unbestätigt. Ein erneuter Klick auf „Verbindung testen“ prüft den ursprünglichen Vorgang, ohne einen weiteren Test zu senden.",
  "请先点击测试连接，通过后再设为当前模型。":
    "Testen Sie zuerst die Verbindung; legen Sie das Modell erst nach bestandenem Test als aktuell fest.",
  "这是当前正在使用的模型，请先切换到别的模型再删除。":
    "Dieses Modell wird gerade verwendet. Wechseln Sie vor dem Löschen zu einem anderen Modell.",
  "模型配置操作未成功，请核对输入后重试。":
    "Der Vorgang an der Modellkonfiguration ist fehlgeschlagen. Prüfen Sie Ihre Eingaben und versuchen Sie es erneut.",
  "它没有 MCP 通道，无法发放可撤销的记忆凭证":
    "Diese App hat keinen MCP-Kanal für widerrufbare Erinnerungs-Anmeldedaten",
  当前安装不支持一键插入:
    "Ein-Klick-Verbindung wird in dieser Installation nicht unterstützt",
  桌面端的连接组件不可用:
    "Die Desktop-Verbindungskomponente ist nicht verfügbar",
  安装后没有拿到安装回执: "Es wurde kein Installationsbeleg zurückgegeben",
  记忆使用权限没有获批: "Erinnerungszugriff wurde nicht genehmigt",
  配置文件被改过: "Die Konfigurationsdatei wurde bearbeitet",
  "有无法确认的残留，需要人工核对":
    "Einige Reste müssen manuell geprüft werden",
  权限存储暂不可用: "Berechtigungsspeicher ist nicht verfügbar",
  "请先把 MemHub 拖到“应用程序”文件夹，再接入应用":
    "Verschieben Sie MemHub nach „Programme“, bevor Sie Apps verbinden",
  本机运行时暂不可用: "Die lokale Laufzeit ist nicht verfügbar",
  桌面端读取接入状态超时:
    "Zeitüberschreitung beim Lesen des Desktop-Verbindungsstatus",
  "apps.apps": "Apps",
  "apps.allApps": "Alle Apps",
  "apps.home": "Apps-Startseite",
  "apps.back": "Zurück zur Startseite",
  "apps.overview": "Erinnerungsübersicht",
  "apps.memories": "Gespeicherte Erinnerungen",
  "apps.rawConversations": "Originalunterhaltungen",
  "apps.rawSaving": "Wird gespeichert",
  "apps.rawSavingCompact": "Wird gespeichert",
  "apps.rawSavingStatus": "Speicherstatus",
  "apps.rawSavingDetail":
    "Originalunterhaltungen werden ohne generatives Modell gespeichert",
  "apps.rawNoExcerpt": "Kein anzeigbarer Benutzertext in dieser Unterhaltung",
  "apps.rawConversation": "Originalunterhaltung",
  "apps.rawSaved": "Originalunterhaltung gespeichert",
  "apps.rawEmpty": "Noch keine Originalunterhaltungen gespeichert",
  "apps.rawCheckedEmpty":
    "Geprüft: keine anzeigbaren Aktualisierungen der Originalunterhaltungen",
  "apps.memoryCheckedEmpty":
    "Geprüft: keine anzeigbaren Erinnerungsaktualisierungen",
  "apps.recentEmptyCompact": "Keine Updates",
  "apps.rawWaiting": "Gespeicherte Originalunterhaltungen erscheinen hier",
  "apps.checkedRecent": "Aktueller Inhalt geprüft",
  "apps.currentMode": "Aktueller Modus",
  "apps.mode.lite": "Basisspeicher (lite)",
  "apps.mode.pro": "Intelligenter Speicher (pro)",
  "apps.mode.ultra": "Tiefenspeicher (ultra)",
  "apps.readingCount": "Lokale Erinnerungen werden geprüft…",
  "apps.rawReadingCount": "Originalunterhaltungen werden geprüft…",
  "apps.startingRetry":
    "Der Hintergrunddienst startet; die Anzeige wird automatisch aktualisiert",
  "apps.countWillUpdate":
    "Die Anzahl der Erinnerungen wird automatisch aktualisiert",
  "apps.rawCountWillUpdate":
    "Die Anzahl der Originalunterhaltungen wird automatisch aktualisiert",
  "apps.showingPrevious":
    "Vorherige Daten werden angezeigt; die Anzeige wird automatisch aktualisiert",
  "apps.unreadable": "Nicht verfügbar",
  "apps.unit": "Stück",
  "apps.scope": "Wird im Rahmen der App-Berechtigungen genutzt",
  "apps.processing": "Verarbeitungsstatus",
  "apps.running": "Wird verarbeitet",
  "apps.paused": "Angehalten",
  "apps.stoppedNew": "Es werden keine neuen Erinnerungen erstellt",
  "apps.checkingProcessing": "Verarbeitungsstatus wird geprüft",
  "apps.checkingStatus": "Wird geprüft",
  "apps.unchecked": "Prüfung nötig",
  "apps.organizingNew": "Neue Unterhaltungen werden verarbeitet",
  "apps.recentUpdate": "Letzte Aktualisierung",
  "apps.checkingRecent": "Wird geprüft",
  "apps.recentWillUpdate": "Die letzte Aktualisierung erscheint automatisch",
  "apps.notGenerated": "Noch nicht erstellt",
  "apps.saved": "Erinnerung gespeichert",
  "apps.separate": "Verlauf und Erinnerungen werden getrennt verarbeitet",
  "apps.recentCall": "Letzter Aufruf",
  "apps.activityUnavailable": "Aktivität nicht verfügbar",
  "apps.firstBatch": "Erste Erinnerungen",
  "apps.callsUnavailable": "Aufrufverlauf konnte nicht gelesen werden",
  "apps.memoryUnavailable": "Die Erinnerungsübersicht ist noch nicht bereit",
  "apps.importing":
    "Unterhaltungen werden importiert, bevor Erinnerungen erstellt werden",
  "apps.firstRunning": "Die ersten Erinnerungen werden erstellt",
  "apps.firstQueued": "Die ersten Erinnerungen werden vorbereitet",
  "apps.readingActivity": "Erinnerungsübersicht wird vorbereitet",
  "apps.emptyMemories": "Noch keine Erinnerungen erstellt",
  "apps.callReadFailure":
    "Gespeicherte Erinnerungen bleiben einsehbar. Ein neuer Versuch löst keinen neuen Aufruf aus.",
  "apps.memoryReadFailure":
    "Die Anzeige wird aktualisiert, sobald der Dienst wieder läuft. Sie können es auch jetzt erneut versuchen.",
  "apps.savingConversations":
    "Unterhaltungen werden weiter im Hintergrund gespeichert.",
  "apps.waitingMemories":
    "Der Verlauf ist gespeichert. Neue Erinnerungen erscheinen hier.",
  "apps.reread": "Wiederholen",
  "apps.viewCalls": "Verlauf anzeigen",
  "apps.viewMemory": "Erinnerungen anzeigen",
  "apps.backgroundDown":
    "Der Hintergrunddienst läuft nicht; Apps können derzeit keine Erinnerungen abfragen",
  "apps.restart": "Neu starten",
  "apps.runtime": "Laufzeitstatus",
  "apps.backgroundRunning": "Läuft im Hintergrund",
  "apps.connectionUnchecked": "Verbindung muss geprüft werden",
  "apps.disconnected": "Dienst getrennt",
  "apps.authorizedOnly": "Verarbeitet nur autorisierte Inhalte",
  "apps.sync": "Unterhaltungssynchronisierung",
  "apps.organizing": "Erinnerungsverarbeitung",
  "apps.access": "App-Zugriff",
  "apps.checkingAccess": "Prüfung der App-Verbindung",
  "apps.checking": "Wird geprüft…",
  "apps.recheck": "Erneut prüfen",
  "apps.accessState": "Status der App-Verbindung",
  "apps.staleAccess":
    "Status der App-Verbindung konnte nicht gelesen werden; der letzte bekannte Stand wird angezeigt.",
  "apps.reconnect": "Neu verbinden",
  "apps.myApps": "Meine Apps",
  "apps.list": "App-Liste",
  "apps.syncEnabled": "Fortlaufende Synchronisierung aktiviert",
  "apps.recallAllowed": "Darf autorisierte Erinnerungen anfordern",
  "apps.historyAllowed": "Verlaufszugriff autorisiert",
  "apps.connectionPending": "Verbindung unvollständig",
  "apps.details": "Details anzeigen",
  "apps.listUnavailable": "App-Liste nicht verfügbar",
  "apps.emptyApps": "Noch keine Apps anzuzeigen",
  "apps.checkingNow": "Wird geprüft",
  "apps.checkingState": "Status wird geprüft",
  "apps.notEnabled": "Nicht aktiviert",
  "apps.enableFailed": "Aktivierung fehlgeschlagen",
  "apps.unknownTime": "Zeit unbekannt",
  "apps.today": "Heute",
  "apps.newMemory": "Neue Erinnerung",
  "apps.updatedMemory": "Aktualisierte Erinnerung",
  "apps.unlinked": "Noch nicht verbunden",
  "apps.lastRead": "Zuletzt gelesen: ",
  "apps.recentSync": "Zuletzt synchronisiert {time}",
  "apps.connectedCount": "{count} Apps verbunden",
  "apps.connectedCount.one": "{count} App verbunden",
  "apps.pendingCount": "{count} Apps müssen geprüft werden",
  "apps.pendingCount.one": "{count} App muss geprüft werden",
  "apps.mixedCount": "{count} Apps verbunden · Prüfung ausstehend: {pending}",
  "apps.mixedCount.one":
    "{count} App verbunden · Prüfung ausstehend: {pending}",
  "apps.unknownAccess":
    "Status der App-Verbindung nicht verfügbar; die Anzahl verbundener Apps kann nicht bestätigt werden.",
  "apps.unknownPermissions":
    "Erinnerungsberechtigungen von {names} konnten nicht gelesen werden; der Verbindungsstatus ist unbestätigt.",
  "apps.enabledCount": "{count} Apps aktiviert",
  "apps.enabledCount.one": "{count} App aktiviert",
  "apps.appCount": "{count} Apps",
  "apps.appCount.one": "{count} App",
  "apps.viewAll": "Alle {count} Apps anzeigen",
  "apps.viewAll.one": "{count} App anzeigen",
  "apps.checkedAt": "Zuletzt geprüft: {time}",
  "apps.revision": "Version {count}",
  "apps.conversationTime": "Unterhaltungszeit",
  "apps.importTime": "Importzeit",
  "apps.writeTime": "Schreibzeit",
  "apps.updateTime": "Aktualisierungszeit",
  "apps.incompleteCalls":
    " · Einige Aufrufe wurden nicht aufgezeichnet; die Liste ist möglicherweise unvollständig",
  "apps.timeoutWarning":
    "{host} konnte zuletzt keine Erinnerungen abfragen. Der Hintergrunddienst ist vielleicht ausgelastet und sollte sich automatisch erholen.",
  "apps.autoHostFailed":
    "{name} konnte nicht automatisch verbunden werden{reason}.",
  "apps.failureReason": ": {reason}",
  "apps.autoSyncFailed":
    "Fortlaufende Synchronisierung konnte für {name} nicht automatisch aktiviert werden. {reason}",
  "apps.noAutoRetry": "Es erfolgt kein automatischer neuer Versuch.",
  "apps.cardUnavailable":
    "{label} ist vorübergehend nicht verfügbar. Andere Funktionen sind nicht betroffen; die Anzeige sollte sich automatisch erholen.",
  "apps.navMemories": "Erinnerungen",
  "apps.navigation": "Hauptnavigation",
  "apps.skip": "Zum Hauptinhalt springen",
  "apps.local": "Lokal gespeichert",
  "apps.localHint":
    "Erinnerungen werden auf diesem Gerät gespeichert. Relevante Inhalte werden an den Dienst gesendet, wenn Sie ein Cloud-Modell oder einen autorisierten Assistenten nutzen.",
  "memory:记忆抽取": "Erinnerungsextraktion",
  "memory:关系归纳": "Beziehungssynthese",
  "memory:深度整理": "Tiefenverarbeitung",
  "memory:跨域整理": "Bereichsübergreifende Verarbeitung",
  "memory:当前模型已变化": "Aktuelles Modell geändert",
  "memory:本地会话内容已变化": "Lokaler Unterhaltungsinhalt geändert",
  "memory:这段对话含过长原文，暂未整理；原文已保留":
    "Diese Unterhaltung enthält zu langen Text für die Verarbeitung; das Original bleibt erhalten",
  "memory:记忆库分页": "Seiten der Erinnerungsbibliothek",
  "memory:上一页": "Vorherige Seite",
  "memory:下一页": "Nächste Seite",
  "memory:等待可用生成模型": "Warten auf verfügbares Generierungsmodell",
  "memory:还没有启用生成模型": "Noch kein Generierungsmodell aktiviert",
  "memory:缺少生成模型 API Key": "API-Schlüssel des Generierungsmodells fehlt",
  "memory:模型凭据暂不可用":
    "Modell-Anmeldedaten sind vorübergehend nicht verfügbar",
  "memory:本机凭据存储暂不可用":
    "Lokaler Anmeldedatenspeicher ist vorübergehend nicht verfügbar",
  "memory:需要授权访问本机钥匙串。请重新打开 App 并完成一次授权":
    "Schlüsselbund-Zugriff muss autorisiert werden. Öffnen Sie die App erneut und bestätigen Sie den Zugriff.",
  "memory:读取本机钥匙串超时。重新保存一遍模型配置即可恢复":
    "Zeitüberschreitung beim Lesen des Schlüsselbunds. Speichern Sie die Modellkonfiguration erneut, um das Problem zu beheben.",
  "memory:写入本机钥匙串超时。重新保存一遍模型配置即可恢复":
    "Zeitüberschreitung beim Schreiben in den Schlüsselbund. Speichern Sie die Modellkonfiguration erneut, um das Problem zu beheben.",
  "memory:模型认证失败。更新 key 后，到设置里点一次检测，会自动接着处理":
    "Modellauthentifizierung fehlgeschlagen. Aktualisieren Sie den Schlüssel und starten Sie in den Einstellungen eine Prüfung, um die Verarbeitung fortzusetzen.",
  "memory:模型账户余额不足。充值或换 key 后，到设置里点一次检测，会自动接着处理":
    "Guthaben des Modellkontos reicht nicht aus. Laden Sie auf oder wechseln Sie den Schlüssel und starten Sie in den Einstellungen eine Prüfung, um die Verarbeitung fortzusetzen.",
  "memory:模型服务拒绝访问。更新权限后，到设置里点一次检测，会自动接着处理":
    "Modelldienst hat den Zugriff verweigert. Aktualisieren Sie die Berechtigungen und starten Sie in den Einstellungen eine Prüfung, um die Verarbeitung fortzusetzen.",
  "memory:模型服务限流": "Anfragelimit des Modelldienstes erreicht",
  "memory:模型服务暂不可用": "Modelldienst vorübergehend nicht verfügbar",
  "memory:模型服务响应超时": "Zeitüberschreitung beim Modelldienst",
  "memory:模型请求不兼容，请核对配置":
    "Modellanfrage ist nicht kompatibel; prüfen Sie die Konfiguration",
  "memory:模型请求未成功": "Modellanfrage fehlgeschlagen",
  "memory:HY Memory 不可用": "HY Memory nicht verfügbar",
  "memory:HY Memory 启动失败": "HY Memory konnte nicht gestartet werden",
  "memory:暂时没能整理，稍后会自动重试":
    "Noch nicht verarbeitet; wird automatisch wiederholt",
  "memory:这次没能整理成功，正在重试":
    "Verarbeitung diesmal fehlgeschlagen; wird wiederholt",
  "memory:生成模型换过了，这批会重新整理":
    "Generierungsmodell geändert; diese Einträge werden neu verarbeitet",
  "memory:整理方式换过了，这批会重新整理":
    "Verarbeitungseinstellungen geändert; diese Einträge werden neu verarbeitet",
  "memory:已提炼": "Extrahiert",
  "memory:旧版确认记录": "Alter Bestätigungseintrag",
  "memory:创建": "Erstellt",
  "memory:纠正": "Korrektur",
  "memory:调整使用范围": "Zugriffsbereich geändert",
  "memory:撤销修改": "Änderung rückgängig gemacht",
  "memory:模型更新": "Modellaktualisierung",
  "memory:被新记忆取代": "Durch neue Erinnerung ersetzt",
  "memory:其他操作": "Sonstige Aktion",
  "memory:删除（记录）": "Löschung",
  "memory:修改已保存，但读取最新列表失败。已停止使用旧回答，请重新读取。":
    "Änderung gespeichert, aber die neueste Liste konnte nicht geladen werden. Alte Antworten werden nicht mehr verwendet; laden Sie die Liste neu.",
  "memory:纠正已保存，但读取最新列表失败。已停止使用旧回答，请重新读取。":
    "Korrektur gespeichert, aber die neueste Liste konnte nicht geladen werden. Alte Antworten werden nicht mehr verwendet; laden Sie die Liste neu.",
  "memory:未收到操作结果，请重试。":
    "Kein Ergebnis erhalten. Bitte wiederholen Sie den Vorgang.",
  "memory:批量处理结果已保留，但列表刷新失败，请重新读取记忆库。":
    "Ergebnisse der Stapelaktion bleiben erhalten, aber die Liste konnte nicht aktualisiert werden. Laden Sie die Erinnerungsbibliothek neu.",
  "memory:批量处理结果已保留，但列表刷新失败，稍后会自动重新读取。":
    "Ergebnisse der Stapelaktion bleiben erhalten, aber die Liste konnte nicht aktualisiert werden. Sie wird später automatisch neu geladen.",
  "memory:召回范围已保存，但列表刷新失败。请稍后重新打开记忆页。":
    "Abrufbereich gespeichert, aber die Liste konnte nicht aktualisiert werden. Öffnen Sie „Erinnerungen“ später erneut.",
  "memory:原始对话暂时无法读取":
    "Originalunterhaltungen konnten nicht geladen werden",
  "memory:没有匹配的原始对话": "Keine passenden Originalunterhaltungen",
  "memory:还没有导入的原始对话": "Noch keine Originalunterhaltungen importiert",
  "memory:查看已读内容": "Gespeicherte Inhalte anzeigen",
  "memory:已保存原文": "Originaltext gespeichert",
  "memory:刷新已读内容": "Gespeicherte Inhalte aktualisieren",
  "memory:正在核对本机已保存记录…":
    "Gespeicherte lokale Einträge werden geprüft…",
  "memory:原始消息": "Originalnachrichten",
  "memory:会话": "Unterhaltungen",
  "memory:用户发言": "Nutzernachrichten",
  "memory:Agent 发言": "Agent-Nachricht",
  "memory:作者未知": "Unbekannter Autor",
  "memory:原始时间未知": "Ursprüngliche Zeit unbekannt",
  "memory:工具记录": "Tool-Eintrag",
  "memory:原始时间来自已保存记录。":
    "Ursprüngliche Zeitstempel stammen aus gespeicherten Einträgen.",
  "memory:已保存的原文样例": "Gespeicherte Originaltext-Beispiele",
  "memory:最多展示 3 段，不代表全部历史或推荐记忆。":
    "Bis zu 3 Beispiele; nicht der gesamte Verlauf und keine empfohlenen Erinnerungen.",
  "memory:查看完整对话": "Ganze Unterhaltung anzeigen",
  "memory:原文较长，此处仅展示前 600 个字符。":
    "Langer Originaltext; hier werden nur die ersten 600 Zeichen angezeigt.",
  "memory:这个来源还没有保存原文。":
    "Aus dieser Quelle ist noch kein Originaltext gespeichert.",
  "memory:完整对话": "Ganze Unterhaltung",
  "memory:关闭完整对话": "Ganze Unterhaltung schließen",
  "memory:用户": "Nutzer",
  "memory:暂时无法读取已保存内容，旧回执已收起。请检查授权或重试。":
    "Gespeicherte Inhalte konnten nicht gelesen werden. Der vorherige Beleg wurde geschlossen; prüfen Sie die Berechtigungen oder versuchen Sie es erneut.",
  "memory:这段原文暂不可读，可能已被撤回授权。请刷新回执。":
    "Dieser Originaltext ist nicht verfügbar; der Zugriff wurde möglicherweise widerrufen. Aktualisieren Sie den Beleg.",
  "memory:外部记忆召回范围": "Bereich des externen Erinnerungsabrufs",
  "memory:外部记忆召回": "Externer Erinnerungsabruf",
  "memory:调整召回范围": "Abrufbereich anpassen",
  "memory:所有关联工具": "Alle verbundenen Tools",
  "memory:已停止外部记忆召回": "Externer Erinnerungsabruf gestoppt",
  "memory:原有限定范围": "Bisheriger begrenzter Bereich",
  "memory:选择召回范围": "Abrufbereich wählen",
  "memory:停止外部记忆召回": "Externen Erinnerungsabruf stoppen",
  "memory:正在保存…": "Wird gespeichert…",
  "memory:保存召回范围": "Abrufbereich speichern",
  "memory:取消范围修改": "Bereichsänderungen verwerfen",
  "memory:原文检索仍按来源授权；已带出的内容不会撤回。":
    "Originaltextsuchen folgen weiterhin den Quellberechtigungen; bereits bereitgestellte Inhalte werden nicht zurückgeholt.",
  "memory:记忆已更新，请重新打开这条记忆后再调整范围。":
    "Die Erinnerung hat sich geändert. Öffnen Sie sie erneut, bevor Sie den Bereich anpassen.",
  "memory:范围修改未提交，当前范围保持不变。请修正后重试。":
    "Bereichsänderungen wurden nicht übermittelt. Der aktuelle Bereich bleibt unverändert; beheben Sie das Problem und versuchen Sie es erneut.",
  "memory:保存结果尚未确认，请重试。":
    "Speicherergebnis ist unbestätigt. Bitte wiederholen Sie den Vorgang.",
  "memory:原话已保存在本机；当前内容、模型接收方或授权版本尚未通过核对，未继续整理。":
    "Das Original ist auf diesem Gerät gespeichert; Inhalt, Modellempfänger oder Berechtigungsversion haben die Prüfung nicht bestanden, daher wurde die Verarbeitung nicht fortgesetzt.",
  "memory:HY 未返回可核对的已有记忆，未创建替代内容；原始记录已保留，可重试处理。":
    "HY hat keine prüfbaren vorhandenen Erinnerungen zurückgegeben, und es wurde kein Ersatz erstellt. Originaleinträge bleiben erhalten; Sie können die Verarbeitung wiederholen.",
  "memory:历史引用尚未绑定到原始来源，当前批次未完成，原话已保留。":
    "Historische Quellenangaben sind noch nicht mit ihren Originalquellen verknüpft. Dieser Stapel ist unvollständig; das Original bleibt erhalten.",
  "memory:记忆内容未通过来源与作者核验，当前批次未完成，原话已保留。":
    "Der Erinnerungsinhalt hat die Quellen- und Autorenprüfung nicht bestanden. Dieser Stapel ist unvollständig; das Original bleibt erhalten.",
  "memory:所选记忆已不可读取，请重新读取记忆库。":
    "Die ausgewählte Erinnerung kann nicht mehr gelesen werden. Laden Sie die Erinnerungsbibliothek neu.",
  "memory:还没连上本地 Core，正在重试…":
    "Lokaler Core ist noch nicht verbunden; neuer Versuch läuft…",
  "memory:记忆内容或使用权限已变化，回答已停止。请重新提问。":
    "Inhalt oder Zugriffsberechtigungen der Erinnerung haben sich geändert, daher wurde die Antwort gestoppt. Fragen Sie erneut.",
  "memory:这条记忆的来源授权已撤回，暂时不能读取。":
    "Der Quellzugriff für diese Erinnerung wurde widerrufen; sie kann derzeit nicht gelesen werden.",
  "memory:记忆已被更新，请刷新后重试。":
    "Diese Erinnerung wurde aktualisiert. Aktualisieren Sie die Anzeige und versuchen Sie es erneut.",
  "memory:已删除的记忆不能修改。":
    "Gelöschte Erinnerungen können nicht bearbeitet werden.",
  "memory:这条记忆已经删除。": "Diese Erinnerung wurde gelöscht.",
  "memory:这条记忆已被更新的记忆取代，只能查看或删除。":
    "Diese Erinnerung wurde durch eine aktualisierte ersetzt und kann nur angezeigt oder gelöscht werden.",
  "memory:没有更早的修改可以撤销。":
    "Es gibt keine früheren Änderungen zum Rückgängigmachen.",
  "memory:来源原话已不存在，无法建立这条记忆。":
    "Der Originaltext der Quelle existiert nicht mehr, daher kann diese Erinnerung nicht erstellt werden.",
  "memory:这次操作与已完成的请求不一致。":
    "Diese Aktion passt nicht zur abgeschlossenen Anfrage.",
  "memory:输入内容不符合要求。": "Die Eingabe erfüllt die Anforderungen nicht.",
  "memory:本地记忆库正忙，请稍后重试。":
    "Die lokale Erinnerungsbibliothek ist ausgelastet. Bitte versuchen Sie es später erneut.",
  "memory:记忆": "Erinnerungen",
  "memory:记忆空间": "Erinnerungsbereich",
  "memory:全部应用": "Alle Apps",
  "memory:来源": "Quelle",
  "memory:主题": "Thema",
  "memory:归纳": "Synthese",
  "memory:全部记忆": "Alle Erinnerungen",
  "memory:来源待核对": "Quelle prüfen",
  "memory:所选来源待核对": "Ausgewählte Quelle prüfen",
  "memory:未识别来源": "Unbekannte Quelle",
  "memory:这个来源下还没有记忆": "Noch keine Erinnerungen aus dieser Quelle",
  "memory:只在最相关的 {count} 条记忆里查找关键词":
    "Schlüsselwörter werden nur in den {count} relevantesten Erinnerungen gesucht",
  "memory:未知来源": "Unbekannte Quelle",
  "memory:记忆读取失败": "Erinnerungen konnten nicht geladen werden",
  "memory:正在读取…": "Wird geladen…",
  "memory:正在更新…": "Wird aktualisiert…",
  "memory:读取失败": "Laden fehlgeschlagen",
  "memory:正在核对来源…": "Quellen werden geprüft…",
  "memory:正在导入历史会话，记忆将陆续出现":
    "Frühere Unterhaltungen werden importiert; Erinnerungen erscheinen nach und nach",
  "memory:记忆工作台": "Erinnerungs-Arbeitsbereich",
  "memory:来源与主题": "Quellen und Themen",
  "memory:记忆或原始对话": "Erinnerungen oder Originalunterhaltungen",
  "memory:原始对话": "Originalunterhaltungen",
  "memory:地图组织方式": "Kartengruppierung",
  "memory:筛选地图记忆": "Kartenerinnerungen filtern",
  "memory:搜索原始对话": "Originalunterhaltungen durchsuchen",
  "memory:搜索记忆": "Erinnerungen durchsuchen",
  "memory:筛选记忆…": "Erinnerungen filtern…",
  "memory:向记忆提问": "Erinnerungen eine Frage stellen",
  "memory:询问全部已授权可用记忆…":
    "Alle autorisierten, verfügbaren Erinnerungen fragen…",
  "memory:发送问题": "Frage senden",
  "memory:刷新地图记忆": "Kartenerinnerungen aktualisieren",
  "memory:关闭新增记忆提示": "Hinweis auf neue Erinnerung schließen",
  "memory:地图记忆列表": "Liste der Kartenerinnerungen",
  "memory:记忆读取失败，请重试。":
    "Erinnerungen konnten nicht geladen werden. Bitte wiederholen Sie den Vorgang.",
  "memory:重新读取地图记忆": "Kartenerinnerungen neu laden",
  "memory:没有匹配的记忆": "Keine passenden Erinnerungen",
  "memory:本页没有匹配的主题记忆":
    "Keine passenden Themenerinnerungen auf dieser Seite",
  "memory:还没有已生成的记忆": "Noch keine Erinnerungen erstellt",
  "memory:地图记忆分页": "Seiten der Kartenerinnerungen",
  "memory:上一页记忆": "Vorherige Erinnerungsseite",
  "memory:下一页记忆": "Nächste Erinnerungsseite",
  "memory:记忆关系图": "Erinnerungsgraph",
  "memory:记忆关联": "Erinnerungsverbindungen",
  "memory:刚导入的原话，还不是沉淀后的记忆":
    "Neu importierter Originaltext, noch keine Erinnerungen",
  "memory:来源归属": "Quellverbindungen",
  "memory:可在沉淀记忆后查看视图":
    "Erstellen Sie Erinnerungen, um den Graphen zu sehen",
  "memory:正在读取记忆地图…": "Erinnerungskarte wird geladen…",
  "memory:查看选中来源原文": "Text der ausgewählten Quelle anzeigen",
  "memory:查看选中记忆原文": "Text der ausgewählten Erinnerung anzeigen",
  "memory:查看原文": "Originalunterhaltung anzeigen",
  "memory:整理已有关系": "Vorhandene Verbindungen erstellen",
  "memory:正在读取原文…": "Originalunterhaltung wird geladen…",
  "memory:选择一条原始对话": "Originalunterhaltung auswählen",
  "memory:记忆暂时无法读取": "Erinnerungen sind vorübergehend nicht verfügbar",
  "memory:当前筛选没有可显示的记忆": "Keine Erinnerungen für diesen Filter",
  "memory:正在读取记忆…": "Erinnerungen werden geladen…",
  "memory:已保存的对话还未生成记忆":
    "Gespeicherte Unterhaltungen sind noch keine Erinnerungen",
  "memory:还没有可显示的记忆": "Noch keine Erinnerungen anzuzeigen",
  "memory:连线表示来源归属": "Linien zeigen Quellverbindungen",
  "memory:记忆详情": "Erinnerungsdetails",
  "memory:来源原文": "Originaltext der Quelle",
  "memory:关联节点": "Verknüpfter Knoten",
  "memory:关闭记忆详情": "Erinnerungsdetails schließen",
  "memory:关闭详情": "Details schließen",
  "memory:正在读取这条记忆…": "Diese Erinnerung wird geladen…",
  "memory:这条记忆的原文暂时无法读取，选择已保留。":
    "Originaltext dieser Erinnerung konnte nicht geladen werden. Die Auswahl bleibt erhalten.",
  "memory:重试读取原文": "Unterhaltung erneut laden",
  "memory:已沉淀的记忆": "Erstellte Erinnerungen",
  "memory:正在读取记忆详情…": "Erinnerungsdetails werden geladen…",
  "memory:沉淀记忆后查看详情":
    "Erstellen Sie Erinnerungen, um Details zu sehen",
  "memory:时间未知": "Zeit unbekannt",
  "memory:来源原文已不可读取": "Originaltext der Quelle ist nicht verfügbar",
  "memory:关联记忆": "Verwandte Erinnerungen",
  "memory:正在读取完整对话…": "Ganze Unterhaltung wird geladen…",
  "memory:重试读取完整对话": "Ganze Unterhaltung erneut laden",
  "memory:看完整对话": "Ganze Unterhaltung anzeigen",
  "memory:今天": "Heute",
  "memory:你": "Sie",
  "memory:助手": "Assistent",
  "memory:工具": "Tool",
  "memory:未知": "Unbekannt",
  "memory:作者待核对": "Autor prüfen",
  "memory:你说": "Ihre Aussage",
  "memory:助手原话": "Originalnachricht des Assistenten",
  "memory:已沉淀": "Erstellt",
  "memory:已删除": "Gelöscht",
  "memory:已被取代": "Ersetzt",
  "memory:状态待核对": "Status prüfen",
  "memory:编辑": "Bearbeiten",
  "memory:原始依据": "Originalbeleg",
  "memory:这段原文当前不可查看。":
    "Dieser Originaltext ist derzeit nicht verfügbar.",
  "memory:版本记录": "Versionsverlauf",
  "memory:更多管理": "Weitere Aktionen",
  "memory:撤销上次修改": "Letzte Änderung rückgängig",
  "memory:删除记忆": "Erinnerung löschen",
  "memory:编辑记忆": "Erinnerung bearbeiten",
  "memory:返回记忆": "Zurück zur Erinnerung",
  "memory:修改这条记忆，原始对话保持不变。":
    "Bearbeiten Sie diese Erinnerung; die Originalunterhaltung bleibt unverändert.",
  "memory:记忆内容": "Inhalt der Erinnerung",
  "memory:使用范围保持不变": "Zugriffsbereich bleibt unverändert",
  "memory:已有新版本": "Es gibt eine neuere Version",
  "memory:你的草稿已保留，先查看新版本再决定如何修改。":
    "Ihr Entwurf bleibt erhalten. Prüfen Sie die neue Version, bevor Sie Änderungen vornehmen.",
  "memory:查看新版本": "Neue Version anzeigen",
  "memory:基于此版本继续编辑": "Ab dieser Version weiter bearbeiten",
  "memory:取消": "Abbrechen",
  "memory:保存": "Speichern",
  "memory:重试": "Wiederholen",
  "memory:删除": "Löschen",
  "memory:对话": "Unterhaltung",
  "memory:工作": "Arbeit",
  "memory:编码": "Programmieren",
  "memory:保存修改": "Speichern",
  "memory:对应记忆": "Zugehörige Erinnerung",
  "memory:查看记忆": "Erinnerungen anzeigen",
  "memory:删除这条记忆？": "Diese Erinnerung löschen?",
  "memory:删除后不再用于新的召回，来源 App 中的原始对话不会删除。":
    "Sie wird für neue Abfragen nicht mehr verwendet. Originalunterhaltungen in der Quell-App werden nicht gelöscht.",
  "memory:已经发送给应用的内容无法远程收回。":
    "Bereits an Apps gesendete Inhalte können nicht zurückgeholt werden.",
  "memory:放弃尚未保存的修改？": "Ungespeicherte Änderungen verwerfen?",
  "memory:来源权限已变化或原文不可用。":
    "Quellberechtigungen haben sich geändert oder der Originaltext ist nicht verfügbar.",
  "memory:暂时无法读取新版本，草稿已保留。":
    "Neue Version konnte nicht geladen werden. Ihr Entwurf bleibt erhalten.",
  "memory:修改未提交；草稿已保留。请按提示修正后重试。":
    "Änderungen wurden nicht übermittelt; Ihr Entwurf bleibt erhalten. Beheben Sie das Problem und versuchen Sie es erneut.",
  "memory:修改尚未确认，草稿已保留。请先刷新核对，避免重复提交。":
    "Änderungen sind unbestätigt; Ihr Entwurf bleibt erhalten. Aktualisieren und prüfen Sie vor dem erneuten Versuch, um doppeltes Übermitteln zu vermeiden.",
  "memory:删除未提交。请核对这条记忆的状态后重试。":
    "Löschung wurde nicht übermittelt. Prüfen Sie den Status dieser Erinnerung, bevor Sie es erneut versuchen.",
  "memory:删除尚未确认。请重新核对记忆状态，不会自动扩大删除范围。":
    "Löschung ist unbestätigt. Prüfen Sie den Status der Erinnerung; der Löschumfang wird nicht automatisch erweitert.",
  "memory:撤销未提交。请按提示修正后重试。":
    "Rückgängig wurde nicht übermittelt. Beheben Sie das Problem und versuchen Sie es erneut.",
  "memory:撤销尚未确认，请先核对当前记忆版本，避免重复撤销。":
    "Rückgängig ist unbestätigt. Prüfen Sie die aktuelle Version vor dem erneuten Versuch, um doppeltes Rückgängigmachen zu vermeiden.",
  "memory:添加记忆": "Erinnerung hinzufügen",
  "memory:关闭新增记忆": "Dialog „Erinnerung hinzufügen“ schließen",
  "memory:关闭": "Schließen",
  "memory:记下这件事": "Notieren",
  "memory:例如：项目周会不排在周五。":
    "Zum Beispiel: Das Projektmeeting nicht auf Freitag legen.",
  "memory:整理模型：": "Verarbeitungsmodell: ",
  "memory:尚未配置，原话仍可保存":
    "Nicht eingerichtet; das Original lässt sich trotzdem speichern",
  "memory:正在读取": "Wird geladen",
  "memory:配置模型": "Modell einrichten",
  "memory:保存中…": "Wird gespeichert…",
  "memory:保存原话": "Original speichern",
  "memory:最近添加": "Zuletzt hinzugefügt",
  "memory:原话已保存，等待可用模型":
    "Original gespeichert; warten auf verfügbares Modell",
  "memory:原话已保存，等待整理":
    "Original gespeichert; warten auf Verarbeitung",
  "memory:正在等待整理": "Warten auf Verarbeitung",
  "memory:正在整理": "Wird verarbeitet",
  "memory:原话已保存，整理未完成":
    "Original gespeichert; Verarbeitung unvollständig",
  "memory:整理已完成": "Verarbeitung abgeschlossen",
  "memory:原话已保存，未提取出可用记忆":
    "Original gespeichert; keine nutzbare Erinnerung extrahiert",
  "memory:最近添加的内容读取失败。":
    "Zuletzt hinzugefügte Erinnerungen konnten nicht gelesen werden.",
  "memory:整理状态暂时不可用，原话已保存。":
    "Verarbeitungsstatus ist vorübergehend nicht verfügbar; das Original ist gespeichert.",
  "memory:已保存，正在后台沉淀记忆":
    "Gespeichert; Erinnerungen werden im Hintergrund erstellt",
  "memory:未能确认保存结果，输入已保留。重试不会重复添加。":
    "Speicherergebnis konnte nicht bestätigt werden. Ihr Text bleibt erhalten; ein neuer Versuch fügt kein Duplikat hinzu.",
  "memory:模型服务要求检查余额或付款状态，原话仍已保存。":
    "Der Modelldienst verlangt eine Prüfung von Guthaben oder Zahlung; das Original bleibt gespeichert.",
  "memory:这台设备打不开 3D 视图，已显示关联列表。":
    "3D-Ansicht ist auf diesem Gerät nicht verfügbar. Verbindungen werden als Liste angezeigt.",
  "memory:图谱工具": "Graph-Steuerung",
  "memory:放大": "Vergrößern",
  "memory:放大图谱": "Graph vergrößern",
  "memory:缩小": "Verkleinern",
  "memory:缩小图谱": "Graph verkleinern",
  "memory:重置视角": "Ansicht zurücksetzen",
  "memory:重置图谱视角": "Graph-Ansicht zurücksetzen",
  "memory:查看读取详情": "Lesedetails anzeigen",
  "memory:原文暂时无法读取": "Originaltext konnte nicht geladen werden",
  "memory:暂时无法读取这段对话，已保留当前记忆。":
    "Diese Unterhaltung konnte nicht geladen werden. Die aktuelle Erinnerung bleibt ausgewählt.",
  "memory:已清除筛选，定位到关联记忆。":
    "Filter zurückgesetzt, um die zugehörige Erinnerung zu finden.",
  "memory:所选记忆不在当前筛选内，已取消选择。":
    "Die ausgewählte Erinnerung liegt nicht im Filter; Auswahl aufgehoben.",
  "memory:列表分隔符": ", ",
  "memory:冒号": ":",
  "memory:手动添加": "Manuell hinzugefügt",
  "memory:导入的已有记忆": "Importierte vorhandene Erinnerung",
  "memory:记忆与原文": "Erinnerungen und Originaltext",
  "memory:第 {revision} 版": "Version {revision}",
  "memory:对话时间 ": "Unterhaltungszeit",
  "memory:沉淀时间 ": "Erstellt am",
  "memory:原对话 ": "Originalunterhaltung ",
  "memory:{count} 条记录没有原始时间，未用导入时间代替。":
    "Einträge ohne ursprünglichen Zeitstempel: {count}; die Importzeit wurde nicht als Ersatz verwendet.",
  "memory:作者数量按当前记录分类统计，不代表记忆抽取已完成或语义已核实。":
    "Autorenzahlen spiegeln gespeicherte Einträge wider, nicht abgeschlossene Erinnerungsextraktion oder geprüfte Bedeutung.",
  "memory:统计于 ": "Statistik vom ",
  "memory: · 导入数量仍在更新，可刷新核对":
    " · Importanzahl wird noch aktualisiert; aktualisieren Sie zur Prüfung",
  "memory:对话于 {day}": "Unterhaltung am {day}",
  "memory:导入于 {day}": "Importiert am {day}",
  "memory:写入于 {day}": "Geschrieben am {day}",
  "memory:已开始处理所选对话": "Ausgewählte Unterhaltungen werden verarbeitet",
  "memory:处理请求已受理": "Verarbeitungsanfrage erhalten",
  "memory:模型连接已通过。首批记忆生成后，会逐步出现在记忆列表里。":
    "Modellverbindung bestätigt. Erinnerungen erscheinen in der Liste, sobald sie erstellt sind.",
  "memory:已导入 {count} 段对话，整理成记忆后会出现在这里。":
    "Importierte Unterhaltungen: {count}. Erinnerungen erscheinen nach der Verarbeitung hier.",
  "memory:历史已保存在本机，记忆就绪后会出现在这里。":
    "Der Verlauf ist auf diesem Gerät gespeichert. Erinnerungen erscheinen hier, sobald sie bereit sind.",
  "memory:连接应用并导入历史后，可以开始沉淀记忆。":
    "Verbinden Sie eine App und importieren Sie den Verlauf, um Erinnerungen zu erstellen.",
  "memory:当前已生成 0 条记忆。请求已受理不等于已经完成，原始对话、模型处理任务和记忆数量分别统计。":
    "Noch keine Erinnerungen erstellt. Eine erhaltene Anfrage ist nicht abgeschlossen; Originalunterhaltungen, Verarbeitungsjobs und Erinnerungen werden getrennt gezählt.",
  "memory:返回应用": "Zurück zu Apps",
  "memory:共 {count} 条 · 正在核对来源…":
    "{count} insgesamt · Quellen werden geprüft…",
  "memory:共 {count} 条 · 正在核对来源….one":
    "{count} insgesamt · Quellen werden geprüft…",
  "memory:共 {count} 条": "{count} Einträge insgesamt",
  "memory:共 {count} 条.one": "{count} Eintrag insgesamt",
  "memory: · 正在核对…": " · Wird geprüft…",
  "memory:新增 {count} 条记忆": "{count} neue Erinnerungen",
  "memory:新增 {count} 条记忆.one": "{count} neue Erinnerung",
  "memory:沉淀于 ": "Erstellt am ",
  "memory:共 {count} 条，第 {page} / {pages} 页":
    "{count} insgesamt · Seite {page} von {pages}",
  "memory:共 {count} 条，第 {page} / {pages} 页.one":
    "{count} insgesamt · Seite {page} von {pages}",
  "memory:已沉淀 {count} 条记忆": "{count} Erinnerungen erstellt",
  "memory:已沉淀 {count} 条记忆.one": "{count} Erinnerung erstellt",
  "memory:关键词“{query}”": "Schlüsselwort „{query}“",
  "memory:已沉淀 {count} 条{filter} · 当前展示 {shown} 条":
    "{count} Erinnerungen erstellt{filter} · {shown} angezeigt",
  "memory:已沉淀 {count} 条{filter} · 当前展示 {shown} 条.one":
    "{count} Erinnerung erstellt{filter} · {shown} angezeigt",
  "memory:（{filter}）": " ({filter})",
  "memory:第 {page} 页": "Seite {page}",
  "memory:正在等待整理：": "Warten auf Verarbeitung: ",
  "memory:记忆未在生成": "Es werden keine Erinnerungen erstellt",
  "memory:还没有开始生成记忆：{reason}（{count} 条待处理）":
    "Es werden keine Erinnerungen erstellt: {reason} ({count} ausstehend)",
  "memory:去配置生成模型": "Generierungsmodell einrichten",
  "memory:模型暂停处理": "Modell pausiert",
  "memory:模型配置有问题（{reason}），记忆暂停处理，已有 {count} 条在排队":
    "Problem mit der Modellkonfiguration ({reason}). Die Verarbeitung ist pausiert; {count} Einträge warten",
  "memory:模型配置有问题（{reason}），记忆暂停处理，已有 {count} 条在排队.one":
    "Problem mit der Modellkonfiguration ({reason}). Die Verarbeitung ist pausiert; {count} Eintrag wartet",
  "memory:API Key 无效或没有权限":
    "API-Schlüssel ungültig oder ohne Berechtigung",
  "memory:找不到模型或地址": "Modell oder Adresse nicht gefunden",
  "memory:请求过于频繁，被服务商限流":
    "zu viele Anfragen, vom Anbieter gedrosselt",
  "memory:模型响应超时": "Zeitüberschreitung bei der Modellantwort",
  "memory:模型服务出错": "Fehler im Modelldienst",
  "memory:无法连接模型服务": "Modelldienst nicht erreichbar",
  "memory:连接模型服务失败": "Verbindung zum Modelldienst fehlgeschlagen",
  "calls.directHost": "Direkter Aufruf (Diagnose)",
  "calls.headline.timedOut":
    "{host} konnte Erinnerungen nicht rechtzeitig abfragen",
  "calls.headline.unavailable":
    "{host} kann Erinnerungen gerade nicht abfragen",
  "calls.headline.denied": "{host} war nicht zur Erinnerungsabfrage berechtigt",
  "calls.headline.empty": "{host} hat keine passenden Erinnerungen gefunden",
  "calls.headline.found": "{host} hat {count} verwandte Einträge gefunden",
  "calls.headline.found.one": "{host} hat {count} verwandten Eintrag gefunden",
  "calls.headline.rawOnly":
    "{host} hat {count} ursprüngliche Unterhaltungen gefunden",
  "calls.headline.rawOnly.one":
    "{host} hat {count} ursprüngliche Unterhaltung gefunden",
  "calls.headline.memoryOnly": "{host} hat {count} Erinnerungen gefunden",
  "calls.headline.memoryOnly.one": "{host} hat {count} Erinnerung gefunden",
  "calls.headline.mixed":
    "{host} hat {memories} Erinnerungen · {raw} ursprüngliche Unterhaltungen gefunden",
  "calls.headline.mixed.one":
    "{host} hat {memories} Erinnerung · {raw} ursprüngliche Unterhaltungen gefunden",
  "calls.headline.completed": "Aufruf von {host} abgeschlossen",
  "calls.detail.timedOut":
    "Erinnerungen werden verarbeitet. Bei der Abfrage kam es zu einer Zeitüberschreitung, daher enthielt diese Antwort keine Erinnerungen.",
  "calls.detail.unavailable":
    "MemHub ist vorübergehend nicht verfügbar, daher enthielt diese Antwort keine Erinnerungen.",
  "calls.detail.denied":
    "Die aktuellen Berechtigungen erlauben diese Abfrage nicht.",
  "calls.detail.partialDelivered":
    "An {host} bereitgestellt · einige Inhalte werden noch verarbeitet",
  "calls.detail.partial": "Einige Inhalte werden noch verarbeitet",
  "calls.detail.delivered": "An {host} bereitgestellt",
  "calls.detail.notDelivered": "Nicht an die App bereitgestellt",
  "calls.detail.empty": "Diesmal keine passenden Erinnerungen",
  "calls.detail.success": "Abfrage erfolgreich",
  "calls.title": "Nutzungsverlauf",
  "calls.query": "Diese Erinnerungsabfrage",
  "calls.backApps": "Zurück zu Apps",
  "calls.backHistory": "Zurück zum Verlauf",
  "calls.filter": "Nach App filtern",
  "calls.allApps": "Alle Apps",
  "calls.recent": "Letzter Verlauf",
  "calls.refresh": "Verlauf aktualisieren",
  "calls.clearPage": "Diese Seite löschen",
  "calls.clearAll": "Gesamten Verlauf löschen",
  "calls.clearAllShort": "Alle löschen",
  "calls.loading": "Nutzungsverlauf wird geladen",
  "calls.clearing": "Ausgewählter Verlauf wird gelöscht",
  "calls.readFailed":
    "Verlauf konnte nicht geladen werden. Aktualisieren Sie und versuchen Sie es erneut.",
  "calls.unavailable":
    "Nutzungsverlauf ist nicht verfügbar. Aktualisieren Sie später.",
  "calls.incomplete":
    "Einige Aufrufe wurden nicht aufgezeichnet; die Liste ist möglicherweise unvollständig.",
  "calls.callDetail": "Aufrufdetails",
  "calls.source": " · Quelle: {source}",
  "calls.stats":
    "Originalpassagen: {raw} · Erinnerungen: {memories} · {seconds} s",
  "calls.metadata": "Eintragsdetails",
  "calls.privacy":
    "Zum Schutz Ihrer Privatsphäre werden Ihre ursprüngliche Frage und die Antwort hier nicht gespeichert; nur Zeit, Quelle und Ergebnis bleiben erhalten.",
  "calls.exactTime": "Genaue Zeit",
  "calls.app": "App",
  "calls.type": "Typ",
  "calls.result": "Ergebnisstatus",
  "calls.tool": "Erinnerungen abfragen",
  "calls.notRecorded": "Nicht aufgezeichnet",
  "calls.missingRow":
    "Dieser Eintrag ist nicht mehr auf dieser Seite. Kehren Sie zum Verlauf zurück, um ihn zu finden.",
  "calls.list": "Aufrufverlauf",
  "calls.emptyPage": "Keine Einträge auf dieser Seite",
  "calls.empty": "Noch kein Nutzungsverlauf",
  "calls.pagination": "Verlaufsseiten",
  "calls.previous": "Vorherige Seite",
  "calls.next": "Nächste Seite",
  "calls.policy": "Aufbewahrung: {days} Tage, bis zu {count} Einträge",
  "calls.confirmAllTitle": "Gesamten Nutzungsverlauf löschen?",
  "calls.confirmPageTitle": "{count} Einträge auf dieser Seite löschen?",
  "calls.confirmPageTitle.one": "{count} Eintrag auf dieser Seite löschen?",
  "calls.confirmAllDescription":
    "Löscht alle aufbewahrten Aufrufeinträge, nicht nur diese Seite oder diesen Filter. Originalunterhaltungen, Erinnerungen, Berechtigungen und Aufbewahrungsregeln bleiben unverändert. Dies kann nicht rückgängig gemacht werden.",
  "calls.confirmPageDescription":
    "Löscht nur diese Aufrufeinträge. Originalunterhaltungen, Erinnerungen, Berechtigungen und Aufbewahrungsregeln bleiben unverändert. Dies kann nicht rückgängig gemacht werden.",
  "calls.confirmAll": "Alle löschen bestätigen",
  "calls.confirmPage": "Löschen bestätigen",
  "calls.cancel": "Abbrechen",
  "calls.clearedAll": "Gesamter Verlauf wurde gelöscht.",
  "calls.clearedPage": "Ausgewählter Verlauf wurde gelöscht.",
  "calls.clearAllFailed":
    "Nutzungsverlauf konnte nicht gelöscht werden. Vorhandene Einträge bleiben unverändert; prüfen Sie sie und versuchen Sie es erneut.",
  "calls.clearPageFailed":
    "Ausgewählter Verlauf konnte nicht gelöscht werden. Vorhandene Einträge bleiben unverändert; prüfen Sie sie und versuchen Sie es erneut.",
  "calls.clearUnknown":
    "Ergebnis konnte nicht bestätigt werden. Prüfen Sie den neu geladenen Verlauf; das Löschen wurde nicht automatisch wiederholt.",
  "calls.references": "Verwandte Inhalte",
  "calls.referencesLoading": "Verwandte Inhalte werden geladen",
  "calls.referencesFailed":
    "Verwandte Inhalte konnten nicht geladen werden. Aktualisieren Sie und versuchen Sie es erneut.",
  "calls.raw": "Originalunterhaltung",
  "calls.memory": "Erinnerungen",
  "calls.changed": "Version geändert",
  "calls.pending": "Prüfung nötig",
  "calls.notAvailable": "Nicht verfügbar",
  "calls.openRaw": "Originalunterhaltung anzeigen",
  "calls.openMemory": "Erinnerungen anzeigen",
  "calls.noReferences": "Keine verwandten Inhalte gefunden",
  "calls.rawBody": "Text der Originalunterhaltung",
  "calls.rawLoading": "Originalunterhaltung wird geladen",
  "calls.rawFailed":
    "Die Originalunterhaltung ist nicht verfügbar oder ihre Version hat sich geändert.",
  "calls.state.grounded": "Abfrage erfolgreich",
  "calls.state.empty": "Abfrage erfolgreich; keine passenden Erinnerungen",
  "calls.state.partial":
    "Abfrage erfolgreich; einige Inhalte werden noch verarbeitet",
  "calls.state.timed_out":
    "Zeitüberschreitung bei der Abfrage; keine Erinnerungen bereitgestellt",
  "calls.state.unavailable": "MemHub ist vorübergehend nicht verfügbar",
  "calls.state.denied": "Diese Abfrage ist nicht autorisiert",
  "calls.return.timed_out":
    "MemHub importiert und verarbeitet Erinnerungen. Die Abfrage wurde nicht rechtzeitig fertig, daher hat {host} ohne Erinnerungen geantwortet. Nach Abschluss der Verarbeitung funktioniert es wieder normal.",
  "calls.return.unavailable":
    "MemHub ist vorübergehend nicht verfügbar; diese Antwort enthielt keine Erinnerungen.",
  "calls.return.denied":
    "Die aktuellen Berechtigungen erlauben diese Abfrage nicht; der App wurden keine Erinnerungen bereitgestellt.",
  "calls.return.partial": "Einige Inhalte werden noch verarbeitet.",
  "clock.unknown": "Zeit unbekannt",
  "clock.today": "Heute {clock}",
  settings: "Einstellungen",
  languageTitle: "Oberflächensprache",
  languageLabel: "Sprache",
  languageSystem: "Systemsprache verwenden",
  languageChinese: "Vereinfachtes Chinesisch",
  languageEnglish: "Englisch",
  languageSpanish: "Spanisch",
  languageDescription:
    "Wählen Sie die Oberflächensprache. Diese Einstellung ändert Ihre Unterhaltungen oder Erinnerungen nicht.",
  languageSaveError:
    "Spracheinstellung konnte nicht gespeichert werden. Bitte wiederholen Sie den Vorgang.",
  languageLoadError:
    "Spracheinstellung konnte nicht geladen werden. Bitte wiederholen Sie den Vorgang.",
  nativeErrorTitle: "MemHub konnte nicht gestartet werden",
  startupPreparing: "Start läuft, Erinnerungsdienst wird vorbereitet…",
  startupUpgradeHint:
    "Der erste Start oder ein Upgrade der Erinnerungsdaten kann einige Minuten dauern. MemHub öffnet sich danach automatisch.",
  "migration.dialogMessage":
    "Die Datenmigration für dieses MemHub-Update wurde nicht abgeschlossen.",
  "migration.dialogDetail":
    "Zum Schutz Ihrer Daten startet MemHub nicht. Bitte hängen Sie das Migrationsprotokoll an ein GitHub-Issue an und senden Sie es uns. (Fehlercode: {code})",
  "migration.dataRestored":
    "Ihre Daten wurden auf den Stand vor dem Update zurückgesetzt.",
  "migration.logPath": "Protokoll: {path}",
  "migration.openLog": "Protokoll öffnen",
  "migration.quit": "Beenden",
  "migration.issueHeading": "## Migration beim Update fehlgeschlagen",
  "migration.issueUpgrade":
    "- Beim Update von MemHub {fromVersion} (Build {fromBuild}) auf {toVersion} (Build {toBuild}) ist die Datenmigration fehlgeschlagen.",
  "migration.issueCode": "- Fehlercode: {code}",
  "migration.issueStep": "- Fehlgeschlagener Schritt: {step}",
  "migration.issueRestored":
    "- Daten auf den Stand vor dem Update zurückgesetzt: {restored}",
  "migration.issueSystem": "- System: {system}",
  "migration.issueArch": " ({arch})",
  "migration.issueLog": "- Migrationsprotokoll (lokal): {path}",
  "migration.issueAttach":
    "Bitte ziehen Sie die oben genannte Protokolldatei als Anhang hierher und senden Sie dann ab.",
  "migration.unknown": "unbekannt",
  "migration.yes": "ja",
  "migration.no": "nein",
};
