import { currentEnglishMessages } from "./gui-i18n-current.js";

// English translations integrated from PR #114.
const baseEnglishMessages = {
  "memory:{count} 条": "{count} items",
  "memory:已可用 · {count} 段原文": "Available · {count} original excerpts",
  "memory:主题视角（非事项）": "Topic view (not matters)",
  "memory:来源视角": "Source view",
  "memory:{count} 条记忆 · 当前已加载": "{count} memories · Loaded",
  "memory:最近 {count} 条": "{count} recent conversations",
  "memory:已确认": "Confirmed",
  "memory:记忆库": "Memory library",
  "memory:问记忆": "Ask memories",
  "memory:历史对话": "Conversation history",
  "memory:项目记忆": "Project memory",
  "memory:编码偏好": "Coding preference",
  "memory:长期模式": "Long-term pattern",
  "memory:意图": "Intent",
  "memory:近 3 个月": "Past 3 months",
  "memory:近 6 个月": "Past 6 months",
  "memory:近 1 年": "Past year",
  "memory:全部历史": "All history",
  "memory:{count} 条这次没能整理成功，正在重试": "Processing retry · {count}",
  "memory:{count} 处理中": "Processing: {count}",
  "memory: · 处理中 {running} / 待派发 {active}":
    " · {running} processing / {active} queued",
  "memory: · {count} 条任务状态待核对": " · {count} tasks need status review",
  "memory: · {count} 条任务状态异常，正在恢复":
    " · {count} tasks have status issues; recovering",
  "memory:{count} 等待": "{count} waiting",
  "memory:最新 {count} 条待处理": "Latest {count} pending",
  "memory:历史积压 {count} 条": "{count} in backlog",
  "memory:（约 {count} 分钟）": " (about {count} minutes)",
  "memory:（约 {count} 小时）": " (about {count} hours)",
  "memory:（约 {count} 天）": " (about {count} days)",
  "memory:同步尝试 {count} 次": "{count} sync attempts",
  "memory:尚未尝试同步": "Sync not attempted",
  "memory: · 状态更新于 {date}": " · Updated {date}",
  "memory:HY Memory 当前没有这条记忆，仅重试同步不能补齐。":
    "HY Memory does not contain this memory; retrying sync cannot restore it.",
  "memory:同步需要可用的本机整理模型或本地嵌入授权。":
    "Sync requires an available local processing model or permission to use local embeddings.",
  "memory:这条记忆或模型版本已经变化，先刷新核对再决定下一步。":
    "This memory or model version has changed. Refresh and review it before deciding what to do.",
  "memory:这条记忆已改，搜索索引还没开始同步。":
    "This memory changed, but search index sync has not started.",
  "memory:最近一次同步未成功；可以先重试，结果仍不确定时再刷新核对。":
    "The last sync failed. Retry first; if the result is still uncertain, refresh and review it.",
  "memory:这条记忆已改，搜索索引还没跟上。":
    "This memory changed, but the search index has not caught up.",
  "memory:搜索里还是改之前那句话。":
    "Search still shows the text from before the correction.",
  "memory:重试同步": "Retry sync",
  "memory:配置整理模型": "Configure processing model",
  "memory:未提炼 · 在窗口外": "Not extracted · Outside the window",
  "memory:未提炼 · 排队中": "Not extracted · Queued",
  "wb.select": "Select",
  "memory:模型综合": "Model synthesis",
  "memory:原文摘录": "Original excerpt",
  "memory:没有命中": "No match",
  "memory:正在生成": "Generating",
  "memory:正在检索记忆": "Looking up memories",
  "memory: · 基于 {count} 条已确认记忆":
    " · Based on {count} confirmed memories",
  "memory: · 基于 {count} 条已确认记忆.one":
    " · Based on {count} confirmed memory",
  "memory:整理需要处理": "Processing needs attention",
  "memory:正在整理记忆": "Processing memories",
  "memory:整理状态": "Processing status",
  "memory:重新整理": "Process again",
  "memory:自动提炼范围": "Automatic extraction window",
  "memory:手动提炼下一批": "Extract next batch manually",
  "memory:刷新核对": "Refresh and check",
  "memory:正在导入历史会话，记忆库暂时只显示一部分；导入完成后会自动补全，不需要重试。":
    "Importing conversation history. Only some memories are visible; the list will fill in automatically after import.",
  "memory:选择：{content}": "Select: {content}",
  "memory:处理失败：{content}": "Failed to process: {content}",
  "memory:已选 {count} 条": "{count} selected",
  "memory:已选 {count} 条.one": "{count} selected",
  "memory:删除所选 {count} 条": "Delete {count} selected",
  "memory:删除所选 {count} 条.one": "Delete {count} selected",
  "memory:重试失败的 {count} 条": "Retry {count} failed items",
  "memory:重试失败的 {count} 条.one": "Retry {count} failed items",
  "memory:没有{status}的记忆。": "No {status} memories.",
  "memory:已{action} {count} 条": "Action completed ({action}): {count} items",
  "memory:已{action} {count} 条.one":
    "Action completed ({action}): {count} item",
  "memory:，{count} 条未完成": ", {count} incomplete",
  "memory:，{count} 条未完成.one": ", {count} incomplete",
  "memory:未能{action}：{error}": "Could not {action}: {error}",
  "memory:重试{action}": "Retry {action}",
  "memory:删除（操作）": "Delete",
  "memory:处理（操作）": "process",
  "memory:{count} 条原话": "{count} original excerpts",
  "memory:{count} 条原话.one": "{count} original excerpts",
  "memory:有 {count} 段对话的整理结果不确定":
    "Processing results for {count} conversations are uncertain",
  "memory:有 {count} 段对话的整理结果不确定.one":
    "Processing results for {count} conversations are uncertain",
  "memory:{count} 次整理已完成": "{count} processing runs completed",
  "memory:{count} 次整理已完成.one": "{count} processing runs completed",
  "memory: · {count} 条在窗口外（原文可搜）":
    " · {count} outside the window (original text searchable)",
  "memory: · {count} 条在窗口外（原文可搜）.one":
    " · {count} outside the window (original text searchable)",
  "memory: · 已规划 {planned} 批，当前第 {current} 批":
    " · {planned} batches planned, now on batch {current}",
  "memory:{kind}卡在 {reason}（{count} 条）":
    "{kind} blocked by {reason} ({count})",
  "memory:已加载 {count} 条记忆": "Loaded {count} memories",
  "memory:已加载 {count} 条记忆.one": "Loaded {count} memories",
  "memory:回答只使用已确认记忆。每个答案都可以回到对应记忆和来源原话。":
    "Answers use only confirmed memories. Each answer links back to the memory and original text.",
  "memory:选择一条记忆": "Select a memory",
  "memory:查看它的来源、使用范围和修改历史。":
    "See its sources, access scope and change history.",
  "memory:查看替代它的记忆": "View the memory that replaced it",
  "memory:查看它取代的旧记忆": "View the memory it replaced",
  "memory:保存纠正": "Save correction",
  "memory:修改历史": "Change history",
  "memory:已可用": "Available",
  "memory:处理中…": "Processing…",
  "memory:搜索": "Search",
  "memory:记忆治理状态": "Memory status",
  "memory:记忆空间模式": "Memory space modes",
  "memory:来源状态待核对，记忆库暂不可读取。":
    "Source status needs review; the memory library cannot be read yet.",
  "memory:记忆库读取失败，请重试。":
    "Could not load the memory library. Please retry.",
  "memory:重新读取记忆库": "Reload memory library",
  "memory:正在读取记忆库…": "Loading memory library…",
  "memory:批量操作结果": "Bulk action result",
  "memory:批量操作": "Bulk actions",
  "memory:取消选择": "Clear selection",
  "memory:向已确认记忆提问": "Ask confirmed memories",
  "memory:搜索记忆库": "Search memory library",
  "memory:问答记忆范围": "Memory question scope",
  "memory:只问个人画像": "Ask about personal profile only",
  "memory:问全部记忆（含项目）": "Ask all memories (including projects)",
  "memory:例如：我对真实数据有什么要求？":
    "For example: what are my requirements for real data?",
  "memory:搜索偏好、决定或经历": "Search preferences, decisions or experiences",
  "memory:根据记忆回答": "Answer from memories",
  "memory:清除搜索": "Clear search",
  "wb.finding.duplicate": "From the same original message",
  "wb.finding.duplicateText":
    "“{first}” and “{second}” came from the same original message and were assigned to {firstScope} and {secondScope}. Check whether they refer to the same thing.",
  "wb.finding.dormant": "Not updated recently",
  "wb.finding.dormantText":
    "{count} memories have not been changed or mentioned in two months. They may need review.",
  "wb.finding.blocked": "Waiting on a requirement",
  "wb.finding.blockedText":
    "{count} conversations are queued pending a requirement. Their original text is already saved and searchable.",
  "wb.finding.blockedText.one":
    "{count} conversations are queued pending a requirement. Their original text is already saved and searchable.",
  "wb.unclassified": "Uncategorized",
  "wb.profile": "Personal profile",
  "wb.project": "Project {id}",
  "wb.topics": "Topic",
  "wb.all": "All",
  "wb.topicCount": "{count} topics formed",
  "wb.topicCount.one": "{count} topics formed",
  "wb.singletons": "; {count} other tags appear only once",
  "wb.singletons.one": "; {count} other tag appears only once",
  "wb.memories": "{count} memories",
  "wb.memories.one": "{count} memories",
  "wb.sources": "{count} sources",
  "wb.sources.one": "{count} source",
  "wb.once":
    "This topic only appears here. It may be a one-off, or the same topic may have been split under different names.",
  "wb.graphTitle": "{label} relationship graph",
  "wb.readSummary": "Read a summary of this matter",
  "wb.summaryPrompt":
    "Explain “{label}” clearly: where things stand, what has been decided, and what remains open.",
  "wb.building": "Synthesizing…",
  "wb.build": "Build relationships with HY Memory",
  "wb.drawn": "Showing {shown} / {total} memories",
  "wb.askLabel": "Ask about this matter",
  "wb.askPlaceholder":
    "Ask about this matter; answers use only confirmed memories",
  "wb.ask": "Ask",
  "wb.originals": "Original conversations",
  "wb.evidenceNote":
    "This memory comes from {count} original excerpts below, ordered by time",
  "wb.evidenceNote.one":
    "This memory comes from {count} original excerpts below, ordered by time",
  "wb.pickCircle": "Select a circle on the graph to see its original text here",
  "wb.unavailable": "Source original text is unavailable",
  "wb.fullConversation": "View full conversation",
  "wb.you": "You",
  "wb.assistant": "Assistant",
  "wb.noMemories":
    "No memories yet. Search imported original conversations in Conversation history.",
  "wb.waiting":
    "Some conversations are queued pending a requirement. Their original text is saved and searchable.",
  "wb.conditions": "See what is missing",
  "wb.findings": "What your records reveal",
  "wb.findingsNote":
    "Computed from local records. Select one to highlight its supporting memories on the graph.",
  "wb.showGraph": "View on graph",
  "wb.loading": "Loading memories…",
  "wb.coreStarting":
    "It will appear automatically after local Core starts; no refresh needed.",
  "wb.nothingToConnect": "There is nothing to connect yet.",
  "wb.skeletonNote":
    "Authorize a source to see the source and topic structure; memories will appear as they are created.",
  "wb.authorize": "Authorize a source",
  "wb.conversations": "{count} conversations",
  "wb.conversations.one": "{count} conversation",
  "wb.chipChanges": "What has changed recently here?",
  "wb.chipSummarize": "Summarize these in a few points",
  "wb.unknownTime": "Time unknown",
  "wb.unknownSource": "Unknown source",
  "wb.transcriptGone": "This conversation is no longer in the library.",
  "wb.edgeCount": "{label}: {count} connections",
  "wb.edgeCount.one": "{label}: {count} connection",
  "wb.noRelations":
    "HY Memory has not found relationships yet. Use the button above to run System 2.",
  "wb.noModelRelations":
    "No usable model is configured; HY Memory relationships are unavailable.",
  "provenance.multiple": "Multiple authors",
  "provenance.person": "Original user message",
  "provenance.agent": "Original agent message",
  "provenance.unknown": "Unknown author",
  "provenance.imported": "Imported memory · {author}",
  "provenance.manual": "Added manually",
  "provenance.answered": "Created from an answer",
  "provenance.generated": "Created from an AI reply",
  "provenance.conversation": "Created from original conversations",
  "arrival.region": "Memory processing status",
  "arrival.importing": "Originals are still importing",
  "arrival.messages": "{count} messages saved",
  "arrival.messages.one": "{count} messages saved",
  "arrival.conversations": "{count} conversations",
  "arrival.conversations.one": "{count} conversation",
  "arrival.details": "View progress and details",
  "arrival.backlog":
    "Large amount of existing data; processing in batches in the background for quality",
  "arrival.items": "{count} items",
  "arrival.items.one": "{count} item",
  "arrival.configure": "Configure memory processing",
  "arrival.status": "View processing status",
  "arrival.history": "View saved conversations",
  "arrival.connect": "Connect an app",
  "arrival.retained": "Saved originals remain on this device.",
  "arrival.description.model-required":
    "Review the processing model before continuing.",
  "arrival.description.processing":
    "Processing recent conversations first; more memories will appear gradually",
  "arrival.description.importing":
    "Saving originals; import progress does not reflect memory creation progress.",
  "arrival.description.failed":
    "Check why it failed before deciding whether to retry.",
  "arrival.description.unavailable":
    "The last confirmed information is retained; check current progress again.",
  "arrival.description.refreshing":
    "Checking local records; unconfirmed progress is not counted.",
  "arrival.description.checking":
    "Saved content is retained; progress needs updating.",
  "arrival.description.stopped":
    "Processing has stopped; review its status before continuing.",
  "arrival.description.idle": "No processing task is running now.",
  "arrival.description.empty":
    "Connect an app or import a conversation file; originals will be saved on this device first.",
  "arrival.title.refreshing": "Saved content is retained; checking progress",
  "arrival.title.checking": "Progress needs updating",
  "arrival.title.importing": "Importing conversations",
  "arrival.title.processing": "Creating memories",
  "arrival.title.continue": "Needs attention",
  "arrival.title.model": "Processing model not configured",
  "arrival.title.empty": "No conversations saved yet",
  "handoff.region": "Take memories to another app",
  "handoff.answer": "Take answer and references to another app",
  "handoff.selected": "Take selected memories to another app",
  "handoff.title": "Take to another app",
  "handoff.closeTitle": "Close preview",
  "handoff.close": "Close handoff preview",
  "handoff.stats": "{count} memories · {characters} characters · Not truncated",
  "handoff.stats.one":
    "{count} memory · {characters} characters · Not truncated",
  "handoff.content": "Full content to take to another app",
  "handoff.target": "Destination app",
  "handoff.other": "Other app · Copy only",
  "handoff.copyOnly": " · Copy only",
  "handoff.boundary":
    "Only the content above is included, not the original conversations. Copying does not authorize a connection; copies in other apps cannot be recalled.",
  "handoff.copy": "Copy content",
  "handoff.open": "Copy and open {host}",
  "handoff.copyFailed":
    "Copy failed; no app was opened. Select and copy the text in the preview instead.",
  "handoff.opened":
    "Copied and requested to open {host}. Nothing was sent; check the destination input.",
  "handoff.notPrefilled":
    "Copied in full. {host} could not be prefilled; paste it in the destination app. Nothing was sent.",
  "handoff.openFailed":
    "Copied in full, but could not open {host}. Nothing was sent.",
  "handoff.copied": "{target} copied in full. Nothing was sent.",
  "handoff.pasteTarget": ", ready to paste into {host}",
  "wb.memory": "This memory",
  "wb.node": "This node",
  "wb.matter": "This matter",
  "wb.available": "Available",
  "wb.superseded": "Superseded",
  "wb.deleted": "Deleted",
  "wb.revision": "Version {revision}",
  "wb.collapse": "Collapse",
  "wb.oldSearch": "Search still shows the text from before the correction.",
  "wb.missingIndex":
    "HY Memory does not contain this memory; retrying cannot restore it.",
  "wb.retrySync": "Retry sync",
  "wb.correctForm": "Correct memory content",
  "wb.content": "Memory content",
  "wb.correct": "Correct",
  "wb.saveCorrection": "Save correction",
  "wb.cancelEdit": "Cancel editing",
  "wb.undo": "Undo last change",
  "wb.delete": "Delete",
  "wb.nodeNote":
    "This is not a memory; memories connect to it. {direct} direct links; {indirect} reachable in two hops.",
  "wb.direct": "Direct",
  "wb.twoHops": "Two hops",
  "wb.idle":
    "Circles represent memories; lines show those mentioned in the same conversation. Select one to see its origins and edit it. Choosing another matter on the left updates the graph.",
  "wb.searching": "Searching memories…",
  "wb.generated": "Model synthesis",
  "wb.extractive": "Original excerpt",
  "wb.noHit": "No match",
  "wb.answerSource": "{mode} · Based on {count} confirmed memories",
  "wb.answerSource.one": "{mode} · Based on {count} confirmed memories",
  "wb.recalled": "{count} memories retrieved",
  "wb.recalled.one": "{count} memories retrieved",
  "wb.saveMemory": "Save as memory",
  "wb.tooLong":
    "This answer exceeds the current save limit and cannot be saved as a memory.",
  "wb.summary":
    "This is a summary, not a memory. It is rebuilt each time so it stays consistent with the memories below.",
  "wb.thisAnswer": "This answer",
  "wb.type.source": "Source app",
  "wb.type.topic": "Topic",
  "wb.type.schema": "HY synthesis",
  "wb.type.memory": "Memories",
  "wb.origin.hy_relation": "HY Memory synthesis",
  "wb.origin.same_conversation": "Same conversation",
  "wb.origin.shared_tag": "Same topic",
  "wb.origin.provenance": "Original-text source",
  "wb.relation.evidence_source": "From",
  "wb.relation.same_conversation": "Same conversation",
  "wb.relation.shared_tag": "Same topic",
  "wb.relation.derived_from": "Synthesized from",
  "wb.relation.tagged_with": "Tagged with",
  "wb.relation.related_to": "Related",
  guideEyebrow: "Try it out",
  guideTitle: "Pick up where your last conversation left off",
  guideBack: "Back to memory",
  guideChooseScene: "Choose a scenario",
  guideSame: "Continue in a new conversation",
  guideSameDescription:
    "Find earlier decisions in a new conversation with the same app.",
  guideSwitch: "Continue in another app",
  guideSwitchDescription:
    "Same work, different AI. Bring over the relevant context first.",
  guideNew: "Help a new tool understand me",
  guideNewDescription:
    "Start with one detail you choose to share instead of introducing yourself again.",
  guideReadError: "Could not read memories right now. Try again later.",
  guideDetailError:
    "This memory cannot be read right now. Choose another or refresh.",
  guideChanged:
    "The memory or its permissions changed. Refresh and review it again.",
  guideConnectionChanged:
    "Connection status changed; nothing was copied. Choose a method again.",
  guideCopiedOpened:
    "Copied and requested to open {host}. Nothing was sent; check the input field.",
  guideCopiedNotFilled:
    "Copied in full, but could not prefill. Paste it in the destination app; nothing was sent.",
  guideCopiedNotOpened:
    "Copied in full, but could not open the destination app. Nothing was sent.",
  guideCopied: "Copied in full. Nothing was sent or granted new read access.",
  guideValidationError:
    "Could not verify the current memory or permissions. The old preview was hidden; refresh and retry.",
  guideCopyError:
    "Could not verify the latest content or finish copying. Retry; the destination app was not opened.",
  guideManual: "Added in the app",
  guideOriginalApp: "Source app",
  guideListSeparator: ", ",
  guideSaved: "{count} original messages saved on this device · ",
  "guideSaved.one": "{count} original messages saved on this device · ",
  guideReadingMemories: "Loading processed memories",
  guideLoaded: "{count} usable memories loaded on this page",
  "guideLoaded.one": "{count} usable memories loaded on this page",
  guideSeparate: ". These stages are counted separately.",
  guideChooseBackground: "Choose context",
  guideCheckSources: "Review sources",
  guideTry: "Try in a new conversation",
  guideLoading: "Loading existing memories on this device…",
  guideEmptyTitle: "Add one useful piece of context first",
  guideEmptyDescription:
    "No memories are available for this trial. Imported originals are not processed memories, and suggestions are not shared automatically.",
  guideAdd: "Add a memory",
  guideConnections: "View imports and connections",
  guideRefresh: "Refresh",
  guideBackgroundTitle: "Context for this trial",
  guideRefreshExample: "Refresh example",
  guideChooseMemory: "Select a memory",
  guideLoadedScope:
    "Choose from {count} usable memories loaded here; this is not your entire history.",
  "guideLoadedScope.one":
    "Choose from {count} usable memories loaded here; this is not your entire history.",
  guideCheckingEvidence: "Checking original evidence…",
  guideRevision:
    "{source} · Version {revision} · System-generated, needs your review",
  guideScope: "Scope: ",
  guideAllApps: "Available across apps; not necessarily a personal preference",
  guidePrivate:
    "External recall is off; sharing manually requires a separate decision",
  guideLimited: "Limited scope; external recall has not been expanded",
  guideEvidence: "Original evidence · {count} excerpts",
  "guideEvidence.one": "Original evidence · {count} excerpts",
  guideUnknownSource: "Unknown source",
  guideYou: "You",
  guideUnknownAuthor: "Author unknown",
  guideUnknownTime: "Time unknown",
  guideUnavailable:
    "The original text or current permission is unavailable. Choose another memory or refresh; this content will not be shared.",
  guideDraftTitle: "Opening for the next conversation",
  guideChooseApp: "Choose an app to try",
  guideCopyOnly: "Choose an app yourself · Copy only",
  guideHostsError:
    "Could not read connection status. Retry or copy the context only.",
  guideNoApp:
    "No connected app selected. Sharing manually does not install a connection.",
  guideWired: "Last self-check succeeded; verify it works for this use.",
  guideInstalled: "Connection installed; read access has not been verified.",
  guideNotInstalled: "Read connection not installed.",
  guideManage: "Manage app connections",
  guideChooseRoute: "Choose how to bring context in",
  guideContext: "Bring in context",
  guideQuery: "Let the app search",
  guideTopic: "Topic to continue",
  guideQueryHint:
    "Only send a query; do not place the expected answer above into the new conversation.",
  guideNewHint:
    "Only share the memory you chose; no personal profile is created automatically.",
  guideContextHint:
    "Include this context and its citations; this is not an automatic recall test.",
  guidePreview: "Full opening preview",
  guidePreviewPlaceholder:
    "The opening will appear when original evidence is available",
  guideChecking: "Checking…",
  guideCopy: "Copy opening",
  guideCopyOpen: "Copy and open {host}",
  guideNoReceipt:
    "No receipt from an external call this time. Did the new conversation cite the right context?",
  guideVerified: "I reviewed the result",
  guideMissing: "Could not find it",
  guideFeedbackOk:
    "Your feedback: reviewed. This is not an automated verification record.",
  guideFeedbackMissing:
    "Your feedback: not found. Check source imports, memory scope and app connections first.",
  guideInvitation: "Invitation to try it out",
  guideInvitationHint:
    "Try continuing a new conversation with one of your own memories.",
  guideTryOnce: "Try it",
  guideLater: "Maybe later",
  guideDismiss: "Dismiss invitation",
  healthTitle: "Health diagnostics",
  healthLocal: "Local service",
  healthBackground: "Background service",
  healthSources: "Totals by source",
  healthProcessing: "Memory processing",
  healthHosts: "AI connections",
  healthDetailed: "Detailed logging",
  healthDetailedTitle: "Detailed logging (for troubleshooting)",
  healthDetailedHint:
    "Off by default. Turn this on only when support asks you to troubleshoot.",
  healthDetailedError: "Could not change detailed logging. Please retry.",
  healthRefresh: "Refresh status",
  healthIncident: "Mark issue time",
  healthIncidentDesktop: "Mark the issue time in the desktop app.",
  healthIncidentPrompt: "Briefly describe the issue (optional)",
  healthIncidentCopied: "Issue ID copied: {id}",
  healthIncidentError: "Could not mark the issue time. Please retry.",
  healthReadFailed: "Temporarily unavailable",
  healthStale: " · Data is outdated; showing the last result.",
  healthReading: "Reading status…",
  healthRereading: "Refreshing; showing the last successful reading…",
  healthReadSuccessTime: "Last successful reading",
  healthReadFailedTime: "Read failed at",
  healthReadingSources: "Reading local service and source status…",
  healthPrevious: "Last read",
  healthPreviousPrefix: "Last read: ",
  healthRunning: "Running",
  healthCoreVersion: "{state} · Version {version} · Instance {instance}",
  healthCoreDisconnected: "Local Core is not connected yet.",
  healthBackgroundUnavailable:
    "Cannot check the background service here. Use a supported desktop app.",
  healthRuntime: "Core: {core} · Host bridge: {broker}",
  healthSourceCount: "{conversations} conversations · {messages} messages",
  healthNoSources: "No connected sources yet.",
  healthTaskCounts:
    "{running} processing · {pending} queued · {completed} completed · {failed} failed",
  healthImportDetails: "Import and processing details",
  healthRawTurns: "Original text and complete turns",
  healthRawCounts: "{raw} originals · {turns} complete turns",
  healthHy: "HY processing",
  healthHyCounts:
    "{calls} calls · {completed} completed · {unknown} uncertain · {failed} failed · {cancelled} cancelled",
  healthOutput: "Output",
  healthMemories: "{count} memories",
  "healthMemories.one": "{count} memories",
  healthWorkbuddy: "WorkBuddy memory lookups",
  healthWorkbuddyCounts:
    "{injected} injections · {timedOut} timeouts · {unavailable} unavailable",
  healthBlocked: "{kind} blocked by {reason} ({count})",
  healthRetrying: "Processing retry · {count}",
  "healthRetrying.one": "Processing retry · {count}",
  healthNoIssues: "No issues need attention.",
  healthHostsUnavailable:
    "Cannot read this in a browser; check the desktop app.",
  healthNoHosts: "No supported AI tools detected on this device.",
  healthBackgroundHealthy: "Background service available",
  healthBackgroundStopped: "Background service stopped",
  healthBackgroundDegraded: "Background service partially unavailable",
  healthBackgroundUntrusted:
    "Background service identity could not be verified",
  healthBackgroundUnknown: "Background status not confirmed",
  healthStopped: "Stopped",
  healthUnhealthy: "Cannot connect",
  healthUntrusted: "Identity not verified",
  healthUnknown: "Not confirmed",
  healthNotChecked: "Not checked",
  healthWired: "Connected",
  healthBlind: "Installed, but cannot retrieve memories",
  healthUnreachable: "Not connected",
  startupReadError: "Could not read startup status. Please retry.",
  startupRetry: "Retry",
  startupOpening: "Opening MemHub…",
  nativeHelp: "Help",
  nativeExportLogs: "Export logs for support",
  nativeExportError: "Could not export logs. Please retry later.",
  nativeLogin: "Launch {product} at login",
  nativeTrialUnavailable: " (unavailable in this build)",
  nativeCliManaged: " (managed by CLI)",
  nativeStatusUnavailable: " (status unavailable)",
  nativeDevelopmentUnavailable: " (unavailable in development)",
  nativeOpen: "Open {product}",
  nativeQuit: "Quit {product} (background service keeps running)",
  nativeLoginError: "Could not change the login startup setting. Please retry.",
  nativeMoveTitle: "Move MemHub to Applications first",
  nativeMoveDetail:
    "Reopen MemHub from Applications to connect other apps and install the command-line tool.",
  nativeAcknowledge: "OK",
  graphWidthTitle: "Existing memory library does not match the built-in model",
  graphWidthDescription:
    "Your memory library uses {stored} dimensions, but the built-in model produces {expected}. They cannot work together; extraction will keep failing until you choose an option.",
  graphWidthKeep:
    "Keep existing library (default): Continue using the previous embedding configuration; all {count} memories and the graph stay unchanged",
  graphWidthRebuild:
    "Use the built-in model: Archive (do not delete) the existing graph; relationships for {count} memories will need rebuilding",
  graphWidthExport:
    "Export first, then decide: Leave everything unchanged for now and choose again after exporting",
  graphWidthExported:
    "Exported to {path}. The existing library was unchanged; you can choose again.",
  graphWidthUnknown:
    "The dimension choice is unconfirmed. The existing memory library was unchanged; please retry.",
  graphWidthConfirm: "Confirm choice",
  importUserProgress:
    "Importing your conversation history. {count} conversations imported. Memories will appear gradually; you can keep using the app.",
  "importUserProgress.one":
    "Importing your conversation history. {count} conversations imported. Memories will appear gradually; you can keep using the app.",
  importUserModel:
    "The model is not configured yet. New conversations will not be processed into memories for now.",
  importUserIncomplete: "Some content was not fully processed. Check Settings.",
  importUserSettings: "Go to Settings",
  diagnosticExportDesktop: "Export diagnostics from the desktop app.",
  diagnosticExported: "Logs exported: {filename}",
  diagnosticFile: "diagnostics file",
  diagnosticExportFailed: "Could not export diagnostics. Please retry.",
  debugTitle: "Debug logs",
  debugCount: "{count} logs from this session",
  "debugCount.one": "{count} logs from this session",
  debugDiagnostics: "Diagnostics",
  debugUsageSubtitle: "Memory processing model usage",
  debugTabs: "Diagnostics tabs",
  debugLogsTab: "Logs",
  debugUsageTab: "Model usage",
  debugFullscreen: "Fullscreen",
  debugExitFullscreen: "Exit fullscreen",
  debugFullscreenLabel: "Show logs fullscreen",
  debugExitFullscreenLabel: "Exit fullscreen logs",
  usageRange15m: "Last 15 minutes",
  usageRange1h: "Last hour",
  usageRange6h: "Last 6 hours",
  usageRange24h: "Last 24 hours",
  usageRange7d: "Last 7 days",
  usageRange: "Time range",
  usageRangeLabel: "Model usage time range",
  usageLoading: "Loading…",
  usageReadError: "Could not load",
  usageLive: "Refresh every 5 seconds",
  usageTotalTokens: "Total tokens",
  usageRequests: "Requests",
  usageChartRegion: "Tokens per minute by prompt",
  usageChartTitle: "Token usage by prompt",
  usageChartSubtitle: "Per minute · Total tokens",
  usageBreakdown: "Prompt details",
  usageCurrentRange: "Current time range",
  usageRequestCount: "{count} requests",
  "usageRequestCount.one": "{count} requests",
  usageEmpty: "No memory processing model calls in this time range",
  usageNoChart: "No chart data yet",
  usageChartAria: "Token usage per minute by prompt",
  debugPause: "Pause live refresh",
  debugResume: "Resume live refresh",
  debugRefresh: "Refresh now",
  debugClose: "Close Debug logs",
  debugSearchPlaceholder: "Search events, components, error codes or content",
  debugSearch: "Search logs",
  debugSource: "Log source",
  debugAllSources: "All sources",
  debugCapture: "Capture level",
  debugDisplay: "Display level",
  debugAll: "All",
  debugMatches: "{count} matches",
  "debugMatches.one": "{count} matches",
  debugLive: "Refreshing live",
  debugPaused: "Paused",
  debugTruncated: "Too many logs; truncated",
  debugReadError: "Could not load",
  debugLoading: "Loading logs…",
  debugEmpty: "No matching logs",
  debugOpened: "Debug logs are open",
  debugOpen: "Open Debug logs",
  "这里需要 API Key，不是网址。请填写服务方提供的密钥。":
    "Enter the API key provided by the service, not a URL.",
  "服务商拒绝了这个 API Key（401）。请确认密钥属于当前填写的服务地址。":
    "The provider rejected this API key (401). Make sure the key belongs to the endpoint entered.",
  "服务商没有这个模型名，请对照服务商的模型列表核对。":
    "The provider does not recognize this model name. Check the provider's model list.",
  "连不上接口地址，请检查网络和服务地址是否正确。":
    "Could not connect to the endpoint. Check the network and service address.",
  未提供接口地址: "No endpoint provided",
  "请填写生成模型名。": "Enter the generation model name.",
  "请填写完整的 http:// 或 https:// 接口地址。":
    "Enter a complete http:// or https:// endpoint URL.",
  "请填写向量模型名。": "Enter the embedding model name.",
  "请填写完整的向量模型接口地址。":
    "Enter the complete embedding model endpoint URL.",
  "这份配置在测试期间被改过，刚才测的是改动后的版本。配置已保存但未启用，请重新测试并启用。":
    "This configuration changed during testing; the newer version was tested. It was saved but not enabled. Test and enable it again.",
  "测试结果来自这份配置更早的版本。配置已保存但未启用，请重新测试并启用。":
    "The test result belongs to an earlier version of this configuration. It was saved but not enabled. Test and enable it again.",
  "配置已保存，连接测试未通过。请核对配置后重试，历史对话尚未发送。":
    "Configuration saved, but the connection test failed. Review it and retry. Conversation history was not sent.",
  "配置已保存，尚未确认启用成功。请核对配置后重试。":
    "Configuration saved, but enabling is unconfirmed. Review it and retry.",
  "配置尚未完成测试和启用，请核对后重试。":
    "Testing and enabling are incomplete. Review the configuration and retry.",
  "配置尚未保存成功，已填写内容保留，可核对后重试。":
    "Could not confirm saving the configuration. Your entries are retained; review and retry.",
  "只需配置生成模型。向量嵌入已内置在本机，不为 System 1、System 2 分开选模型。":
    "Only the generation model needs configuring. Vector embeddings are built in locally; System 1 and System 2 do not need separate models.",
  "无法读取测试恢复记录，暂不发送新的测试。":
    "Could not read the test recovery record; no new test will be sent.",
  "上次模型测试结果待核对，暂不修改或启用配置。":
    "The previous model test needs review; configuration will not be edited or enabled yet.",
  核对测试结果: "Review test result",
  "上次模型创建结果待核对，暂不开始新的操作。":
    "The previous model creation needs review; no new operation will start yet.",
  核对创建结果: "Review creation result",
  "配置已保存，正在测试连接…": "Configuration saved; testing connection…",
  "连接通过，正在启用模型…": "Connection passed; enabling model…",
  刷新配置列表: "Refresh configurations",
  自定义一个模型: "Configure a custom model",
  已保存的模型配置: "Saved model configuration",
  "已保存，已启用": "Saved and enabled",
  "已保存，连接通过": "Saved; connection passed",
  "已保存，测试未通过": "Saved; test failed",
  "已保存，待测试": "Saved; awaiting test",
  版本: "Version",
  "需要允许 MemHub 访问钥匙串。系统询问时请选择“始终允许”；已保存的配置不会丢失。":
    "Allow MemHub to access Keychain. Choose ‘Always Allow’ when prompted; saved configurations will not be lost.",
  "测试只向此接口发送测试内容，不发送历史对话。":
    "Tests send only test content to this endpoint, not conversation history.",
  重试测试并启用: "Retry test and enable",
  测试并启用: "Test and enable",
  编辑配置: "Edit configuration",
  新增配置: "Add configuration",
  编辑: "Edit",
  "。修改后需重新测试并设为当前模型，内容授权不会自动更新。":
    ". After making changes, test again and set it as the current model. Content permissions will not update automatically.",
  "配置已在其他位置变化，草稿已保留。请取消编辑后重新打开核对。":
    "Configuration changed elsewhere; your draft is retained. Cancel editing and reopen to review.",
  服务商预设: "Provider preset",
  自定义兼容服务: "Custom compatible service",
  "只是帮你填好接口地址和一个模型名，不代表这个模型当前可用。 请对照服务商的模型列表核对后再保存。":
    "This fills in an endpoint and model name, but does not guarantee availability. Check the provider's model list before saving.",
  协议: "Protocol",
  "与模型服务提供的接口协议保持一致。":
    "Match the protocol provided by the model service.",
  配置名称: "Configuration name",
  "用于区分配置，不影响模型请求。":
    "Identifies the configuration; does not affect model requests.",
  生成模型名: "Generation model name",
  "例如：gpt-4o-mini…": "For example: gpt-4o-mini…",
  "填写服务方提供的完整模型标识。":
    "Enter the full model ID provided by the service.",
  "高级：自行配置向量模型": "Advanced: configure your own embedding model",
  向量配置模式: "Embedding configuration mode",
  本地内置: "Built-in local model",
  自定义向量模型: "Custom embedding model",
  向量模型名: "Embedding model name",
  "例如：text-embedding-3-small…": "For example: text-embedding-3-small…",
  向量模型来源: "Embedding model provider",
  "本机 Ollama": "Local Ollama",
  "同源 OpenAI-compatible（共用 API Key）":
    "Same-origin OpenAI-compatible (shared API key)",
  "向量模型 Base URL": "Embedding model Base URL",
  "为避免凭据外泄，向量地址必须与生成模型同源。":
    "To protect credentials, the embedding endpoint must share the generation model's origin.",
  "使用内置本地向量模型；不会发送内容给远程向量服务。":
    "Uses the built-in local embedding model; no content is sent to a remote embedding service.",
  自定义向量: "Custom embedding",
  尚未填写模型: "No model entered",
  尚未填写地址: "No address entered",
  "填写服务接口地址，不是聊天网页地址。":
    "Enter the API endpoint, not the chat website URL.",
  "（可选）": " (optional)",
  "保存后不会再次显示…": "Will not be shown again after saving…",
  "留空保留已保存密钥；填写新密钥后需重新测试和启用。":
    "Leave blank to keep the saved key; test and enable again after entering a new key.",
  "换了服务商后需要填写新的 API Key，已保存的密钥不会带到新地址。":
    "Enter a new API key after changing providers; the saved key is not sent to the new endpoint.",
  "保存在系统安全存储中，不随安装包分发。":
    "Stored in the system's secure storage; not distributed with the app.",
  "关闭思考链（向支持的厂商下发 thinking-off 参数）":
    "Disable chain of thought (send thinking-off to supported providers)",
  保存修改: "Save changes",
  保存配置: "Save configuration",
  "正在启用配置…": "Enabling configuration…",
  测试并完成配置: "Test and complete setup",
  保存并测试启用: "Save, test and enable",
  取消编辑: "Cancel editing",
  "密钥保存在系统安全存储；测试仅发送测试内容。":
    "The key is stored securely; tests send only test content.",
  当前使用: "In use",
  未启用: "Not enabled",
  测试目标: "Test target",
  "。仅发送测试内容，不发送历史对话。":
    ". Only test content is sent, not conversation history.",
  测试连接: "Test connection",
  设为当前模型: "Set as current model",
  删除: "Delete",
  "粘贴 API Key": "Paste API Key",
  "密钥尚未保存成功，已填写内容保留，可重试。":
    "Could not save the key. Your input is retained; you can retry.",
  生成接收方: "Generation recipient",
  向量接收方: "Embedding recipient",
  本机: "On this device",
  "。启用此配置后按上方选择处理历史及未来允许来源。":
    ". After enabling this configuration, the selections above determine which past and future authorized sources are processed.",
  "保存 key": "Save key",
  "还没有 key？去申请": "No key yet? Apply for one",
  删除这张卡: "Remove this card",
  "密钥已配置，当前读取状态待确认。":
    "Key configured; access status needs confirmation.",
  "等待 key：其余都已经填好了，贴上 key 就能用。":
    "Waiting for a key. The other settings are ready; paste your key to continue.",
  "key 已保存，还没测过这个模型能不能用。":
    "Key saved; this model has not been tested yet.",
  "Ollama（本机）": "Ollama (local)",
  "内置 bge-small-zh-v1.5": "Built-in bge-small-zh-v1.5",
  通过: "Passed",
  "HY 托管模型尚不可用": "HY hosted model unavailable",
  "缺少 API Key": "API Key missing",
  服务不可达: "Service unreachable",
  "认证失败；更新 key 后再检测，会自动接着处理":
    "Authentication failed; update the key and test again to resume processing.",
  请求频率受限: "Rate limited",
  "模型账户余额不足；充值或换 key 后再检测，会自动接着处理":
    "Model account balance is insufficient; top up or change the key and test again to resume.",
  没有找到模型: "Model not found",
  "该网关不提供模型列表，已改由生成调用验证":
    "The gateway has no model list; verified through a generation request instead.",
  服务返回不符合协议: "Service response does not match the protocol",
  结构化输出未通过: "Structured output test failed",
  "需要填写 Embedding 模型": "Enter an embedding model",
  "Embedding 模型不可用": "Embedding model unavailable",
  "请先核对上次模型测试结果，暂不修改或启用配置。":
    "Review the previous model test first; configuration will not be changed or enabled yet.",
  "本地恢复记录已变化，仍保持锁定。请重新打开后核对。":
    "The local recovery record has changed. Configuration remains locked; reopen and review it.",
  测试结果: "Test results",
  "暂不支持一键插入：它没有 MCP 通道，无法发放可撤销的记忆凭证。":
    "One-click connection is unavailable: this app has no MCP channel for a revocable memory credential.",
  应用控制通道暂不可用: "The app control channel is unavailable",
  appFailureReason: " Reason: {reason}. ",
  appFailureReread:
    "The connection status could not be reloaded either; the displayed state may be outdated.",
  模型: "Model",
  连接其他应用: "Connect other apps",
  帮助与诊断: "Help and diagnostics",
  设置分组: "Settings sections",
  记忆模型: "Memory model",
  当前模型: "Current model",
  "正在读取模型…": "Loading model…",
  模型状态暂不可读: "Model status is unavailable",
  尚未配置模型: "No model configured",
  修改模型: "Edit model",
  配置模型: "Configure model",
  刷新模型状态: "Refresh model status",
  "本机 / 受管端点": "Local / managed endpoint",
  接口地址待核对: "Endpoint needs review",
  "模型状态未能读取，请重试。":
    "Could not read the model status. Please retry.",
  "模型状态刷新未成功，仍显示上次确认的模型状态；请重试。":
    "Could not refresh the model status. Showing the last confirmed state; please retry.",
  "上次模型测试或创建还待核对，配置暂时不能改动。请先在下方模型配置区核对结果； 现有配置和原文不会被改动。":
    "A previous model test or creation needs review. Check the result below before editing. Your configuration and original text are unchanged.",
  用模型沉淀记忆: "Create memories with a model",
  "开启后，导入和同步到本机的会话会交给当前模型整理成记忆，会产生模型调用。 停止后不再调用模型、不再整理记忆，但会话仍会照常导入和保存；已排队的内容会留着，重新开启后继续。 模型配置始终保留，重新开启不需要重新填写。":
    "When on, imported and synced conversations are processed into memories by the current model, using model calls. When off, conversations are still imported and saved; queued work resumes when you turn it on again. Your model configuration stays in place.",
  "启用未能确认，仍显示上次确认的状态；请重试。":
    "Could not confirm enabling. Showing the last confirmed state; please retry.",
  "停止未能确认，模型可能仍在整理记忆，请重试。":
    "Could not confirm stopping. The model may still be processing memories; please retry.",
  "当前安装还不能读取这个开关，请确认后台服务已启动后重试。":
    "This installation cannot read the switch. Check that the background service is running and retry.",
  关于: "About",
  "此应用还没有读取授权，请先授权它的对话目录。":
    "This app has no read permission yet. Authorize its conversation directory first.",
  "此应用的读取授权已撤销，请重新授权。":
    "This app's read permission was revoked. Authorize it again.",
  "此应用的对话目录已被重建，请重新授权。":
    "This app's conversation directory was rebuilt. Authorize it again.",
  "此应用的对话目录现在读不到，请确认应用已安装并产生过对话。":
    "This app's conversation directory cannot be read. Check that the app is installed and has conversations.",
  "此应用的对话目录已变更，请按最新目录重新授权。":
    "This app's conversation directory changed. Authorize the new directory.",
  "此应用还没有读取授权，请先授权它的对话目录。完成后再开启持续同步。":
    "This app does not have read access yet. Authorize its conversation directory, then enable continuous sync.",
  "此应用的读取授权已撤销，请重新授权。完成后再开启持续同步。":
    "This app's read access was revoked. Authorize it again, then enable continuous sync.",
  "此应用的对话目录已被重建，请重新授权。完成后再开启持续同步。":
    "This app's conversation directory was rebuilt. Authorize it again, then enable continuous sync.",
  "此应用的对话目录现在读不到，请确认应用已安装并产生过对话。完成后再开启持续同步。":
    "This app's conversation directory cannot be read. Make sure the app is installed and has conversations, then enable continuous sync.",
  "此应用的对话目录已变更，请按最新目录重新授权。完成后再开启持续同步。":
    "This app's conversation directory changed. Authorize the new directory, then enable continuous sync.",
  "此应用的读取授权已失效，请重新授权后再开启持续同步。":
    "This app's read access expired. Authorize it again before enabling continuous sync.",
  "此应用的授权或目录已变化，已按最新状态显示，请确认后再试。":
    "This app's permissions or directory changed. Review the updated status and try again.",
  模型配置表单: "Model configuration form",
  "部分运行状态暂时无法读取，当前显示最后读到的状态，请重试。":
    "Some status information is unavailable. Showing the last known state; please retry.",
  "当前安装还不能设置登录后后台启动。":
    "This installation cannot configure startup at login.",
  "登录后后台启动设置未能确认，请重试。":
    "Could not confirm the login startup setting. Please retry.",
  "当前安装还不能退出界面，请从菜单栏退出。":
    "This installation cannot quit the window. Use the menu bar instead.",
  "退出界面未能确认，请重试。": "Could not confirm quitting. Please retry.",
  登录后启动后台服务: "Start background service at login",
  "无需打开界面，持续同步、整理和提供记忆。":
    "Keep syncing, processing and providing memories without opening the app.",
  导出日志: "Export logs",
  "把本机运行日志打包成一个文件，发给开发排查。":
    "Package local diagnostic logs into a file to share with support.",
  "确定退出 One Memory 界面吗？": "Quit the MemHub window?",
  "后台服务会继续同步、整理和提供记忆。":
    "The background service will continue syncing, processing and providing memories.",
  取消: "Cancel",
  退出界面: "Quit window",
  应用详情: "App details",
  返回应用: "Back to apps",
  同步开关: "Sync switch",
  "开启后处理历史待办，并持续接收此应用的新会话。":
    "Process pending history and keep receiving new conversations from this app when on.",
  "正在读取已有对话，首次开启可能需要一两分钟，请不要重复点击。":
    "Reading existing conversations. The first run may take a minute or two; please do not click again.",
  "将补充冻结期间的新对话；原有 pending Job 继续处理。":
    "Conversations received while frozen will be backfilled; existing pending jobs keep processing.",
  一键插入应用: "One-click app connection",
  "安装记忆连接，并默认允许此应用使用已授权记忆。":
    "Install the memory connection and allow this app to use authorized memories by default.",
  允许使用记忆: "Allow memory access",
  连接状态: "Connection status",
  已接通: "Connected",
  "已安装，但取不到记忆": "Installed, but cannot retrieve memories",
  未接通: "Not connected",
  状态待核对: "Status needs review",
  检测中: "Checking",
  未开启关联: "Connection off",
  "正在开启处理…": "Starting processing…",
  "正在冻结处理…": "Freezing processing…",
  处理中: "Processing",
  已冻结: "Frozen",
  "此应用的处理开关结果未能确认，其他应用不受影响。":
    "Could not confirm the processing switch for this app. Other apps are unaffected.",
  "暂时无法刷新来源状态，请重试；未更改同步或读取权限。":
    "Could not refresh source status. Retry; sync and read permissions were not changed.",
  "暂时无法读取连接状态，当前状态待核对；请重试。":
    "Could not read connection status. The current state needs review; please retry.",
  "已接通。已经打开的对话不会中途出现这个工具，请新开一轮。":
    "Connected. The tool will not appear in a conversation already open; start a new one.",
  "本次连接探测失败，当前状态待核对；未把上次读取当作本次成功。":
    "This connection check failed. The current state needs review; the previous result was not treated as a new success.",
  上次读取: "Last read",
  appSettingsTitle: "{name} app settings",
  appMemoryScope:
    "Allow {name} to look up memories within its authorized scope.",
  appRestart:
    "The memory tool is not in {name}'s current conversation. Fully quit and reopen {name}; it will appear in a new conversation.",
  appConnectFailed:
    "One-click connection for this app did not complete. Other apps are unaffected. {reason}",
  appDisconnectFailed:
    "The connection could not be removed, and memory access could not be revoked. {name} can still use memories; please retry. {reason}",
  appPermissionFailed:
    "Memory access for this app could not be confirmed. Please retry. {reason}",
  appRevokedModified:
    "Memory access was revoked, so {name} can no longer look up memories. The configuration file was modified, so MemHub did not delete it automatically; remove the MemHub connection manually.",
  appRevokedRetained:
    "Memory access was revoked, so {name} can no longer look up memories. The connection file was not removed; review it again.",
  appRevokedReview:
    "Memory access was revoked, so {name} can no longer look up memories. Full removal could not be confirmed; review these paths manually: {paths}.",
  appRemovalReview:
    "Full connection removal could not be confirmed. Review these paths manually: {paths}.",
  "Cursor 的连接凭证已更新。请完全退出并重新打开 Cursor，再开始新对话。":
    "Cursor's connection credential changed. Fully quit and reopen Cursor before starting a new conversation.",
  连接其他工具: "Connect other tools",
  "本机连接材料仅在桌面 App 中可用。":
    "Local connection details are only available in the desktop app.",
  "仅本机 MCP / 命令工具。纯网页聊天不支持直接连接本机。":
    "For local MCP or command-line tools only. Browser chat cannot connect directly to this device.",
  "列表内工具请用上方一键安装，此处仅供未列出的工具。":
    "Use one-click setup above for listed tools. This is for tools that are not listed.",
  "正在读取本机连接配置…": "Loading local connection details…",
  "未能读取连接配置。": "Could not load connection details.",
  重试: "Retry",
  连接材料格式: "Connection details format",
  连接提示词: "Connection prompt",
  "MCP 配置": "MCP configuration",
  命令: "Command",
  本机连接材料: "Local connection details",
  "包含本机路径，不含记忆和密钥。片段会运行本机 MemHub 连接程序；粘贴前请确认，MemHub 需保持运行。":
    "Contains local paths, but no memories or keys. The snippet runs the local MemHub connector; review it before pasting and keep MemHub running.",
  "已复制，尚未建立连接。": "Copied; no connection has been made yet.",
  "复制失败，可在上方选中文本复制。":
    "Copy failed. Select and copy the text above instead.",
  复制提示词: "Copy prompt",
  复制配置: "Copy configuration",
  复制命令: "Copy command",
  记忆库分页: "Memory library pages",
  上一页: "Previous page",
  下一页: "Next page",
  "把熟悉的背景，带到这里": "Bring your context here",
  "你的记忆，从这里接上": "Bring your memories together",
  "这张卡还没测过。点一次「设为当前模型」会先自动测，如果还是不行，说明端点、key 或模型名有一处不对。":
    "This configuration has not been tested. Setting it as the current model runs a test first; if it still fails, check the endpoint, key, and model name.",
  "这类模型接口暂时用不了，换一张卡。":
    "This type of model endpoint is not supported yet. Use another configuration.",
  "这张卡缺了向量化模型，补上才能启用。":
    "This configuration needs an embedding model before it can be enabled.",
  "这张卡已经不在了，可能刚被删掉。刷新一下。":
    "This configuration is no longer available. It may have been deleted; refresh the list.",
  "这张卡刚被改过。刷新一下再试，免得覆盖掉别处的改动。":
    "This configuration was just changed. Refresh before retrying to avoid overwriting another change.",
  onboardingModelOperationFailed: "Operation failed: {code}",
  onboardingModelSkipDestination: "Skip and go to Apps",
  首次设置进度: "First-time setup progress",
  导入历史: "Import history",
  连接应用: "Connect apps",
  沉淀记忆: "Create memories",
  本地存储: "Stored locally",
  "对话与记忆保存在本机；使用云模型或已授权的助手时，相关内容会发送给对应服务。":
    "Conversations and memories are stored on this device. Relevant content is sent to cloud models or authorized assistants when you use them.",
  "来源位置、身份或状态已变化，请重新选择并核对。尚未提交本次授权。":
    "The source location, identity or status changed. Select and review it again. This authorization was not submitted.",
  "来源位置、身份或状态已变化，正在等待本次提交回执。不会自动扩大已提交范围。":
    "The source location, identity or status changed. Waiting for confirmation of this submission; the submitted scope will not expand automatically.",
  测试实例不接入应用: "Test instances do not connect to apps",
  "请先把 MemHub 拖到“应用程序”并从那里重新打开，才能接入应用。":
    "Move MemHub to Applications and reopen it from there before connecting apps.",
  "部分应用未能完成接入，已成功的设置会保留，可进入应用后单独处理。":
    "Some apps could not be connected. Successful settings are retained; handle the rest individually in Apps.",
  onboardingConnectionPartial:
    "{success} apps connected; {failed} incomplete: {names}. You can handle them individually in Apps.",
  "首次设置的完成状态未能保存。已确认的导入回执不受影响；下次启动可能再次出现此页。":
    "Could not save first-time setup completion. Confirmed import receipts are unaffected; this page may appear again next time.",
  "首次设置的完成状态尚未确认。已确认的导入回执不受影响；下次启动可能再次出现此页。":
    "First-time setup completion is unconfirmed. Confirmed import receipts are unaffected; this page may appear again next time.",
  "位置检测未完成，尚未读取历史。可以重新检测，或先进入工作台。":
    "Location detection is incomplete, and history has not been read. Check again or continue to the workspace.",
  "暂时无法核对来源，尚未提交授权。选择已保留，可以重试。":
    "Sources cannot be reviewed right now, so authorization was not submitted. Your selections are retained; you can retry.",
  "本次没有来源获准导入。请核对下方原因后重试，或到应用页查看状态。":
    "No sources were authorized for this import. Review the reasons below and retry, or check their status in Apps.",
  "开始导入的结果尚未确认，选择已保留。可以重试，或进入应用页核对。":
    "The import start is unconfirmed. Your selections are retained; retry or check in Apps.",
  未完成: "Incomplete",
  核对中: "Checking",
  仍进入应用页: "Continue to Apps",
  仍进入工作台: "Continue to workspace",
  仍进入设置: "Continue to Settings",
  重试保存并进入: "Retry saving and continue",
  "稍后添加，先进入": "Add later and continue",
  "正在开始导入…": "Starting import…",
  开始导入: "Start importing",
  "不用从头介绍自己。从常用的 App 开始，把已有背景带过来。":
    "You do not have to start over. Bring context from your frequently used apps.",
  "确认前只检查应用信息，不读取对话，也不安装连接。":
    "Before confirmation, only app information is checked. Conversations are not read and no connection is installed.",
  "本机 AI 应用": "AI apps on this device",
  重新检测位置: "Check locations again",
  "检测中…": "Checking…",
  重新检测: "Check again",
  候选应用: "Available apps",
  "正在检测其他应用…": "Looking for other apps…",
  检测未完成: "Check incomplete",
  已全选: "All selected",
  全选: "Select all",
  onboardingImportHistory: "Import history from {name}",
  正在核对本机保存位置: "Checking local storage location",
  已发现: "Found",
  位置待核对: "Location needs review",
  "当前没有候选来源。可以稍后在应用页添加历史包。":
    "No sources found. You can add a history archive later from Apps.",
  "候选来源尚未加载。可以检测位置，或先进入工作台。":
    "Sources have not loaded. Check locations or continue to the workspace.",
  本次导入: "This import",
  "个 App 的历史对话": "app histories",
  "把零散的会话，": "Bring conversations",
  汇成完整的记忆: "together as memories",
  "少一些重复交代，多一些理解。": "Less repetition. More understanding.",
  onboardingMoreApps: "Scroll for more · {count} apps remaining",
  "onboardingMoreApps.one": "Scroll for more · {count} app remaining",
  onboardingAllApps: "All {count} apps shown",
  默认位置: "Default location",
  自定义位置: "Custom location",
  尚未检测: "Not checked",
  未发现: "Not found",
  无法访问: "Cannot access",
  位置无效: "Invalid location",
  "需要更新 Core": "Core update required",
  "候选位置不存在，可能尚无历史或路径不同。":
    "This location does not exist. There may be no history yet, or the path may be different.",
  "无法检查此位置，请核对目录访问权限后重试。":
    "Could not check this location. Review directory access and retry.",
  "此位置未通过安全检查，不能读取。":
    "This location failed the security check and cannot be read.",
  "当前 Core 未提供可确认的位置身份，需更新后再选择。":
    "The current Core does not provide a verifiable location identity. Update it before selecting.",
  暂不支持历史导入: "History import is not supported yet",
  "当前来源不可读取，请重新检测。":
    "The current source cannot be read. Check again.",
  "来源目录暂时不可读取，请核对位置和权限。":
    "The source directory is temporarily unreadable. Check its location and permissions.",
  "来源目录身份已变化，请返回重新检测。":
    "The source directory identity changed. Go back and check again.",
  "来源权限已变化，请返回重新确认。":
    "Source permissions changed. Go back and confirm again.",
  "此来源尚未获得读取授权。": "This source does not have read access yet.",
  "自定义来源位置需要重新确认。":
    "The custom source location needs confirmation again.",
  "当前 Core 未启用历史导入。":
    "History import is not enabled in the current Core.",
  onboardingImportRefused:
    "Import not authorized ({reason}). Review the source and retry.",
  确认自定义位置: "Confirm custom location",
  关闭位置确认: "Close location confirmation",
  "此位置来自当前配置，并非应用默认目录。确认位置不会开始读取历史。":
    "This location comes from your current configuration, not the app's default directory. Confirming the location does not start reading history.",
  onboardingConfirmHistory: "Confirm this is {name}'s history location",
  确认此位置: "Confirm this location",
  "对话正在汇入，记忆从这里开始":
    "Conversations are coming in. Memories start here",
  "导入进行中 · 顺手连接应用": "Importing · Connect your apps",
  "对话已保存，记忆尚待生成。":
    "Conversations saved; memories are yet to be created.",
  "导入在后台继续，无需等待。":
    "Import continues in the background. No need to wait.",
  "正在重新核对来源；已保存数量仍按任务回执显示。":
    "Rechecking sources; saved counts still reflect job receipts.",
  "本次历史导入已暂停，已保存的对话还在。":
    "This history import is paused. Saved conversations remain available.",
  "导入等待处理，已保存的对话还在。":
    "Import is awaiting processing. Saved conversations remain available.",
  停止后续操作: "Stop remaining actions",
  暂停导入: "Pause import",
  继续导入: "Resume import",
  "配置模型，沉淀记忆": "Configure a model to create memories",
  导入与原文: "Import and original text",
  "本次导入已结束。已保存的对话无需重复导入。":
    "This import is complete. Saved conversations do not need reimporting.",
  "对话先保存在本机。你可以同时设置后续连接。":
    "Conversations are saved locally first. You can set up connections in the meantime.",
  "导入不会自动开放应用读取权限。配置模型后，才会开始生成记忆。":
    "Importing does not automatically grant apps read access. Memories are created only after you configure a model.",
  onboardingHistoryWindow:
    "A large amount of conversation data was found. To limit usage and keep app timelines aligned, this import includes only the last 3 days for all apps.",
  导入说明: "Import information",
  正在导入你的会话: "Importing your conversations",
  "后台将持续为你导入，可以放心进入下一步":
    "Import will continue in the background. You can move on.",
  未开始的导入: "Imports that could not start",
  "未能开始导入，其他已接受任务不受影响。":
    "Import could not start. Other accepted jobs are unaffected.",
  查看未导入的应用: "View apps not imported",
  让记忆继续生长: "Keep your memories growing",
  连接你的应用: "Connect your apps",
  "适用于本次选择且支持的应用。":
    "For selected apps that support this connection.",
  一键接入应用: "Connect apps in one step",
  "安装记忆连接，让所选应用用上 MemHub 中的相关记忆。":
    "Install memory connections so selected apps can use relevant memories in MemHub.",
  "当前使用独立测试数据目录，不会修改本机应用的连接配置。":
    "This instance uses an isolated test data directory and will not change local app connections.",
  "点击底部按钮后，后台分别接入各应用；某个应用失败不影响其他应用。":
    "After you click the button below, apps are connected individually in the background; one failure will not affect others.",
  "暂时无法连接应用，当前不会安装连接。":
    "Apps cannot be connected right now. No connection will be installed.",
  onboardingBatchStopped:
    "Stopped remaining actions; check receipts for actions already sent. ",
  onboardingBatchResult: "{confirmed} confirmed, {failed} unconfirmed.",
  "已确认项保留，请核对未确认任务后重试。":
    " Confirmed actions are retained. Check unconfirmed jobs before retrying.",
  本次应用导入进度: "Import progress for selected apps",
  正在导入: "Importing",
  已暂停: "Paused",
  已中断: "Interrupted",
  导入遇到问题: "Import encountered a problem",
  导入完成: "Import complete",
  已取消: "Cancelled",
  已保存: "Saved",
  onboardingAppProgress: "Import progress for {name}",
  onboardingSavedMessages: "{count} messages saved",
  onboardingScannedOutsideRange:
    "Scanned {count} items; no conversations matched the time range",
  onboardingScannedNothingToSave:
    "Scanned {count} items; no conversations to save",
  onboardingScannedFilteringRecent:
    "Scanned {count} items; filtering the last 3 days",
  onboardingScannedFiltering: "Scanned {count} items; filtering",
  onboardingScannedNotSaved: "Scanned {count} items; not saved yet",
  没有可保存的历史对话: "No history conversations to save",
  读取授权不可用: "Read access unavailable",
  onboardingReadErrors: " · {count} read errors",
  "配置详情，按需要调整": "Configuration details, if needed",
  "连接未通过，配置已保留": "Connection failed; configuration retained",
  "选好模型，让记忆开始沉淀": "Choose a model to start creating memories",
  "连接设置已保存\n成功项保留，不重复执行。":
    "Connection settings saved.\nSuccessful steps are retained and will not be repeated.",
  "已选择：持续同步 · 一键接入":
    "Selected: continuous sync · one-step connection",
  "沉淀记忆 · 使用你选择的模型": "Create memories · With your chosen model",
  "当前无法启用模型，已填写内容保留。":
    "Cannot enable the model right now. Your entries are retained.",
  "模型连接测试通过，可以启用并开始沉淀记忆。":
    "Model connection passed. You can enable it to create memories.",
  "先测试模型连通性；测试通过后再启用。":
    "Test the model connection first; enable it after the test passes.",
  "跳过，先进入应用": "Skip and go to Apps",
  "正在测试模型…": "Testing model…",
  测试模型连通性: "Test model connection",
  "正在启用…": "Enabling…",
  确认启用: "Confirm and enable",
  核对提交状态: "Check submission status",
  模型配置: "Model configuration",
  模型参数: "Model parameters",
  "选择服务商，填入 API Key。核对已有配置后再继续。":
    "Choose a provider and enter an API key. Review existing configuration before continuing.",
  模型服务商: "Model provider",
  选择服务商: "Select a provider",
  "已安全保存，留空保留": "Saved securely; leave blank to keep",
  "输入服务商提供的 API Key": "Enter the API key from your provider",
  "连接未通过，请检查 API Key、模型与接口地址后重试。配置已保留。":
    "Connection failed. Check the API key, model and endpoint, then retry. Your configuration is retained.",
  "需要允许 MemHub 访问钥匙串。系统询问时请选择“始终允许”。":
    "Allow MemHub to access Keychain. Choose ‘Always Allow’ when prompted.",
  "留空保留已保存密钥；填写新密钥后需重新测试。":
    "Leave blank to keep the saved key; test again after entering a new key.",
  "密钥保存在系统安全存储中，不写入对话或日志。":
    "The key is kept in secure system storage, not conversations or logs.",
  配置详情与高级设置: "Configuration details and advanced settings",
  服务商待选择: "Provider not selected",
  模型待填写: "Model not entered",
  本地向量模型: "Local embedding model",
  向量模型待填写: "Embedding model not entered",
  思考已关闭: "Thinking disabled",
  思考未关闭: "Thinking not disabled",
  "预设已填好，只有自定义服务或特殊需求才需要修改。":
    "Presets are filled in. Change them only for custom services or special requirements.",
  返回服务商选择: "Back to provider selection",
  模型名称: "Model name",
  "Anthropic（暂不支持）": "Anthropic (not supported yet)",
  "HY 托管（当前不可用）": "HY managed (currently unavailable)",
  向量模型: "Embedding model",
  "本地 ONNX · 内置": "Local ONNX · Built in",
  "超时（秒）": "Timeout (seconds)",
  最大并发: "Maximum concurrency",
  上下文窗口: "Context window",
  "自动 / 可选": "Automatic / optional",
  关闭思考模式: "Disable thinking mode",
  "自定义向量配置可展开；远程向量服务须遵守凭据边界。":
    "You can expand custom embedding settings. Remote embedding services must respect credential boundaries.",
  "切换服务商或模型后，需要重新测试并启用。":
    "After changing providers or models, test and enable again.",
  向量模型名称: "Embedding model name",
  向量协议: "Embedding protocol",
  "同源 OpenAI-compatible（共用 Key）":
    "Same-origin OpenAI-compatible (shared key)",
  "向量 Base URL": "Embedding Base URL",
  "远程向量地址须与生成服务同源；本机地址仍需校验。":
    "Remote embedding endpoints must match the generation service origin; local endpoints still need validation.",
  "放弃旧记录，重新配置": "Discard old record and reconfigure",
  模型提交状态: "Model submission status",
  "正在测试连接…": "Testing connection…",
  "正在提交保存与启用，请等待回执…":
    "Saving and enabling; waiting for confirmation…",
  "操作结果未确认，已填写内容保留，请核对后重试。":
    "Outcome unconfirmed. Your entries are retained; check before retrying.",
  本次沉淀: "This memory setup",
  让记忆从这里开始: "Start creating memories here",
  对话来源: "Conversation sources",
  尚无已选来源: "No sources selected",
  onboardingSourcesSummary: "{names} and {count} apps in total",
  onboardingImportedConversations:
    "{count} conversations imported; source switches still control what syncs to this device later.",
  "处理服务：": "Processing service:",
  待选择: "Not selected",
  "相关对话会发送给所选服务商。":
    "Relevant conversations will be sent to the chosen provider.",
  "测试连接只发送测试内容。启用后会使用此模型整理已导入和之后同步的会话。":
    "Connection tests send only test content. After enabling, this model processes imported and subsequently synced conversations.",
  "当前不能在此启用模型。": "Cannot enable the model here right now.",
  "配置已修改，需要重新测试。": "Configuration changed. Test again.",
  onboardingIntegerRange: "Enter a whole number from {min} to {max}.",
  "请填写配置名称。": "Enter a configuration name.",
  "请填写实际可用的模型 ID。": "Enter an available model ID.",
  "请填写有效的模型接口地址。": "Enter a valid model endpoint URL.",
  "请填写 API Key。": "Enter an API key.",
  "请填写向量模型 ID。": "Enter an embedding model ID.",
  "配置结果尚未确认，请在模型设置中核对后重试。":
    "The configuration result is unconfirmed. Check it in Model settings before retrying.",
  "模型连接测试通过，可以启用。":
    "Model connection test passed. You can enable it.",
  "连接未通过，请核对模型、密钥和接收方。":
    "Connection failed. Check the model, key, and recipient.",
  "配置或测试结果暂未确认，请核对后重试。":
    "Could not confirm the configuration or test result. Check before retrying.",
  所选连接已启用: "Selected app connections are enabled",
  本次未选择应用连接: "No app connection selected this time",
  尚未填写接收方: "No recipient entered",
  本机内置向量模型: "Built-in local embedding model",
  "操作未确认，草稿已保留。":
    "Could not confirm the action. Your draft is retained.",
  "操作已完成，但配置列表暂未刷新。已保留操作回执，请刷新核对，不要重复保存。":
    "The operation completed, but the configuration list has not refreshed. The receipt is retained; refresh to review it. Do not save again.",
  "暂时无法刷新模型配置，已确认的配置仍保留。请重试刷新。":
    "Could not refresh model configurations. Confirmed configurations are retained; try refreshing again.",
  "未找到上次创建记录。确认放弃旧记录后可重新配置；系统不会自动重试。":
    "The previous creation record was not found. Discard it to reconfigure; no automatic retry will occur.",
  "创建结果仍待核对，不会重复创建。请稍后再次核对。":
    "The creation result still needs review. No duplicate will be created; check again later.",
  "已发现模型配置，旧创建记录仍保留。请先核对已有配置。":
    "A model configuration was found. The old creation record is retained; review the existing configuration first.",
  "旧创建记录尚未安全解除，配置仍保持锁定。请重新核对。":
    "The old creation record has not been safely cleared. Configuration remains locked; review it again.",
  "配置未保存，请检查模型地址和参数后重试；已填写内容保留。":
    "Configuration was not saved. Check the model endpoint and settings before retrying; your input is retained.",
  "创建结果尚未确认，请核对已保存结果，不要重复提交。":
    "The creation result is unconfirmed. Review saved results; do not submit again.",
  "模型配置已变化，旧测试结果不会用于启用，请重新核对配置。":
    "The model configuration changed. The old test result will not be used to enable it; review the configuration again.",
  "暂时无法核对测试服务，本次未发送测试，请恢复连接后重试。":
    "Could not verify the test service. No test was sent; restore the connection and try again.",
  "另一个模型的测试结果尚未确认，请先在该模型上核对测试结果。":
    "Another model's test result is unconfirmed. Review that model's result first.",
  "测试结果尚未确认。再次点击测试连接将核对原操作，不会重复发送测试。":
    "The test result is unconfirmed. Clicking Test connection again will review the original operation without sending another test.",
  "请先点击测试连接，通过后再设为当前模型。":
    "Test the connection first; set it as the current model only after it passes.",
  "这是当前正在使用的模型，请先切换到别的模型再删除。":
    "This model is currently in use. Switch to another model before deleting it.",
  "模型配置操作未成功，请核对输入后重试。":
    "The model configuration operation did not succeed. Check your input and try again.",
  "它没有 MCP 通道，无法发放可撤销的记忆凭证":
    "This app has no MCP channel for a revocable memory credential",
  当前安装不支持一键插入:
    "One-click connection is unsupported in this installation",
  桌面端的连接组件不可用: "The desktop connection component is unavailable",
  安装后没有拿到安装回执: "No installation receipt was returned",
  记忆使用权限没有获批: "Memory access was not approved",
  配置文件被改过: "The configuration file was edited",
  "有无法确认的残留，需要人工核对": "Some remnants need manual review",
  权限存储暂不可用: "Permission storage is unavailable",
  "请先把 MemHub 拖到“应用程序”文件夹，再接入应用":
    "Move MemHub to Applications before connecting apps",
  本机运行时暂不可用: "The local runtime is unavailable",
  桌面端读取接入状态超时: "Reading the desktop connection status timed out",
  "apps.apps": "Apps",
  "apps.allApps": "All apps",
  "apps.home": "Apps home",
  "apps.back": "Back to home",
  "apps.overview": "Memory overview",
  "apps.memories": "Saved memories",
  "apps.readingCount": "Loading memory count…",
  "apps.unreadable": "Unavailable",
  "apps.unit": "items",
  "apps.scope": "Used within app permissions",
  "apps.processing": "Processing status",
  "apps.running": "Processing",
  "apps.paused": "Paused",
  "apps.stoppedNew": "No new memories are being created",
  "apps.checkingProcessing": "Checking processing status",
  "apps.unchecked": "Needs review",
  "apps.organizingNew": "Processing new conversations",
  "apps.recentUpdate": "Recent update",
  "apps.notGenerated": "Not created yet",
  "apps.saved": "Memory saved",
  "apps.separate": "History and memories are processed separately",
  "apps.recentCall": "Recent call",
  "apps.activityUnavailable": "Activity unavailable",
  "apps.firstBatch": "First memories",
  "apps.callsUnavailable": "Could not read call history",
  "apps.memoryUnavailable": "Could not read memory status",
  "apps.importing": "Importing conversations before creating memories",
  "apps.firstRunning": "Creating the first memories",
  "apps.firstQueued": "Preparing the first memories",
  "apps.readingActivity": "Loading activity",
  "apps.emptyMemories": "No memories created yet",
  "apps.callReadFailure":
    "You can still view saved memories. Retrying will not make a new call.",
  "apps.memoryReadFailure":
    "A failed read does not mean there are no memories.",
  "apps.savingConversations":
    "Conversations are still being saved in the background.",
  "apps.waitingMemories": "History is saved. New memories will appear here.",
  "apps.reread": "Retry",
  "apps.viewCalls": "View history",
  "apps.viewMemory": "View memories",
  "apps.backgroundDown":
    "The background service is not running; apps cannot look up memories right now",
  "apps.restart": "Restart",
  "apps.runtime": "Runtime status",
  "apps.backgroundRunning": "Running in background",
  "apps.connectionUnchecked": "Connection needs review",
  "apps.disconnected": "Service disconnected",
  "apps.authorizedOnly": "Only processes authorized content",
  "apps.sync": "Conversation sync",
  "apps.organizing": "Memory processing",
  "apps.access": "App access",
  "apps.checkingAccess": "App connection check",
  "apps.checking": "Checking…",
  "apps.recheck": "Check again",
  "apps.accessState": "App connection status",
  "apps.staleAccess":
    "Could not read app connection status; showing the last known state.",
  "apps.reconnect": "Reconnect",
  "apps.myApps": "My apps",
  "apps.list": "App list",
  "apps.syncEnabled": "Continuous sync enabled",
  "apps.recallAllowed": "Can request authorized memories",
  "apps.historyAllowed": "History access authorized",
  "apps.connectionPending": "Connection incomplete",
  "apps.details": "View details",
  "apps.listUnavailable": "App list unavailable",
  "apps.emptyApps": "No apps to display yet",
  "apps.checkingNow": "Checking",
  "apps.checkingState": "Checking status",
  "apps.notEnabled": "Not enabled",
  "apps.enableFailed": "Could not enable",
  "apps.unknownTime": "Time unknown",
  "apps.today": "Today",
  "apps.newMemory": "New memory",
  "apps.updatedMemory": "Updated memory",
  "apps.unlinked": "Not connected yet",
  "apps.lastRead": "Last read: ",
  "apps.recentSync": "Last synced {time}",
  "apps.connectedCount": "{count} apps connected",
  "apps.connectedCount.one": "{count} apps connected",
  "apps.pendingCount": "{count} apps need review",
  "apps.pendingCount.one": "{count} apps need review",
  "apps.mixedCount": "{count} apps connected · Pending review: {pending}",
  "apps.mixedCount.one": "{count} app connected · Pending review: {pending}",
  "apps.unknownAccess":
    "App connection status is unavailable; the connected app count cannot be confirmed.",
  "apps.unknownPermissions":
    "Could not read memory permissions for {names}; connection status is unconfirmed.",
  "apps.enabledCount": "{count} apps enabled",
  "apps.enabledCount.one": "{count} apps enabled",
  "apps.appCount": "{count} apps",
  "apps.appCount.one": "{count} apps",
  "apps.viewAll": "View all {count} apps",
  "apps.viewAll.one": "View all {count} apps",
  "apps.checkedAt": "Last checked: {time}",
  "apps.revision": "Version {count}",
  "apps.conversationTime": "Conversation time",
  "apps.importTime": "Import time",
  "apps.incompleteCalls":
    " · Some calls were not recorded; the list may be incomplete",
  "apps.timeoutWarning":
    "{host} could not look up memories recently. The background service may be busy and should recover automatically.",
  "apps.autoHostFailed": "{name} could not connect automatically{reason}.",
  "apps.failureReason": ": {reason}",
  "apps.autoSyncFailed":
    "Continuous sync could not be enabled automatically for {name}. {reason}",
  "apps.noAutoRetry": "It will not retry automatically.",
  "apps.cardUnavailable":
    "{label} is temporarily unavailable. Other features are unaffected, and it should recover automatically.",
  "apps.navMemories": "Memories",
  "apps.navigation": "Main navigation",
  "apps.skip": "Skip to main content",
  "apps.local": "Stored locally",
  "apps.localHint":
    "Memories are stored on this device. Relevant content is sent to the service when you use a cloud model or an authorized assistant.",
  "memory:记忆抽取": "Memory extraction",
  "memory:关系归纳": "Relationship synthesis",
  "memory:深度整理": "Deep processing",
  "memory:跨域整理": "Cross-domain processing",
  "memory:当前模型已变化": "Current model changed",
  "memory:本地会话内容已变化": "Local conversation content changed",
  "memory:这段对话含过长原文，暂未整理；原文已保留":
    "This conversation contains text too long to process; the original is preserved",
  "memory:记忆库分页": "Memory library pages",
  "memory:上一页": "Previous page",
  "memory:下一页": "Next page",
  "memory:等待可用生成模型": "Waiting for an available generation model",
  "memory:还没有启用生成模型": "No generation model enabled yet",
  "memory:缺少生成模型 API Key": "Generation model API key is missing",
  "memory:模型凭据暂不可用": "Model credentials are temporarily unavailable",
  "memory:本机凭据存储暂不可用":
    "Local credential storage is temporarily unavailable",
  "memory:需要授权访问本机钥匙串。请重新打开 App 并完成一次授权":
    "Keychain access needs authorization. Reopen the app and approve access.",
  "memory:读取本机钥匙串超时。重新保存一遍模型配置即可恢复":
    "Keychain read timed out. Save the model configuration again to recover.",
  "memory:写入本机钥匙串超时。重新保存一遍模型配置即可恢复":
    "Keychain write timed out. Save the model configuration again to recover.",
  "memory:模型认证失败。更新 key 后，到设置里点一次检测，会自动接着处理":
    "Model authentication failed. Update the key and run a check in Settings to resume processing.",
  "memory:模型账户余额不足。充值或换 key 后，到设置里点一次检测，会自动接着处理":
    "Model account balance is insufficient. Top up or change the key, then run a check in Settings to resume processing.",
  "memory:模型服务拒绝访问。更新权限后，到设置里点一次检测，会自动接着处理":
    "Model service denied access. Update permissions and run a check in Settings to resume processing.",
  "memory:模型服务限流": "Model service rate limited",
  "memory:模型服务暂不可用": "Model service temporarily unavailable",
  "memory:模型服务响应超时": "Model service timed out",
  "memory:模型请求不兼容，请核对配置":
    "Model request is incompatible; check the configuration",
  "memory:模型请求未成功": "Model request failed",
  "memory:HY Memory 不可用": "HY Memory unavailable",
  "memory:HY Memory 启动失败": "HY Memory failed to start",
  "memory:暂时没能整理，稍后会自动重试":
    "Could not process this yet; will retry automatically",
  "memory:这次没能整理成功，正在重试": "Could not process this time; retrying",
  "memory:生成模型换过了，这批会重新整理":
    "Generation model changed; these items will be processed again",
  "memory:整理方式换过了，这批会重新整理":
    "Processing settings changed; these items will be processed again",
  "memory:已提炼": "Extracted",
  "memory:旧版确认记录": "Legacy confirmation record",
  "memory:创建": "Created",
  "memory:纠正": "Correct",
  "memory:调整使用范围": "Access scope changed",
  "memory:撤销修改": "Change undone",
  "memory:模型更新": "Model update",
  "memory:被新记忆取代": "Replaced by a new memory",
  "memory:其他操作": "Other action",
  "memory:删除（记录）": "Deletion",
  "memory:修改已保存，但读取最新列表失败。已停止使用旧回答，请重新读取。":
    "Change saved, but the latest list could not be loaded. Old answers are no longer used; reload the list.",
  "memory:纠正已保存，但读取最新列表失败。已停止使用旧回答，请重新读取。":
    "Correction saved, but the latest list could not be loaded. Old answers are no longer used; reload the list.",
  "memory:未收到操作结果，请重试。": "No result was received. Please retry.",
  "memory:批量处理结果已保留，但列表刷新失败，请重新读取记忆库。":
    "Bulk action results are retained, but the list could not refresh. Reload the memory library.",
  "memory:批量处理结果已保留，但列表刷新失败，稍后会自动重新读取。":
    "Bulk action results are retained, but the list could not refresh. It will reload automatically later.",
  "memory:召回范围已保存，但列表刷新失败。请稍后重新打开记忆页。":
    "Recall scope saved, but the list could not refresh. Reopen Memories later.",
  "memory:重新整理可能再次调用模型。只会重跑结果不确定的对话，是否继续？":
    "Processing again may call the model. Only conversations with uncertain results will be retried. Continue?",
  "memory:原始对话暂时无法读取": "Could not load original conversations",
  "memory:没有匹配的原始对话": "No matching original conversations",
  "memory:还没有导入的原始对话": "No original conversations imported yet",
  "memory:查看已读内容": "View saved content",
  "memory:已保存原文": "Original text saved",
  "memory:刷新已读内容": "Refresh saved content",
  "memory:正在核对本机已保存记录…": "Checking saved local records…",
  "memory:原始消息": "Original messages",
  "memory:会话": "Conversations",
  "memory:用户发言": "User messages",
  "memory:Agent 发言": "Agent message",
  "memory:作者未知": "Unknown author",
  "memory:原始时间未知": "Original time unknown",
  "memory:工具记录": "Tool record",
  "memory:原始时间来自已保存记录。":
    "Original timestamps come from saved records.",
  "memory:已保存的原文样例": "Saved original-text examples",
  "memory:最多展示 3 段，不代表全部历史或推荐记忆。":
    "Up to 3 examples; not the full history or recommended memories.",
  "memory:查看完整对话": "View full conversation",
  "memory:原文较长，此处仅展示前 600 个字符。":
    "Long original text; only the first 600 characters are shown here.",
  "memory:这个来源还没有保存原文。":
    "No original text saved from this source yet.",
  "memory:完整对话": "Full conversation",
  "memory:关闭完整对话": "Close full conversation",
  "memory:用户": "User",
  "memory:暂时无法读取已保存内容，旧回执已收起。请检查授权或重试。":
    "Could not read saved content. The previous receipt is closed; check permissions or retry.",
  "memory:这段原文暂不可读，可能已被撤回授权。请刷新回执。":
    "This original text is unavailable; access may have been revoked. Refresh the receipt.",
  "memory:外部记忆召回范围": "External memory lookup scope",
  "memory:外部记忆召回": "External memory lookup",
  "memory:调整召回范围": "Adjust lookup scope",
  "memory:所有关联工具": "All connected tools",
  "memory:已停止外部记忆召回": "External memory lookup stopped",
  "memory:原有限定范围": "Previous limited scope",
  "memory:选择召回范围": "Choose lookup scope",
  "memory:停止外部记忆召回": "Stop external memory lookup",
  "memory:正在保存…": "Saving…",
  "memory:保存召回范围": "Save lookup scope",
  "memory:取消范围修改": "Cancel scope changes",
  "memory:原文检索仍按来源授权；已带出的内容不会撤回。":
    "Original-text searches still follow source permissions; content already provided will not be recalled.",
  "memory:记忆已更新，请重新打开这条记忆后再调整范围。":
    "Memory has changed. Reopen it before adjusting the scope.",
  "memory:范围修改未提交，当前范围保持不变。请修正后重试。":
    "Scope changes were not submitted. The current scope is unchanged; fix the issue and retry.",
  "memory:保存结果尚未确认，请重试。":
    "Save result is unconfirmed. Please retry.",
  "memory:原话已保存在本机；当前内容、模型接收方或授权版本尚未通过核对，未继续整理。":
    "The original is saved on this device; the content, model recipient or permission version did not pass review, so processing did not continue.",
  "memory:HY 未返回可核对的已有记忆，未创建替代内容；原始记录已保留，可重试处理。":
    "HY returned no existing memories that could be verified, and no replacement was created. Original records are retained; you can retry processing.",
  "memory:历史引用尚未绑定到原始来源，当前批次未完成，原话已保留。":
    "Historical citations are not yet linked to their original sources. This batch is incomplete; the original is retained.",
  "memory:记忆内容未通过来源与作者核验，当前批次未完成，原话已保留。":
    "The memory content did not pass source and author verification. This batch is incomplete; the original is retained.",
  "memory:所选记忆已不可读取，请重新读取记忆库。":
    "The selected memory can no longer be read. Reload the memory library.",
  "memory:还没连上本地 Core，正在重试…":
    "Local Core is not connected yet; retrying…",
  "memory:记忆内容或使用权限已变化，回答已停止。请重新提问。":
    "The memory content or access permissions changed, so the answer was stopped. Ask again.",
  "memory:这条记忆的来源授权已撤回，暂时不能读取。":
    "Source access for this memory was revoked, so it cannot be read right now.",
  "memory:记忆已被更新，请刷新后重试。":
    "This memory was updated. Refresh and retry.",
  "memory:已删除的记忆不能修改。": "Deleted memories cannot be edited.",
  "memory:这条记忆已经删除。": "This memory has been deleted.",
  "memory:这条记忆已被更新的记忆取代，只能查看或删除。":
    "This memory was replaced by an updated memory and can only be viewed or deleted.",
  "memory:没有更早的修改可以撤销。": "There are no earlier changes to undo.",
  "memory:来源原话已不存在，无法建立这条记忆。":
    "The original source text no longer exists, so this memory cannot be created.",
  "memory:这次操作与已完成的请求不一致。":
    "This action does not match the completed request.",
  "memory:输入内容不符合要求。": "The input does not meet the requirements.",
  "memory:本地记忆库正忙，请稍后重试。":
    "The local memory library is busy. Please retry later.",
  "memory:记忆": "Memories",
  "memory:记忆空间": "Memory space",
  "memory:全部应用": "All apps",
  "memory:来源": "Source",
  "memory:主题": "Topic",
  "memory:归纳": "Synthesis",
  "memory:全部记忆": "All memories",
  "memory:来源待核对": "Source needs review",
  "memory:所选来源待核对": "Selected source needs review",
  "memory:未知来源": "Unknown source",
  "memory:记忆读取失败": "Could not load memories",
  "memory:正在读取…": "Loading…",
  "memory:正在更新…": "Updating…",
  "memory:读取失败": "Could not load",
  "memory:正在核对来源…": "Checking sources…",
  "memory:正在导入历史会话，记忆将陆续出现":
    "Importing earlier conversations; memories will appear gradually",
  "memory:记忆工作台": "Memory workspace",
  "memory:来源与主题": "Sources and topics",
  "memory:记忆或原始对话": "Memories or original conversations",
  "memory:原始对话": "Original conversations",
  "memory:地图组织方式": "Map grouping",
  "memory:筛选地图记忆": "Filter map memories",
  "memory:搜索原始对话": "Search original conversations",
  "memory:搜索记忆": "Search memories",
  "memory:筛选记忆…": "Filter memories…",
  "memory:向记忆提问": "Ask memories a question",
  "memory:询问全部已授权可用记忆…":
    "Ask about all authorized, available memories…",
  "memory:发送问题": "Send question",
  "memory:刷新地图记忆": "Refresh map memories",
  "memory:关闭新增记忆提示": "Dismiss new memory notice",
  "memory:地图记忆列表": "Map memory list",
  "memory:记忆读取失败，请重试。": "Could not load memories. Please retry.",
  "memory:重新读取地图记忆": "Reload map memories",
  "memory:没有匹配的记忆": "No matching memories",
  "memory:本页没有匹配的主题记忆": "No matching topic memories on this page",
  "memory:还没有已生成的记忆": "No memories created yet",
  "memory:地图记忆分页": "Map memory pages",
  "memory:上一页记忆": "Previous memory page",
  "memory:下一页记忆": "Next memory page",
  "memory:记忆关系图": "Memory graph",
  "memory:记忆关联": "Memory connections",
  "memory:刚导入的原话，还不是沉淀后的记忆":
    "Newly imported original text, not yet memories",
  "memory:来源归属": "Source connections",
  "memory:可在沉淀记忆后查看视图": "Create memories to view the graph",
  "memory:查看选中来源原文": "View selected source text",
  "memory:查看选中记忆原文": "View selected memory text",
  "memory:查看原文": "View original conversation",
  "memory:整理已有关系": "Build existing connections",
  "memory:正在读取原文…": "Loading original conversation…",
  "memory:选择一条原始对话": "Select an original conversation",
  "memory:记忆暂时无法读取": "Memories are temporarily unavailable",
  "memory:当前筛选没有可显示的记忆": "No memories to display for this filter",
  "memory:正在读取记忆…": "Loading memories…",
  "memory:已保存的对话还未生成记忆":
    "Saved conversations have not become memories yet",
  "memory:还没有可显示的记忆": "No memories to display yet",
  "memory:连线表示来源归属": "Lines show source connections",
  "memory:记忆详情": "Memory details",
  "memory:来源原文": "Source original text",
  "memory:关联节点": "Related node",
  "memory:关闭记忆详情": "Close memory details",
  "memory:关闭详情": "Close details",
  "memory:正在读取这条记忆…": "Loading this memory…",
  "memory:这条记忆的原文暂时无法读取，选择已保留。":
    "Could not load this memory's original text. Selection is preserved.",
  "memory:重试读取原文": "Retry loading conversation",
  "memory:已沉淀的记忆": "Created memories",
  "memory:正在读取记忆详情…": "Loading memory details…",
  "memory:沉淀记忆后查看详情": "Create memories to see details",
  "memory:时间未知": "Time unknown",
  "memory:来源原文已不可读取": "Source original text is unavailable",
  "memory:关联记忆": "Related memories",
  "memory:正在读取完整对话…": "Loading full conversation…",
  "memory:重试读取完整对话": "Retry loading full conversation",
  "memory:看完整对话": "View full conversation",
  "memory:今天": "Today",
  "memory:你": "You",
  "memory:助手": "Assistant",
  "memory:工具": "Tool",
  "memory:未知": "Unknown",
  "memory:作者待核对": "Author needs review",
  "memory:你说": "You said",
  "memory:助手原话": "Original assistant message",
  "memory:已沉淀": "Created",
  "memory:已删除": "Deleted",
  "memory:已被取代": "Superseded",
  "memory:状态待核对": "Status needs review",
  "memory:编辑": "Edit",
  "memory:原始依据": "Original evidence",
  "memory:这段原文当前不可查看。":
    "This original text is currently unavailable.",
  "memory:版本记录": "Version history",
  "memory:更多管理": "More actions",
  "memory:撤销上次修改": "Undo last change",
  "memory:删除记忆": "Delete memory",
  "memory:编辑记忆": "Edit memory",
  "memory:返回记忆": "Back to memory",
  "memory:修改这条记忆，原始对话保持不变。":
    "Edit this memory; the original conversation stays unchanged.",
  "memory:记忆内容": "Memory content",
  "memory:使用范围保持不变": "Access scope stays unchanged",
  "memory:已有新版本": "A newer version exists",
  "memory:你的草稿已保留，先查看新版本再决定如何修改。":
    "Your draft is retained. Review the new version before making changes.",
  "memory:查看新版本": "View new version",
  "memory:基于此版本继续编辑": "Continue editing from this version",
  "memory:取消": "Cancel",
  "memory:保存": "Save",
  "memory:重试": "Retry",
  "memory:删除": "Delete",
  "memory:对话": "Conversation",
  "memory:保存修改": "Save changes",
  "memory:对应记忆": "Related memory",
  "memory:查看记忆": "View memories",
  "memory:删除这条记忆？": "Delete this memory?",
  "memory:删除后不再用于新的召回，来源 App 中的原始对话不会删除。":
    "It will no longer be used for new lookups. Original conversations in the source app will not be deleted.",
  "memory:已经发送给应用的内容无法远程收回。":
    "Content already sent to apps cannot be recalled remotely.",
  "memory:放弃尚未保存的修改？": "Discard unsaved changes?",
  "memory:来源权限已变化或原文不可用。":
    "Source permissions changed or the original text is unavailable.",
  "memory:暂时无法读取新版本，草稿已保留。":
    "Could not load the new version. Your draft is retained.",
  "memory:修改未提交；草稿已保留。请按提示修正后重试。":
    "Changes were not submitted; your draft is retained. Fix the issue and retry.",
  "memory:修改尚未确认，草稿已保留。请先刷新核对，避免重复提交。":
    "Changes are unconfirmed; your draft is retained. Refresh and check before retrying to avoid a duplicate submission.",
  "memory:删除未提交。请核对这条记忆的状态后重试。":
    "Deletion was not submitted. Check this memory's status before retrying.",
  "memory:删除尚未确认。请重新核对记忆状态，不会自动扩大删除范围。":
    "Deletion is unconfirmed. Check the memory's status; the deletion scope will not expand automatically.",
  "memory:撤销未提交。请按提示修正后重试。":
    "Undo was not submitted. Fix the issue and retry.",
  "memory:撤销尚未确认，请先核对当前记忆版本，避免重复撤销。":
    "Undo is unconfirmed. Check the current memory version before retrying to avoid undoing twice.",
  "memory:添加记忆": "Add memory",
  "memory:关闭新增记忆": "Close add-memory dialog",
  "memory:关闭": "Close",
  "memory:记下这件事": "Write it down",
  "memory:例如：项目周会不排在周五。":
    "For example: Do not schedule the project meeting on Fridays.",
  "memory:整理模型：": "Processing model: ",
  "memory:尚未配置，原话仍可保存":
    "Not configured; you can still save the original",
  "memory:正在读取": "Loading",
  "memory:配置模型": "Configure model",
  "memory:保存中…": "Saving…",
  "memory:保存原话": "Save original",
  "memory:最近添加": "Recently added",
  "memory:原话已保存，等待可用模型":
    "Original saved; waiting for an available model",
  "memory:原话已保存，等待整理": "Original saved; waiting for processing",
  "memory:正在整理": "Processing",
  "memory:原话已保存，整理未完成": "Original saved; processing incomplete",
  "memory:整理已完成": "Processing complete",
  "memory:原话已保存，未提取出可用记忆":
    "Original saved; no usable memory extracted",
  "memory:最近添加的内容读取失败。": "Could not read recently added notes.",
  "memory:整理状态暂时不可用，原话已保存。":
    "Processing status is temporarily unavailable; the original is saved.",
  "memory:已保存，正在后台沉淀记忆":
    "Saved; creating memories in the background",
  "memory:未能确认保存结果，输入已保留。重试不会重复添加。":
    "Could not confirm the save result. Your text is retained; retrying will not add a duplicate.",
  "memory:整理未能开始，原话仍已保存。":
    "Processing could not start; the original is still saved.",
  "memory:整理结果尚未确认，原话仍已保存。请刷新后核对。":
    "Processing is not yet confirmed; the original is saved. Refresh to check.",
  "memory:模型服务要求检查余额或付款状态，原话仍已保存。":
    "The model service requires a balance or payment check; the original remains saved.",
  "memory:这台设备打不开 3D 视图，已显示关联列表。":
    "3D view is unavailable on this device. Showing the connections as a list.",
  "memory:图谱工具": "Graph controls",
  "memory:放大": "Zoom in",
  "memory:放大图谱": "Zoom in on graph",
  "memory:缩小": "Zoom out",
  "memory:缩小图谱": "Zoom out of graph",
  "memory:重置视角": "Reset view",
  "memory:重置图谱视角": "Reset graph view",
  "memory:查看读取详情": "View read details",
  "memory:原文暂时无法读取": "Could not load original text",
  "memory:暂时无法读取这段对话，已保留当前记忆。":
    "Could not load this conversation. The current memory remains selected.",
  "memory:已清除筛选，定位到关联记忆。":
    "Filters cleared to find the related memory.",
  "memory:所选记忆不在当前筛选内，已取消选择。":
    "The selected memory is not in this filter; selection cleared.",
  "memory:列表分隔符": ", ",
  "memory:冒号": ":",
  "memory:手动添加": "Added manually",
  "memory:导入的已有记忆": "Imported existing memory",
  "memory:记忆与原文": "Memories and original text",
  "memory:第 {revision} 版": "Version {revision}",
  "memory:对话时间 ": "Conversation time",
  "memory:沉淀时间 ": "Created at",
  "memory:原对话 ": "Original conversation ",
  "memory:{count} 条记录没有原始时间，未用导入时间代替。":
    "{count} records have no original timestamp; import time was not substituted.",
  "memory:作者数量按当前记录分类统计，不代表记忆抽取已完成或语义已核实。":
    "Author counts reflect saved records, not completed memory extraction or verified meaning.",
  "memory:统计于 ": "Statistics as of ",
  "memory: · 导入数量仍在更新，可刷新核对":
    " · Import count is still updating; refresh to check",
  "memory:对话于 {day}": "Conversation on {day}",
  "memory:导入于 {day}": "Imported on {day}",
  "memory:已开始处理所选对话": "Processing selected conversations",
  "memory:处理请求已受理": "Processing request received",
  "memory:模型连接已通过。首批记忆生成后，会逐步出现在记忆列表里。":
    "Model connection confirmed. Memories will appear in the list as they are created.",
  "memory:已导入 {count} 段对话，整理成记忆后会出现在这里。":
    "Imported {count} conversations. Memories will appear here after processing.",
  "memory:历史已保存在本机，记忆就绪后会出现在这里。":
    "History is saved on this device. Memories will appear here when ready.",
  "memory:连接应用并导入历史后，可以开始沉淀记忆。":
    "Connect an app and import history to start creating memories.",
  "memory:当前已生成 0 条记忆。请求已受理不等于已经完成，原始对话、模型处理任务和记忆数量分别统计。":
    "No memories created yet. A received request is not complete; original conversations, processing jobs and memories are counted separately.",
  "memory:返回应用": "Back to apps",
  "memory:共 {count} 条 · 正在核对来源…": "{count} total · Checking sources…",
  "memory:共 {count} 条 · 正在核对来源….one":
    "{count} total · Checking sources…",
  "memory:共 {count} 条": "{count} items total",
  "memory:共 {count} 条.one": "{count} items total",
  "memory: · 正在核对…": " · Checking…",
  "memory:新增 {count} 条记忆": "{count} new memories",
  "memory:新增 {count} 条记忆.one": "{count} new memories",
  "memory:沉淀于 ": "Created at ",
  "memory:共 {count} 条，第 {page} / {pages} 页":
    "{count} total · Page {page} of {pages}",
  "memory:共 {count} 条，第 {page} / {pages} 页.one":
    "{count} total · Page {page} of {pages}",
  "memory:已沉淀 {count} 条记忆": "{count} memories created",
  "memory:已沉淀 {count} 条记忆.one": "{count} memories created",
  "memory:关键词“{query}”": "Keyword “{query}”",
  "memory:已沉淀 {count} 条{filter} · 当前展示 {shown} 条":
    "{count} memories created{filter} · Showing {shown}",
  "memory:已沉淀 {count} 条{filter} · 当前展示 {shown} 条.one":
    "{count} memories created{filter} · Showing {shown}",
  "memory:（{filter}）": " ({filter})",
  "memory:第 {page} 页": "Page {page}",
  "memory:原话已保存，正在等待整理：":
    "Original text saved, awaiting processing: ",
  "memory:记忆未在生成": "Memories are not being created",
  "memory:还没有开始生成记忆：{reason}（{count} 条待处理）":
    "Memories are not being created: {reason} ({count} pending)",
  "memory:去配置生成模型": "Configure generation model",
  "calls.directHost": "Direct call (diagnostic)",
  "calls.headline.timedOut": "{host} could not look up memories in time",
  "calls.headline.unavailable": "{host} could not look up memories right now",
  "calls.headline.denied": "{host} was not authorized to look up memories",
  "calls.headline.empty": "{host} found no matching memories",
  "calls.headline.found": "{host} found {count} related items",
  "calls.headline.found.one": "{host} found {count} related items",
  "calls.detail.timedOut":
    "Memories are being processed. The lookup timed out, so this answer did not include memories.",
  "calls.detail.unavailable":
    "MemHub is temporarily unavailable, so this answer did not include memories.",
  "calls.detail.denied": "Current permissions do not allow this lookup.",
  "calls.detail.partialDelivered":
    "Provided to {host} · some content is still being processed",
  "calls.detail.partial": "Some content is still being processed",
  "calls.detail.delivered": "Provided to {host}",
  "calls.detail.notDelivered": "Not provided to the app",
  "calls.detail.empty": "No matching memories this time",
  "calls.detail.success": "Lookup succeeded",
  "calls.title": "Usage history",
  "calls.query": "This memory lookup",
  "calls.backApps": "Back to apps",
  "calls.backHistory": "Back to history",
  "calls.filter": "Filter by app",
  "calls.allApps": "All apps",
  "calls.recent": "Recent history",
  "calls.refresh": "Refresh history",
  "calls.clearPage": "Clear this page",
  "calls.clearAll": "Clear all history",
  "calls.clearAllShort": "Clear all",
  "calls.loading": "Loading usage history",
  "calls.clearing": "Clearing selected history",
  "calls.readFailed": "Could not load history. Refresh and try again.",
  "calls.unavailable": "Usage history is unavailable. Refresh later.",
  "calls.incomplete":
    "Some calls were not recorded; the list may be incomplete.",
  "calls.callDetail": "Call details",
  "calls.source": " · Source: {source}",
  "calls.stats":
    "Found {raw} original passages · {memories} memories · {seconds} seconds",
  "calls.metadata": "Record details",
  "calls.privacy":
    "For privacy, your original question and the answer are not stored here; only the time, source and result are kept.",
  "calls.exactTime": "Exact time",
  "calls.app": "Apps",
  "calls.type": "Type",
  "calls.result": "Result status",
  "calls.tool": "Look up memories",
  "calls.notRecorded": "Not recorded",
  "calls.missingRow":
    "This record is no longer on this page. Return to history to find it.",
  "calls.list": "Call history",
  "calls.emptyPage": "No records on this page",
  "calls.empty": "No usage history yet",
  "calls.pagination": "History pages",
  "calls.previous": "Previous page",
  "calls.next": "Next page",
  "calls.policy": "Kept for {days} days, up to {count} records",
  "calls.confirmAllTitle": "Clear all usage history?",
  "calls.confirmPageTitle": "Clear {count} records on this page?",
  "calls.confirmPageTitle.one": "Clear {count} records on this page?",
  "calls.confirmAllDescription":
    "Delete all retained call records, not only this page or filter. Original conversations and memories, permissions and retention rules are unchanged. This cannot be undone.",
  "calls.confirmPageDescription":
    "Delete only these call records. Original conversations and memories, permissions and retention rules are unchanged. This cannot be undone.",
  "calls.confirmAll": "Confirm clear all",
  "calls.confirmPage": "Confirm clear",
  "calls.cancel": "Cancel",
  "calls.clearedAll": "All history was cleared.",
  "calls.clearedPage": "Selected history was cleared.",
  "calls.clearAllFailed":
    "Could not clear usage history. Existing records are unchanged; check and retry.",
  "calls.clearPageFailed":
    "Could not clear selected history. Existing records are unchanged; check and retry.",
  "calls.clearUnknown":
    "Could not confirm the result. Check the reloaded history; clearing was not retried automatically.",
  "calls.references": "Related content",
  "calls.referencesLoading": "Loading related content",
  "calls.referencesFailed":
    "Could not load related content. Refresh and try again.",
  "calls.raw": "Original conversation",
  "calls.memory": "Memories",
  "calls.changed": "Version changed",
  "calls.pending": "Needs review",
  "calls.notAvailable": "Unavailable",
  "calls.openRaw": "View original conversation",
  "calls.openMemory": "View memories",
  "calls.noReferences": "No related content found",
  "calls.rawBody": "Original conversation text",
  "calls.rawLoading": "Loading original conversation",
  "calls.rawFailed":
    "The original conversation is unavailable or its version has changed.",
  "calls.state.grounded": "Lookup succeeded",
  "calls.state.empty": "Lookup succeeded; no matching memories",
  "calls.state.partial":
    "Lookup succeeded; some content is still being processed",
  "calls.state.timed_out": "Lookup timed out; no memories provided",
  "calls.state.unavailable": "MemHub is temporarily unavailable",
  "calls.state.denied": "This lookup is not authorized",
  "calls.return.timed_out":
    "MemHub is importing and processing memories. The lookup did not finish in time, so {host} answered without memories. It will resume once processing is complete.",
  "calls.return.unavailable":
    "MemHub is temporarily unavailable; this answer did not include memories.",
  "calls.return.denied":
    "Current permissions do not allow this lookup; no memories were provided to the app.",
  "calls.return.partial": "Some content is still being processed.",
  "clock.unknown": "Time unknown",
  "clock.today": "Today {clock}",
  settings: "Settings",
  languageTitle: "Interface language",
  languageLabel: "Language",
  languageSystem: "Use system language",
  languageChinese: "Simplified Chinese",
  languageEnglish: "English",
  languageSpanish: "Spanish",
  languageDescription:
    "Choose the interface language. This setting does not change your conversations or memories.",
  languageSaveError: "Could not save the language setting. Please retry.",
  languageLoadError: "Could not load the language setting. Please retry.",
  nativeErrorTitle: "MemHub could not start",
  startupPreparing: "Starting and preparing the memory service…",
} as const;

export const englishMessages = {
  ...baseEnglishMessages,
  ...currentEnglishMessages,
} as const;
