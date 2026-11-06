import type { MessageKey } from "../gui-i18n.js";
import { embeddingSwitchKoreanMessages } from "../gui-i18n-embedding-switch.js";
import { modeKoreanMessages } from "../gui-i18n-mode.js";

/** Korean (ko) GUI copy. Machine-assisted; native-speaker review recommended. */
export const koreanMessages: Record<MessageKey, string> = {
  ...modeKoreanMessages,
  ...embeddingSwitchKoreanMessages,
  "connectionFallback.title": "다른 도구 연결",
  "connectionFallback.desktopOnly":
    "로컬 페어링은 데스크톱 앱에서만 사용할 수 있습니다.",
  "connectionFallback.description":
    "목록에 없는 로컬 Harness용 일회용 페어링 Skill을 생성합니다. 브라우저 채팅, 원격 컨테이너, 다른 컴퓨터는 이 기기에 직접 연결할 수 없습니다.",
  "connectionFallback.name": "Harness 이름",
  "connectionFallback.namePlaceholder": "예: Kimi Code",
  "connectionFallback.generating": "생성 중…",
  "connectionFallback.generate": "페어링 Skill 생성",
  "connectionFallback.generateFailed": "페어링 Skill을 생성하지 못했습니다.",
  "connectionFallback.retry": "다시 시도",
  "connectionFallback.generated":
    "{name}용 일회용 페어링 Skill을 생성했습니다. {time}에 만료됩니다.",
  "connectionFallback.skillLabel": "페어링 Skill",
  "connectionFallback.copy": "Skill 복사",
  "connectionFallback.copied":
    "페어링 Skill을 복사했습니다. 해당 Harness에 붙여넣고 실행하세요.",
  "connectionFallback.copyFailed":
    "복사하지 못했습니다. 위의 텍스트를 선택해 직접 복사하세요.",
  "connectionFallback.localDescription":
    "로컬 MCP 또는 명령줄 도구 전용입니다. 브라우저 채팅은 이 기기에 직접 연결할 수 없습니다.",
  "connectionFallback.loading": "로컬 연결 정보를 불러오는 중…",
  "connectionFallback.loadFailed": "연결 정보를 불러오지 못했습니다.",
  "connectionFallback.format": "연결 정보 형식",
  "connectionFallback.promptTab": "연결 프롬프트",
  "connectionFallback.mcpTab": "MCP 구성",
  "connectionFallback.commandTab": "명령",
  "connectionFallback.materialLabel": "로컬 연결 정보",
  "connectionFallback.materialBoundary":
    "로컬 경로는 포함되지만 기억이나 키는 포함되지 않습니다. 붙여넣기 전에 확인하고 MemHub를 계속 실행해 두세요.",
  "connectionFallback.materialCopied":
    "복사했습니다. 아직 연결되지 않았습니다.",
  "connectionFallback.copyPrompt": "프롬프트 복사",
  "connectionFallback.copyConfig": "구성 복사",
  "connectionFallback.copyCommand": "명령 복사",
  "connectionFallback.promptTemplate":
    "다음 MCP 구성으로 이 로컬 Harness를 MemHub에 연결하세요.\n```json\n{mcp}```\n{shellBlock}\n연결한 후 실제 기억 조회를 한 번 실행하고 결과를 정확하게 보고하세요.",
  "connectionFallback.promptShell":
    "\n로컬 셸 명령만 지원되는 경우 다음을 사용하세요.\n```bash\n{shell}```\n",
  "sdk.memory.title": "SDK 기억 저장소",
  "sdk.memory.status": "기억 상태",
  "sdk.memory.status.active": "사용 가능",
  "sdk.memory.status.superseded": "대체됨",
  "sdk.memory.total": "기억 {count}개",
  "sdk.memory.total.one": "기억 {count}개",
  "sdk.memory.loadFailed":
    "기억 저장소를 불러오지 못했습니다. 다시 시도하세요.",
  "sdk.memory.detailLoadFailed": "기억 세부 정보를 불러오지 못했습니다.",
  "sdk.memory.updateFailed":
    "변경 사항을 저장하지 못했습니다. 다시 시도하세요.",
  "sdk.memory.deleteFailed": "기억을 삭제하지 못했습니다. 다시 시도하세요.",
  "sdk.memory.loading": "기억 저장소를 불러오는 중…",
  "sdk.memory.pagination": "SDK 기억 저장소 페이지",
  "sdk.memory.previous": "이전 페이지",
  "sdk.memory.next": "다음 페이지",
  "sdk.memory.detail": "SDK 기억 세부 정보",
  "sdk.memory.content": "기억 내용",
  "sdk.memory.save": "변경 사항 저장",
  "sdk.memory.delete": "기억 삭제",
  "sdk.memory.scene.chat": "채팅",
  "sdk.memory.scene.working": "업무",
  "sdk.memory.scene.coding": "코딩",
  "sdk.memory.task": "작업",
  "sdk.memory.solution": "해결 방법",
  "sdk.memory.page": "{page} / {pages} 페이지",
  "sdk.raw.title": "SDK 원본 대화",
  "sdk.raw.searchLabel": "원본 대화 검색",
  "sdk.raw.searching": "검색 중…",
  "sdk.raw.search": "검색",
  "sdk.raw.loadFailed": "원본 대화를 일시적으로 불러올 수 없습니다.",
  "sdk.raw.searchFailed": "원본 대화를 검색하지 못했습니다. 다시 시도하세요.",
  "sdk.raw.recent": "최근 원본 대화",
  "sdk.raw.empty":
    "아직 원본 대화가 없습니다. 소스를 연결하면 여기에 표시됩니다.",
  "sdk.raw.total": "원본 대화 {count}개",
  "sdk.raw.total.one": "원본 대화 {count}개",
  "memory:当前使用基础记忆。新对话会保存为原始记录，不会生成高级记忆；以前生成的高级记忆仍会保留，但当前不会显示或参与记忆召回。":
    "기본 메모리를 사용 중입니다. 새 대화는 원본 기록으로 저장되며 고급 메모리를 만들지 않습니다. 기존 고급 메모리는 보존되지만 현재 표시되거나 회상에 사용되지 않습니다.",
  "memory:当前使用智能记忆。新对话会由模型整理为高级记忆。":
    "스마트 메모리를 사용 중입니다. 모델이 새 대화를 고급 메모리로 정리합니다.",
  "memory:当前使用深度记忆。新对话会生成高级记忆，并由知识图谱和 evolve 继续整合。":
    "심층 메모리를 사용 중입니다. 새 대화에서 고급 메모리를 만들고 지식 그래프와 evolve가 계속 통합합니다.",
  "sdk.raw.found": "결과 {count}개",
  "sdk.raw.found.one": "결과 {count}개",
  "memoryInspector.emptyHint": "출처, 내용, 변경 기록을 확인하세요.",
  "memory.operationFailed": "기억 작업 실패({code})",
  "memorySpace.tabs.workbench": "워크벤치",
  "selectedMemory.status.confirmed": "생성됨",
  "selectedMemory.status.active": "활성",
  "selectedMemory.status.deleted": "삭제됨",
  "selectedMemory.status.superseded": "대체됨",
  "selectedMemory.status.shadow": "숨김",
  "selectedMemory.status.negated": "부정됨",
  "selectedMemory.status.conflicted": "충돌",
  "selectedMemory.status.archived": "보관됨",
  "selectedMemory.status.unknown": "상태 확인 필요",
  "selectedMemory.sourceUnknown": "출처 확인 필요",
  "selectedMemory.sourceUnattributed": "확인되지 않은 출처",
  "selectedMemory.discardDraft": "저장하지 않은 변경 사항을 버리시겠습니까?",
  "selectedMemory.newVersionReadFailed":
    "새 버전을 불러오지 못했습니다. 초안은 유지됩니다.",
  "selectedMemory.updateRejected":
    "변경 사항이 제출되지 않았습니다. 초안은 유지됩니다. 문제를 해결한 후 다시 시도하세요.",
  "selectedMemory.updateUnknown":
    "변경 사항이 확인되지 않았습니다. 초안은 유지됩니다. 다시 시도하기 전에 새로 고치세요.",
  "selectedMemory.deleteRejected":
    "삭제가 제출되지 않았습니다. 이 기억을 확인한 후 다시 시도하세요.",
  "selectedMemory.deleteUnknown":
    "삭제가 확인되지 않았습니다. 기억을 확인하세요. 삭제 범위는 확대되지 않습니다.",
  "selectedMemory.undoRejected":
    "실행 취소가 제출되지 않았습니다. 문제를 해결한 후 다시 시도하세요.",
  "selectedMemory.undoUnknown":
    "실행 취소가 확인되지 않았습니다. 다시 시도하기 전에 현재 버전을 확인하세요.",
  "selectedMemory.author.you": "내가 한 말",
  "selectedMemory.author.assistant": "어시스턴트 원문",
  "selectedMemory.author.unknown": "작성자 확인 필요",
  "selectedMemory.version": "버전 {revision}",
  "selectedMemory.sourceLine": "출처 {source} · {time}",
  "selectedMemory.timestamps": "대화 시간 {conversation} · 생성 {created}",
  "selectedMemory.writeTime": "작성 {created}",
  "selectedMemory.edit": "편집",
  "selectedMemory.evidence": "원본 근거",
  "selectedMemory.evidenceLine": "{author} · 원본 대화 {time}:",
  "selectedMemory.evidenceUnavailable": "이 원문은 현재 볼 수 없습니다.",
  "selectedMemory.viewOriginal": "원문 보기",
  "selectedMemory.history": "버전 기록",
  "selectedMemory.more": "추가 작업",
  "selectedMemory.undo": "마지막 변경 실행 취소",
  "selectedMemory.delete": "기억 삭제",
  "selectedMemory.editTitle": "기억 편집",
  "selectedMemory.originalTitle": "원본 대화",
  "selectedMemory.back": "기억으로 돌아가기",
  "selectedMemory.editDescription":
    "이 기억을 편집합니다. 원본 대화는 변경되지 않습니다.",
  "selectedMemory.content": "기억 내용",
  "selectedMemory.scopeUnchanged": "접근 범위는 변경되지 않습니다",
  "selectedMemory.conflictTitle": "최신 버전이 있습니다",
  "selectedMemory.conflictDescription":
    "초안은 유지됩니다. 편집하기 전에 새 버전을 확인하세요.",
  "selectedMemory.viewNewVersion": "새 버전 보기",
  "selectedMemory.continueFromVersion": "이 버전에서 계속 편집",
  "selectedMemory.cancel": "취소",
  "selectedMemory.save": "변경 사항 저장",
  "selectedMemory.author.user": "나",
  "selectedMemory.author.agent": "어시스턴트",
  "selectedMemory.author.tool": "도구",
  "selectedMemory.sourceUnavailable":
    "출처 권한이 변경되었거나 원문을 사용할 수 없습니다.",
  "selectedMemory.related": "관련 기억",
  "selectedMemory.viewMemory": "기억 보기",
  "selectedMemory.deleteTitle": "이 기억을 삭제하시겠습니까?",
  "selectedMemory.deleteDescription":
    "새 조회에 더 이상 사용되지 않습니다. 출처 앱의 대화는 삭제되지 않습니다.",
  "selectedMemory.sentCannotRecall":
    "이미 앱으로 보낸 내용은 원격으로 회수할 수 없습니다.",
  "memory:{count} 条": "{count}개",
  "memory:已可用 · {count} 段原文": "사용 가능 · 원문 발췌 {count}개",
  "memory:主题视角（非事项）": "주제 보기(사안 아님)",
  "memory:来源视角": "출처 보기",
  "memory:{count} 条记忆 · 当前已加载": "기억 {count}개 · 불러옴",
  "memory:最近 {count} 条": "최근 대화 {count}개",
  "memory:已确认": "확인됨",
  "memory:记忆库": "기억 저장소",
  "memory:问记忆": "기억에 묻기",
  "memory:历史对话": "대화 기록",
  "memory:项目记忆": "프로젝트 기억",
  "memory:编码偏好": "코딩 선호",
  "memory:长期模式": "장기 패턴",
  "memory:意图": "의도",
  "memory:近 3 个月": "최근 3개월",
  "memory:近 6 个月": "최근 6개월",
  "memory:近 1 年": "최근 1년",
  "memory:全部历史": "전체 기록",
  "memory:{count} 条这次没能整理成功，正在重试": "정리 재시도 · {count}",
  "memory:{count} 处理中": "정리 중: {count}",
  "memory: · 处理中 {running} / 待派发 {active}":
    " · 정리 중 {running} / 대기 {active}",
  "memory: · {count} 条任务状态待核对": " · 상태 확인이 필요한 작업 {count}개",
  "memory: · {count} 条任务状态异常，正在恢复":
    " · 상태 문제가 있는 작업 {count}개, 복구 중",
  "memory:{count} 等待": "대기 {count}",
  "memory:最新 {count} 条待处理": "최근 대기 항목 {count}개",
  "memory:历史积压 {count} 条": "밀린 항목 {count}개",
  "memory:（约 {count} 分钟）": " (약 {count}분)",
  "memory:（约 {count} 小时）": " (약 {count}시간)",
  "memory:（约 {count} 天）": " (약 {count}일)",
  "memory:同步尝试 {count} 次": "동기화 시도 {count}회",
  "memory:尚未尝试同步": "동기화를 시도하지 않음",
  "memory: · 状态更新于 {date}": " · {date} 업데이트됨",
  "memory:HY Memory 当前没有这条记忆，仅重试同步不能补齐。":
    "HY Memory에 이 기억이 없습니다. 동기화를 다시 시도해도 복원할 수 없습니다.",
  "memory:同步需要可用的本机整理模型或本地嵌入授权。":
    "동기화하려면 사용 가능한 로컬 정리 모델 또는 로컬 임베딩 사용 권한이 필요합니다.",
  "memory:这条记忆或模型版本已经变化，先刷新核对再决定下一步。":
    "이 기억 또는 모델 버전이 변경되었습니다. 새로 고쳐 확인한 후 다음 작업을 결정하세요.",
  "memory:这条记忆已改，搜索索引还没开始同步。":
    "이 기억이 변경되었지만 검색 색인 동기화가 아직 시작되지 않았습니다.",
  "memory:最近一次同步未成功；可以先重试，结果仍不确定时再刷新核对。":
    "마지막 동기화에 실패했습니다. 먼저 다시 시도하고, 결과가 여전히 불확실하면 새로 고쳐 확인하세요.",
  "memory:这条记忆已改，搜索索引还没跟上。":
    "이 기억이 변경되었지만 검색 색인에 아직 반영되지 않았습니다.",
  "memory:搜索里还是改之前那句话。": "검색에 아직 수정 전 텍스트가 표시됩니다.",
  "memory:重试同步": "동기화 다시 시도",
  "memory:配置整理模型": "정리 모델 구성",
  "memory:未提炼 · 在窗口外": "추출 안 됨 · 범위 밖",
  "memory:未提炼 · 排队中": "추출 안 됨 · 대기 중",
  "wb.select": "선택",
  "memory:模型综合": "모델 종합",
  "memory:原文摘录": "원문 발췌",
  "memory:没有命中": "일치 항목 없음",
  "memory:正在生成": "생성 중",
  "memory:正在检索记忆": "기억 조회 중",
  "memory: · 基于 {count} 条已确认记忆": " · 확인된 기억 {count}개 기반",
  "memory: · 基于 {count} 条已确认记忆.one": " · 확인된 기억 {count}개 기반",
  "memory:整理需要处理": "정리 작업 확인 필요",
  "memory:正在整理记忆": "기억 정리 중",
  "memory:整理状态": "정리 상태",
  "memory:自动提炼范围": "자동 추출 범위",
  "memory:手动提炼下一批": "다음 묶음 수동 추출",
  "memory:刷新核对": "새로 고쳐 확인",
  "memory:正在导入历史会话，记忆库暂时只显示一部分；导入完成后会自动补全，不需要重试。":
    "대화 기록을 가져오는 중입니다. 일부 기억만 표시되며, 가져오기가 끝나면 목록이 자동으로 채워집니다.",
  "memory:选择：{content}": "선택: {content}",
  "memory:处理失败：{content}": "정리 실패: {content}",
  "memory:已选 {count} 条": "{count}개 선택됨",
  "memory:已选 {count} 条.one": "{count}개 선택됨",
  "memory:删除所选 {count} 条": "선택한 {count}개 삭제",
  "memory:删除所选 {count} 条.one": "선택한 {count}개 삭제",
  "memory:重试失败的 {count} 条": "실패한 {count}개 다시 시도",
  "memory:重试失败的 {count} 条.one": "실패한 {count}개 다시 시도",
  "memory:没有{status}的记忆。": "{status} 상태인 기억이 없습니다.",
  "memory:已{action} {count} 条": "작업 완료({action}): {count}개",
  "memory:已{action} {count} 条.one": "작업 완료({action}): {count}개",
  "memory:，{count} 条未完成": ", {count}개 미완료",
  "memory:，{count} 条未完成.one": ", {count}개 미완료",
  "memory:未能{action}：{error}": "{action}하지 못했습니다: {error}",
  "memory:重试{action}": "{action} 다시 시도",
  "memory:删除（操作）": "삭제",
  "memory:处理（操作）": "정리",
  "memory:{count} 条原话": "원문 발췌 {count}개",
  "memory:{count} 条原话.one": "원문 발췌 {count}개",
  "memory:{count} 次整理已完成": "정리 완료 {count}회",
  "memory:{count} 次整理已完成.one": "정리 완료 {count}회",
  "memory: · {count} 条在窗口外（原文可搜）":
    " · 범위 밖 {count}개(원문 검색 가능)",
  "memory: · {count} 条在窗口外（原文可搜）.one":
    " · 범위 밖 {count}개(원문 검색 가능)",
  "memory: · 已规划 {planned} 批，当前第 {current} 批":
    " · {planned}개 묶음 계획됨, 현재 {current}번째 묶음",
  "memory:{kind}卡在 {reason}（{count} 条）":
    "{kind}: {reason}(으)로 막힘({count}개)",
  "memory:已加载 {count} 条记忆": "기억 {count}개 불러옴",
  "memory:已加载 {count} 条记忆.one": "기억 {count}개 불러옴",
  "memory:回答只使用已确认记忆。每个答案都可以回到对应记忆和来源原话。":
    "답변에는 확인된 기억만 사용됩니다. 각 답변에서 해당 기억과 원문으로 이동할 수 있습니다.",
  "memory:选择一条记忆": "기억 선택",
  "memory:查看它的来源、使用范围和修改历史。":
    "출처, 접근 범위, 변경 기록을 확인하세요.",
  "memory:查看替代它的记忆": "이 기억을 대체한 기억 보기",
  "memory:查看它取代的旧记忆": "이 기억이 대체한 기억 보기",
  "memory:保存纠正": "수정 저장",
  "memory:修改历史": "변경 기록",
  "memory:已可用": "사용 가능",
  "memory:处理中…": "정리 중…",
  "memory:搜索": "검색",
  "memory:记忆治理状态": "기억 상태",
  "memory:记忆空间模式": "기억 공간 모드",
  "memory:来源状态待核对，记忆库暂不可读取。":
    "출처 상태를 확인해야 합니다. 아직 기억 저장소를 읽을 수 없습니다.",
  "memory:记忆库读取失败，请重试。":
    "기억 저장소를 불러오지 못했습니다. 다시 시도하세요.",
  "memory:重新读取记忆库": "기억 저장소 다시 불러오기",
  "memory:正在读取记忆库…": "기억 저장소를 불러오는 중…",
  "memory:批量操作结果": "일괄 작업 결과",
  "memory:批量操作": "일괄 작업",
  "memory:取消选择": "선택 해제",
  "memory:向已确认记忆提问": "확인된 기억에 묻기",
  "memory:搜索记忆库": "기억 저장소 검색",
  "memory:问答记忆范围": "기억 질문 범위",
  "memory:只问个人画像": "개인 프로필만 묻기",
  "memory:问全部记忆（含项目）": "모든 기억에 묻기(프로젝트 포함)",
  "memory:例如：我对真实数据有什么要求？":
    "예: 실제 데이터에 대한 내 요구 사항은 무엇인가요?",
  "memory:搜索偏好、决定或经历": "선호, 결정, 경험 검색",
  "memory:根据记忆回答": "기억으로 답변",
  "memory:清除搜索": "검색 지우기",
  "wb.finding.duplicate": "같은 원문 메시지에서 추출됨",
  "wb.finding.duplicateText":
    "“{first}”와(과) “{second}”는 같은 원문 메시지에서 추출되어 각각 {firstScope}와(과) {secondScope}에 배정되었습니다. 같은 내용을 가리키는지 확인하세요.",
  "wb.finding.dormant": "최근 업데이트 없음",
  "wb.finding.dormantText":
    "기억 {count}개가 두 달 동안 변경되거나 언급되지 않았습니다. 검토가 필요할 수 있습니다.",
  "wb.finding.blocked": "요건 대기 중",
  "wb.finding.blockedText":
    "대화 {count}개가 요건 충족을 기다리며 대기 중입니다. 원문은 이미 저장되어 검색할 수 있습니다.",
  "wb.finding.blockedText.one":
    "대화 {count}개가 요건 충족을 기다리며 대기 중입니다. 원문은 이미 저장되어 검색할 수 있습니다.",
  "wb.unclassified": "미분류",
  "wb.profile": "개인 프로필",
  "wb.project": "프로젝트 {id}",
  "wb.topics": "주제",
  "wb.all": "전체",
  "wb.topicCount": "주제 {count}개 형성됨",
  "wb.topicCount.one": "주제 {count}개 형성됨",
  "wb.singletons": "; 한 번만 나타난 다른 태그 {count}개",
  "wb.singletons.one": "; 한 번만 나타난 다른 태그 {count}개",
  "wb.memories": "기억 {count}개",
  "wb.memories.one": "기억 {count}개",
  "wb.sources": "출처 {count}개",
  "wb.sources.one": "출처 {count}개",
  "wb.once":
    "이 주제는 여기에만 나타납니다. 일회성일 수도 있고, 같은 주제가 다른 이름으로 나뉘었을 수도 있습니다.",
  "wb.graphTitle": "{label} 관계 그래프",
  "wb.readSummary": "이 사안의 요약 읽기",
  "wb.summaryPrompt":
    "“{label}”에 대해 명확히 설명하세요: 현재 진행 상황, 결정된 사항, 아직 정해지지 않은 사항.",
  "wb.building": "종합 중…",
  "wb.build": "HY Memory로 관계 생성",
  "wb.drawn": "기억 {shown} / {total}개 표시 중",
  "wb.askLabel": "이 사안에 대해 묻기",
  "wb.askPlaceholder":
    "이 사안에 대해 물어보세요. 답변에는 확인된 기억만 사용됩니다",
  "wb.ask": "묻기",
  "wb.originals": "원본 대화",
  "wb.evidenceNote":
    "이 기억은 아래 원문 발췌 {count}개에서 나왔으며 시간순으로 정렬되어 있습니다",
  "wb.evidenceNote.one":
    "이 기억은 아래 원문 발췌 {count}개에서 나왔으며 시간순으로 정렬되어 있습니다",
  "wb.pickCircle": "그래프에서 원을 선택하면 여기에 원문이 표시됩니다",
  "wb.unavailable": "출처 원문을 사용할 수 없습니다",
  "wb.fullConversation": "전체 대화 보기",
  "wb.you": "나",
  "wb.assistant": "어시스턴트",
  "wb.noMemories":
    "아직 기억이 없습니다. 가져온 원본 대화는 대화 기록에서 검색할 수 있습니다.",
  "wb.waiting":
    "일부 대화가 요건 충족을 기다리며 대기 중입니다. 원문은 저장되어 검색할 수 있습니다.",
  "wb.conditions": "부족한 항목 보기",
  "wb.findings": "기록에서 발견한 사항",
  "wb.findingsNote":
    "로컬 기록을 바탕으로 계산했습니다. 항목을 선택하면 근거가 되는 기억이 그래프에 강조 표시됩니다.",
  "wb.showGraph": "그래프에서 보기",
  "wb.loading": "기억을 불러오는 중…",
  "wb.coreStarting":
    "로컬 Core가 시작되면 자동으로 표시됩니다. 새로 고칠 필요가 없습니다.",
  "wb.nothingToConnect": "아직 연결할 항목이 없습니다.",
  "wb.skeletonNote":
    "출처를 승인하면 출처와 주제 구조가 표시되며, 기억은 생성되는 대로 나타납니다.",
  "wb.authorize": "출처 승인",
  "wb.conversations": "대화 {count}개",
  "wb.conversations.one": "대화 {count}개",
  "wb.chipChanges": "최근 여기에서 무엇이 바뀌었나요?",
  "wb.chipSummarize": "몇 가지 요점으로 요약해 주세요",
  "wb.unknownTime": "시간 알 수 없음",
  "wb.unknownSource": "알 수 없는 출처",
  "wb.transcriptGone": "이 대화는 더 이상 저장소에 없습니다.",
  "wb.edgeCount": "{label}: 연결 {count}개",
  "wb.edgeCount.one": "{label}: 연결 {count}개",
  "wb.noRelations":
    "HY Memory가 아직 관계를 찾지 못했습니다. 위의 버튼을 사용해 System 2를 실행하세요.",
  "wb.noModelRelations":
    "사용 가능한 모델이 구성되지 않아 HY Memory 관계를 사용할 수 없습니다.",
  "provenance.multiple": "여러 작성자",
  "provenance.person": "사용자 원문 메시지",
  "provenance.agent": "에이전트 원문 메시지",
  "provenance.unknown": "알 수 없는 작성자",
  "provenance.imported": "가져온 기억 · {author}",
  "provenance.manual": "수동으로 추가됨",
  "provenance.answered": "답변에서 생성됨",
  "provenance.generated": "AI 응답에서 생성됨",
  "provenance.conversation": "원본 대화에서 생성됨",
  "arrival.region": "기억 정리 상태",
  "arrival.importing": "원문을 아직 가져오는 중입니다",
  "arrival.messages": "메시지 {count}개 저장됨",
  "arrival.messages.one": "메시지 {count}개 저장됨",
  "arrival.conversations": "대화 {count}개",
  "arrival.conversations.one": "대화 {count}개",
  "arrival.details": "진행 상황 및 세부 정보 보기",
  "arrival.backlog":
    "기존 데이터가 많아 품질을 위해 백그라운드에서 묶음 단위로 정리하고 있습니다",
  "arrival.items": "{count}개 항목",
  "arrival.items.one": "{count}개 항목",
  "arrival.configure": "기억 정리 설정",
  "arrival.status": "정리 상태 보기",
  "arrival.history": "저장된 대화 보기",
  "arrival.connect": "앱 연결",
  "arrival.retained": "저장된 원문은 이 기기에 유지됩니다.",
  "arrival.description.model-required": "계속하기 전에 정리 모델을 확인하세요.",
  "arrival.description.processing":
    "최근 대화를 먼저 정리하고 있습니다. 더 많은 기억이 점차 나타납니다",
  "arrival.description.importing":
    "원문을 저장하는 중입니다. 가져오기 진행률은 기억 생성 진행률과 다릅니다.",
  "arrival.description.failed":
    "다시 시도할지 결정하기 전에 실패 원인을 확인하세요.",
  "arrival.description.unavailable":
    "마지막으로 확인된 정보가 유지됩니다. 현재 진행 상황을 다시 확인하세요.",
  "arrival.description.refreshing":
    "로컬 기록을 확인하는 중입니다. 확인되지 않은 진행 상황은 집계되지 않습니다.",
  "arrival.description.checking":
    "저장된 내용은 유지됩니다. 진행 상황을 업데이트해야 합니다.",
  "arrival.description.stopped":
    "정리가 중지되었습니다. 계속하기 전에 상태를 확인하세요.",
  "arrival.description.idle": "현재 실행 중인 정리 작업이 없습니다.",
  "arrival.description.empty":
    "앱을 연결하거나 대화 파일을 가져오세요. 원문은 먼저 이 기기에 저장됩니다.",
  "arrival.title.refreshing": "저장된 내용은 유지됩니다. 진행 상황 확인 중",
  "arrival.title.checking": "진행 상황 업데이트 필요",
  "arrival.title.importing": "대화 가져오는 중",
  "arrival.title.processing": "기억 생성 중",
  "arrival.title.continue": "확인 필요",
  "arrival.title.model": "정리 모델이 구성되지 않음",
  "arrival.title.empty": "저장된 대화가 아직 없습니다",
  "handoff.region": "다른 앱으로 기억 가져가기",
  "handoff.answer": "답변과 참조를 다른 앱으로 가져가기",
  "handoff.selected": "선택한 기억을 다른 앱으로 가져가기",
  "handoff.title": "다른 앱으로 가져가기",
  "handoff.closeTitle": "미리 보기 닫기",
  "handoff.close": "전달 미리 보기 닫기",
  "handoff.stats": "기억 {count}개 · {characters}자 · 생략 없음",
  "handoff.stats.one": "기억 {count}개 · {characters}자 · 생략 없음",
  "handoff.content": "다른 앱으로 가져갈 전체 내용",
  "handoff.target": "대상 앱",
  "handoff.other": "기타 앱 · 복사만",
  "handoff.copyOnly": " · 복사만",
  "handoff.boundary":
    "위의 내용만 포함되며 원본 대화는 포함되지 않습니다. 복사는 연결을 승인하는 것이 아니며, 다른 앱에 있는 사본은 회수할 수 없습니다.",
  "handoff.copy": "내용 복사",
  "handoff.open": "복사 후 {host} 열기",
  "handoff.copyFailed":
    "복사하지 못해 앱을 열지 않았습니다. 미리 보기에서 텍스트를 선택해 직접 복사하세요.",
  "handoff.opened":
    "복사한 후 {host} 열기를 요청했습니다. 전송된 내용은 없습니다. 대상 입력란을 확인하세요.",
  "handoff.notPrefilled":
    "전체를 복사했습니다. {host}에 미리 채우지 못했으니 대상 앱에 붙여넣으세요. 전송된 내용은 없습니다.",
  "handoff.openFailed":
    "전체를 복사했지만 {host}을(를) 열지 못했습니다. 전송된 내용은 없습니다.",
  "handoff.copied": "{target} 전체를 복사했습니다. 전송된 내용은 없습니다.",
  "handoff.pasteTarget": ", {host}에 붙여넣을 수 있습니다",
  "wb.memory": "이 기억",
  "wb.node": "이 노드",
  "wb.matter": "이 사안",
  "wb.available": "사용 가능",
  "wb.superseded": "대체됨",
  "wb.deleted": "삭제됨",
  "wb.revision": "버전 {revision}",
  "wb.collapse": "접기",
  "wb.oldSearch": "검색에 아직 수정 전 텍스트가 표시됩니다.",
  "wb.missingIndex":
    "HY Memory에 이 기억이 없습니다. 다시 시도해도 복원할 수 없습니다.",
  "wb.retrySync": "동기화 다시 시도",
  "wb.correctForm": "기억 내용 수정",
  "wb.content": "기억 내용",
  "wb.correct": "수정",
  "wb.saveCorrection": "수정 저장",
  "wb.cancelEdit": "편집 취소",
  "wb.undo": "마지막 변경 실행 취소",
  "wb.delete": "삭제",
  "wb.nodeNote":
    "이 항목은 기억이 아니며, 기억이 연결되는 대상입니다. 직접 연결 {direct}개, 두 단계로 연결 {indirect}개.",
  "wb.direct": "직접",
  "wb.twoHops": "두 단계",
  "wb.idle":
    "원은 기억을, 선은 같은 대화에서 언급된 기억을 나타냅니다. 하나를 선택하면 출처를 확인하고 편집할 수 있습니다. 왼쪽에서 다른 사안을 선택하면 그래프가 업데이트됩니다.",
  "wb.searching": "기억 검색 중…",
  "wb.generated": "모델 종합",
  "wb.extractive": "원문 발췌",
  "wb.noHit": "일치 항목 없음",
  "wb.answerSource": "{mode} · 확인된 기억 {count}개 기반",
  "wb.answerSource.one": "{mode} · 확인된 기억 {count}개 기반",
  "wb.recalled": "기억 {count}개 검색됨",
  "wb.recalled.one": "기억 {count}개 검색됨",
  "wb.saveMemory": "기억으로 저장",
  "wb.tooLong":
    "이 답변은 현재 저장 한도를 초과하여 기억으로 저장할 수 없습니다.",
  "wb.summary":
    "이것은 기억이 아니라 요약입니다. 아래 기억과 일치하도록 매번 다시 생성됩니다.",
  "wb.thisAnswer": "이 답변",
  "wb.type.source": "출처 앱",
  "wb.type.topic": "주제",
  "wb.type.schema": "HY 종합",
  "wb.type.memory": "기억",
  "wb.origin.hy_relation": "HY Memory 종합",
  "wb.origin.same_conversation": "같은 대화",
  "wb.origin.shared_tag": "같은 주제",
  "wb.origin.provenance": "원문 출처",
  "wb.relation.evidence_source": "출처",
  "wb.relation.same_conversation": "같은 대화",
  "wb.relation.shared_tag": "같은 주제",
  "wb.relation.derived_from": "종합 출처",
  "wb.relation.tagged_with": "태그",
  "wb.relation.related_to": "관련",
  guideEyebrow: "체험하기",
  guideTitle: "지난 대화에 이어서 계속하세요",
  guideBack: "기억으로 돌아가기",
  guideChooseScene: "시나리오 선택",
  guideSame: "새 대화에서 이어가기",
  guideSameDescription: "같은 앱의 새 대화에서 이전 결정을 찾아보세요.",
  guideSwitch: "다른 앱에서 이어가기",
  guideSwitchDescription: "같은 작업, 다른 AI. 먼저 관련 맥락을 가져오세요.",
  guideNew: "새 도구가 나를 이해하도록 돕기",
  guideNewDescription:
    "자기소개를 다시 하는 대신 직접 고른 정보 하나로 시작하세요.",
  guideReadError: "지금은 기억을 읽을 수 없습니다. 나중에 다시 시도하세요.",
  guideDetailError:
    "지금은 이 기억을 읽을 수 없습니다. 다른 기억을 선택하거나 새로 고치세요.",
  guideChanged:
    "기억 또는 권한이 변경되었습니다. 새로 고친 후 다시 확인하세요.",
  guideConnectionChanged:
    "연결 상태가 변경되어 아무것도 복사하지 않았습니다. 방법을 다시 선택하세요.",
  guideCopiedOpened:
    "복사한 후 {host} 열기를 요청했습니다. 전송된 내용은 없습니다. 입력란을 확인하세요.",
  guideCopiedNotFilled:
    "전체를 복사했지만 미리 채우지 못했습니다. 대상 앱에 붙여넣으세요. 전송된 내용은 없습니다.",
  guideCopiedNotOpened:
    "전체를 복사했지만 대상 앱을 열지 못했습니다. 전송된 내용은 없습니다.",
  guideCopied:
    "전체를 복사했습니다. 전송된 내용이나 새로 부여된 읽기 권한은 없습니다.",
  guideValidationError:
    "현재 기억 또는 권한을 확인하지 못했습니다. 이전 미리 보기를 숨겼습니다. 새로 고친 후 다시 시도하세요.",
  guideCopyError:
    "최신 내용을 확인하거나 복사를 완료하지 못했습니다. 다시 시도하세요. 대상 앱은 열리지 않았습니다.",
  guideManual: "앱에서 추가됨",
  guideOriginalApp: "출처 앱",
  guideListSeparator: ", ",
  guideSaved: "이 기기에 원문 메시지 {count}개 저장됨 · ",
  "guideSaved.one": "이 기기에 원문 메시지 {count}개 저장됨 · ",
  guideReadingMemories: "정리된 기억을 불러오는 중",
  guideLoaded: "이 페이지에 사용 가능한 기억 {count}개 불러옴",
  "guideLoaded.one": "이 페이지에 사용 가능한 기억 {count}개 불러옴",
  guideSeparate: ". 이 단계들은 따로 집계됩니다.",
  guideChooseBackground: "맥락 선택",
  guideCheckSources: "출처 확인",
  guideTry: "새 대화에서 체험",
  guideLoading: "이 기기에 있는 기억을 불러오는 중…",
  guideEmptyTitle: "먼저 유용한 맥락을 하나 추가하세요",
  guideEmptyDescription:
    "이번 체험에 사용할 수 있는 기억이 없습니다. 가져온 원문은 정리된 기억이 아니며, 제안 항목은 자동으로 공유되지 않습니다.",
  guideAdd: "기억 추가",
  guideConnections: "가져오기 및 연결 보기",
  guideRefresh: "새로 고침",
  guideBackgroundTitle: "이번 체험에 사용할 맥락",
  guideRefreshExample: "예시 새로 고침",
  guideChooseMemory: "기억 선택",
  guideLoadedScope:
    "여기에 불러온 사용 가능한 기억 {count}개 중에서 선택하세요. 전체 기록이 아닙니다.",
  "guideLoadedScope.one":
    "여기에 불러온 사용 가능한 기억 {count}개 중에서 선택하세요. 전체 기록이 아닙니다.",
  guideCheckingEvidence: "원본 근거 확인 중…",
  guideRevision: "{source} · 버전 {revision} · 시스템 생성, 확인 필요",
  guideScope: "범위: ",
  guideAllApps:
    "여러 앱에서 사용 가능하며, 반드시 개인 선호를 뜻하지는 않습니다",
  guidePrivate: "외부 조회가 꺼져 있습니다. 수동 공유는 별도로 결정해야 합니다",
  guideLimited: "제한된 범위입니다. 외부 조회 범위는 확대되지 않았습니다",
  guideEvidence: "원본 근거 · 발췌 {count}개",
  "guideEvidence.one": "원본 근거 · 발췌 {count}개",
  guideUnknownSource: "알 수 없는 출처",
  guideYou: "나",
  guideUnknownAuthor: "작성자 알 수 없음",
  guideUnknownTime: "시간 알 수 없음",
  guideUnavailable:
    "원문 또는 현재 권한을 사용할 수 없습니다. 다른 기억을 선택하거나 새로 고치세요. 이 내용은 공유되지 않습니다.",
  guideDraftTitle: "다음 대화의 시작 문구",
  guideChooseApp: "체험할 앱 선택",
  guideCopyOnly: "직접 앱 선택 · 복사만",
  guideHostsError:
    "연결 상태를 읽지 못했습니다. 다시 시도하거나 맥락만 복사하세요.",
  guideNoApp:
    "연결된 앱이 선택되지 않았습니다. 수동 공유로는 연결이 설치되지 않습니다.",
  guideWired:
    "마지막 자체 점검에 성공했습니다. 이번 용도로 작동하는지 확인하세요.",
  guideInstalled:
    "연결이 설치되었습니다. 읽기 권한은 아직 확인되지 않았습니다.",
  guideNotInstalled: "읽기 연결이 설치되지 않았습니다.",
  guideManage: "앱 연결 관리",
  guideChooseRoute: "맥락을 가져올 방법 선택",
  guideContext: "맥락 가져오기",
  guideQuery: "앱이 검색하도록 하기",
  guideTopic: "이어갈 주제",
  guideQueryHint: "질문만 보내세요. 위의 예상 답변을 새 대화에 넣지 마세요.",
  guideNewHint:
    "선택한 기억만 공유됩니다. 개인 프로필은 자동으로 생성되지 않습니다.",
  guideContextHint: "이 맥락과 인용을 포함합니다. 자동 조회 테스트가 아닙니다.",
  guidePreview: "시작 문구 전체 미리 보기",
  guidePreviewPlaceholder:
    "원본 근거를 사용할 수 있으면 시작 문구가 표시됩니다",
  guideChecking: "확인 중…",
  guideCopy: "시작 문구 복사",
  guideCopyOpen: "복사 후 {host} 열기",
  guideNoReceipt:
    "이번에는 외부 호출 수신 확인이 없습니다. 새 대화에서 올바른 맥락을 인용했나요?",
  guideVerified: "결과를 확인했습니다",
  guideMissing: "찾을 수 없었습니다",
  guideFeedbackOk: "내 피드백: 확인함. 자동 검증 기록이 아닙니다.",
  guideFeedbackMissing:
    "내 피드백: 찾지 못함. 먼저 출처 가져오기, 기억 범위, 앱 연결을 확인하세요.",
  guideInvitation: "체험 초대",
  guideInvitationHint: "내 기억 하나로 새 대화를 이어가 보세요.",
  guideTryOnce: "체험하기",
  guideLater: "나중에",
  guideDismiss: "초대 닫기",
  healthTitle: "상태 진단",
  healthLocal: "로컬 서비스",
  healthBackground: "백그라운드 서비스",
  healthSources: "출처별 합계",
  healthProcessing: "기억 정리",
  healthHosts: "AI 연결",
  healthDetailed: "상세 로그",
  healthDetailedTitle: "상세 로그(문제 해결용)",
  healthDetailedHint:
    "기본적으로 꺼져 있습니다. 지원팀이 문제 해결을 요청할 때만 켜세요.",
  healthDetailedError: "상세 로그 설정을 변경하지 못했습니다. 다시 시도하세요.",
  healthRefresh: "상태 새로 고침",
  healthIncident: "문제 발생 시각 기록",
  healthIncidentDesktop: "데스크톱 앱에서 문제 발생 시각을 기록하세요.",
  healthIncidentPrompt: "문제를 간단히 설명하세요(선택 사항)",
  healthIncidentCopied: "문제 ID를 복사했습니다: {id}",
  healthIncidentError: "문제 발생 시각을 기록하지 못했습니다. 다시 시도하세요.",
  healthReadFailed: "일시적으로 사용할 수 없음",
  healthStale: " · 데이터가 오래되었습니다. 마지막 결과를 표시합니다.",
  healthReading: "상태를 읽는 중…",
  healthRereading: "새로 고치는 중입니다. 마지막으로 성공한 결과를 표시합니다…",
  healthReadSuccessTime: "마지막 성공 읽기",
  healthReadFailedTime: "읽기 실패 시각",
  healthReadingSources: "로컬 서비스 및 출처 상태를 읽는 중…",
  healthPrevious: "마지막 읽기",
  healthPreviousPrefix: "마지막 읽기: ",
  healthRunning: "실행 중",
  healthCoreVersion: "{state} · 버전 {version} · 인스턴스 {instance}",
  healthCoreDisconnected: "로컬 Core가 아직 연결되지 않았습니다.",
  healthBackgroundUnavailable:
    "여기에서는 백그라운드 서비스를 확인할 수 없습니다. 지원되는 데스크톱 앱을 사용하세요.",
  healthRuntime: "Core: {core} · 호스트 브리지: {broker}",
  healthSourceCount: "대화 {conversations}개 · 메시지 {messages}개",
  healthNoSources: "아직 연결된 출처가 없습니다.",
  healthTaskCounts:
    "정리 중 {running} · 대기 {pending} · 완료 {completed} · 실패 {failed}",
  healthImportDetails: "가져오기 및 정리 세부 정보",
  healthRawTurns: "원문 및 완료된 대화 턴",
  healthRawCounts: "원문 {raw}개 · 완료된 턴 {turns}개",
  healthHy: "HY 정리",
  healthHyCounts:
    "호출 {calls}회 · 완료 {completed} · 불확실 {unknown} · 실패 {failed} · 취소 {cancelled}",
  healthOutput: "결과물",
  healthMemories: "기억 {count}개",
  "healthMemories.one": "기억 {count}개",
  healthWorkbuddy: "WorkBuddy 기억 조회",
  healthWorkbuddyCounts:
    "주입 {injected}회 · 시간 초과 {timedOut}회 · 사용 불가 {unavailable}회",
  healthBlocked: "{kind}: {reason}(으)로 막힘({count}개)",
  healthRetrying: "정리 재시도 · {count}",
  "healthRetrying.one": "정리 재시도 · {count}",
  healthNoIssues: "확인이 필요한 문제가 없습니다.",
  healthHostsUnavailable:
    "브라우저에서는 읽을 수 없습니다. 데스크톱 앱에서 확인하세요.",
  healthNoHosts: "이 기기에서 지원되는 AI 도구가 감지되지 않았습니다.",
  healthBackgroundHealthy: "백그라운드 서비스 사용 가능",
  healthBackgroundStopped: "백그라운드 서비스 중지됨",
  healthBackgroundDegraded: "백그라운드 서비스 일부 사용 불가",
  healthBackgroundUntrusted: "백그라운드 서비스 신원을 확인할 수 없음",
  healthBackgroundUnknown: "백그라운드 상태 확인되지 않음",
  healthStopped: "중지됨",
  healthUnhealthy: "연결할 수 없음",
  healthUntrusted: "신원 확인 안 됨",
  healthUnknown: "확인되지 않음",
  healthNotChecked: "확인 안 함",
  healthWired: "연결됨",
  healthBlind: "설치되었지만 기억을 가져올 수 없음",
  healthUnreachable: "연결 안 됨",
  startupReadError: "시작 상태를 읽지 못했습니다. 다시 시도하세요.",
  startupRetry: "다시 시도",
  startupOpening: "MemHub 여는 중…",
  nativeHelp: "도움말",
  nativeExportLogs: "지원용 로그 내보내기",
  nativeExportError: "로그를 내보내지 못했습니다. 나중에 다시 시도하세요.",
  nativeLogin: "로그인 시 {product} 실행",
  nativeTrialUnavailable: " (이 빌드에서 사용 불가)",
  nativeCliManaged: " (CLI에서 관리)",
  nativeStatusUnavailable: " (상태 확인 불가)",
  nativeDevelopmentUnavailable: " (개발 모드에서 사용 불가)",
  nativeOpen: "{product} 열기",
  nativeQuit: "{product} 종료(백그라운드 서비스는 계속 실행)",
  nativeQuitStopsService: "{product} 종료(메모리 서비스도 중지)",
  nativeLoginError:
    "로그인 시 실행 설정을 변경하지 못했습니다. 다시 시도하세요.",
  nativeMoveTitle: "먼저 MemHub를 응용 프로그램 폴더로 옮기세요",
  nativeMoveDetail:
    "다른 앱을 연결하고 명령줄 도구를 설치하려면 응용 프로그램 폴더에서 MemHub를 다시 여세요.",
  nativeAcknowledge: "확인",
  graphWidthTitle: "기존 기억 저장소가 내장 모델과 맞지 않습니다",
  graphWidthDescription:
    "기억 저장소는 {stored}차원을 사용하지만 내장 모델은 {expected}차원을 생성합니다. 둘은 함께 작동할 수 없으므로 옵션을 선택할 때까지 추출이 계속 실패합니다.",
  graphWidthKeep:
    "기존 저장소 유지(기본값): 이전 임베딩 구성을 계속 사용하며, 기억 {count}개와 그래프는 그대로 유지됩니다",
  graphWidthRebuild:
    "내장 모델 사용: 기존 그래프를 보관(삭제 아님)하며, 기억 {count}개의 관계를 다시 구축해야 합니다",
  graphWidthExport:
    "먼저 내보낸 후 결정: 지금은 아무것도 변경하지 않고 내보낸 후 다시 선택합니다",
  graphWidthExported:
    "{path}(으)로 내보냈습니다. 기존 저장소는 변경되지 않았으며 다시 선택할 수 있습니다.",
  graphWidthUnknown:
    "차원 선택이 확인되지 않았습니다. 기존 기억 저장소는 변경되지 않았습니다. 다시 시도하세요.",
  graphWidthConfirm: "선택 확인",
  importUserProgress:
    "대화 기록을 가져오는 중입니다. 대화 {count}개를 가져왔습니다. 기억은 점차 나타나며 앱을 계속 사용할 수 있습니다.",
  "importUserProgress.one":
    "대화 기록을 가져오는 중입니다. 대화 {count}개를 가져왔습니다. 기억은 점차 나타나며 앱을 계속 사용할 수 있습니다.",
  importUserModel:
    "모델이 아직 구성되지 않았습니다. 당분간 새 대화는 기억으로 정리되지 않습니다.",
  importUserIncomplete:
    "일부 내용이 완전히 정리되지 않았습니다. 설정을 확인하세요.",
  importUserSettings: "설정으로 이동",
  diagnosticExportDesktop: "데스크톱 앱에서 진단 정보를 내보내세요.",
  diagnosticExported: "로그를 내보냈습니다: {filename}",
  diagnosticFile: "진단 파일",
  diagnosticExportFailed: "진단 정보를 내보내지 못했습니다. 다시 시도하세요.",
  debugTitle: "디버그 로그",
  debugCount: "이번 세션의 로그 {count}개",
  "debugCount.one": "이번 세션의 로그 {count}개",
  debugDiagnostics: "진단",
  debugUsageSubtitle: "기억 정리 모델 사용량",
  debugTabs: "진단 탭",
  debugLogsTab: "로그",
  debugUsageTab: "모델 사용량",
  debugFullscreen: "전체 화면",
  debugExitFullscreen: "전체 화면 종료",
  debugFullscreenLabel: "로그를 전체 화면으로 보기",
  debugExitFullscreenLabel: "로그 전체 화면 종료",
  usageRange15m: "최근 15분",
  usageRange1h: "최근 1시간",
  usageRange6h: "최근 6시간",
  usageRange24h: "최근 24시간",
  usageRange7d: "최근 7일",
  usageRange: "기간",
  usageRangeLabel: "모델 사용량 기간",
  usageLoading: "불러오는 중…",
  usageReadError: "불러오지 못함",
  usageLive: "5초마다 새로 고침",
  usageTotalTokens: "총 토큰",
  usageRequests: "요청 수",
  usageChartRegion: "프롬프트별 분당 토큰",
  usageChartTitle: "프롬프트별 토큰 사용량",
  usageChartSubtitle: "분당 · 총 토큰",
  usageBreakdown: "프롬프트 세부 정보",
  usageCurrentRange: "현재 기간",
  usageRequestCount: "요청 {count}회",
  "usageRequestCount.one": "요청 {count}회",
  usageEmpty: "이 기간에 기억 정리 모델 호출이 없습니다",
  usageNoChart: "아직 차트 데이터가 없습니다",
  usageChartAria: "프롬프트별 분당 토큰 사용량",
  debugPause: "실시간 새로 고침 일시 중지",
  debugResume: "실시간 새로 고침 재개",
  debugRefresh: "지금 새로 고침",
  debugClose: "디버그 로그 닫기",
  debugSearchPlaceholder: "이벤트, 구성 요소, 오류 코드 또는 내용 검색",
  debugSearch: "로그 검색",
  debugSource: "로그 출처",
  debugAllSources: "모든 출처",
  debugCapture: "수집 수준",
  debugDisplay: "표시 수준",
  debugAll: "전체",
  debugMatches: "일치 항목 {count}개",
  "debugMatches.one": "일치 항목 {count}개",
  debugLive: "실시간 새로 고침 중",
  debugPaused: "일시 중지됨",
  debugTruncated: "로그가 너무 많아 잘렸습니다",
  debugReadError: "불러오지 못함",
  debugLoading: "로그를 불러오는 중…",
  debugEmpty: "일치하는 로그가 없습니다",
  debugOpened: "디버그 로그가 열려 있습니다",
  debugOpen: "디버그 로그 열기",
  "这里需要 API Key，不是网址。请填写服务方提供的密钥。":
    "URL이 아니라 서비스에서 제공한 API 키를 입력하세요.",
  "服务商拒绝了这个 API Key（401）。请确认密钥属于当前填写的服务地址。":
    "제공업체가 이 API 키를 거부했습니다(401). 키가 입력한 엔드포인트에 해당하는지 확인하세요.",
  "服务商没有这个模型名，请对照服务商的模型列表核对。":
    "제공업체가 이 모델 이름을 인식하지 못합니다. 제공업체의 모델 목록을 확인하세요.",
  "连不上接口地址，请检查网络和服务地址是否正确。":
    "엔드포인트에 연결할 수 없습니다. 네트워크와 서비스 주소를 확인하세요.",
  未提供接口地址: "엔드포인트가 입력되지 않음",
  "请填写生成模型名。": "생성 모델 이름을 입력하세요.",
  "请填写完整的 http:// 或 https:// 接口地址。":
    "http:// 또는 https://로 시작하는 전체 엔드포인트 URL을 입력하세요.",
  "请填写向量模型名。": "임베딩 모델 이름을 입력하세요.",
  "请填写完整的向量模型接口地址。":
    "임베딩 모델의 전체 엔드포인트 URL을 입력하세요.",
  "这份配置在测试期间被改过，刚才测的是改动后的版本。配置已保存但未启用，请重新测试并启用。":
    "테스트 중에 이 구성이 변경되어 최신 버전으로 테스트했습니다. 저장되었지만 활성화되지 않았습니다. 다시 테스트하고 활성화하세요.",
  "测试结果来自这份配置更早的版本。配置已保存但未启用，请重新测试并启用。":
    "테스트 결과가 이 구성의 이전 버전에 해당합니다. 저장되었지만 활성화되지 않았습니다. 다시 테스트하고 활성화하세요.",
  "配置已保存，连接测试未通过。请核对配置后重试，历史对话尚未发送。":
    "구성은 저장되었지만 연결 테스트에 실패했습니다. 확인 후 다시 시도하세요. 대화 기록은 전송되지 않았습니다.",
  "配置已保存，尚未确认启用成功。请核对配置后重试。":
    "구성은 저장되었지만 활성화가 확인되지 않았습니다. 확인 후 다시 시도하세요.",
  "配置尚未完成测试和启用，请核对后重试。":
    "테스트와 활성화가 완료되지 않았습니다. 구성을 확인한 후 다시 시도하세요.",
  "配置尚未保存成功，已填写内容保留，可核对后重试。":
    "구성 저장을 확인하지 못했습니다. 입력한 내용은 유지됩니다. 확인 후 다시 시도하세요.",
  "只需配置生成模型。向量嵌入已内置在本机，不为 System 1、System 2 分开选模型。":
    "생성 모델만 구성하면 됩니다. 벡터 임베딩은 로컬에 내장되어 있으며, System 1과 System 2에 별도 모델이 필요하지 않습니다.",
  "无法读取测试恢复记录，暂不发送新的测试。":
    "테스트 복구 기록을 읽지 못했습니다. 새 테스트를 보내지 않습니다.",
  "上次模型测试结果待核对，暂不修改或启用配置。":
    "이전 모델 테스트를 확인해야 합니다. 아직 구성을 편집하거나 활성화하지 않습니다.",
  核对测试结果: "테스트 결과 확인",
  "上次模型创建结果待核对，暂不开始新的操作。":
    "이전 모델 생성을 확인해야 합니다. 아직 새 작업을 시작하지 않습니다.",
  核对创建结果: "생성 결과 확인",
  "配置已保存，正在测试连接…": "구성을 저장했습니다. 연결 테스트 중…",
  "连接通过，正在启用模型…": "연결에 성공했습니다. 모델 활성화 중…",
  刷新配置列表: "구성 새로 고침",
  自定义一个模型: "사용자 지정 모델 구성",
  已保存的模型配置: "저장된 모델 구성",
  "已保存，已启用": "저장 및 활성화됨",
  "已保存，连接通过": "저장됨, 연결 성공",
  "已保存，测试未通过": "저장됨, 테스트 실패",
  "已保存，待测试": "저장됨, 테스트 대기 중",
  版本: "버전",
  "需要允许 MemHub 访问钥匙串。系统询问时请选择“始终允许”；已保存的配置不会丢失。":
    "MemHub가 키체인에 접근하도록 허용하세요. 메시지가 표시되면 ‘항상 허용’을 선택하세요. 저장된 구성은 사라지지 않습니다.",
  "测试只向此接口发送测试内容，不发送历史对话。":
    "테스트는 대화 기록이 아닌 테스트 내용만 이 엔드포인트로 보냅니다.",
  重试测试并启用: "테스트 재시도 및 활성화",
  测试并启用: "테스트 및 활성화",
  编辑配置: "구성 편집",
  新增配置: "구성 추가",
  编辑: "편집",
  "。修改后需重新测试并设为当前模型，内容授权不会自动更新。":
    ". 변경한 후에는 다시 테스트하고 현재 모델로 설정하세요. 콘텐츠 권한은 자동으로 업데이트되지 않습니다.",
  "配置已在其他位置变化，草稿已保留。请取消编辑后重新打开核对。":
    "다른 곳에서 구성이 변경되었습니다. 초안은 유지됩니다. 편집을 취소하고 다시 열어 확인하세요.",
  服务商预设: "제공업체 사전 설정",
  自定义兼容服务: "사용자 지정 호환 서비스",
  "只是帮你填好接口地址和一个模型名，不代表这个模型当前可用。 请对照服务商的模型列表核对后再保存。":
    "엔드포인트와 모델 이름을 채워 주지만 사용 가능 여부를 보장하지는 않습니다. 저장하기 전에 제공업체의 모델 목록을 확인하세요.",
  协议: "프로토콜",
  "与模型服务提供的接口协议保持一致。":
    "모델 서비스에서 제공하는 프로토콜과 일치시키세요.",
  配置名称: "구성 이름",
  "用于区分配置，不影响模型请求。":
    "구성을 구분하는 데 사용되며 모델 요청에는 영향을 주지 않습니다.",
  生成模型名: "생성 모델 이름",
  "例如：gpt-4o-mini…": "예: gpt-4o-mini…",
  "填写服务方提供的完整模型标识。":
    "서비스에서 제공한 전체 모델 ID를 입력하세요.",
  "高级：自行配置向量模型": "고급: 임베딩 모델 직접 구성",
  向量配置模式: "임베딩 구성 모드",
  本地内置: "내장 로컬 모델",
  自定义向量模型: "사용자 지정 임베딩 모델",
  向量模型名: "임베딩 모델 이름",
  "例如：text-embedding-3-small…": "예: text-embedding-3-small…",
  向量模型来源: "임베딩 모델 제공업체",
  "本机 Ollama": "로컬 Ollama",
  "同源 OpenAI-compatible（共用 API Key）":
    "동일 출처 OpenAI-compatible(API 키 공유)",
  "向量模型 Base URL": "임베딩 모델 Base URL",
  "为避免凭据外泄，向量地址必须与生成模型同源。":
    "자격 증명 보호를 위해 임베딩 엔드포인트는 생성 모델과 동일한 출처(origin)여야 합니다.",
  "使用内置本地向量模型；不会发送内容给远程向量服务。":
    "내장 로컬 임베딩 모델을 사용합니다. 원격 임베딩 서비스로 내용을 보내지 않습니다.",
  自定义向量: "사용자 지정 임베딩",
  尚未填写模型: "모델이 입력되지 않음",
  尚未填写地址: "주소가 입력되지 않음",
  "填写服务接口地址，不是聊天网页地址。":
    "채팅 웹사이트 URL이 아니라 API 엔드포인트를 입력하세요.",
  "（可选）": " (선택 사항)",
  "保存后不会再次显示…": "저장 후에는 다시 표시되지 않습니다…",
  "留空保留已保存密钥；填写新密钥后需重新测试和启用。":
    "저장된 키를 유지하려면 비워 두세요. 새 키를 입력한 후에는 다시 테스트하고 활성화하세요.",
  "换了服务商后需要填写新的 API Key，已保存的密钥不会带到新地址。":
    "제공업체를 변경한 후에는 새 API 키를 입력하세요. 저장된 키는 새 엔드포인트로 전송되지 않습니다.",
  "保存在系统安全存储中，不随安装包分发。":
    "시스템 보안 저장소에 저장되며 앱과 함께 배포되지 않습니다.",
  "关闭思考链（向支持的厂商下发 thinking-off 参数）":
    "사고 과정 끄기(지원되는 제공업체에 thinking-off 전송)",
  保存修改: "변경 사항 저장",
  保存配置: "구성 저장",
  "正在启用配置…": "구성 활성화 중…",
  测试并完成配置: "테스트 후 설정 완료",
  保存并测试启用: "저장, 테스트 및 활성화",
  取消编辑: "편집 취소",
  "密钥保存在系统安全存储；测试仅发送测试内容。":
    "키는 안전하게 저장되며, 테스트는 테스트 내용만 보냅니다.",
  当前使用: "사용 중",
  未启用: "활성화 안 됨",
  测试目标: "테스트 대상",
  "。仅发送测试内容，不发送历史对话。":
    ". 대화 기록이 아닌 테스트 내용만 전송됩니다.",
  测试连接: "연결 테스트",
  设为当前模型: "현재 모델로 설정",
  删除: "삭제",
  "粘贴 API Key": "API 키 붙여넣기",
  "密钥尚未保存成功，已填写内容保留，可重试。":
    "키를 저장하지 못했습니다. 입력한 내용은 유지되며 다시 시도할 수 있습니다.",
  生成接收方: "생성 수신처",
  向量接收方: "임베딩 수신처",
  本机: "이 기기",
  "。启用此配置后按上方选择处理历史及未来允许来源。":
    ". 이 구성을 활성화하면 위의 선택에 따라 승인된 과거 및 향후 출처가 정리됩니다.",
  "保存 key": "키 저장",
  "还没有 key？去申请": "아직 키가 없나요? 신청하기",
  删除这张卡: "이 카드 삭제",
  "密钥已配置，当前读取状态待确认。":
    "키가 구성되었습니다. 접근 상태를 확인해야 합니다.",
  "等待 key：其余都已经填好了，贴上 key 就能用。":
    "키 대기 중입니다. 다른 설정은 준비되었으니 키를 붙여넣어 계속하세요.",
  "key 已保存，还没测过这个模型能不能用。":
    "키를 저장했습니다. 이 모델은 아직 테스트하지 않았습니다.",
  "Ollama（本机）": "Ollama(로컬)",
  "内置 bge-small-zh-v1.5": "내장 bge-small-zh-v1.5",
  通过: "통과",
  "HY 托管模型尚不可用": "HY 호스팅 모델을 사용할 수 없음",
  "缺少 API Key": "API 키 없음",
  服务不可达: "서비스에 연결할 수 없음",
  "认证失败；更新 key 后再检测，会自动接着处理":
    "인증에 실패했습니다. 키를 업데이트하고 다시 테스트하면 정리가 재개됩니다.",
  请求频率受限: "요청 한도 초과",
  "模型账户余额不足；充值或换 key 后再检测，会自动接着处理":
    "모델 계정 잔액이 부족합니다. 충전하거나 키를 변경한 후 다시 테스트하면 재개됩니다.",
  没有找到模型: "모델을 찾을 수 없음",
  "该网关不提供模型列表，已改由生成调用验证":
    "게이트웨이에 모델 목록이 없어 생성 요청으로 대신 확인했습니다.",
  服务返回不符合协议: "서비스 응답이 프로토콜과 일치하지 않음",
  结构化输出未通过: "구조화된 출력 테스트 실패",
  "需要填写 Embedding 模型": "임베딩 모델을 입력하세요",
  "Embedding 模型不可用": "임베딩 모델을 사용할 수 없음",
  "请先核对上次模型测试结果，暂不修改或启用配置。":
    "먼저 이전 모델 테스트를 확인하세요. 아직 구성을 변경하거나 활성화하지 않습니다.",
  "本地恢复记录已变化，仍保持锁定。请重新打开后核对。":
    "로컬 복구 기록이 변경되었습니다. 구성은 잠긴 상태로 유지됩니다. 다시 열어 확인하세요.",
  测试结果: "테스트 결과",
  "暂不支持一键插入：它没有 MCP 通道，无法发放可撤销的记忆凭证。":
    "원클릭 연결을 사용할 수 없습니다. 이 앱에는 취소 가능한 기억 자격 증명을 위한 MCP 채널이 없습니다.",
  应用控制通道暂不可用: "앱 제어 채널을 사용할 수 없습니다",
  appFailureReason: " 원인: {reason}. ",
  appFailureReread:
    "연결 상태도 다시 불러오지 못했습니다. 표시된 상태가 최신이 아닐 수 있습니다.",
  模型: "모델",
  连接其他应用: "다른 앱 연결",
  帮助与诊断: "도움말 및 진단",
  设置分组: "설정 섹션",
  记忆模型: "기억 모델",
  当前模型: "현재 모델",
  "分别管理记忆整理与搜索所使用的模型。":
    "기억 정리와 검색에 사용할 모델을 각각 관리합니다.",
  记忆整理: "기억 정리",
  "把会话理解并整理成可长期使用的记忆。":
    "대화를 이해하고 장기간 활용할 수 있는 기억으로 정리합니다.",
  记忆搜索: "기억 검색",
  "在需要时找到与你的问题最相关的记忆。":
    "필요할 때 질문과 가장 관련성 높은 기억을 찾습니다.",
  "本地内置（推荐）": "로컬 내장(권장)",
  "Embedding · 配置记忆整理后可自定义":
    "Embedding · 기억 정리 구성 후 사용자 지정 가능",
  "Embedding · 本机处理，无需额外配置":
    "Embedding · 로컬 처리, 추가 구성 필요 없음",
  "正在读取模型…": "모델을 불러오는 중…",
  模型状态暂不可读: "모델 상태를 확인할 수 없습니다",
  尚未配置模型: "구성된 모델이 없습니다",
  修改模型: "모델 편집",
  配置模型: "모델 구성",
  刷新模型状态: "모델 상태 새로 고침",
  "本机 / 受管端点": "로컬 / 관리형 엔드포인트",
  接口地址待核对: "엔드포인트 확인 필요",
  "模型状态未能读取，请重试。": "모델 상태를 읽지 못했습니다. 다시 시도하세요.",
  "模型状态刷新未成功，仍显示上次确认的模型状态；请重试。":
    "모델 상태를 새로 고치지 못했습니다. 마지막으로 확인된 상태를 표시합니다. 다시 시도하세요.",
  "上次模型测试或创建还待核对，配置暂时不能改动。请先在下方模型配置区核对结果； 现有配置和原文不会被改动。":
    "이전 모델 테스트 또는 생성을 확인해야 합니다. 편집하기 전에 아래에서 결과를 확인하세요. 구성과 원문은 변경되지 않았습니다.",
  用模型沉淀记忆: "모델로 기억 생성",
  "开启后，导入和同步到本机的会话会交给当前模型整理成记忆，会产生模型调用。 停止后不再调用模型、不再整理记忆，但会话仍会照常导入和保存；已排队的内容会留着，重新开启后继续。 模型配置始终保留，重新开启不需要重新填写。":
    "켜면 가져오거나 동기화한 대화를 현재 모델이 기억으로 정리하며, 모델 호출이 발생합니다. 끄면 대화는 계속 가져와 저장되며, 대기 중인 작업은 다시 켤 때 재개됩니다. 모델 구성은 그대로 유지됩니다.",
  "启用未能确认，仍显示上次确认的状态；请重试。":
    "활성화를 확인하지 못했습니다. 마지막으로 확인된 상태를 표시합니다. 다시 시도하세요.",
  "停止未能确认，模型可能仍在整理记忆，请重试。":
    "중지를 확인하지 못했습니다. 모델이 아직 기억을 정리 중일 수 있습니다. 다시 시도하세요.",
  "当前安装还不能读取这个开关，请确认后台服务已启动后重试。":
    "이 설치에서는 이 스위치를 읽을 수 없습니다. 백그라운드 서비스가 실행 중인지 확인한 후 다시 시도하세요.",
  关于: "정보",
  "此应用还没有读取授权，请先授权它的对话目录。":
    "이 앱에는 아직 읽기 권한이 없습니다. 먼저 대화 디렉터리를 승인하세요.",
  "此应用的读取授权已撤销，请重新授权。":
    "이 앱의 읽기 권한이 취소되었습니다. 다시 승인하세요.",
  "此应用的对话目录已被重建，请重新授权。":
    "이 앱의 대화 디렉터리가 다시 만들어졌습니다. 다시 승인하세요.",
  "此应用的对话目录现在读不到，请确认应用已安装并产生过对话。":
    "이 앱의 대화 디렉터리를 읽을 수 없습니다. 앱이 설치되어 있고 대화가 있는지 확인하세요.",
  "此应用的对话目录已变更，请按最新目录重新授权。":
    "이 앱의 대화 디렉터리가 변경되었습니다. 새 디렉터리를 승인하세요.",
  "此应用还没有读取授权，请先授权它的对话目录。完成后再开启持续同步。":
    "이 앱에는 아직 읽기 권한이 없습니다. 대화 디렉터리를 승인한 후 지속 동기화를 켜세요.",
  "此应用的读取授权已撤销，请重新授权。完成后再开启持续同步。":
    "이 앱의 읽기 권한이 취소되었습니다. 다시 승인한 후 지속 동기화를 켜세요.",
  "此应用的对话目录已被重建，请重新授权。完成后再开启持续同步。":
    "이 앱의 대화 디렉터리가 다시 만들어졌습니다. 다시 승인한 후 지속 동기화를 켜세요.",
  "此应用的对话目录现在读不到，请确认应用已安装并产生过对话。完成后再开启持续同步。":
    "이 앱의 대화 디렉터리를 읽을 수 없습니다. 앱이 설치되어 있고 대화가 있는지 확인한 후 지속 동기화를 켜세요.",
  "此应用的对话目录已变更，请按最新目录重新授权。完成后再开启持续同步。":
    "이 앱의 대화 디렉터리가 변경되었습니다. 새 디렉터리를 승인한 후 지속 동기화를 켜세요.",
  "此应用的读取授权已失效，请重新授权后再开启持续同步。":
    "이 앱의 읽기 권한이 만료되었습니다. 지속 동기화를 켜기 전에 다시 승인하세요.",
  "此应用的授权或目录已变化，已按最新状态显示，请确认后再试。":
    "이 앱의 권한 또는 디렉터리가 변경되었습니다. 업데이트된 상태를 확인하고 다시 시도하세요.",
  模型配置表单: "모델 구성 양식",
  "部分运行状态暂时无法读取，当前显示最后读到的状态，请重试。":
    "일부 상태 정보를 확인할 수 없습니다. 마지막으로 확인된 상태를 표시합니다. 다시 시도하세요.",
  "当前安装还不能设置登录后后台启动。":
    "이 설치에서는 로그인 시 실행을 구성할 수 없습니다.",
  "登录后后台启动设置未能确认，请重试。":
    "로그인 시 실행 설정을 확인하지 못했습니다. 다시 시도하세요.",
  "当前安装还不能退出界面，请从菜单栏退出。":
    "이 설치에서는 창을 종료할 수 없습니다. 메뉴 막대를 사용하세요.",
  "退出界面未能确认，请重试。": "종료를 확인하지 못했습니다. 다시 시도하세요.",
  登录后启动后台服务: "로그인 시 백그라운드 서비스 시작",
  "无需打开界面，持续同步、整理和提供记忆。":
    "앱을 열지 않아도 기억을 계속 동기화, 정리하고 제공합니다.",
  导出日志: "로그 내보내기",
  "把本机运行日志打包成一个文件，发给开发排查。":
    "모든 로컬 로그를 묶습니다. 대화 내용이나 로컬 경로가 포함될 수 있으므로 신뢰할 수 있는 개발자에게만 공유하세요.",
  "确定退出 One Memory 界面吗？": "MemHub 창을 종료하시겠습니까?",
  "后台服务会继续同步、整理和提供记忆。":
    "백그라운드 서비스는 계속 기억을 동기화, 정리하고 제공합니다.",
  取消: "취소",
  退出界面: "창 종료",
  应用详情: "앱 세부 정보",
  返回应用: "앱으로 돌아가기",
  同步开关: "동기화 스위치",
  "开启后处理历史待办，并持续接收此应用的新会话。":
    "켜면 대기 중인 기록을 정리하고 이 앱의 새 대화를 계속 받습니다.",
  "正在读取已有对话，首次开启可能需要一两分钟，请不要重复点击。":
    "기존 대화를 읽는 중입니다. 처음 실행하면 1~2분 정도 걸릴 수 있으니 다시 클릭하지 마세요.",
  "将补充冻结期间的新对话；原有 pending Job 继续处理。":
    "동결 기간에 받은 대화를 보충합니다. 기존 대기 중인 작업은 계속 정리됩니다.",
  一键插入应用: "원클릭 앱 연결",
  "安装记忆连接，并默认允许此应用使用已授权记忆。":
    "기억 연결을 설치하고 기본적으로 이 앱이 승인된 기억을 사용하도록 허용합니다.",
  允许使用记忆: "기억 접근 허용",
  连接状态: "연결 상태",
  已接通: "연결됨",
  "已安装，但取不到记忆": "설치되었지만 기억을 가져올 수 없음",
  未接通: "연결 안 됨",
  状态待核对: "상태 확인 필요",
  检测中: "확인 중",
  未开启关联: "연결 꺼짐",
  "正在开启处理…": "정리 시작 중…",
  "正在冻结处理…": "정리 동결 중…",
  处理中: "정리 중",
  已冻结: "동결됨",
  "此应用的处理开关结果未能确认，其他应用不受影响。":
    "이 앱의 정리 스위치를 확인하지 못했습니다. 다른 앱에는 영향이 없습니다.",
  "暂时无法刷新来源状态，请重试；未更改同步或读取权限。":
    "출처 상태를 새로 고치지 못했습니다. 다시 시도하세요. 동기화 및 읽기 권한은 변경되지 않았습니다.",
  "暂时无法读取连接状态，当前状态待核对；请重试。":
    "연결 상태를 읽지 못했습니다. 현재 상태를 확인해야 합니다. 다시 시도하세요.",
  "已接通。已经打开的对话不会中途出现这个工具，请新开一轮。":
    "연결되었습니다. 이미 열려 있는 대화에는 도구가 나타나지 않으니 새 대화를 시작하세요.",
  "本次连接探测失败，当前状态待核对；未把上次读取当作本次成功。":
    "이번 연결 확인에 실패했습니다. 현재 상태를 확인해야 하며, 이전 결과를 새 성공으로 간주하지 않았습니다.",
  上次读取: "마지막 읽기",
  appSettingsTitle: "{name} 앱 설정",
  appMemoryScope:
    "{name}이(가) 승인된 범위 내에서 기억을 조회하도록 허용합니다.",
  appRestart:
    "기억 도구가 {name}의 현재 대화에 없습니다. {name}을(를) 완전히 종료했다가 다시 열면 새 대화에 나타납니다.",
  appConnectFailed:
    "이 앱의 원클릭 연결이 완료되지 않았습니다. 다른 앱에는 영향이 없습니다. {reason}",
  appDisconnectFailed:
    "연결을 제거하지 못했고 기억 접근 권한도 취소하지 못했습니다. {name}은(는) 여전히 기억을 사용할 수 있습니다. 다시 시도하세요. {reason}",
  appPermissionFailed:
    "이 앱의 기억 접근 권한을 확인하지 못했습니다. 다시 시도하세요. {reason}",
  appRevokedModified:
    "기억 접근 권한이 취소되어 {name}은(는) 더 이상 기억을 조회할 수 없습니다. 구성 파일이 수정되어 MemHub가 자동으로 삭제하지 않았습니다. MemHub 연결을 직접 제거하세요.",
  appRevokedRetained:
    "기억 접근 권한이 취소되어 {name}은(는) 더 이상 기억을 조회할 수 없습니다. 연결 파일이 제거되지 않았으니 다시 확인하세요.",
  appRevokedReview:
    "기억 접근 권한이 취소되어 {name}은(는) 더 이상 기억을 조회할 수 없습니다. 완전히 제거되었는지 확인할 수 없으니 다음 경로를 직접 확인하세요: {paths}.",
  appRemovalReview:
    "연결이 완전히 제거되었는지 확인할 수 없습니다. 다음 경로를 직접 확인하세요: {paths}.",
  appProfilesLabel: "{name} 프로필",
  appProfileOn: "{profile}: 연결됨",
  appProfileOff: "{profile}: 연결 안 됨",
  appProfilesHint:
    "이 스위치는 감지된 모든 프로필에 기억 연결을 설치하고 접근을 허용하며, 끄면 모두 취소합니다. 켜져 있는 동안 새 프로필은 자동으로 연결됩니다.",
  appProfilesNone:
    "{name} 프로필이 감지되지 않았습니다. 프로필을 만든 뒤 이 페이지로 돌아오세요.",
  appProfilesConnectFailed:
    "다음 프로필을 연결하지 못했습니다: {profiles}. 다른 프로필은 유지됩니다. {reason}",
  appProfilesDisconnectFailed:
    "다음 프로필의 연결을 해제하지 못해 여전히 기억을 사용할 수 있습니다: {profiles}. 다시 시도하세요. {reason}",
  appProfilesRevokedRetained:
    "다음 프로필의 기억 접근 권한을 취소했지만 연결 파일이 모두 제거되지는 않았습니다. 확인하세요: {profiles}.",
  appProfileNameUnsupported:
    "프로필 이름에는 소문자, 숫자, 하이픈만 사용할 수 있으며 최대 32자입니다",
  "apps.autoHostProfilesFailed":
    "{name}의 다음 프로필을 자동으로 연결하지 못했습니다({profiles}){reason}.",
  "Cursor 的连接凭证已更新。请完全退出并重新打开 Cursor，再开始新对话。":
    "Cursor의 연결 자격 증명이 변경되었습니다. 새 대화를 시작하기 전에 Cursor를 완전히 종료했다가 다시 여세요.",
  连接其他工具: "다른 도구 연결",
  "本机连接材料仅在桌面 App 中可用。":
    "로컬 연결 정보는 데스크톱 앱에서만 사용할 수 있습니다.",
  "仅本机 MCP / 命令工具。纯网页聊天不支持直接连接本机。":
    "로컬 MCP 또는 명령줄 도구 전용입니다. 브라우저 채팅은 이 기기에 직접 연결할 수 없습니다.",
  "列表内工具请用上方一键安装，此处仅供未列出的工具。":
    "목록에 있는 도구는 위의 원클릭 설정을 사용하세요. 여기는 목록에 없는 도구용입니다.",
  "正在读取本机连接配置…": "로컬 연결 정보를 불러오는 중…",
  "未能读取连接配置。": "연결 정보를 불러오지 못했습니다.",
  重试: "다시 시도",
  连接材料格式: "연결 정보 형식",
  连接提示词: "연결 프롬프트",
  "MCP 配置": "MCP 구성",
  命令: "명령",
  本机连接材料: "로컬 연결 정보",
  "包含本机路径，不含记忆和密钥。片段会运行本机 MemHub 连接程序；粘贴前请确认，MemHub 需保持运行。":
    "로컬 경로는 포함되지만 기억이나 키는 포함되지 않습니다. 이 코드는 로컬 MemHub 커넥터를 실행합니다. 붙여넣기 전에 확인하고 MemHub를 계속 실행해 두세요.",
  "已复制，尚未建立连接。": "복사했습니다. 아직 연결되지 않았습니다.",
  "复制失败，可在上方选中文本复制。":
    "복사하지 못했습니다. 위의 텍스트를 선택해 직접 복사하세요.",
  复制提示词: "프롬프트 복사",
  复制配置: "구성 복사",
  复制命令: "명령 복사",
  记忆库分页: "기억 저장소 페이지",
  上一页: "이전 페이지",
  下一页: "다음 페이지",
  "把熟悉的背景，带到这里": "익숙한 맥락을 여기로 가져오세요",
  "你的记忆，从这里接上": "흩어진 기억을 한곳에 모으세요",
  "这张卡还没测过。点一次「设为当前模型」会先自动测，如果还是不行，说明端点、key 或模型名有一处不对。":
    "이 구성은 아직 테스트하지 않았습니다. 현재 모델로 설정하면 먼저 테스트가 실행됩니다. 그래도 실패하면 엔드포인트, 키, 모델 이름을 확인하세요.",
  "这类模型接口暂时用不了，换一张卡。":
    "이 유형의 모델 엔드포인트는 아직 지원되지 않습니다. 다른 구성을 사용하세요.",
  "这张卡缺了向量化模型，补上才能启用。":
    "이 구성을 활성화하려면 임베딩 모델이 필요합니다.",
  "这张卡已经不在了，可能刚被删掉。刷新一下。":
    "이 구성을 더 이상 사용할 수 없습니다. 삭제되었을 수 있으니 목록을 새로 고치세요.",
  "这张卡刚被改过。刷新一下再试，免得覆盖掉别处的改动。":
    "이 구성이 방금 변경되었습니다. 다른 변경 사항을 덮어쓰지 않도록 새로 고친 후 다시 시도하세요.",
  onboardingModelOperationFailed: "작업 실패: {code}",
  onboardingModelSkipDestination: "건너뛰고 앱으로 이동",
  首次设置进度: "초기 설정 진행 상황",
  导入历史: "기록 가져오기",
  连接应用: "앱 연결",
  沉淀记忆: "기억 생성",
  本地存储: "로컬에 저장",
  "对话与记忆保存在本机；使用云模型或已授权的助手时，相关内容会发送给对应服务。":
    "대화와 기억은 이 기기에 저장됩니다. 클라우드 모델이나 승인된 어시스턴트를 사용할 때는 관련 내용이 해당 서비스로 전송됩니다.",
  "来源位置、身份或状态已变化，请重新选择并核对。尚未提交本次授权。":
    "출처 위치, 신원 또는 상태가 변경되었습니다. 다시 선택하고 확인하세요. 이번 승인은 제출되지 않았습니다.",
  "来源位置、身份或状态已变化，正在等待本次提交回执。不会自动扩大已提交范围。":
    "출처 위치, 신원 또는 상태가 변경되었습니다. 이번 제출의 확인을 기다리는 중이며, 제출된 범위는 자동으로 확대되지 않습니다.",
  测试实例不接入应用: "테스트 인스턴스는 앱에 연결되지 않습니다",
  "请先把 MemHub 拖到“应用程序”并从那里重新打开，才能接入应用。":
    "앱을 연결하려면 MemHub를 응용 프로그램 폴더로 옮긴 후 그곳에서 다시 여세요.",
  "部分应用未能完成接入，已成功的设置会保留，可进入应用后单独处理。":
    "일부 앱을 연결하지 못했습니다. 성공한 설정은 유지되며, 나머지는 앱에서 개별적으로 처리하세요.",
  onboardingConnectionPartial:
    "앱 {success}개 연결됨, {failed}개 미완료: {names}. 앱에서 개별적으로 처리할 수 있습니다.",
  "首次设置的完成状态未能保存。已确认的导入回执不受影响；下次启动可能再次出现此页。":
    "초기 설정 완료 상태를 저장하지 못했습니다. 확인된 가져오기 기록에는 영향이 없으며, 다음에 이 페이지가 다시 나타날 수 있습니다.",
  "首次设置的完成状态尚未确认。已确认的导入回执不受影响；下次启动可能再次出现此页。":
    "초기 설정 완료 상태가 확인되지 않았습니다. 확인된 가져오기 기록에는 영향이 없으며, 다음에 이 페이지가 다시 나타날 수 있습니다.",
  "位置检测未完成，尚未读取历史。可以重新检测，或先进入工作台。":
    "위치 감지가 완료되지 않아 기록을 읽지 않았습니다. 다시 확인하거나 워크스페이스로 이동하세요.",
  "暂时无法核对来源，尚未提交授权。选择已保留，可以重试。":
    "지금은 출처를 확인할 수 없어 승인이 제출되지 않았습니다. 선택 항목은 유지되며 다시 시도할 수 있습니다.",
  "本次没有来源获准导入。请核对下方原因后重试，或到应用页查看状态。":
    "이번 가져오기에 승인된 출처가 없습니다. 아래 원인을 확인하고 다시 시도하거나 앱에서 상태를 확인하세요.",
  "开始导入的结果尚未确认，选择已保留。可以重试，或进入应用页核对。":
    "가져오기 시작이 확인되지 않았습니다. 선택 항목은 유지됩니다. 다시 시도하거나 앱에서 확인하세요.",
  未完成: "미완료",
  核对中: "확인 중",
  仍进入应用页: "앱으로 계속",
  仍进入工作台: "워크스페이스로 계속",
  仍进入设置: "설정으로 계속",
  重试保存并进入: "다시 저장하고 계속",
  "稍后添加，先进入": "나중에 추가하고 계속",
  "正在开始导入…": "가져오기 시작 중…",
  开始导入: "가져오기 시작",
  "不用从头介绍自己。从常用的 App 开始，把已有背景带过来。":
    "처음부터 다시 설명할 필요가 없습니다. 자주 쓰는 앱에서 맥락을 가져오세요.",
  "确认前只检查应用信息，不读取对话，也不安装连接。":
    "확인 전에는 앱 정보만 확인합니다. 대화를 읽거나 연결을 설치하지 않습니다.",
  "本机 AI 应用": "이 기기의 AI 앱",
  重新检测位置: "위치 다시 확인",
  "检测中…": "확인 중…",
  重新检测: "다시 확인",
  候选应用: "사용 가능한 앱",
  "正在检测其他应用…": "다른 앱을 찾는 중…",
  检测未完成: "확인 미완료",
  已全选: "모두 선택됨",
  全选: "모두 선택",
  onboardingImportHistory: "{name}에서 기록 가져오기",
  正在核对本机保存位置: "로컬 저장 위치 확인 중",
  已发现: "발견됨",
  位置待核对: "위치 확인 필요",
  "当前没有候选来源。可以稍后在应用页添加历史包。":
    "출처를 찾지 못했습니다. 나중에 앱에서 기록 보관 파일을 추가할 수 있습니다.",
  "候选来源尚未加载。可以检测位置，或先进入工作台。":
    "출처를 불러오지 않았습니다. 위치를 확인하거나 워크스페이스로 이동하세요.",
  本次导入: "이번 가져오기",
  "个 App 的历史对话": "개 앱의 기록",
  "把零散的会话，": "흩어진 대화를",
  汇成完整的记忆: "하나의 기억으로",
  "少一些重复交代，多一些理解。": "반복은 줄이고, 이해는 깊게.",
  onboardingMoreApps: "스크롤하여 더 보기 · 앱 {count}개 남음",
  "onboardingMoreApps.one": "스크롤하여 더 보기 · 앱 {count}개 남음",
  onboardingMoreImportApps: "가로로 스크롤 · 앱 {count}개 남음",
  onboardingImportChecking:
    "가져오기 결과를 확인하는 중이며 선택 항목은 유지됩니다",
  onboardingAllApps: "앱 {count}개 모두 표시됨",
  默认位置: "기본 위치",
  自定义位置: "사용자 지정 위치",
  尚未检测: "확인 안 함",
  未发现: "찾을 수 없음",
  无法访问: "접근할 수 없음",
  位置无效: "잘못된 위치",
  "需要更新 Core": "Core 업데이트 필요",
  "候选位置不存在，可能尚无历史或路径不同。":
    "이 위치가 존재하지 않습니다. 아직 기록이 없거나 경로가 다를 수 있습니다.",
  "无法检查此位置，请核对目录访问权限后重试。":
    "이 위치를 확인할 수 없습니다. 디렉터리 접근 권한을 확인한 후 다시 시도하세요.",
  "此位置未通过安全检查，不能读取。":
    "이 위치는 보안 검사를 통과하지 못해 읽을 수 없습니다.",
  "当前 Core 未提供可确认的位置身份，需更新后再选择。":
    "현재 Core가 확인 가능한 위치 신원을 제공하지 않습니다. 선택하기 전에 업데이트하세요.",
  暂不支持历史导入: "기록 가져오기가 아직 지원되지 않습니다",
  "当前来源不可读取，请重新检测。":
    "현재 출처를 읽을 수 없습니다. 다시 확인하세요.",
  "来源目录暂时不可读取，请核对位置和权限。":
    "출처 디렉터리를 일시적으로 읽을 수 없습니다. 위치와 권한을 확인하세요.",
  "来源目录身份已变化，请返回重新检测。":
    "출처 디렉터리 신원이 변경되었습니다. 돌아가서 다시 확인하세요.",
  "来源权限已变化，请返回重新确认。":
    "출처 권한이 변경되었습니다. 돌아가서 다시 확인하세요.",
  "此来源尚未获得读取授权。": "이 출처에는 아직 읽기 권한이 없습니다.",
  "自定义来源位置需要重新确认。":
    "사용자 지정 출처 위치를 다시 확인해야 합니다.",
  "当前 Core 未启用历史导入。":
    "현재 Core에서 기록 가져오기가 활성화되지 않았습니다.",
  onboardingImportRefused:
    "가져오기가 승인되지 않았습니다({reason}). 출처를 확인하고 다시 시도하세요.",
  确认自定义位置: "사용자 지정 위치 확인",
  关闭位置确认: "위치 확인 닫기",
  "此位置来自当前配置，并非应用默认目录。确认位置不会开始读取历史。":
    "이 위치는 앱의 기본 디렉터리가 아니라 현재 구성에서 가져온 것입니다. 위치를 확인해도 기록 읽기가 시작되지 않습니다.",
  onboardingConfirmHistory: "{name}의 기록 위치가 맞는지 확인",
  确认此位置: "이 위치 확인",
  "对话正在汇入，记忆从这里开始":
    "대화를 가져오는 중입니다. 기억은 여기서 시작됩니다",
  "导入进行中 · 顺手连接应用": "가져오는 중 · 앱 연결하기",
  "对话已保存，记忆尚待生成。":
    "대화를 저장했습니다. 기억은 아직 생성되지 않았습니다.",
  "导入在后台继续，无需等待。":
    "가져오기는 백그라운드에서 계속됩니다. 기다릴 필요가 없습니다.",
  "正在重新核对来源；已保存数量仍按任务回执显示。":
    "출처를 다시 확인하는 중입니다. 저장된 개수는 작업 기록을 기준으로 표시됩니다.",
  "本次历史导入已暂停，已保存的对话还在。":
    "이번 기록 가져오기가 일시 중지되었습니다. 저장된 대화는 그대로 있습니다.",
  "导入等待处理，已保存的对话还在。":
    "가져오기가 처리를 기다리고 있습니다. 저장된 대화는 그대로 있습니다.",
  停止后续操作: "남은 작업 중지",
  暂停导入: "가져오기 일시 중지",
  继续导入: "가져오기 재개",
  "配置模型，沉淀记忆": "모델을 구성하여 기억 생성",
  导入与原文: "가져오기 및 원문",
  "本次导入已结束。已保存的对话无需重复导入。":
    "이번 가져오기가 완료되었습니다. 저장된 대화는 다시 가져올 필요가 없습니다.",
  "对话先保存在本机。你可以同时设置后续连接。":
    "대화는 먼저 로컬에 저장됩니다. 그동안 연결을 설정할 수 있습니다.",
  "导入不会自动开放应用读取权限。配置模型后，才会开始生成记忆。":
    "가져오기로 앱에 읽기 권한이 자동 부여되지는 않습니다. 기억은 모델을 구성한 후에만 생성됩니다.",
  onboardingHistoryWindow:
    "대화 데이터가 많습니다. 사용량을 제한하고 앱 간 타임라인을 맞추기 위해 이번 가져오기에는 모든 앱의 최근 3일 대화만 포함됩니다.",
  导入说明: "가져오기 정보",
  正在导入你的会话: "대화를 가져오는 중",
  "后台将持续为你导入，可以放心进入下一步":
    "가져오기는 백그라운드에서 계속됩니다. 다음 단계로 진행하세요.",
  未开始的导入: "시작하지 못한 가져오기",
  "未能开始导入，其他已接受任务不受影响。":
    "가져오기를 시작하지 못했습니다. 이미 수락된 다른 작업에는 영향이 없습니다.",
  查看未导入的应用: "가져오지 않은 앱 보기",
  让记忆继续生长: "기억을 계속 키워 가세요",
  连接你的应用: "앱 연결하기",
  "适用于本次选择且支持的应用。": "이 연결을 지원하는 선택한 앱에 적용됩니다.",
  一键接入应用: "한 번에 앱 연결",
  "安装记忆连接，让所选应用用上 MemHub 中的相关记忆。":
    "선택한 앱이 MemHub의 관련 기억을 사용할 수 있도록 기억 연결을 설치합니다.",
  "当前使用独立测试数据目录，不会修改本机应用的连接配置。":
    "이 인스턴스는 격리된 테스트 데이터 디렉터리를 사용하며 로컬 앱 연결을 변경하지 않습니다.",
  "点击底部按钮后，后台分别接入各应用；某个应用失败不影响其他应用。":
    "아래 버튼을 클릭하면 백그라운드에서 앱이 개별적으로 연결됩니다. 하나가 실패해도 다른 앱에는 영향이 없습니다.",
  "暂时无法连接应用，当前不会安装连接。":
    "지금은 앱을 연결할 수 없습니다. 연결이 설치되지 않습니다.",
  onboardingBatchStopped:
    "남은 작업을 중지했습니다. 이미 보낸 작업은 기록을 확인하세요. ",
  onboardingBatchResult: "{confirmed}개 확인됨, {failed}개 미확인.",
  "已确认项保留，请核对未确认任务后重试。":
    " 확인된 작업은 유지됩니다. 다시 시도하기 전에 미확인 작업을 확인하세요.",
  本次应用导入进度: "선택한 앱의 가져오기 진행 상황",
  正在导入: "가져오는 중",
  已暂停: "일시 중지됨",
  已中断: "중단됨",
  导入遇到问题: "가져오기 중 문제 발생",
  导入完成: "가져오기 완료",
  已取消: "취소됨",
  已保存: "저장됨",
  onboardingAppProgress: "{name} 가져오기 진행 상황",
  onboardingSavedMessages: "메시지 {count}개 저장됨",
  onboardingScannedOutsideRange: "{count}개 항목 검사함, 기간에 맞는 대화 없음",
  onboardingScannedNothingToSave: "{count}개 항목 검사함, 저장할 대화 없음",
  onboardingScannedFilteringRecent: "{count}개 항목 검사함, 최근 3일 필터링 중",
  onboardingScannedFiltering: "{count}개 항목 검사함, 필터링 중",
  onboardingScannedNotSaved: "{count}개 항목 검사함, 아직 저장 안 됨",
  没有可保存的历史对话: "저장할 기록 대화가 없습니다",
  读取授权不可用: "읽기 권한을 사용할 수 없음",
  onboardingReadErrors: " · 읽기 오류 {count}개",
  "配置详情，按需要调整": "필요한 경우 구성 세부 정보",
  "连接未通过，配置已保留": "연결 실패, 구성은 유지됨",
  "选好模型，让记忆开始沉淀": "모델을 선택하여 기억 생성 시작",
  "连接设置已保存\n成功项保留，不重复执行。":
    "연결 설정을 저장했습니다.\n성공한 단계는 유지되며 반복되지 않습니다.",
  "已选择：持续同步 · 一键接入": "선택됨: 지속 동기화 · 한 번에 연결",
  "沉淀记忆 · 使用你选择的模型": "기억 생성 · 선택한 모델 사용",
  "当前无法启用模型，已填写内容保留。":
    "지금은 모델을 활성화할 수 없습니다. 입력한 내용은 유지됩니다.",
  "模型连接测试通过，可以启用并开始沉淀记忆。":
    "모델 연결에 성공했습니다. 활성화하여 기억을 생성할 수 있습니다.",
  "先测试模型连通性；测试通过后再启用。":
    "먼저 모델 연결을 테스트하고, 테스트를 통과한 후 활성화하세요.",
  "跳过，先进入应用": "건너뛰고 앱으로 이동",
  "正在测试模型…": "모델 테스트 중…",
  测试模型连通性: "모델 연결 테스트",
  "正在启用…": "활성화 중…",
  确认启用: "확인 및 활성화",
  核对提交状态: "제출 상태 확인",
  模型配置: "모델 구성",
  模型参数: "모델 매개변수",
  "选择服务商，填入 API Key。核对已有配置后再继续。":
    "제공업체를 선택하고 API 키를 입력하세요. 계속하기 전에 기존 구성을 확인하세요.",
  模型服务商: "모델 제공업체",
  选择服务商: "제공업체 선택",
  "已安全保存，留空保留": "안전하게 저장됨. 유지하려면 비워 두세요",
  "输入服务商提供的 API Key": "제공업체에서 받은 API 키 입력",
  "连接未通过，请检查 API Key、模型与接口地址后重试。配置已保留。":
    "연결에 실패했습니다. API 키, 모델, 엔드포인트를 확인한 후 다시 시도하세요. 구성은 유지됩니다.",
  "需要允许 MemHub 访问钥匙串。系统询问时请选择“始终允许”。":
    "MemHub가 키체인에 접근하도록 허용하세요. 메시지가 표시되면 ‘항상 허용’을 선택하세요.",
  "留空保留已保存密钥；填写新密钥后需重新测试。":
    "저장된 키를 유지하려면 비워 두세요. 새 키를 입력한 후에는 다시 테스트하세요.",
  "密钥保存在系统安全存储中，不写入对话或日志。":
    "키는 대화나 로그가 아닌 시스템 보안 저장소에 보관됩니다.",
  配置详情与高级设置: "구성 세부 정보 및 고급 설정",
  服务商待选择: "제공업체가 선택되지 않음",
  模型待填写: "모델이 입력되지 않음",
  本地向量模型: "로컬 임베딩 모델",
  向量模型待填写: "임베딩 모델이 입력되지 않음",
  思考已关闭: "사고 기능 꺼짐",
  思考未关闭: "사고 기능 켜짐",
  "预设已填好，只有自定义服务或特殊需求才需要修改。":
    "사전 설정이 입력되어 있습니다. 사용자 지정 서비스나 특별한 요구 사항이 있을 때만 변경하세요.",
  返回服务商选择: "제공업체 선택으로 돌아가기",
  模型名称: "모델 이름",
  "Anthropic（暂不支持）": "Anthropic(아직 지원되지 않음)",
  "HY 托管（当前不可用）": "HY 관리형(현재 사용 불가)",
  向量模型: "임베딩 모델",
  "本地 ONNX · 内置": "로컬 ONNX · 내장",
  "超时（秒）": "시간 제한(초)",
  最大并发: "최대 동시 실행 수",
  上下文窗口: "컨텍스트 창",
  "自动 / 可选": "자동 / 선택 사항",
  关闭思考模式: "사고 모드 끄기",
  "自定义向量配置可展开；远程向量服务须遵守凭据边界。":
    "사용자 지정 임베딩 설정을 펼칠 수 있습니다. 원격 임베딩 서비스는 자격 증명 경계를 준수해야 합니다.",
  "切换服务商或模型后，需要重新测试并启用。":
    "제공업체나 모델을 변경한 후에는 다시 테스트하고 활성화하세요.",
  向量模型名称: "임베딩 모델 이름",
  向量协议: "임베딩 프로토콜",
  "同源 OpenAI-compatible（共用 Key）": "동일 출처 OpenAI-compatible(키 공유)",
  "向量 Base URL": "임베딩 Base URL",
  "远程向量地址须与生成服务同源；本机地址仍需校验。":
    "원격 임베딩 엔드포인트는 생성 서비스와 출처(origin)가 같아야 하며, 로컬 엔드포인트도 검증이 필요합니다.",
  "放弃旧记录，重新配置": "이전 기록을 버리고 다시 구성",
  模型提交状态: "모델 제출 상태",
  "正在测试连接…": "연결 테스트 중…",
  "正在提交保存与启用，请等待回执…":
    "저장 및 활성화 중입니다. 확인을 기다리는 중…",
  "操作结果未确认，已填写内容保留，请核对后重试。":
    "결과가 확인되지 않았습니다. 입력한 내용은 유지됩니다. 다시 시도하기 전에 확인하세요.",
  本次沉淀: "이번 기억 설정",
  让记忆从这里开始: "여기서 기억 생성 시작",
  对话来源: "대화 출처",
  尚无已选来源: "선택된 출처 없음",
  onboardingSourcesSummary: "{names} 등 총 {count}개 앱",
  onboardingImportedConversations:
    "대화 {count}개를 가져왔습니다. 이후 이 기기로 동기화되는 내용은 출처 스위치로 계속 제어됩니다.",
  "处理服务：": "정리 서비스:",
  待选择: "선택 안 됨",
  "相关对话会发送给所选服务商。": "관련 대화가 선택한 제공업체로 전송됩니다.",
  "测试连接只发送测试内容。启用后会使用此模型整理已导入和之后同步的会话。":
    "연결 테스트는 테스트 내용만 보냅니다. 활성화하면 이 모델이 가져온 대화와 이후 동기화되는 대화를 정리합니다.",
  "当前不能在此启用模型。": "지금은 여기서 모델을 활성화할 수 없습니다.",
  "配置已修改，需要重新测试。": "구성이 변경되었습니다. 다시 테스트하세요.",
  onboardingIntegerRange: "{min}에서 {max} 사이의 정수를 입력하세요.",
  "请填写配置名称。": "구성 이름을 입력하세요.",
  "请填写实际可用的模型 ID。": "사용 가능한 모델 ID를 입력하세요.",
  "请填写有效的模型接口地址。": "올바른 모델 엔드포인트 URL을 입력하세요.",
  "请填写 API Key。": "API 키를 입력하세요.",
  "请填写向量模型 ID。": "임베딩 모델 ID를 입력하세요.",
  "配置结果尚未确认，请在模型设置中核对后重试。":
    "구성 결과가 확인되지 않았습니다. 다시 시도하기 전에 모델 설정에서 확인하세요.",
  "模型连接测试通过，可以启用。":
    "모델 연결 테스트를 통과했습니다. 활성화할 수 있습니다.",
  "连接未通过，请核对模型、密钥和接收方。":
    "연결에 실패했습니다. 모델, 키, 수신처를 확인하세요.",
  "配置或测试结果暂未确认，请核对后重试。":
    "구성 또는 테스트 결과를 확인하지 못했습니다. 다시 시도하기 전에 확인하세요.",
  "索引更新没有完成，仍在使用原来的搜索模型。请检查连接后重试。":
    "인덱스 업데이트가 완료되지 않았습니다. 이전 검색 모델을 계속 사용합니다. 연결을 확인한 후 다시 시도하세요.",
  "上次模型测试结果仍待核对，请稍后重试。":
    "이전 모델 테스트 결과를 아직 확인해야 합니다. 잠시 후 다시 시도하세요.",
  "模型写入暂时关闭。请等待索引维护完成后重试。":
    "모델 쓰기가 일시 중지되었습니다. 인덱스 유지 관리가 완료된 후 다시 시도하세요.",
  "模型未能确认启用，请在设置中核对。":
    "모델이 활성화되었는지 확인하지 못했습니다. 설정에서 확인하세요.",
  "已保存的模型配置暂时无法读取，不会重复创建。请重试刷新。":
    "저장된 모델 구성을 일시적으로 읽을 수 없습니다. 중복으로 생성되지 않습니다. 다시 새로 고쳐 보세요.",
  所选连接已启用: "선택한 앱 연결이 활성화되었습니다",
  本次未选择应用连接: "이번에 선택한 앱 연결 없음",
  尚未填写接收方: "수신처가 입력되지 않음",
  本机内置向量模型: "내장 로컬 임베딩 모델",
  "操作未确认，草稿已保留。": "작업을 확인하지 못했습니다. 초안은 유지됩니다.",
  "操作已完成，但配置列表暂未刷新。已保留操作回执，请刷新核对，不要重复保存。":
    "작업은 완료되었지만 구성 목록이 새로 고쳐지지 않았습니다. 기록은 유지되니 새로 고쳐 확인하세요. 다시 저장하지 마세요.",
  "暂时无法刷新模型配置，已确认的配置仍保留。请重试刷新。":
    "모델 구성을 새로 고치지 못했습니다. 확인된 구성은 유지됩니다. 다시 새로 고쳐 보세요.",
  "未找到上次创建记录。确认放弃旧记录后可重新配置；系统不会自动重试。":
    "이전 생성 기록을 찾을 수 없습니다. 다시 구성하려면 기록을 버리세요. 자동으로 다시 시도하지 않습니다.",
  "创建结果仍待核对，不会重复创建。请稍后再次核对。":
    "생성 결과를 아직 확인해야 합니다. 중복 생성되지 않으니 나중에 다시 확인하세요.",
  "已发现模型配置，旧创建记录仍保留。请先核对已有配置。":
    "모델 구성을 찾았습니다. 이전 생성 기록은 유지됩니다. 먼저 기존 구성을 확인하세요.",
  "旧创建记录尚未安全解除，配置仍保持锁定。请重新核对。":
    "이전 생성 기록이 안전하게 정리되지 않았습니다. 구성은 잠긴 상태로 유지됩니다. 다시 확인하세요.",
  "配置未保存，请检查模型地址和参数后重试；已填写内容保留。":
    "구성이 저장되지 않았습니다. 다시 시도하기 전에 모델 엔드포인트와 설정을 확인하세요. 입력한 내용은 유지됩니다.",
  "创建结果尚未确认，请核对已保存结果，不要重复提交。":
    "생성 결과가 확인되지 않았습니다. 저장된 결과를 확인하고 다시 제출하지 마세요.",
  "模型配置已变化，旧测试结果不会用于启用，请重新核对配置。":
    "모델 구성이 변경되었습니다. 이전 테스트 결과는 활성화에 사용되지 않으니 구성을 다시 확인하세요.",
  "暂时无法核对测试服务，本次未发送测试，请恢复连接后重试。":
    "테스트 서비스를 확인하지 못했습니다. 테스트를 보내지 않았습니다. 연결을 복구한 후 다시 시도하세요.",
  "另一个模型的测试结果尚未确认，请先在该模型上核对测试结果。":
    "다른 모델의 테스트 결과가 확인되지 않았습니다. 먼저 해당 모델의 결과를 확인하세요.",
  "测试结果尚未确认。再次点击测试连接将核对原操作，不会重复发送测试。":
    "테스트 결과가 확인되지 않았습니다. 연결 테스트를 다시 클릭하면 테스트를 다시 보내지 않고 원래 작업을 확인합니다.",
  "请先点击测试连接，通过后再设为当前模型。":
    "먼저 연결을 테스트하고, 통과한 후에 현재 모델로 설정하세요.",
  "这是当前正在使用的模型，请先切换到别的模型再删除。":
    "현재 사용 중인 모델입니다. 삭제하기 전에 다른 모델로 전환하세요.",
  "模型配置操作未成功，请核对输入后重试。":
    "모델 구성 작업에 실패했습니다. 입력 내용을 확인하고 다시 시도하세요.",
  "它没有 MCP 通道，无法发放可撤销的记忆凭证":
    "이 앱에는 취소 가능한 기억 자격 증명을 위한 MCP 채널이 없습니다",
  当前安装不支持一键插入: "이 설치에서는 원클릭 연결이 지원되지 않습니다",
  桌面端的连接组件不可用: "데스크톱 연결 구성 요소를 사용할 수 없습니다",
  安装后没有拿到安装回执: "설치 확인 정보가 반환되지 않았습니다",
  记忆使用权限没有获批: "기억 접근 권한이 승인되지 않았습니다",
  配置文件被改过: "구성 파일이 수정되었습니다",
  "有无法确认的残留，需要人工核对": "일부 잔여 항목을 직접 확인해야 합니다",
  权限存储暂不可用: "권한 저장소를 사용할 수 없습니다",
  "请先把 MemHub 拖到“应用程序”文件夹，再接入应用":
    "앱을 연결하기 전에 MemHub를 응용 프로그램 폴더로 옮기세요",
  本机运行时暂不可用: "로컬 런타임을 사용할 수 없습니다",
  桌面端读取接入状态超时: "데스크톱 연결 상태 읽기 시간이 초과되었습니다",
  "apps.apps": "앱",
  "apps.allApps": "모든 앱",
  "apps.home": "앱 홈",
  "apps.back": "홈으로 돌아가기",
  "apps.overview": "기억 개요",
  "apps.memories": "저장된 기억",
  "apps.rawConversations": "원본 대화",
  "apps.rawSaving": "저장 중",
  "apps.rawSavingCompact": "저장 중",
  "apps.rawSavingStatus": "저장 상태",
  "apps.rawSavingDetail": "생성 모델 없이 원본 대화를 저장합니다",
  "apps.rawNoExcerpt": "표시할 수 있는 사용자 원문이 없습니다",
  "apps.rawConversation": "원본 대화",
  "apps.rawSaved": "원본 대화 저장됨",
  "apps.rawEmpty": "저장된 원본 대화가 아직 없습니다",
  "apps.rawCheckedEmpty": "확인됨: 표시할 원본 대화 업데이트가 없습니다",
  "apps.memoryCheckedEmpty": "확인됨: 표시할 메모리 업데이트가 없습니다",
  "apps.recentEmptyCompact": "업데이트 없음",
  "apps.rawWaiting": "저장된 원본 대화가 여기에 표시됩니다",
  "apps.checkedRecent": "현재 내용 확인됨",
  "apps.currentMode": "현재 모드",
  "apps.mode.lite": "기본 메모리 (lite)",
  "apps.mode.pro": "스마트 메모리 (pro)",
  "apps.mode.ultra": "심층 메모리 (ultra)",
  "apps.readingCount": "로컬 기억 확인 중…",
  "apps.rawReadingCount": "원본 대화 확인 중…",
  "apps.startingRetry":
    "백그라운드 서비스를 시작하는 중입니다. 자동으로 업데이트됩니다",
  "apps.countWillUpdate": "기억 개수는 자동으로 업데이트됩니다",
  "apps.rawCountWillUpdate": "원본 대화 수는 자동으로 업데이트됩니다",
  "apps.showingPrevious":
    "이전 데이터를 표시하는 중입니다. 자동으로 업데이트됩니다",
  "apps.unreadable": "사용할 수 없음",
  "apps.unit": "개",
  "apps.scope": "앱 권한 범위 내에서 사용",
  "apps.processing": "정리 상태",
  "apps.running": "정리 중",
  "apps.paused": "일시 중지됨",
  "apps.stoppedNew": "새 기억을 생성하지 않는 중",
  "apps.checkingProcessing": "정리 상태 확인 중",
  "apps.checkingStatus": "확인 중",
  "apps.unchecked": "확인 필요",
  "apps.organizingNew": "새 대화 정리 중",
  "apps.recentUpdate": "최근 업데이트",
  "apps.checkingRecent": "확인 중",
  "apps.recentWillUpdate": "최신 업데이트가 자동으로 표시됩니다",
  "apps.notGenerated": "아직 생성되지 않음",
  "apps.saved": "기억 저장됨",
  "apps.separate": "기록과 기억은 따로 정리됩니다",
  "apps.recentCall": "최근 호출",
  "apps.activityUnavailable": "활동을 확인할 수 없음",
  "apps.firstBatch": "첫 기억",
  "apps.callsUnavailable": "호출 기록을 읽지 못했습니다",
  "apps.memoryUnavailable": "기억 개요가 아직 준비되지 않았습니다",
  "apps.importing": "기억을 생성하기 전에 대화를 가져오는 중",
  "apps.firstRunning": "첫 기억 생성 중",
  "apps.firstQueued": "첫 기억 준비 중",
  "apps.readingActivity": "기억 개요 준비 중",
  "apps.emptyMemories": "아직 생성된 기억이 없습니다",
  "apps.callReadFailure":
    "저장된 기억은 계속 볼 수 있습니다. 다시 시도해도 새 호출이 발생하지 않습니다.",
  "apps.memoryReadFailure":
    "서비스가 복구되면 업데이트됩니다. 지금 다시 시도할 수도 있습니다.",
  "apps.savingConversations": "대화가 백그라운드에서 계속 저장되고 있습니다.",
  "apps.waitingMemories": "기록이 저장되었습니다. 새 기억이 여기에 표시됩니다.",
  "apps.reread": "다시 시도",
  "apps.viewCalls": "기록 보기",
  "apps.viewMemory": "기억 보기",
  "apps.backgroundDown":
    "백그라운드 서비스가 실행 중이 아니어서 지금은 앱이 기억을 조회할 수 없습니다",
  "apps.restart": "다시 시작",
  "apps.runtime": "실행 상태",
  "apps.backgroundRunning": "백그라운드에서 실행 중",
  "apps.connectionUnchecked": "연결 상태 확인 필요",
  "apps.disconnected": "서비스 연결 끊김",
  "apps.authorizedOnly": "승인된 내용만 정리",
  "apps.sync": "대화 동기화",
  "apps.organizing": "기억 정리",
  "apps.access": "앱 접근",
  "apps.checkingAccess": "앱 연결 확인",
  "apps.checking": "확인 중…",
  "apps.recheck": "다시 확인",
  "apps.accessState": "앱 연결 상태",
  "apps.staleAccess":
    "앱 연결 상태를 읽지 못했습니다. 마지막으로 확인된 상태를 표시합니다.",
  "apps.reconnect": "다시 연결",
  "apps.myApps": "내 앱",
  "apps.list": "앱 목록",
  "apps.syncEnabled": "지속 동기화 켜짐",
  "apps.recallAllowed": "승인된 기억 요청 가능",
  "apps.historyAllowed": "기록 접근 승인됨",
  "apps.connectionPending": "연결 미완료",
  "apps.details": "세부 정보 보기",
  "apps.listUnavailable": "앱 목록을 사용할 수 없음",
  "apps.emptyApps": "아직 표시할 앱이 없습니다",
  "apps.checkingNow": "확인 중",
  "apps.checkingState": "상태 확인 중",
  "apps.notEnabled": "활성화 안 됨",
  "apps.enableFailed": "활성화하지 못함",
  "apps.unknownTime": "시간 알 수 없음",
  "apps.today": "오늘",
  "apps.newMemory": "새 기억",
  "apps.updatedMemory": "업데이트된 기억",
  "apps.unlinked": "아직 연결 안 됨",
  "apps.lastRead": "마지막 읽기: ",
  "apps.recentSync": "마지막 동기화 {time}",
  "apps.connectedCount": "앱 {count}개 연결됨",
  "apps.connectedCount.one": "앱 {count}개 연결됨",
  "apps.pendingCount": "확인이 필요한 앱 {count}개",
  "apps.pendingCount.one": "확인이 필요한 앱 {count}개",
  "apps.mixedCount": "앱 {count}개 연결됨 · 확인 대기: {pending}",
  "apps.mixedCount.one": "앱 {count}개 연결됨 · 확인 대기: {pending}",
  "apps.unknownAccess":
    "앱 연결 상태를 사용할 수 없어 연결된 앱 수를 확인할 수 없습니다.",
  "apps.unknownPermissions":
    "{names}의 기억 권한을 읽지 못했습니다. 연결 상태가 확인되지 않았습니다.",
  "apps.enabledCount": "앱 {count}개 활성화됨",
  "apps.enabledCount.one": "앱 {count}개 활성화됨",
  "apps.appCount": "앱 {count}개",
  "apps.appCount.one": "앱 {count}개",
  "apps.viewAll": "앱 {count}개 모두 보기",
  "apps.viewAll.one": "앱 {count}개 모두 보기",
  "apps.checkedAt": "마지막 확인: {time}",
  "apps.revision": "버전 {count}",
  "apps.conversationTime": "대화 시간",
  "apps.importTime": "가져온 시간",
  "apps.writeTime": "기록 시간",
  "apps.updateTime": "업데이트 시간",
  "apps.incompleteCalls":
    " · 일부 호출이 기록되지 않아 목록이 불완전할 수 있습니다",
  "apps.timeoutWarning":
    "{host}이(가) 최근 기억을 조회하지 못했습니다. 백그라운드 서비스가 바쁠 수 있으며 자동으로 복구됩니다.",
  "apps.autoHostFailed": "{name}을(를) 자동으로 연결하지 못했습니다{reason}.",
  "apps.failureReason": ": {reason}",
  "apps.autoSyncFailed":
    "{name}의 지속 동기화를 자동으로 켜지 못했습니다. {reason}",
  "apps.noAutoRetry": "자동으로 다시 시도하지 않습니다.",
  "apps.cardUnavailable":
    "{label}을(를) 일시적으로 사용할 수 없습니다. 다른 기능에는 영향이 없으며 자동으로 복구됩니다.",
  "apps.navMemories": "기억",
  "apps.navigation": "기본 탐색",
  "apps.skip": "본문으로 건너뛰기",
  "apps.local": "로컬에 저장",
  "apps.localHint":
    "기억은 이 기기에 저장됩니다. 클라우드 모델이나 승인된 어시스턴트를 사용할 때는 관련 내용이 해당 서비스로 전송됩니다.",
  "memory:记忆抽取": "기억 추출",
  "memory:关系归纳": "관계 종합",
  "memory:深度整理": "심층 정리",
  "memory:跨域整理": "교차 영역 정리",
  "memory:当前模型已变化": "현재 모델이 변경됨",
  "memory:本地会话内容已变化": "로컬 대화 내용이 변경됨",
  "memory:这段对话含过长原文，暂未整理；原文已保留":
    "이 대화에 너무 긴 텍스트가 있어 정리할 수 없습니다. 원문은 보존됩니다",
  "memory:记忆库分页": "기억 저장소 페이지",
  "memory:上一页": "이전 페이지",
  "memory:下一页": "다음 페이지",
  "memory:等待可用生成模型": "사용 가능한 생성 모델 대기 중",
  "memory:还没有启用生成模型": "아직 활성화된 생성 모델이 없습니다",
  "memory:缺少生成模型 API Key": "생성 모델 API 키가 없습니다",
  "memory:模型凭据暂不可用": "모델 자격 증명을 일시적으로 사용할 수 없습니다",
  "memory:本机凭据存储暂不可用":
    "로컬 자격 증명 저장소를 일시적으로 사용할 수 없습니다",
  "memory:需要授权访问本机钥匙串。请重新打开 App 并完成一次授权":
    "키체인 접근 승인이 필요합니다. 앱을 다시 열고 접근을 허용하세요.",
  "memory:读取本机钥匙串超时。重新保存一遍模型配置即可恢复":
    "키체인 읽기 시간이 초과되었습니다. 모델 구성을 다시 저장하면 복구됩니다.",
  "memory:写入本机钥匙串超时。重新保存一遍模型配置即可恢复":
    "키체인 쓰기 시간이 초과되었습니다. 모델 구성을 다시 저장하면 복구됩니다.",
  "memory:模型认证失败。更新 key 后，到设置里点一次检测，会自动接着处理":
    "모델 인증에 실패했습니다. 키를 업데이트하고 설정에서 확인을 실행하면 정리가 재개됩니다.",
  "memory:模型账户余额不足。充值或换 key 后，到设置里点一次检测，会自动接着处理":
    "모델 계정 잔액이 부족합니다. 충전하거나 키를 변경한 후 설정에서 확인을 실행하면 정리가 재개됩니다.",
  "memory:模型服务拒绝访问。更新权限后，到设置里点一次检测，会自动接着处理":
    "모델 서비스가 접근을 거부했습니다. 권한을 업데이트하고 설정에서 확인을 실행하면 정리가 재개됩니다.",
  "memory:模型服务限流": "모델 서비스 요청 한도 초과",
  "memory:模型服务暂不可用": "모델 서비스를 일시적으로 사용할 수 없음",
  "memory:模型服务响应超时": "모델 서비스 응답 시간 초과",
  "memory:模型请求不兼容，请核对配置":
    "모델 요청이 호환되지 않습니다. 구성을 확인하세요",
  "memory:模型请求未成功": "모델 요청 실패",
  "memory:HY Memory 不可用": "HY Memory를 사용할 수 없음",
  "memory:HY Memory 启动失败": "HY Memory를 시작하지 못함",
  "memory:暂时没能整理，稍后会自动重试":
    "아직 정리하지 못했습니다. 자동으로 다시 시도합니다",
  "memory:这次没能整理成功，正在重试":
    "이번에는 정리하지 못했습니다. 다시 시도하는 중",
  "memory:生成模型换过了，这批会重新整理":
    "생성 모델이 변경되어 이 항목들을 다시 정리합니다",
  "memory:整理方式换过了，这批会重新整理":
    "정리 설정이 변경되어 이 항목들을 다시 정리합니다",
  "memory:已提炼": "추출됨",
  "memory:旧版确认记录": "이전 버전 확인 기록",
  "memory:创建": "생성",
  "memory:纠正": "수정",
  "memory:调整使用范围": "접근 범위 변경",
  "memory:撤销修改": "변경 실행 취소",
  "memory:模型更新": "모델 업데이트",
  "memory:被新记忆取代": "새 기억으로 대체됨",
  "memory:其他操作": "기타 작업",
  "memory:删除（记录）": "삭제",
  "memory:修改已保存，但读取最新列表失败。已停止使用旧回答，请重新读取。":
    "변경 사항은 저장되었지만 최신 목록을 불러오지 못했습니다. 이전 답변은 더 이상 사용되지 않으니 목록을 다시 불러오세요.",
  "memory:纠正已保存，但读取最新列表失败。已停止使用旧回答，请重新读取。":
    "수정 사항은 저장되었지만 최신 목록을 불러오지 못했습니다. 이전 답변은 더 이상 사용되지 않으니 목록을 다시 불러오세요.",
  "memory:未收到操作结果，请重试。": "결과를 받지 못했습니다. 다시 시도하세요.",
  "memory:批量处理结果已保留，但列表刷新失败，请重新读取记忆库。":
    "일괄 작업 결과는 유지되지만 목록을 새로 고치지 못했습니다. 기억 저장소를 다시 불러오세요.",
  "memory:批量处理结果已保留，但列表刷新失败，稍后会自动重新读取。":
    "일괄 작업 결과는 유지되지만 목록을 새로 고치지 못했습니다. 나중에 자동으로 다시 불러옵니다.",
  "memory:召回范围已保存，但列表刷新失败。请稍后重新打开记忆页。":
    "조회 범위는 저장되었지만 목록을 새로 고치지 못했습니다. 나중에 기억 페이지를 다시 여세요.",
  "memory:原始对话暂时无法读取": "원본 대화를 불러오지 못했습니다",
  "memory:没有匹配的原始对话": "일치하는 원본 대화가 없습니다",
  "memory:还没有导入的原始对话": "아직 가져온 원본 대화가 없습니다",
  "memory:查看已读内容": "저장된 내용 보기",
  "memory:已保存原文": "원문 저장됨",
  "memory:刷新已读内容": "저장된 내용 새로 고침",
  "memory:正在核对本机已保存记录…": "저장된 로컬 기록 확인 중…",
  "memory:原始消息": "원문 메시지",
  "memory:会话": "대화",
  "memory:用户发言": "사용자 메시지",
  "memory:Agent 发言": "에이전트 메시지",
  "memory:作者未知": "알 수 없는 작성자",
  "memory:原始时间未知": "원래 시간 알 수 없음",
  "memory:工具记录": "도구 기록",
  "memory:原始时间来自已保存记录。":
    "원래 시간은 저장된 기록을 기준으로 합니다.",
  "memory:已保存的原文样例": "저장된 원문 예시",
  "memory:最多展示 3 段，不代表全部历史或推荐记忆。":
    "최대 3개까지 표시되며, 전체 기록이나 추천 기억이 아닙니다.",
  "memory:查看完整对话": "전체 대화 보기",
  "memory:原文较长，此处仅展示前 600 个字符。":
    "원문이 길어 처음 600자만 표시합니다.",
  "memory:这个来源还没有保存原文。": "이 출처에서 저장된 원문이 아직 없습니다.",
  "memory:完整对话": "전체 대화",
  "memory:关闭完整对话": "전체 대화 닫기",
  "memory:用户": "사용자",
  "memory:暂时无法读取已保存内容，旧回执已收起。请检查授权或重试。":
    "저장된 내용을 읽지 못했습니다. 이전 기록은 닫혔습니다. 권한을 확인하거나 다시 시도하세요.",
  "memory:这段原文暂不可读，可能已被撤回授权。请刷新回执。":
    "이 원문을 사용할 수 없습니다. 접근 권한이 취소되었을 수 있습니다. 기록을 새로 고치세요.",
  "memory:外部记忆召回范围": "외부 기억 조회 범위",
  "memory:外部记忆召回": "외부 기억 조회",
  "memory:调整召回范围": "조회 범위 조정",
  "memory:所有关联工具": "연결된 모든 도구",
  "memory:已停止外部记忆召回": "외부 기억 조회 중지됨",
  "memory:原有限定范围": "이전 제한 범위",
  "memory:选择召回范围": "조회 범위 선택",
  "memory:停止外部记忆召回": "외부 기억 조회 중지",
  "memory:正在保存…": "저장 중…",
  "memory:保存召回范围": "조회 범위 저장",
  "memory:取消范围修改": "범위 변경 취소",
  "memory:原文检索仍按来源授权；已带出的内容不会撤回。":
    "원문 검색은 계속 출처 권한을 따릅니다. 이미 제공된 내용은 회수되지 않습니다.",
  "memory:记忆已更新，请重新打开这条记忆后再调整范围。":
    "기억이 변경되었습니다. 범위를 조정하기 전에 다시 여세요.",
  "memory:范围修改未提交，当前范围保持不变。请修正后重试。":
    "범위 변경이 제출되지 않았습니다. 현재 범위는 변경되지 않았습니다. 문제를 해결한 후 다시 시도하세요.",
  "memory:保存结果尚未确认，请重试。":
    "저장 결과가 확인되지 않았습니다. 다시 시도하세요.",
  "memory:原话已保存在本机；当前内容、模型接收方或授权版本尚未通过核对，未继续整理。":
    "원문은 이 기기에 저장되었지만 내용, 모델 수신처 또는 권한 버전이 확인을 통과하지 못해 정리를 계속하지 않았습니다.",
  "memory:HY 未返回可核对的已有记忆，未创建替代内容；原始记录已保留，可重试处理。":
    "HY가 확인 가능한 기존 기억을 반환하지 않아 대체 내용을 만들지 않았습니다. 원본 기록은 유지되며 다시 정리할 수 있습니다.",
  "memory:历史引用尚未绑定到原始来源，当前批次未完成，原话已保留。":
    "과거 인용이 아직 원본 출처에 연결되지 않았습니다. 이 묶음은 완료되지 않았으며 원문은 유지됩니다.",
  "memory:记忆内容未通过来源与作者核验，当前批次未完成，原话已保留。":
    "기억 내용이 출처 및 작성자 검증을 통과하지 못했습니다. 이 묶음은 완료되지 않았으며 원문은 유지됩니다.",
  "memory:所选记忆已不可读取，请重新读取记忆库。":
    "선택한 기억을 더 이상 읽을 수 없습니다. 기억 저장소를 다시 불러오세요.",
  "memory:还没连上本地 Core，正在重试…":
    "로컬 Core가 아직 연결되지 않았습니다. 다시 시도하는 중…",
  "memory:记忆内容或使用权限已变化，回答已停止。请重新提问。":
    "기억 내용 또는 접근 권한이 변경되어 답변이 중지되었습니다. 다시 질문하세요.",
  "memory:这条记忆的来源授权已撤回，暂时不能读取。":
    "이 기억의 출처 접근 권한이 취소되어 지금은 읽을 수 없습니다.",
  "memory:记忆已被更新，请刷新后重试。":
    "이 기억이 업데이트되었습니다. 새로 고친 후 다시 시도하세요.",
  "memory:已删除的记忆不能修改。": "삭제된 기억은 편집할 수 없습니다.",
  "memory:这条记忆已经删除。": "이 기억은 삭제되었습니다.",
  "memory:这条记忆已被更新的记忆取代，只能查看或删除。":
    "이 기억은 업데이트된 기억으로 대체되어 보기 또는 삭제만 할 수 있습니다.",
  "memory:没有更早的修改可以撤销。": "실행 취소할 이전 변경 사항이 없습니다.",
  "memory:来源原话已不存在，无法建立这条记忆。":
    "원본 출처 텍스트가 더 이상 존재하지 않아 이 기억을 생성할 수 없습니다.",
  "memory:这次操作与已完成的请求不一致。":
    "이 작업이 완료된 요청과 일치하지 않습니다.",
  "memory:输入内容不符合要求。": "입력 내용이 요구 사항에 맞지 않습니다.",
  "memory:本地记忆库正忙，请稍后重试。":
    "로컬 기억 저장소가 사용 중입니다. 나중에 다시 시도하세요.",
  "memory:记忆": "기억",
  "memory:记忆空间": "기억 공간",
  "memory:全部应用": "모든 앱",
  "memory:来源": "출처",
  "memory:主题": "주제",
  "memory:归纳": "종합",
  "memory:全部记忆": "모든 기억",
  "memory:来源待核对": "출처 확인 필요",
  "memory:所选来源待核对": "선택한 출처 확인 필요",
  "memory:未识别来源": "확인되지 않은 출처",
  "memory:这个来源下还没有记忆": "이 출처의 기억이 아직 없습니다",
  "memory:只在最相关的 {count} 条记忆里查找关键词":
    "가장 관련성 높은 기억 {count}개에서만 키워드를 찾습니다",
  "memory:未知来源": "알 수 없는 출처",
  "memory:记忆读取失败": "기억을 불러오지 못했습니다",
  "memory:正在读取…": "불러오는 중…",
  "memory:正在更新…": "업데이트 중…",
  "memory:读取失败": "불러오지 못함",
  "memory:正在核对来源…": "출처 확인 중…",
  "memory:正在导入历史会话，记忆将陆续出现":
    "이전 대화를 가져오는 중입니다. 기억이 점차 나타납니다",
  "memory:记忆工作台": "기억 워크스페이스",
  "memory:来源与主题": "출처 및 주제",
  "memory:记忆或原始对话": "기억 또는 원본 대화",
  "memory:原始对话": "원본 대화",
  "memory:地图组织方式": "지도 그룹화",
  "memory:筛选地图记忆": "지도 기억 필터",
  "memory:搜索原始对话": "원본 대화 검색",
  "memory:搜索记忆": "기억 검색",
  "memory:筛选记忆…": "기억 필터…",
  "memory:向记忆提问": "기억에 질문하기",
  "memory:询问全部已授权可用记忆…": "승인된 사용 가능한 모든 기억에 질문…",
  "memory:发送问题": "질문 보내기",
  "memory:刷新地图记忆": "지도 기억 새로 고침",
  "memory:关闭新增记忆提示": "새 기억 알림 닫기",
  "memory:地图记忆列表": "지도 기억 목록",
  "memory:记忆读取失败，请重试。":
    "기억을 불러오지 못했습니다. 다시 시도하세요.",
  "memory:重新读取地图记忆": "지도 기억 다시 불러오기",
  "memory:没有匹配的记忆": "일치하는 기억이 없습니다",
  "memory:本页没有匹配的主题记忆": "이 페이지에 일치하는 주제 기억이 없습니다",
  "memory:还没有已生成的记忆": "아직 생성된 기억이 없습니다",
  "memory:地图记忆分页": "지도 기억 페이지",
  "memory:上一页记忆": "이전 기억 페이지",
  "memory:下一页记忆": "다음 기억 페이지",
  "memory:记忆关系图": "기억 그래프",
  "memory:记忆关联": "기억 연결",
  "memory:刚导入的原话，还不是沉淀后的记忆":
    "새로 가져온 원문으로, 아직 기억이 아닙니다",
  "memory:来源归属": "출처 연결",
  "memory:可在沉淀记忆后查看视图": "기억을 생성하면 그래프를 볼 수 있습니다",
  "memory:正在读取记忆地图…": "기억 지도를 불러오는 중…",
  "memory:查看选中来源原文": "선택한 출처 원문 보기",
  "memory:查看选中记忆原文": "선택한 기억 원문 보기",
  "memory:查看原文": "원본 대화 보기",
  "memory:整理已有关系": "기존 연결 구축",
  "memory:正在读取原文…": "원본 대화를 불러오는 중…",
  "memory:选择一条原始对话": "원본 대화 선택",
  "memory:记忆暂时无法读取": "기억을 일시적으로 사용할 수 없습니다",
  "memory:当前筛选没有可显示的记忆": "이 필터에 표시할 기억이 없습니다",
  "memory:正在读取记忆…": "기억을 불러오는 중…",
  "memory:已保存的对话还未生成记忆":
    "저장된 대화가 아직 기억으로 생성되지 않았습니다",
  "memory:还没有可显示的记忆": "아직 표시할 기억이 없습니다",
  "memory:连线表示来源归属": "선은 출처 연결을 나타냅니다",
  "memory:记忆详情": "기억 세부 정보",
  "memory:来源原文": "출처 원문",
  "memory:关联节点": "관련 노드",
  "memory:关闭记忆详情": "기억 세부 정보 닫기",
  "memory:关闭详情": "세부 정보 닫기",
  "memory:正在读取这条记忆…": "이 기억을 불러오는 중…",
  "memory:这条记忆的原文暂时无法读取，选择已保留。":
    "이 기억의 원문을 불러오지 못했습니다. 선택은 유지됩니다.",
  "memory:重试读取原文": "대화 다시 불러오기",
  "memory:已沉淀的记忆": "생성된 기억",
  "memory:正在读取记忆详情…": "기억 세부 정보를 불러오는 중…",
  "memory:沉淀记忆后查看详情": "기억을 생성하면 세부 정보를 볼 수 있습니다",
  "memory:时间未知": "시간 알 수 없음",
  "memory:来源原文已不可读取": "출처 원문을 사용할 수 없습니다",
  "memory:关联记忆": "관련 기억",
  "memory:正在读取完整对话…": "전체 대화를 불러오는 중…",
  "memory:重试读取完整对话": "전체 대화 다시 불러오기",
  "memory:看完整对话": "전체 대화 보기",
  "memory:今天": "오늘",
  "memory:你": "나",
  "memory:助手": "어시스턴트",
  "memory:工具": "도구",
  "memory:未知": "알 수 없음",
  "memory:作者待核对": "작성자 확인 필요",
  "memory:你说": "내가 한 말",
  "memory:助手原话": "어시스턴트 원문",
  "memory:已沉淀": "생성됨",
  "memory:已删除": "삭제됨",
  "memory:已被取代": "대체됨",
  "memory:状态待核对": "상태 확인 필요",
  "memory:编辑": "편집",
  "memory:原始依据": "원본 근거",
  "memory:这段原文当前不可查看。": "이 원문은 현재 볼 수 없습니다.",
  "memory:版本记录": "버전 기록",
  "memory:更多管理": "추가 작업",
  "memory:撤销上次修改": "마지막 변경 실행 취소",
  "memory:删除记忆": "기억 삭제",
  "memory:编辑记忆": "기억 편집",
  "memory:返回记忆": "기억으로 돌아가기",
  "memory:修改这条记忆，原始对话保持不变。":
    "이 기억을 편집합니다. 원본 대화는 변경되지 않습니다.",
  "memory:记忆内容": "기억 내용",
  "memory:使用范围保持不变": "접근 범위는 변경되지 않습니다",
  "memory:已有新版本": "최신 버전이 있습니다",
  "memory:你的草稿已保留，先查看新版本再决定如何修改。":
    "초안은 유지됩니다. 변경하기 전에 새 버전을 확인하세요.",
  "memory:查看新版本": "새 버전 보기",
  "memory:基于此版本继续编辑": "이 버전에서 계속 편집",
  "memory:取消": "취소",
  "memory:保存": "저장",
  "memory:重试": "다시 시도",
  "memory:删除": "삭제",
  "memory:对话": "대화",
  "memory:工作": "업무",
  "memory:编码": "코딩",
  "memory:保存修改": "변경 사항 저장",
  "memory:对应记忆": "관련 기억",
  "memory:查看记忆": "기억 보기",
  "memory:删除这条记忆？": "이 기억을 삭제하시겠습니까?",
  "memory:删除后不再用于新的召回，来源 App 中的原始对话不会删除。":
    "새 조회에 더 이상 사용되지 않습니다. 출처 앱의 원본 대화는 삭제되지 않습니다.",
  "memory:已经发送给应用的内容无法远程收回。":
    "이미 앱으로 보낸 내용은 원격으로 회수할 수 없습니다.",
  "memory:放弃尚未保存的修改？": "저장하지 않은 변경 사항을 버리시겠습니까?",
  "memory:来源权限已变化或原文不可用。":
    "출처 권한이 변경되었거나 원문을 사용할 수 없습니다.",
  "memory:暂时无法读取新版本，草稿已保留。":
    "새 버전을 불러오지 못했습니다. 초안은 유지됩니다.",
  "memory:修改未提交；草稿已保留。请按提示修正后重试。":
    "변경 사항이 제출되지 않았습니다. 초안은 유지됩니다. 문제를 해결한 후 다시 시도하세요.",
  "memory:修改尚未确认，草稿已保留。请先刷新核对，避免重复提交。":
    "변경 사항이 확인되지 않았습니다. 초안은 유지됩니다. 중복 제출을 피하려면 다시 시도하기 전에 새로 고쳐 확인하세요.",
  "memory:删除未提交。请核对这条记忆的状态后重试。":
    "삭제가 제출되지 않았습니다. 이 기억의 상태를 확인한 후 다시 시도하세요.",
  "memory:删除尚未确认。请重新核对记忆状态，不会自动扩大删除范围。":
    "삭제가 확인되지 않았습니다. 기억 상태를 확인하세요. 삭제 범위는 자동으로 확대되지 않습니다.",
  "memory:撤销未提交。请按提示修正后重试。":
    "실행 취소가 제출되지 않았습니다. 문제를 해결한 후 다시 시도하세요.",
  "memory:撤销尚未确认，请先核对当前记忆版本，避免重复撤销。":
    "실행 취소가 확인되지 않았습니다. 두 번 실행 취소하지 않도록 다시 시도하기 전에 현재 기억 버전을 확인하세요.",
  "memory:添加记忆": "기억 추가",
  "memory:关闭新增记忆": "기억 추가 대화 상자 닫기",
  "memory:关闭": "닫기",
  "memory:记下这件事": "기록해 두기",
  "memory:例如：项目周会不排在周五。":
    "예: 프로젝트 회의는 금요일에 잡지 않습니다.",
  "memory:整理模型：": "정리 모델: ",
  "memory:尚未配置，原话仍可保存": "구성되지 않음. 원문은 저장할 수 있습니다",
  "memory:正在读取": "불러오는 중",
  "memory:配置模型": "모델 구성",
  "memory:保存中…": "저장 중…",
  "memory:保存原话": "원문 저장",
  "memory:最近添加": "최근 추가됨",
  "memory:原话已保存，等待可用模型": "원문 저장됨, 사용 가능한 모델 대기 중",
  "memory:原话已保存，等待整理": "원문 저장됨, 정리 대기 중",
  "memory:正在等待整理": "정리 대기 중",
  "memory:正在整理": "정리 중",
  "memory:原话已保存，整理未完成": "원문 저장됨, 정리 미완료",
  "memory:整理已完成": "정리 완료",
  "memory:原话已保存，未提取出可用记忆":
    "원문 저장됨, 사용 가능한 기억이 추출되지 않음",
  "memory:最近添加的内容读取失败。": "최근 추가한 기억을 읽지 못했습니다.",
  "memory:整理状态暂时不可用，原话已保存。":
    "정리 상태를 일시적으로 확인할 수 없습니다. 원문은 저장되었습니다.",
  "memory:已保存，正在后台沉淀记忆": "저장됨. 백그라운드에서 기억 생성 중",
  "memory:未能确认保存结果，输入已保留。重试不会重复添加。":
    "저장 결과를 확인하지 못했습니다. 입력한 텍스트는 유지되며, 다시 시도해도 중복으로 추가되지 않습니다.",
  "memory:模型服务要求检查余额或付款状态，原话仍已保存。":
    "모델 서비스에서 잔액 또는 결제 상태 확인을 요구합니다. 원문은 저장된 상태입니다.",
  "memory:这台设备打不开 3D 视图，已显示关联列表。":
    "이 기기에서는 3D 보기를 사용할 수 없어 연결을 목록으로 표시합니다.",
  "memory:图谱工具": "그래프 컨트롤",
  "memory:放大": "확대",
  "memory:放大图谱": "그래프 확대",
  "memory:缩小": "축소",
  "memory:缩小图谱": "그래프 축소",
  "memory:重置视角": "보기 초기화",
  "memory:重置图谱视角": "그래프 보기 초기화",
  "memory:查看读取详情": "읽기 세부 정보 보기",
  "memory:原文暂时无法读取": "원문을 불러오지 못했습니다",
  "memory:暂时无法读取这段对话，已保留当前记忆。":
    "이 대화를 불러오지 못했습니다. 현재 기억은 선택된 상태로 유지됩니다.",
  "memory:已清除筛选，定位到关联记忆。":
    "관련 기억을 찾기 위해 필터를 지웠습니다.",
  "memory:所选记忆不在当前筛选内，已取消选择。":
    "선택한 기억이 이 필터에 없어 선택을 해제했습니다.",
  "memory:列表分隔符": ", ",
  "memory:冒号": ":",
  "memory:手动添加": "수동으로 추가됨",
  "memory:导入的已有记忆": "가져온 기존 기억",
  "memory:记忆与原文": "기억 및 원문",
  "memory:第 {revision} 版": "버전 {revision}",
  "memory:对话时间 ": "대화 시간",
  "memory:沉淀时间 ": "생성 시각",
  "memory:原对话 ": "원본 대화 ",
  "memory:{count} 条记录没有原始时间，未用导入时间代替。":
    "기록 {count}개에 원래 시간이 없습니다. 가져온 시간으로 대체하지 않았습니다.",
  "memory:作者数量按当前记录分类统计，不代表记忆抽取已完成或语义已核实。":
    "작성자 수는 저장된 기록을 기준으로 하며, 기억 추출 완료나 의미 검증을 뜻하지 않습니다.",
  "memory:统计于 ": "집계 시각 ",
  "memory: · 导入数量仍在更新，可刷新核对":
    " · 가져온 개수가 아직 업데이트 중입니다. 새로 고쳐 확인하세요",
  "memory:对话于 {day}": "{day} 대화",
  "memory:导入于 {day}": "{day} 가져옴",
  "memory:写入于 {day}": "{day} 작성",
  "memory:已开始处理所选对话": "선택한 대화 정리 중",
  "memory:处理请求已受理": "정리 요청 접수됨",
  "memory:模型连接已通过。首批记忆生成后，会逐步出现在记忆列表里。":
    "모델 연결이 확인되었습니다. 기억이 생성되는 대로 목록에 표시됩니다.",
  "memory:已导入 {count} 段对话，整理成记忆后会出现在这里。":
    "대화 {count}개를 가져왔습니다. 정리가 끝나면 기억이 여기에 표시됩니다.",
  "memory:历史已保存在本机，记忆就绪后会出现在这里。":
    "기록이 이 기기에 저장되었습니다. 기억이 준비되면 여기에 표시됩니다.",
  "memory:连接应用并导入历史后，可以开始沉淀记忆。":
    "앱을 연결하고 기록을 가져오면 기억 생성을 시작할 수 있습니다.",
  "memory:当前已生成 0 条记忆。请求已受理不等于已经完成，原始对话、模型处理任务和记忆数量分别统计。":
    "아직 생성된 기억이 없습니다. 접수된 요청이 완료를 의미하지는 않으며, 원본 대화, 정리 작업, 기억은 따로 집계됩니다.",
  "memory:返回应用": "앱으로 돌아가기",
  "memory:共 {count} 条 · 正在核对来源…": "총 {count}개 · 출처 확인 중…",
  "memory:共 {count} 条 · 正在核对来源….one": "총 {count}개 · 출처 확인 중…",
  "memory:共 {count} 条": "총 {count}개",
  "memory:共 {count} 条.one": "총 {count}개",
  "memory: · 正在核对…": " · 확인 중…",
  "memory:新增 {count} 条记忆": "새 기억 {count}개",
  "memory:新增 {count} 条记忆.one": "새 기억 {count}개",
  "memory:沉淀于 ": "생성 시각 ",
  "memory:共 {count} 条，第 {page} / {pages} 页":
    "총 {count}개 · {page} / {pages} 페이지",
  "memory:共 {count} 条，第 {page} / {pages} 页.one":
    "총 {count}개 · {page} / {pages} 페이지",
  "memory:已沉淀 {count} 条记忆": "기억 {count}개 생성됨",
  "memory:已沉淀 {count} 条记忆.one": "기억 {count}개 생성됨",
  "memory:关键词“{query}”": "키워드 “{query}”",
  "memory:已沉淀 {count} 条{filter} · 当前展示 {shown} 条":
    "기억 {count}개 생성됨{filter} · {shown}개 표시 중",
  "memory:已沉淀 {count} 条{filter} · 当前展示 {shown} 条.one":
    "기억 {count}개 생성됨{filter} · {shown}개 표시 중",
  "memory:（{filter}）": " ({filter})",
  "memory:第 {page} 页": "{page} 페이지",
  "memory:正在等待整理：": "정리 대기 중: ",
  "memory:记忆未在生成": "기억이 생성되지 않는 중",
  "memory:还没有开始生成记忆：{reason}（{count} 条待处理）":
    "기억이 생성되지 않는 중: {reason}(대기 {count}개)",
  "memory:去配置生成模型": "생성 모델 구성",
  "memory:模型暂停处理": "모델 일시 중지됨",
  "memory:模型配置有问题（{reason}），记忆暂停处理，已有 {count} 条在排队":
    "모델 설정에 문제가 있습니다({reason}). 기억 처리가 일시 중지되었으며 {count}개가 대기 중입니다",
  "memory:模型配置有问题（{reason}），记忆暂停处理，已有 {count} 条在排队.one":
    "모델 설정에 문제가 있습니다({reason}). 기억 처리가 일시 중지되었으며 {count}개가 대기 중입니다",
  "memory:API Key 无效或没有权限": "API 키가 잘못되었거나 권한이 없습니다",
  "memory:找不到模型或地址": "모델 또는 주소를 찾을 수 없습니다",
  "memory:请求过于频繁，被服务商限流":
    "요청이 너무 많아 서비스 제공자가 제한했습니다",
  "memory:模型响应超时": "모델 응답 시간이 초과되었습니다",
  "memory:模型服务出错": "모델 서비스 오류",
  "memory:无法连接模型服务": "모델 서비스에 연결할 수 없습니다",
  "memory:连接模型服务失败": "모델 서비스 연결에 실패했습니다",
  "calls.directHost": "직접 호출(진단)",
  "calls.headline.timedOut": "{host}이(가) 제때 기억을 조회하지 못했습니다",
  "calls.headline.unavailable": "{host}이(가) 지금은 기억을 조회할 수 없습니다",
  "calls.headline.denied": "{host}에 기억 조회 권한이 없습니다",
  "calls.headline.empty": "{host}이(가) 일치하는 기억을 찾지 못했습니다",
  "calls.headline.found": "{host}이(가) 관련 항목 {count}개를 찾았습니다",
  "calls.headline.found.one": "{host}이(가) 관련 항목 {count}개를 찾았습니다",
  "calls.headline.rawOnly": "{host}이(가) 원본 대화 {count}개를 찾았습니다",
  "calls.headline.rawOnly.one": "{host}이(가) 원본 대화 {count}개를 찾았습니다",
  "calls.headline.memoryOnly": "{host}이(가) 기억 {count}개를 찾았습니다",
  "calls.headline.memoryOnly.one": "{host}이(가) 기억 {count}개를 찾았습니다",
  "calls.headline.mixed":
    "{host}이(가) 기억 {memories}개 · 원본 대화 {raw}개를 찾았습니다",
  "calls.headline.mixed.one":
    "{host}이(가) 기억 {memories}개 · 원본 대화 {raw}개를 찾았습니다",
  "calls.headline.completed": "{host} 호출이 완료되었습니다",
  "calls.detail.timedOut":
    "기억을 정리하는 중입니다. 조회 시간이 초과되어 이번 답변에는 기억이 포함되지 않았습니다.",
  "calls.detail.unavailable":
    "MemHub를 일시적으로 사용할 수 없어 이번 답변에는 기억이 포함되지 않았습니다.",
  "calls.detail.denied": "현재 권한으로는 이 조회를 할 수 없습니다.",
  "calls.detail.partialDelivered": "{host}에 제공됨 · 일부 내용은 아직 정리 중",
  "calls.detail.partial": "일부 내용은 아직 정리 중입니다",
  "calls.detail.delivered": "{host}에 제공됨",
  "calls.detail.notDelivered": "앱에 제공되지 않음",
  "calls.detail.empty": "이번에는 일치하는 기억이 없습니다",
  "calls.detail.success": "조회 성공",
  "calls.title": "사용 기록",
  "calls.query": "이번 기억 조회",
  "calls.backApps": "앱으로 돌아가기",
  "calls.backHistory": "기록으로 돌아가기",
  "calls.filter": "앱별 필터",
  "calls.allApps": "모든 앱",
  "calls.recent": "최근 기록",
  "calls.refresh": "기록 새로 고침",
  "calls.clearPage": "이 페이지 지우기",
  "calls.clearAll": "모든 기록 지우기",
  "calls.clearAllShort": "모두 지우기",
  "calls.loading": "사용 기록을 불러오는 중",
  "calls.clearing": "선택한 기록을 지우는 중",
  "calls.readFailed":
    "기록을 불러오지 못했습니다. 새로 고친 후 다시 시도하세요.",
  "calls.unavailable": "사용 기록을 사용할 수 없습니다. 나중에 새로 고치세요.",
  "calls.incomplete": "일부 호출이 기록되지 않아 목록이 불완전할 수 있습니다.",
  "calls.callDetail": "호출 세부 정보",
  "calls.source": " · 출처: {source}",
  "calls.stats": "원문 {raw}개 · 기억 {memories}개 찾음 · {seconds}초",
  "calls.metadata": "기록 세부 정보",
  "calls.privacy":
    "개인정보 보호를 위해 질문 원문과 답변은 여기에 저장하지 않으며, 시간, 출처, 결과만 보관합니다.",
  "calls.exactTime": "정확한 시간",
  "calls.app": "앱",
  "calls.type": "유형",
  "calls.result": "결과 상태",
  "calls.tool": "기억 조회",
  "calls.notRecorded": "기록되지 않음",
  "calls.missingRow":
    "이 기록은 더 이상 이 페이지에 없습니다. 기록으로 돌아가서 찾으세요.",
  "calls.list": "호출 기록",
  "calls.emptyPage": "이 페이지에 기록이 없습니다",
  "calls.empty": "아직 사용 기록이 없습니다",
  "calls.pagination": "기록 페이지",
  "calls.previous": "이전 페이지",
  "calls.next": "다음 페이지",
  "calls.policy": "{days}일 동안 최대 {count}개 기록 보관",
  "calls.confirmAllTitle": "모든 사용 기록을 지우시겠습니까?",
  "calls.confirmPageTitle": "이 페이지의 기록 {count}개를 지우시겠습니까?",
  "calls.confirmPageTitle.one": "이 페이지의 기록 {count}개를 지우시겠습니까?",
  "calls.confirmAllDescription":
    "이 페이지나 필터뿐 아니라 보관된 모든 호출 기록을 삭제합니다. 원본 대화와 기억, 권한, 보관 규칙은 변경되지 않습니다. 이 작업은 실행 취소할 수 없습니다.",
  "calls.confirmPageDescription":
    "이 호출 기록만 삭제합니다. 원본 대화와 기억, 권한, 보관 규칙은 변경되지 않습니다. 이 작업은 실행 취소할 수 없습니다.",
  "calls.confirmAll": "모두 지우기 확인",
  "calls.confirmPage": "지우기 확인",
  "calls.cancel": "취소",
  "calls.clearedAll": "모든 기록을 지웠습니다.",
  "calls.clearedPage": "선택한 기록을 지웠습니다.",
  "calls.clearAllFailed":
    "사용 기록을 지우지 못했습니다. 기존 기록은 변경되지 않았습니다. 확인 후 다시 시도하세요.",
  "calls.clearPageFailed":
    "선택한 기록을 지우지 못했습니다. 기존 기록은 변경되지 않았습니다. 확인 후 다시 시도하세요.",
  "calls.clearUnknown":
    "결과를 확인하지 못했습니다. 다시 불러온 기록을 확인하세요. 지우기를 자동으로 다시 시도하지 않았습니다.",
  "calls.references": "관련 내용",
  "calls.referencesLoading": "관련 내용을 불러오는 중",
  "calls.referencesFailed":
    "관련 내용을 불러오지 못했습니다. 새로 고친 후 다시 시도하세요.",
  "calls.raw": "원본 대화",
  "calls.memory": "기억",
  "calls.changed": "버전 변경됨",
  "calls.pending": "확인 필요",
  "calls.notAvailable": "사용할 수 없음",
  "calls.openRaw": "원본 대화 보기",
  "calls.openMemory": "기억 보기",
  "calls.noReferences": "관련 내용을 찾지 못했습니다",
  "calls.rawBody": "원본 대화 텍스트",
  "calls.rawLoading": "원본 대화를 불러오는 중",
  "calls.rawFailed": "원본 대화를 사용할 수 없거나 버전이 변경되었습니다.",
  "calls.state.grounded": "조회 성공",
  "calls.state.empty": "조회 성공, 일치하는 기억 없음",
  "calls.state.partial": "조회 성공, 일부 내용은 아직 정리 중",
  "calls.state.timed_out": "조회 시간 초과, 제공된 기억 없음",
  "calls.state.unavailable": "MemHub를 일시적으로 사용할 수 없음",
  "calls.state.denied": "이 조회는 승인되지 않았습니다",
  "calls.return.timed_out":
    "MemHub가 기억을 가져와 정리하는 중입니다. 조회가 제때 끝나지 않아 {host}은(는) 기억 없이 답변했습니다. 정리가 끝나면 정상으로 돌아옵니다.",
  "calls.return.unavailable":
    "MemHub를 일시적으로 사용할 수 없어 이번 답변에는 기억이 포함되지 않았습니다.",
  "calls.return.denied":
    "현재 권한으로는 이 조회를 할 수 없어 앱에 기억을 제공하지 않았습니다.",
  "calls.return.partial": "일부 내용은 아직 정리 중입니다.",
  "clock.unknown": "시간 알 수 없음",
  "clock.today": "오늘 {clock}",
  settings: "설정",
  languageTitle: "인터페이스 언어",
  languageLabel: "언어",
  languageSystem: "시스템 언어 사용",
  languageChinese: "중국어(간체)",
  languageEnglish: "영어",
  languageSpanish: "스페인어",
  languageDescription:
    "인터페이스 언어를 선택하세요. 이 설정은 대화나 기억을 변경하지 않습니다.",
  languageSaveError: "언어 설정을 저장하지 못했습니다. 다시 시도하세요.",
  languageLoadError: "언어 설정을 불러오지 못했습니다. 다시 시도하세요.",
  nativeErrorTitle: "MemHub를 시작할 수 없습니다",
  startupPreparing: "시작하는 중입니다. 기억 서비스를 준비하는 중…",
  startupUpgradeHint:
    "첫 실행이나 기억 데이터 업그레이드에는 몇 분이 걸릴 수 있습니다. 완료되면 MemHub가 자동으로 열립니다.",
  "migration.dialogMessage":
    "이번 MemHub 업그레이드의 데이터 마이그레이션이 완료되지 않았습니다.",
  "migration.dialogDetail":
    "데이터를 보호하기 위해 MemHub가 시작되지 않습니다. 마이그레이션 로그를 GitHub 이슈에 첨부해 보내 주세요. (오류 코드: {code})",
  "migration.dataRestored": "데이터가 업그레이드 이전 상태로 복원되었습니다.",
  "migration.logPath": "로그: {path}",
  "migration.openLog": "로그 열기",
  "migration.quit": "종료",
  "migration.issueHeading": "## 업그레이드 마이그레이션 실패",
  "migration.issueUpgrade":
    "- MemHub를 {fromVersion}(build {fromBuild})에서 {toVersion}(build {toBuild})(으)로 업그레이드하는 중 데이터 마이그레이션에 실패했습니다.",
  "migration.issueCode": "- 오류 코드: {code}",
  "migration.issueStep": "- 실패한 단계: {step}",
  "migration.issueRestored": "- 업그레이드 이전 상태로 데이터 복원: {restored}",
  "migration.issueSystem": "- 시스템: {system}",
  "migration.issueArch": " ({arch})",
  "migration.issueLog": "- 마이그레이션 로그(로컬): {path}",
  "migration.issueAttach":
    "위의 로그 파일을 여기로 끌어 첨부한 후 제출해 주세요.",
  "migration.unknown": "알 수 없음",
  "migration.yes": "예",
  "migration.no": "아니요",
};
