import type { MessageKey } from "../gui-i18n.js";
import { embeddingSwitchPortugueseMessages } from "../gui-i18n-embedding-switch.js";
import { modePortugueseMessages } from "../gui-i18n-mode.js";

/** Portuguese (pt, Brazilian usage) GUI copy. Machine-assisted; native-speaker review recommended. */
export const portugueseMessages: Record<MessageKey, string> = {
  ...modePortugueseMessages,
  ...embeddingSwitchPortugueseMessages,
  "connectionFallback.title": "Conectar outras ferramentas",
  "connectionFallback.desktopOnly":
    "O pareamento local só está disponível no app para desktop.",
  "connectionFallback.description":
    "Gere uma Skill de pareamento de uso único para um Harness local que não está na lista. Chats no navegador, contêineres remotos e outros computadores não podem se conectar diretamente a este dispositivo.",
  "connectionFallback.name": "Nome do Harness",
  "connectionFallback.namePlaceholder": "Por exemplo, Kimi Code",
  "connectionFallback.generating": "Gerando…",
  "connectionFallback.generate": "Gerar Skill de pareamento",
  "connectionFallback.generateFailed":
    "Não foi possível gerar a Skill de pareamento.",
  "connectionFallback.retry": "Tentar novamente",
  "connectionFallback.generated":
    "Skill de pareamento de uso único gerada para {name}. Ela expira às {time}.",
  "connectionFallback.skillLabel": "Skill de pareamento",
  "connectionFallback.copy": "Copiar Skill",
  "connectionFallback.copied":
    "Skill de pareamento copiada. Cole e execute no Harness correspondente.",
  "connectionFallback.copyFailed":
    "Falha ao copiar. Selecione e copie o texto acima.",
  "connectionFallback.localDescription":
    "Somente para ferramentas locais de MCP ou linha de comando. Chats no navegador não podem se conectar diretamente a este dispositivo.",
  "connectionFallback.loading": "Carregando detalhes da conexão local…",
  "connectionFallback.loadFailed":
    "Não foi possível carregar os detalhes da conexão.",
  "connectionFallback.format": "Formato dos detalhes da conexão",
  "connectionFallback.promptTab": "Prompt de conexão",
  "connectionFallback.mcpTab": "Configuração MCP",
  "connectionFallback.commandTab": "Comando",
  "connectionFallback.materialLabel": "Detalhes da conexão local",
  "connectionFallback.materialBoundary":
    "Contém caminhos locais, mas nenhuma memória ou chave. Revise antes de colar e mantenha o MemHub em execução.",
  "connectionFallback.materialCopied":
    "Copiado. Nenhuma conexão foi feita ainda.",
  "connectionFallback.copyPrompt": "Copiar prompt",
  "connectionFallback.copyConfig": "Copiar configuração",
  "connectionFallback.copyCommand": "Copiar comando",
  "connectionFallback.promptTemplate":
    "Conecte este Harness local ao MemHub com a seguinte configuração MCP:\n```json\n{mcp}```\n{shellBlock}\nDepois de conectar, faça uma consulta real de memória e informe o resultado com precisão.",
  "connectionFallback.promptShell":
    "\nSe apenas comandos de shell locais forem compatíveis, use:\n```bash\n{shell}```\n",
  "sdk.memory.title": "Biblioteca de memórias do SDK",
  "sdk.memory.status": "Status da memória",
  "sdk.memory.status.active": "Disponível",
  "sdk.memory.status.superseded": "Substituída",
  "sdk.memory.total": "Memórias: {count}",
  "sdk.memory.total.one": "{count} memória",
  "sdk.memory.loadFailed":
    "Não foi possível carregar a biblioteca de memórias. Tente novamente.",
  "sdk.memory.detailLoadFailed":
    "Não foi possível carregar os detalhes da memória.",
  "sdk.memory.updateFailed":
    "Não foi possível salvar a alteração. Tente novamente.",
  "sdk.memory.deleteFailed":
    "Não foi possível excluir a memória. Tente novamente.",
  "sdk.memory.loading": "Carregando biblioteca de memórias…",
  "sdk.memory.pagination": "Páginas da biblioteca de memórias do SDK",
  "sdk.memory.previous": "Página anterior",
  "sdk.memory.next": "Próxima página",
  "sdk.memory.detail": "Detalhes da memória do SDK",
  "sdk.memory.content": "Conteúdo da memória",
  "sdk.memory.save": "Salvar alterações",
  "sdk.memory.delete": "Excluir memória",
  "sdk.memory.scene.chat": "Chat",
  "sdk.memory.scene.working": "Trabalho",
  "sdk.memory.scene.coding": "Programação",
  "sdk.memory.task": "Tarefa",
  "sdk.memory.solution": "Solução",
  "sdk.memory.page": "Página {page} de {pages}",
  "sdk.raw.title": "Conversas originais do SDK",
  "sdk.raw.searchLabel": "Pesquisar conversas originais",
  "sdk.raw.searching": "Pesquisando…",
  "sdk.raw.search": "Pesquisar",
  "sdk.raw.loadFailed":
    "As conversas originais estão temporariamente indisponíveis.",
  "sdk.raw.searchFailed":
    "Não foi possível pesquisar as conversas originais. Tente novamente.",
  "sdk.raw.recent": "Conversas originais recentes",
  "sdk.raw.empty":
    "Ainda não há conversas originais. Ligue uma origem para as ver aqui.",
  "sdk.raw.total": "{count} conversas originais",
  "sdk.raw.total.one": "{count} conversa original",
  "memory:当前使用基础记忆。新对话会保存为原始记录，不会生成高级记忆；以前生成的高级记忆仍会保留，但当前不会显示或参与记忆召回。":
    "A memória básica está ativa. As novas conversas são guardadas como registos originais e não criam memórias avançadas. As memórias avançadas existentes continuam guardadas, mas não são mostradas nem usadas na recuperação.",
  "memory:当前使用智能记忆。新对话会由模型整理为高级记忆。":
    "A memória inteligente está ativa. O modelo transforma novas conversas em memórias avançadas.",
  "memory:当前使用深度记忆。新对话会生成高级记忆，并由知识图谱和 evolve 继续整合。":
    "A memória profunda está ativa. As novas conversas criam memórias avançadas que o grafo de conhecimento e o evolve continuam a consolidar.",
  "sdk.raw.found": "Resultados: {count}",
  "sdk.raw.found.one": "{count} resultado",
  "memoryInspector.emptyHint":
    "Veja as fontes, o conteúdo e o histórico de alterações.",
  "memory.operationFailed": "Falha na operação de memória ({code})",
  "memorySpace.tabs.workbench": "Bancada",
  "selectedMemory.status.confirmed": "Criada",
  "selectedMemory.status.active": "Ativa",
  "selectedMemory.status.deleted": "Excluída",
  "selectedMemory.status.superseded": "Substituída",
  "selectedMemory.status.shadow": "Oculta",
  "selectedMemory.status.negated": "Negada",
  "selectedMemory.status.conflicted": "Em conflito",
  "selectedMemory.status.archived": "Arquivada",
  "selectedMemory.status.unknown": "Status precisa de revisão",
  "selectedMemory.sourceUnknown": "Fonte precisa de revisão",
  "selectedMemory.sourceUnattributed": "Fonte não identificada",
  "selectedMemory.discardDraft": "Descartar alterações não salvas?",
  "selectedMemory.newVersionReadFailed":
    "Não foi possível carregar a nova versão. Seu rascunho foi mantido.",
  "selectedMemory.updateRejected":
    "As alterações não foram enviadas. Seu rascunho foi mantido; corrija o problema e tente novamente.",
  "selectedMemory.updateUnknown":
    "As alterações não foram confirmadas. Seu rascunho foi mantido; atualize antes de tentar novamente.",
  "selectedMemory.deleteRejected":
    "A exclusão não foi enviada. Revise esta memória e tente novamente.",
  "selectedMemory.deleteUnknown":
    "A exclusão não foi confirmada. Revise a memória; o escopo da exclusão não será ampliado.",
  "selectedMemory.undoRejected":
    "A ação de desfazer não foi enviada. Corrija o problema e tente novamente.",
  "selectedMemory.undoUnknown":
    "A ação de desfazer não foi confirmada. Revise a versão atual antes de tentar novamente.",
  "selectedMemory.author.you": "Você disse",
  "selectedMemory.author.assistant": "Mensagem original do assistente",
  "selectedMemory.author.unknown": "Autor precisa de revisão",
  "selectedMemory.version": "Versão {revision}",
  "selectedMemory.sourceLine": "Fonte {source} · {time}",
  "selectedMemory.timestamps":
    "Hora da conversa {conversation} · Criada em {created}",
  "selectedMemory.writeTime": "Gravada em {created}",
  "selectedMemory.edit": "Editar",
  "selectedMemory.evidence": "Evidência original",
  "selectedMemory.evidenceLine": "{author} · Conversa original {time}:",
  "selectedMemory.evidenceUnavailable":
    "Este texto original não está disponível no momento.",
  "selectedMemory.viewOriginal": "Ver original",
  "selectedMemory.history": "Histórico de versões",
  "selectedMemory.more": "Mais ações",
  "selectedMemory.undo": "Desfazer última alteração",
  "selectedMemory.delete": "Excluir memória",
  "selectedMemory.editTitle": "Editar memória",
  "selectedMemory.originalTitle": "Conversa original",
  "selectedMemory.back": "Voltar à memória",
  "selectedMemory.editDescription":
    "Edite esta memória; a conversa original não será alterada.",
  "selectedMemory.content": "Conteúdo da memória",
  "selectedMemory.scopeUnchanged": "O escopo de acesso não muda",
  "selectedMemory.conflictTitle": "Existe uma versão mais recente",
  "selectedMemory.conflictDescription":
    "Seu rascunho foi mantido. Revise a nova versão antes de editar.",
  "selectedMemory.viewNewVersion": "Ver nova versão",
  "selectedMemory.continueFromVersion":
    "Continuar editando a partir desta versão",
  "selectedMemory.cancel": "Cancelar",
  "selectedMemory.save": "Salvar alterações",
  "selectedMemory.author.user": "Você",
  "selectedMemory.author.agent": "Assistente",
  "selectedMemory.author.tool": "Ferramenta",
  "selectedMemory.sourceUnavailable":
    "As permissões da fonte mudaram ou o texto original não está disponível.",
  "selectedMemory.related": "Memória relacionada",
  "selectedMemory.viewMemory": "Ver memória",
  "selectedMemory.deleteTitle": "Excluir esta memória?",
  "selectedMemory.deleteDescription":
    "Ela não será mais usada em novas consultas. A conversa no app de origem não será excluída.",
  "selectedMemory.sentCannotRecall":
    "Conteúdo já enviado aos apps não pode ser recuperado remotamente.",
  "memory:{count} 条": "Itens: {count}",
  "memory:已可用 · {count} 段原文": "Disponível · Trechos originais: {count}",
  "memory:主题视角（非事项）": "Visão por tema (não por assunto)",
  "memory:来源视角": "Visão por fonte",
  "memory:{count} 条记忆 · 当前已加载": "Memórias: {count} · Carregadas",
  "memory:最近 {count} 条": "Conversas recentes: {count}",
  "memory:已确认": "Confirmada",
  "memory:记忆库": "Biblioteca de memórias",
  "memory:问记忆": "Perguntar às memórias",
  "memory:历史对话": "Histórico de conversas",
  "memory:项目记忆": "Memória de projeto",
  "memory:编码偏好": "Preferência de programação",
  "memory:长期模式": "Padrão de longo prazo",
  "memory:意图": "Intenção",
  "memory:近 3 个月": "Últimos 3 meses",
  "memory:近 6 个月": "Últimos 6 meses",
  "memory:近 1 年": "Último ano",
  "memory:全部历史": "Todo o histórico",
  "memory:{count} 条这次没能整理成功，正在重试":
    "Repetindo processamento · {count}",
  "memory:{count} 处理中": "Em processamento: {count}",
  "memory: · 处理中 {running} / 待派发 {active}":
    " · Em processamento: {running} / Na fila: {active}",
  "memory: · {count} 条任务状态待核对":
    " · Tarefas com status a revisar: {count}",
  "memory: · {count} 条任务状态异常，正在恢复":
    " · Tarefas com problemas de status: {count}; recuperando",
  "memory:{count} 等待": "Aguardando: {count}",
  "memory:最新 {count} 条待处理": "Mais recentes pendentes: {count}",
  "memory:历史积压 {count} 条": "Acumulados: {count}",
  "memory:（约 {count} 分钟）": " (cerca de {count} min)",
  "memory:（约 {count} 小时）": " (cerca de {count} h)",
  "memory:（约 {count} 天）": " (cerca de {count} dia(s))",
  "memory:同步尝试 {count} 次": "Tentativas de sincronização: {count}",
  "memory:尚未尝试同步": "Sincronização não tentada",
  "memory: · 状态更新于 {date}": " · Atualizado em {date}",
  "memory:HY Memory 当前没有这条记忆，仅重试同步不能补齐。":
    "O HY Memory não contém esta memória; tentar sincronizar de novo não vai restaurá-la.",
  "memory:同步需要可用的本机整理模型或本地嵌入授权。":
    "A sincronização requer um modelo de processamento local disponível ou permissão para usar embeddings locais.",
  "memory:这条记忆或模型版本已经变化，先刷新核对再决定下一步。":
    "Esta memória ou a versão do modelo mudou. Atualize e revise antes de decidir o que fazer.",
  "memory:这条记忆已改，搜索索引还没开始同步。":
    "Esta memória mudou, mas a sincronização do índice de pesquisa ainda não começou.",
  "memory:最近一次同步未成功；可以先重试，结果仍不确定时再刷新核对。":
    "A última sincronização falhou. Tente novamente primeiro; se o resultado ainda for incerto, atualize e revise.",
  "memory:这条记忆已改，搜索索引还没跟上。":
    "Esta memória mudou, mas o índice de pesquisa ainda não foi atualizado.",
  "memory:搜索里还是改之前那句话。":
    "A pesquisa ainda mostra o texto anterior à correção.",
  "memory:重试同步": "Tentar sincronizar de novo",
  "memory:配置整理模型": "Configurar modelo de processamento",
  "memory:未提炼 · 在窗口外": "Não extraída · Fora da janela",
  "memory:未提炼 · 排队中": "Não extraída · Na fila",
  "wb.select": "Selecionar",
  "memory:模型综合": "Síntese do modelo",
  "memory:原文摘录": "Trecho original",
  "memory:没有命中": "Nenhum resultado",
  "memory:正在生成": "Gerando",
  "memory:正在检索记忆": "Consultando memórias",
  "memory: · 基于 {count} 条已确认记忆":
    " · Com base em memórias confirmadas: {count}",
  "memory: · 基于 {count} 条已确认记忆.one":
    " · Com base em {count} memória confirmada",
  "memory:整理需要处理": "O processamento precisa de atenção",
  "memory:正在整理记忆": "Processando memórias",
  "memory:整理状态": "Status do processamento",
  "memory:自动提炼范围": "Janela de extração automática",
  "memory:手动提炼下一批": "Extrair próximo lote manualmente",
  "memory:刷新核对": "Atualizar e verificar",
  "memory:正在导入历史会话，记忆库暂时只显示一部分；导入完成后会自动补全，不需要重试。":
    "Importando o histórico de conversas. Apenas parte das memórias está visível; a lista será completada automaticamente após a importação.",
  "memory:选择：{content}": "Selecionar: {content}",
  "memory:处理失败：{content}": "Falha ao processar: {content}",
  "memory:已选 {count} 条": "Selecionados: {count}",
  "memory:已选 {count} 条.one": "{count} selecionado",
  "memory:删除所选 {count} 条": "Excluir selecionados ({count})",
  "memory:删除所选 {count} 条.one": "Excluir selecionado ({count})",
  "memory:重试失败的 {count} 条": "Tentar de novo os itens com falha ({count})",
  "memory:重试失败的 {count} 条.one":
    "Tentar de novo o item com falha ({count})",
  "memory:没有{status}的记忆。": "Nenhuma memória com status: {status}.",
  "memory:已{action} {count} 条": "Ação concluída ({action}): {count} itens",
  "memory:已{action} {count} 条.one": "Ação concluída ({action}): {count} item",
  "memory:，{count} 条未完成": ", não concluídos: {count}",
  "memory:，{count} 条未完成.one": ", {count} não concluído",
  "memory:未能{action}：{error}":
    "Não foi possível concluir a ação ({action}): {error}",
  "memory:重试{action}": "Tentar novamente: {action}",
  "memory:删除（操作）": "Excluir",
  "memory:处理（操作）": "processar",
  "memory:{count} 条原话": "Trechos originais: {count}",
  "memory:{count} 条原话.one": "{count} trecho original",
  "memory:{count} 次整理已完成": "Processamentos concluídos: {count}",
  "memory:{count} 次整理已完成.one": "{count} processamento concluído",
  "memory: · {count} 条在窗口外（原文可搜）":
    " · Fora da janela: {count} (texto original pesquisável)",
  "memory: · {count} 条在窗口外（原文可搜）.one":
    " · {count} fora da janela (texto original pesquisável)",
  "memory: · 已规划 {planned} 批，当前第 {current} 批":
    " · Lotes planejados: {planned}, lote atual: {current}",
  "memory:{kind}卡在 {reason}（{count} 条）":
    "{kind} bloqueado por {reason} ({count})",
  "memory:已加载 {count} 条记忆": "Memórias carregadas: {count}",
  "memory:已加载 {count} 条记忆.one": "{count} memória carregada",
  "memory:回答只使用已确认记忆。每个答案都可以回到对应记忆和来源原话。":
    "As respostas usam apenas memórias confirmadas. Cada resposta leva de volta à memória e ao texto original.",
  "memory:选择一条记忆": "Selecione uma memória",
  "memory:查看它的来源、使用范围和修改历史。":
    "Veja as fontes, o escopo de acesso e o histórico de alterações.",
  "memory:查看替代它的记忆": "Ver a memória que a substituiu",
  "memory:查看它取代的旧记忆": "Ver a memória que ela substituiu",
  "memory:保存纠正": "Salvar correção",
  "memory:修改历史": "Histórico de alterações",
  "memory:已可用": "Disponível",
  "memory:处理中…": "Processando…",
  "memory:搜索": "Pesquisar",
  "memory:记忆治理状态": "Status da memória",
  "memory:记忆空间模式": "Modos do espaço de memória",
  "memory:来源状态待核对，记忆库暂不可读取。":
    "O status da fonte precisa de revisão; a biblioteca de memórias ainda não pode ser lida.",
  "memory:记忆库读取失败，请重试。":
    "Não foi possível carregar a biblioteca de memórias. Tente novamente.",
  "memory:重新读取记忆库": "Recarregar biblioteca de memórias",
  "memory:正在读取记忆库…": "Carregando biblioteca de memórias…",
  "memory:批量操作结果": "Resultado da ação em lote",
  "memory:批量操作": "Ações em lote",
  "memory:取消选择": "Limpar seleção",
  "memory:向已确认记忆提问": "Perguntar às memórias confirmadas",
  "memory:搜索记忆库": "Pesquisar na biblioteca de memórias",
  "memory:问答记忆范围": "Escopo das memórias para perguntas",
  "memory:只问个人画像": "Perguntar só sobre o perfil pessoal",
  "memory:问全部记忆（含项目）":
    "Perguntar a todas as memórias (incluindo projetos)",
  "memory:例如：我对真实数据有什么要求？":
    "Por exemplo: quais são meus requisitos para dados reais?",
  "memory:搜索偏好、决定或经历":
    "Pesquise preferências, decisões ou experiências",
  "memory:根据记忆回答": "Responder com base nas memórias",
  "memory:清除搜索": "Limpar pesquisa",
  "wb.finding.duplicate": "Da mesma mensagem original",
  "wb.finding.duplicateText":
    "“{first}” e “{second}” vieram da mesma mensagem original e foram atribuídas a {firstScope} e {secondScope}. Verifique se elas se referem à mesma coisa.",
  "wb.finding.dormant": "Sem atualizações recentes",
  "wb.finding.dormantText":
    "Memórias sem alterações nem menções há dois meses: {count}. Talvez precisem de revisão.",
  "wb.finding.blocked": "Aguardando um requisito",
  "wb.finding.blockedText":
    "Conversas na fila aguardando um requisito: {count}. O texto original já está salvo e pode ser pesquisado.",
  "wb.finding.blockedText.one":
    "{count} conversa está na fila aguardando um requisito. O texto original já está salvo e pode ser pesquisado.",
  "wb.unclassified": "Sem categoria",
  "wb.profile": "Perfil pessoal",
  "wb.project": "Projeto {id}",
  "wb.topics": "Tema",
  "wb.all": "Tudo",
  "wb.topicCount": "Temas formados: {count}",
  "wb.topicCount.one": "{count} tema formado",
  "wb.singletons": "; outras tags que aparecem só uma vez: {count}",
  "wb.singletons.one": "; {count} outra tag aparece só uma vez",
  "wb.memories": "Memórias: {count}",
  "wb.memories.one": "{count} memória",
  "wb.sources": "Fontes: {count}",
  "wb.sources.one": "{count} fonte",
  "wb.once":
    "Este tema só aparece aqui. Pode ser algo pontual, ou o mesmo tema pode ter sido dividido com nomes diferentes.",
  "wb.graphTitle": "Grafo de relações: {label}",
  "wb.readSummary": "Ler um resumo deste assunto",
  "wb.summaryPrompt":
    "Explique “{label}” com clareza: como estão as coisas, o que já foi decidido e o que continua em aberto.",
  "wb.building": "Sintetizando…",
  "wb.build": "Criar relações com o HY Memory",
  "wb.drawn": "Exibindo {shown} / {total} memórias",
  "wb.askLabel": "Perguntar sobre este assunto",
  "wb.askPlaceholder":
    "Pergunte sobre este assunto; as respostas usam apenas memórias confirmadas",
  "wb.ask": "Perguntar",
  "wb.originals": "Conversas originais",
  "wb.evidenceNote":
    "Esta memória vem dos trechos originais abaixo ({count}), em ordem cronológica",
  "wb.evidenceNote.one":
    "Esta memória vem do trecho original abaixo, em ordem cronológica",
  "wb.pickCircle":
    "Selecione um círculo no grafo para ver o texto original aqui",
  "wb.unavailable": "O texto original da fonte não está disponível",
  "wb.fullConversation": "Ver conversa completa",
  "wb.you": "Você",
  "wb.assistant": "Assistente",
  "wb.noMemories":
    "Ainda não há memórias. Pesquise as conversas originais importadas no Histórico de conversas.",
  "wb.waiting":
    "Algumas conversas estão na fila aguardando um requisito. O texto original está salvo e pode ser pesquisado.",
  "wb.conditions": "Ver o que falta",
  "wb.findings": "O que seus registros revelam",
  "wb.findingsNote":
    "Calculado a partir dos registros locais. Selecione um item para destacar no grafo as memórias que o sustentam.",
  "wb.showGraph": "Ver no grafo",
  "wb.loading": "Carregando memórias…",
  "wb.coreStarting":
    "Elas aparecerão automaticamente depois que o Core local iniciar; não é preciso atualizar.",
  "wb.nothingToConnect": "Ainda não há nada para conectar.",
  "wb.skeletonNote":
    "Autorize uma fonte para ver a estrutura de fontes e temas; as memórias aparecerão conforme forem criadas.",
  "wb.authorize": "Autorizar uma fonte",
  "wb.conversations": "Conversas: {count}",
  "wb.conversations.one": "{count} conversa",
  "wb.chipChanges": "O que mudou recentemente aqui?",
  "wb.chipSummarize": "Resuma isto em poucos pontos",
  "wb.unknownTime": "Horário desconhecido",
  "wb.unknownSource": "Fonte desconhecida",
  "wb.transcriptGone": "Esta conversa não está mais na biblioteca.",
  "wb.edgeCount": "{label}: {count} conexões",
  "wb.edgeCount.one": "{label}: {count} conexão",
  "wb.noRelations":
    "O HY Memory ainda não encontrou relações. Use o botão acima para executar o System 2.",
  "wb.noModelRelations":
    "Nenhum modelo utilizável está configurado; as relações do HY Memory não estão disponíveis.",
  "provenance.multiple": "Vários autores",
  "provenance.person": "Mensagem original do usuário",
  "provenance.agent": "Mensagem original do agente",
  "provenance.unknown": "Autor desconhecido",
  "provenance.imported": "Memória importada · {author}",
  "provenance.manual": "Adicionada manualmente",
  "provenance.answered": "Criada a partir de uma resposta",
  "provenance.generated": "Criada a partir de uma resposta da IA",
  "provenance.conversation": "Criada a partir de conversas originais",
  "arrival.region": "Status do processamento de memórias",
  "arrival.importing": "Os originais ainda estão sendo importados",
  "arrival.messages": "Mensagens salvas: {count}",
  "arrival.messages.one": "{count} mensagem salva",
  "arrival.conversations": "Conversas: {count}",
  "arrival.conversations.one": "{count} conversa",
  "arrival.details": "Ver progresso e detalhes",
  "arrival.backlog":
    "Grande volume de dados existentes; processando em lotes em segundo plano para manter a qualidade",
  "arrival.items": "Itens: {count}",
  "arrival.items.one": "{count} item",
  "arrival.configure": "Configurar processamento de memórias",
  "arrival.status": "Ver status do processamento",
  "arrival.history": "Ver conversas salvas",
  "arrival.connect": "Conectar um app",
  "arrival.retained": "Os originais salvos permanecem neste dispositivo.",
  "arrival.description.model-required":
    "Revise o modelo de processamento antes de continuar.",
  "arrival.description.processing":
    "Processando primeiro as conversas recentes; mais memórias aparecerão aos poucos",
  "arrival.description.importing":
    "Salvando os originais; o progresso da importação não reflete o progresso de criação das memórias.",
  "arrival.description.failed":
    "Veja por que falhou antes de decidir se vai tentar novamente.",
  "arrival.description.unavailable":
    "As últimas informações confirmadas foram mantidas; verifique o progresso atual novamente.",
  "arrival.description.refreshing":
    "Verificando os registros locais; o progresso não confirmado não é contado.",
  "arrival.description.checking":
    "O conteúdo salvo foi mantido; o progresso precisa ser atualizado.",
  "arrival.description.stopped":
    "O processamento parou; revise o status antes de continuar.",
  "arrival.description.idle":
    "Nenhuma tarefa de processamento em execução no momento.",
  "arrival.description.empty":
    "Conecte um app ou importe um arquivo de conversas; os originais serão salvos primeiro neste dispositivo.",
  "arrival.title.refreshing": "Conteúdo salvo mantido; verificando o progresso",
  "arrival.title.checking": "O progresso precisa ser atualizado",
  "arrival.title.importing": "Importando conversas",
  "arrival.title.processing": "Criando memórias",
  "arrival.title.continue": "Precisa de atenção",
  "arrival.title.model": "Modelo de processamento não configurado",
  "arrival.title.empty": "Nenhuma conversa salva ainda",
  "handoff.region": "Levar memórias para outro app",
  "handoff.answer": "Levar resposta e referências para outro app",
  "handoff.selected": "Levar memórias selecionadas para outro app",
  "handoff.title": "Levar para outro app",
  "handoff.closeTitle": "Fechar pré-visualização",
  "handoff.close": "Fechar pré-visualização do envio",
  "handoff.stats": "Memórias: {count} · Caracteres: {characters} · Sem cortes",
  "handoff.stats.one":
    "{count} memória · Caracteres: {characters} · Sem cortes",
  "handoff.content": "Conteúdo completo para levar a outro app",
  "handoff.target": "App de destino",
  "handoff.other": "Outro app · Somente copiar",
  "handoff.copyOnly": " · Somente copiar",
  "handoff.boundary":
    "Apenas o conteúdo acima é incluído, não as conversas originais. Copiar não autoriza uma conexão; cópias em outros apps não podem ser recuperadas.",
  "handoff.copy": "Copiar conteúdo",
  "handoff.open": "Copiar e abrir {host}",
  "handoff.copyFailed":
    "Falha ao copiar; nenhum app foi aberto. Selecione e copie o texto da pré-visualização.",
  "handoff.opened":
    "Copiado e solicitada a abertura de {host}. Nada foi enviado; confira o campo de entrada no destino.",
  "handoff.notPrefilled":
    "Copiado por completo. Não foi possível preencher {host}; cole no app de destino. Nada foi enviado.",
  "handoff.openFailed":
    "Copiado por completo, mas não foi possível abrir {host}. Nada foi enviado.",
  "handoff.copied": "{target} copiado por completo. Nada foi enviado.",
  "handoff.pasteTarget": ", pronto para colar em {host}",
  "wb.memory": "Esta memória",
  "wb.node": "Este nó",
  "wb.matter": "Este assunto",
  "wb.available": "Disponível",
  "wb.superseded": "Substituída",
  "wb.deleted": "Excluída",
  "wb.revision": "Versão {revision}",
  "wb.collapse": "Recolher",
  "wb.oldSearch": "A pesquisa ainda mostra o texto anterior à correção.",
  "wb.missingIndex":
    "O HY Memory não contém esta memória; tentar de novo não vai restaurá-la.",
  "wb.retrySync": "Tentar sincronizar de novo",
  "wb.correctForm": "Corrigir conteúdo da memória",
  "wb.content": "Conteúdo da memória",
  "wb.correct": "Corrigir",
  "wb.saveCorrection": "Salvar correção",
  "wb.cancelEdit": "Cancelar edição",
  "wb.undo": "Desfazer última alteração",
  "wb.delete": "Excluir",
  "wb.nodeNote":
    "Isto não é uma memória; as memórias se conectam a ele. Ligações diretas: {direct}; alcançáveis em dois saltos: {indirect}.",
  "wb.direct": "Diretas",
  "wb.twoHops": "Dois saltos",
  "wb.idle":
    "Os círculos representam memórias; as linhas ligam as que foram mencionadas na mesma conversa. Selecione uma para ver a origem e editá-la. Escolher outro assunto à esquerda atualiza o grafo.",
  "wb.searching": "Pesquisando memórias…",
  "wb.generated": "Síntese do modelo",
  "wb.extractive": "Trecho original",
  "wb.noHit": "Nenhum resultado",
  "wb.answerSource": "{mode} · Com base em memórias confirmadas: {count}",
  "wb.answerSource.one": "{mode} · Com base em {count} memória confirmada",
  "wb.recalled": "Memórias recuperadas: {count}",
  "wb.recalled.one": "{count} memória recuperada",
  "wb.saveMemory": "Salvar como memória",
  "wb.tooLong":
    "Esta resposta excede o limite atual e não pode ser salva como memória.",
  "wb.summary":
    "Isto é um resumo, não uma memória. Ele é refeito a cada vez para se manter coerente com as memórias abaixo.",
  "wb.thisAnswer": "Esta resposta",
  "wb.type.source": "App de origem",
  "wb.type.topic": "Tema",
  "wb.type.schema": "Síntese HY",
  "wb.type.memory": "Memórias",
  "wb.origin.hy_relation": "Síntese do HY Memory",
  "wb.origin.same_conversation": "Mesma conversa",
  "wb.origin.shared_tag": "Mesmo tema",
  "wb.origin.provenance": "Fonte do texto original",
  "wb.relation.evidence_source": "De",
  "wb.relation.same_conversation": "Mesma conversa",
  "wb.relation.shared_tag": "Mesmo tema",
  "wb.relation.derived_from": "Sintetizada a partir de",
  "wb.relation.tagged_with": "Marcada com",
  "wb.relation.related_to": "Relacionada",
  guideEyebrow: "Experimente",
  guideTitle: "Continue de onde sua última conversa parou",
  guideBack: "Voltar à memória",
  guideChooseScene: "Escolha um cenário",
  guideSame: "Continuar em uma nova conversa",
  guideSameDescription:
    "Encontre decisões anteriores em uma nova conversa no mesmo app.",
  guideSwitch: "Continuar em outro app",
  guideSwitchDescription:
    "O mesmo trabalho, outra IA. Traga primeiro o contexto relevante.",
  guideNew: "Ajudar uma nova ferramenta a me entender",
  guideNewDescription:
    "Comece com um detalhe que você escolher compartilhar em vez de se apresentar de novo.",
  guideReadError:
    "Não foi possível ler as memórias agora. Tente novamente mais tarde.",
  guideDetailError:
    "Esta memória não pode ser lida agora. Escolha outra ou atualize.",
  guideChanged:
    "A memória ou as permissões dela mudaram. Atualize e revise novamente.",
  guideConnectionChanged:
    "O status da conexão mudou; nada foi copiado. Escolha um método novamente.",
  guideCopiedOpened:
    "Copiado e solicitada a abertura de {host}. Nada foi enviado; confira o campo de entrada.",
  guideCopiedNotFilled:
    "Copiado por completo, mas não foi possível preencher. Cole no app de destino; nada foi enviado.",
  guideCopiedNotOpened:
    "Copiado por completo, mas não foi possível abrir o app de destino. Nada foi enviado.",
  guideCopied:
    "Copiado por completo. Nada foi enviado nem foi concedido novo acesso de leitura.",
  guideValidationError:
    "Não foi possível verificar a memória ou as permissões atuais. A pré-visualização antiga foi ocultada; atualize e tente novamente.",
  guideCopyError:
    "Não foi possível verificar o conteúdo mais recente nem concluir a cópia. Tente novamente; o app de destino não foi aberto.",
  guideManual: "Adicionada no app",
  guideOriginalApp: "App de origem",
  guideListSeparator: ", ",
  guideSaved: "Mensagens originais salvas neste dispositivo: {count} · ",
  "guideSaved.one": "{count} mensagem original salva neste dispositivo · ",
  guideReadingMemories: "Carregando memórias processadas",
  guideLoaded: "Memórias utilizáveis carregadas nesta página: {count}",
  "guideLoaded.one": "{count} memória utilizável carregada nesta página",
  guideSeparate: ". Essas etapas são contadas separadamente.",
  guideChooseBackground: "Escolher contexto",
  guideCheckSources: "Revisar fontes",
  guideTry: "Testar em uma nova conversa",
  guideLoading: "Carregando as memórias existentes neste dispositivo…",
  guideEmptyTitle: "Adicione primeiro um contexto útil",
  guideEmptyDescription:
    "Não há memórias disponíveis para este teste. Originais importados não são memórias processadas, e as sugestões não são compartilhadas automaticamente.",
  guideAdd: "Adicionar uma memória",
  guideConnections: "Ver importações e conexões",
  guideRefresh: "Atualizar",
  guideBackgroundTitle: "Contexto para este teste",
  guideRefreshExample: "Atualizar exemplo",
  guideChooseMemory: "Selecione uma memória",
  guideLoadedScope:
    "Escolha entre as memórias utilizáveis carregadas aqui ({count}); este não é todo o seu histórico.",
  "guideLoadedScope.one":
    "Escolha a memória utilizável carregada aqui; este não é todo o seu histórico.",
  guideCheckingEvidence: "Verificando evidências originais…",
  guideRevision:
    "{source} · Versão {revision} · Gerada pelo sistema, precisa da sua revisão",
  guideScope: "Escopo: ",
  guideAllApps:
    "Disponível em todos os apps; não é necessariamente uma preferência pessoal",
  guidePrivate:
    "A recuperação externa está desativada; compartilhar manualmente exige uma decisão à parte",
  guideLimited: "Escopo limitado; a recuperação externa não foi ampliada",
  guideEvidence: "Evidência original · Trechos: {count}",
  "guideEvidence.one": "Evidência original · {count} trecho",
  guideUnknownSource: "Fonte desconhecida",
  guideYou: "Você",
  guideUnknownAuthor: "Autor desconhecido",
  guideUnknownTime: "Horário desconhecido",
  guideUnavailable:
    "O texto original ou a permissão atual não está disponível. Escolha outra memória ou atualize; este conteúdo não será compartilhado.",
  guideDraftTitle: "Abertura para a próxima conversa",
  guideChooseApp: "Escolha um app para testar",
  guideCopyOnly: "Escolher um app por conta própria · Somente copiar",
  guideHostsError:
    "Não foi possível ler o status da conexão. Tente novamente ou copie apenas o contexto.",
  guideNoApp:
    "Nenhum app conectado selecionado. Compartilhar manualmente não instala uma conexão.",
  guideWired:
    "A última autoverificação foi bem-sucedida; confirme se funciona para este uso.",
  guideInstalled: "Conexão instalada; o acesso de leitura não foi verificado.",
  guideNotInstalled: "Conexão de leitura não instalada.",
  guideManage: "Gerenciar conexões de apps",
  guideChooseRoute: "Escolha como trazer o contexto",
  guideContext: "Trazer contexto",
  guideQuery: "Deixar o app pesquisar",
  guideTopic: "Tema para continuar",
  guideQueryHint:
    "Envie apenas uma consulta; não coloque a resposta esperada acima na nova conversa.",
  guideNewHint:
    "Compartilhe apenas a memória que você escolheu; nenhum perfil pessoal é criado automaticamente.",
  guideContextHint:
    "Inclua este contexto e suas citações; isto não é um teste de recuperação automática.",
  guidePreview: "Pré-visualização completa da abertura",
  guidePreviewPlaceholder:
    "A abertura aparecerá quando a evidência original estiver disponível",
  guideChecking: "Verificando…",
  guideCopy: "Copiar abertura",
  guideCopyOpen: "Copiar e abrir {host}",
  guideNoReceipt:
    "Nenhum retorno de uma chamada externa desta vez. A nova conversa citou o contexto certo?",
  guideVerified: "Revisei o resultado",
  guideMissing: "Não encontrei",
  guideFeedbackOk:
    "Seu feedback: revisado. Isto não é um registro de verificação automática.",
  guideFeedbackMissing:
    "Seu feedback: não encontrado. Verifique primeiro as importações de fontes, o escopo da memória e as conexões de apps.",
  guideInvitation: "Convite para experimentar",
  guideInvitationHint:
    "Experimente continuar uma nova conversa com uma das suas memórias.",
  guideTryOnce: "Experimentar",
  guideLater: "Talvez depois",
  guideDismiss: "Dispensar convite",
  healthTitle: "Diagnóstico de integridade",
  healthLocal: "Serviço local",
  healthBackground: "Serviço em segundo plano",
  healthSources: "Totais por fonte",
  healthProcessing: "Processamento de memórias",
  healthHosts: "Conexões de IA",
  healthDetailed: "Registro detalhado",
  healthDetailedTitle: "Registro detalhado (para solução de problemas)",
  healthDetailedHint:
    "Desativado por padrão. Ative apenas quando o suporte pedir para solucionar um problema.",
  healthDetailedError:
    "Não foi possível alterar o registro detalhado. Tente novamente.",
  healthRefresh: "Atualizar status",
  healthIncident: "Marcar hora do problema",
  healthIncidentDesktop: "Marque a hora do problema no app para desktop.",
  healthIncidentPrompt: "Descreva o problema brevemente (opcional)",
  healthIncidentCopied: "ID do problema copiado: {id}",
  healthIncidentError:
    "Não foi possível marcar a hora do problema. Tente novamente.",
  healthReadFailed: "Temporariamente indisponível",
  healthStale:
    " · Os dados estão desatualizados; mostrando o último resultado.",
  healthReading: "Lendo status…",
  healthRereading: "Atualizando; mostrando a última leitura bem-sucedida…",
  healthReadSuccessTime: "Última leitura bem-sucedida",
  healthReadFailedTime: "Falha na leitura em",
  healthReadingSources: "Lendo o status do serviço local e das fontes…",
  healthPrevious: "Última leitura",
  healthPreviousPrefix: "Última leitura: ",
  healthRunning: "Em execução",
  healthCoreVersion: "{state} · Versão {version} · Instância {instance}",
  healthCoreDisconnected: "O Core local ainda não está conectado.",
  healthBackgroundUnavailable:
    "Não é possível verificar o serviço em segundo plano aqui. Use um app para desktop compatível.",
  healthRuntime: "Core: {core} · Ponte do host: {broker}",
  healthSourceCount: "Conversas: {conversations} · Mensagens: {messages}",
  healthNoSources: "Nenhuma fonte conectada ainda.",
  healthTaskCounts:
    "Em processamento: {running} · Na fila: {pending} · Concluídas: {completed} · Com falha: {failed}",
  healthImportDetails: "Detalhes de importação e processamento",
  healthRawTurns: "Texto original e turnos completos",
  healthRawCounts: "Originais: {raw} · Turnos completos: {turns}",
  healthHy: "Processamento HY",
  healthHyCounts:
    "Chamadas: {calls} · Concluídas: {completed} · Incertas: {unknown} · Com falha: {failed} · Canceladas: {cancelled}",
  healthOutput: "Saída",
  healthMemories: "Memórias: {count}",
  "healthMemories.one": "{count} memória",
  healthWorkbuddy: "Consultas de memória do WorkBuddy",
  healthWorkbuddyCounts:
    "Injeções: {injected} · Tempos esgotados: {timedOut} · Indisponíveis: {unavailable}",
  healthBlocked: "{kind} bloqueado por {reason} ({count})",
  healthRetrying: "Repetindo processamento · {count}",
  "healthRetrying.one": "Repetindo processamento · {count}",
  healthNoIssues: "Nenhum problema precisa de atenção.",
  healthHostsUnavailable:
    "Não é possível ler isto no navegador; verifique o app para desktop.",
  healthNoHosts:
    "Nenhuma ferramenta de IA compatível detectada neste dispositivo.",
  healthBackgroundHealthy: "Serviço em segundo plano disponível",
  healthBackgroundStopped: "Serviço em segundo plano parado",
  healthBackgroundDegraded:
    "Serviço em segundo plano parcialmente indisponível",
  healthBackgroundUntrusted:
    "Não foi possível verificar a identidade do serviço em segundo plano",
  healthBackgroundUnknown: "Status em segundo plano não confirmado",
  healthStopped: "Parado",
  healthUnhealthy: "Não é possível conectar",
  healthUntrusted: "Identidade não verificada",
  healthUnknown: "Não confirmado",
  healthNotChecked: "Não verificado",
  healthWired: "Conectado",
  healthBlind: "Instalado, mas não consegue recuperar memórias",
  healthUnreachable: "Não conectado",
  startupReadError:
    "Não foi possível ler o status de inicialização. Tente novamente.",
  startupRetry: "Tentar novamente",
  startupOpening: "Abrindo o MemHub…",
  nativeHelp: "Ajuda",
  nativeExportLogs: "Exportar logs para o suporte",
  nativeExportError:
    "Não foi possível exportar os logs. Tente novamente mais tarde.",
  nativeLogin: "Abrir {product} ao iniciar sessão",
  nativeTrialUnavailable: " (indisponível nesta versão)",
  nativeCliManaged: " (gerenciado pela CLI)",
  nativeStatusUnavailable: " (status indisponível)",
  nativeDevelopmentUnavailable: " (indisponível em desenvolvimento)",
  nativeOpen: "Abrir {product}",
  nativeQuit:
    "Sair do {product} (o serviço em segundo plano continua em execução)",
  nativeQuitStopsService:
    "Sair do {product} (o serviço de memória também é encerrado)",
  nativeLoginError:
    "Não foi possível alterar a configuração de abertura ao iniciar sessão. Tente novamente.",
  nativeMoveTitle: "Mova o MemHub para Aplicativos primeiro",
  nativeMoveDetail:
    "Abra o MemHub novamente a partir de Aplicativos para conectar outros apps e instalar a ferramenta de linha de comando.",
  nativeAcknowledge: "OK",
  graphWidthTitle:
    "A biblioteca de memórias existente não corresponde ao modelo integrado",
  graphWidthDescription:
    "Sua biblioteca de memórias usa {stored} dimensões, mas o modelo integrado produz {expected}. Eles não funcionam juntos; a extração continuará falhando até você escolher uma opção.",
  graphWidthKeep:
    "Manter a biblioteca existente (padrão): continuar usando a configuração de embeddings anterior; todas as memórias ({count}) e o grafo permanecem inalterados",
  graphWidthRebuild:
    "Usar o modelo integrado: arquivar (sem excluir) o grafo existente; as relações de {count} memórias precisarão ser refeitas",
  graphWidthExport:
    "Exportar primeiro e decidir depois: deixar tudo como está por enquanto e escolher novamente após exportar",
  graphWidthExported:
    "Exportado para {path}. A biblioteca existente não foi alterada; você pode escolher novamente.",
  graphWidthUnknown:
    "A escolha de dimensões não foi confirmada. A biblioteca de memórias existente não foi alterada; tente novamente.",
  graphWidthConfirm: "Confirmar escolha",
  importUserProgress:
    "Importando seu histórico de conversas. Conversas importadas: {count}. As memórias aparecerão aos poucos; você pode continuar usando o app.",
  "importUserProgress.one":
    "Importando seu histórico de conversas. {count} conversa importada. As memórias aparecerão aos poucos; você pode continuar usando o app.",
  importUserModel:
    "O modelo ainda não está configurado. Por enquanto, novas conversas não serão processadas em memórias.",
  importUserIncomplete:
    "Parte do conteúdo não foi totalmente processada. Verifique as Configurações.",
  importUserSettings: "Ir para Configurações",
  diagnosticExportDesktop: "Exporte o diagnóstico pelo app para desktop.",
  diagnosticExported: "Logs exportados: {filename}",
  diagnosticFile: "arquivo de diagnóstico",
  diagnosticExportFailed:
    "Não foi possível exportar o diagnóstico. Tente novamente.",
  debugTitle: "Logs de depuração",
  debugCount: "Logs desta sessão: {count}",
  "debugCount.one": "{count} log desta sessão",
  debugDiagnostics: "Diagnóstico",
  debugUsageSubtitle: "Uso do modelo de processamento de memórias",
  debugTabs: "Abas de diagnóstico",
  debugLogsTab: "Logs",
  debugUsageTab: "Uso do modelo",
  debugFullscreen: "Tela cheia",
  debugExitFullscreen: "Sair da tela cheia",
  debugFullscreenLabel: "Mostrar logs em tela cheia",
  debugExitFullscreenLabel: "Sair dos logs em tela cheia",
  usageRange15m: "Últimos 15 minutos",
  usageRange1h: "Última hora",
  usageRange6h: "Últimas 6 horas",
  usageRange24h: "Últimas 24 horas",
  usageRange7d: "Últimos 7 dias",
  usageRange: "Período",
  usageRangeLabel: "Período de uso do modelo",
  usageLoading: "Carregando…",
  usageReadError: "Não foi possível carregar",
  usageLive: "Atualizar a cada 5 segundos",
  usageTotalTokens: "Total de tokens",
  usageRequests: "Solicitações",
  usageChartRegion: "Tokens por minuto por prompt",
  usageChartTitle: "Uso de tokens por prompt",
  usageChartSubtitle: "Por minuto · Total de tokens",
  usageBreakdown: "Detalhes por prompt",
  usageCurrentRange: "Período atual",
  usageRequestCount: "Solicitações: {count}",
  "usageRequestCount.one": "{count} solicitação",
  usageEmpty:
    "Nenhuma chamada ao modelo de processamento de memórias neste período",
  usageNoChart: "Ainda não há dados para o gráfico",
  usageChartAria: "Uso de tokens por minuto por prompt",
  debugPause: "Pausar atualização ao vivo",
  debugResume: "Retomar atualização ao vivo",
  debugRefresh: "Atualizar agora",
  debugClose: "Fechar logs de depuração",
  debugSearchPlaceholder:
    "Pesquise eventos, componentes, códigos de erro ou conteúdo",
  debugSearch: "Pesquisar logs",
  debugSource: "Origem do log",
  debugAllSources: "Todas as origens",
  debugCapture: "Nível de captura",
  debugDisplay: "Nível de exibição",
  debugAll: "Todos",
  debugMatches: "Correspondências: {count}",
  "debugMatches.one": "{count} correspondência",
  debugLive: "Atualizando ao vivo",
  debugPaused: "Pausado",
  debugTruncated: "Logs demais; lista truncada",
  debugReadError: "Não foi possível carregar",
  debugLoading: "Carregando logs…",
  debugEmpty: "Nenhum log correspondente",
  debugOpened: "Os logs de depuração estão abertos",
  debugOpen: "Abrir logs de depuração",
  "这里需要 API Key，不是网址。请填写服务方提供的密钥。":
    "Insira a chave de API fornecida pelo serviço, não uma URL.",
  "服务商拒绝了这个 API Key（401）。请确认密钥属于当前填写的服务地址。":
    "O provedor rejeitou esta chave de API (401). Verifique se a chave pertence ao endpoint informado.",
  "服务商没有这个模型名，请对照服务商的模型列表核对。":
    "O provedor não reconhece este nome de modelo. Confira a lista de modelos do provedor.",
  "连不上接口地址，请检查网络和服务地址是否正确。":
    "Não foi possível conectar ao endpoint. Verifique a rede e o endereço do serviço.",
  未提供接口地址: "Nenhum endpoint informado",
  "请填写生成模型名。": "Insira o nome do modelo de geração.",
  "请填写完整的 http:// 或 https:// 接口地址。":
    "Insira uma URL de endpoint completa com http:// ou https://.",
  "请填写向量模型名。": "Insira o nome do modelo de embeddings.",
  "请填写完整的向量模型接口地址。":
    "Insira a URL completa do endpoint do modelo de embeddings.",
  "这份配置在测试期间被改过，刚才测的是改动后的版本。配置已保存但未启用，请重新测试并启用。":
    "Esta configuração mudou durante o teste; a versão mais recente foi testada. Ela foi salva, mas não ativada. Teste e ative novamente.",
  "测试结果来自这份配置更早的版本。配置已保存但未启用，请重新测试并启用。":
    "O resultado do teste é de uma versão anterior desta configuração. Ela foi salva, mas não ativada. Teste e ative novamente.",
  "配置已保存，连接测试未通过。请核对配置后重试，历史对话尚未发送。":
    "Configuração salva, mas o teste de conexão falhou. Revise e tente novamente. O histórico de conversas não foi enviado.",
  "配置已保存，尚未确认启用成功。请核对配置后重试。":
    "Configuração salva, mas a ativação não foi confirmada. Revise e tente novamente.",
  "配置尚未完成测试和启用，请核对后重试。":
    "O teste e a ativação não foram concluídos. Revise a configuração e tente novamente.",
  "配置尚未保存成功，已填写内容保留，可核对后重试。":
    "Não foi possível confirmar que a configuração foi salva. Seus dados foram mantidos; revise e tente novamente.",
  "只需配置生成模型。向量嵌入已内置在本机，不为 System 1、System 2 分开选模型。":
    "Só é preciso configurar o modelo de geração. Os embeddings vetoriais são integrados localmente; System 1 e System 2 não precisam de modelos separados.",
  "无法读取测试恢复记录，暂不发送新的测试。":
    "Não foi possível ler o registro de recuperação do teste; nenhum novo teste será enviado.",
  "上次模型测试结果待核对，暂不修改或启用配置。":
    "O teste de modelo anterior precisa de revisão; a configuração não será editada nem ativada por enquanto.",
  核对测试结果: "Revisar resultado do teste",
  "上次模型创建结果待核对，暂不开始新的操作。":
    "A criação de modelo anterior precisa de revisão; nenhuma nova operação será iniciada por enquanto.",
  核对创建结果: "Revisar resultado da criação",
  "配置已保存，正在测试连接…": "Configuração salva; testando conexão…",
  "连接通过，正在启用模型…": "Conexão aprovada; ativando o modelo…",
  刷新配置列表: "Atualizar configurações",
  自定义一个模型: "Configurar um modelo personalizado",
  已保存的模型配置: "Configuração de modelo salva",
  "已保存，已启用": "Salva e ativada",
  "已保存，连接通过": "Salva; conexão aprovada",
  "已保存，测试未通过": "Salva; teste falhou",
  "已保存，待测试": "Salva; aguardando teste",
  版本: "Versão",
  "需要允许 MemHub 访问钥匙串。系统询问时请选择“始终允许”；已保存的配置不会丢失。":
    "Permita que o MemHub acesse as Chaves. Quando solicitado, escolha “Sempre Permitir”; as configurações salvas não serão perdidas.",
  "测试只向此接口发送测试内容，不发送历史对话。":
    "Os testes enviam apenas conteúdo de teste para este endpoint, não o histórico de conversas.",
  重试测试并启用: "Testar de novo e ativar",
  测试并启用: "Testar e ativar",
  编辑配置: "Editar configuração",
  新增配置: "Adicionar configuração",
  编辑: "Editar",
  "。修改后需重新测试并设为当前模型，内容授权不会自动更新。":
    ". Após fazer alterações, teste novamente e defina como modelo atual. As permissões de conteúdo não são atualizadas automaticamente.",
  "配置已在其他位置变化，草稿已保留。请取消编辑后重新打开核对。":
    "A configuração foi alterada em outro lugar; seu rascunho foi mantido. Cancele a edição e abra novamente para revisar.",
  服务商预设: "Predefinição do provedor",
  自定义兼容服务: "Serviço compatível personalizado",
  "只是帮你填好接口地址和一个模型名，不代表这个模型当前可用。 请对照服务商的模型列表核对后再保存。":
    "Isto preenche um endpoint e um nome de modelo, mas não garante que ele esteja disponível. Confira a lista de modelos do provedor antes de salvar.",
  协议: "Protocolo",
  "与模型服务提供的接口协议保持一致。":
    "Use o mesmo protocolo oferecido pelo serviço de modelo.",
  配置名称: "Nome da configuração",
  "用于区分配置，不影响模型请求。":
    "Identifica a configuração; não afeta as solicitações ao modelo.",
  生成模型名: "Nome do modelo de geração",
  "例如：gpt-4o-mini…": "Por exemplo: gpt-4o-mini…",
  "填写服务方提供的完整模型标识。":
    "Insira o ID completo do modelo fornecido pelo serviço.",
  "高级：自行配置向量模型":
    "Avançado: configurar seu próprio modelo de embeddings",
  向量配置模式: "Modo de configuração de embeddings",
  本地内置: "Modelo local integrado",
  自定义向量模型: "Modelo de embeddings personalizado",
  向量模型名: "Nome do modelo de embeddings",
  "例如：text-embedding-3-small…": "Por exemplo: text-embedding-3-small…",
  向量模型来源: "Provedor do modelo de embeddings",
  "本机 Ollama": "Ollama local",
  "同源 OpenAI-compatible（共用 API Key）":
    "Compatível com OpenAI na mesma origem (chave de API compartilhada)",
  "向量模型 Base URL": "Base URL do modelo de embeddings",
  "为避免凭据外泄，向量地址必须与生成模型同源。":
    "Para proteger as credenciais, o endpoint de embeddings deve ter a mesma origem do modelo de geração.",
  "使用内置本地向量模型；不会发送内容给远程向量服务。":
    "Usa o modelo de embeddings local integrado; nenhum conteúdo é enviado a um serviço de embeddings remoto.",
  自定义向量: "Embeddings personalizados",
  尚未填写模型: "Nenhum modelo informado",
  尚未填写地址: "Nenhum endereço informado",
  "填写服务接口地址，不是聊天网页地址。":
    "Insira o endpoint da API, não a URL do site de chat.",
  "（可选）": " (opcional)",
  "保存后不会再次显示…": "Não será exibida novamente após salvar…",
  "留空保留已保存密钥；填写新密钥后需重新测试和启用。":
    "Deixe em branco para manter a chave salva; após inserir uma nova chave, teste e ative novamente.",
  "换了服务商后需要填写新的 API Key，已保存的密钥不会带到新地址。":
    "Insira uma nova chave de API após trocar de provedor; a chave salva não é enviada ao novo endpoint.",
  "保存在系统安全存储中，不随安装包分发。":
    "Armazenada no armazenamento seguro do sistema; não é distribuída com o app.",
  "关闭思考链（向支持的厂商下发 thinking-off 参数）":
    "Desativar cadeia de raciocínio (envia thinking-off aos provedores compatíveis)",
  保存修改: "Salvar alterações",
  保存配置: "Salvar configuração",
  "正在启用配置…": "Ativando configuração…",
  测试并完成配置: "Testar e concluir configuração",
  保存并测试启用: "Salvar, testar e ativar",
  取消编辑: "Cancelar edição",
  "密钥保存在系统安全存储；测试仅发送测试内容。":
    "A chave fica guardada com segurança; os testes enviam apenas conteúdo de teste.",
  当前使用: "Em uso",
  未启用: "Não ativada",
  测试目标: "Destino do teste",
  "。仅发送测试内容，不发送历史对话。":
    ". Apenas conteúdo de teste é enviado, não o histórico de conversas.",
  测试连接: "Testar conexão",
  设为当前模型: "Definir como modelo atual",
  删除: "Excluir",
  "粘贴 API Key": "Colar chave de API",
  "密钥尚未保存成功，已填写内容保留，可重试。":
    "Não foi possível salvar a chave. Seus dados foram mantidos; você pode tentar novamente.",
  生成接收方: "Destinatário da geração",
  向量接收方: "Destinatário dos embeddings",
  本机: "Neste dispositivo",
  "。启用此配置后按上方选择处理历史及未来允许来源。":
    ". Após ativar esta configuração, as seleções acima determinam quais fontes autorizadas, passadas e futuras, serão processadas.",
  "保存 key": "Salvar chave",
  "还没有 key？去申请": "Ainda não tem uma chave? Solicite uma",
  删除这张卡: "Remover este cartão",
  "密钥已配置，当前读取状态待确认。":
    "Chave configurada; o status de acesso precisa de confirmação.",
  "等待 key：其余都已经填好了，贴上 key 就能用。":
    "Aguardando uma chave. O restante já está pronto; cole sua chave para continuar.",
  "key 已保存，还没测过这个模型能不能用。":
    "Chave salva; este modelo ainda não foi testado.",
  "Ollama（本机）": "Ollama (local)",
  "内置 bge-small-zh-v1.5": "bge-small-zh-v1.5 integrado",
  通过: "Aprovado",
  "HY 托管模型尚不可用": "Modelo hospedado HY indisponível",
  "缺少 API Key": "Chave de API ausente",
  服务不可达: "Serviço inacessível",
  "认证失败；更新 key 后再检测，会自动接着处理":
    "Falha na autenticação; atualize a chave e teste novamente para retomar o processamento.",
  请求频率受限: "Limite de solicitações atingido",
  "模型账户余额不足；充值或换 key 后再检测，会自动接着处理":
    "Saldo insuficiente na conta do modelo; recarregue ou troque a chave e teste novamente para retomar.",
  没有找到模型: "Modelo não encontrado",
  "该网关不提供模型列表，已改由生成调用验证":
    "O gateway não fornece lista de modelos; a verificação foi feita com uma solicitação de geração.",
  服务返回不符合协议: "A resposta do serviço não segue o protocolo",
  结构化输出未通过: "O teste de saída estruturada falhou",
  "需要填写 Embedding 模型": "Insira um modelo de embeddings",
  "Embedding 模型不可用": "Modelo de embeddings indisponível",
  "请先核对上次模型测试结果，暂不修改或启用配置。":
    "Revise primeiro o teste de modelo anterior; a configuração não será alterada nem ativada por enquanto.",
  "本地恢复记录已变化，仍保持锁定。请重新打开后核对。":
    "O registro de recuperação local mudou. A configuração continua bloqueada; abra novamente e revise.",
  测试结果: "Resultados do teste",
  "暂不支持一键插入：它没有 MCP 通道，无法发放可撤销的记忆凭证。":
    "A conexão com um clique não está disponível: este app não tem um canal MCP para uma credencial de memória revogável.",
  应用控制通道暂不可用: "O canal de controle do app não está disponível",
  appFailureReason: " Motivo: {reason}. ",
  appFailureReread:
    "Também não foi possível recarregar o status da conexão; o estado exibido pode estar desatualizado.",
  模型: "Modelo",
  连接其他应用: "Conectar outros apps",
  帮助与诊断: "Ajuda e diagnóstico",
  设置分组: "Seções das configurações",
  记忆模型: "Modelo de memória",
  当前模型: "Modelo atual",
  "分别管理记忆整理与搜索所使用的模型。":
    "Gerencie separadamente os modelos usados para organizar e pesquisar memórias.",
  记忆整理: "Organização de memórias",
  "把会话理解并整理成可长期使用的记忆。":
    "Transforma conversas em memórias úteis a longo prazo.",
  记忆搜索: "Pesquisa de memórias",
  "在需要时找到与你的问题最相关的记忆。":
    "Encontra, quando necessário, as memórias mais relevantes para sua pergunta.",
  "本地内置（推荐）": "Integrado localmente (recomendado)",
  "Embedding · 配置记忆整理后可自定义":
    "Embedding · Personalizável após configurar a organização de memórias",
  "Embedding · 本机处理，无需额外配置":
    "Embedding · Processamento local, sem configuração adicional",
  "正在读取模型…": "Carregando modelo…",
  模型状态暂不可读: "O status do modelo não está disponível",
  尚未配置模型: "Nenhum modelo configurado",
  修改模型: "Editar modelo",
  配置模型: "Configurar modelo",
  刷新模型状态: "Atualizar status do modelo",
  "本机 / 受管端点": "Endpoint local / gerenciado",
  接口地址待核对: "Endpoint precisa de revisão",
  "模型状态未能读取，请重试。":
    "Não foi possível ler o status do modelo. Tente novamente.",
  "模型状态刷新未成功，仍显示上次确认的模型状态；请重试。":
    "Não foi possível atualizar o status do modelo. Mostrando o último estado confirmado; tente novamente.",
  "上次模型测试或创建还待核对，配置暂时不能改动。请先在下方模型配置区核对结果； 现有配置和原文不会被改动。":
    "Um teste ou criação de modelo anterior precisa de revisão. Confira o resultado abaixo antes de editar. Sua configuração e o texto original não foram alterados.",
  用模型沉淀记忆: "Criar memórias com um modelo",
  "开启后，导入和同步到本机的会话会交给当前模型整理成记忆，会产生模型调用。 停止后不再调用模型、不再整理记忆，但会话仍会照常导入和保存；已排队的内容会留着，重新开启后继续。 模型配置始终保留，重新开启不需要重新填写。":
    "Quando ativado, as conversas importadas e sincronizadas são processadas em memórias pelo modelo atual, usando chamadas ao modelo. Quando desativado, as conversas continuam sendo importadas e salvas; o trabalho na fila é retomado quando você ativar de novo. Sua configuração de modelo é mantida.",
  "启用未能确认，仍显示上次确认的状态；请重试。":
    "Não foi possível confirmar a ativação. Mostrando o último estado confirmado; tente novamente.",
  "停止未能确认，模型可能仍在整理记忆，请重试。":
    "Não foi possível confirmar a interrupção. O modelo pode ainda estar processando memórias; tente novamente.",
  "当前安装还不能读取这个开关，请确认后台服务已启动后重试。":
    "Esta instalação não consegue ler esta opção. Verifique se o serviço em segundo plano está em execução e tente novamente.",
  关于: "Sobre",
  "此应用还没有读取授权，请先授权它的对话目录。":
    "Este app ainda não tem permissão de leitura. Autorize primeiro o diretório de conversas dele.",
  "此应用的读取授权已撤销，请重新授权。":
    "A permissão de leitura deste app foi revogada. Autorize novamente.",
  "此应用的对话目录已被重建，请重新授权。":
    "O diretório de conversas deste app foi recriado. Autorize novamente.",
  "此应用的对话目录现在读不到，请确认应用已安装并产生过对话。":
    "Não é possível ler o diretório de conversas deste app. Verifique se o app está instalado e tem conversas.",
  "此应用的对话目录已变更，请按最新目录重新授权。":
    "O diretório de conversas deste app mudou. Autorize o novo diretório.",
  "此应用还没有读取授权，请先授权它的对话目录。完成后再开启持续同步。":
    "Este app ainda não tem acesso de leitura. Autorize o diretório de conversas dele e depois ative a sincronização contínua.",
  "此应用的读取授权已撤销，请重新授权。完成后再开启持续同步。":
    "O acesso de leitura deste app foi revogado. Autorize novamente e depois ative a sincronização contínua.",
  "此应用的对话目录已被重建，请重新授权。完成后再开启持续同步。":
    "O diretório de conversas deste app foi recriado. Autorize novamente e depois ative a sincronização contínua.",
  "此应用的对话目录现在读不到，请确认应用已安装并产生过对话。完成后再开启持续同步。":
    "Não é possível ler o diretório de conversas deste app. Verifique se o app está instalado e tem conversas, depois ative a sincronização contínua.",
  "此应用的对话目录已变更，请按最新目录重新授权。完成后再开启持续同步。":
    "O diretório de conversas deste app mudou. Autorize o novo diretório e depois ative a sincronização contínua.",
  "此应用的读取授权已失效，请重新授权后再开启持续同步。":
    "O acesso de leitura deste app expirou. Autorize novamente antes de ativar a sincronização contínua.",
  "此应用的授权或目录已变化，已按最新状态显示，请确认后再试。":
    "As permissões ou o diretório deste app mudaram. Revise o status atualizado e tente novamente.",
  模型配置表单: "Formulário de configuração do modelo",
  "部分运行状态暂时无法读取，当前显示最后读到的状态，请重试。":
    "Algumas informações de status não estão disponíveis. Mostrando o último estado conhecido; tente novamente.",
  "当前安装还不能设置登录后后台启动。":
    "Esta instalação não consegue configurar a abertura ao iniciar sessão.",
  "登录后后台启动设置未能确认，请重试。":
    "Não foi possível confirmar a configuração de abertura ao iniciar sessão. Tente novamente.",
  "当前安装还不能退出界面，请从菜单栏退出。":
    "Esta instalação não consegue fechar a janela. Use a barra de menus.",
  "退出界面未能确认，请重试。":
    "Não foi possível confirmar a saída. Tente novamente.",
  登录后启动后台服务: "Iniciar serviço em segundo plano ao iniciar sessão",
  "无需打开界面，持续同步、整理和提供记忆。":
    "Continue sincronizando, processando e fornecendo memórias sem abrir o app.",
  导出日志: "Exportar logs",
  "把本机运行日志打包成一个文件，发给开发排查。":
    "Empacota todos os logs locais. Eles podem conter conversas ou caminhos locais; compartilhe apenas com desenvolvedores de confiança.",
  "确定退出 One Memory 界面吗？": "Fechar a janela do MemHub?",
  "后台服务会继续同步、整理和提供记忆。":
    "O serviço em segundo plano continuará sincronizando, processando e fornecendo memórias.",
  取消: "Cancelar",
  退出界面: "Fechar janela",
  应用详情: "Detalhes do app",
  返回应用: "Voltar aos apps",
  同步开关: "Opção de sincronização",
  "开启后处理历史待办，并持续接收此应用的新会话。":
    "Quando ativado, processa o histórico pendente e continua recebendo novas conversas deste app.",
  "正在读取已有对话，首次开启可能需要一两分钟，请不要重复点击。":
    "Lendo as conversas existentes. Na primeira vez, isso pode levar um ou dois minutos; não clique novamente.",
  "将补充冻结期间的新对话；原有 pending Job 继续处理。":
    "As conversas recebidas durante o congelamento serão incluídas; os jobs pendentes existentes continuam sendo processados.",
  一键插入应用: "Conexão com um clique",
  "安装记忆连接，并默认允许此应用使用已授权记忆。":
    "Instale a conexão de memória e permita, por padrão, que este app use as memórias autorizadas.",
  允许使用记忆: "Permitir acesso às memórias",
  连接状态: "Status da conexão",
  已接通: "Conectado",
  "已安装，但取不到记忆": "Instalado, mas não consegue recuperar memórias",
  未接通: "Não conectado",
  状态待核对: "Status precisa de revisão",
  检测中: "Verificando",
  未开启关联: "Conexão desativada",
  "正在开启处理…": "Iniciando processamento…",
  "正在冻结处理…": "Congelando processamento…",
  处理中: "Processando",
  已冻结: "Congelado",
  "此应用的处理开关结果未能确认，其他应用不受影响。":
    "Não foi possível confirmar a opção de processamento deste app. Os outros apps não foram afetados.",
  "暂时无法刷新来源状态，请重试；未更改同步或读取权限。":
    "Não foi possível atualizar o status das fontes. Tente novamente; as permissões de sincronização e leitura não foram alteradas.",
  "暂时无法读取连接状态，当前状态待核对；请重试。":
    "Não foi possível ler o status da conexão. O estado atual precisa de revisão; tente novamente.",
  "已接通。已经打开的对话不会中途出现这个工具，请新开一轮。":
    "Conectado. A ferramenta não aparecerá em uma conversa já aberta; inicie uma nova.",
  "本次连接探测失败，当前状态待核对；未把上次读取当作本次成功。":
    "Esta verificação de conexão falhou. O estado atual precisa de revisão; o resultado anterior não foi considerado um novo sucesso.",
  上次读取: "Última leitura",
  appSettingsTitle: "Configurações do app {name}",
  appMemoryScope:
    "Permitir que {name} consulte memórias dentro do escopo autorizado.",
  appRestart:
    "A ferramenta de memória não está na conversa atual de {name}. Feche {name} por completo e abra novamente; ela aparecerá em uma nova conversa.",
  appConnectFailed:
    "A conexão com um clique deste app não foi concluída. Os outros apps não foram afetados. {reason}",
  appDisconnectFailed:
    "Não foi possível remover a conexão nem revogar o acesso às memórias. {name} ainda pode usar as memórias; tente novamente. {reason}",
  appPermissionFailed:
    "Não foi possível confirmar o acesso às memórias deste app. Tente novamente. {reason}",
  appRevokedModified:
    "O acesso às memórias foi revogado, então {name} não pode mais consultar memórias. O arquivo de configuração foi modificado, por isso o MemHub não o excluiu automaticamente; remova a conexão do MemHub manualmente.",
  appRevokedRetained:
    "O acesso às memórias foi revogado, então {name} não pode mais consultar memórias. O arquivo de conexão não foi removido; revise-o novamente.",
  appRevokedReview:
    "O acesso às memórias foi revogado, então {name} não pode mais consultar memórias. Não foi possível confirmar a remoção completa; revise estes caminhos manualmente: {paths}.",
  appRemovalReview:
    "Não foi possível confirmar a remoção completa da conexão. Revise estes caminhos manualmente: {paths}.",
  appProfilesLabel: "Perfis de {name}",
  appProfileOn: "{profile}: conectado",
  appProfileOff: "{profile}: não conectado",
  appProfilesHint:
    "O interruptor instala a conexão de memórias e concede acesso a cada perfil detectado, e revoga todos ao ser desligado. Enquanto estiver ligado, novos perfis são conectados automaticamente.",
  appProfilesNone:
    "Nenhum perfil de {name} foi detectado. Crie um perfil e volte a esta página.",
  appProfilesConnectFailed:
    "Não foi possível conectar estes perfis: {profiles}. Os outros perfis foram mantidos. {reason}",
  appProfilesDisconnectFailed:
    "Não foi possível desconectar estes perfis, que ainda podem usar memórias: {profiles}. Tente novamente. {reason}",
  appProfilesRevokedRetained:
    "O acesso às memórias foi revogado para estes perfis, mas nem todos os arquivos de conexão foram removidos; revise-os: {profiles}.",
  appProfileNameUnsupported:
    "nomes de perfil só podem usar letras minúsculas, dígitos e hifens, até 32 caracteres",
  "apps.autoHostProfilesFailed":
    "Não foi possível conectar automaticamente estes perfis de {name} ({profiles}){reason}.",
  "Cursor 的连接凭证已更新。请完全退出并重新打开 Cursor，再开始新对话。":
    "A credencial de conexão do Cursor mudou. Feche o Cursor por completo e abra novamente antes de iniciar uma nova conversa.",
  连接其他工具: "Conectar outras ferramentas",
  "本机连接材料仅在桌面 App 中可用。":
    "Os detalhes da conexão local só estão disponíveis no app para desktop.",
  "仅本机 MCP / 命令工具。纯网页聊天不支持直接连接本机。":
    "Somente para ferramentas locais de MCP ou linha de comando. Chats no navegador não podem se conectar diretamente a este dispositivo.",
  "列表内工具请用上方一键安装，此处仅供未列出的工具。":
    "Para as ferramentas da lista, use a configuração com um clique acima. Esta opção é para ferramentas que não estão na lista.",
  "正在读取本机连接配置…": "Carregando detalhes da conexão local…",
  "未能读取连接配置。": "Não foi possível carregar os detalhes da conexão.",
  重试: "Tentar novamente",
  连接材料格式: "Formato dos detalhes da conexão",
  连接提示词: "Prompt de conexão",
  "MCP 配置": "Configuração MCP",
  命令: "Comando",
  本机连接材料: "Detalhes da conexão local",
  "包含本机路径，不含记忆和密钥。片段会运行本机 MemHub 连接程序；粘贴前请确认，MemHub 需保持运行。":
    "Contém caminhos locais, mas nenhuma memória ou chave. O trecho executa o conector local do MemHub; revise antes de colar e mantenha o MemHub em execução.",
  "已复制，尚未建立连接。": "Copiado; nenhuma conexão foi feita ainda.",
  "复制失败，可在上方选中文本复制。":
    "Falha ao copiar. Selecione e copie o texto acima.",
  复制提示词: "Copiar prompt",
  复制配置: "Copiar configuração",
  复制命令: "Copiar comando",
  记忆库分页: "Páginas da biblioteca de memórias",
  上一页: "Página anterior",
  下一页: "Próxima página",
  "把熟悉的背景，带到这里": "Traga seu contexto para cá",
  "你的记忆，从这里接上": "Reúna suas memórias",
  "这张卡还没测过。点一次「设为当前模型」会先自动测，如果还是不行，说明端点、key 或模型名有一处不对。":
    "Esta configuração ainda não foi testada. Defini-la como modelo atual executa um teste primeiro; se ainda falhar, verifique o endpoint, a chave e o nome do modelo.",
  "这类模型接口暂时用不了，换一张卡。":
    "Este tipo de endpoint de modelo ainda não é compatível. Use outra configuração.",
  "这张卡缺了向量化模型，补上才能启用。":
    "Esta configuração precisa de um modelo de embeddings antes de ser ativada.",
  "这张卡已经不在了，可能刚被删掉。刷新一下。":
    "Esta configuração não está mais disponível. Ela pode ter sido excluída; atualize a lista.",
  "这张卡刚被改过。刷新一下再试，免得覆盖掉别处的改动。":
    "Esta configuração acabou de ser alterada. Atualize antes de tentar novamente para não sobrescrever outra alteração.",
  onboardingModelOperationFailed: "Falha na operação: {code}",
  onboardingModelSkipDestination: "Pular e ir para Apps",
  首次设置进度: "Progresso da configuração inicial",
  导入历史: "Importar histórico",
  连接应用: "Conectar apps",
  沉淀记忆: "Criar memórias",
  本地存储: "Armazenado localmente",
  "对话与记忆保存在本机；使用云模型或已授权的助手时，相关内容会发送给对应服务。":
    "As conversas e memórias ficam armazenadas neste dispositivo. O conteúdo relevante é enviado a modelos na nuvem ou a assistentes autorizados quando você os usa.",
  "来源位置、身份或状态已变化，请重新选择并核对。尚未提交本次授权。":
    "O local, a identidade ou o status da fonte mudou. Selecione e revise novamente. Esta autorização não foi enviada.",
  "来源位置、身份或状态已变化，正在等待本次提交回执。不会自动扩大已提交范围。":
    "O local, a identidade ou o status da fonte mudou. Aguardando a confirmação deste envio; o escopo enviado não será ampliado automaticamente.",
  测试实例不接入应用: "Instâncias de teste não se conectam a apps",
  "请先把 MemHub 拖到“应用程序”并从那里重新打开，才能接入应用。":
    "Mova o MemHub para Aplicativos e abra-o novamente a partir de lá antes de conectar apps.",
  "部分应用未能完成接入，已成功的设置会保留，可进入应用后单独处理。":
    "Não foi possível conectar alguns apps. As configurações bem-sucedidas foram mantidas; resolva o restante individualmente em Apps.",
  onboardingConnectionPartial:
    "Apps conectados: {success}; incompletos: {failed} ({names}). Você pode resolvê-los individualmente em Apps.",
  "首次设置的完成状态未能保存。已确认的导入回执不受影响；下次启动可能再次出现此页。":
    "Não foi possível salvar a conclusão da configuração inicial. Os comprovantes de importação confirmados não foram afetados; esta página pode aparecer de novo na próxima vez.",
  "首次设置的完成状态尚未确认。已确认的导入回执不受影响；下次启动可能再次出现此页。":
    "A conclusão da configuração inicial não foi confirmada. Os comprovantes de importação confirmados não foram afetados; esta página pode aparecer de novo na próxima vez.",
  "位置检测未完成，尚未读取历史。可以重新检测，或先进入工作台。":
    "A detecção de locais não foi concluída e o histórico não foi lido. Verifique novamente ou continue para o espaço de trabalho.",
  "暂时无法核对来源，尚未提交授权。选择已保留，可以重试。":
    "Não é possível revisar as fontes agora, então a autorização não foi enviada. Suas seleções foram mantidas; você pode tentar novamente.",
  "本次没有来源获准导入。请核对下方原因后重试，或到应用页查看状态。":
    "Nenhuma fonte foi autorizada para esta importação. Revise os motivos abaixo e tente novamente, ou confira o status em Apps.",
  "开始导入的结果尚未确认，选择已保留。可以重试，或进入应用页核对。":
    "O início da importação não foi confirmado. Suas seleções foram mantidas; tente novamente ou confira em Apps.",
  未完成: "Incompleto",
  核对中: "Verificando",
  仍进入应用页: "Continuar para Apps",
  仍进入工作台: "Continuar para o espaço de trabalho",
  仍进入设置: "Continuar para Configurações",
  重试保存并进入: "Tentar salvar de novo e continuar",
  "稍后添加，先进入": "Adicionar depois e continuar",
  "正在开始导入…": "Iniciando importação…",
  开始导入: "Iniciar importação",
  "不用从头介绍自己。从常用的 App 开始，把已有背景带过来。":
    "Você não precisa começar do zero. Traga o contexto dos apps que você mais usa.",
  "确认前只检查应用信息，不读取对话，也不安装连接。":
    "Antes da confirmação, apenas as informações dos apps são verificadas. Nenhuma conversa é lida e nenhuma conexão é instalada.",
  "本机 AI 应用": "Apps de IA neste dispositivo",
  重新检测位置: "Verificar locais novamente",
  "检测中…": "Verificando…",
  重新检测: "Verificar novamente",
  候选应用: "Apps disponíveis",
  "正在检测其他应用…": "Procurando outros apps…",
  检测未完成: "Verificação incompleta",
  已全选: "Todos selecionados",
  全选: "Selecionar todos",
  onboardingImportHistory: "Importar histórico de {name}",
  正在核对本机保存位置: "Verificando o local de armazenamento",
  已发现: "Encontrado",
  位置待核对: "Local precisa de revisão",
  "当前没有候选来源。可以稍后在应用页添加历史包。":
    "Nenhuma fonte encontrada. Você pode adicionar um arquivo de histórico depois em Apps.",
  "候选来源尚未加载。可以检测位置，或先进入工作台。":
    "As fontes não foram carregadas. Verifique os locais ou continue para o espaço de trabalho.",
  本次导入: "Esta importação",
  "个 App 的历史对话": "históricos de apps",
  "把零散的会话，": "Reúna as conversas",
  汇成完整的记忆: "em memórias completas",
  "少一些重复交代，多一些理解。": "Menos repetição. Mais compreensão.",
  onboardingMoreApps: "Role para ver mais · Apps restantes: {count}",
  "onboardingMoreApps.one": "Role para ver mais · {count} app restante",
  onboardingMoreImportApps: "Role horizontalmente · {count} apps restantes",
  onboardingImportChecking:
    "Verificando o resultado da importação; sua seleção foi mantida",
  onboardingAllApps: "Todos os apps exibidos ({count})",
  默认位置: "Local padrão",
  自定义位置: "Local personalizado",
  尚未检测: "Não verificado",
  未发现: "Não encontrado",
  无法访问: "Sem acesso",
  位置无效: "Local inválido",
  "需要更新 Core": "É necessário atualizar o Core",
  "候选位置不存在，可能尚无历史或路径不同。":
    "Este local não existe. Pode ainda não haver histórico, ou o caminho pode ser outro.",
  "无法检查此位置，请核对目录访问权限后重试。":
    "Não foi possível verificar este local. Revise o acesso ao diretório e tente novamente.",
  "此位置未通过安全检查，不能读取。":
    "Este local não passou na verificação de segurança e não pode ser lido.",
  "当前 Core 未提供可确认的位置身份，需更新后再选择。":
    "O Core atual não fornece uma identidade de local verificável. Atualize-o antes de selecionar.",
  暂不支持历史导入: "A importação de histórico ainda não é compatível",
  "当前来源不可读取，请重新检测。":
    "A fonte atual não pode ser lida. Verifique novamente.",
  "来源目录暂时不可读取，请核对位置和权限。":
    "O diretório da fonte está temporariamente ilegível. Verifique o local e as permissões.",
  "来源目录身份已变化，请返回重新检测。":
    "A identidade do diretório da fonte mudou. Volte e verifique novamente.",
  "来源权限已变化，请返回重新确认。":
    "As permissões da fonte mudaram. Volte e confirme novamente.",
  "此来源尚未获得读取授权。": "Esta fonte ainda não tem acesso de leitura.",
  "自定义来源位置需要重新确认。":
    "O local personalizado da fonte precisa ser confirmado novamente.",
  "当前 Core 未启用历史导入。":
    "A importação de histórico não está ativada no Core atual.",
  onboardingImportRefused:
    "Importação não autorizada ({reason}). Revise a fonte e tente novamente.",
  确认自定义位置: "Confirmar local personalizado",
  关闭位置确认: "Fechar confirmação de local",
  "此位置来自当前配置，并非应用默认目录。确认位置不会开始读取历史。":
    "Este local vem da sua configuração atual, não do diretório padrão do app. Confirmar o local não inicia a leitura do histórico.",
  onboardingConfirmHistory:
    "Confirme que este é o local do histórico de {name}",
  确认此位置: "Confirmar este local",
  "对话正在汇入，记忆从这里开始":
    "As conversas estão chegando. As memórias começam aqui",
  "导入进行中 · 顺手连接应用": "Importando · Conecte seus apps",
  "对话已保存，记忆尚待生成。":
    "Conversas salvas; as memórias ainda serão criadas.",
  "导入在后台继续，无需等待。":
    "A importação continua em segundo plano. Não é preciso esperar.",
  "正在重新核对来源；已保存数量仍按任务回执显示。":
    "Verificando as fontes novamente; as contagens salvas ainda refletem os comprovantes das tarefas.",
  "本次历史导入已暂停，已保存的对话还在。":
    "Esta importação de histórico está pausada. As conversas salvas continuam disponíveis.",
  "导入等待处理，已保存的对话还在。":
    "A importação está aguardando processamento. As conversas salvas continuam disponíveis.",
  停止后续操作: "Parar ações restantes",
  暂停导入: "Pausar importação",
  继续导入: "Retomar importação",
  "配置模型，沉淀记忆": "Configure um modelo para criar memórias",
  导入与原文: "Importação e texto original",
  "本次导入已结束。已保存的对话无需重复导入。":
    "Esta importação foi concluída. As conversas salvas não precisam ser importadas de novo.",
  "对话先保存在本机。你可以同时设置后续连接。":
    "As conversas são salvas primeiro neste dispositivo. Enquanto isso, você pode configurar as conexões.",
  "导入不会自动开放应用读取权限。配置模型后，才会开始生成记忆。":
    "Importar não concede automaticamente acesso de leitura aos apps. As memórias só são criadas depois que você configura um modelo.",
  onboardingHistoryWindow:
    "Foi encontrado um grande volume de conversas. Para limitar o uso e manter as linhas do tempo dos apps alinhadas, esta importação inclui apenas os últimos 3 dias de todos os apps.",
  导入说明: "Informações da importação",
  正在导入你的会话: "Importando suas conversas",
  "后台将持续为你导入，可以放心进入下一步":
    "A importação continuará em segundo plano. Você pode seguir em frente.",
  未开始的导入: "Importações que não puderam ser iniciadas",
  "未能开始导入，其他已接受任务不受影响。":
    "Não foi possível iniciar a importação. As outras tarefas aceitas não foram afetadas.",
  查看未导入的应用: "Ver apps não importados",
  让记忆继续生长: "Faça suas memórias continuarem crescendo",
  连接你的应用: "Conecte seus apps",
  "适用于本次选择且支持的应用。":
    "Para os apps selecionados que permitem esta conexão.",
  一键接入应用: "Conectar apps em uma etapa",
  "安装记忆连接，让所选应用用上 MemHub 中的相关记忆。":
    "Instale as conexões de memória para que os apps selecionados possam usar as memórias relevantes do MemHub.",
  "当前使用独立测试数据目录，不会修改本机应用的连接配置。":
    "Esta instância usa um diretório de dados de teste isolado e não vai alterar as conexões dos apps locais.",
  "点击底部按钮后，后台分别接入各应用；某个应用失败不影响其他应用。":
    "Depois que você clicar no botão abaixo, os apps serão conectados individualmente em segundo plano; a falha de um não afeta os outros.",
  "暂时无法连接应用，当前不会安装连接。":
    "Não é possível conectar apps agora. Nenhuma conexão será instalada.",
  onboardingBatchStopped:
    "Ações restantes interrompidas; confira os comprovantes das ações já enviadas. ",
  onboardingBatchResult: "Confirmadas: {confirmed}; não confirmadas: {failed}.",
  "已确认项保留，请核对未确认任务后重试。":
    " As ações confirmadas foram mantidas. Confira as tarefas não confirmadas antes de tentar novamente.",
  本次应用导入进度: "Progresso da importação dos apps selecionados",
  正在导入: "Importando",
  已暂停: "Pausado",
  已中断: "Interrompido",
  导入遇到问题: "A importação encontrou um problema",
  导入完成: "Importação concluída",
  已取消: "Cancelado",
  已保存: "Salvo",
  onboardingAppProgress: "Progresso da importação de {name}",
  onboardingSavedMessages: "Mensagens salvas: {count}",
  onboardingScannedOutsideRange:
    "Itens verificados: {count}; nenhuma conversa no período",
  onboardingScannedNothingToSave:
    "Itens verificados: {count}; nenhuma conversa para salvar",
  onboardingScannedFilteringRecent:
    "Itens verificados: {count}; filtrando os últimos 3 dias",
  onboardingScannedFiltering: "Itens verificados: {count}; filtrando",
  onboardingScannedNotSaved: "Itens verificados: {count}; ainda não salvos",
  没有可保存的历史对话: "Nenhuma conversa do histórico para salvar",
  读取授权不可用: "Acesso de leitura indisponível",
  onboardingReadErrors: " · Erros de leitura: {count}",
  "配置详情，按需要调整": "Detalhes da configuração, se necessário",
  "连接未通过，配置已保留": "Falha na conexão; configuração mantida",
  "选好模型，让记忆开始沉淀": "Escolha um modelo para começar a criar memórias",
  "连接设置已保存\n成功项保留，不重复执行。":
    "Configurações de conexão salvas.\nAs etapas bem-sucedidas foram mantidas e não serão repetidas.",
  "已选择：持续同步 · 一键接入":
    "Selecionado: sincronização contínua · conexão em uma etapa",
  "沉淀记忆 · 使用你选择的模型": "Criar memórias · Com o modelo escolhido",
  "当前无法启用模型，已填写内容保留。":
    "Não é possível ativar o modelo agora. Seus dados foram mantidos.",
  "模型连接测试通过，可以启用并开始沉淀记忆。":
    "A conexão com o modelo foi aprovada. Você pode ativá-lo para criar memórias.",
  "先测试模型连通性；测试通过后再启用。":
    "Teste primeiro a conexão com o modelo; ative depois que o teste for aprovado.",
  "跳过，先进入应用": "Pular e ir para Apps",
  "正在测试模型…": "Testando modelo…",
  测试模型连通性: "Testar conexão com o modelo",
  "正在启用…": "Ativando…",
  确认启用: "Confirmar e ativar",
  核对提交状态: "Verificar status do envio",
  模型配置: "Configuração do modelo",
  模型参数: "Parâmetros do modelo",
  "选择服务商，填入 API Key。核对已有配置后再继续。":
    "Escolha um provedor e insira uma chave de API. Revise a configuração existente antes de continuar.",
  模型服务商: "Provedor do modelo",
  选择服务商: "Selecione um provedor",
  "已安全保存，留空保留": "Salva com segurança; deixe em branco para manter",
  "输入服务商提供的 API Key": "Insira a chave de API do seu provedor",
  "连接未通过，请检查 API Key、模型与接口地址后重试。配置已保留。":
    "Falha na conexão. Verifique a chave de API, o modelo e o endpoint e tente novamente. Sua configuração foi mantida.",
  "需要允许 MemHub 访问钥匙串。系统询问时请选择“始终允许”。":
    "Permita que o MemHub acesse as Chaves. Quando solicitado, escolha “Sempre Permitir”.",
  "留空保留已保存密钥；填写新密钥后需重新测试。":
    "Deixe em branco para manter a chave salva; teste novamente após inserir uma nova chave.",
  "密钥保存在系统安全存储中，不写入对话或日志。":
    "A chave fica no armazenamento seguro do sistema, não em conversas nem em logs.",
  配置详情与高级设置: "Detalhes da configuração e configurações avançadas",
  服务商待选择: "Provedor não selecionado",
  模型待填写: "Modelo não informado",
  本地向量模型: "Modelo de embeddings local",
  向量模型待填写: "Modelo de embeddings não informado",
  思考已关闭: "Raciocínio desativado",
  思考未关闭: "Raciocínio não desativado",
  "预设已填好，只有自定义服务或特殊需求才需要修改。":
    "As predefinições já estão preenchidas. Altere-as apenas para serviços personalizados ou necessidades especiais.",
  返回服务商选择: "Voltar à seleção de provedor",
  模型名称: "Nome do modelo",
  "Anthropic（暂不支持）": "Anthropic (ainda não compatível)",
  "HY 托管（当前不可用）": "HY gerenciado (indisponível no momento)",
  向量模型: "Modelo de embeddings",
  "本地 ONNX · 内置": "ONNX local · Integrado",
  "超时（秒）": "Tempo limite (segundos)",
  最大并发: "Simultaneidade máxima",
  上下文窗口: "Janela de contexto",
  "自动 / 可选": "Automático / opcional",
  关闭思考模式: "Desativar modo de raciocínio",
  "自定义向量配置可展开；远程向量服务须遵守凭据边界。":
    "Você pode expandir as configurações de embeddings personalizados. Serviços de embeddings remotos devem respeitar os limites das credenciais.",
  "切换服务商或模型后，需要重新测试并启用。":
    "Após trocar de provedor ou de modelo, teste e ative novamente.",
  向量模型名称: "Nome do modelo de embeddings",
  向量协议: "Protocolo de embeddings",
  "同源 OpenAI-compatible（共用 Key）":
    "Compatível com OpenAI na mesma origem (chave compartilhada)",
  "向量 Base URL": "Base URL de embeddings",
  "远程向量地址须与生成服务同源；本机地址仍需校验。":
    "Endpoints de embeddings remotos devem ter a mesma origem do serviço de geração; endpoints locais ainda precisam de validação.",
  "放弃旧记录，重新配置": "Descartar registro antigo e reconfigurar",
  模型提交状态: "Status do envio do modelo",
  "正在测试连接…": "Testando conexão…",
  "正在提交保存与启用，请等待回执…":
    "Salvando e ativando; aguardando confirmação…",
  "操作结果未确认，已填写内容保留，请核对后重试。":
    "Resultado não confirmado. Seus dados foram mantidos; verifique antes de tentar novamente.",
  本次沉淀: "Esta configuração de memórias",
  让记忆从这里开始: "Comece a criar memórias aqui",
  对话来源: "Fontes de conversas",
  尚无已选来源: "Nenhuma fonte selecionada",
  onboardingSourcesSummary: "{names} · Total de apps: {count}",
  onboardingImportedConversations:
    "Conversas importadas: {count}; as opções de cada fonte ainda controlam o que será sincronizado com este dispositivo depois.",
  "处理服务：": "Serviço de processamento:",
  待选择: "Não selecionado",
  "相关对话会发送给所选服务商。":
    "As conversas relevantes serão enviadas ao provedor escolhido.",
  "测试连接只发送测试内容。启用后会使用此模型整理已导入和之后同步的会话。":
    "Os testes de conexão enviam apenas conteúdo de teste. Após a ativação, este modelo processa as conversas importadas e as sincronizadas depois.",
  "当前不能在此启用模型。": "Não é possível ativar o modelo aqui agora.",
  "配置已修改，需要重新测试。": "A configuração mudou. Teste novamente.",
  onboardingIntegerRange: "Insira um número inteiro de {min} a {max}.",
  "请填写配置名称。": "Insira um nome para a configuração.",
  "请填写实际可用的模型 ID。": "Insira um ID de modelo disponível.",
  "请填写有效的模型接口地址。": "Insira uma URL de endpoint de modelo válida.",
  "请填写 API Key。": "Insira uma chave de API.",
  "请填写向量模型 ID。": "Insira um ID de modelo de embeddings.",
  "配置结果尚未确认，请在模型设置中核对后重试。":
    "O resultado da configuração não foi confirmado. Verifique nas configurações do modelo antes de tentar novamente.",
  "模型连接测试通过，可以启用。":
    "O teste de conexão com o modelo foi aprovado. Você pode ativá-lo.",
  "连接未通过，请核对模型、密钥和接收方。":
    "Falha na conexão. Verifique o modelo, a chave e o destinatário.",
  "配置或测试结果暂未确认，请核对后重试。":
    "Não foi possível confirmar a configuração ou o resultado do teste. Verifique antes de tentar novamente.",
  "索引更新没有完成，仍在使用原来的搜索模型。请检查连接后重试。":
    "A atualização do índice não foi concluída. O modelo de busca anterior continua em uso. Verifique a conexão e tente novamente.",
  "上次模型测试结果仍待核对，请稍后重试。":
    "O resultado do último teste do modelo ainda precisa ser verificado. Tente novamente mais tarde.",
  "模型写入暂时关闭。请等待索引维护完成后重试。":
    "As gravações do modelo estão temporariamente pausadas. Aguarde a conclusão da manutenção do índice e tente novamente.",
  "模型未能确认启用，请在设置中核对。":
    "Não foi possível confirmar que o modelo foi ativado. Verifique nas Configurações.",
  "已保存的模型配置暂时无法读取，不会重复创建。请重试刷新。":
    "A configuração de modelo salva está temporariamente indisponível. Nenhuma duplicata será criada. Tente atualizar novamente.",
  所选连接已启用: "As conexões dos apps selecionados estão ativadas",
  本次未选择应用连接: "Nenhuma conexão de app selecionada desta vez",
  尚未填写接收方: "Nenhum destinatário informado",
  本机内置向量模型: "Modelo de embeddings local integrado",
  "操作未确认，草稿已保留。":
    "Não foi possível confirmar a ação. Seu rascunho foi mantido.",
  "操作已完成，但配置列表暂未刷新。已保留操作回执，请刷新核对，不要重复保存。":
    "A operação foi concluída, mas a lista de configurações não foi atualizada. O comprovante foi mantido; atualize para revisar. Não salve novamente.",
  "暂时无法刷新模型配置，已确认的配置仍保留。请重试刷新。":
    "Não foi possível atualizar as configurações de modelo. As configurações confirmadas foram mantidas; tente atualizar de novo.",
  "未找到上次创建记录。确认放弃旧记录后可重新配置；系统不会自动重试。":
    "O registro da criação anterior não foi encontrado. Descarte-o para reconfigurar; não haverá nova tentativa automática.",
  "创建结果仍待核对，不会重复创建。请稍后再次核对。":
    "O resultado da criação ainda precisa de revisão. Nada será criado em duplicidade; verifique novamente mais tarde.",
  "已发现模型配置，旧创建记录仍保留。请先核对已有配置。":
    "Uma configuração de modelo foi encontrada. O registro de criação antigo foi mantido; revise primeiro a configuração existente.",
  "旧创建记录尚未安全解除，配置仍保持锁定。请重新核对。":
    "O registro de criação antigo não foi removido com segurança. A configuração continua bloqueada; revise novamente.",
  "配置未保存，请检查模型地址和参数后重试；已填写内容保留。":
    "A configuração não foi salva. Verifique o endpoint e os parâmetros do modelo antes de tentar novamente; seus dados foram mantidos.",
  "创建结果尚未确认，请核对已保存结果，不要重复提交。":
    "O resultado da criação não foi confirmado. Revise os resultados salvos; não envie novamente.",
  "模型配置已变化，旧测试结果不会用于启用，请重新核对配置。":
    "A configuração do modelo mudou. O resultado do teste antigo não será usado para ativá-la; revise a configuração novamente.",
  "暂时无法核对测试服务，本次未发送测试，请恢复连接后重试。":
    "Não foi possível verificar o serviço de teste. Nenhum teste foi enviado; restabeleça a conexão e tente novamente.",
  "另一个模型的测试结果尚未确认，请先在该模型上核对测试结果。":
    "O resultado do teste de outro modelo não foi confirmado. Revise primeiro o resultado desse modelo.",
  "测试结果尚未确认。再次点击测试连接将核对原操作，不会重复发送测试。":
    "O resultado do teste não foi confirmado. Clicar em Testar conexão novamente vai revisar a operação original sem enviar outro teste.",
  "请先点击测试连接，通过后再设为当前模型。":
    "Teste a conexão primeiro; defina como modelo atual só depois que o teste for aprovado.",
  "这是当前正在使用的模型，请先切换到别的模型再删除。":
    "Este modelo está em uso. Mude para outro modelo antes de excluí-lo.",
  "模型配置操作未成功，请核对输入后重试。":
    "A operação de configuração do modelo não foi concluída. Verifique os dados e tente novamente.",
  "它没有 MCP 通道，无法发放可撤销的记忆凭证":
    "Este app não tem um canal MCP para uma credencial de memória revogável",
  当前安装不支持一键插入:
    "A conexão com um clique não é compatível com esta instalação",
  桌面端的连接组件不可用:
    "O componente de conexão do desktop não está disponível",
  安装后没有拿到安装回执: "Nenhum comprovante de instalação foi retornado",
  记忆使用权限没有获批: "O acesso às memórias não foi aprovado",
  配置文件被改过: "O arquivo de configuração foi editado",
  "有无法确认的残留，需要人工核对":
    "Alguns resíduos precisam de revisão manual",
  权限存储暂不可用: "O armazenamento de permissões não está disponível",
  "请先把 MemHub 拖到“应用程序”文件夹，再接入应用":
    "Mova o MemHub para Aplicativos antes de conectar apps",
  本机运行时暂不可用: "O runtime local não está disponível",
  桌面端读取接入状态超时:
    "Tempo esgotado ao ler o status de conexão do desktop",
  "apps.apps": "Apps",
  "apps.allApps": "Todos os apps",
  "apps.home": "Início dos apps",
  "apps.back": "Voltar ao início",
  "apps.overview": "Visão geral das memórias",
  "apps.memories": "Memórias salvas",
  "apps.rawConversations": "Conversas originais",
  "apps.rawSaving": "Salvando",
  "apps.rawSavingCompact": "Salvando",
  "apps.rawSavingStatus": "Status de salvamento",
  "apps.rawSavingDetail":
    "As conversas originais são salvas sem um modelo generativo",
  "apps.rawNoExcerpt": "Esta conversa não tem texto do usuário para exibir",
  "apps.rawConversation": "Conversa original",
  "apps.rawSaved": "Conversa original salva",
  "apps.rawEmpty": "Ainda não há conversas originais salvas",
  "apps.rawCheckedEmpty":
    "Verificado: não há atualizações de conversas para mostrar",
  "apps.memoryCheckedEmpty":
    "Verificado: não há atualizações de memórias para mostrar",
  "apps.recentEmptyCompact": "Sem atualizações",
  "apps.rawWaiting": "As conversas originais salvas aparecerão aqui",
  "apps.checkedRecent": "Conteúdo atual verificado",
  "apps.currentMode": "Modo atual",
  "apps.mode.lite": "Memória básica (lite)",
  "apps.mode.pro": "Memória inteligente (pro)",
  "apps.mode.ultra": "Memória profunda (ultra)",
  "apps.readingCount": "Verificando memórias locais…",
  "apps.rawReadingCount": "Verificando conversas originais…",
  "apps.startingRetry":
    "O serviço em segundo plano está iniciando; isto será atualizado automaticamente",
  "apps.countWillUpdate":
    "A contagem de memórias será atualizada automaticamente",
  "apps.rawCountWillUpdate":
    "A contagem de conversas originais será atualizada automaticamente",
  "apps.showingPrevious":
    "Mostrando os dados anteriores; isto será atualizado automaticamente",
  "apps.unreadable": "Indisponível",
  "apps.unit": "itens",
  "apps.scope": "Usadas dentro das permissões dos apps",
  "apps.processing": "Status do processamento",
  "apps.running": "Processando",
  "apps.paused": "Pausado",
  "apps.stoppedNew": "Nenhuma memória nova está sendo criada",
  "apps.checkingProcessing": "Verificando status do processamento",
  "apps.checkingStatus": "Verificando",
  "apps.unchecked": "Precisa de revisão",
  "apps.organizingNew": "Processando novas conversas",
  "apps.recentUpdate": "Atualização recente",
  "apps.checkingRecent": "Verificando",
  "apps.recentWillUpdate":
    "A atualização mais recente aparecerá automaticamente",
  "apps.notGenerated": "Ainda não criada",
  "apps.saved": "Memória salva",
  "apps.separate": "O histórico e as memórias são processados separadamente",
  "apps.recentCall": "Chamada recente",
  "apps.activityUnavailable": "Atividade indisponível",
  "apps.firstBatch": "Primeiras memórias",
  "apps.callsUnavailable": "Não foi possível ler o histórico de chamadas",
  "apps.memoryUnavailable": "A visão geral das memórias ainda não está pronta",
  "apps.importing": "Importando conversas antes de criar memórias",
  "apps.firstRunning": "Criando as primeiras memórias",
  "apps.firstQueued": "Preparando as primeiras memórias",
  "apps.readingActivity": "Preparando visão geral das memórias",
  "apps.emptyMemories": "Nenhuma memória criada ainda",
  "apps.callReadFailure":
    "Você ainda pode ver as memórias salvas. Tentar novamente não fará uma nova chamada.",
  "apps.memoryReadFailure":
    "Isto será atualizado quando o serviço se recuperar. Você também pode tentar novamente agora.",
  "apps.savingConversations":
    "As conversas ainda estão sendo salvas em segundo plano.",
  "apps.waitingMemories":
    "O histórico foi salvo. As novas memórias aparecerão aqui.",
  "apps.reread": "Tentar novamente",
  "apps.viewCalls": "Ver histórico",
  "apps.viewMemory": "Ver memórias",
  "apps.backgroundDown":
    "O serviço em segundo plano não está em execução; os apps não podem consultar memórias agora",
  "apps.restart": "Reiniciar",
  "apps.runtime": "Status de execução",
  "apps.backgroundRunning": "Em execução em segundo plano",
  "apps.connectionUnchecked": "A conexão precisa de revisão",
  "apps.disconnected": "Serviço desconectado",
  "apps.authorizedOnly": "Processa apenas conteúdo autorizado",
  "apps.sync": "Sincronização de conversas",
  "apps.organizing": "Processamento de memórias",
  "apps.access": "Acesso dos apps",
  "apps.checkingAccess": "Verificação de conexão dos apps",
  "apps.checking": "Verificando…",
  "apps.recheck": "Verificar novamente",
  "apps.accessState": "Status de conexão dos apps",
  "apps.staleAccess":
    "Não foi possível ler o status de conexão dos apps; mostrando o último estado conhecido.",
  "apps.reconnect": "Reconectar",
  "apps.myApps": "Meus apps",
  "apps.list": "Lista de apps",
  "apps.syncEnabled": "Sincronização contínua ativada",
  "apps.recallAllowed": "Pode solicitar memórias autorizadas",
  "apps.historyAllowed": "Acesso ao histórico autorizado",
  "apps.connectionPending": "Conexão incompleta",
  "apps.details": "Ver detalhes",
  "apps.listUnavailable": "Lista de apps indisponível",
  "apps.emptyApps": "Ainda não há apps para exibir",
  "apps.checkingNow": "Verificando",
  "apps.checkingState": "Verificando status",
  "apps.notEnabled": "Não ativado",
  "apps.enableFailed": "Não foi possível ativar",
  "apps.unknownTime": "Horário desconhecido",
  "apps.today": "Hoje",
  "apps.newMemory": "Nova memória",
  "apps.updatedMemory": "Memória atualizada",
  "apps.unlinked": "Ainda não conectado",
  "apps.lastRead": "Última leitura: ",
  "apps.recentSync": "Última sincronização: {time}",
  "apps.connectedCount": "Apps conectados: {count}",
  "apps.connectedCount.one": "{count} app conectado",
  "apps.pendingCount": "Apps que precisam de revisão: {count}",
  "apps.pendingCount.one": "{count} app precisa de revisão",
  "apps.mixedCount": "Apps conectados: {count} · Revisão pendente: {pending}",
  "apps.mixedCount.one": "{count} app conectado · Revisão pendente: {pending}",
  "apps.unknownAccess":
    "O status de conexão dos apps não está disponível; não é possível confirmar quantos apps estão conectados.",
  "apps.unknownPermissions":
    "Não foi possível ler as permissões de memória de {names}; o status da conexão não foi confirmado.",
  "apps.enabledCount": "Apps ativados: {count}",
  "apps.enabledCount.one": "{count} app ativado",
  "apps.appCount": "Apps: {count}",
  "apps.appCount.one": "{count} app",
  "apps.viewAll": "Ver todos os apps ({count})",
  "apps.viewAll.one": "Ver o app",
  "apps.checkedAt": "Última verificação: {time}",
  "apps.revision": "Versão {count}",
  "apps.conversationTime": "Hora da conversa",
  "apps.importTime": "Hora da importação",
  "apps.writeTime": "Hora da escrita",
  "apps.updateTime": "Hora da atualização",
  "apps.incompleteCalls":
    " · Algumas chamadas não foram registradas; a lista pode estar incompleta",
  "apps.timeoutWarning":
    "{host} não conseguiu consultar memórias recentemente. O serviço em segundo plano pode estar ocupado e deve se recuperar automaticamente.",
  "apps.autoHostFailed":
    "Não foi possível conectar {name} automaticamente{reason}.",
  "apps.failureReason": ": {reason}",
  "apps.autoSyncFailed":
    "Não foi possível ativar automaticamente a sincronização contínua de {name}. {reason}",
  "apps.noAutoRetry": "Não haverá nova tentativa automática.",
  "apps.cardUnavailable":
    "{label} está temporariamente indisponível. Os outros recursos não foram afetados, e ele deve se recuperar automaticamente.",
  "apps.navMemories": "Memórias",
  "apps.navigation": "Navegação principal",
  "apps.skip": "Pular para o conteúdo principal",
  "apps.local": "Armazenado localmente",
  "apps.localHint":
    "As memórias ficam armazenadas neste dispositivo. O conteúdo relevante é enviado ao serviço quando você usa um modelo na nuvem ou um assistente autorizado.",
  "memory:记忆抽取": "Extração de memórias",
  "memory:关系归纳": "Síntese de relações",
  "memory:深度整理": "Processamento profundo",
  "memory:跨域整理": "Processamento entre domínios",
  "memory:当前模型已变化": "O modelo atual mudou",
  "memory:本地会话内容已变化": "O conteúdo da conversa local mudou",
  "memory:这段对话含过长原文，暂未整理；原文已保留":
    "Esta conversa contém um texto longo demais para ser processado; o original foi preservado",
  "memory:记忆库分页": "Páginas da biblioteca de memórias",
  "memory:上一页": "Página anterior",
  "memory:下一页": "Próxima página",
  "memory:等待可用生成模型": "Aguardando um modelo de geração disponível",
  "memory:还没有启用生成模型": "Nenhum modelo de geração ativado ainda",
  "memory:缺少生成模型 API Key":
    "A chave de API do modelo de geração está ausente",
  "memory:模型凭据暂不可用":
    "As credenciais do modelo estão temporariamente indisponíveis",
  "memory:本机凭据存储暂不可用":
    "O armazenamento local de credenciais está temporariamente indisponível",
  "memory:需要授权访问本机钥匙串。请重新打开 App 并完成一次授权":
    "O acesso ao chaveiro precisa de autorização. Abra o app novamente e aprove o acesso.",
  "memory:读取本机钥匙串超时。重新保存一遍模型配置即可恢复":
    "Tempo esgotado ao ler o chaveiro. Salve a configuração do modelo novamente para recuperar.",
  "memory:写入本机钥匙串超时。重新保存一遍模型配置即可恢复":
    "Tempo esgotado ao gravar no chaveiro. Salve a configuração do modelo novamente para recuperar.",
  "memory:模型认证失败。更新 key 后，到设置里点一次检测，会自动接着处理":
    "Falha na autenticação do modelo. Atualize a chave e execute uma verificação nas Configurações para retomar o processamento.",
  "memory:模型账户余额不足。充值或换 key 后，到设置里点一次检测，会自动接着处理":
    "Saldo insuficiente na conta do modelo. Recarregue ou troque a chave e execute uma verificação nas Configurações para retomar o processamento.",
  "memory:模型服务拒绝访问。更新权限后，到设置里点一次检测，会自动接着处理":
    "O serviço do modelo negou o acesso. Atualize as permissões e execute uma verificação nas Configurações para retomar o processamento.",
  "memory:模型服务限流": "Limite de solicitações do serviço do modelo atingido",
  "memory:模型服务暂不可用": "Serviço do modelo temporariamente indisponível",
  "memory:模型服务响应超时": "Tempo esgotado no serviço do modelo",
  "memory:模型请求不兼容，请核对配置":
    "A solicitação ao modelo é incompatível; verifique a configuração",
  "memory:模型请求未成功": "A solicitação ao modelo falhou",
  "memory:HY Memory 不可用": "HY Memory indisponível",
  "memory:HY Memory 启动失败": "Falha ao iniciar o HY Memory",
  "memory:暂时没能整理，稍后会自动重试":
    "Ainda não foi possível processar; nova tentativa automática em breve",
  "memory:这次没能整理成功，正在重试":
    "Não foi possível processar desta vez; tentando novamente",
  "memory:生成模型换过了，这批会重新整理":
    "O modelo de geração mudou; estes itens serão processados novamente",
  "memory:整理方式换过了，这批会重新整理":
    "As configurações de processamento mudaram; estes itens serão processados novamente",
  "memory:已提炼": "Extraída",
  "memory:旧版确认记录": "Registro de confirmação antigo",
  "memory:创建": "Criada",
  "memory:纠正": "Corrigir",
  "memory:调整使用范围": "Escopo de acesso alterado",
  "memory:撤销修改": "Alteração desfeita",
  "memory:模型更新": "Atualização do modelo",
  "memory:被新记忆取代": "Substituída por uma nova memória",
  "memory:其他操作": "Outra ação",
  "memory:删除（记录）": "Exclusão",
  "memory:修改已保存，但读取最新列表失败。已停止使用旧回答，请重新读取。":
    "Alteração salva, mas não foi possível carregar a lista mais recente. As respostas antigas não são mais usadas; recarregue a lista.",
  "memory:纠正已保存，但读取最新列表失败。已停止使用旧回答，请重新读取。":
    "Correção salva, mas não foi possível carregar a lista mais recente. As respostas antigas não são mais usadas; recarregue a lista.",
  "memory:未收到操作结果，请重试。":
    "Nenhum resultado foi recebido. Tente novamente.",
  "memory:批量处理结果已保留，但列表刷新失败，请重新读取记忆库。":
    "Os resultados da ação em lote foram mantidos, mas não foi possível atualizar a lista. Recarregue a biblioteca de memórias.",
  "memory:批量处理结果已保留，但列表刷新失败，稍后会自动重新读取。":
    "Os resultados da ação em lote foram mantidos, mas não foi possível atualizar a lista. Ela será recarregada automaticamente mais tarde.",
  "memory:召回范围已保存，但列表刷新失败。请稍后重新打开记忆页。":
    "Escopo de consulta salvo, mas não foi possível atualizar a lista. Abra Memórias novamente mais tarde.",
  "memory:原始对话暂时无法读取":
    "Não foi possível carregar as conversas originais",
  "memory:没有匹配的原始对话": "Nenhuma conversa original correspondente",
  "memory:还没有导入的原始对话": "Nenhuma conversa original importada ainda",
  "memory:查看已读内容": "Ver conteúdo salvo",
  "memory:已保存原文": "Texto original salvo",
  "memory:刷新已读内容": "Atualizar conteúdo salvo",
  "memory:正在核对本机已保存记录…": "Verificando os registros locais salvos…",
  "memory:原始消息": "Mensagens originais",
  "memory:会话": "Conversas",
  "memory:用户发言": "Mensagens do usuário",
  "memory:Agent 发言": "Mensagem do agente",
  "memory:作者未知": "Autor desconhecido",
  "memory:原始时间未知": "Horário original desconhecido",
  "memory:工具记录": "Registro de ferramenta",
  "memory:原始时间来自已保存记录。":
    "Os horários originais vêm dos registros salvos.",
  "memory:已保存的原文样例": "Exemplos de texto original salvos",
  "memory:最多展示 3 段，不代表全部历史或推荐记忆。":
    "Até 3 exemplos; não representam todo o histórico nem as memórias recomendadas.",
  "memory:查看完整对话": "Ver conversa completa",
  "memory:原文较长，此处仅展示前 600 个字符。":
    "Texto original longo; apenas os primeiros 600 caracteres são exibidos aqui.",
  "memory:这个来源还没有保存原文。":
    "Ainda não há texto original salvo desta fonte.",
  "memory:完整对话": "Conversa completa",
  "memory:关闭完整对话": "Fechar conversa completa",
  "memory:用户": "Usuário",
  "memory:暂时无法读取已保存内容，旧回执已收起。请检查授权或重试。":
    "Não foi possível ler o conteúdo salvo. O comprovante anterior foi fechado; verifique as permissões ou tente novamente.",
  "memory:这段原文暂不可读，可能已被撤回授权。请刷新回执。":
    "Este texto original não está disponível; o acesso pode ter sido revogado. Atualize o comprovante.",
  "memory:外部记忆召回范围": "Escopo da consulta externa de memórias",
  "memory:外部记忆召回": "Consulta externa de memórias",
  "memory:调整召回范围": "Ajustar escopo da consulta",
  "memory:所有关联工具": "Todas as ferramentas conectadas",
  "memory:已停止外部记忆召回": "Consulta externa de memórias interrompida",
  "memory:原有限定范围": "Escopo limitado anterior",
  "memory:选择召回范围": "Escolher escopo da consulta",
  "memory:停止外部记忆召回": "Interromper consulta externa de memórias",
  "memory:正在保存…": "Salvando…",
  "memory:保存召回范围": "Salvar escopo da consulta",
  "memory:取消范围修改": "Cancelar alterações no escopo",
  "memory:原文检索仍按来源授权；已带出的内容不会撤回。":
    "As pesquisas no texto original continuam seguindo as permissões das fontes; o conteúdo já fornecido não será recuperado.",
  "memory:记忆已更新，请重新打开这条记忆后再调整范围。":
    "A memória mudou. Abra-a novamente antes de ajustar o escopo.",
  "memory:范围修改未提交，当前范围保持不变。请修正后重试。":
    "As alterações no escopo não foram enviadas. O escopo atual não mudou; corrija o problema e tente novamente.",
  "memory:保存结果尚未确认，请重试。":
    "O resultado do salvamento não foi confirmado. Tente novamente.",
  "memory:原话已保存在本机；当前内容、模型接收方或授权版本尚未通过核对，未继续整理。":
    "O original está salvo neste dispositivo; o conteúdo, o destinatário do modelo ou a versão da permissão não passou na revisão, então o processamento não continuou.",
  "memory:HY 未返回可核对的已有记忆，未创建替代内容；原始记录已保留，可重试处理。":
    "O HY não retornou memórias existentes verificáveis, e nenhuma substituição foi criada. Os registros originais foram mantidos; você pode tentar processar novamente.",
  "memory:历史引用尚未绑定到原始来源，当前批次未完成，原话已保留。":
    "As citações do histórico ainda não estão vinculadas às fontes originais. Este lote está incompleto; o original foi mantido.",
  "memory:记忆内容未通过来源与作者核验，当前批次未完成，原话已保留。":
    "O conteúdo da memória não passou na verificação de fonte e autor. Este lote está incompleto; o original foi mantido.",
  "memory:所选记忆已不可读取，请重新读取记忆库。":
    "A memória selecionada não pode mais ser lida. Recarregue a biblioteca de memórias.",
  "memory:还没连上本地 Core，正在重试…":
    "O Core local ainda não está conectado; tentando novamente…",
  "memory:记忆内容或使用权限已变化，回答已停止。请重新提问。":
    "O conteúdo da memória ou as permissões de acesso mudaram, então a resposta foi interrompida. Pergunte novamente.",
  "memory:这条记忆的来源授权已撤回，暂时不能读取。":
    "O acesso à fonte desta memória foi revogado, então ela não pode ser lida agora.",
  "memory:记忆已被更新，请刷新后重试。":
    "Esta memória foi atualizada. Atualize e tente novamente.",
  "memory:已删除的记忆不能修改。": "Memórias excluídas não podem ser editadas.",
  "memory:这条记忆已经删除。": "Esta memória foi excluída.",
  "memory:这条记忆已被更新的记忆取代，只能查看或删除。":
    "Esta memória foi substituída por uma memória atualizada e só pode ser vista ou excluída.",
  "memory:没有更早的修改可以撤销。":
    "Não há alterações anteriores para desfazer.",
  "memory:来源原话已不存在，无法建立这条记忆。":
    "O texto original da fonte não existe mais, então esta memória não pode ser criada.",
  "memory:这次操作与已完成的请求不一致。":
    "Esta ação não corresponde à solicitação concluída.",
  "memory:输入内容不符合要求。":
    "Os dados informados não atendem aos requisitos.",
  "memory:本地记忆库正忙，请稍后重试。":
    "A biblioteca de memórias local está ocupada. Tente novamente mais tarde.",
  "memory:记忆": "Memórias",
  "memory:记忆空间": "Espaço de memória",
  "memory:全部应用": "Todos os apps",
  "memory:来源": "Fonte",
  "memory:主题": "Tema",
  "memory:归纳": "Síntese",
  "memory:全部记忆": "Todas as memórias",
  "memory:来源待核对": "Fonte precisa de revisão",
  "memory:所选来源待核对": "A fonte selecionada precisa de revisão",
  "memory:未识别来源": "Fonte não identificada",
  "memory:这个来源下还没有记忆": "Ainda não há memórias desta fonte",
  "memory:只在最相关的 {count} 条记忆里查找关键词":
    "As palavras-chave são procuradas apenas entre as memórias mais relevantes ({count})",
  "memory:未知来源": "Fonte desconhecida",
  "memory:记忆读取失败": "Não foi possível carregar as memórias",
  "memory:正在读取…": "Carregando…",
  "memory:正在更新…": "Atualizando…",
  "memory:读取失败": "Não foi possível carregar",
  "memory:正在核对来源…": "Verificando fontes…",
  "memory:正在导入历史会话，记忆将陆续出现":
    "Importando conversas anteriores; as memórias aparecerão aos poucos",
  "memory:记忆工作台": "Espaço de trabalho de memórias",
  "memory:来源与主题": "Fontes e temas",
  "memory:记忆或原始对话": "Memórias ou conversas originais",
  "memory:原始对话": "Conversas originais",
  "memory:地图组织方式": "Agrupamento do mapa",
  "memory:筛选地图记忆": "Filtrar memórias do mapa",
  "memory:搜索原始对话": "Pesquisar conversas originais",
  "memory:搜索记忆": "Pesquisar memórias",
  "memory:筛选记忆…": "Filtrar memórias…",
  "memory:向记忆提问": "Fazer uma pergunta às memórias",
  "memory:询问全部已授权可用记忆…":
    "Pergunte sobre todas as memórias autorizadas e disponíveis…",
  "memory:发送问题": "Enviar pergunta",
  "memory:刷新地图记忆": "Atualizar memórias do mapa",
  "memory:关闭新增记忆提示": "Dispensar aviso de nova memória",
  "memory:地图记忆列表": "Lista de memórias do mapa",
  "memory:记忆读取失败，请重试。":
    "Não foi possível carregar as memórias. Tente novamente.",
  "memory:重新读取地图记忆": "Recarregar memórias do mapa",
  "memory:没有匹配的记忆": "Nenhuma memória correspondente",
  "memory:本页没有匹配的主题记忆":
    "Nenhuma memória de tema correspondente nesta página",
  "memory:还没有已生成的记忆": "Nenhuma memória criada ainda",
  "memory:地图记忆分页": "Páginas de memórias do mapa",
  "memory:上一页记忆": "Página anterior de memórias",
  "memory:下一页记忆": "Próxima página de memórias",
  "memory:记忆关系图": "Grafo de memórias",
  "memory:记忆关联": "Conexões das memórias",
  "memory:刚导入的原话，还不是沉淀后的记忆":
    "Texto original recém-importado, ainda não transformado em memórias",
  "memory:来源归属": "Conexões com as fontes",
  "memory:可在沉淀记忆后查看视图": "Crie memórias para ver o grafo",
  "memory:正在读取记忆地图…": "Carregando mapa de memórias…",
  "memory:查看选中来源原文": "Ver texto da fonte selecionada",
  "memory:查看选中记忆原文": "Ver texto da memória selecionada",
  "memory:查看原文": "Ver conversa original",
  "memory:整理已有关系": "Criar conexões existentes",
  "memory:正在读取原文…": "Carregando conversa original…",
  "memory:选择一条原始对话": "Selecione uma conversa original",
  "memory:记忆暂时无法读取": "As memórias estão temporariamente indisponíveis",
  "memory:当前筛选没有可显示的记忆":
    "Nenhuma memória para exibir com este filtro",
  "memory:正在读取记忆…": "Carregando memórias…",
  "memory:已保存的对话还未生成记忆":
    "As conversas salvas ainda não viraram memórias",
  "memory:还没有可显示的记忆": "Ainda não há memórias para exibir",
  "memory:连线表示来源归属": "As linhas mostram as conexões com as fontes",
  "memory:记忆详情": "Detalhes da memória",
  "memory:来源原文": "Texto original da fonte",
  "memory:关联节点": "Nó relacionado",
  "memory:关闭记忆详情": "Fechar detalhes da memória",
  "memory:关闭详情": "Fechar detalhes",
  "memory:正在读取这条记忆…": "Carregando esta memória…",
  "memory:这条记忆的原文暂时无法读取，选择已保留。":
    "Não foi possível carregar o texto original desta memória. A seleção foi mantida.",
  "memory:重试读取原文": "Tentar carregar a conversa de novo",
  "memory:已沉淀的记忆": "Memórias criadas",
  "memory:正在读取记忆详情…": "Carregando detalhes da memória…",
  "memory:沉淀记忆后查看详情": "Crie memórias para ver os detalhes",
  "memory:时间未知": "Horário desconhecido",
  "memory:来源原文已不可读取": "O texto original da fonte não está disponível",
  "memory:关联记忆": "Memórias relacionadas",
  "memory:正在读取完整对话…": "Carregando conversa completa…",
  "memory:重试读取完整对话": "Tentar carregar a conversa completa de novo",
  "memory:看完整对话": "Ver conversa completa",
  "memory:今天": "Hoje",
  "memory:你": "Você",
  "memory:助手": "Assistente",
  "memory:工具": "Ferramenta",
  "memory:未知": "Desconhecido",
  "memory:作者待核对": "Autor precisa de revisão",
  "memory:你说": "Você disse",
  "memory:助手原话": "Mensagem original do assistente",
  "memory:已沉淀": "Criada",
  "memory:已删除": "Excluída",
  "memory:已被取代": "Substituída",
  "memory:状态待核对": "Status precisa de revisão",
  "memory:编辑": "Editar",
  "memory:原始依据": "Evidência original",
  "memory:这段原文当前不可查看。":
    "Este texto original não está disponível no momento.",
  "memory:版本记录": "Histórico de versões",
  "memory:更多管理": "Mais ações",
  "memory:撤销上次修改": "Desfazer última alteração",
  "memory:删除记忆": "Excluir memória",
  "memory:编辑记忆": "Editar memória",
  "memory:返回记忆": "Voltar à memória",
  "memory:修改这条记忆，原始对话保持不变。":
    "Edite esta memória; a conversa original não será alterada.",
  "memory:记忆内容": "Conteúdo da memória",
  "memory:使用范围保持不变": "O escopo de acesso não muda",
  "memory:已有新版本": "Existe uma versão mais recente",
  "memory:你的草稿已保留，先查看新版本再决定如何修改。":
    "Seu rascunho foi mantido. Revise a nova versão antes de fazer alterações.",
  "memory:查看新版本": "Ver nova versão",
  "memory:基于此版本继续编辑": "Continuar editando a partir desta versão",
  "memory:取消": "Cancelar",
  "memory:保存": "Salvar",
  "memory:重试": "Tentar novamente",
  "memory:删除": "Excluir",
  "memory:对话": "Conversa",
  "memory:工作": "Trabalho",
  "memory:编码": "Programação",
  "memory:保存修改": "Salvar alterações",
  "memory:对应记忆": "Memória relacionada",
  "memory:查看记忆": "Ver memórias",
  "memory:删除这条记忆？": "Excluir esta memória?",
  "memory:删除后不再用于新的召回，来源 App 中的原始对话不会删除。":
    "Ela não será mais usada em novas consultas. As conversas originais no app de origem não serão excluídas.",
  "memory:已经发送给应用的内容无法远程收回。":
    "Conteúdo já enviado aos apps não pode ser recuperado remotamente.",
  "memory:放弃尚未保存的修改？": "Descartar alterações não salvas?",
  "memory:来源权限已变化或原文不可用。":
    "As permissões da fonte mudaram ou o texto original não está disponível.",
  "memory:暂时无法读取新版本，草稿已保留。":
    "Não foi possível carregar a nova versão. Seu rascunho foi mantido.",
  "memory:修改未提交；草稿已保留。请按提示修正后重试。":
    "As alterações não foram enviadas; seu rascunho foi mantido. Corrija o problema e tente novamente.",
  "memory:修改尚未确认，草稿已保留。请先刷新核对，避免重复提交。":
    "As alterações não foram confirmadas; seu rascunho foi mantido. Atualize e verifique antes de tentar novamente para evitar um envio duplicado.",
  "memory:删除未提交。请核对这条记忆的状态后重试。":
    "A exclusão não foi enviada. Verifique o status desta memória antes de tentar novamente.",
  "memory:删除尚未确认。请重新核对记忆状态，不会自动扩大删除范围。":
    "A exclusão não foi confirmada. Verifique o status da memória; o escopo da exclusão não será ampliado automaticamente.",
  "memory:撤销未提交。请按提示修正后重试。":
    "A ação de desfazer não foi enviada. Corrija o problema e tente novamente.",
  "memory:撤销尚未确认，请先核对当前记忆版本，避免重复撤销。":
    "A ação de desfazer não foi confirmada. Verifique a versão atual da memória antes de tentar novamente para não desfazer duas vezes.",
  "memory:添加记忆": "Adicionar memória",
  "memory:关闭新增记忆": "Fechar janela de nova memória",
  "memory:关闭": "Fechar",
  "memory:记下这件事": "Anotar isto",
  "memory:例如：项目周会不排在周五。":
    "Por exemplo: não marcar a reunião do projeto às sextas-feiras.",
  "memory:整理模型：": "Modelo de processamento: ",
  "memory:尚未配置，原话仍可保存":
    "Não configurado; você ainda pode salvar o original",
  "memory:正在读取": "Carregando",
  "memory:配置模型": "Configurar modelo",
  "memory:保存中…": "Salvando…",
  "memory:保存原话": "Salvar original",
  "memory:最近添加": "Adicionadas recentemente",
  "memory:原话已保存，等待可用模型":
    "Original salvo; aguardando um modelo disponível",
  "memory:原话已保存，等待整理": "Original salvo; aguardando processamento",
  "memory:正在等待整理": "Aguardando processamento",
  "memory:正在整理": "Processando",
  "memory:原话已保存，整理未完成": "Original salvo; processamento incompleto",
  "memory:整理已完成": "Processamento concluído",
  "memory:原话已保存，未提取出可用记忆":
    "Original salvo; nenhuma memória utilizável foi extraída",
  "memory:最近添加的内容读取失败。":
    "Não foi possível ler as memórias adicionadas recentemente.",
  "memory:整理状态暂时不可用，原话已保存。":
    "O status do processamento está temporariamente indisponível; o original foi salvo.",
  "memory:已保存，正在后台沉淀记忆": "Salvo; criando memórias em segundo plano",
  "memory:未能确认保存结果，输入已保留。重试不会重复添加。":
    "Não foi possível confirmar o salvamento. Seu texto foi mantido; tentar novamente não vai adicionar uma duplicata.",
  "memory:模型服务要求检查余额或付款状态，原话仍已保存。":
    "O serviço do modelo exige uma verificação de saldo ou pagamento; o original continua salvo.",
  "memory:这台设备打不开 3D 视图，已显示关联列表。":
    "A visualização 3D não está disponível neste dispositivo. As conexões são exibidas em lista.",
  "memory:图谱工具": "Controles do grafo",
  "memory:放大": "Ampliar",
  "memory:放大图谱": "Ampliar o grafo",
  "memory:缩小": "Reduzir",
  "memory:缩小图谱": "Reduzir o grafo",
  "memory:重置视角": "Redefinir visualização",
  "memory:重置图谱视角": "Redefinir visualização do grafo",
  "memory:查看读取详情": "Ver detalhes da leitura",
  "memory:原文暂时无法读取": "Não foi possível carregar o texto original",
  "memory:暂时无法读取这段对话，已保留当前记忆。":
    "Não foi possível carregar esta conversa. A memória atual continua selecionada.",
  "memory:已清除筛选，定位到关联记忆。":
    "Filtros limpos para localizar a memória relacionada.",
  "memory:所选记忆不在当前筛选内，已取消选择。":
    "A memória selecionada não está neste filtro; a seleção foi limpa.",
  "memory:列表分隔符": ", ",
  "memory:冒号": ":",
  "memory:手动添加": "Adicionada manualmente",
  "memory:导入的已有记忆": "Memória existente importada",
  "memory:记忆与原文": "Memórias e texto original",
  "memory:第 {revision} 版": "Versão {revision}",
  "memory:对话时间 ": "Hora da conversa",
  "memory:沉淀时间 ": "Criada em",
  "memory:原对话 ": "Conversa original ",
  "memory:{count} 条记录没有原始时间，未用导入时间代替。":
    "Registros sem horário original: {count}; o horário da importação não foi usado no lugar.",
  "memory:作者数量按当前记录分类统计，不代表记忆抽取已完成或语义已核实。":
    "As contagens por autor refletem os registros salvos, não uma extração de memórias concluída nem um significado verificado.",
  "memory:统计于 ": "Estatísticas de ",
  "memory: · 导入数量仍在更新，可刷新核对":
    " · A contagem de importação ainda está sendo atualizada; atualize para verificar",
  "memory:对话于 {day}": "Conversa em {day}",
  "memory:导入于 {day}": "Importada em {day}",
  "memory:写入于 {day}": "Gravada em {day}",
  "memory:已开始处理所选对话": "Processando as conversas selecionadas",
  "memory:处理请求已受理": "Solicitação de processamento recebida",
  "memory:模型连接已通过。首批记忆生成后，会逐步出现在记忆列表里。":
    "Conexão com o modelo confirmada. As memórias aparecerão na lista conforme forem criadas.",
  "memory:已导入 {count} 段对话，整理成记忆后会出现在这里。":
    "Conversas importadas: {count}. As memórias aparecerão aqui após o processamento.",
  "memory:历史已保存在本机，记忆就绪后会出现在这里。":
    "O histórico está salvo neste dispositivo. As memórias aparecerão aqui quando estiverem prontas.",
  "memory:连接应用并导入历史后，可以开始沉淀记忆。":
    "Conecte um app e importe o histórico para começar a criar memórias.",
  "memory:当前已生成 0 条记忆。请求已受理不等于已经完成，原始对话、模型处理任务和记忆数量分别统计。":
    "Nenhuma memória criada ainda. Uma solicitação recebida não significa que foi concluída; conversas originais, tarefas de processamento e memórias são contadas separadamente.",
  "memory:返回应用": "Voltar aos apps",
  "memory:共 {count} 条 · 正在核对来源…":
    "Total: {count} · Verificando fontes…",
  "memory:共 {count} 条 · 正在核对来源….one":
    "Total: {count} · Verificando fontes…",
  "memory:共 {count} 条": "Total: {count}",
  "memory:共 {count} 条.one": "Total: {count}",
  "memory: · 正在核对…": " · Verificando…",
  "memory:新增 {count} 条记忆": "Novas memórias: {count}",
  "memory:新增 {count} 条记忆.one": "{count} nova memória",
  "memory:沉淀于 ": "Criada em ",
  "memory:共 {count} 条，第 {page} / {pages} 页":
    "Total: {count} · Página {page} de {pages}",
  "memory:共 {count} 条，第 {page} / {pages} 页.one":
    "Total: {count} · Página {page} de {pages}",
  "memory:已沉淀 {count} 条记忆": "Memórias criadas: {count}",
  "memory:已沉淀 {count} 条记忆.one": "{count} memória criada",
  "memory:关键词“{query}”": "Palavra-chave “{query}”",
  "memory:已沉淀 {count} 条{filter} · 当前展示 {shown} 条":
    "Memórias criadas: {count}{filter} · Exibindo {shown}",
  "memory:已沉淀 {count} 条{filter} · 当前展示 {shown} 条.one":
    "{count} memória criada{filter} · Exibindo {shown}",
  "memory:（{filter}）": " ({filter})",
  "memory:第 {page} 页": "Página {page}",
  "memory:正在等待整理：": "Aguardando processamento: ",
  "memory:记忆未在生成": "As memórias não estão sendo criadas",
  "memory:还没有开始生成记忆：{reason}（{count} 条待处理）":
    "As memórias não estão sendo criadas: {reason} (pendentes: {count})",
  "memory:去配置生成模型": "Configurar modelo de geração",
  "memory:模型暂停处理": "Modelo pausado",
  "memory:模型配置有问题（{reason}），记忆暂停处理，已有 {count} 条在排队":
    "Há um problema na configuração do modelo ({reason}). O processamento de memórias está pausado; {count} itens na fila",
  "memory:模型配置有问题（{reason}），记忆暂停处理，已有 {count} 条在排队.one":
    "Há um problema na configuração do modelo ({reason}). O processamento de memórias está pausado; {count} item na fila",
  "memory:API Key 无效或没有权限": "chave de API inválida ou sem permissão",
  "memory:找不到模型或地址": "modelo ou endereço não encontrado",
  "memory:请求过于频繁，被服务商限流":
    "muitas solicitações, limitadas pelo provedor",
  "memory:模型响应超时": "tempo de resposta do modelo esgotado",
  "memory:模型服务出错": "erro no serviço do modelo",
  "memory:无法连接模型服务": "não foi possível conectar ao serviço do modelo",
  "memory:连接模型服务失败": "falha na conexão com o serviço do modelo",
  "calls.directHost": "Chamada direta (diagnóstico)",
  "calls.headline.timedOut":
    "{host} não conseguiu consultar as memórias a tempo",
  "calls.headline.unavailable":
    "{host} não conseguiu consultar as memórias agora",
  "calls.headline.denied":
    "{host} não tinha autorização para consultar memórias",
  "calls.headline.empty": "{host} não encontrou memórias correspondentes",
  "calls.headline.found": "{host} encontrou itens relacionados: {count}",
  "calls.headline.found.one": "{host} encontrou {count} item relacionado",
  "calls.headline.rawOnly": "{host} encontrou {count} conversas originais",
  "calls.headline.rawOnly.one": "{host} encontrou {count} conversa original",
  "calls.headline.memoryOnly": "{host} encontrou {count} memórias",
  "calls.headline.memoryOnly.one": "{host} encontrou {count} memória",
  "calls.headline.mixed":
    "{host} encontrou {memories} memórias · {raw} conversas originais",
  "calls.headline.mixed.one":
    "{host} encontrou {memories} memória · {raw} conversas originais",
  "calls.headline.completed": "A chamada de {host} foi concluída",
  "calls.detail.timedOut":
    "As memórias estão sendo processadas. A consulta excedeu o tempo limite, então esta resposta não incluiu memórias.",
  "calls.detail.unavailable":
    "O MemHub está temporariamente indisponível, então esta resposta não incluiu memórias.",
  "calls.detail.denied": "As permissões atuais não permitem esta consulta.",
  "calls.detail.partialDelivered":
    "Fornecido a {host} · parte do conteúdo ainda está sendo processada",
  "calls.detail.partial": "Parte do conteúdo ainda está sendo processada",
  "calls.detail.delivered": "Fornecido a {host}",
  "calls.detail.notDelivered": "Não fornecido ao app",
  "calls.detail.empty": "Nenhuma memória correspondente desta vez",
  "calls.detail.success": "Consulta bem-sucedida",
  "calls.title": "Histórico de uso",
  "calls.query": "Esta consulta de memórias",
  "calls.backApps": "Voltar aos apps",
  "calls.backHistory": "Voltar ao histórico",
  "calls.filter": "Filtrar por app",
  "calls.allApps": "Todos os apps",
  "calls.recent": "Histórico recente",
  "calls.refresh": "Atualizar histórico",
  "calls.clearPage": "Limpar esta página",
  "calls.clearAll": "Limpar todo o histórico",
  "calls.clearAllShort": "Limpar tudo",
  "calls.loading": "Carregando histórico de uso",
  "calls.clearing": "Limpando o histórico selecionado",
  "calls.readFailed":
    "Não foi possível carregar o histórico. Atualize e tente novamente.",
  "calls.unavailable":
    "O histórico de uso não está disponível. Atualize mais tarde.",
  "calls.incomplete":
    "Algumas chamadas não foram registradas; a lista pode estar incompleta.",
  "calls.callDetail": "Detalhes da chamada",
  "calls.source": " · Fonte: {source}",
  "calls.stats":
    "Trechos originais encontrados: {raw} · Memórias: {memories} · {seconds} s",
  "calls.metadata": "Detalhes do registro",
  "calls.privacy":
    "Por privacidade, sua pergunta original e a resposta não são armazenadas aqui; apenas o horário, a fonte e o resultado são mantidos.",
  "calls.exactTime": "Horário exato",
  "calls.app": "Apps",
  "calls.type": "Tipo",
  "calls.result": "Status do resultado",
  "calls.tool": "Consultar memórias",
  "calls.notRecorded": "Não registrado",
  "calls.missingRow":
    "Este registro não está mais nesta página. Volte ao histórico para encontrá-lo.",
  "calls.list": "Histórico de chamadas",
  "calls.emptyPage": "Nenhum registro nesta página",
  "calls.empty": "Ainda não há histórico de uso",
  "calls.pagination": "Páginas do histórico",
  "calls.previous": "Página anterior",
  "calls.next": "Próxima página",
  "calls.policy": "Mantido por {days} dias, até {count} registros",
  "calls.confirmAllTitle": "Limpar todo o histórico de uso?",
  "calls.confirmPageTitle": "Limpar os registros desta página ({count})?",
  "calls.confirmPageTitle.one": "Limpar o registro desta página?",
  "calls.confirmAllDescription":
    "Exclui todos os registros de chamadas mantidos, não apenas esta página ou filtro. As conversas originais, as memórias, as permissões e as regras de retenção não mudam. Esta ação não pode ser desfeita.",
  "calls.confirmPageDescription":
    "Exclui apenas estes registros de chamadas. As conversas originais, as memórias, as permissões e as regras de retenção não mudam. Esta ação não pode ser desfeita.",
  "calls.confirmAll": "Confirmar limpeza total",
  "calls.confirmPage": "Confirmar limpeza",
  "calls.cancel": "Cancelar",
  "calls.clearedAll": "Todo o histórico foi limpo.",
  "calls.clearedPage": "O histórico selecionado foi limpo.",
  "calls.clearAllFailed":
    "Não foi possível limpar o histórico de uso. Os registros existentes não mudaram; verifique e tente novamente.",
  "calls.clearPageFailed":
    "Não foi possível limpar o histórico selecionado. Os registros existentes não mudaram; verifique e tente novamente.",
  "calls.clearUnknown":
    "Não foi possível confirmar o resultado. Confira o histórico recarregado; a limpeza não foi repetida automaticamente.",
  "calls.references": "Conteúdo relacionado",
  "calls.referencesLoading": "Carregando conteúdo relacionado",
  "calls.referencesFailed":
    "Não foi possível carregar o conteúdo relacionado. Atualize e tente novamente.",
  "calls.raw": "Conversa original",
  "calls.memory": "Memórias",
  "calls.changed": "Versão alterada",
  "calls.pending": "Precisa de revisão",
  "calls.notAvailable": "Indisponível",
  "calls.openRaw": "Ver conversa original",
  "calls.openMemory": "Ver memórias",
  "calls.noReferences": "Nenhum conteúdo relacionado encontrado",
  "calls.rawBody": "Texto da conversa original",
  "calls.rawLoading": "Carregando conversa original",
  "calls.rawFailed":
    "A conversa original não está disponível ou a versão dela mudou.",
  "calls.state.grounded": "Consulta bem-sucedida",
  "calls.state.empty": "Consulta bem-sucedida; nenhuma memória correspondente",
  "calls.state.partial":
    "Consulta bem-sucedida; parte do conteúdo ainda está sendo processada",
  "calls.state.timed_out":
    "A consulta excedeu o tempo limite; nenhuma memória fornecida",
  "calls.state.unavailable": "O MemHub está temporariamente indisponível",
  "calls.state.denied": "Esta consulta não está autorizada",
  "calls.return.timed_out":
    "O MemHub está importando e processando memórias. A consulta não terminou a tempo, então {host} respondeu sem memórias. Isso voltará ao normal quando o processamento for concluído.",
  "calls.return.unavailable":
    "O MemHub está temporariamente indisponível; esta resposta não incluiu memórias.",
  "calls.return.denied":
    "As permissões atuais não permitem esta consulta; nenhuma memória foi fornecida ao app.",
  "calls.return.partial": "Parte do conteúdo ainda está sendo processada.",
  "clock.unknown": "Horário desconhecido",
  "clock.today": "Hoje, {clock}",
  settings: "Configurações",
  languageTitle: "Idioma da interface",
  languageLabel: "Idioma",
  languageSystem: "Usar idioma do sistema",
  languageChinese: "Chinês simplificado",
  languageEnglish: "Inglês",
  languageSpanish: "Espanhol",
  languageDescription:
    "Escolha o idioma da interface. Esta configuração não altera suas conversas nem suas memórias.",
  languageSaveError:
    "Não foi possível salvar a configuração de idioma. Tente novamente.",
  languageLoadError:
    "Não foi possível carregar a configuração de idioma. Tente novamente.",
  nativeErrorTitle: "Não foi possível iniciar o MemHub",
  startupPreparing: "Iniciando e preparando o serviço de memória…",
  startupUpgradeHint:
    "A primeira abertura ou uma atualização dos dados de memória pode levar alguns minutos. O MemHub abrirá automaticamente quando terminar.",
  "migration.dialogMessage":
    "A migração de dados desta atualização do MemHub não foi concluída.",
  "migration.dialogDetail":
    "Para proteger seus dados, o MemHub não será iniciado. Anexe o log de migração a uma issue no GitHub e envie para nós. (Código de erro: {code})",
  "migration.dataRestored":
    "Seus dados foram restaurados ao estado anterior à atualização.",
  "migration.logPath": "Log: {path}",
  "migration.openLog": "Abrir log",
  "migration.quit": "Sair",
  "migration.issueHeading": "## Falha na migração da atualização",
  "migration.issueUpgrade":
    "- Ao atualizar o MemHub de {fromVersion} (build {fromBuild}) para {toVersion} (build {toBuild}), a migração de dados falhou.",
  "migration.issueCode": "- Código de erro: {code}",
  "migration.issueStep": "- Etapa com falha: {step}",
  "migration.issueRestored":
    "- Dados restaurados ao estado anterior à atualização: {restored}",
  "migration.issueSystem": "- Sistema: {system}",
  "migration.issueArch": " ({arch})",
  "migration.issueLog": "- Log de migração (local): {path}",
  "migration.issueAttach":
    "Arraste o arquivo de log acima para cá como anexo e depois envie.",
  "migration.unknown": "desconhecido",
  "migration.yes": "sim",
  "migration.no": "não",
};
