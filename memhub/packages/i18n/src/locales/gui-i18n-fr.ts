import type { MessageKey } from "../gui-i18n.js";
import { embeddingSwitchFrenchMessages } from "../gui-i18n-embedding-switch.js";
import { modeFrenchMessages } from "../gui-i18n-mode.js";

/** French (fr, vous) GUI copy. Machine-assisted; native-speaker review recommended. */
export const frenchMessages: Record<MessageKey, string> = {
  ...modeFrenchMessages,
  ...embeddingSwitchFrenchMessages,
  "connectionFallback.title": "Connecter d’autres outils",
  "connectionFallback.desktopOnly":
    "L’association locale n’est disponible que dans l’application de bureau.",
  "connectionFallback.description":
    "Générez un Skill d’association à usage unique pour un Harness local qui ne figure pas dans la liste. Les chats dans un navigateur, les conteneurs distants et les autres ordinateurs ne peuvent pas se connecter directement à cet appareil.",
  "connectionFallback.name": "Nom du Harness",
  "connectionFallback.namePlaceholder": "Par exemple, Kimi Code",
  "connectionFallback.generating": "Génération…",
  "connectionFallback.generate": "Générer le Skill d’association",
  "connectionFallback.generateFailed":
    "Impossible de générer le Skill d’association.",
  "connectionFallback.retry": "Réessayer",
  "connectionFallback.generated":
    "Skill d’association à usage unique généré pour {name}. Il expire à {time}.",
  "connectionFallback.skillLabel": "Skill d’association",
  "connectionFallback.copy": "Copier le Skill",
  "connectionFallback.copied":
    "Skill d’association copié. Collez-le et exécutez-le dans le Harness correspondant.",
  "connectionFallback.copyFailed":
    "Échec de la copie. Sélectionnez et copiez plutôt le texte ci-dessus.",
  "connectionFallback.localDescription":
    "Réservé aux outils MCP ou en ligne de commande locaux. Les chats dans un navigateur ne peuvent pas se connecter directement à cet appareil.",
  "connectionFallback.loading":
    "Chargement des informations de connexion locales…",
  "connectionFallback.loadFailed":
    "Impossible de charger les informations de connexion.",
  "connectionFallback.format": "Format des informations de connexion",
  "connectionFallback.promptTab": "Prompt de connexion",
  "connectionFallback.mcpTab": "Configuration MCP",
  "connectionFallback.commandTab": "Commande",
  "connectionFallback.materialLabel": "Informations de connexion locales",
  "connectionFallback.materialBoundary":
    "Contient des chemins locaux, mais aucune mémoire ni clé. Vérifiez-le avant de le coller et laissez MemHub ouvert.",
  "connectionFallback.materialCopied":
    "Copié. Aucune connexion n’a encore été établie.",
  "connectionFallback.copyPrompt": "Copier le prompt",
  "connectionFallback.copyConfig": "Copier la configuration",
  "connectionFallback.copyCommand": "Copier la commande",
  "connectionFallback.promptTemplate":
    "Connectez ce Harness local à MemHub avec la configuration MCP suivante :\n```json\n{mcp}```\n{shellBlock}\nUne fois connecté, effectuez une vraie recherche dans les mémoires et indiquez le résultat avec exactitude.",
  "connectionFallback.promptShell":
    "\nSi seules les commandes shell locales sont prises en charge, utilisez :\n```bash\n{shell}```\n",
  "sdk.memory.title": "Bibliothèque de mémoires SDK",
  "sdk.memory.status": "État de la mémoire",
  "sdk.memory.status.active": "Disponible",
  "sdk.memory.status.superseded": "Remplacée",
  "sdk.memory.total": "{count} mémoires",
  "sdk.memory.total.one": "{count} mémoire",
  "sdk.memory.loadFailed":
    "Impossible de charger la bibliothèque de mémoires. Veuillez réessayer.",
  "sdk.memory.detailLoadFailed":
    "Impossible de charger les détails de la mémoire.",
  "sdk.memory.updateFailed":
    "Impossible d’enregistrer la modification. Veuillez réessayer.",
  "sdk.memory.deleteFailed":
    "Impossible de supprimer la mémoire. Veuillez réessayer.",
  "sdk.memory.loading": "Chargement de la bibliothèque de mémoires…",
  "sdk.memory.pagination": "Pages de la bibliothèque de mémoires SDK",
  "sdk.memory.previous": "Page précédente",
  "sdk.memory.next": "Page suivante",
  "sdk.memory.detail": "Détails de la mémoire SDK",
  "sdk.memory.content": "Contenu de la mémoire",
  "sdk.memory.save": "Enregistrer",
  "sdk.memory.delete": "Supprimer la mémoire",
  "sdk.memory.scene.chat": "Discussion",
  "sdk.memory.scene.working": "Travail",
  "sdk.memory.scene.coding": "Code",
  "sdk.memory.task": "Tâche",
  "sdk.memory.solution": "Solution",
  "sdk.memory.page": "Page {page} sur {pages}",
  "sdk.raw.title": "Conversations originales SDK",
  "sdk.raw.searchLabel": "Rechercher dans les conversations originales",
  "sdk.raw.searching": "Recherche…",
  "sdk.raw.search": "Rechercher",
  "sdk.raw.loadFailed":
    "Les conversations originales sont temporairement indisponibles.",
  "sdk.raw.searchFailed":
    "Impossible de rechercher dans les conversations originales. Veuillez réessayer.",
  "sdk.raw.recent": "Conversations originales récentes",
  "sdk.raw.empty":
    "Aucune conversation originale pour le moment. Connectez une source pour les afficher ici.",
  "sdk.raw.total": "{count} conversations originales",
  "sdk.raw.total.one": "{count} conversation originale",
  "memory:当前使用基础记忆。新对话会保存为原始记录，不会生成高级记忆；以前生成的高级记忆仍会保留，但当前不会显示或参与记忆召回。":
    "La mémoire de base est active. Les nouvelles conversations sont conservées telles quelles et ne créent pas de mémoires avancées. Les mémoires avancées existantes restent enregistrées, mais ne sont ni affichées ni rappelées.",
  "memory:当前使用智能记忆。新对话会由模型整理为高级记忆。":
    "La mémoire intelligente est active. Le modèle transforme les nouvelles conversations en mémoires avancées.",
  "memory:当前使用深度记忆。新对话会生成高级记忆，并由知识图谱和 evolve 继续整合。":
    "La mémoire approfondie est active. Les nouvelles conversations créent des mémoires avancées que le graphe de connaissances et evolve continuent de consolider.",
  "sdk.raw.found": "{count} résultats",
  "sdk.raw.found.one": "{count} résultat",
  "memoryInspector.emptyHint":
    "Consultez ses sources, son contenu et son historique des modifications.",
  "memory.operationFailed": "Échec de l’opération sur la mémoire ({code})",
  "memorySpace.tabs.workbench": "Atelier",
  "selectedMemory.status.confirmed": "Créée",
  "selectedMemory.status.active": "Active",
  "selectedMemory.status.deleted": "Supprimée",
  "selectedMemory.status.superseded": "Remplacée",
  "selectedMemory.status.shadow": "Masquée",
  "selectedMemory.status.negated": "Infirmée",
  "selectedMemory.status.conflicted": "En conflit",
  "selectedMemory.status.archived": "Archivée",
  "selectedMemory.status.unknown": "État à vérifier",
  "selectedMemory.sourceUnknown": "Source à vérifier",
  "selectedMemory.sourceUnattributed": "Source non identifiée",
  "selectedMemory.discardDraft":
    "Abandonner les modifications non enregistrées ?",
  "selectedMemory.newVersionReadFailed":
    "Impossible de charger la nouvelle version. Votre brouillon est conservé.",
  "selectedMemory.updateRejected":
    "Les modifications n’ont pas été envoyées. Votre brouillon est conservé ; corrigez le problème et réessayez.",
  "selectedMemory.updateUnknown":
    "Les modifications ne sont pas confirmées. Votre brouillon est conservé ; actualisez avant de réessayer.",
  "selectedMemory.deleteRejected":
    "La suppression n’a pas été envoyée. Vérifiez cette mémoire et réessayez.",
  "selectedMemory.deleteUnknown":
    "La suppression n’est pas confirmée. Vérifiez la mémoire ; la portée de la suppression ne sera pas étendue.",
  "selectedMemory.undoRejected":
    "L’annulation n’a pas été envoyée. Corrigez le problème et réessayez.",
  "selectedMemory.undoUnknown":
    "L’annulation n’est pas confirmée. Vérifiez la version actuelle avant de réessayer.",
  "selectedMemory.author.you": "Vous avez dit",
  "selectedMemory.author.assistant": "Message original de l’assistant",
  "selectedMemory.author.unknown": "Auteur à vérifier",
  "selectedMemory.version": "Version {revision}",
  "selectedMemory.sourceLine": "Source {source} · {time}",
  "selectedMemory.timestamps":
    "Conversation du {conversation} · Créée le {created}",
  "selectedMemory.writeTime": "Écrite le {created}",
  "selectedMemory.edit": "Modifier",
  "selectedMemory.evidence": "Preuves originales",
  "selectedMemory.evidenceLine":
    "{author} · Conversation originale du {time} :",
  "selectedMemory.evidenceUnavailable":
    "Ce texte original est actuellement indisponible.",
  "selectedMemory.viewOriginal": "Voir l’original",
  "selectedMemory.history": "Historique des versions",
  "selectedMemory.more": "Plus d’actions",
  "selectedMemory.undo": "Annuler la dernière modification",
  "selectedMemory.delete": "Supprimer la mémoire",
  "selectedMemory.editTitle": "Modifier la mémoire",
  "selectedMemory.originalTitle": "Conversation originale",
  "selectedMemory.back": "Retour à la mémoire",
  "selectedMemory.editDescription":
    "Modifiez cette mémoire ; la conversation originale reste inchangée.",
  "selectedMemory.content": "Contenu de la mémoire",
  "selectedMemory.scopeUnchanged": "La portée d’accès reste inchangée",
  "selectedMemory.conflictTitle": "Une version plus récente existe",
  "selectedMemory.conflictDescription":
    "Votre brouillon est conservé. Consultez la nouvelle version avant de modifier.",
  "selectedMemory.viewNewVersion": "Voir la nouvelle version",
  "selectedMemory.continueFromVersion":
    "Continuer à modifier à partir de cette version",
  "selectedMemory.cancel": "Annuler",
  "selectedMemory.save": "Enregistrer",
  "selectedMemory.author.user": "Vous",
  "selectedMemory.author.agent": "Assistant",
  "selectedMemory.author.tool": "Outil",
  "selectedMemory.sourceUnavailable":
    "Les autorisations de la source ont changé ou le texte original est indisponible.",
  "selectedMemory.related": "Mémoire associée",
  "selectedMemory.viewMemory": "Voir la mémoire",
  "selectedMemory.deleteTitle": "Supprimer cette mémoire ?",
  "selectedMemory.deleteDescription":
    "Elle ne sera plus utilisée pour les nouvelles recherches. La conversation dans l’application source ne sera pas supprimée.",
  "selectedMemory.sentCannotRecall":
    "Le contenu déjà envoyé aux applications ne peut pas être récupéré à distance.",
  "memory:{count} 条": "Éléments : {count}",
  "memory:已可用 · {count} 段原文": "Disponible · Extraits originaux : {count}",
  "memory:主题视角（非事项）": "Vue par thème (pas par sujet)",
  "memory:来源视角": "Vue par source",
  "memory:{count} 条记忆 · 当前已加载": "Mémoires chargées : {count}",
  "memory:最近 {count} 条": "Conversations récentes : {count}",
  "memory:已确认": "Confirmée",
  "memory:记忆库": "Bibliothèque de mémoires",
  "memory:问记忆": "Interroger les mémoires",
  "memory:历史对话": "Historique des conversations",
  "memory:项目记忆": "Mémoire de projet",
  "memory:编码偏好": "Préférence de code",
  "memory:长期模式": "Tendance de fond",
  "memory:意图": "Intention",
  "memory:近 3 个月": "3 derniers mois",
  "memory:近 6 个月": "6 derniers mois",
  "memory:近 1 年": "Dernière année",
  "memory:全部历史": "Tout l’historique",
  "memory:{count} 条这次没能整理成功，正在重试":
    "Nouvelle tentative de traitement · {count}",
  "memory:{count} 处理中": "En cours de traitement : {count}",
  "memory: · 处理中 {running} / 待派发 {active}":
    " · En cours : {running} / En file : {active}",
  "memory: · {count} 条任务状态待核对":
    " · Tâches dont l’état est à vérifier : {count}",
  "memory: · {count} 条任务状态异常，正在恢复":
    " · Tâches en état anormal, rétablissement en cours : {count}",
  "memory:{count} 等待": "En attente : {count}",
  "memory:最新 {count} 条待处理": "Plus récents en attente : {count}",
  "memory:历史积压 {count} 条": "En retard : {count}",
  "memory:（约 {count} 分钟）": " (environ {count} min)",
  "memory:（约 {count} 小时）": " (environ {count} h)",
  "memory:（约 {count} 天）": " (environ {count} j)",
  "memory:同步尝试 {count} 次": "Tentatives de synchronisation : {count}",
  "memory:尚未尝试同步": "Synchronisation non tentée",
  "memory: · 状态更新于 {date}": " · Mis à jour le {date}",
  "memory:HY Memory 当前没有这条记忆，仅重试同步不能补齐。":
    "HY Memory ne contient pas cette mémoire ; relancer la synchronisation ne suffira pas à la rétablir.",
  "memory:同步需要可用的本机整理模型或本地嵌入授权。":
    "La synchronisation nécessite un modèle de traitement local disponible ou l’autorisation d’utiliser les embeddings locaux.",
  "memory:这条记忆或模型版本已经变化，先刷新核对再决定下一步。":
    "Cette mémoire ou la version du modèle a changé. Actualisez et vérifiez avant de décider de la suite.",
  "memory:这条记忆已改，搜索索引还没开始同步。":
    "Cette mémoire a changé, mais la synchronisation de l’index de recherche n’a pas commencé.",
  "memory:最近一次同步未成功；可以先重试，结果仍不确定时再刷新核对。":
    "La dernière synchronisation a échoué. Réessayez d’abord ; si le résultat reste incertain, actualisez et vérifiez.",
  "memory:这条记忆已改，搜索索引还没跟上。":
    "Cette mémoire a changé, mais l’index de recherche n’est pas encore à jour.",
  "memory:搜索里还是改之前那句话。":
    "La recherche affiche encore le texte d’avant la correction.",
  "memory:重试同步": "Relancer la synchronisation",
  "memory:配置整理模型": "Configurer le modèle de traitement",
  "memory:未提炼 · 在窗口外": "Non extrait · Hors de la fenêtre",
  "memory:未提炼 · 排队中": "Non extrait · En file d’attente",
  "wb.select": "Sélectionner",
  "memory:模型综合": "Synthèse du modèle",
  "memory:原文摘录": "Extrait original",
  "memory:没有命中": "Aucun résultat",
  "memory:正在生成": "Génération",
  "memory:正在检索记忆": "Recherche dans les mémoires",
  "memory: · 基于 {count} 条已确认记忆":
    " · D’après {count} mémoires confirmées",
  "memory: · 基于 {count} 条已确认记忆.one":
    " · D’après {count} mémoire confirmée",
  "memory:整理需要处理": "Le traitement requiert votre attention",
  "memory:正在整理记忆": "Traitement des mémoires",
  "memory:整理状态": "État du traitement",
  "memory:自动提炼范围": "Fenêtre d’extraction automatique",
  "memory:手动提炼下一批": "Extraire manuellement le lot suivant",
  "memory:刷新核对": "Actualiser et vérifier",
  "memory:正在导入历史会话，记忆库暂时只显示一部分；导入完成后会自动补全，不需要重试。":
    "Importation de l’historique des conversations. Seule une partie des mémoires est visible ; la liste se complétera automatiquement après l’importation.",
  "memory:选择：{content}": "Sélectionner : {content}",
  "memory:处理失败：{content}": "Échec du traitement : {content}",
  "memory:已选 {count} 条": "{count} sélectionnées",
  "memory:已选 {count} 条.one": "{count} sélectionnée",
  "memory:删除所选 {count} 条": "Supprimer la sélection ({count})",
  "memory:删除所选 {count} 条.one": "Supprimer la sélection ({count})",
  "memory:重试失败的 {count} 条": "Réessayer les {count} éléments en échec",
  "memory:重试失败的 {count} 条.one": "Réessayer l’élément en échec",
  "memory:没有{status}的记忆。": "Aucune mémoire avec l’état « {status} ».",
  "memory:已{action} {count} 条":
    "Action effectuée ({action}) : {count} éléments",
  "memory:已{action} {count} 条.one":
    "Action effectuée ({action}) : {count} élément",
  "memory:，{count} 条未完成": ", {count} non terminés",
  "memory:，{count} 条未完成.one": ", {count} non terminé",
  "memory:未能{action}：{error}": "Échec de l’action ({action}) : {error}",
  "memory:重试{action}": "Réessayer ({action})",
  "memory:删除（操作）": "Supprimer",
  "memory:处理（操作）": "traiter",
  "memory:{count} 条原话": "{count} extraits originaux",
  "memory:{count} 条原话.one": "{count} extrait original",
  "memory:{count} 次整理已完成": "{count} traitements terminés",
  "memory:{count} 次整理已完成.one": "{count} traitement terminé",
  "memory: · {count} 条在窗口外（原文可搜）":
    " · {count} hors de la fenêtre (texte original consultable)",
  "memory: · {count} 条在窗口外（原文可搜）.one":
    " · {count} hors de la fenêtre (texte original consultable)",
  "memory: · 已规划 {planned} 批，当前第 {current} 批":
    " · Lots prévus : {planned}, lot en cours : {current}",
  "memory:{kind}卡在 {reason}（{count} 条）":
    "{kind} bloqué par {reason} ({count})",
  "memory:已加载 {count} 条记忆": "{count} mémoires chargées",
  "memory:已加载 {count} 条记忆.one": "{count} mémoire chargée",
  "memory:回答只使用已确认记忆。每个答案都可以回到对应记忆和来源原话。":
    "Les réponses n’utilisent que des mémoires confirmées. Chaque réponse renvoie à la mémoire et au texte original.",
  "memory:选择一条记忆": "Sélectionnez une mémoire",
  "memory:查看它的来源、使用范围和修改历史。":
    "Consultez ses sources, sa portée d’accès et son historique des modifications.",
  "memory:查看替代它的记忆": "Voir la mémoire qui l’a remplacée",
  "memory:查看它取代的旧记忆": "Voir la mémoire qu’elle a remplacée",
  "memory:保存纠正": "Enregistrer la correction",
  "memory:修改历史": "Historique des modifications",
  "memory:已可用": "Disponible",
  "memory:处理中…": "Traitement…",
  "memory:搜索": "Rechercher",
  "memory:记忆治理状态": "État des mémoires",
  "memory:记忆空间模式": "Modes de l’espace mémoire",
  "memory:来源状态待核对，记忆库暂不可读取。":
    "L’état de la source est à vérifier ; la bibliothèque de mémoires ne peut pas encore être lue.",
  "memory:记忆库读取失败，请重试。":
    "Impossible de charger la bibliothèque de mémoires. Veuillez réessayer.",
  "memory:重新读取记忆库": "Recharger la bibliothèque de mémoires",
  "memory:正在读取记忆库…": "Chargement de la bibliothèque de mémoires…",
  "memory:批量操作结果": "Résultat de l’action groupée",
  "memory:批量操作": "Actions groupées",
  "memory:取消选择": "Effacer la sélection",
  "memory:向已确认记忆提问": "Interroger les mémoires confirmées",
  "memory:搜索记忆库": "Rechercher dans la bibliothèque de mémoires",
  "memory:问答记忆范围": "Portée des questions sur les mémoires",
  "memory:只问个人画像": "Interroger uniquement le profil personnel",
  "memory:问全部记忆（含项目）":
    "Interroger toutes les mémoires (projets compris)",
  "memory:例如：我对真实数据有什么要求？":
    "Par exemple : quelles sont mes exigences concernant les données réelles ?",
  "memory:搜索偏好、决定或经历":
    "Rechercher des préférences, décisions ou expériences",
  "memory:根据记忆回答": "Répondre d’après les mémoires",
  "memory:清除搜索": "Effacer la recherche",
  "wb.finding.duplicate": "Issues du même message original",
  "wb.finding.duplicateText":
    "« {first} » et « {second} » proviennent du même message original et ont été classées dans {firstScope} et {secondScope}. Vérifiez si elles désignent la même chose.",
  "wb.finding.dormant": "Pas mises à jour récemment",
  "wb.finding.dormantText":
    "{count} mémoires n’ont été ni modifiées ni mentionnées depuis deux mois. Elles méritent peut-être une vérification.",
  "wb.finding.blocked": "En attente d’une condition",
  "wb.finding.blockedText":
    "{count} conversations sont en file d’attente dans l’attente d’une condition. Leur texte original est déjà enregistré et consultable.",
  "wb.finding.blockedText.one":
    "{count} conversation est en file d’attente dans l’attente d’une condition. Son texte original est déjà enregistré et consultable.",
  "wb.unclassified": "Non classé",
  "wb.profile": "Profil personnel",
  "wb.project": "Projet {id}",
  "wb.topics": "Thème",
  "wb.all": "Tout",
  "wb.topicCount": "{count} thèmes constitués",
  "wb.topicCount.one": "{count} thème constitué",
  "wb.singletons": " ; {count} autres étiquettes n’apparaissent qu’une fois",
  "wb.singletons.one": " ; {count} autre étiquette n’apparaît qu’une fois",
  "wb.memories": "{count} mémoires",
  "wb.memories.one": "{count} mémoire",
  "wb.sources": "{count} sources",
  "wb.sources.one": "{count} source",
  "wb.once":
    "Ce thème n’apparaît qu’ici. Il peut s’agir d’un cas isolé, ou le même thème a pu être scindé sous des noms différents.",
  "wb.graphTitle": "Graphe des relations : {label}",
  "wb.readSummary": "Lire une synthèse de ce sujet",
  "wb.summaryPrompt":
    "Expliquez clairement « {label} » : où en sont les choses, ce qui a été décidé et ce qui reste en suspens.",
  "wb.building": "Synthèse en cours…",
  "wb.build": "Établir les relations avec HY Memory",
  "wb.drawn": "Affichage de {shown} / {total} mémoires",
  "wb.askLabel": "Poser une question sur ce sujet",
  "wb.askPlaceholder":
    "Posez une question sur ce sujet ; les réponses n’utilisent que des mémoires confirmées",
  "wb.ask": "Demander",
  "wb.originals": "Conversations originales",
  "wb.evidenceNote":
    "Cette mémoire provient des {count} extraits originaux ci-dessous, classés par date",
  "wb.evidenceNote.one":
    "Cette mémoire provient de l’extrait original ci-dessous",
  "wb.pickCircle":
    "Sélectionnez un cercle du graphe pour afficher ici son texte original",
  "wb.unavailable": "Le texte original de la source est indisponible",
  "wb.fullConversation": "Voir la conversation complète",
  "wb.you": "Vous",
  "wb.assistant": "Assistant",
  "wb.noMemories":
    "Aucune mémoire pour l’instant. Recherchez les conversations originales importées dans l’historique des conversations.",
  "wb.waiting":
    "Des conversations sont en file d’attente dans l’attente d’une condition. Leur texte original est enregistré et consultable.",
  "wb.conditions": "Voir ce qui manque",
  "wb.findings": "Ce que révèlent vos données",
  "wb.findingsNote":
    "Calculé à partir des données locales. Sélectionnez un élément pour mettre en évidence les mémoires qui l’étayent sur le graphe.",
  "wb.showGraph": "Voir sur le graphe",
  "wb.loading": "Chargement des mémoires…",
  "wb.coreStarting":
    "Le contenu apparaîtra automatiquement après le démarrage du Core local, sans actualisation.",
  "wb.nothingToConnect": "Il n’y a encore rien à relier.",
  "wb.skeletonNote":
    "Autorisez une source pour voir la structure des sources et des thèmes ; les mémoires apparaîtront au fur et à mesure de leur création.",
  "wb.authorize": "Autoriser une source",
  "wb.conversations": "{count} conversations",
  "wb.conversations.one": "{count} conversation",
  "wb.chipChanges": "Qu’est-ce qui a changé récemment ici ?",
  "wb.chipSummarize": "Résumez ceci en quelques points",
  "wb.unknownTime": "Date inconnue",
  "wb.unknownSource": "Source inconnue",
  "wb.transcriptGone": "Cette conversation n’est plus dans la bibliothèque.",
  "wb.edgeCount": "{label} : {count} liens",
  "wb.edgeCount.one": "{label} : {count} lien",
  "wb.noRelations":
    "HY Memory n’a pas encore trouvé de relations. Utilisez le bouton ci-dessus pour lancer System 2.",
  "wb.noModelRelations":
    "Aucun modèle utilisable n’est configuré ; les relations HY Memory sont indisponibles.",
  "provenance.multiple": "Plusieurs auteurs",
  "provenance.person": "Message original de l’utilisateur",
  "provenance.agent": "Message original de l’agent",
  "provenance.unknown": "Auteur inconnu",
  "provenance.imported": "Mémoire importée · {author}",
  "provenance.manual": "Ajoutée manuellement",
  "provenance.answered": "Créée à partir d’une réponse",
  "provenance.generated": "Créée à partir d’une réponse de l’IA",
  "provenance.conversation": "Créée à partir des conversations originales",
  "arrival.region": "État du traitement des mémoires",
  "arrival.importing": "Importation des originaux en cours",
  "arrival.messages": "{count} messages enregistrés",
  "arrival.messages.one": "{count} message enregistré",
  "arrival.conversations": "{count} conversations",
  "arrival.conversations.one": "{count} conversation",
  "arrival.details": "Voir la progression et les détails",
  "arrival.backlog":
    "Volume important de données existantes : traitement par lots en arrière-plan pour garantir la qualité",
  "arrival.items": "{count} éléments",
  "arrival.items.one": "{count} élément",
  "arrival.configure": "Configurer le traitement des mémoires",
  "arrival.status": "Voir l’état du traitement",
  "arrival.history": "Voir les conversations enregistrées",
  "arrival.connect": "Connecter une application",
  "arrival.retained": "Les originaux enregistrés restent sur cet appareil.",
  "arrival.description.model-required":
    "Vérifiez le modèle de traitement avant de continuer.",
  "arrival.description.processing":
    "Traitement prioritaire des conversations récentes ; d’autres mémoires apparaîtront progressivement",
  "arrival.description.importing":
    "Enregistrement des originaux ; la progression de l’importation ne reflète pas celle de la création des mémoires.",
  "arrival.description.failed":
    "Vérifiez la cause de l’échec avant de décider de réessayer.",
  "arrival.description.unavailable":
    "Les dernières informations confirmées sont conservées ; vérifiez à nouveau la progression actuelle.",
  "arrival.description.refreshing":
    "Vérification des données locales ; la progression non confirmée n’est pas comptabilisée.",
  "arrival.description.checking":
    "Le contenu enregistré est conservé ; la progression doit être mise à jour.",
  "arrival.description.stopped":
    "Le traitement est arrêté ; vérifiez son état avant de continuer.",
  "arrival.description.idle": "Aucun traitement en cours.",
  "arrival.description.empty":
    "Connectez une application ou importez un fichier de conversations ; les originaux seront d’abord enregistrés sur cet appareil.",
  "arrival.title.refreshing":
    "Contenu enregistré conservé ; vérification de la progression",
  "arrival.title.checking": "Progression à mettre à jour",
  "arrival.title.importing": "Importation des conversations",
  "arrival.title.processing": "Création des mémoires",
  "arrival.title.continue": "Votre attention est requise",
  "arrival.title.model": "Modèle de traitement non configuré",
  "arrival.title.empty": "Aucune conversation enregistrée",
  "handoff.region": "Emporter des mémoires dans une autre application",
  "handoff.answer":
    "Emporter la réponse et ses références dans une autre application",
  "handoff.selected":
    "Emporter les mémoires sélectionnées dans une autre application",
  "handoff.title": "Emporter dans une autre application",
  "handoff.closeTitle": "Fermer l’aperçu",
  "handoff.close": "Fermer l’aperçu du transfert",
  "handoff.stats": "{count} mémoires · {characters} caractères · Non tronqué",
  "handoff.stats.one":
    "{count} mémoire · {characters} caractères · Non tronqué",
  "handoff.content": "Contenu complet à emporter dans une autre application",
  "handoff.target": "Application de destination",
  "handoff.other": "Autre application · Copie uniquement",
  "handoff.copyOnly": " · Copie uniquement",
  "handoff.boundary":
    "Seul le contenu ci-dessus est inclus, sans les conversations originales. La copie n’autorise aucune connexion ; les copies présentes dans d’autres applications ne peuvent pas être récupérées.",
  "handoff.copy": "Copier le contenu",
  "handoff.open": "Copier et ouvrir {host}",
  "handoff.copyFailed":
    "Échec de la copie ; aucune application n’a été ouverte. Sélectionnez et copiez plutôt le texte de l’aperçu.",
  "handoff.opened":
    "Copié, ouverture de {host} demandée. Rien n’a été envoyé ; vérifiez le champ de saisie de destination.",
  "handoff.notPrefilled":
    "Copié intégralement. Impossible de préremplir {host} ; collez le contenu dans l’application de destination. Rien n’a été envoyé.",
  "handoff.openFailed":
    "Copié intégralement, mais impossible d’ouvrir {host}. Rien n’a été envoyé.",
  "handoff.copied": "{target} copié intégralement. Rien n’a été envoyé.",
  "handoff.pasteTarget": ", prêt à être collé dans {host}",
  "wb.memory": "Cette mémoire",
  "wb.node": "Ce nœud",
  "wb.matter": "Ce sujet",
  "wb.available": "Disponible",
  "wb.superseded": "Remplacée",
  "wb.deleted": "Supprimée",
  "wb.revision": "Version {revision}",
  "wb.collapse": "Réduire",
  "wb.oldSearch": "La recherche affiche encore le texte d’avant la correction.",
  "wb.missingIndex":
    "HY Memory ne contient pas cette mémoire ; réessayer ne permettra pas de la rétablir.",
  "wb.retrySync": "Relancer la synchronisation",
  "wb.correctForm": "Corriger le contenu de la mémoire",
  "wb.content": "Contenu de la mémoire",
  "wb.correct": "Corriger",
  "wb.saveCorrection": "Enregistrer la correction",
  "wb.cancelEdit": "Annuler la modification",
  "wb.undo": "Annuler la dernière modification",
  "wb.delete": "Supprimer",
  "wb.nodeNote":
    "Ceci n’est pas une mémoire ; des mémoires y sont rattachées. Liens directs : {direct} ; accessibles en deux sauts : {indirect}.",
  "wb.direct": "Direct",
  "wb.twoHops": "Deux sauts",
  "wb.idle":
    "Les cercles représentent les mémoires ; les lignes relient celles mentionnées dans la même conversation. Sélectionnez-en une pour voir son origine et la modifier. Choisir un autre sujet à gauche met à jour le graphe.",
  "wb.searching": "Recherche dans les mémoires…",
  "wb.generated": "Synthèse du modèle",
  "wb.extractive": "Extrait original",
  "wb.noHit": "Aucun résultat",
  "wb.answerSource": "{mode} · D’après {count} mémoires confirmées",
  "wb.answerSource.one": "{mode} · D’après {count} mémoire confirmée",
  "wb.recalled": "{count} mémoires retrouvées",
  "wb.recalled.one": "{count} mémoire retrouvée",
  "wb.saveMemory": "Enregistrer comme mémoire",
  "wb.tooLong":
    "Cette réponse dépasse la limite actuelle et ne peut pas être enregistrée comme mémoire.",
  "wb.summary":
    "Ceci est une synthèse, pas une mémoire. Elle est régénérée à chaque fois pour rester cohérente avec les mémoires ci-dessous.",
  "wb.thisAnswer": "Cette réponse",
  "wb.type.source": "Application source",
  "wb.type.topic": "Thème",
  "wb.type.schema": "Synthèse HY",
  "wb.type.memory": "Mémoires",
  "wb.origin.hy_relation": "Synthèse HY Memory",
  "wb.origin.same_conversation": "Même conversation",
  "wb.origin.shared_tag": "Même thème",
  "wb.origin.provenance": "Source du texte original",
  "wb.relation.evidence_source": "Provient de",
  "wb.relation.same_conversation": "Même conversation",
  "wb.relation.shared_tag": "Même thème",
  "wb.relation.derived_from": "Synthétisé à partir de",
  "wb.relation.tagged_with": "Étiqueté",
  "wb.relation.related_to": "Lié",
  guideEyebrow: "Essayer",
  guideTitle: "Reprenez là où votre dernière conversation s’est arrêtée",
  guideBack: "Retour à la mémoire",
  guideChooseScene: "Choisissez un scénario",
  guideSame: "Continuer dans une nouvelle conversation",
  guideSameDescription:
    "Retrouvez des décisions antérieures dans une nouvelle conversation avec la même application.",
  guideSwitch: "Continuer dans une autre application",
  guideSwitchDescription:
    "Même travail, autre IA. Apportez d’abord le contexte pertinent.",
  guideNew: "Aider un nouvel outil à me connaître",
  guideNewDescription:
    "Commencez par un détail que vous choisissez de partager, au lieu de vous présenter à nouveau.",
  guideReadError:
    "Impossible de lire les mémoires pour le moment. Réessayez plus tard.",
  guideDetailError:
    "Cette mémoire est illisible pour le moment. Choisissez-en une autre ou actualisez.",
  guideChanged:
    "La mémoire ou ses autorisations ont changé. Actualisez et vérifiez-la à nouveau.",
  guideConnectionChanged:
    "L’état de la connexion a changé ; rien n’a été copié. Choisissez à nouveau une méthode.",
  guideCopiedOpened:
    "Copié, ouverture de {host} demandée. Rien n’a été envoyé ; vérifiez le champ de saisie.",
  guideCopiedNotFilled:
    "Copié intégralement, mais impossible de préremplir. Collez-le dans l’application de destination ; rien n’a été envoyé.",
  guideCopiedNotOpened:
    "Copié intégralement, mais impossible d’ouvrir l’application de destination. Rien n’a été envoyé.",
  guideCopied:
    "Copié intégralement. Rien n’a été envoyé et aucun nouvel accès en lecture n’a été accordé.",
  guideValidationError:
    "Impossible de vérifier la mémoire ou les autorisations actuelles. L’ancien aperçu a été masqué ; actualisez et réessayez.",
  guideCopyError:
    "Impossible de vérifier le dernier contenu ou de terminer la copie. Réessayez ; l’application de destination n’a pas été ouverte.",
  guideManual: "Ajoutée dans l’application",
  guideOriginalApp: "Application source",
  guideListSeparator: ", ",
  guideSaved: "{count} messages originaux enregistrés sur cet appareil · ",
  "guideSaved.one": "{count} message original enregistré sur cet appareil · ",
  guideReadingMemories: "Chargement des mémoires traitées",
  guideLoaded: "{count} mémoires utilisables chargées sur cette page",
  "guideLoaded.one": "{count} mémoire utilisable chargée sur cette page",
  guideSeparate: ". Ces étapes sont comptabilisées séparément.",
  guideChooseBackground: "Choisir le contexte",
  guideCheckSources: "Vérifier les sources",
  guideTry: "Essayer dans une nouvelle conversation",
  guideLoading: "Chargement des mémoires présentes sur cet appareil…",
  guideEmptyTitle: "Ajoutez d’abord un élément de contexte utile",
  guideEmptyDescription:
    "Aucune mémoire n’est disponible pour cet essai. Les originaux importés ne sont pas des mémoires traitées, et les suggestions ne sont pas partagées automatiquement.",
  guideAdd: "Ajouter une mémoire",
  guideConnections: "Voir les importations et connexions",
  guideRefresh: "Actualiser",
  guideBackgroundTitle: "Contexte de cet essai",
  guideRefreshExample: "Actualiser l’exemple",
  guideChooseMemory: "Sélectionnez une mémoire",
  guideLoadedScope:
    "Choisissez parmi les {count} mémoires utilisables chargées ici ; ce n’est pas l’ensemble de votre historique.",
  "guideLoadedScope.one":
    "Choisissez parmi les mémoires utilisables chargées ici ({count}) ; ce n’est pas l’ensemble de votre historique.",
  guideCheckingEvidence: "Vérification des preuves originales…",
  guideRevision:
    "{source} · Version {revision} · Générée par le système, à vérifier",
  guideScope: "Portée : ",
  guideAllApps:
    "Disponible dans toutes les applications ; pas nécessairement une préférence personnelle",
  guidePrivate:
    "La recherche externe est désactivée ; un partage manuel exige une décision distincte",
  guideLimited: "Portée limitée ; la recherche externe n’a pas été étendue",
  guideEvidence: "Preuves originales · {count} extraits",
  "guideEvidence.one": "Preuves originales · {count} extrait",
  guideUnknownSource: "Source inconnue",
  guideYou: "Vous",
  guideUnknownAuthor: "Auteur inconnu",
  guideUnknownTime: "Date inconnue",
  guideUnavailable:
    "Le texte original ou l’autorisation actuelle est indisponible. Choisissez une autre mémoire ou actualisez ; ce contenu ne sera pas partagé.",
  guideDraftTitle: "Introduction de la prochaine conversation",
  guideChooseApp: "Choisissez une application pour l’essai",
  guideCopyOnly: "Choisir soi-même l’application · Copie uniquement",
  guideHostsError:
    "Impossible de lire l’état des connexions. Réessayez ou copiez uniquement le contexte.",
  guideNoApp:
    "Aucune application connectée sélectionnée. Le partage manuel n’installe aucune connexion.",
  guideWired:
    "Le dernier autotest a réussi ; vérifiez que cela fonctionne pour cet usage.",
  guideInstalled:
    "Connexion installée ; l’accès en lecture n’a pas été vérifié.",
  guideNotInstalled: "Connexion en lecture non installée.",
  guideManage: "Gérer les connexions des applications",
  guideChooseRoute: "Choisissez comment apporter le contexte",
  guideContext: "Apporter le contexte",
  guideQuery: "Laisser l’application chercher",
  guideTopic: "Sujet à poursuivre",
  guideQueryHint:
    "Envoyez seulement une requête ; ne placez pas la réponse attendue ci-dessus dans la nouvelle conversation.",
  guideNewHint:
    "Seule la mémoire choisie est partagée ; aucun profil personnel n’est créé automatiquement.",
  guideContextHint:
    "Inclut ce contexte et ses citations ; il ne s’agit pas d’un test de recherche automatique.",
  guidePreview: "Aperçu complet de l’introduction",
  guidePreviewPlaceholder:
    "L’introduction apparaîtra lorsque les preuves originales seront disponibles",
  guideChecking: "Vérification…",
  guideCopy: "Copier l’introduction",
  guideCopyOpen: "Copier et ouvrir {host}",
  guideNoReceipt:
    "Aucun accusé de réception d’un appel externe cette fois-ci. La nouvelle conversation a-t-elle cité le bon contexte ?",
  guideVerified: "J’ai vérifié le résultat",
  guideMissing: "Introuvable",
  guideFeedbackOk:
    "Votre retour : vérifié. Il ne s’agit pas d’une vérification automatique.",
  guideFeedbackMissing:
    "Votre retour : introuvable. Vérifiez d’abord l’importation des sources, la portée de la mémoire et les connexions des applications.",
  guideInvitation: "Invitation à essayer",
  guideInvitationHint:
    "Essayez de poursuivre une nouvelle conversation avec l’une de vos mémoires.",
  guideTryOnce: "Essayer",
  guideLater: "Plus tard",
  guideDismiss: "Ignorer l’invitation",
  healthTitle: "Diagnostic de fonctionnement",
  healthLocal: "Service local",
  healthBackground: "Service en arrière-plan",
  healthSources: "Totaux par source",
  healthProcessing: "Traitement des mémoires",
  healthHosts: "Connexions IA",
  healthDetailed: "Journalisation détaillée",
  healthDetailedTitle: "Journalisation détaillée (pour le dépannage)",
  healthDetailedHint:
    "Désactivée par défaut. Activez-la uniquement si l’assistance vous le demande pour un dépannage.",
  healthDetailedError:
    "Impossible de modifier la journalisation détaillée. Veuillez réessayer.",
  healthRefresh: "Actualiser l’état",
  healthIncident: "Marquer l’heure du problème",
  healthIncidentDesktop:
    "Marquez l’heure du problème dans l’application de bureau.",
  healthIncidentPrompt: "Décrivez brièvement le problème (facultatif)",
  healthIncidentCopied: "Identifiant du problème copié : {id}",
  healthIncidentError:
    "Impossible de marquer l’heure du problème. Veuillez réessayer.",
  healthReadFailed: "Temporairement indisponible",
  healthStale: " · Données obsolètes ; affichage du dernier résultat.",
  healthReading: "Lecture de l’état…",
  healthRereading: "Actualisation ; affichage de la dernière lecture réussie…",
  healthReadSuccessTime: "Dernière lecture réussie",
  healthReadFailedTime: "Échec de lecture à",
  healthReadingSources: "Lecture de l’état du service local et des sources…",
  healthPrevious: "Dernière lecture",
  healthPreviousPrefix: "Dernière lecture : ",
  healthRunning: "En cours d’exécution",
  healthCoreVersion: "{state} · Version {version} · Instance {instance}",
  healthCoreDisconnected: "Le Core local n’est pas encore connecté.",
  healthBackgroundUnavailable:
    "Impossible de vérifier le service en arrière-plan ici. Utilisez une application de bureau compatible.",
  healthRuntime: "Core : {core} · Passerelle hôte : {broker}",
  healthSourceCount: "Conversations : {conversations} · Messages : {messages}",
  healthNoSources: "Aucune source connectée pour l’instant.",
  healthTaskCounts:
    "En cours : {running} · En file : {pending} · Terminés : {completed} · Échecs : {failed}",
  healthImportDetails: "Détails de l’importation et du traitement",
  healthRawTurns: "Texte original et tours complets",
  healthRawCounts: "Originaux : {raw} · Tours complets : {turns}",
  healthHy: "Traitement HY",
  healthHyCounts:
    "Appels : {calls} · Terminés : {completed} · Incertains : {unknown} · Échecs : {failed} · Annulés : {cancelled}",
  healthOutput: "Production",
  healthMemories: "{count} mémoires",
  "healthMemories.one": "{count} mémoire",
  healthWorkbuddy: "Recherches de mémoires WorkBuddy",
  healthWorkbuddyCounts:
    "Injections : {injected} · Délais dépassés : {timedOut} · Indisponibles : {unavailable}",
  healthBlocked: "{kind} bloqué par {reason} ({count})",
  healthRetrying: "Nouvelle tentative de traitement · {count}",
  "healthRetrying.one": "Nouvelle tentative de traitement · {count}",
  healthNoIssues: "Aucun problème à traiter.",
  healthHostsUnavailable:
    "Lecture impossible dans un navigateur ; consultez l’application de bureau.",
  healthNoHosts: "Aucun outil d’IA compatible détecté sur cet appareil.",
  healthBackgroundHealthy: "Service en arrière-plan disponible",
  healthBackgroundStopped: "Service en arrière-plan arrêté",
  healthBackgroundDegraded:
    "Service en arrière-plan partiellement indisponible",
  healthBackgroundUntrusted:
    "Impossible de vérifier l’identité du service en arrière-plan",
  healthBackgroundUnknown: "État de l’arrière-plan non confirmé",
  healthStopped: "Arrêté",
  healthUnhealthy: "Connexion impossible",
  healthUntrusted: "Identité non vérifiée",
  healthUnknown: "Non confirmé",
  healthNotChecked: "Non vérifié",
  healthWired: "Connecté",
  healthBlind: "Installé, mais impossible de récupérer les mémoires",
  healthUnreachable: "Non connecté",
  startupReadError:
    "Impossible de lire l’état du démarrage. Veuillez réessayer.",
  startupRetry: "Réessayer",
  startupOpening: "Ouverture de MemHub…",
  nativeHelp: "Aide",
  nativeExportLogs: "Exporter les journaux pour l’assistance",
  nativeExportError:
    "Impossible d’exporter les journaux. Veuillez réessayer plus tard.",
  nativeLogin: "Lancer {product} à l’ouverture de session",
  nativeTrialUnavailable: " (indisponible dans cette version)",
  nativeCliManaged: " (géré par la CLI)",
  nativeStatusUnavailable: " (état indisponible)",
  nativeDevelopmentUnavailable: " (indisponible en mode développement)",
  nativeOpen: "Ouvrir {product}",
  nativeQuit:
    "Quitter {product} (le service en arrière-plan continue de fonctionner)",
  nativeQuitStopsService:
    "Quitter {product} (le service de mémoire s’arrête aussi)",
  nativeLoginError:
    "Impossible de modifier le réglage de lancement à l’ouverture de session. Veuillez réessayer.",
  nativeMoveTitle: "Déplacez d’abord MemHub dans Applications",
  nativeMoveDetail:
    "Rouvrez MemHub depuis Applications pour connecter d’autres applications et installer l’outil en ligne de commande.",
  nativeAcknowledge: "OK",
  graphWidthTitle:
    "La bibliothèque de mémoires existante ne correspond pas au modèle intégré",
  graphWidthDescription:
    "Votre bibliothèque de mémoires utilise {stored} dimensions, mais le modèle intégré en produit {expected}. Ils ne peuvent pas fonctionner ensemble ; l’extraction continuera d’échouer tant que vous n’aurez pas choisi une option.",
  graphWidthKeep:
    "Conserver la bibliothèque existante (par défaut) : continuer à utiliser la configuration d’embeddings précédente ; les {count} mémoires et le graphe restent inchangés",
  graphWidthRebuild:
    "Utiliser le modèle intégré : archiver (sans supprimer) le graphe existant ; les relations des {count} mémoires devront être reconstruites",
  graphWidthExport:
    "Exporter d’abord, puis décider : ne rien modifier pour l’instant et choisir à nouveau après l’exportation",
  graphWidthExported:
    "Exporté vers {path}. La bibliothèque existante n’a pas été modifiée ; vous pouvez choisir à nouveau.",
  graphWidthUnknown:
    "Le choix de dimension n’est pas confirmé. La bibliothèque de mémoires existante n’a pas été modifiée ; veuillez réessayer.",
  graphWidthConfirm: "Confirmer le choix",
  importUserProgress:
    "Importation de votre historique de conversations. {count} conversations importées. Les mémoires apparaîtront progressivement ; vous pouvez continuer à utiliser l’application.",
  "importUserProgress.one":
    "Importation de votre historique de conversations. {count} conversation importée. Les mémoires apparaîtront progressivement ; vous pouvez continuer à utiliser l’application.",
  importUserModel:
    "Le modèle n’est pas encore configuré. Les nouvelles conversations ne seront pas transformées en mémoires pour l’instant.",
  importUserIncomplete:
    "Certains contenus n’ont pas été entièrement traités. Consultez les paramètres.",
  importUserSettings: "Ouvrir les paramètres",
  diagnosticExportDesktop:
    "Exportez les diagnostics depuis l’application de bureau.",
  diagnosticExported: "Journaux exportés : {filename}",
  diagnosticFile: "fichier de diagnostic",
  diagnosticExportFailed:
    "Impossible d’exporter les diagnostics. Veuillez réessayer.",
  debugTitle: "Journaux de débogage",
  debugCount: "{count} journaux de cette session",
  "debugCount.one": "{count} journal de cette session",
  debugDiagnostics: "Diagnostics",
  debugUsageSubtitle: "Utilisation du modèle de traitement des mémoires",
  debugTabs: "Onglets de diagnostic",
  debugLogsTab: "Journaux",
  debugUsageTab: "Utilisation du modèle",
  debugFullscreen: "Plein écran",
  debugExitFullscreen: "Quitter le plein écran",
  debugFullscreenLabel: "Afficher les journaux en plein écran",
  debugExitFullscreenLabel: "Quitter le plein écran des journaux",
  usageRange15m: "15 dernières minutes",
  usageRange1h: "Dernière heure",
  usageRange6h: "6 dernières heures",
  usageRange24h: "24 dernières heures",
  usageRange7d: "7 derniers jours",
  usageRange: "Période",
  usageRangeLabel: "Période d’utilisation du modèle",
  usageLoading: "Chargement…",
  usageReadError: "Échec du chargement",
  usageLive: "Actualiser toutes les 5 secondes",
  usageTotalTokens: "Total des tokens",
  usageRequests: "Requêtes",
  usageChartRegion: "Tokens par minute et par prompt",
  usageChartTitle: "Utilisation des tokens par prompt",
  usageChartSubtitle: "Par minute · Total des tokens",
  usageBreakdown: "Détails par prompt",
  usageCurrentRange: "Période actuelle",
  usageRequestCount: "{count} requêtes",
  "usageRequestCount.one": "{count} requête",
  usageEmpty:
    "Aucun appel au modèle de traitement des mémoires sur cette période",
  usageNoChart: "Aucune donnée de graphique pour l’instant",
  usageChartAria: "Utilisation des tokens par minute et par prompt",
  debugPause: "Suspendre l’actualisation en direct",
  debugResume: "Reprendre l’actualisation en direct",
  debugRefresh: "Actualiser maintenant",
  debugClose: "Fermer les journaux de débogage",
  debugSearchPlaceholder:
    "Rechercher des événements, composants, codes d’erreur ou contenus",
  debugSearch: "Rechercher dans les journaux",
  debugSource: "Source des journaux",
  debugAllSources: "Toutes les sources",
  debugCapture: "Niveau de capture",
  debugDisplay: "Niveau d’affichage",
  debugAll: "Tout",
  debugMatches: "{count} correspondances",
  "debugMatches.one": "{count} correspondance",
  debugLive: "Actualisation en direct",
  debugPaused: "En pause",
  debugTruncated: "Trop de journaux ; liste tronquée",
  debugReadError: "Échec du chargement",
  debugLoading: "Chargement des journaux…",
  debugEmpty: "Aucun journal correspondant",
  debugOpened: "Les journaux de débogage sont ouverts",
  debugOpen: "Ouvrir les journaux de débogage",
  "这里需要 API Key，不是网址。请填写服务方提供的密钥。":
    "Saisissez la clé API fournie par le service, pas une URL.",
  "服务商拒绝了这个 API Key（401）。请确认密钥属于当前填写的服务地址。":
    "Le fournisseur a refusé cette clé API (401). Vérifiez que la clé correspond au point de terminaison saisi.",
  "服务商没有这个模型名，请对照服务商的模型列表核对。":
    "Le fournisseur ne reconnaît pas ce nom de modèle. Consultez la liste des modèles du fournisseur.",
  "连不上接口地址，请检查网络和服务地址是否正确。":
    "Impossible de se connecter au point de terminaison. Vérifiez le réseau et l’adresse du service.",
  未提供接口地址: "Aucun point de terminaison fourni",
  "请填写生成模型名。": "Saisissez le nom du modèle de génération.",
  "请填写完整的 http:// 或 https:// 接口地址。":
    "Saisissez une URL de point de terminaison complète en http:// ou https://.",
  "请填写向量模型名。": "Saisissez le nom du modèle d’embeddings.",
  "请填写完整的向量模型接口地址。":
    "Saisissez l’URL complète du point de terminaison du modèle d’embeddings.",
  "这份配置在测试期间被改过，刚才测的是改动后的版本。配置已保存但未启用，请重新测试并启用。":
    "Cette configuration a été modifiée pendant le test ; c’est la version la plus récente qui a été testée. Elle est enregistrée mais pas activée. Testez-la et activez-la à nouveau.",
  "测试结果来自这份配置更早的版本。配置已保存但未启用，请重新测试并启用。":
    "Le résultat du test concerne une version antérieure de cette configuration. Elle est enregistrée mais pas activée. Testez-la et activez-la à nouveau.",
  "配置已保存，连接测试未通过。请核对配置后重试，历史对话尚未发送。":
    "Configuration enregistrée, mais le test de connexion a échoué. Vérifiez-la et réessayez. L’historique des conversations n’a pas été envoyé.",
  "配置已保存，尚未确认启用成功。请核对配置后重试。":
    "Configuration enregistrée, mais l’activation n’est pas confirmée. Vérifiez-la et réessayez.",
  "配置尚未完成测试和启用，请核对后重试。":
    "Le test et l’activation ne sont pas terminés. Vérifiez la configuration et réessayez.",
  "配置尚未保存成功，已填写内容保留，可核对后重试。":
    "Impossible de confirmer l’enregistrement de la configuration. Vos saisies sont conservées ; vérifiez et réessayez.",
  "只需配置生成模型。向量嵌入已内置在本机，不为 System 1、System 2 分开选模型。":
    "Seul le modèle de génération doit être configuré. Les embeddings vectoriels sont intégrés localement ; System 1 et System 2 n’ont pas besoin de modèles distincts.",
  "无法读取测试恢复记录，暂不发送新的测试。":
    "Impossible de lire l’enregistrement de reprise du test ; aucun nouveau test ne sera envoyé.",
  "上次模型测试结果待核对，暂不修改或启用配置。":
    "Le test de modèle précédent doit être vérifié ; la configuration ne sera ni modifiée ni activée pour l’instant.",
  核对测试结果: "Vérifier le résultat du test",
  "上次模型创建结果待核对，暂不开始新的操作。":
    "La création de modèle précédente doit être vérifiée ; aucune nouvelle opération ne démarrera pour l’instant.",
  核对创建结果: "Vérifier le résultat de la création",
  "配置已保存，正在测试连接…":
    "Configuration enregistrée ; test de la connexion…",
  "连接通过，正在启用模型…": "Connexion réussie ; activation du modèle…",
  刷新配置列表: "Actualiser les configurations",
  自定义一个模型: "Configurer un modèle personnalisé",
  已保存的模型配置: "Configuration de modèle enregistrée",
  "已保存，已启用": "Enregistrée et activée",
  "已保存，连接通过": "Enregistrée ; connexion réussie",
  "已保存，测试未通过": "Enregistrée ; échec du test",
  "已保存，待测试": "Enregistrée ; test en attente",
  版本: "Version",
  "需要允许 MemHub 访问钥匙串。系统询问时请选择“始终允许”；已保存的配置不会丢失。":
    "Autorisez MemHub à accéder au trousseau. Choisissez « Toujours autoriser » lorsque le système vous le demande ; les configurations enregistrées ne seront pas perdues.",
  "测试只向此接口发送测试内容，不发送历史对话。":
    "Les tests n’envoient que du contenu de test à ce point de terminaison, jamais l’historique des conversations.",
  重试测试并启用: "Retester et activer",
  测试并启用: "Tester et activer",
  编辑配置: "Modifier la configuration",
  新增配置: "Ajouter une configuration",
  编辑: "Modifier",
  "。修改后需重新测试并设为当前模型，内容授权不会自动更新。":
    ". Après modification, testez à nouveau et définissez-la comme modèle actuel. Les autorisations de contenu ne seront pas mises à jour automatiquement.",
  "配置已在其他位置变化，草稿已保留。请取消编辑后重新打开核对。":
    "La configuration a été modifiée ailleurs ; votre brouillon est conservé. Annulez la modification et rouvrez-la pour vérifier.",
  服务商预设: "Préréglage du fournisseur",
  自定义兼容服务: "Service compatible personnalisé",
  "只是帮你填好接口地址和一个模型名，不代表这个模型当前可用。 请对照服务商的模型列表核对后再保存。":
    "Cela remplit un point de terminaison et un nom de modèle, sans garantir leur disponibilité. Consultez la liste des modèles du fournisseur avant d’enregistrer.",
  协议: "Protocole",
  "与模型服务提供的接口协议保持一致。":
    "Utilisez le protocole fourni par le service de modèles.",
  配置名称: "Nom de la configuration",
  "用于区分配置，不影响模型请求。":
    "Identifie la configuration ; n’affecte pas les requêtes au modèle.",
  生成模型名: "Nom du modèle de génération",
  "例如：gpt-4o-mini…": "Par exemple : gpt-4o-mini…",
  "填写服务方提供的完整模型标识。":
    "Saisissez l’identifiant complet du modèle fourni par le service.",
  "高级：自行配置向量模型":
    "Avancé : configurer votre propre modèle d’embeddings",
  向量配置模式: "Mode de configuration des embeddings",
  本地内置: "Modèle local intégré",
  自定义向量模型: "Modèle d’embeddings personnalisé",
  向量模型名: "Nom du modèle d’embeddings",
  "例如：text-embedding-3-small…": "Par exemple : text-embedding-3-small…",
  向量模型来源: "Fournisseur du modèle d’embeddings",
  "本机 Ollama": "Ollama local",
  "同源 OpenAI-compatible（共用 API Key）":
    "OpenAI-compatible de même origine (clé API partagée)",
  "向量模型 Base URL": "Base URL du modèle d’embeddings",
  "为避免凭据外泄，向量地址必须与生成模型同源。":
    "Pour protéger les identifiants, le point de terminaison des embeddings doit avoir la même origine que le modèle de génération.",
  "使用内置本地向量模型；不会发送内容给远程向量服务。":
    "Utilise le modèle d’embeddings local intégré ; aucun contenu n’est envoyé à un service d’embeddings distant.",
  自定义向量: "Embeddings personnalisés",
  尚未填写模型: "Aucun modèle saisi",
  尚未填写地址: "Aucune adresse saisie",
  "填写服务接口地址，不是聊天网页地址。":
    "Saisissez le point de terminaison de l’API, pas l’URL du site de chat.",
  "（可选）": " (facultatif)",
  "保存后不会再次显示…": "Ne sera plus affichée après l’enregistrement…",
  "留空保留已保存密钥；填写新密钥后需重新测试和启用。":
    "Laissez vide pour conserver la clé enregistrée ; après la saisie d’une nouvelle clé, testez et activez à nouveau.",
  "换了服务商后需要填写新的 API Key，已保存的密钥不会带到新地址。":
    "Saisissez une nouvelle clé API après avoir changé de fournisseur ; la clé enregistrée n’est pas envoyée au nouveau point de terminaison.",
  "保存在系统安全存储中，不随安装包分发。":
    "Stockée dans le stockage sécurisé du système ; non distribuée avec l’application.",
  "关闭思考链（向支持的厂商下发 thinking-off 参数）":
    "Désactiver la chaîne de pensée (envoie thinking-off aux fournisseurs compatibles)",
  保存修改: "Enregistrer",
  保存配置: "Enregistrer la configuration",
  "正在启用配置…": "Activation de la configuration…",
  测试并完成配置: "Tester et terminer la configuration",
  保存并测试启用: "Enregistrer, tester et activer",
  取消编辑: "Annuler la modification",
  "密钥保存在系统安全存储；测试仅发送测试内容。":
    "La clé est stockée de façon sécurisée ; les tests n’envoient que du contenu de test.",
  当前使用: "Utilisé",
  未启用: "Non activé",
  测试目标: "Cible du test",
  "。仅发送测试内容，不发送历史对话。":
    ". Seul du contenu de test est envoyé, jamais l’historique des conversations.",
  测试连接: "Tester la connexion",
  设为当前模型: "Définir comme modèle actuel",
  删除: "Supprimer",
  "粘贴 API Key": "Coller la clé API",
  "密钥尚未保存成功，已填写内容保留，可重试。":
    "Impossible d’enregistrer la clé. Votre saisie est conservée ; vous pouvez réessayer.",
  生成接收方: "Destinataire de la génération",
  向量接收方: "Destinataire des embeddings",
  本机: "Sur cet appareil",
  "。启用此配置后按上方选择处理历史及未来允许来源。":
    ". Une fois cette configuration activée, les choix ci-dessus déterminent quelles sources autorisées, passées et futures, sont traitées.",
  "保存 key": "Enregistrer la clé",
  "还没有 key？去申请": "Pas encore de clé ? Obtenez-en une",
  删除这张卡: "Retirer cette carte",
  "密钥已配置，当前读取状态待确认。":
    "Clé configurée ; l’état de l’accès doit être confirmé.",
  "等待 key：其余都已经填好了，贴上 key 就能用。":
    "En attente d’une clé. Les autres réglages sont prêts ; collez votre clé pour continuer.",
  "key 已保存，还没测过这个模型能不能用。":
    "Clé enregistrée ; ce modèle n’a pas encore été testé.",
  "Ollama（本机）": "Ollama (local)",
  "内置 bge-small-zh-v1.5": "bge-small-zh-v1.5 intégré",
  通过: "Réussi",
  "HY 托管模型尚不可用": "Modèle hébergé HY indisponible",
  "缺少 API Key": "Clé API manquante",
  服务不可达: "Service injoignable",
  "认证失败；更新 key 后再检测，会自动接着处理":
    "Échec de l’authentification ; mettez à jour la clé et testez à nouveau pour reprendre le traitement.",
  请求频率受限: "Limite de requêtes atteinte",
  "模型账户余额不足；充值或换 key 后再检测，会自动接着处理":
    "Solde du compte du modèle insuffisant ; rechargez ou changez de clé, puis testez à nouveau pour reprendre.",
  没有找到模型: "Modèle introuvable",
  "该网关不提供模型列表，已改由生成调用验证":
    "La passerelle ne fournit pas de liste de modèles ; vérification effectuée via une requête de génération.",
  服务返回不符合协议: "La réponse du service ne respecte pas le protocole",
  结构化输出未通过: "Échec du test de sortie structurée",
  "需要填写 Embedding 模型": "Saisissez un modèle d’embeddings",
  "Embedding 模型不可用": "Modèle d’embeddings indisponible",
  "请先核对上次模型测试结果，暂不修改或启用配置。":
    "Vérifiez d’abord le test de modèle précédent ; la configuration ne sera ni modifiée ni activée pour l’instant.",
  "本地恢复记录已变化，仍保持锁定。请重新打开后核对。":
    "L’enregistrement de reprise local a changé. La configuration reste verrouillée ; rouvrez-la pour la vérifier.",
  测试结果: "Résultats du test",
  "暂不支持一键插入：它没有 MCP 通道，无法发放可撤销的记忆凭证。":
    "Connexion en un clic indisponible : cette application n’a pas de canal MCP pour un identifiant de mémoire révocable.",
  应用控制通道暂不可用:
    "Le canal de contrôle de l’application est indisponible",
  appFailureReason: " Raison : {reason}. ",
  appFailureReread:
    "L’état de la connexion n’a pas non plus pu être rechargé ; l’état affiché peut être obsolète.",
  模型: "Modèle",
  连接其他应用: "Connecter d’autres applications",
  帮助与诊断: "Aide et diagnostics",
  设置分组: "Sections des paramètres",
  记忆模型: "Modèle de mémoire",
  当前模型: "Modèle actuel",
  "分别管理记忆整理与搜索所使用的模型。":
    "Gérez séparément les modèles utilisés pour organiser et rechercher les mémoires.",
  记忆整理: "Organisation des mémoires",
  "把会话理解并整理成可长期使用的记忆。":
    "Transforme les conversations en mémoires utiles à long terme.",
  记忆搜索: "Recherche de mémoires",
  "在需要时找到与你的问题最相关的记忆。":
    "Trouve, au besoin, les mémoires les plus pertinentes pour votre question.",
  "本地内置（推荐）": "Intégré en local (recommandé)",
  "Embedding · 配置记忆整理后可自定义":
    "Embedding · Personnalisable après la configuration de l’organisation des mémoires",
  "Embedding · 本机处理，无需额外配置":
    "Embedding · Traitement local, sans configuration supplémentaire",
  "正在读取模型…": "Chargement du modèle…",
  模型状态暂不可读: "L’état du modèle est indisponible",
  尚未配置模型: "Aucun modèle configuré",
  修改模型: "Modifier le modèle",
  配置模型: "Configurer le modèle",
  刷新模型状态: "Actualiser l’état du modèle",
  "本机 / 受管端点": "Point de terminaison local / géré",
  接口地址待核对: "Point de terminaison à vérifier",
  "模型状态未能读取，请重试。":
    "Impossible de lire l’état du modèle. Veuillez réessayer.",
  "模型状态刷新未成功，仍显示上次确认的模型状态；请重试。":
    "Impossible d’actualiser l’état du modèle. Affichage du dernier état confirmé ; veuillez réessayer.",
  "上次模型测试或创建还待核对，配置暂时不能改动。请先在下方模型配置区核对结果； 现有配置和原文不会被改动。":
    "Un test ou une création de modèle précédent doit être vérifié. Consultez le résultat ci-dessous avant de modifier. Votre configuration et le texte original ne sont pas modifiés.",
  用模型沉淀记忆: "Créer des mémoires avec un modèle",
  "开启后，导入和同步到本机的会话会交给当前模型整理成记忆，会产生模型调用。 停止后不再调用模型、不再整理记忆，但会话仍会照常导入和保存；已排队的内容会留着，重新开启后继续。 模型配置始终保留，重新开启不需要重新填写。":
    "Lorsque cette option est activée, les conversations importées et synchronisées sont transformées en mémoires par le modèle actuel, ce qui génère des appels au modèle. Lorsqu’elle est désactivée, les conversations sont toujours importées et enregistrées ; le travail en file reprend lorsque vous la réactivez. Votre configuration de modèle est conservée.",
  "启用未能确认，仍显示上次确认的状态；请重试。":
    "Impossible de confirmer l’activation. Affichage du dernier état confirmé ; veuillez réessayer.",
  "停止未能确认，模型可能仍在整理记忆，请重试。":
    "Impossible de confirmer l’arrêt. Le modèle traite peut-être encore des mémoires ; veuillez réessayer.",
  "当前安装还不能读取这个开关，请确认后台服务已启动后重试。":
    "Cette installation ne peut pas lire ce réglage. Vérifiez que le service en arrière-plan est lancé et réessayez.",
  关于: "À propos",
  "此应用还没有读取授权，请先授权它的对话目录。":
    "Cette application n’a pas encore d’autorisation de lecture. Autorisez d’abord son dossier de conversations.",
  "此应用的读取授权已撤销，请重新授权。":
    "L’autorisation de lecture de cette application a été révoquée. Autorisez-la à nouveau.",
  "此应用的对话目录已被重建，请重新授权。":
    "Le dossier de conversations de cette application a été recréé. Autorisez-le à nouveau.",
  "此应用的对话目录现在读不到，请确认应用已安装并产生过对话。":
    "Le dossier de conversations de cette application est illisible. Vérifiez que l’application est installée et contient des conversations.",
  "此应用的对话目录已变更，请按最新目录重新授权。":
    "Le dossier de conversations de cette application a changé. Autorisez le nouveau dossier.",
  "此应用还没有读取授权，请先授权它的对话目录。完成后再开启持续同步。":
    "Cette application n’a pas encore d’accès en lecture. Autorisez son dossier de conversations, puis activez la synchronisation continue.",
  "此应用的读取授权已撤销，请重新授权。完成后再开启持续同步。":
    "L’accès en lecture de cette application a été révoqué. Autorisez-la à nouveau, puis activez la synchronisation continue.",
  "此应用的对话目录已被重建，请重新授权。完成后再开启持续同步。":
    "Le dossier de conversations de cette application a été recréé. Autorisez-le à nouveau, puis activez la synchronisation continue.",
  "此应用的对话目录现在读不到，请确认应用已安装并产生过对话。完成后再开启持续同步。":
    "Le dossier de conversations de cette application est illisible. Vérifiez que l’application est installée et contient des conversations, puis activez la synchronisation continue.",
  "此应用的对话目录已变更，请按最新目录重新授权。完成后再开启持续同步。":
    "Le dossier de conversations de cette application a changé. Autorisez le nouveau dossier, puis activez la synchronisation continue.",
  "此应用的读取授权已失效，请重新授权后再开启持续同步。":
    "L’accès en lecture de cette application a expiré. Autorisez-la à nouveau avant d’activer la synchronisation continue.",
  "此应用的授权或目录已变化，已按最新状态显示，请确认后再试。":
    "Les autorisations ou le dossier de cette application ont changé. Vérifiez l’état mis à jour et réessayez.",
  模型配置表单: "Formulaire de configuration du modèle",
  "部分运行状态暂时无法读取，当前显示最后读到的状态，请重试。":
    "Certaines informations d’état sont indisponibles. Affichage du dernier état connu ; veuillez réessayer.",
  "当前安装还不能设置登录后后台启动。":
    "Cette installation ne permet pas de configurer le lancement à l’ouverture de session.",
  "登录后后台启动设置未能确认，请重试。":
    "Impossible de confirmer le réglage de lancement à l’ouverture de session. Veuillez réessayer.",
  "当前安装还不能退出界面，请从菜单栏退出。":
    "Cette installation ne peut pas fermer la fenêtre. Utilisez plutôt la barre des menus.",
  "退出界面未能确认，请重试。":
    "Impossible de confirmer la fermeture. Veuillez réessayer.",
  登录后启动后台服务:
    "Lancer le service en arrière-plan à l’ouverture de session",
  "无需打开界面，持续同步、整理和提供记忆。":
    "Continuez à synchroniser, traiter et fournir les mémoires sans ouvrir l’application.",
  导出日志: "Exporter les journaux",
  "把本机运行日志打包成一个文件，发给开发排查。":
    "Regroupe tous les journaux locaux. Ils peuvent contenir des conversations ou des chemins locaux ; partagez-les uniquement avec des développeurs de confiance.",
  "确定退出 One Memory 界面吗？": "Quitter la fenêtre MemHub ?",
  "后台服务会继续同步、整理和提供记忆。":
    "Le service en arrière-plan continuera de synchroniser, traiter et fournir les mémoires.",
  取消: "Annuler",
  退出界面: "Quitter la fenêtre",
  应用详情: "Détails de l’application",
  返回应用: "Retour aux applications",
  同步开关: "Interrupteur de synchronisation",
  "开启后处理历史待办，并持续接收此应用的新会话。":
    "Lorsque cette option est activée, l’historique en attente est traité et les nouvelles conversations de cette application sont reçues en continu.",
  "正在读取已有对话，首次开启可能需要一两分钟，请不要重复点击。":
    "Lecture des conversations existantes. La première activation peut prendre une ou deux minutes ; ne cliquez pas à nouveau.",
  "将补充冻结期间的新对话；原有 pending Job 继续处理。":
    "Les conversations reçues pendant le gel seront rattrapées ; les tâches en attente existantes continuent d’être traitées.",
  一键插入应用: "Connexion de l’application en un clic",
  "安装记忆连接，并默认允许此应用使用已授权记忆。":
    "Installe la connexion de mémoire et autorise par défaut cette application à utiliser les mémoires autorisées.",
  允许使用记忆: "Autoriser l’accès aux mémoires",
  连接状态: "État de la connexion",
  已接通: "Connecté",
  "已安装，但取不到记忆": "Installé, mais impossible de récupérer les mémoires",
  未接通: "Non connecté",
  状态待核对: "État à vérifier",
  检测中: "Vérification",
  未开启关联: "Connexion désactivée",
  "正在开启处理…": "Démarrage du traitement…",
  "正在冻结处理…": "Gel du traitement…",
  处理中: "En cours de traitement",
  已冻结: "Gelé",
  "此应用的处理开关结果未能确认，其他应用不受影响。":
    "Impossible de confirmer l’interrupteur de traitement de cette application. Les autres applications ne sont pas affectées.",
  "暂时无法刷新来源状态，请重试；未更改同步或读取权限。":
    "Impossible d’actualiser l’état de la source. Réessayez ; les autorisations de synchronisation et de lecture n’ont pas été modifiées.",
  "暂时无法读取连接状态，当前状态待核对；请重试。":
    "Impossible de lire l’état de la connexion. L’état actuel doit être vérifié ; veuillez réessayer.",
  "已接通。已经打开的对话不会中途出现这个工具，请新开一轮。":
    "Connecté. L’outil n’apparaîtra pas dans une conversation déjà ouverte ; démarrez-en une nouvelle.",
  "本次连接探测失败，当前状态待核对；未把上次读取当作本次成功。":
    "Cette vérification de connexion a échoué. L’état actuel doit être vérifié ; le résultat précédent n’a pas été considéré comme une nouvelle réussite.",
  上次读取: "Dernière lecture",
  appSettingsTitle: "Paramètres de l’application {name}",
  appMemoryScope:
    "Autoriser {name} à rechercher des mémoires dans sa portée autorisée.",
  appRestart:
    "L’outil de mémoire n’est pas présent dans la conversation actuelle de {name}. Quittez complètement {name} et rouvrez-le ; il apparaîtra dans une nouvelle conversation.",
  appConnectFailed:
    "La connexion en un clic de cette application n’a pas abouti. Les autres applications ne sont pas affectées. {reason}",
  appDisconnectFailed:
    "Impossible de supprimer la connexion et de révoquer l’accès aux mémoires. {name} peut toujours utiliser les mémoires ; veuillez réessayer. {reason}",
  appPermissionFailed:
    "Impossible de confirmer l’accès aux mémoires pour cette application. Veuillez réessayer. {reason}",
  appRevokedModified:
    "L’accès aux mémoires a été révoqué : {name} ne peut plus rechercher de mémoires. Le fichier de configuration ayant été modifié, MemHub ne l’a pas supprimé automatiquement ; retirez manuellement la connexion MemHub.",
  appRevokedRetained:
    "L’accès aux mémoires a été révoqué : {name} ne peut plus rechercher de mémoires. Le fichier de connexion n’a pas été supprimé ; vérifiez-le à nouveau.",
  appRevokedReview:
    "L’accès aux mémoires a été révoqué : {name} ne peut plus rechercher de mémoires. La suppression complète n’a pas pu être confirmée ; vérifiez manuellement ces chemins : {paths}.",
  appRemovalReview:
    "La suppression complète de la connexion n’a pas pu être confirmée. Vérifiez manuellement ces chemins : {paths}.",
  appProfilesLabel: "Profils de {name}",
  appProfileOn: "{profile} : connecté",
  appProfileOff: "{profile} : non connecté",
  appProfilesHint:
    "L’interrupteur installe la connexion aux mémoires et accorde l’accès à chaque profil détecté, puis les révoque tous lorsqu’il est désactivé. Tant qu’il est activé, les nouveaux profils sont connectés automatiquement.",
  appProfilesNone:
    "Aucun profil de {name} n’a été détecté. Créez un profil, puis revenez sur cette page.",
  appProfilesConnectFailed:
    "Ces profils n’ont pas pu être connectés : {profiles}. Les autres profils sont conservés. {reason}",
  appProfilesDisconnectFailed:
    "Ces profils n’ont pas pu être déconnectés et peuvent encore utiliser les mémoires : {profiles}. Réessayez. {reason}",
  appProfilesRevokedRetained:
    "L’accès aux mémoires de ces profils a été révoqué, mais tous leurs fichiers de connexion n’ont pas été supprimés ; vérifiez-les : {profiles}.",
  appProfileNameUnsupported:
    "les noms de profil ne peuvent contenir que des minuscules, des chiffres et des tirets, 32 caractères au maximum",
  "apps.autoHostProfilesFailed":
    "Ces profils de {name} n’ont pas pu être connectés automatiquement ({profiles}){reason}.",
  "Cursor 的连接凭证已更新。请完全退出并重新打开 Cursor，再开始新对话。":
    "L’identifiant de connexion de Cursor a changé. Quittez complètement Cursor et rouvrez-le avant de démarrer une nouvelle conversation.",
  连接其他工具: "Connecter d’autres outils",
  "本机连接材料仅在桌面 App 中可用。":
    "Les informations de connexion locales ne sont disponibles que dans l’application de bureau.",
  "仅本机 MCP / 命令工具。纯网页聊天不支持直接连接本机。":
    "Réservé aux outils MCP ou en ligne de commande locaux. Les chats dans un navigateur ne peuvent pas se connecter directement à cet appareil.",
  "列表内工具请用上方一键安装，此处仅供未列出的工具。":
    "Pour les outils de la liste, utilisez l’installation en un clic ci-dessus. Cette section concerne les outils non listés.",
  "正在读取本机连接配置…": "Chargement des informations de connexion locales…",
  "未能读取连接配置。": "Impossible de charger les informations de connexion.",
  重试: "Réessayer",
  连接材料格式: "Format des informations de connexion",
  连接提示词: "Prompt de connexion",
  "MCP 配置": "Configuration MCP",
  命令: "Commande",
  本机连接材料: "Informations de connexion locales",
  "包含本机路径，不含记忆和密钥。片段会运行本机 MemHub 连接程序；粘贴前请确认，MemHub 需保持运行。":
    "Contient des chemins locaux, mais aucune mémoire ni clé. L’extrait exécute le connecteur MemHub local ; vérifiez-le avant de le coller et laissez MemHub ouvert.",
  "已复制，尚未建立连接。": "Copié ; aucune connexion n’a encore été établie.",
  "复制失败，可在上方选中文本复制。":
    "Échec de la copie. Sélectionnez et copiez plutôt le texte ci-dessus.",
  复制提示词: "Copier le prompt",
  复制配置: "Copier la configuration",
  复制命令: "Copier la commande",
  记忆库分页: "Pages de la bibliothèque de mémoires",
  上一页: "Page précédente",
  下一页: "Page suivante",
  "把熟悉的背景，带到这里": "Apportez votre contexte ici",
  "你的记忆，从这里接上": "Rassemblez vos mémoires",
  "这张卡还没测过。点一次「设为当前模型」会先自动测，如果还是不行，说明端点、key 或模型名有一处不对。":
    "Cette configuration n’a pas été testée. La définir comme modèle actuel lance d’abord un test ; en cas d’échec, vérifiez le point de terminaison, la clé et le nom du modèle.",
  "这类模型接口暂时用不了，换一张卡。":
    "Ce type de point de terminaison de modèle n’est pas encore pris en charge. Utilisez une autre configuration.",
  "这张卡缺了向量化模型，补上才能启用。":
    "Cette configuration nécessite un modèle d’embeddings avant de pouvoir être activée.",
  "这张卡已经不在了，可能刚被删掉。刷新一下。":
    "Cette configuration n’est plus disponible. Elle a peut-être été supprimée ; actualisez la liste.",
  "这张卡刚被改过。刷新一下再试，免得覆盖掉别处的改动。":
    "Cette configuration vient d’être modifiée. Actualisez avant de réessayer pour ne pas écraser une autre modification.",
  onboardingModelOperationFailed: "Échec de l’opération : {code}",
  onboardingModelSkipDestination: "Passer et aller aux applications",
  首次设置进度: "Progression de la configuration initiale",
  导入历史: "Importer l’historique",
  连接应用: "Connecter des applications",
  沉淀记忆: "Créer des mémoires",
  本地存储: "Stockage local",
  "对话与记忆保存在本机；使用云模型或已授权的助手时，相关内容会发送给对应服务。":
    "Les conversations et les mémoires sont stockées sur cet appareil. Le contenu pertinent est envoyé aux modèles cloud ou aux assistants autorisés lorsque vous les utilisez.",
  "来源位置、身份或状态已变化，请重新选择并核对。尚未提交本次授权。":
    "L’emplacement, l’identité ou l’état de la source a changé. Sélectionnez-la et vérifiez-la à nouveau. Cette autorisation n’a pas été envoyée.",
  "来源位置、身份或状态已变化，正在等待本次提交回执。不会自动扩大已提交范围。":
    "L’emplacement, l’identité ou l’état de la source a changé. En attente de la confirmation de cet envoi ; la portée envoyée ne sera pas étendue automatiquement.",
  测试实例不接入应用:
    "Les instances de test ne se connectent pas aux applications",
  "请先把 MemHub 拖到“应用程序”并从那里重新打开，才能接入应用。":
    "Déplacez MemHub dans Applications et rouvrez-le depuis cet emplacement avant de connecter des applications.",
  "部分应用未能完成接入，已成功的设置会保留，可进入应用后单独处理。":
    "Certaines applications n’ont pas pu être connectées. Les réglages réussis sont conservés ; traitez les autres individuellement dans Applications.",
  onboardingConnectionPartial:
    "Applications connectées : {success} ; incomplètes : {failed} ({names}). Vous pouvez les traiter individuellement dans Applications.",
  "首次设置的完成状态未能保存。已确认的导入回执不受影响；下次启动可能再次出现此页。":
    "Impossible d’enregistrer la fin de la configuration initiale. Les accusés d’importation confirmés ne sont pas affectés ; cette page pourra réapparaître au prochain lancement.",
  "首次设置的完成状态尚未确认。已确认的导入回执不受影响；下次启动可能再次出现此页。":
    "La fin de la configuration initiale n’est pas confirmée. Les accusés d’importation confirmés ne sont pas affectés ; cette page pourra réapparaître au prochain lancement.",
  "位置检测未完成，尚未读取历史。可以重新检测，或先进入工作台。":
    "La détection des emplacements est incomplète et l’historique n’a pas été lu. Relancez la détection ou continuez vers l’espace de travail.",
  "暂时无法核对来源，尚未提交授权。选择已保留，可以重试。":
    "Les sources ne peuvent pas être vérifiées pour le moment ; l’autorisation n’a donc pas été envoyée. Vos sélections sont conservées ; vous pouvez réessayer.",
  "本次没有来源获准导入。请核对下方原因后重试，或到应用页查看状态。":
    "Aucune source n’a été autorisée pour cette importation. Consultez les raisons ci-dessous et réessayez, ou vérifiez leur état dans Applications.",
  "开始导入的结果尚未确认，选择已保留。可以重试，或进入应用页核对。":
    "Le lancement de l’importation n’est pas confirmé. Vos sélections sont conservées ; réessayez ou vérifiez dans Applications.",
  未完成: "Incomplet",
  核对中: "Vérification",
  仍进入应用页: "Continuer vers Applications",
  仍进入工作台: "Continuer vers l’espace de travail",
  仍进入设置: "Continuer vers les paramètres",
  重试保存并进入: "Réessayer l’enregistrement et continuer",
  "稍后添加，先进入": "Ajouter plus tard et continuer",
  "正在开始导入…": "Lancement de l’importation…",
  开始导入: "Lancer l’importation",
  "不用从头介绍自己。从常用的 App 开始，把已有背景带过来。":
    "Inutile de tout recommencer. Apportez le contexte de vos applications habituelles.",
  "确认前只检查应用信息，不读取对话，也不安装连接。":
    "Avant confirmation, seules les informations des applications sont vérifiées. Les conversations ne sont pas lues et aucune connexion n’est installée.",
  "本机 AI 应用": "Applications d’IA sur cet appareil",
  重新检测位置: "Détecter à nouveau les emplacements",
  "检测中…": "Vérification…",
  重新检测: "Vérifier à nouveau",
  候选应用: "Applications disponibles",
  "正在检测其他应用…": "Recherche d’autres applications…",
  检测未完成: "Vérification incomplète",
  已全选: "Tout est sélectionné",
  全选: "Tout sélectionner",
  onboardingImportHistory: "Importer l’historique de {name}",
  正在核对本机保存位置: "Vérification de l’emplacement de stockage local",
  已发现: "Trouvé",
  位置待核对: "Emplacement à vérifier",
  "当前没有候选来源。可以稍后在应用页添加历史包。":
    "Aucune source trouvée. Vous pourrez ajouter une archive d’historique plus tard depuis Applications.",
  "候选来源尚未加载。可以检测位置，或先进入工作台。":
    "Les sources n’ont pas été chargées. Détectez les emplacements ou continuez vers l’espace de travail.",
  本次导入: "Cette importation",
  "个 App 的历史对话": "historiques d’applications",
  "把零散的会话，": "Rassemblez vos conversations",
  汇成完整的记忆: "en mémoires",
  "少一些重复交代，多一些理解。":
    "Moins de répétitions. Plus de compréhension.",
  onboardingMoreApps:
    "Faites défiler pour voir plus · {count} applications restantes",
  "onboardingMoreApps.one":
    "Faites défiler pour voir plus · {count} application restante",
  onboardingMoreImportApps:
    "Faites défiler horizontalement · encore {count} applications",
  onboardingImportChecking:
    "Vérification du résultat de l’importation ; votre sélection est conservée",
  onboardingAllApps: "Les {count} applications sont affichées",
  默认位置: "Emplacement par défaut",
  自定义位置: "Emplacement personnalisé",
  尚未检测: "Non vérifié",
  未发现: "Introuvable",
  无法访问: "Accès impossible",
  位置无效: "Emplacement non valide",
  "需要更新 Core": "Mise à jour du Core requise",
  "候选位置不存在，可能尚无历史或路径不同。":
    "Cet emplacement n’existe pas. Il n’y a peut-être pas encore d’historique, ou le chemin est différent.",
  "无法检查此位置，请核对目录访问权限后重试。":
    "Impossible de vérifier cet emplacement. Vérifiez l’accès au dossier et réessayez.",
  "此位置未通过安全检查，不能读取。":
    "Cet emplacement n’a pas passé le contrôle de sécurité et ne peut pas être lu.",
  "当前 Core 未提供可确认的位置身份，需更新后再选择。":
    "Le Core actuel ne fournit pas d’identité d’emplacement vérifiable. Mettez-le à jour avant de sélectionner.",
  暂不支持历史导入:
    "L’importation de l’historique n’est pas encore prise en charge",
  "当前来源不可读取，请重新检测。":
    "La source actuelle ne peut pas être lue. Vérifiez à nouveau.",
  "来源目录暂时不可读取，请核对位置和权限。":
    "Le dossier source est temporairement illisible. Vérifiez son emplacement et ses autorisations.",
  "来源目录身份已变化，请返回重新检测。":
    "L’identité du dossier source a changé. Revenez en arrière et vérifiez à nouveau.",
  "来源权限已变化，请返回重新确认。":
    "Les autorisations de la source ont changé. Revenez en arrière et confirmez à nouveau.",
  "此来源尚未获得读取授权。": "Cette source n’a pas encore d’accès en lecture.",
  "自定义来源位置需要重新确认。":
    "L’emplacement de la source personnalisée doit être confirmé à nouveau.",
  "当前 Core 未启用历史导入。":
    "L’importation de l’historique n’est pas activée dans le Core actuel.",
  onboardingImportRefused:
    "Importation non autorisée ({reason}). Vérifiez la source et réessayez.",
  确认自定义位置: "Confirmer l’emplacement personnalisé",
  关闭位置确认: "Fermer la confirmation d’emplacement",
  "此位置来自当前配置，并非应用默认目录。确认位置不会开始读取历史。":
    "Cet emplacement provient de votre configuration actuelle, et non du dossier par défaut de l’application. Confirmer l’emplacement ne lance pas la lecture de l’historique.",
  onboardingConfirmHistory:
    "Confirmez qu’il s’agit de l’emplacement de l’historique de {name}",
  确认此位置: "Confirmer cet emplacement",
  "对话正在汇入，记忆从这里开始":
    "Les conversations arrivent. Les mémoires commencent ici",
  "导入进行中 · 顺手连接应用":
    "Importation en cours · Connectez vos applications",
  "对话已保存，记忆尚待生成。":
    "Conversations enregistrées ; les mémoires restent à créer.",
  "导入在后台继续，无需等待。":
    "L’importation se poursuit en arrière-plan. Inutile d’attendre.",
  "正在重新核对来源；已保存数量仍按任务回执显示。":
    "Nouvelle vérification des sources ; les nombres enregistrés reflètent toujours les accusés des tâches.",
  "本次历史导入已暂停，已保存的对话还在。":
    "Cette importation de l’historique est en pause. Les conversations enregistrées restent disponibles.",
  "导入等待处理，已保存的对话还在。":
    "L’importation attend d’être traitée. Les conversations enregistrées restent disponibles.",
  停止后续操作: "Arrêter les actions restantes",
  暂停导入: "Suspendre l’importation",
  继续导入: "Reprendre l’importation",
  "配置模型，沉淀记忆": "Configurer un modèle pour créer des mémoires",
  导入与原文: "Importation et texte original",
  "本次导入已结束。已保存的对话无需重复导入。":
    "Cette importation est terminée. Les conversations enregistrées n’ont pas besoin d’être réimportées.",
  "对话先保存在本机。你可以同时设置后续连接。":
    "Les conversations sont d’abord enregistrées localement. Vous pouvez configurer les connexions en attendant.",
  "导入不会自动开放应用读取权限。配置模型后，才会开始生成记忆。":
    "L’importation n’accorde pas automatiquement l’accès en lecture aux applications. Les mémoires ne sont créées qu’après la configuration d’un modèle.",
  onboardingHistoryWindow:
    "Un volume important de conversations a été détecté. Pour limiter l’utilisation et aligner les chronologies des applications, cette importation ne comprend que les 3 derniers jours pour toutes les applications.",
  导入说明: "Informations sur l’importation",
  正在导入你的会话: "Importation de vos conversations",
  "后台将持续为你导入，可以放心进入下一步":
    "L’importation se poursuivra en arrière-plan. Vous pouvez passer à la suite.",
  未开始的导入: "Importations n’ayant pas pu démarrer",
  "未能开始导入，其他已接受任务不受影响。":
    "L’importation n’a pas pu démarrer. Les autres tâches acceptées ne sont pas affectées.",
  查看未导入的应用: "Voir les applications non importées",
  让记忆继续生长: "Faites grandir vos mémoires",
  连接你的应用: "Connectez vos applications",
  "适用于本次选择且支持的应用。":
    "Pour les applications sélectionnées qui prennent en charge cette connexion.",
  一键接入应用: "Connecter les applications en une étape",
  "安装记忆连接，让所选应用用上 MemHub 中的相关记忆。":
    "Installez les connexions de mémoire pour que les applications sélectionnées puissent utiliser les mémoires pertinentes de MemHub.",
  "当前使用独立测试数据目录，不会修改本机应用的连接配置。":
    "Cette instance utilise un dossier de données de test isolé et ne modifiera pas les connexions des applications locales.",
  "点击底部按钮后，后台分别接入各应用；某个应用失败不影响其他应用。":
    "Après un clic sur le bouton ci-dessous, les applications sont connectées une à une en arrière-plan ; un échec n’affecte pas les autres.",
  "暂时无法连接应用，当前不会安装连接。":
    "Impossible de connecter les applications pour le moment. Aucune connexion ne sera installée.",
  onboardingBatchStopped:
    "Actions restantes arrêtées ; consultez les accusés pour les actions déjà envoyées. ",
  onboardingBatchResult: "Confirmées : {confirmed}, non confirmées : {failed}.",
  "已确认项保留，请核对未确认任务后重试。":
    " Les actions confirmées sont conservées. Vérifiez les tâches non confirmées avant de réessayer.",
  本次应用导入进度:
    "Progression de l’importation des applications sélectionnées",
  正在导入: "Importation",
  已暂停: "En pause",
  已中断: "Interrompue",
  导入遇到问题: "L’importation a rencontré un problème",
  导入完成: "Importation terminée",
  已取消: "Annulée",
  已保存: "Enregistrée",
  onboardingAppProgress: "Progression de l’importation pour {name}",
  onboardingSavedMessages: "Messages enregistrés : {count}",
  onboardingScannedOutsideRange:
    "Éléments analysés : {count} ; aucune conversation ne correspond à la période",
  onboardingScannedNothingToSave:
    "Éléments analysés : {count} ; aucune conversation à enregistrer",
  onboardingScannedFilteringRecent:
    "Éléments analysés : {count} ; filtrage des 3 derniers jours",
  onboardingScannedFiltering: "Éléments analysés : {count} ; filtrage",
  onboardingScannedNotSaved:
    "Éléments analysés : {count} ; pas encore enregistrés",
  没有可保存的历史对话: "Aucune conversation d’historique à enregistrer",
  读取授权不可用: "Accès en lecture indisponible",
  onboardingReadErrors: " · Erreurs de lecture : {count}",
  "配置详情，按需要调整": "Détails de la configuration, si nécessaire",
  "连接未通过，配置已保留": "Échec de la connexion ; configuration conservée",
  "选好模型，让记忆开始沉淀":
    "Choisissez un modèle pour commencer à créer des mémoires",
  "连接设置已保存\n成功项保留，不重复执行。":
    "Paramètres de connexion enregistrés.\nLes étapes réussies sont conservées et ne seront pas répétées.",
  "已选择：持续同步 · 一键接入":
    "Sélection : synchronisation continue · connexion en une étape",
  "沉淀记忆 · 使用你选择的模型":
    "Créer des mémoires · Avec le modèle de votre choix",
  "当前无法启用模型，已填写内容保留。":
    "Impossible d’activer le modèle pour le moment. Vos saisies sont conservées.",
  "模型连接测试通过，可以启用并开始沉淀记忆。":
    "Connexion au modèle réussie. Vous pouvez l’activer pour créer des mémoires.",
  "先测试模型连通性；测试通过后再启用。":
    "Testez d’abord la connexion au modèle ; activez-le une fois le test réussi.",
  "跳过，先进入应用": "Passer et aller aux applications",
  "正在测试模型…": "Test du modèle…",
  测试模型连通性: "Tester la connexion au modèle",
  "正在启用…": "Activation…",
  确认启用: "Confirmer et activer",
  核对提交状态: "Vérifier l’état de l’envoi",
  模型配置: "Configuration du modèle",
  模型参数: "Paramètres du modèle",
  "选择服务商，填入 API Key。核对已有配置后再继续。":
    "Choisissez un fournisseur et saisissez une clé API. Vérifiez la configuration existante avant de continuer.",
  模型服务商: "Fournisseur du modèle",
  选择服务商: "Sélectionner un fournisseur",
  "已安全保存，留空保留":
    "Enregistrée de façon sécurisée ; laissez vide pour la conserver",
  "输入服务商提供的 API Key": "Saisissez la clé API de votre fournisseur",
  "连接未通过，请检查 API Key、模型与接口地址后重试。配置已保留。":
    "Échec de la connexion. Vérifiez la clé API, le modèle et le point de terminaison, puis réessayez. Votre configuration est conservée.",
  "需要允许 MemHub 访问钥匙串。系统询问时请选择“始终允许”。":
    "Autorisez MemHub à accéder au trousseau. Choisissez « Toujours autoriser » lorsque le système vous le demande.",
  "留空保留已保存密钥；填写新密钥后需重新测试。":
    "Laissez vide pour conserver la clé enregistrée ; testez à nouveau après la saisie d’une nouvelle clé.",
  "密钥保存在系统安全存储中，不写入对话或日志。":
    "La clé est conservée dans le stockage sécurisé du système, jamais dans les conversations ni les journaux.",
  配置详情与高级设置: "Détails de la configuration et paramètres avancés",
  服务商待选择: "Fournisseur non sélectionné",
  模型待填写: "Modèle non saisi",
  本地向量模型: "Modèle d’embeddings local",
  向量模型待填写: "Modèle d’embeddings non saisi",
  思考已关闭: "Réflexion désactivée",
  思考未关闭: "Réflexion non désactivée",
  "预设已填好，只有自定义服务或特殊需求才需要修改。":
    "Les préréglages sont remplis. Ne les modifiez que pour des services personnalisés ou des besoins particuliers.",
  返回服务商选择: "Retour au choix du fournisseur",
  模型名称: "Nom du modèle",
  "Anthropic（暂不支持）": "Anthropic (pas encore pris en charge)",
  "HY 托管（当前不可用）": "HY géré (actuellement indisponible)",
  向量模型: "Modèle d’embeddings",
  "本地 ONNX · 内置": "ONNX local · Intégré",
  "超时（秒）": "Délai d’expiration (secondes)",
  最大并发: "Concurrence maximale",
  上下文窗口: "Fenêtre de contexte",
  "自动 / 可选": "Automatique / facultatif",
  关闭思考模式: "Désactiver le mode réflexion",
  "自定义向量配置可展开；远程向量服务须遵守凭据边界。":
    "Vous pouvez développer les réglages d’embeddings personnalisés. Les services d’embeddings distants doivent respecter les limites relatives aux identifiants.",
  "切换服务商或模型后，需要重新测试并启用。":
    "Après un changement de fournisseur ou de modèle, testez et activez à nouveau.",
  向量模型名称: "Nom du modèle d’embeddings",
  向量协议: "Protocole des embeddings",
  "同源 OpenAI-compatible（共用 Key）":
    "OpenAI-compatible de même origine (clé partagée)",
  "向量 Base URL": "Base URL des embeddings",
  "远程向量地址须与生成服务同源；本机地址仍需校验。":
    "Les points de terminaison d’embeddings distants doivent avoir la même origine que le service de génération ; les points de terminaison locaux doivent tout de même être validés.",
  "放弃旧记录，重新配置": "Abandonner l’ancien enregistrement et reconfigurer",
  模型提交状态: "État de l’envoi du modèle",
  "正在测试连接…": "Test de la connexion…",
  "正在提交保存与启用，请等待回执…":
    "Enregistrement et activation ; en attente de confirmation…",
  "操作结果未确认，已填写内容保留，请核对后重试。":
    "Résultat non confirmé. Vos saisies sont conservées ; vérifiez avant de réessayer.",
  本次沉淀: "Cette configuration des mémoires",
  让记忆从这里开始: "Commencez à créer des mémoires ici",
  对话来源: "Sources des conversations",
  尚无已选来源: "Aucune source sélectionnée",
  onboardingSourcesSummary: "{names}, etc. ({count} applications au total)",
  onboardingImportedConversations:
    "Conversations importées : {count}. Les interrupteurs des sources déterminent toujours ce qui sera ensuite synchronisé sur cet appareil.",
  "处理服务：": "Service de traitement :",
  待选择: "Non sélectionné",
  "相关对话会发送给所选服务商。":
    "Les conversations pertinentes seront envoyées au fournisseur choisi.",
  "测试连接只发送测试内容。启用后会使用此模型整理已导入和之后同步的会话。":
    "Les tests de connexion n’envoient que du contenu de test. Une fois activé, ce modèle traite les conversations importées et celles synchronisées ensuite.",
  "当前不能在此启用模型。":
    "Impossible d’activer le modèle ici pour le moment.",
  "配置已修改，需要重新测试。": "Configuration modifiée. Testez à nouveau.",
  onboardingIntegerRange:
    "Saisissez un nombre entier compris entre {min} et {max}.",
  "请填写配置名称。": "Saisissez un nom de configuration.",
  "请填写实际可用的模型 ID。": "Saisissez un identifiant de modèle disponible.",
  "请填写有效的模型接口地址。":
    "Saisissez une URL de point de terminaison de modèle valide.",
  "请填写 API Key。": "Saisissez une clé API.",
  "请填写向量模型 ID。": "Saisissez un identifiant de modèle d’embeddings.",
  "配置结果尚未确认，请在模型设置中核对后重试。":
    "Le résultat de la configuration n’est pas confirmé. Vérifiez-le dans les paramètres du modèle avant de réessayer.",
  "模型连接测试通过，可以启用。":
    "Test de connexion au modèle réussi. Vous pouvez l’activer.",
  "连接未通过，请核对模型、密钥和接收方。":
    "Échec de la connexion. Vérifiez le modèle, la clé et le destinataire.",
  "配置或测试结果暂未确认，请核对后重试。":
    "Impossible de confirmer la configuration ou le résultat du test. Vérifiez avant de réessayer.",
  "索引更新没有完成，仍在使用原来的搜索模型。请检查连接后重试。":
    "La mise à jour de l’index ne s’est pas terminée. Le modèle de recherche précédent reste utilisé. Vérifiez la connexion et réessayez.",
  "上次模型测试结果仍待核对，请稍后重试。":
    "Le résultat du dernier test du modèle doit encore être vérifié. Réessayez plus tard.",
  "模型写入暂时关闭。请等待索引维护完成后重试。":
    "Les écritures du modèle sont temporairement suspendues. Attendez la fin de la maintenance de l’index, puis réessayez.",
  "模型未能确认启用，请在设置中核对。":
    "Impossible de confirmer que le modèle a été activé. Vérifiez dans les Paramètres.",
  "已保存的模型配置暂时无法读取，不会重复创建。请重试刷新。":
    "La configuration de modèle enregistrée est temporairement inaccessible. Aucun doublon ne sera créé. Réessayez d’actualiser.",
  所选连接已启用: "Les connexions des applications sélectionnées sont activées",
  本次未选择应用连接:
    "Aucune connexion d’application sélectionnée cette fois-ci",
  尚未填写接收方: "Aucun destinataire saisi",
  本机内置向量模型: "Modèle d’embeddings local intégré",
  "操作未确认，草稿已保留。":
    "Impossible de confirmer l’action. Votre brouillon est conservé.",
  "操作已完成，但配置列表暂未刷新。已保留操作回执，请刷新核对，不要重复保存。":
    "L’opération est terminée, mais la liste des configurations n’a pas été actualisée. L’accusé est conservé ; actualisez pour le vérifier. N’enregistrez pas à nouveau.",
  "暂时无法刷新模型配置，已确认的配置仍保留。请重试刷新。":
    "Impossible d’actualiser les configurations de modèle. Les configurations confirmées sont conservées ; réessayez d’actualiser.",
  "未找到上次创建记录。确认放弃旧记录后可重新配置；系统不会自动重试。":
    "L’enregistrement de création précédent est introuvable. Abandonnez-le pour reconfigurer ; aucune nouvelle tentative automatique n’aura lieu.",
  "创建结果仍待核对，不会重复创建。请稍后再次核对。":
    "Le résultat de la création doit encore être vérifié. Aucun doublon ne sera créé ; vérifiez à nouveau plus tard.",
  "已发现模型配置，旧创建记录仍保留。请先核对已有配置。":
    "Une configuration de modèle a été trouvée. L’ancien enregistrement de création est conservé ; vérifiez d’abord la configuration existante.",
  "旧创建记录尚未安全解除，配置仍保持锁定。请重新核对。":
    "L’ancien enregistrement de création n’a pas été supprimé de façon sûre. La configuration reste verrouillée ; vérifiez-la à nouveau.",
  "配置未保存，请检查模型地址和参数后重试；已填写内容保留。":
    "La configuration n’a pas été enregistrée. Vérifiez le point de terminaison et les paramètres du modèle avant de réessayer ; votre saisie est conservée.",
  "创建结果尚未确认，请核对已保存结果，不要重复提交。":
    "Le résultat de la création n’est pas confirmé. Vérifiez les résultats enregistrés ; ne renvoyez pas la demande.",
  "模型配置已变化，旧测试结果不会用于启用，请重新核对配置。":
    "La configuration du modèle a changé. L’ancien résultat de test ne servira pas à l’activer ; vérifiez à nouveau la configuration.",
  "暂时无法核对测试服务，本次未发送测试，请恢复连接后重试。":
    "Impossible de vérifier le service de test. Aucun test n’a été envoyé ; rétablissez la connexion et réessayez.",
  "另一个模型的测试结果尚未确认，请先在该模型上核对测试结果。":
    "Le résultat du test d’un autre modèle n’est pas confirmé. Vérifiez d’abord le résultat de ce modèle.",
  "测试结果尚未确认。再次点击测试连接将核对原操作，不会重复发送测试。":
    "Le résultat du test n’est pas confirmé. Cliquer à nouveau sur Tester la connexion vérifiera l’opération d’origine sans envoyer de nouveau test.",
  "请先点击测试连接，通过后再设为当前模型。":
    "Testez d’abord la connexion ; ne le définissez comme modèle actuel qu’une fois le test réussi.",
  "这是当前正在使用的模型，请先切换到别的模型再删除。":
    "Ce modèle est actuellement utilisé. Passez à un autre modèle avant de le supprimer.",
  "模型配置操作未成功，请核对输入后重试。":
    "L’opération de configuration du modèle a échoué. Vérifiez votre saisie et réessayez.",
  "它没有 MCP 通道，无法发放可撤销的记忆凭证":
    "Cette application n’a pas de canal MCP pour un identifiant de mémoire révocable",
  当前安装不支持一键插入:
    "La connexion en un clic n’est pas prise en charge dans cette installation",
  桌面端的连接组件不可用:
    "Le composant de connexion de l’application de bureau est indisponible",
  安装后没有拿到安装回执: "Aucun accusé d’installation n’a été renvoyé",
  记忆使用权限没有获批: "L’accès aux mémoires n’a pas été approuvé",
  配置文件被改过: "Le fichier de configuration a été modifié",
  "有无法确认的残留，需要人工核对":
    "Certains éléments résiduels doivent être vérifiés manuellement",
  权限存储暂不可用: "Le stockage des autorisations est indisponible",
  "请先把 MemHub 拖到“应用程序”文件夹，再接入应用":
    "Déplacez MemHub dans Applications avant de connecter des applications",
  本机运行时暂不可用: "L’environnement d’exécution local est indisponible",
  桌面端读取接入状态超时:
    "La lecture de l’état de connexion de l’application de bureau a expiré",
  "apps.apps": "Applications",
  "apps.allApps": "Toutes les applications",
  "apps.home": "Accueil des applications",
  "apps.back": "Retour à l’accueil",
  "apps.overview": "Aperçu des mémoires",
  "apps.memories": "Mémoires enregistrées",
  "apps.rawConversations": "Conversations d’origine",
  "apps.rawSaving": "Enregistrement",
  "apps.rawSavingCompact": "Enregistrement",
  "apps.rawSavingStatus": "État de l’enregistrement",
  "apps.rawSavingDetail":
    "Les conversations d’origine sont enregistrées sans modèle génératif",
  "apps.rawNoExcerpt": "Aucun extrait utilisateur affichable",
  "apps.rawConversation": "Conversation d’origine",
  "apps.rawSaved": "Conversation d’origine enregistrée",
  "apps.rawEmpty": "Aucune conversation d’origine enregistrée",
  "apps.rawCheckedEmpty":
    "Vérifié : aucune mise à jour de conversation à afficher",
  "apps.memoryCheckedEmpty":
    "Vérifié : aucune mise à jour de mémoire à afficher",
  "apps.recentEmptyCompact": "Aucune mise à jour",
  "apps.rawWaiting":
    "Les conversations d’origine enregistrées apparaîtront ici",
  "apps.checkedRecent": "Contenu actuel vérifié",
  "apps.currentMode": "Mode actuel",
  "apps.mode.lite": "Mémoire de base (lite)",
  "apps.mode.pro": "Mémoire intelligente (pro)",
  "apps.mode.ultra": "Mémoire approfondie (ultra)",
  "apps.readingCount": "Vérification des mémoires locales…",
  "apps.rawReadingCount": "Vérification des conversations d’origine…",
  "apps.startingRetry":
    "Le service en arrière-plan démarre ; l’affichage se mettra à jour automatiquement",
  "apps.countWillUpdate":
    "Le nombre de mémoires se mettra à jour automatiquement",
  "apps.rawCountWillUpdate":
    "Le nombre de conversations d’origine se mettra à jour automatiquement",
  "apps.showingPrevious":
    "Affichage des données précédentes ; mise à jour automatique à venir",
  "apps.unreadable": "Indisponible",
  "apps.unit": "éléments",
  "apps.scope": "Utilisées dans la limite des autorisations des applications",
  "apps.processing": "État du traitement",
  "apps.running": "Traitement en cours",
  "apps.paused": "En pause",
  "apps.stoppedNew": "Aucune nouvelle mémoire n’est créée",
  "apps.checkingProcessing": "Vérification de l’état du traitement",
  "apps.checkingStatus": "Vérification",
  "apps.unchecked": "À vérifier",
  "apps.organizingNew": "Traitement des nouvelles conversations",
  "apps.recentUpdate": "Mise à jour récente",
  "apps.checkingRecent": "Vérification",
  "apps.recentWillUpdate":
    "La dernière mise à jour s’affichera automatiquement",
  "apps.notGenerated": "Pas encore créée",
  "apps.saved": "Mémoire enregistrée",
  "apps.separate": "L’historique et les mémoires sont traités séparément",
  "apps.recentCall": "Appel récent",
  "apps.activityUnavailable": "Activité indisponible",
  "apps.firstBatch": "Premières mémoires",
  "apps.callsUnavailable": "Impossible de lire l’historique des appels",
  "apps.memoryUnavailable": "L’aperçu des mémoires n’est pas encore prêt",
  "apps.importing":
    "Importation des conversations avant la création des mémoires",
  "apps.firstRunning": "Création des premières mémoires",
  "apps.firstQueued": "Préparation des premières mémoires",
  "apps.readingActivity": "Préparation de l’aperçu des mémoires",
  "apps.emptyMemories": "Aucune mémoire créée pour l’instant",
  "apps.callReadFailure":
    "Vous pouvez toujours consulter les mémoires enregistrées. Réessayer ne lancera pas de nouvel appel.",
  "apps.memoryReadFailure":
    "L’affichage se mettra à jour au rétablissement du service. Vous pouvez aussi réessayer maintenant.",
  "apps.savingConversations":
    "Les conversations sont toujours en cours d’enregistrement en arrière-plan.",
  "apps.waitingMemories":
    "L’historique est enregistré. Les nouvelles mémoires apparaîtront ici.",
  "apps.reread": "Réessayer",
  "apps.viewCalls": "Voir l’historique",
  "apps.viewMemory": "Voir les mémoires",
  "apps.backgroundDown":
    "Le service en arrière-plan ne fonctionne pas ; les applications ne peuvent pas rechercher de mémoires pour le moment",
  "apps.restart": "Redémarrer",
  "apps.runtime": "État d’exécution",
  "apps.backgroundRunning": "Fonctionne en arrière-plan",
  "apps.connectionUnchecked": "Connexion à vérifier",
  "apps.disconnected": "Service déconnecté",
  "apps.authorizedOnly": "Traite uniquement le contenu autorisé",
  "apps.sync": "Synchronisation des conversations",
  "apps.organizing": "Traitement des mémoires",
  "apps.access": "Accès des applications",
  "apps.checkingAccess": "Vérification des connexions des applications",
  "apps.checking": "Vérification…",
  "apps.recheck": "Vérifier à nouveau",
  "apps.accessState": "État de connexion des applications",
  "apps.staleAccess":
    "Impossible de lire l’état de connexion des applications ; affichage du dernier état connu.",
  "apps.reconnect": "Reconnecter",
  "apps.myApps": "Mes applications",
  "apps.list": "Liste des applications",
  "apps.syncEnabled": "Synchronisation continue activée",
  "apps.recallAllowed": "Peut demander les mémoires autorisées",
  "apps.historyAllowed": "Accès à l’historique autorisé",
  "apps.connectionPending": "Connexion incomplète",
  "apps.details": "Voir les détails",
  "apps.listUnavailable": "Liste des applications indisponible",
  "apps.emptyApps": "Aucune application à afficher pour l’instant",
  "apps.checkingNow": "Vérification",
  "apps.checkingState": "Vérification de l’état",
  "apps.notEnabled": "Non activé",
  "apps.enableFailed": "Activation impossible",
  "apps.unknownTime": "Date inconnue",
  "apps.today": "Aujourd’hui",
  "apps.newMemory": "Nouvelle mémoire",
  "apps.updatedMemory": "Mémoire mise à jour",
  "apps.unlinked": "Pas encore connecté",
  "apps.lastRead": "Dernière lecture : ",
  "apps.recentSync": "Dernière synchronisation : {time}",
  "apps.connectedCount": "{count} applications connectées",
  "apps.connectedCount.one": "{count} application connectée",
  "apps.pendingCount": "{count} applications à vérifier",
  "apps.pendingCount.one": "{count} application à vérifier",
  "apps.mixedCount": "{count} applications connectées · À vérifier : {pending}",
  "apps.mixedCount.one":
    "{count} application connectée · À vérifier : {pending}",
  "apps.unknownAccess":
    "L’état de connexion des applications est indisponible ; le nombre d’applications connectées ne peut pas être confirmé.",
  "apps.unknownPermissions":
    "Impossible de lire les autorisations de mémoire de {names} ; l’état de connexion n’est pas confirmé.",
  "apps.enabledCount": "{count} applications activées",
  "apps.enabledCount.one": "{count} application activée",
  "apps.appCount": "{count} applications",
  "apps.appCount.one": "{count} application",
  "apps.viewAll": "Voir les {count} applications",
  "apps.viewAll.one": "Voir l’application",
  "apps.checkedAt": "Dernière vérification : {time}",
  "apps.revision": "Version {count}",
  "apps.conversationTime": "Date de la conversation",
  "apps.importTime": "Date d’importation",
  "apps.writeTime": "Date d’écriture",
  "apps.updateTime": "Date de mise à jour",
  "apps.incompleteCalls":
    " · Certains appels n’ont pas été enregistrés ; la liste peut être incomplète",
  "apps.timeoutWarning":
    "{host} n’a pas pu rechercher de mémoires récemment. Le service en arrière-plan est peut-être occupé et devrait se rétablir automatiquement.",
  "apps.autoHostFailed":
    "{name} n’a pas pu se connecter automatiquement{reason}.",
  "apps.failureReason": " : {reason}",
  "apps.autoSyncFailed":
    "La synchronisation continue n’a pas pu être activée automatiquement pour {name}. {reason}",
  "apps.noAutoRetry":
    "Aucune nouvelle tentative automatique ne sera effectuée.",
  "apps.cardUnavailable":
    "{label} est temporairement indisponible. Les autres fonctionnalités ne sont pas affectées, et cela devrait se rétablir automatiquement.",
  "apps.navMemories": "Mémoires",
  "apps.navigation": "Navigation principale",
  "apps.skip": "Aller au contenu principal",
  "apps.local": "Stockage local",
  "apps.localHint":
    "Les mémoires sont stockées sur cet appareil. Le contenu pertinent est envoyé au service lorsque vous utilisez un modèle cloud ou un assistant autorisé.",
  "memory:记忆抽取": "Extraction des mémoires",
  "memory:关系归纳": "Synthèse des relations",
  "memory:深度整理": "Traitement approfondi",
  "memory:跨域整理": "Traitement interdomaines",
  "memory:当前模型已变化": "Le modèle actuel a changé",
  "memory:本地会话内容已变化": "Le contenu des conversations locales a changé",
  "memory:这段对话含过长原文，暂未整理；原文已保留":
    "Cette conversation contient un texte trop long pour être traité ; l’original est conservé",
  "memory:记忆库分页": "Pages de la bibliothèque de mémoires",
  "memory:上一页": "Page précédente",
  "memory:下一页": "Page suivante",
  "memory:等待可用生成模型": "En attente d’un modèle de génération disponible",
  "memory:还没有启用生成模型": "Aucun modèle de génération activé",
  "memory:缺少生成模型 API Key": "Clé API du modèle de génération manquante",
  "memory:模型凭据暂不可用":
    "Identifiants du modèle temporairement indisponibles",
  "memory:本机凭据存储暂不可用":
    "Stockage local des identifiants temporairement indisponible",
  "memory:需要授权访问本机钥匙串。请重新打开 App 并完成一次授权":
    "L’accès au trousseau doit être autorisé. Rouvrez l’application et approuvez l’accès.",
  "memory:读取本机钥匙串超时。重新保存一遍模型配置即可恢复":
    "La lecture du trousseau a expiré. Enregistrez à nouveau la configuration du modèle pour rétablir l’accès.",
  "memory:写入本机钥匙串超时。重新保存一遍模型配置即可恢复":
    "L’écriture dans le trousseau a expiré. Enregistrez à nouveau la configuration du modèle pour rétablir l’accès.",
  "memory:模型认证失败。更新 key 后，到设置里点一次检测，会自动接着处理":
    "Échec de l’authentification du modèle. Mettez à jour la clé et lancez une vérification dans les paramètres pour reprendre le traitement.",
  "memory:模型账户余额不足。充值或换 key 后，到设置里点一次检测，会自动接着处理":
    "Solde du compte du modèle insuffisant. Rechargez ou changez de clé, puis lancez une vérification dans les paramètres pour reprendre le traitement.",
  "memory:模型服务拒绝访问。更新权限后，到设置里点一次检测，会自动接着处理":
    "Le service de modèle a refusé l’accès. Mettez à jour les autorisations et lancez une vérification dans les paramètres pour reprendre le traitement.",
  "memory:模型服务限流": "Limite de requêtes du service de modèle atteinte",
  "memory:模型服务暂不可用": "Service de modèle temporairement indisponible",
  "memory:模型服务响应超时": "Délai de réponse du service de modèle dépassé",
  "memory:模型请求不兼容，请核对配置":
    "Requête au modèle incompatible ; vérifiez la configuration",
  "memory:模型请求未成功": "Échec de la requête au modèle",
  "memory:HY Memory 不可用": "HY Memory indisponible",
  "memory:HY Memory 启动失败": "Échec du démarrage de HY Memory",
  "memory:暂时没能整理，稍后会自动重试":
    "Traitement impossible pour l’instant ; nouvelle tentative automatique plus tard",
  "memory:这次没能整理成功，正在重试":
    "Échec du traitement cette fois-ci ; nouvelle tentative en cours",
  "memory:生成模型换过了，这批会重新整理":
    "Le modèle de génération a changé ; ces éléments seront traités à nouveau",
  "memory:整理方式换过了，这批会重新整理":
    "Les réglages de traitement ont changé ; ces éléments seront traités à nouveau",
  "memory:已提炼": "Extraite",
  "memory:旧版确认记录": "Ancien enregistrement de confirmation",
  "memory:创建": "Création",
  "memory:纠正": "Correction",
  "memory:调整使用范围": "Portée d’accès modifiée",
  "memory:撤销修改": "Modification annulée",
  "memory:模型更新": "Mise à jour par le modèle",
  "memory:被新记忆取代": "Remplacée par une nouvelle mémoire",
  "memory:其他操作": "Autre action",
  "memory:删除（记录）": "Suppression",
  "memory:修改已保存，但读取最新列表失败。已停止使用旧回答，请重新读取。":
    "Modification enregistrée, mais impossible de charger la liste à jour. Les anciennes réponses ne sont plus utilisées ; rechargez la liste.",
  "memory:纠正已保存，但读取最新列表失败。已停止使用旧回答，请重新读取。":
    "Correction enregistrée, mais impossible de charger la liste à jour. Les anciennes réponses ne sont plus utilisées ; rechargez la liste.",
  "memory:未收到操作结果，请重试。": "Aucun résultat reçu. Veuillez réessayer.",
  "memory:批量处理结果已保留，但列表刷新失败，请重新读取记忆库。":
    "Les résultats de l’action groupée sont conservés, mais la liste n’a pas pu être actualisée. Rechargez la bibliothèque de mémoires.",
  "memory:批量处理结果已保留，但列表刷新失败，稍后会自动重新读取。":
    "Les résultats de l’action groupée sont conservés, mais la liste n’a pas pu être actualisée. Elle se rechargera automatiquement plus tard.",
  "memory:召回范围已保存，但列表刷新失败。请稍后重新打开记忆页。":
    "Portée de recherche enregistrée, mais la liste n’a pas pu être actualisée. Rouvrez Mémoires plus tard.",
  "memory:原始对话暂时无法读取":
    "Impossible de charger les conversations originales",
  "memory:没有匹配的原始对话": "Aucune conversation originale correspondante",
  "memory:还没有导入的原始对话":
    "Aucune conversation originale importée pour l’instant",
  "memory:查看已读内容": "Voir le contenu enregistré",
  "memory:已保存原文": "Texte original enregistré",
  "memory:刷新已读内容": "Actualiser le contenu enregistré",
  "memory:正在核对本机已保存记录…":
    "Vérification des données locales enregistrées…",
  "memory:原始消息": "Messages originaux",
  "memory:会话": "Conversations",
  "memory:用户发言": "Messages de l’utilisateur",
  "memory:Agent 发言": "Message de l’agent",
  "memory:作者未知": "Auteur inconnu",
  "memory:原始时间未知": "Date d’origine inconnue",
  "memory:工具记录": "Trace d’outil",
  "memory:原始时间来自已保存记录。":
    "Les dates d’origine proviennent des données enregistrées.",
  "memory:已保存的原文样例": "Exemples de textes originaux enregistrés",
  "memory:最多展示 3 段，不代表全部历史或推荐记忆。":
    "3 exemples au maximum ; il ne s’agit ni de l’historique complet ni de mémoires recommandées.",
  "memory:查看完整对话": "Voir la conversation complète",
  "memory:原文较长，此处仅展示前 600 个字符。":
    "Texte original long ; seuls les 600 premiers caractères sont affichés ici.",
  "memory:这个来源还没有保存原文。":
    "Aucun texte original enregistré depuis cette source pour l’instant.",
  "memory:完整对话": "Conversation complète",
  "memory:关闭完整对话": "Fermer la conversation complète",
  "memory:用户": "Utilisateur",
  "memory:暂时无法读取已保存内容，旧回执已收起。请检查授权或重试。":
    "Impossible de lire le contenu enregistré. L’accusé précédent est fermé ; vérifiez les autorisations ou réessayez.",
  "memory:这段原文暂不可读，可能已被撤回授权。请刷新回执。":
    "Ce texte original est indisponible ; l’accès a peut-être été révoqué. Actualisez l’accusé.",
  "memory:外部记忆召回范围": "Portée de la recherche externe de mémoires",
  "memory:外部记忆召回": "Recherche externe de mémoires",
  "memory:调整召回范围": "Ajuster la portée de recherche",
  "memory:所有关联工具": "Tous les outils connectés",
  "memory:已停止外部记忆召回": "Recherche externe de mémoires arrêtée",
  "memory:原有限定范围": "Portée limitée précédente",
  "memory:选择召回范围": "Choisir la portée de recherche",
  "memory:停止外部记忆召回": "Arrêter la recherche externe de mémoires",
  "memory:正在保存…": "Enregistrement…",
  "memory:保存召回范围": "Enregistrer la portée de recherche",
  "memory:取消范围修改": "Annuler les modifications de portée",
  "memory:原文检索仍按来源授权；已带出的内容不会撤回。":
    "Les recherches dans le texte original respectent toujours les autorisations des sources ; le contenu déjà fourni ne sera pas récupéré.",
  "memory:记忆已更新，请重新打开这条记忆后再调整范围。":
    "La mémoire a changé. Rouvrez-la avant d’ajuster la portée.",
  "memory:范围修改未提交，当前范围保持不变。请修正后重试。":
    "Les modifications de portée n’ont pas été envoyées. La portée actuelle est inchangée ; corrigez le problème et réessayez.",
  "memory:保存结果尚未确认，请重试。":
    "Le résultat de l’enregistrement n’est pas confirmé. Veuillez réessayer.",
  "memory:原话已保存在本机；当前内容、模型接收方或授权版本尚未通过核对，未继续整理。":
    "L’original est enregistré sur cet appareil ; le contenu, le destinataire du modèle ou la version des autorisations n’a pas passé la vérification, le traitement n’a donc pas continué.",
  "memory:HY 未返回可核对的已有记忆，未创建替代内容；原始记录已保留，可重试处理。":
    "HY n’a renvoyé aucune mémoire existante vérifiable, et aucun remplacement n’a été créé. Les données originales sont conservées ; vous pouvez relancer le traitement.",
  "memory:历史引用尚未绑定到原始来源，当前批次未完成，原话已保留。":
    "Les citations historiques ne sont pas encore liées à leurs sources originales. Ce lot est incomplet ; l’original est conservé.",
  "memory:记忆内容未通过来源与作者核验，当前批次未完成，原话已保留。":
    "Le contenu de la mémoire n’a pas passé la vérification de la source et de l’auteur. Ce lot est incomplet ; l’original est conservé.",
  "memory:所选记忆已不可读取，请重新读取记忆库。":
    "La mémoire sélectionnée n’est plus lisible. Rechargez la bibliothèque de mémoires.",
  "memory:还没连上本地 Core，正在重试…":
    "Le Core local n’est pas encore connecté ; nouvelle tentative…",
  "memory:记忆内容或使用权限已变化，回答已停止。请重新提问。":
    "Le contenu ou les autorisations d’accès de la mémoire ont changé ; la réponse a donc été interrompue. Posez à nouveau votre question.",
  "memory:这条记忆的来源授权已撤回，暂时不能读取。":
    "L’accès à la source de cette mémoire a été révoqué ; elle ne peut pas être lue pour le moment.",
  "memory:记忆已被更新，请刷新后重试。":
    "Cette mémoire a été mise à jour. Actualisez et réessayez.",
  "memory:已删除的记忆不能修改。":
    "Les mémoires supprimées ne peuvent pas être modifiées.",
  "memory:这条记忆已经删除。": "Cette mémoire a été supprimée.",
  "memory:这条记忆已被更新的记忆取代，只能查看或删除。":
    "Cette mémoire a été remplacée par une mémoire plus récente et peut seulement être consultée ou supprimée.",
  "memory:没有更早的修改可以撤销。":
    "Aucune modification antérieure à annuler.",
  "memory:来源原话已不存在，无法建立这条记忆。":
    "Le texte source original n’existe plus ; cette mémoire ne peut donc pas être créée.",
  "memory:这次操作与已完成的请求不一致。":
    "Cette action ne correspond pas à la demande déjà effectuée.",
  "memory:输入内容不符合要求。": "La saisie ne respecte pas les exigences.",
  "memory:本地记忆库正忙，请稍后重试。":
    "La bibliothèque de mémoires locale est occupée. Veuillez réessayer plus tard.",
  "memory:记忆": "Mémoires",
  "memory:记忆空间": "Espace mémoire",
  "memory:全部应用": "Toutes les applications",
  "memory:来源": "Source",
  "memory:主题": "Thème",
  "memory:归纳": "Synthèse",
  "memory:全部记忆": "Toutes les mémoires",
  "memory:来源待核对": "Source à vérifier",
  "memory:所选来源待核对": "Source sélectionnée à vérifier",
  "memory:未识别来源": "Source non identifiée",
  "memory:这个来源下还没有记忆":
    "Aucune mémoire issue de cette source pour l’instant",
  "memory:只在最相关的 {count} 条记忆里查找关键词":
    "Les mots-clés ne sont recherchés que parmi les {count} mémoires les plus pertinentes",
  "memory:未知来源": "Source inconnue",
  "memory:记忆读取失败": "Impossible de charger les mémoires",
  "memory:正在读取…": "Chargement…",
  "memory:正在更新…": "Mise à jour…",
  "memory:读取失败": "Échec du chargement",
  "memory:正在核对来源…": "Vérification des sources…",
  "memory:正在导入历史会话，记忆将陆续出现":
    "Importation des conversations antérieures ; les mémoires apparaîtront progressivement",
  "memory:记忆工作台": "Espace de travail des mémoires",
  "memory:来源与主题": "Sources et thèmes",
  "memory:记忆或原始对话": "Mémoires ou conversations originales",
  "memory:原始对话": "Conversations originales",
  "memory:地图组织方式": "Regroupement de la carte",
  "memory:筛选地图记忆": "Filtrer les mémoires de la carte",
  "memory:搜索原始对话": "Rechercher dans les conversations originales",
  "memory:搜索记忆": "Rechercher dans les mémoires",
  "memory:筛选记忆…": "Filtrer les mémoires…",
  "memory:向记忆提问": "Poser une question aux mémoires",
  "memory:询问全部已授权可用记忆…":
    "Interroger toutes les mémoires autorisées et disponibles…",
  "memory:发送问题": "Envoyer la question",
  "memory:刷新地图记忆": "Actualiser les mémoires de la carte",
  "memory:关闭新增记忆提示": "Ignorer l’avis de nouvelle mémoire",
  "memory:地图记忆列表": "Liste des mémoires de la carte",
  "memory:记忆读取失败，请重试。":
    "Impossible de charger les mémoires. Veuillez réessayer.",
  "memory:重新读取地图记忆": "Recharger les mémoires de la carte",
  "memory:没有匹配的记忆": "Aucune mémoire correspondante",
  "memory:本页没有匹配的主题记忆":
    "Aucune mémoire de thème correspondante sur cette page",
  "memory:还没有已生成的记忆": "Aucune mémoire créée pour l’instant",
  "memory:地图记忆分页": "Pages des mémoires de la carte",
  "memory:上一页记忆": "Page de mémoires précédente",
  "memory:下一页记忆": "Page de mémoires suivante",
  "memory:记忆关系图": "Graphe des mémoires",
  "memory:记忆关联": "Liens entre mémoires",
  "memory:刚导入的原话，还不是沉淀后的记忆":
    "Texte original tout juste importé, pas encore transformé en mémoires",
  "memory:来源归属": "Rattachement aux sources",
  "memory:可在沉淀记忆后查看视图": "Créez des mémoires pour afficher le graphe",
  "memory:正在读取记忆地图…": "Chargement de la carte des mémoires…",
  "memory:查看选中来源原文": "Voir le texte de la source sélectionnée",
  "memory:查看选中记忆原文": "Voir le texte de la mémoire sélectionnée",
  "memory:查看原文": "Voir la conversation originale",
  "memory:整理已有关系": "Établir les liens existants",
  "memory:正在读取原文…": "Chargement de la conversation originale…",
  "memory:选择一条原始对话": "Sélectionnez une conversation originale",
  "memory:记忆暂时无法读取": "Les mémoires sont temporairement indisponibles",
  "memory:当前筛选没有可显示的记忆": "Aucune mémoire à afficher pour ce filtre",
  "memory:正在读取记忆…": "Chargement des mémoires…",
  "memory:已保存的对话还未生成记忆":
    "Les conversations enregistrées n’ont pas encore été transformées en mémoires",
  "memory:还没有可显示的记忆": "Aucune mémoire à afficher pour l’instant",
  "memory:连线表示来源归属": "Les lignes indiquent le rattachement aux sources",
  "memory:记忆详情": "Détails de la mémoire",
  "memory:来源原文": "Texte original de la source",
  "memory:关联节点": "Nœud associé",
  "memory:关闭记忆详情": "Fermer les détails de la mémoire",
  "memory:关闭详情": "Fermer les détails",
  "memory:正在读取这条记忆…": "Chargement de cette mémoire…",
  "memory:这条记忆的原文暂时无法读取，选择已保留。":
    "Impossible de charger le texte original de cette mémoire. La sélection est conservée.",
  "memory:重试读取原文": "Réessayer de charger la conversation",
  "memory:已沉淀的记忆": "Mémoires créées",
  "memory:正在读取记忆详情…": "Chargement des détails de la mémoire…",
  "memory:沉淀记忆后查看详情": "Créez des mémoires pour voir les détails",
  "memory:时间未知": "Date inconnue",
  "memory:来源原文已不可读取":
    "Le texte original de la source est indisponible",
  "memory:关联记忆": "Mémoires associées",
  "memory:正在读取完整对话…": "Chargement de la conversation complète…",
  "memory:重试读取完整对话": "Réessayer de charger la conversation complète",
  "memory:看完整对话": "Voir la conversation complète",
  "memory:今天": "Aujourd’hui",
  "memory:你": "Vous",
  "memory:助手": "Assistant",
  "memory:工具": "Outil",
  "memory:未知": "Inconnu",
  "memory:作者待核对": "Auteur à vérifier",
  "memory:你说": "Vous avez dit",
  "memory:助手原话": "Message original de l’assistant",
  "memory:已沉淀": "Créée",
  "memory:已删除": "Supprimée",
  "memory:已被取代": "Remplacée",
  "memory:状态待核对": "État à vérifier",
  "memory:编辑": "Modifier",
  "memory:原始依据": "Preuves originales",
  "memory:这段原文当前不可查看。":
    "Ce texte original est actuellement indisponible.",
  "memory:版本记录": "Historique des versions",
  "memory:更多管理": "Plus d’actions",
  "memory:撤销上次修改": "Annuler la dernière modification",
  "memory:删除记忆": "Supprimer la mémoire",
  "memory:编辑记忆": "Modifier la mémoire",
  "memory:返回记忆": "Retour à la mémoire",
  "memory:修改这条记忆，原始对话保持不变。":
    "Modifiez cette mémoire ; la conversation originale reste inchangée.",
  "memory:记忆内容": "Contenu de la mémoire",
  "memory:使用范围保持不变": "La portée d’accès reste inchangée",
  "memory:已有新版本": "Une version plus récente existe",
  "memory:你的草稿已保留，先查看新版本再决定如何修改。":
    "Votre brouillon est conservé. Consultez la nouvelle version avant de faire des modifications.",
  "memory:查看新版本": "Voir la nouvelle version",
  "memory:基于此版本继续编辑": "Continuer à modifier à partir de cette version",
  "memory:取消": "Annuler",
  "memory:保存": "Enregistrer",
  "memory:重试": "Réessayer",
  "memory:删除": "Supprimer",
  "memory:对话": "Discussion",
  "memory:工作": "Travail",
  "memory:编码": "Code",
  "memory:保存修改": "Enregistrer",
  "memory:对应记忆": "Mémoire associée",
  "memory:查看记忆": "Voir les mémoires",
  "memory:删除这条记忆？": "Supprimer cette mémoire ?",
  "memory:删除后不再用于新的召回，来源 App 中的原始对话不会删除。":
    "Elle ne sera plus utilisée pour les nouvelles recherches. Les conversations originales dans l’application source ne seront pas supprimées.",
  "memory:已经发送给应用的内容无法远程收回。":
    "Le contenu déjà envoyé aux applications ne peut pas être récupéré à distance.",
  "memory:放弃尚未保存的修改？":
    "Abandonner les modifications non enregistrées ?",
  "memory:来源权限已变化或原文不可用。":
    "Les autorisations de la source ont changé ou le texte original est indisponible.",
  "memory:暂时无法读取新版本，草稿已保留。":
    "Impossible de charger la nouvelle version. Votre brouillon est conservé.",
  "memory:修改未提交；草稿已保留。请按提示修正后重试。":
    "Les modifications n’ont pas été envoyées ; votre brouillon est conservé. Corrigez le problème et réessayez.",
  "memory:修改尚未确认，草稿已保留。请先刷新核对，避免重复提交。":
    "Les modifications ne sont pas confirmées ; votre brouillon est conservé. Actualisez et vérifiez avant de réessayer pour éviter un double envoi.",
  "memory:删除未提交。请核对这条记忆的状态后重试。":
    "La suppression n’a pas été envoyée. Vérifiez l’état de cette mémoire avant de réessayer.",
  "memory:删除尚未确认。请重新核对记忆状态，不会自动扩大删除范围。":
    "La suppression n’est pas confirmée. Vérifiez l’état de la mémoire ; la portée de la suppression ne sera pas étendue automatiquement.",
  "memory:撤销未提交。请按提示修正后重试。":
    "L’annulation n’a pas été envoyée. Corrigez le problème et réessayez.",
  "memory:撤销尚未确认，请先核对当前记忆版本，避免重复撤销。":
    "L’annulation n’est pas confirmée. Vérifiez la version actuelle de la mémoire avant de réessayer pour éviter une double annulation.",
  "memory:添加记忆": "Ajouter une mémoire",
  "memory:关闭新增记忆": "Fermer la fenêtre d’ajout de mémoire",
  "memory:关闭": "Fermer",
  "memory:记下这件事": "Notez-le",
  "memory:例如：项目周会不排在周五。":
    "Par exemple : ne pas programmer la réunion de projet le vendredi.",
  "memory:整理模型：": "Modèle de traitement : ",
  "memory:尚未配置，原话仍可保存":
    "Non configuré ; vous pouvez tout de même enregistrer l’original",
  "memory:正在读取": "Chargement",
  "memory:配置模型": "Configurer le modèle",
  "memory:保存中…": "Enregistrement…",
  "memory:保存原话": "Enregistrer l’original",
  "memory:最近添加": "Ajoutées récemment",
  "memory:原话已保存，等待可用模型":
    "Original enregistré ; en attente d’un modèle disponible",
  "memory:原话已保存，等待整理":
    "Original enregistré ; en attente de traitement",
  "memory:正在等待整理": "En attente de traitement",
  "memory:正在整理": "Traitement en cours",
  "memory:原话已保存，整理未完成": "Original enregistré ; traitement incomplet",
  "memory:整理已完成": "Traitement terminé",
  "memory:原话已保存，未提取出可用记忆":
    "Original enregistré ; aucune mémoire utilisable extraite",
  "memory:最近添加的内容读取失败。":
    "Impossible de lire les mémoires ajoutées récemment.",
  "memory:整理状态暂时不可用，原话已保存。":
    "L’état du traitement est temporairement indisponible ; l’original est enregistré.",
  "memory:已保存，正在后台沉淀记忆":
    "Enregistré ; création des mémoires en arrière-plan",
  "memory:未能确认保存结果，输入已保留。重试不会重复添加。":
    "Impossible de confirmer l’enregistrement. Votre texte est conservé ; réessayer ne créera pas de doublon.",
  "memory:模型服务要求检查余额或付款状态，原话仍已保存。":
    "Le service de modèle demande de vérifier le solde ou l’état du paiement ; l’original reste enregistré.",
  "memory:这台设备打不开 3D 视图，已显示关联列表。":
    "La vue 3D n’est pas disponible sur cet appareil. Affichage des liens sous forme de liste.",
  "memory:图谱工具": "Commandes du graphe",
  "memory:放大": "Zoom avant",
  "memory:放大图谱": "Zoom avant sur le graphe",
  "memory:缩小": "Zoom arrière",
  "memory:缩小图谱": "Zoom arrière sur le graphe",
  "memory:重置视角": "Réinitialiser la vue",
  "memory:重置图谱视角": "Réinitialiser la vue du graphe",
  "memory:查看读取详情": "Voir les détails de lecture",
  "memory:原文暂时无法读取": "Impossible de charger le texte original",
  "memory:暂时无法读取这段对话，已保留当前记忆。":
    "Impossible de charger cette conversation. La mémoire actuelle reste sélectionnée.",
  "memory:已清除筛选，定位到关联记忆。":
    "Filtres effacés pour afficher la mémoire associée.",
  "memory:所选记忆不在当前筛选内，已取消选择。":
    "La mémoire sélectionnée ne correspond pas à ce filtre ; sélection effacée.",
  "memory:列表分隔符": ", ",
  "memory:冒号": " :",
  "memory:手动添加": "Ajoutée manuellement",
  "memory:导入的已有记忆": "Mémoire existante importée",
  "memory:记忆与原文": "Mémoires et texte original",
  "memory:第 {revision} 版": "Version {revision}",
  "memory:对话时间 ": "Date de la conversation",
  "memory:沉淀时间 ": "Créée le",
  "memory:原对话 ": "Conversation originale ",
  "memory:{count} 条记录没有原始时间，未用导入时间代替。":
    "{count} enregistrements n’ont pas de date d’origine ; la date d’importation ne l’a pas remplacée.",
  "memory:作者数量按当前记录分类统计，不代表记忆抽取已完成或语义已核实。":
    "Le nombre d’auteurs reflète les données enregistrées, et non une extraction de mémoires terminée ou un sens vérifié.",
  "memory:统计于 ": "Statistiques au ",
  "memory: · 导入数量仍在更新，可刷新核对":
    " · Le nombre d’importations est encore en cours de mise à jour ; actualisez pour vérifier",
  "memory:对话于 {day}": "Conversation du {day}",
  "memory:导入于 {day}": "Importée le {day}",
  "memory:写入于 {day}": "Écrite le {day}",
  "memory:已开始处理所选对话": "Traitement des conversations sélectionnées",
  "memory:处理请求已受理": "Demande de traitement reçue",
  "memory:模型连接已通过。首批记忆生成后，会逐步出现在记忆列表里。":
    "Connexion au modèle confirmée. Les mémoires apparaîtront dans la liste au fur et à mesure de leur création.",
  "memory:已导入 {count} 段对话，整理成记忆后会出现在这里。":
    "Conversations importées : {count}. Les mémoires apparaîtront ici après le traitement.",
  "memory:历史已保存在本机，记忆就绪后会出现在这里。":
    "L’historique est enregistré sur cet appareil. Les mémoires apparaîtront ici lorsqu’elles seront prêtes.",
  "memory:连接应用并导入历史后，可以开始沉淀记忆。":
    "Connectez une application et importez l’historique pour commencer à créer des mémoires.",
  "memory:当前已生成 0 条记忆。请求已受理不等于已经完成，原始对话、模型处理任务和记忆数量分别统计。":
    "Aucune mémoire créée pour l’instant. Une demande reçue n’est pas une demande terminée ; les conversations originales, les tâches de traitement et les mémoires sont comptabilisées séparément.",
  "memory:返回应用": "Retour aux applications",
  "memory:共 {count} 条 · 正在核对来源…":
    "Total : {count} · Vérification des sources…",
  "memory:共 {count} 条 · 正在核对来源….one":
    "Total : {count} · Vérification des sources…",
  "memory:共 {count} 条": "Total : {count}",
  "memory:共 {count} 条.one": "Total : {count}",
  "memory: · 正在核对…": " · Vérification…",
  "memory:新增 {count} 条记忆": "{count} nouvelles mémoires",
  "memory:新增 {count} 条记忆.one": "{count} nouvelle mémoire",
  "memory:沉淀于 ": "Créée le ",
  "memory:共 {count} 条，第 {page} / {pages} 页":
    "Total : {count} · Page {page} sur {pages}",
  "memory:共 {count} 条，第 {page} / {pages} 页.one":
    "Total : {count} · Page {page} sur {pages}",
  "memory:已沉淀 {count} 条记忆": "{count} mémoires créées",
  "memory:已沉淀 {count} 条记忆.one": "{count} mémoire créée",
  "memory:关键词“{query}”": "Mot-clé « {query} »",
  "memory:已沉淀 {count} 条{filter} · 当前展示 {shown} 条":
    "{count} mémoires créées{filter} · Affichées : {shown}",
  "memory:已沉淀 {count} 条{filter} · 当前展示 {shown} 条.one":
    "{count} mémoire créée{filter} · Affichées : {shown}",
  "memory:（{filter}）": " ({filter})",
  "memory:第 {page} 页": "Page {page}",
  "memory:正在等待整理：": "En attente de traitement : ",
  "memory:记忆未在生成": "Aucune mémoire n’est en cours de création",
  "memory:还没有开始生成记忆：{reason}（{count} 条待处理）":
    "Aucune mémoire n’est en cours de création : {reason} ({count} en attente)",
  "memory:去配置生成模型": "Configurer le modèle de génération",
  "memory:模型暂停处理": "Modèle en pause",
  "memory:模型配置有问题（{reason}），记忆暂停处理，已有 {count} 条在排队":
    "Problème de configuration du modèle ({reason}). Le traitement des mémoires est en pause ; {count} éléments en attente",
  "memory:模型配置有问题（{reason}），记忆暂停处理，已有 {count} 条在排队.one":
    "Problème de configuration du modèle ({reason}). Le traitement des mémoires est en pause ; {count} élément en attente",
  "memory:API Key 无效或没有权限": "clé d’API invalide ou sans autorisation",
  "memory:找不到模型或地址": "modèle ou adresse introuvable",
  "memory:请求过于频繁，被服务商限流":
    "trop de requêtes, limitées par le fournisseur",
  "memory:模型响应超时": "délai de réponse du modèle dépassé",
  "memory:模型服务出错": "erreur du service de modèle",
  "memory:无法连接模型服务": "impossible de joindre le service de modèle",
  "memory:连接模型服务失败": "échec de la connexion au service de modèle",
  "calls.directHost": "Appel direct (diagnostic)",
  "calls.headline.timedOut": "{host} n’a pas pu rechercher de mémoires à temps",
  "calls.headline.unavailable":
    "{host} ne peut pas rechercher de mémoires pour le moment",
  "calls.headline.denied":
    "{host} n’était pas autorisé à rechercher des mémoires",
  "calls.headline.empty": "{host} n’a trouvé aucune mémoire correspondante",
  "calls.headline.found": "{host} a trouvé {count} éléments associés",
  "calls.headline.found.one": "{host} a trouvé {count} élément associé",
  "calls.headline.rawOnly": "{host} a trouvé {count} conversations d’origine",
  "calls.headline.rawOnly.one":
    "{host} a trouvé {count} conversation d’origine",
  "calls.headline.memoryOnly": "{host} a trouvé {count} mémoires",
  "calls.headline.memoryOnly.one": "{host} a trouvé {count} mémoire",
  "calls.headline.mixed":
    "{host} a trouvé {memories} mémoires · {raw} conversations d’origine",
  "calls.headline.mixed.one":
    "{host} a trouvé {memories} mémoire · {raw} conversations d’origine",
  "calls.headline.completed": "L’appel de {host} est terminé",
  "calls.detail.timedOut":
    "Les mémoires sont en cours de traitement. La recherche a expiré ; cette réponse n’inclut donc pas de mémoires.",
  "calls.detail.unavailable":
    "MemHub est temporairement indisponible ; cette réponse n’inclut donc pas de mémoires.",
  "calls.detail.denied":
    "Les autorisations actuelles ne permettent pas cette recherche.",
  "calls.detail.partialDelivered":
    "Fourni à {host} · une partie du contenu est encore en cours de traitement",
  "calls.detail.partial":
    "Une partie du contenu est encore en cours de traitement",
  "calls.detail.delivered": "Fourni à {host}",
  "calls.detail.notDelivered": "Non fourni à l’application",
  "calls.detail.empty": "Aucune mémoire correspondante cette fois-ci",
  "calls.detail.success": "Recherche réussie",
  "calls.title": "Historique d’utilisation",
  "calls.query": "Cette recherche de mémoires",
  "calls.backApps": "Retour aux applications",
  "calls.backHistory": "Retour à l’historique",
  "calls.filter": "Filtrer par application",
  "calls.allApps": "Toutes les applications",
  "calls.recent": "Historique récent",
  "calls.refresh": "Actualiser l’historique",
  "calls.clearPage": "Effacer cette page",
  "calls.clearAll": "Effacer tout l’historique",
  "calls.clearAllShort": "Tout effacer",
  "calls.loading": "Chargement de l’historique d’utilisation",
  "calls.clearing": "Effacement de l’historique sélectionné",
  "calls.readFailed":
    "Impossible de charger l’historique. Actualisez et réessayez.",
  "calls.unavailable":
    "L’historique d’utilisation est indisponible. Actualisez plus tard.",
  "calls.incomplete":
    "Certains appels n’ont pas été enregistrés ; la liste peut être incomplète.",
  "calls.callDetail": "Détails de l’appel",
  "calls.source": " · Source : {source}",
  "calls.stats":
    "Passages originaux trouvés : {raw} · Mémoires : {memories} · Durée : {seconds} s",
  "calls.metadata": "Détails de l’enregistrement",
  "calls.privacy":
    "Pour protéger votre vie privée, votre question d’origine et la réponse ne sont pas stockées ici ; seuls la date, la source et le résultat sont conservés.",
  "calls.exactTime": "Heure exacte",
  "calls.app": "Applications",
  "calls.type": "Type",
  "calls.result": "État du résultat",
  "calls.tool": "Rechercher des mémoires",
  "calls.notRecorded": "Non enregistré",
  "calls.missingRow":
    "Cet enregistrement n’est plus sur cette page. Retournez à l’historique pour le retrouver.",
  "calls.list": "Historique des appels",
  "calls.emptyPage": "Aucun enregistrement sur cette page",
  "calls.empty": "Aucun historique d’utilisation pour l’instant",
  "calls.pagination": "Pages de l’historique",
  "calls.previous": "Page précédente",
  "calls.next": "Page suivante",
  "calls.policy": "Conservé {days} jours, {count} enregistrements au maximum",
  "calls.confirmAllTitle": "Effacer tout l’historique d’utilisation ?",
  "calls.confirmPageTitle":
    "Effacer les {count} enregistrements de cette page ?",
  "calls.confirmPageTitle.one": "Effacer l’enregistrement de cette page ?",
  "calls.confirmAllDescription":
    "Supprime tous les enregistrements d’appels conservés, pas seulement cette page ou ce filtre. Les conversations originales, les mémoires, les autorisations et les règles de conservation restent inchangées. Cette action est irréversible.",
  "calls.confirmPageDescription":
    "Supprime uniquement ces enregistrements d’appels. Les conversations originales, les mémoires, les autorisations et les règles de conservation restent inchangées. Cette action est irréversible.",
  "calls.confirmAll": "Confirmer l’effacement complet",
  "calls.confirmPage": "Confirmer l’effacement",
  "calls.cancel": "Annuler",
  "calls.clearedAll": "Tout l’historique a été effacé.",
  "calls.clearedPage": "L’historique sélectionné a été effacé.",
  "calls.clearAllFailed":
    "Impossible d’effacer l’historique d’utilisation. Les enregistrements existants sont inchangés ; vérifiez et réessayez.",
  "calls.clearPageFailed":
    "Impossible d’effacer l’historique sélectionné. Les enregistrements existants sont inchangés ; vérifiez et réessayez.",
  "calls.clearUnknown":
    "Impossible de confirmer le résultat. Vérifiez l’historique rechargé ; l’effacement n’a pas été relancé automatiquement.",
  "calls.references": "Contenu associé",
  "calls.referencesLoading": "Chargement du contenu associé",
  "calls.referencesFailed":
    "Impossible de charger le contenu associé. Actualisez et réessayez.",
  "calls.raw": "Conversation originale",
  "calls.memory": "Mémoires",
  "calls.changed": "Version modifiée",
  "calls.pending": "À vérifier",
  "calls.notAvailable": "Indisponible",
  "calls.openRaw": "Voir la conversation originale",
  "calls.openMemory": "Voir les mémoires",
  "calls.noReferences": "Aucun contenu associé trouvé",
  "calls.rawBody": "Texte de la conversation originale",
  "calls.rawLoading": "Chargement de la conversation originale",
  "calls.rawFailed":
    "La conversation originale est indisponible ou sa version a changé.",
  "calls.state.grounded": "Recherche réussie",
  "calls.state.empty": "Recherche réussie ; aucune mémoire correspondante",
  "calls.state.partial":
    "Recherche réussie ; une partie du contenu est encore en cours de traitement",
  "calls.state.timed_out":
    "Délai de recherche dépassé ; aucune mémoire fournie",
  "calls.state.unavailable": "MemHub est temporairement indisponible",
  "calls.state.denied": "Cette recherche n’est pas autorisée",
  "calls.return.timed_out":
    "MemHub importe et traite les mémoires. La recherche n’a pas abouti à temps ; {host} a donc répondu sans mémoires. Elle reprendra une fois le traitement terminé.",
  "calls.return.unavailable":
    "MemHub est temporairement indisponible ; cette réponse n’inclut pas de mémoires.",
  "calls.return.denied":
    "Les autorisations actuelles ne permettent pas cette recherche ; aucune mémoire n’a été fournie à l’application.",
  "calls.return.partial":
    "Une partie du contenu est encore en cours de traitement.",
  "clock.unknown": "Date inconnue",
  "clock.today": "Aujourd’hui à {clock}",
  settings: "Paramètres",
  languageTitle: "Langue de l’interface",
  languageLabel: "Langue",
  languageSystem: "Utiliser la langue du système",
  languageChinese: "Chinois simplifié",
  languageEnglish: "Anglais",
  languageSpanish: "Espagnol",
  languageDescription:
    "Choisissez la langue de l’interface. Ce réglage ne modifie ni vos conversations ni vos mémoires.",
  languageSaveError:
    "Impossible d’enregistrer le réglage de langue. Veuillez réessayer.",
  languageLoadError:
    "Impossible de charger le réglage de langue. Veuillez réessayer.",
  nativeErrorTitle: "MemHub n’a pas pu démarrer",
  startupPreparing: "Démarrage et préparation du service de mémoire…",
  startupUpgradeHint:
    "Le premier lancement ou une mise à niveau des données de mémoire peut prendre quelques minutes. MemHub s’ouvrira automatiquement une fois l’opération terminée.",
  "migration.dialogMessage":
    "La migration des données pour cette mise à jour de MemHub ne s’est pas terminée.",
  "migration.dialogDetail":
    "Pour protéger vos données, MemHub ne démarrera pas. Joignez le journal de migration à un ticket GitHub et envoyez-le-nous. (Code d’erreur : {code})",
  "migration.dataRestored":
    "Vos données ont été restaurées dans leur état d’avant la mise à jour.",
  "migration.logPath": "Journal : {path}",
  "migration.openLog": "Ouvrir le journal",
  "migration.quit": "Quitter",
  "migration.issueHeading": "## Échec de la migration lors de la mise à jour",
  "migration.issueUpgrade":
    "- La mise à jour de MemHub de {fromVersion} (build {fromBuild}) vers {toVersion} (build {toBuild}) n’a pas pu migrer les données.",
  "migration.issueCode": "- Code d’erreur : {code}",
  "migration.issueStep": "- Étape en échec : {step}",
  "migration.issueRestored":
    "- Données restaurées dans leur état d’avant la mise à jour : {restored}",
  "migration.issueSystem": "- Système : {system}",
  "migration.issueArch": " ({arch})",
  "migration.issueLog": "- Journal de migration (local) : {path}",
  "migration.issueAttach":
    "Faites glisser ici le fichier journal ci-dessus en pièce jointe, puis envoyez.",
  "migration.unknown": "inconnu",
  "migration.yes": "oui",
  "migration.no": "non",
};
