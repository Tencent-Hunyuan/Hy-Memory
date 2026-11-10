import type { MessageKey } from "../gui-i18n.js";
import { embeddingSwitchJapaneseMessages } from "../gui-i18n-embedding-switch.js";
import { modeJapaneseMessages } from "../gui-i18n-mode.js";

/** Japanese (ja) GUI copy. Machine-assisted; native-speaker review recommended. */
export const japaneseMessages: Record<MessageKey, string> = {
  ...modeJapaneseMessages,
  ...embeddingSwitchJapaneseMessages,
  "connectionFallback.title": "ほかのツールを接続",
  "connectionFallback.desktopOnly":
    "ローカルのペアリングはデスクトップアプリでのみ利用できます。",
  "connectionFallback.description":
    "一覧にないローカルの Harness 用に、使い捨てのペアリング Skill を生成します。ブラウザーのチャット、リモートコンテナー、ほかのコンピューターからはこのデバイスに直接接続できません。",
  "connectionFallback.name": "Harness 名",
  "connectionFallback.namePlaceholder": "例：Kimi Code",
  "connectionFallback.generating": "生成中…",
  "connectionFallback.generate": "ペアリング Skill を生成",
  "connectionFallback.generateFailed":
    "ペアリング Skill を生成できませんでした。",
  "connectionFallback.retry": "再試行",
  "connectionFallback.generated":
    "{name} 用の使い捨てペアリング Skill を生成しました。有効期限は {time} です。",
  "connectionFallback.skillLabel": "ペアリング Skill",
  "connectionFallback.copy": "Skill をコピー",
  "connectionFallback.copied":
    "ペアリング Skill をコピーしました。対応する Harness に貼り付けて実行してください。",
  "connectionFallback.copyFailed":
    "コピーできませんでした。上のテキストを選択してコピーしてください。",
  "connectionFallback.localDescription":
    "ローカルの MCP またはコマンドラインツール専用です。ブラウザーのチャットからはこのデバイスに直接接続できません。",
  "connectionFallback.loading": "ローカルの接続情報を読み込み中…",
  "connectionFallback.loadFailed": "接続情報を読み込めませんでした。",
  "connectionFallback.format": "接続情報の形式",
  "connectionFallback.promptTab": "接続プロンプト",
  "connectionFallback.mcpTab": "MCP 設定",
  "connectionFallback.commandTab": "コマンド",
  "connectionFallback.materialLabel": "ローカルの接続情報",
  "connectionFallback.materialBoundary":
    "ローカルのパスを含みますが、記憶やキーは含みません。貼り付ける前に内容を確認し、MemHub を起動したままにしてください。",
  "connectionFallback.materialCopied":
    "コピーしました。まだ接続は行われていません。",
  "connectionFallback.copyPrompt": "プロンプトをコピー",
  "connectionFallback.copyConfig": "設定をコピー",
  "connectionFallback.copyCommand": "コマンドをコピー",
  "connectionFallback.promptTemplate":
    "次の MCP 設定で、このローカル Harness を MemHub に接続してください：\n```json\n{mcp}```\n{shellBlock}\n接続後、実際に記憶の照会を一度実行し、結果を正確に報告してください。",
  "connectionFallback.promptShell":
    "\nローカルのシェルコマンドしか使えない場合は、次を使用してください：\n```bash\n{shell}```\n",
  "sdk.memory.title": "SDK 記憶ライブラリ",
  "sdk.memory.status": "記憶の状態",
  "sdk.memory.status.active": "利用可能",
  "sdk.memory.status.superseded": "置き換え済み",
  "sdk.memory.total": "記憶 {count} 件",
  "sdk.memory.total.one": "記憶 {count} 件",
  "sdk.memory.loadFailed":
    "記憶ライブラリを読み込めませんでした。もう一度お試しください。",
  "sdk.memory.detailLoadFailed": "記憶の詳細を読み込めませんでした。",
  "sdk.memory.updateFailed":
    "変更を保存できませんでした。もう一度お試しください。",
  "sdk.memory.deleteFailed":
    "記憶を削除できませんでした。もう一度お試しください。",
  "sdk.memory.loading": "記憶ライブラリを読み込み中…",
  "sdk.memory.pagination": "SDK 記憶ライブラリのページ",
  "sdk.memory.previous": "前のページ",
  "sdk.memory.next": "次のページ",
  "sdk.memory.detail": "SDK 記憶の詳細",
  "sdk.memory.content": "記憶の内容",
  "sdk.memory.save": "変更を保存",
  "sdk.memory.delete": "記憶を削除",
  "sdk.memory.scene.chat": "チャット",
  "sdk.memory.scene.working": "仕事",
  "sdk.memory.scene.coding": "コーディング",
  "sdk.memory.task": "タスク",
  "sdk.memory.solution": "解決方法",
  "sdk.memory.page": "{page} / {pages} ページ",
  "sdk.raw.title": "SDK 元の会話",
  "sdk.raw.searchLabel": "元の会話を検索",
  "sdk.raw.searching": "検索中…",
  "sdk.raw.search": "検索",
  "sdk.raw.loadFailed": "元の会話は一時的に利用できません。",
  "sdk.raw.searchFailed":
    "元の会話を検索できませんでした。もう一度お試しください。",
  "sdk.raw.recent": "最近の元の会話",
  "sdk.raw.empty":
    "元の会話はまだありません。ソースを接続すると、ここに表示されます。",
  "sdk.raw.total": "元の会話 {count} 件",
  "sdk.raw.total.one": "元の会話 {count} 件",
  "memory:当前使用基础记忆。新对话会保存为原始记录，不会生成高级记忆；以前生成的高级记忆仍会保留，但当前不会显示或参与记忆召回。":
    "基本メモリを使用中です。新しい会話は原文として保存され、高度なメモリは生成されません。既存の高度なメモリは保持されますが、現在は表示も呼び出しもされません。",
  "memory:当前使用智能记忆。新对话会由模型整理为高级记忆。":
    "スマートメモリを使用中です。モデルが新しい会話を高度なメモリに整理します。",
  "memory:当前使用深度记忆。新对话会生成高级记忆，并由知识图谱和 evolve 继续整合。":
    "ディープメモリを使用中です。新しい会話から高度なメモリを生成し、知識グラフと evolve が統合を続けます。",
  "sdk.raw.found": "{count} 件見つかりました",
  "sdk.raw.found.one": "{count} 件見つかりました",
  "memoryInspector.emptyHint": "ソース、内容、変更履歴を確認できます。",
  "memory.operationFailed": "記憶の操作に失敗しました（{code}）",
  "memorySpace.tabs.workbench": "ワークベンチ",
  "selectedMemory.status.confirmed": "作成済み",
  "selectedMemory.status.active": "有効",
  "selectedMemory.status.deleted": "削除済み",
  "selectedMemory.status.superseded": "置き換え済み",
  "selectedMemory.status.shadow": "非表示",
  "selectedMemory.status.negated": "否定済み",
  "selectedMemory.status.conflicted": "競合あり",
  "selectedMemory.status.archived": "アーカイブ済み",
  "selectedMemory.status.unknown": "状態の確認が必要",
  "selectedMemory.sourceUnknown": "ソースの確認が必要",
  "selectedMemory.sourceUnattributed": "不明なソース",
  "selectedMemory.discardDraft": "保存されていない変更を破棄しますか？",
  "selectedMemory.newVersionReadFailed":
    "新しいバージョンを読み込めませんでした。下書きは保持されています。",
  "selectedMemory.updateRejected":
    "変更は送信されませんでした。下書きは保持されています。問題を修正してから再試行してください。",
  "selectedMemory.updateUnknown":
    "変更が確認できていません。下書きは保持されています。再試行する前に更新してください。",
  "selectedMemory.deleteRejected":
    "削除は送信されませんでした。この記憶を確認してから再試行してください。",
  "selectedMemory.deleteUnknown":
    "削除が確認できていません。記憶を確認してください。削除範囲が広がることはありません。",
  "selectedMemory.undoRejected":
    "取り消しは送信されませんでした。問題を修正してから再試行してください。",
  "selectedMemory.undoUnknown":
    "取り消しが確認できていません。再試行する前に現在のバージョンを確認してください。",
  "selectedMemory.author.you": "あなたの発言",
  "selectedMemory.author.assistant": "アシスタントの元の発言",
  "selectedMemory.author.unknown": "発言者の確認が必要",
  "selectedMemory.version": "バージョン {revision}",
  "selectedMemory.sourceLine": "ソース {source} · {time}",
  "selectedMemory.timestamps": "会話日時 {conversation} · 作成日時 {created}",
  "selectedMemory.writeTime": "書き込み日時 {created}",
  "selectedMemory.edit": "編集",
  "selectedMemory.evidence": "元の根拠",
  "selectedMemory.evidenceLine": "{author} · 元の会話 {time}：",
  "selectedMemory.evidenceUnavailable": "この原文は現在利用できません。",
  "selectedMemory.viewOriginal": "原文を表示",
  "selectedMemory.history": "バージョン履歴",
  "selectedMemory.more": "その他の操作",
  "selectedMemory.undo": "直前の変更を取り消す",
  "selectedMemory.delete": "記憶を削除",
  "selectedMemory.editTitle": "記憶を編集",
  "selectedMemory.originalTitle": "元の会話",
  "selectedMemory.back": "記憶に戻る",
  "selectedMemory.editDescription":
    "この記憶を編集します。元の会話は変更されません。",
  "selectedMemory.content": "記憶の内容",
  "selectedMemory.scopeUnchanged": "利用範囲は変更されません",
  "selectedMemory.conflictTitle": "新しいバージョンがあります",
  "selectedMemory.conflictDescription":
    "下書きは保持されています。編集する前に新しいバージョンを確認してください。",
  "selectedMemory.viewNewVersion": "新しいバージョンを表示",
  "selectedMemory.continueFromVersion": "このバージョンから編集を続ける",
  "selectedMemory.cancel": "キャンセル",
  "selectedMemory.save": "変更を保存",
  "selectedMemory.author.user": "あなた",
  "selectedMemory.author.agent": "アシスタント",
  "selectedMemory.author.tool": "ツール",
  "selectedMemory.sourceUnavailable":
    "ソースの権限が変更されたか、原文を利用できません。",
  "selectedMemory.related": "関連する記憶",
  "selectedMemory.viewMemory": "記憶を表示",
  "selectedMemory.deleteTitle": "この記憶を削除しますか？",
  "selectedMemory.deleteDescription":
    "今後の照会には使われなくなります。ソースアプリ内の会話は削除されません。",
  "selectedMemory.sentCannotRecall":
    "すでにアプリへ送信された内容をリモートで取り消すことはできません。",
  "memory:{count} 条": "{count} 件",
  "memory:已可用 · {count} 段原文": "利用可能 · 原文抜粋 {count} 件",
  "memory:主题视角（非事项）": "トピック表示（事項ではありません）",
  "memory:来源视角": "ソース表示",
  "memory:{count} 条记忆 · 当前已加载": "記憶 {count} 件 · 読み込み済み",
  "memory:最近 {count} 条": "最近の会話 {count} 件",
  "memory:已确认": "確認済み",
  "memory:记忆库": "記憶ライブラリ",
  "memory:问记忆": "記憶に質問",
  "memory:历史对话": "会話履歴",
  "memory:项目记忆": "プロジェクトの記憶",
  "memory:编码偏好": "コーディングの好み",
  "memory:长期模式": "長期的なパターン",
  "memory:意图": "意図",
  "memory:近 3 个月": "過去 3 か月",
  "memory:近 6 个月": "過去 6 か月",
  "memory:近 1 年": "過去 1 年",
  "memory:全部历史": "すべての履歴",
  "memory:{count} 条这次没能整理成功，正在重试": "処理を再試行中 · {count} 件",
  "memory:{count} 处理中": "処理中：{count} 件",
  "memory: · 处理中 {running} / 待派发 {active}":
    " · 処理中 {running} 件 / 待機中 {active} 件",
  "memory: · {count} 条任务状态待核对": " · {count} 件のタスクの状態確認が必要",
  "memory: · {count} 条任务状态异常，正在恢复":
    " · {count} 件のタスクの状態に問題があり、復旧中",
  "memory:{count} 等待": "待機中 {count} 件",
  "memory:最新 {count} 条待处理": "最新の未処理 {count} 件",
  "memory:历史积压 {count} 条": "未処理の蓄積 {count} 件",
  "memory:（约 {count} 分钟）": " （約 {count} 分）",
  "memory:（约 {count} 小时）": " （約 {count} 時間）",
  "memory:（约 {count} 天）": " （約 {count} 日）",
  "memory:同步尝试 {count} 次": "同期の試行 {count} 回",
  "memory:尚未尝试同步": "同期は未実行",
  "memory: · 状态更新于 {date}": " · {date} に更新",
  "memory:HY Memory 当前没有这条记忆，仅重试同步不能补齐。":
    "HY Memory にはこの記憶がありません。同期を再試行しても復元できません。",
  "memory:同步需要可用的本机整理模型或本地嵌入授权。":
    "同期には、利用可能なローカルの処理モデル、またはローカル埋め込みの使用許可が必要です。",
  "memory:这条记忆或模型版本已经变化，先刷新核对再决定下一步。":
    "この記憶またはモデルのバージョンが変更されました。更新して確認してから、次の操作を決めてください。",
  "memory:这条记忆已改，搜索索引还没开始同步。":
    "この記憶は変更されましたが、検索インデックスの同期はまだ始まっていません。",
  "memory:最近一次同步未成功；可以先重试，结果仍不确定时再刷新核对。":
    "前回の同期に失敗しました。まず再試行し、それでも結果が不確かな場合は更新して確認してください。",
  "memory:这条记忆已改，搜索索引还没跟上。":
    "この記憶は変更されましたが、検索インデックスにはまだ反映されていません。",
  "memory:搜索里还是改之前那句话。":
    "検索結果には修正前のテキストが表示されたままです。",
  "memory:重试同步": "同期を再試行",
  "memory:配置整理模型": "処理モデルを設定",
  "memory:未提炼 · 在窗口外": "未抽出 · 対象期間外",
  "memory:未提炼 · 排队中": "未抽出 · 待機中",
  "wb.select": "選択してください",
  "memory:模型综合": "モデルによる統合",
  "memory:原文摘录": "原文抜粋",
  "memory:没有命中": "一致なし",
  "memory:正在生成": "生成中",
  "memory:正在检索记忆": "記憶を照会中",
  "memory: · 基于 {count} 条已确认记忆": " · 確認済みの記憶 {count} 件に基づく",
  "memory: · 基于 {count} 条已确认记忆.one":
    " · 確認済みの記憶 {count} 件に基づく",
  "memory:整理需要处理": "処理に対応が必要",
  "memory:正在整理记忆": "記憶を処理中",
  "memory:整理状态": "処理状況",
  "memory:自动提炼范围": "自動抽出の対象期間",
  "memory:手动提炼下一批": "次のバッチを手動で抽出",
  "memory:刷新核对": "更新して確認",
  "memory:正在导入历史会话，记忆库暂时只显示一部分；导入完成后会自动补全，不需要重试。":
    "会話履歴をインポート中です。現在は一部の記憶のみ表示されています。インポートが完了すると自動的にすべて表示されます。",
  "memory:选择：{content}": "選択：{content}",
  "memory:处理失败：{content}": "処理に失敗：{content}",
  "memory:已选 {count} 条": "{count} 件を選択中",
  "memory:已选 {count} 条.one": "{count} 件を選択中",
  "memory:删除所选 {count} 条": "選択した {count} 件を削除",
  "memory:删除所选 {count} 条.one": "選択した {count} 件を削除",
  "memory:重试失败的 {count} 条": "失敗した {count} 件を再試行",
  "memory:重试失败的 {count} 条.one": "失敗した {count} 件を再試行",
  "memory:没有{status}的记忆。": "「{status}」の記憶はありません。",
  "memory:已{action} {count} 条": "操作完了（{action}）：{count} 件",
  "memory:已{action} {count} 条.one": "操作完了（{action}）：{count} 件",
  "memory:，{count} 条未完成": "、{count} 件は未完了",
  "memory:，{count} 条未完成.one": "、{count} 件は未完了",
  "memory:未能{action}：{error}": "{action}できませんでした：{error}",
  "memory:重试{action}": "{action}を再試行",
  "memory:删除（操作）": "削除",
  "memory:处理（操作）": "処理",
  "memory:{count} 条原话": "原文抜粋 {count} 件",
  "memory:{count} 条原话.one": "原文抜粋 {count} 件",
  "memory:{count} 次整理已完成": "処理 {count} 回完了",
  "memory:{count} 次整理已完成.one": "処理 {count} 回完了",
  "memory: · {count} 条在窗口外（原文可搜）":
    " · 対象期間外 {count} 件（原文は検索可能）",
  "memory: · {count} 条在窗口外（原文可搜）.one":
    " · 対象期間外 {count} 件（原文は検索可能）",
  "memory: · 已规划 {planned} 批，当前第 {current} 批":
    " · 計画 {planned} バッチ中、現在 {current} バッチ目",
  "memory:{kind}卡在 {reason}（{count} 条）":
    "{kind}が {reason} で停止中（{count} 件）",
  "memory:已加载 {count} 条记忆": "記憶 {count} 件を読み込み済み",
  "memory:已加载 {count} 条记忆.one": "記憶 {count} 件を読み込み済み",
  "memory:回答只使用已确认记忆。每个答案都可以回到对应记忆和来源原话。":
    "回答には確認済みの記憶のみを使用します。各回答から、対応する記憶と原文をたどれます。",
  "memory:选择一条记忆": "記憶を選択",
  "memory:查看它的来源、使用范围和修改历史。":
    "ソース、利用範囲、変更履歴を確認できます。",
  "memory:查看替代它的记忆": "この記憶を置き換えた記憶を表示",
  "memory:查看它取代的旧记忆": "この記憶が置き換えた古い記憶を表示",
  "memory:保存纠正": "修正を保存",
  "memory:修改历史": "変更履歴",
  "memory:已可用": "利用可能",
  "memory:处理中…": "処理中…",
  "memory:搜索": "検索",
  "memory:记忆治理状态": "記憶の状態",
  "memory:记忆空间模式": "記憶スペースのモード",
  "memory:来源状态待核对，记忆库暂不可读取。":
    "ソースの状態を確認する必要があるため、記憶ライブラリはまだ読み込めません。",
  "memory:记忆库读取失败，请重试。":
    "記憶ライブラリを読み込めませんでした。もう一度お試しください。",
  "memory:重新读取记忆库": "記憶ライブラリを再読み込み",
  "memory:正在读取记忆库…": "記憶ライブラリを読み込み中…",
  "memory:批量操作结果": "一括操作の結果",
  "memory:批量操作": "一括操作",
  "memory:取消选择": "選択を解除",
  "memory:向已确认记忆提问": "確認済みの記憶に質問",
  "memory:搜索记忆库": "記憶ライブラリを検索",
  "memory:问答记忆范围": "質問の対象となる記憶",
  "memory:只问个人画像": "個人プロフィールのみに質問",
  "memory:问全部记忆（含项目）": "すべての記憶に質問（プロジェクトを含む）",
  "memory:例如：我对真实数据有什么要求？":
    "例：実データについて、私はどんな要件を持っていますか？",
  "memory:搜索偏好、决定或经历": "好み、決定、経験を検索",
  "memory:根据记忆回答": "記憶に基づいて回答",
  "memory:清除搜索": "検索をクリア",
  "wb.finding.duplicate": "同じ元の発言から抽出",
  "wb.finding.duplicateText":
    "「{first}」と「{second}」は同じ元の発言から抽出され、それぞれ {firstScope} と {secondScope} に割り当てられました。同じ事柄を指していないか確認してください。",
  "wb.finding.dormant": "最近更新されていない記憶",
  "wb.finding.dormantText":
    "{count} 件の記憶が 2 か月間、変更も言及もされていません。見直しが必要かもしれません。",
  "wb.finding.blocked": "条件待ち",
  "wb.finding.blockedText":
    "{count} 件の会話が条件を満たすまで待機しています。原文はすでに保存されており、検索できます。",
  "wb.finding.blockedText.one":
    "{count} 件の会話が条件を満たすまで待機しています。原文はすでに保存されており、検索できます。",
  "wb.unclassified": "未分類",
  "wb.profile": "個人プロフィール",
  "wb.project": "プロジェクト {id}",
  "wb.topics": "トピック",
  "wb.all": "すべて",
  "wb.topicCount": "{count} 件のトピックを形成",
  "wb.topicCount.one": "{count} 件のトピックを形成",
  "wb.singletons": "、ほかに 1 回だけ出現したタグが {count} 件",
  "wb.singletons.one": "、ほかに 1 回だけ出現したタグが {count} 件",
  "wb.memories": "記憶 {count} 件",
  "wb.memories.one": "記憶 {count} 件",
  "wb.sources": "ソース {count} 件",
  "wb.sources.one": "ソース {count} 件",
  "wb.once":
    "このトピックはここにしか現れません。一度きりの事柄か、同じトピックが別の名前に分かれている可能性があります。",
  "wb.graphTitle": "{label} の関係グラフ",
  "wb.readSummary": "この事項の要約を読む",
  "wb.summaryPrompt":
    "「{label}」について、現在の状況、決定済みの事項、未決定の事項をわかりやすく説明してください。",
  "wb.building": "統合中…",
  "wb.build": "HY Memory で関係を生成",
  "wb.drawn": "記憶 {shown} / {total} 件を表示中",
  "wb.askLabel": "この事項について質問",
  "wb.askPlaceholder":
    "この事項について質問してください。回答には確認済みの記憶のみを使用します",
  "wb.ask": "質問",
  "wb.originals": "元の会話",
  "wb.evidenceNote":
    "この記憶は、以下の {count} 件の原文抜粋に基づいています（時系列順）",
  "wb.evidenceNote.one":
    "この記憶は、以下の {count} 件の原文抜粋に基づいています（時系列順）",
  "wb.pickCircle": "グラフ上の円を選択すると、その原文がここに表示されます",
  "wb.unavailable": "ソースの原文を利用できません",
  "wb.fullConversation": "会話全体を表示",
  "wb.you": "あなた",
  "wb.assistant": "アシスタント",
  "wb.noMemories":
    "記憶はまだありません。インポート済みの元の会話は「会話履歴」で検索できます。",
  "wb.waiting":
    "一部の会話が条件を満たすまで待機しています。原文は保存されており、検索できます。",
  "wb.conditions": "不足しているものを確認",
  "wb.findings": "記録からわかったこと",
  "wb.findingsNote":
    "ローカルの記録から算出しています。項目を選択すると、その根拠となる記憶がグラフ上で強調表示されます。",
  "wb.showGraph": "グラフで表示",
  "wb.loading": "記憶を読み込み中…",
  "wb.coreStarting":
    "ローカルの Core が起動すると自動的に表示されます。更新は不要です。",
  "wb.nothingToConnect": "まだつなげられるものがありません。",
  "wb.skeletonNote":
    "ソースを承認すると、ソースとトピックの構造が表示されます。記憶は作成され次第表示されます。",
  "wb.authorize": "ソースを承認",
  "wb.conversations": "会話 {count} 件",
  "wb.conversations.one": "会話 {count} 件",
  "wb.chipChanges": "ここで最近何が変わりましたか？",
  "wb.chipSummarize": "これらを数項目にまとめてください",
  "wb.unknownTime": "日時不明",
  "wb.unknownSource": "不明なソース",
  "wb.transcriptGone": "この会話はライブラリに残っていません。",
  "wb.edgeCount": "{label}：{count} 件のつながり",
  "wb.edgeCount.one": "{label}：{count} 件のつながり",
  "wb.noRelations":
    "HY Memory はまだ関係を見つけていません。上のボタンから System 2 を実行してください。",
  "wb.noModelRelations":
    "使用できるモデルが設定されていないため、HY Memory の関係を利用できません。",
  "provenance.multiple": "複数の発言者",
  "provenance.person": "ユーザーの元の発言",
  "provenance.agent": "エージェントの元の発言",
  "provenance.unknown": "発言者不明",
  "provenance.imported": "インポートした記憶 · {author}",
  "provenance.manual": "手動で追加",
  "provenance.answered": "回答から作成",
  "provenance.generated": "AI の返信から作成",
  "provenance.conversation": "元の会話から作成",
  "arrival.region": "記憶の処理状況",
  "arrival.importing": "原文をインポート中",
  "arrival.messages": "{count} 件のメッセージを保存済み",
  "arrival.messages.one": "{count} 件のメッセージを保存済み",
  "arrival.conversations": "会話 {count} 件",
  "arrival.conversations.one": "会話 {count} 件",
  "arrival.details": "進捗と詳細を表示",
  "arrival.backlog":
    "既存データが多いため、品質を保つためにバックグラウンドで分割して処理しています",
  "arrival.items": "{count} 項目",
  "arrival.items.one": "{count} 項目",
  "arrival.configure": "記憶の処理を設定",
  "arrival.status": "処理状況を表示",
  "arrival.history": "保存済みの会話を表示",
  "arrival.connect": "アプリを接続",
  "arrival.retained": "保存済みの原文はこのデバイスに残ります。",
  "arrival.description.model-required":
    "続行する前に処理モデルを確認してください。",
  "arrival.description.processing":
    "最近の会話から処理しています。記憶は順次表示されます",
  "arrival.description.importing":
    "原文を保存中です。インポートの進捗は記憶の作成状況を表すものではありません。",
  "arrival.description.failed":
    "再試行するかどうかを決める前に、失敗の原因を確認してください。",
  "arrival.description.unavailable":
    "最後に確認した情報は保持されています。現在の進捗をもう一度確認してください。",
  "arrival.description.refreshing":
    "ローカルの記録を確認中です。未確認の進捗は含まれません。",
  "arrival.description.checking":
    "保存済みの内容は保持されています。進捗の更新が必要です。",
  "arrival.description.stopped":
    "処理は停止しています。状況を確認してから続行してください。",
  "arrival.description.idle": "実行中の処理タスクはありません。",
  "arrival.description.empty":
    "アプリを接続するか、会話ファイルをインポートしてください。原文はまずこのデバイスに保存されます。",
  "arrival.title.refreshing": "保存済みの内容は保持されています。進捗を確認中",
  "arrival.title.checking": "進捗の更新が必要",
  "arrival.title.importing": "会話をインポート中",
  "arrival.title.processing": "記憶を作成中",
  "arrival.title.continue": "対応が必要",
  "arrival.title.model": "処理モデルが未設定",
  "arrival.title.empty": "保存済みの会話はまだありません",
  "handoff.region": "記憶をほかのアプリに持ち出す",
  "handoff.answer": "回答と引用をほかのアプリに持ち出す",
  "handoff.selected": "選択した記憶をほかのアプリに持ち出す",
  "handoff.title": "ほかのアプリに持ち出す",
  "handoff.closeTitle": "プレビューを閉じる",
  "handoff.close": "持ち出しプレビューを閉じる",
  "handoff.stats": "記憶 {count} 件 · {characters} 文字 · 省略なし",
  "handoff.stats.one": "記憶 {count} 件 · {characters} 文字 · 省略なし",
  "handoff.content": "ほかのアプリに持ち出す内容の全文",
  "handoff.target": "送り先のアプリ",
  "handoff.other": "その他のアプリ · コピーのみ",
  "handoff.copyOnly": " · コピーのみ",
  "handoff.boundary":
    "上の内容のみが含まれ、元の会話は含まれません。コピーしても接続が承認されるわけではありません。ほかのアプリにあるコピーは取り消せません。",
  "handoff.copy": "内容をコピー",
  "handoff.open": "コピーして {host} を開く",
  "handoff.copyFailed":
    "コピーできなかったため、アプリは開いていません。プレビュー内のテキストを選択してコピーしてください。",
  "handoff.opened":
    "コピーして {host} を開くよう要求しました。まだ何も送信されていません。送り先の入力欄を確認してください。",
  "handoff.notPrefilled":
    "全文をコピーしました。{host} に事前入力できなかったため、送り先のアプリに貼り付けてください。まだ何も送信されていません。",
  "handoff.openFailed":
    "全文をコピーしましたが、{host} を開けませんでした。まだ何も送信されていません。",
  "handoff.copied":
    "{target}の全文をコピーしました。まだ何も送信されていません。",
  "handoff.pasteTarget": "（{host} に貼り付け可能）",
  "wb.memory": "この記憶",
  "wb.node": "このノード",
  "wb.matter": "この事項",
  "wb.available": "利用可能",
  "wb.superseded": "置き換え済み",
  "wb.deleted": "削除済み",
  "wb.revision": "バージョン {revision}",
  "wb.collapse": "折りたたむ",
  "wb.oldSearch": "検索結果には修正前のテキストが表示されたままです。",
  "wb.missingIndex":
    "HY Memory にはこの記憶がありません。再試行しても復元できません。",
  "wb.retrySync": "同期を再試行",
  "wb.correctForm": "記憶の内容を修正",
  "wb.content": "記憶の内容",
  "wb.correct": "修正",
  "wb.saveCorrection": "修正を保存",
  "wb.cancelEdit": "編集をキャンセル",
  "wb.undo": "直前の変更を取り消す",
  "wb.delete": "削除",
  "wb.nodeNote":
    "これは記憶ではなく、記憶がつながる対象です。直接のつながり {direct} 件、2 ホップで到達可能 {indirect} 件。",
  "wb.direct": "直接",
  "wb.twoHops": "2 ホップ",
  "wb.idle":
    "円は記憶を、線は同じ会話で言及された記憶同士を表します。円を選択すると、その由来の確認と編集ができます。左側で別の事項を選ぶと、グラフも切り替わります。",
  "wb.searching": "記憶を検索中…",
  "wb.generated": "モデルによる統合",
  "wb.extractive": "原文抜粋",
  "wb.noHit": "一致なし",
  "wb.answerSource": "{mode} · 確認済みの記憶 {count} 件に基づく",
  "wb.answerSource.one": "{mode} · 確認済みの記憶 {count} 件に基づく",
  "wb.recalled": "記憶 {count} 件を取得",
  "wb.recalled.one": "記憶 {count} 件を取得",
  "wb.saveMemory": "記憶として保存",
  "wb.tooLong":
    "この回答は現在の保存上限を超えているため、記憶として保存できません。",
  "wb.summary":
    "これは要約であり、記憶ではありません。下の記憶と食い違わないよう、毎回作り直されます。",
  "wb.thisAnswer": "この回答",
  "wb.type.source": "ソースアプリ",
  "wb.type.topic": "トピック",
  "wb.type.schema": "HY による統合",
  "wb.type.memory": "記憶",
  "wb.origin.hy_relation": "HY Memory による統合",
  "wb.origin.same_conversation": "同じ会話",
  "wb.origin.shared_tag": "同じトピック",
  "wb.origin.provenance": "原文のソース",
  "wb.relation.evidence_source": "出典",
  "wb.relation.same_conversation": "同じ会話",
  "wb.relation.shared_tag": "同じトピック",
  "wb.relation.derived_from": "統合元",
  "wb.relation.tagged_with": "トピック",
  "wb.relation.related_to": "関連",
  guideEyebrow: "試してみる",
  guideTitle: "前回の会話の続きから始める",
  guideBack: "記憶に戻る",
  guideChooseScene: "シナリオを選択",
  guideSame: "新しい会話で続ける",
  guideSameDescription:
    "同じアプリの新しい会話で、以前の決定を見つけられます。",
  guideSwitch: "別のアプリで続ける",
  guideSwitchDescription:
    "作業は同じで、AI だけが違います。まず関連する背景情報を引き継ぎましょう。",
  guideNew: "新しいツールに自分を知ってもらう",
  guideNewDescription:
    "改めて自己紹介する代わりに、共有したい情報を 1 つ選んで始めましょう。",
  guideReadError:
    "現在、記憶を読み込めません。しばらくしてからもう一度お試しください。",
  guideDetailError:
    "この記憶は現在読み込めません。別の記憶を選ぶか、更新してください。",
  guideChanged:
    "記憶またはその権限が変更されました。更新してもう一度確認してください。",
  guideConnectionChanged:
    "接続状況が変わったため、何もコピーされていません。方法をもう一度選んでください。",
  guideCopiedOpened:
    "コピーして {host} を開くよう要求しました。まだ何も送信されていません。入力欄を確認してください。",
  guideCopiedNotFilled:
    "全文をコピーしましたが、事前入力できませんでした。送り先のアプリに貼り付けてください。まだ何も送信されていません。",
  guideCopiedNotOpened:
    "全文をコピーしましたが、送り先のアプリを開けませんでした。まだ何も送信されていません。",
  guideCopied:
    "全文をコピーしました。何も送信されておらず、新たな読み取り権限も付与されていません。",
  guideValidationError:
    "現在の記憶または権限を確認できませんでした。古いプレビューは非表示にしました。更新して再試行してください。",
  guideCopyError:
    "最新の内容を確認できなかったか、コピーを完了できませんでした。再試行してください。送り先のアプリは開いていません。",
  guideManual: "アプリ内で追加",
  guideOriginalApp: "ソースアプリ",
  guideListSeparator: "、",
  guideSaved: "このデバイスに元のメッセージ {count} 件を保存済み · ",
  "guideSaved.one": "このデバイスに元のメッセージ {count} 件を保存済み · ",
  guideReadingMemories: "処理済みの記憶を読み込み中",
  guideLoaded: "このページに利用可能な記憶 {count} 件を読み込み済み",
  "guideLoaded.one": "このページに利用可能な記憶 {count} 件を読み込み済み",
  guideSeparate: "。これらの段階は別々に集計されます。",
  guideChooseBackground: "背景情報を選択",
  guideCheckSources: "ソースを確認",
  guideTry: "新しい会話で試す",
  guideLoading: "このデバイスの既存の記憶を読み込み中…",
  guideEmptyTitle: "まず役立つ背景情報を 1 つ追加しましょう",
  guideEmptyDescription:
    "この体験に使える記憶がありません。インポートした原文は処理済みの記憶ではなく、候補が自動的に共有されることもありません。",
  guideAdd: "記憶を追加",
  guideConnections: "インポートと接続を表示",
  guideRefresh: "更新",
  guideBackgroundTitle: "今回使う背景情報",
  guideRefreshExample: "例を更新",
  guideChooseMemory: "記憶を選択",
  guideLoadedScope:
    "ここに読み込まれた利用可能な記憶 {count} 件から選択します。これは履歴のすべてではありません。",
  "guideLoadedScope.one":
    "ここに読み込まれた利用可能な記憶 {count} 件から選択します。これは履歴のすべてではありません。",
  guideCheckingEvidence: "元の根拠を確認中…",
  guideRevision:
    "{source} · バージョン {revision} · システムが作成、確認が必要",
  guideScope: "適用範囲：",
  guideAllApps: "アプリをまたいで利用可能（個人の好みとは限りません）",
  guidePrivate:
    "外部からの呼び出しはオフです。手動で共有するには別途判断が必要です",
  guideLimited: "範囲限定。外部からの呼び出し範囲は広げていません",
  guideEvidence: "元の根拠 · {count} 件の抜粋",
  "guideEvidence.one": "元の根拠 · {count} 件の抜粋",
  guideUnknownSource: "不明なソース",
  guideYou: "あなた",
  guideUnknownAuthor: "発言者不明",
  guideUnknownTime: "日時不明",
  guideUnavailable:
    "原文または現在の権限を利用できません。別の記憶を選ぶか更新してください。この内容は共有されません。",
  guideDraftTitle: "次の会話の書き出し",
  guideChooseApp: "試すアプリを選択",
  guideCopyOnly: "アプリを自分で選択 · コピーのみ",
  guideHostsError:
    "接続状況を読み込めませんでした。再試行するか、背景情報のみをコピーしてください。",
  guideNoApp:
    "接続済みのアプリが選択されていません。手動で共有しても接続はインストールされません。",
  guideWired:
    "前回のセルフチェックは成功しました。今回の用途で機能するか確認してください。",
  guideInstalled:
    "接続はインストール済みです。読み取りはまだ検証されていません。",
  guideNotInstalled: "読み取り接続がインストールされていません。",
  guideManage: "アプリの接続を管理",
  guideChooseRoute: "背景情報の渡し方を選択",
  guideContext: "背景情報を渡す",
  guideQuery: "アプリに検索させる",
  guideTopic: "続けるトピック",
  guideQueryHint:
    "質問のみを送信します。上の想定回答は新しい会話に入れません。",
  guideNewHint:
    "選んだ記憶のみを共有します。個人プロフィールは自動作成されません。",
  guideContextHint:
    "この背景情報と引用を含めます。これは自動呼び出しのテストではありません。",
  guidePreview: "書き出しの全文プレビュー",
  guidePreviewPlaceholder: "元の根拠が利用可能になると書き出しが表示されます",
  guideChecking: "確認中…",
  guideCopy: "書き出しをコピー",
  guideCopyOpen: "コピーして {host} を開く",
  guideNoReceipt:
    "今回は外部呼び出しの記録がありません。新しい会話は正しい背景情報を引用しましたか？",
  guideVerified: "結果を確認しました",
  guideMissing: "見つかりませんでした",
  guideFeedbackOk:
    "あなたのフィードバック：確認済み。これは自動検証の記録ではありません。",
  guideFeedbackMissing:
    "あなたのフィードバック：見つからない。まずソースのインポート、記憶の範囲、アプリの接続を確認してください。",
  guideInvitation: "お試しのご案内",
  guideInvitationHint:
    "自分の記憶を 1 つ使って、新しい会話を続けてみましょう。",
  guideTryOnce: "試す",
  guideLater: "あとで",
  guideDismiss: "案内を閉じる",
  healthTitle: "ヘルス診断",
  healthLocal: "ローカルサービス",
  healthBackground: "バックグラウンドサービス",
  healthSources: "ソース別の合計",
  healthProcessing: "記憶の処理",
  healthHosts: "AI との接続",
  healthDetailed: "詳細ログ",
  healthDetailedTitle: "詳細ログ（トラブルシューティング用）",
  healthDetailedHint:
    "既定ではオフです。サポートからトラブルシューティングを依頼された場合のみオンにしてください。",
  healthDetailedError:
    "詳細ログの設定を変更できませんでした。もう一度お試しください。",
  healthRefresh: "状態を更新",
  healthIncident: "問題の発生時刻を記録",
  healthIncidentDesktop:
    "問題の発生時刻はデスクトップアプリで記録してください。",
  healthIncidentPrompt: "発生した問題を簡単に説明してください（任意）",
  healthIncidentCopied: "問題 ID をコピーしました：{id}",
  healthIncidentError:
    "問題の発生時刻を記録できませんでした。もう一度お試しください。",
  healthReadFailed: "一時的に利用できません",
  healthStale: " · データが古くなっています。前回の結果を表示しています。",
  healthReading: "状態を読み込み中…",
  healthRereading: "更新中です。前回正常に読み込んだ結果を表示しています…",
  healthReadSuccessTime: "前回の正常な読み込み",
  healthReadFailedTime: "読み込み失敗日時",
  healthReadingSources: "ローカルサービスとソースの状態を読み込み中…",
  healthPrevious: "前回の読み込み",
  healthPreviousPrefix: "前回の読み込み：",
  healthRunning: "実行中",
  healthCoreVersion: "{state} · バージョン {version} · インスタンス {instance}",
  healthCoreDisconnected: "ローカルの Core にまだ接続されていません。",
  healthBackgroundUnavailable:
    "ここではバックグラウンドサービスを確認できません。対応しているデスクトップアプリを使用してください。",
  healthRuntime: "Core：{core} · ホストブリッジ：{broker}",
  healthSourceCount: "会話 {conversations} 件 · メッセージ {messages} 件",
  healthNoSources: "接続済みのソースはまだありません。",
  healthTaskCounts:
    "処理中 {running} · 待機中 {pending} · 完了 {completed} · 失敗 {failed}",
  healthImportDetails: "インポートと処理の詳細",
  healthRawTurns: "原文と完結したターン",
  healthRawCounts: "原文 {raw} 件 · 完結したターン {turns} 件",
  healthHy: "HY による処理",
  healthHyCounts:
    "呼び出し {calls} 回 · 完了 {completed} · 不確定 {unknown} · 失敗 {failed} · キャンセル {cancelled}",
  healthOutput: "出力",
  healthMemories: "記憶 {count} 件",
  "healthMemories.one": "記憶 {count} 件",
  healthWorkbuddy: "WorkBuddy の記憶照会",
  healthWorkbuddyCounts:
    "注入 {injected} 回 · タイムアウト {timedOut} 回 · 利用不可 {unavailable} 回",
  healthBlocked: "{kind}が {reason} で停止中（{count} 件）",
  healthRetrying: "処理を再試行中 · {count} 件",
  "healthRetrying.one": "処理を再試行中 · {count} 件",
  healthNoIssues: "対応が必要な問題はありません。",
  healthHostsUnavailable:
    "ブラウザーでは読み込めません。デスクトップアプリで確認してください。",
  healthNoHosts: "このデバイスで対応している AI ツールが検出されませんでした。",
  healthBackgroundHealthy: "バックグラウンドサービス利用可能",
  healthBackgroundStopped: "バックグラウンドサービス停止中",
  healthBackgroundDegraded: "バックグラウンドサービスの一部が利用不可",
  healthBackgroundUntrusted:
    "バックグラウンドサービスの身元を検証できませんでした",
  healthBackgroundUnknown: "バックグラウンドの状態は未確認",
  healthStopped: "停止中",
  healthUnhealthy: "接続できません",
  healthUntrusted: "身元未検証",
  healthUnknown: "未確認",
  healthNotChecked: "未チェック",
  healthWired: "接続済み",
  healthBlind: "インストール済みですが、記憶を取得できません",
  healthUnreachable: "未接続",
  startupReadError: "起動状況を読み込めませんでした。もう一度お試しください。",
  startupRetry: "再試行",
  startupOpening: "MemHub を開いています…",
  nativeHelp: "ヘルプ",
  nativeExportLogs: "サポート用にログを書き出す",
  nativeExportError:
    "ログを書き出せませんでした。しばらくしてからもう一度お試しください。",
  nativeLogin: "ログイン時に {product} を起動",
  nativeTrialUnavailable: " （このビルドでは利用不可）",
  nativeCliManaged: " （CLI で管理）",
  nativeStatusUnavailable: " （状態を取得できません）",
  nativeDevelopmentUnavailable: " （開発モードでは利用不可）",
  nativeOpen: "{product} を開く",
  nativeQuit: "{product} を終了（バックグラウンドサービスは引き続き実行）",
  nativeQuitStopsService: "{product} を終了（メモリサービスも停止）",
  nativeLoginError:
    "ログイン時の起動設定を変更できませんでした。もう一度お試しください。",
  nativeMoveTitle:
    "まず MemHub を「アプリケーション」フォルダに移動してください",
  nativeMoveDetail:
    "「アプリケーション」フォルダから MemHub を開き直すと、ほかのアプリとの接続やコマンドラインツールのインストールができるようになります。",
  nativeAcknowledge: "了解",
  graphWidthTitle: "既存の記憶ライブラリが内蔵モデルと一致しません",
  graphWidthDescription:
    "記憶ライブラリは {stored} 次元を使用していますが、内蔵モデルは {expected} 次元を出力します。両者は併用できないため、いずれかを選ぶまで抽出は失敗し続けます。",
  graphWidthKeep:
    "既存のライブラリを維持（既定）：以前の埋め込み設定を引き続き使用します。{count} 件の記憶とグラフはすべてそのままです",
  graphWidthRebuild:
    "内蔵モデルを使用：既存のグラフはアーカイブされます（削除はされません）。{count} 件の記憶の関係を再構築する必要があります",
  graphWidthExport:
    "先に書き出してから決める：今は何も変更せず、書き出し後にもう一度選択します",
  graphWidthExported:
    "{path} に書き出しました。既存のライブラリは変更されていません。もう一度選択できます。",
  graphWidthUnknown:
    "次元の選択が確認できていません。既存の記憶ライブラリは変更されていません。もう一度お試しください。",
  graphWidthConfirm: "選択を確定",
  importUserProgress:
    "会話履歴をインポートしています。{count} 件の会話をインポート済みです。記憶は順次表示されます。アプリはそのままお使いいただけます。",
  "importUserProgress.one":
    "会話履歴をインポートしています。{count} 件の会話をインポート済みです。記憶は順次表示されます。アプリはそのままお使いいただけます。",
  importUserModel:
    "モデルがまだ設定されていません。新しい会話は当面、記憶として処理されません。",
  importUserIncomplete:
    "一部の内容が処理しきれませんでした。設定を確認してください。",
  importUserSettings: "設定を開く",
  diagnosticExportDesktop:
    "診断情報はデスクトップアプリから書き出してください。",
  diagnosticExported: "ログを書き出しました：{filename}",
  diagnosticFile: "診断ファイル",
  diagnosticExportFailed:
    "診断情報を書き出せませんでした。もう一度お試しください。",
  debugTitle: "デバッグログ",
  debugCount: "今回のセッションのログ {count} 件",
  "debugCount.one": "今回のセッションのログ {count} 件",
  debugDiagnostics: "診断",
  debugUsageSubtitle: "記憶処理モデルの使用量",
  debugTabs: "診断タブ",
  debugLogsTab: "ログ",
  debugUsageTab: "モデル使用量",
  debugFullscreen: "全画面表示",
  debugExitFullscreen: "全画面表示を終了",
  debugFullscreenLabel: "ログを全画面表示",
  debugExitFullscreenLabel: "ログの全画面表示を終了",
  usageRange15m: "過去 15 分",
  usageRange1h: "過去 1 時間",
  usageRange6h: "過去 6 時間",
  usageRange24h: "過去 24 時間",
  usageRange7d: "過去 7 日間",
  usageRange: "期間",
  usageRangeLabel: "モデル使用量の期間",
  usageLoading: "読み込み中…",
  usageReadError: "読み込めませんでした",
  usageLive: "5 秒ごとに更新",
  usageTotalTokens: "合計トークン数",
  usageRequests: "リクエスト数",
  usageChartRegion: "プロンプト別の 1 分あたりのトークン数",
  usageChartTitle: "プロンプト別のトークン使用量",
  usageChartSubtitle: "1 分あたり · 合計トークン数",
  usageBreakdown: "プロンプトの詳細",
  usageCurrentRange: "現在の期間",
  usageRequestCount: "{count} 回のリクエスト",
  "usageRequestCount.one": "{count} 回のリクエスト",
  usageEmpty: "この期間に記憶処理モデルの呼び出しはありません",
  usageNoChart: "グラフのデータはまだありません",
  usageChartAria: "プロンプト別の 1 分あたりのトークン使用量",
  debugPause: "ライブ更新を一時停止",
  debugResume: "ライブ更新を再開",
  debugRefresh: "今すぐ更新",
  debugClose: "デバッグログを閉じる",
  debugSearchPlaceholder: "イベント、コンポーネント、エラーコード、内容を検索",
  debugSearch: "ログを検索",
  debugSource: "ログのソース",
  debugAllSources: "すべてのソース",
  debugCapture: "収集レベル",
  debugDisplay: "表示レベル",
  debugAll: "すべて",
  debugMatches: "{count} 件一致",
  "debugMatches.one": "{count} 件一致",
  debugLive: "ライブ更新中",
  debugPaused: "一時停止中",
  debugTruncated: "ログが多すぎるため、一部を省略しました",
  debugReadError: "読み込めませんでした",
  debugLoading: "ログを読み込み中…",
  debugEmpty: "一致するログはありません",
  debugOpened: "デバッグログを開いています",
  debugOpen: "デバッグログを開く",
  "这里需要 API Key，不是网址。请填写服务方提供的密钥。":
    "URL ではなく、サービスから提供された API キーを入力してください。",
  "服务商拒绝了这个 API Key（401）。请确认密钥属于当前填写的服务地址。":
    "プロバイダーがこの API キーを拒否しました（401）。入力したエンドポイントに対応するキーか確認してください。",
  "服务商没有这个模型名，请对照服务商的模型列表核对。":
    "プロバイダーがこのモデル名を認識しません。プロバイダーのモデル一覧を確認してください。",
  "连不上接口地址，请检查网络和服务地址是否正确。":
    "エンドポイントに接続できませんでした。ネットワークとサービスのアドレスを確認してください。",
  未提供接口地址: "エンドポイントが指定されていません",
  "请填写生成模型名。": "生成モデル名を入力してください。",
  "请填写完整的 http:// 或 https:// 接口地址。":
    "http:// または https:// で始まるエンドポイント URL を正確に入力してください。",
  "请填写向量模型名。": "埋め込みモデル名を入力してください。",
  "请填写完整的向量模型接口地址。":
    "埋め込みモデルのエンドポイント URL を正確に入力してください。",
  "这份配置在测试期间被改过，刚才测的是改动后的版本。配置已保存但未启用，请重新测试并启用。":
    "テスト中にこの設定が変更されたため、変更後のバージョンがテストされました。設定は保存されましたが、有効になっていません。もう一度テストして有効にしてください。",
  "测试结果来自这份配置更早的版本。配置已保存但未启用，请重新测试并启用。":
    "テスト結果はこの設定の以前のバージョンのものです。設定は保存されましたが、有効になっていません。もう一度テストして有効にしてください。",
  "配置已保存，连接测试未通过。请核对配置后重试，历史对话尚未发送。":
    "設定は保存されましたが、接続テストに失敗しました。設定を確認して再試行してください。会話履歴は送信されていません。",
  "配置已保存，尚未确认启用成功。请核对配置后重试。":
    "設定は保存されましたが、有効化を確認できていません。設定を確認して再試行してください。",
  "配置尚未完成测试和启用，请核对后重试。":
    "テストと有効化が完了していません。設定を確認して再試行してください。",
  "配置尚未保存成功，已填写内容保留，可核对后重试。":
    "設定の保存を確認できませんでした。入力内容は保持されています。確認して再試行してください。",
  "只需配置生成模型。向量嵌入已内置在本机，不为 System 1、System 2 分开选模型。":
    "設定が必要なのは生成モデルのみです。ベクトル埋め込みはこのデバイスに内蔵されており、System 1 と System 2 で別々のモデルを選ぶ必要はありません。",
  "无法读取测试恢复记录，暂不发送新的测试。":
    "テストの復旧記録を読み込めないため、新しいテストは送信しません。",
  "上次模型测试结果待核对，暂不修改或启用配置。":
    "前回のモデルテストの結果を確認する必要があるため、設定の編集や有効化はまだ行いません。",
  核对测试结果: "テスト結果を確認",
  "上次模型创建结果待核对，暂不开始新的操作。":
    "前回のモデル作成の結果を確認する必要があるため、新しい操作はまだ開始しません。",
  核对创建结果: "作成結果を確認",
  "配置已保存，正在测试连接…": "設定を保存しました。接続をテスト中…",
  "连接通过，正在启用模型…": "接続に成功しました。モデルを有効化中…",
  刷新配置列表: "設定一覧を更新",
  自定义一个模型: "カスタムモデルを設定",
  已保存的模型配置: "保存済みのモデル設定",
  "已保存，已启用": "保存済み・有効",
  "已保存，连接通过": "保存済み・接続成功",
  "已保存，测试未通过": "保存済み・テスト失敗",
  "已保存，待测试": "保存済み・テスト待ち",
  版本: "バージョン",
  "需要允许 MemHub 访问钥匙串。系统询问时请选择“始终允许”；已保存的配置不会丢失。":
    "MemHub にキーチェーンへのアクセスを許可してください。確認画面が表示されたら「常に許可」を選択してください。保存済みの設定は失われません。",
  "测试只向此接口发送测试内容，不发送历史对话。":
    "テストではこのエンドポイントにテスト用の内容のみを送信し、会話履歴は送信しません。",
  重试测试并启用: "テストを再試行して有効化",
  测试并启用: "テストして有効化",
  编辑配置: "設定を編集",
  新增配置: "設定を追加",
  编辑: "編集",
  "。修改后需重新测试并设为当前模型，内容授权不会自动更新。":
    "。変更後は、もう一度テストして現在のモデルに設定してください。内容の権限は自動的には更新されません。",
  "配置已在其他位置变化，草稿已保留。请取消编辑后重新打开核对。":
    "設定がほかの場所で変更されました。下書きは保持されています。編集をキャンセルし、開き直して確認してください。",
  服务商预设: "プロバイダーのプリセット",
  自定义兼容服务: "カスタムの互換サービス",
  "只是帮你填好接口地址和一个模型名，不代表这个模型当前可用。 请对照服务商的模型列表核对后再保存。":
    "エンドポイントとモデル名を入力するだけで、そのモデルが利用可能であることを保証するものではありません。保存する前にプロバイダーのモデル一覧を確認してください。",
  协议: "プロトコル",
  "与模型服务提供的接口协议保持一致。":
    "モデルサービスが提供するプロトコルに合わせてください。",
  配置名称: "設定名",
  "用于区分配置，不影响模型请求。":
    "設定を区別するための名前です。モデルへのリクエストには影響しません。",
  生成模型名: "生成モデル名",
  "例如：gpt-4o-mini…": "例：gpt-4o-mini…",
  "填写服务方提供的完整模型标识。":
    "サービスから提供された完全なモデル ID を入力してください。",
  "高级：自行配置向量模型": "詳細：埋め込みモデルを自分で設定",
  向量配置模式: "埋め込みの設定モード",
  本地内置: "内蔵ローカルモデル",
  自定义向量模型: "カスタム埋め込みモデル",
  向量模型名: "埋め込みモデル名",
  "例如：text-embedding-3-small…": "例：text-embedding-3-small…",
  向量模型来源: "埋め込みモデルのプロバイダー",
  "本机 Ollama": "ローカルの Ollama",
  "同源 OpenAI-compatible（共用 API Key）":
    "同一オリジンの OpenAI 互換（API キーを共用）",
  "向量模型 Base URL": "埋め込みモデルの Base URL",
  "为避免凭据外泄，向量地址必须与生成模型同源。":
    "認証情報を保護するため、埋め込みのエンドポイントは生成モデルと同じオリジンである必要があります。",
  "使用内置本地向量模型；不会发送内容给远程向量服务。":
    "内蔵のローカル埋め込みモデルを使用します。リモートの埋め込みサービスに内容は送信されません。",
  自定义向量: "カスタム埋め込み",
  尚未填写模型: "モデル未入力",
  尚未填写地址: "アドレス未入力",
  "填写服务接口地址，不是聊天网页地址。":
    "チャットサイトの URL ではなく、API エンドポイントを入力してください。",
  "（可选）": " （任意）",
  "保存后不会再次显示…": "保存後は再表示されません…",
  "留空保留已保存密钥；填写新密钥后需重新测试和启用。":
    "空欄のままにすると保存済みのキーを維持します。新しいキーを入力した場合は、もう一度テストして有効にしてください。",
  "换了服务商后需要填写新的 API Key，已保存的密钥不会带到新地址。":
    "プロバイダーを変更した場合は新しい API キーを入力してください。保存済みのキーが新しいエンドポイントに送信されることはありません。",
  "保存在系统安全存储中，不随安装包分发。":
    "システムの安全な保管場所に保存され、アプリと一緒に配布されることはありません。",
  "关闭思考链（向支持的厂商下发 thinking-off 参数）":
    "思考プロセスを無効化（対応プロバイダーに thinking-off を送信）",
  保存修改: "変更を保存",
  保存配置: "設定を保存",
  "正在启用配置…": "設定を有効化中…",
  测试并完成配置: "テストして設定を完了",
  保存并测试启用: "保存・テストして有効化",
  取消编辑: "編集をキャンセル",
  "密钥保存在系统安全存储；测试仅发送测试内容。":
    "キーは安全に保存されます。テストではテスト用の内容のみを送信します。",
  当前使用: "使用中",
  未启用: "無効",
  测试目标: "テスト対象",
  "。仅发送测试内容，不发送历史对话。":
    "。テスト用の内容のみを送信し、会話履歴は送信しません。",
  测试连接: "接続をテスト",
  设为当前模型: "現在のモデルに設定",
  删除: "削除",
  "粘贴 API Key": "API キーを貼り付け",
  "密钥尚未保存成功，已填写内容保留，可重试。":
    "キーを保存できませんでした。入力内容は保持されています。再試行できます。",
  生成接收方: "生成の送信先",
  向量接收方: "埋め込みの送信先",
  本机: "このデバイス",
  "。启用此配置后按上方选择处理历史及未来允许来源。":
    "。この設定を有効にすると、上での選択に従って、許可された過去と今後のソースが処理されます。",
  "保存 key": "キーを保存",
  "还没有 key？去申请": "キーをお持ちでない場合は取得",
  删除这张卡: "このカードを削除",
  "密钥已配置，当前读取状态待确认。":
    "キーは設定済みです。アクセス状況の確認が必要です。",
  "等待 key：其余都已经填好了，贴上 key 就能用。":
    "キー待ち：ほかの設定は完了しています。キーを貼り付ければ使用できます。",
  "key 已保存，还没测过这个模型能不能用。":
    "キーは保存済みです。このモデルはまだテストされていません。",
  "Ollama（本机）": "Ollama（ローカル）",
  "内置 bge-small-zh-v1.5": "内蔵 bge-small-zh-v1.5",
  通过: "成功",
  "HY 托管模型尚不可用": "HY ホスト型モデルは利用できません",
  "缺少 API Key": "API キーがありません",
  服务不可达: "サービスに到達できません",
  "认证失败；更新 key 后再检测，会自动接着处理":
    "認証に失敗しました。キーを更新してもう一度テストすると、処理が再開されます。",
  请求频率受限: "レート制限中",
  "模型账户余额不足；充值或换 key 后再检测，会自动接着处理":
    "モデルのアカウント残高が不足しています。チャージするかキーを変更してもう一度テストすると、処理が再開されます。",
  没有找到模型: "モデルが見つかりません",
  "该网关不提供模型列表，已改由生成调用验证":
    "このゲートウェイにはモデル一覧がないため、生成リクエストで検証しました。",
  服务返回不符合协议: "サービスの応答がプロトコルに準拠していません",
  结构化输出未通过: "構造化出力のテストに失敗",
  "需要填写 Embedding 模型": "埋め込みモデルを入力してください",
  "Embedding 模型不可用": "埋め込みモデルを利用できません",
  "请先核对上次模型测试结果，暂不修改或启用配置。":
    "まず前回のモデルテストの結果を確認してください。設定の変更や有効化はまだ行いません。",
  "本地恢复记录已变化，仍保持锁定。请重新打开后核对。":
    "ローカルの復旧記録が変更されました。設定はロックされたままです。開き直して確認してください。",
  测试结果: "テスト結果",
  "暂不支持一键插入：它没有 MCP 通道，无法发放可撤销的记忆凭证。":
    "ワンクリック接続は利用できません：このアプリには、取り消し可能な記憶の認証情報を発行する MCP チャネルがありません。",
  应用控制通道暂不可用: "アプリの制御チャネルを利用できません",
  appFailureReason: " 理由：{reason}。",
  appFailureReread:
    "接続状況も再読み込みできませんでした。表示されている状態は古い可能性があります。",
  模型: "モデル",
  连接其他应用: "ほかのアプリを接続",
  帮助与诊断: "ヘルプと診断",
  设置分组: "設定のセクション",
  记忆模型: "記憶モデル",
  当前模型: "現在のモデル",
  "分别管理记忆整理与搜索所使用的模型。":
    "記憶の整理と検索に使うモデルを個別に管理します。",
  记忆整理: "記憶の整理",
  "把会话理解并整理成可长期使用的记忆。":
    "会話を理解し、長期的に活用できる記憶として整理します。",
  记忆搜索: "記憶の検索",
  "在需要时找到与你的问题最相关的记忆。":
    "必要なときに、質問に最も関連する記憶を見つけます。",
  "本地内置（推荐）": "ローカル内蔵（推奨）",
  "Embedding · 配置记忆整理后可自定义":
    "Embedding · 記憶の整理を設定後にカスタマイズ可能",
  "Embedding · 本机处理，无需额外配置":
    "Embedding · このデバイスで処理、追加設定不要",
  "正在读取模型…": "モデルを読み込み中…",
  模型状态暂不可读: "モデルの状態を取得できません",
  尚未配置模型: "モデル未設定",
  修改模型: "モデルを編集",
  配置模型: "モデルを設定",
  刷新模型状态: "モデルの状態を更新",
  "本机 / 受管端点": "ローカル / 管理対象エンドポイント",
  接口地址待核对: "エンドポイントの確認が必要",
  "模型状态未能读取，请重试。":
    "モデルの状態を読み込めませんでした。もう一度お試しください。",
  "模型状态刷新未成功，仍显示上次确认的模型状态；请重试。":
    "モデルの状態を更新できませんでした。前回確認した状態を表示しています。もう一度お試しください。",
  "上次模型测试或创建还待核对，配置暂时不能改动。请先在下方模型配置区核对结果； 现有配置和原文不会被改动。":
    "前回のモデルのテストまたは作成の結果を確認する必要があります。編集する前に下で結果を確認してください。既存の設定と原文は変更されません。",
  用模型沉淀记忆: "モデルで記憶を作成",
  "开启后，导入和同步到本机的会话会交给当前模型整理成记忆，会产生模型调用。 停止后不再调用模型、不再整理记忆，但会话仍会照常导入和保存；已排队的内容会留着，重新开启后继续。 模型配置始终保留，重新开启不需要重新填写。":
    "オンにすると、インポート・同期された会話が現在のモデルによって記憶に処理され、モデルの呼び出しが発生します。オフにしても会話は通常どおりインポート・保存され、待機中の処理は再びオンにしたときに再開されます。モデルの設定はそのまま保持されます。",
  "启用未能确认，仍显示上次确认的状态；请重试。":
    "有効化を確認できませんでした。前回確認した状態を表示しています。もう一度お試しください。",
  "停止未能确认，模型可能仍在整理记忆，请重试。":
    "停止を確認できませんでした。モデルがまだ記憶を処理している可能性があります。もう一度お試しください。",
  "当前安装还不能读取这个开关，请确认后台服务已启动后重试。":
    "このインストールではこのスイッチを読み込めません。バックグラウンドサービスが起動していることを確認して再試行してください。",
  关于: "このアプリについて",
  "此应用还没有读取授权，请先授权它的对话目录。":
    "このアプリにはまだ読み取り権限がありません。まず会話フォルダを承認してください。",
  "此应用的读取授权已撤销，请重新授权。":
    "このアプリの読み取り権限は取り消されました。もう一度承認してください。",
  "此应用的对话目录已被重建，请重新授权。":
    "このアプリの会話フォルダが再作成されました。もう一度承認してください。",
  "此应用的对话目录现在读不到，请确认应用已安装并产生过对话。":
    "このアプリの会話フォルダを読み込めません。アプリがインストールされ、会話があることを確認してください。",
  "此应用的对话目录已变更，请按最新目录重新授权。":
    "このアプリの会話フォルダが変更されました。新しいフォルダを承認してください。",
  "此应用还没有读取授权，请先授权它的对话目录。完成后再开启持续同步。":
    "このアプリにはまだ読み取り権限がありません。会話フォルダを承認してから、継続的な同期をオンにしてください。",
  "此应用的读取授权已撤销，请重新授权。完成后再开启持续同步。":
    "このアプリの読み取り権限は取り消されました。もう一度承認してから、継続的な同期をオンにしてください。",
  "此应用的对话目录已被重建，请重新授权。完成后再开启持续同步。":
    "このアプリの会話フォルダが再作成されました。もう一度承認してから、継続的な同期をオンにしてください。",
  "此应用的对话目录现在读不到，请确认应用已安装并产生过对话。完成后再开启持续同步。":
    "このアプリの会話フォルダを読み込めません。アプリがインストールされ、会話があることを確認してから、継続的な同期をオンにしてください。",
  "此应用的对话目录已变更，请按最新目录重新授权。完成后再开启持续同步。":
    "このアプリの会話フォルダが変更されました。新しいフォルダを承認してから、継続的な同期をオンにしてください。",
  "此应用的读取授权已失效，请重新授权后再开启持续同步。":
    "このアプリの読み取り権限は失効しました。もう一度承認してから、継続的な同期をオンにしてください。",
  "此应用的授权或目录已变化，已按最新状态显示，请确认后再试。":
    "このアプリの権限またはフォルダが変更されました。最新の状態を確認して、もう一度お試しください。",
  模型配置表单: "モデル設定フォーム",
  "部分运行状态暂时无法读取，当前显示最后读到的状态，请重试。":
    "一部の状態情報を取得できません。最後に取得した状態を表示しています。もう一度お試しください。",
  "当前安装还不能设置登录后后台启动。":
    "このインストールではログイン時の起動を設定できません。",
  "登录后后台启动设置未能确认，请重试。":
    "ログイン時の起動設定を確認できませんでした。もう一度お試しください。",
  "当前安装还不能退出界面，请从菜单栏退出。":
    "このインストールではウインドウを終了できません。メニューバーから終了してください。",
  "退出界面未能确认，请重试。":
    "終了を確認できませんでした。もう一度お試しください。",
  登录后启动后台服务: "ログイン時にバックグラウンドサービスを起動",
  "无需打开界面，持续同步、整理和提供记忆。":
    "アプリを開かなくても、記憶の同期、処理、提供を続けます。",
  导出日志: "ログを書き出す",
  "把本机运行日志打包成一个文件，发给开发排查。":
    "ローカルログをすべてまとめます。会話本文やローカルパスが含まれる場合があるため、信頼できる開発者にのみ共有してください。",
  "确定退出 One Memory 界面吗？": "MemHub のウインドウを終了しますか？",
  "后台服务会继续同步、整理和提供记忆。":
    "バックグラウンドサービスは記憶の同期、処理、提供を続けます。",
  取消: "キャンセル",
  退出界面: "ウインドウを終了",
  应用详情: "アプリの詳細",
  返回应用: "アプリに戻る",
  同步开关: "同期スイッチ",
  "开启后处理历史待办，并持续接收此应用的新会话。":
    "オンにすると、未処理の履歴を処理し、このアプリの新しい会話を継続的に受け取ります。",
  "正在读取已有对话，首次开启可能需要一两分钟，请不要重复点击。":
    "既存の会話を読み込んでいます。初回は 1〜2 分かかることがあります。何度もクリックしないでください。",
  "将补充冻结期间的新对话；原有 pending Job 继续处理。":
    "凍結中に受け取った会話を補完します。既存の保留中のジョブは引き続き処理されます。",
  一键插入应用: "ワンクリックでアプリに接続",
  "安装记忆连接，并默认允许此应用使用已授权记忆。":
    "記憶の接続をインストールし、既定でこのアプリに承認済みの記憶の使用を許可します。",
  允许使用记忆: "記憶の使用を許可",
  连接状态: "接続状況",
  已接通: "接続済み",
  "已安装，但取不到记忆": "インストール済みですが、記憶を取得できません",
  未接通: "未接続",
  状态待核对: "状態の確認が必要",
  检测中: "確認中",
  未开启关联: "接続オフ",
  "正在开启处理…": "処理を開始中…",
  "正在冻结处理…": "処理を凍結中…",
  处理中: "処理中",
  已冻结: "凍結中",
  "此应用的处理开关结果未能确认，其他应用不受影响。":
    "このアプリの処理スイッチの状態を確認できませんでした。ほかのアプリには影響ありません。",
  "暂时无法刷新来源状态，请重试；未更改同步或读取权限。":
    "ソースの状態を更新できませんでした。再試行してください。同期と読み取りの権限は変更されていません。",
  "暂时无法读取连接状态，当前状态待核对；请重试。":
    "接続状況を読み込めませんでした。現在の状態を確認する必要があります。もう一度お試しください。",
  "已接通。已经打开的对话不会中途出现这个工具，请新开一轮。":
    "接続しました。すでに開いている会話にはこのツールは表示されません。新しい会話を始めてください。",
  "本次连接探测失败，当前状态待核对；未把上次读取当作本次成功。":
    "今回の接続チェックに失敗しました。現在の状態を確認する必要があります。前回の結果は今回の成功として扱われていません。",
  上次读取: "前回の読み込み",
  appSettingsTitle: "{name} のアプリ設定",
  appMemoryScope: "承認された範囲内で、{name} に記憶の照会を許可します。",
  appRestart:
    "記憶ツールは {name} の現在の会話には含まれていません。{name} を完全に終了してから開き直すと、新しい会話に表示されます。",
  appConnectFailed:
    "このアプリのワンクリック接続は完了しませんでした。ほかのアプリには影響ありません。{reason}",
  appDisconnectFailed:
    "接続を削除できず、記憶へのアクセスも取り消せませんでした。{name} は引き続き記憶を使用できます。もう一度お試しください。{reason}",
  appPermissionFailed:
    "このアプリの記憶へのアクセスを確認できませんでした。もう一度お試しください。{reason}",
  appRevokedModified:
    "記憶へのアクセスを取り消したため、{name} は記憶を照会できなくなりました。設定ファイルが変更されていたため、MemHub は自動削除していません。MemHub の接続を手動で削除してください。",
  appRevokedRetained:
    "記憶へのアクセスを取り消したため、{name} は記憶を照会できなくなりました。接続ファイルは削除されていません。もう一度確認してください。",
  appRevokedReview:
    "記憶へのアクセスを取り消したため、{name} は記憶を照会できなくなりました。接続が完全に削除されたか確認できませんでした。次のパスを手動で確認してください：{paths}。",
  appRemovalReview:
    "接続が完全に削除されたか確認できませんでした。次のパスを手動で確認してください：{paths}。",
  appProfilesLabel: "{name} のプロファイル",
  appProfileOn: "{profile}：接続済み",
  appProfileOff: "{profile}：未接続",
  appProfilesHint:
    "このスイッチは検出されたすべてのプロファイルに記憶の接続をインストールしてアクセスを許可し、オフにするとすべて取り消します。オンの間は、新しいプロファイルも自動的に接続されます。",
  appProfilesNone:
    "{name} のプロファイルが検出されませんでした。プロファイルを作成してから、このページに戻ってください。",
  appProfilesConnectFailed:
    "次のプロファイルを接続できませんでした：{profiles}。ほかのプロファイルはそのまま維持されます。{reason}",
  appProfilesDisconnectFailed:
    "次のプロファイルの接続を解除できず、引き続き記憶を使用できます：{profiles}。もう一度お試しください。{reason}",
  appProfilesRevokedRetained:
    "次のプロファイルの記憶へのアクセスを取り消しましたが、接続ファイルの一部は削除されていません。確認してください：{profiles}。",
  appProfileNameUnsupported:
    "プロファイル名に使えるのは小文字、数字、ハイフンのみで、32 文字までです",
  "apps.autoHostProfilesFailed":
    "{name} の次のプロファイルを自動的に接続できませんでした（{profiles}）{reason}。",
  "Cursor 的连接凭证已更新。请完全退出并重新打开 Cursor，再开始新对话。":
    "Cursor の接続の認証情報が更新されました。Cursor を完全に終了して開き直してから、新しい会話を始めてください。",
  连接其他工具: "ほかのツールを接続",
  "本机连接材料仅在桌面 App 中可用。":
    "ローカルの接続情報はデスクトップアプリでのみ利用できます。",
  "仅本机 MCP / 命令工具。纯网页聊天不支持直接连接本机。":
    "ローカルの MCP またはコマンドラインツール専用です。ブラウザーのチャットからはこのデバイスに直接接続できません。",
  "列表内工具请用上方一键安装，此处仅供未列出的工具。":
    "一覧にあるツールは上のワンクリック設定を使用してください。ここは一覧にないツール用です。",
  "正在读取本机连接配置…": "ローカルの接続情報を読み込み中…",
  "未能读取连接配置。": "接続情報を読み込めませんでした。",
  重试: "再試行",
  连接材料格式: "接続情報の形式",
  连接提示词: "接続プロンプト",
  "MCP 配置": "MCP 設定",
  命令: "コマンド",
  本机连接材料: "ローカルの接続情報",
  "包含本机路径，不含记忆和密钥。片段会运行本机 MemHub 连接程序；粘贴前请确认，MemHub 需保持运行。":
    "ローカルのパスを含みますが、記憶やキーは含みません。このスニペットはローカルの MemHub コネクタを実行します。貼り付ける前に内容を確認し、MemHub を起動したままにしてください。",
  "已复制，尚未建立连接。": "コピーしました。まだ接続は行われていません。",
  "复制失败，可在上方选中文本复制。":
    "コピーできませんでした。上のテキストを選択してコピーしてください。",
  复制提示词: "プロンプトをコピー",
  复制配置: "設定をコピー",
  复制命令: "コマンドをコピー",
  记忆库分页: "記憶ライブラリのページ",
  上一页: "前のページ",
  下一页: "次のページ",
  "把熟悉的背景，带到这里": "いつもの背景情報を、ここに",
  "你的记忆，从这里接上": "あなたの記憶を、ここでひとつに",
  "这张卡还没测过。点一次「设为当前模型」会先自动测，如果还是不行，说明端点、key 或模型名有一处不对。":
    "この設定はまだテストされていません。「現在のモデルに設定」を選ぶと最初にテストが実行されます。それでも失敗する場合は、エンドポイント、キー、モデル名を確認してください。",
  "这类模型接口暂时用不了，换一张卡。":
    "この種類のモデルエンドポイントはまだサポートされていません。別の設定を使用してください。",
  "这张卡缺了向量化模型，补上才能启用。":
    "この設定を有効にするには埋め込みモデルが必要です。",
  "这张卡已经不在了，可能刚被删掉。刷新一下。":
    "この設定は存在しません。削除された可能性があります。一覧を更新してください。",
  "这张卡刚被改过。刷新一下再试，免得覆盖掉别处的改动。":
    "この設定は変更されたばかりです。ほかの変更を上書きしないよう、更新してから再試行してください。",
  onboardingModelOperationFailed: "操作に失敗しました：{code}",
  onboardingModelSkipDestination: "スキップしてアプリへ",
  首次设置进度: "初期設定の進捗",
  导入历史: "履歴をインポート",
  连接应用: "アプリを接続",
  沉淀记忆: "記憶を作成",
  本地存储: "ローカルに保存",
  "对话与记忆保存在本机；使用云模型或已授权的助手时，相关内容会发送给对应服务。":
    "会話と記憶はこのデバイスに保存されます。クラウドのモデルや承認済みのアシスタントを使う場合は、関連する内容がそのサービスに送信されます。",
  "来源位置、身份或状态已变化，请重新选择并核对。尚未提交本次授权。":
    "ソースの場所、身元、または状態が変更されました。もう一度選択して確認してください。今回の承認は送信されていません。",
  "来源位置、身份或状态已变化，正在等待本次提交回执。不会自动扩大已提交范围。":
    "ソースの場所、身元、または状態が変更されました。今回の送信の確認を待っています。送信済みの範囲が自動的に広がることはありません。",
  测试实例不接入应用: "テストインスタンスはアプリに接続しません",
  "请先把 MemHub 拖到“应用程序”并从那里重新打开，才能接入应用。":
    "アプリに接続するには、MemHub を「アプリケーション」フォルダに移動し、そこから開き直してください。",
  "部分应用未能完成接入，已成功的设置会保留，可进入应用后单独处理。":
    "一部のアプリに接続できませんでした。成功した設定は保持されます。残りは「アプリ」で個別に対応してください。",
  onboardingConnectionPartial:
    "{success} 個のアプリを接続しました。{failed} 個は未完了です：{names}。「アプリ」で個別に対応できます。",
  "首次设置的完成状态未能保存。已确认的导入回执不受影响；下次启动可能再次出现此页。":
    "初期設定の完了状態を保存できませんでした。確認済みのインポート記録には影響ありません。次回起動時にこのページが再表示される場合があります。",
  "首次设置的完成状态尚未确认。已确认的导入回执不受影响；下次启动可能再次出现此页。":
    "初期設定の完了状態を確認できていません。確認済みのインポート記録には影響ありません。次回起動時にこのページが再表示される場合があります。",
  "位置检测未完成，尚未读取历史。可以重新检测，或先进入工作台。":
    "場所の検出が完了していないため、履歴は読み込まれていません。もう一度確認するか、ワークスペースに進んでください。",
  "暂时无法核对来源，尚未提交授权。选择已保留，可以重试。":
    "現在ソースを確認できないため、承認は送信されていません。選択内容は保持されています。再試行できます。",
  "本次没有来源获准导入。请核对下方原因后重试，或到应用页查看状态。":
    "今回インポートが許可されたソースはありません。下の理由を確認して再試行するか、「アプリ」で状態を確認してください。",
  "开始导入的结果尚未确认，选择已保留。可以重试，或进入应用页核对。":
    "インポートの開始を確認できていません。選択内容は保持されています。再試行するか、「アプリ」で確認してください。",
  未完成: "未完了",
  核对中: "確認中",
  仍进入应用页: "このまま「アプリ」へ進む",
  仍进入工作台: "このままワークスペースへ進む",
  仍进入设置: "このまま設定へ進む",
  重试保存并进入: "保存を再試行して続行",
  "稍后添加，先进入": "あとで追加して続行",
  "正在开始导入…": "インポートを開始中…",
  开始导入: "インポートを開始",
  "不用从头介绍自己。从常用的 App 开始，把已有背景带过来。":
    "一から自己紹介する必要はありません。よく使うアプリから背景情報を引き継ぎましょう。",
  "确认前只检查应用信息，不读取对话，也不安装连接。":
    "確認するまではアプリ情報のみをチェックします。会話の読み込みや接続のインストールは行いません。",
  "本机 AI 应用": "このデバイスの AI アプリ",
  重新检测位置: "場所を再確認",
  "检测中…": "確認中…",
  重新检测: "再確認",
  候选应用: "利用可能なアプリ",
  "正在检测其他应用…": "ほかのアプリを検出中…",
  检测未完成: "確認未完了",
  已全选: "すべて選択済み",
  全选: "すべて選択",
  onboardingImportHistory: "{name} の履歴をインポート",
  正在核对本机保存位置: "ローカルの保存場所を確認中",
  已发现: "検出済み",
  位置待核对: "場所の確認が必要",
  "当前没有候选来源。可以稍后在应用页添加历史包。":
    "ソースが見つかりません。あとで「アプリ」から履歴アーカイブを追加できます。",
  "候选来源尚未加载。可以检测位置，或先进入工作台。":
    "ソースがまだ読み込まれていません。場所を確認するか、ワークスペースに進んでください。",
  本次导入: "今回のインポート",
  "个 App 的历史对话": "個のアプリの会話履歴",
  "把零散的会话，": "散らばった会話を、",
  汇成完整的记忆: "ひとつの記憶に",
  "少一些重复交代，多一些理解。": "繰り返しの説明を減らし、理解を深める。",
  onboardingMoreApps: "スクロールしてさらに表示 · 残り {count} 個のアプリ",
  "onboardingMoreApps.one":
    "スクロールしてさらに表示 · 残り {count} 個のアプリ",
  onboardingMoreImportApps: "横にスクロール · 残り {count} 個のアプリ",
  onboardingImportChecking:
    "インポート結果を確認しています。選択内容は保持されています",
  onboardingAllApps: "全 {count} 個のアプリを表示中",
  默认位置: "既定の場所",
  自定义位置: "カスタムの場所",
  尚未检测: "未確認",
  未发现: "見つかりません",
  无法访问: "アクセスできません",
  位置无效: "無効な場所",
  "需要更新 Core": "Core の更新が必要",
  "候选位置不存在，可能尚无历史或路径不同。":
    "この場所は存在しません。まだ履歴がないか、パスが異なる可能性があります。",
  "无法检查此位置，请核对目录访问权限后重试。":
    "この場所を確認できませんでした。フォルダへのアクセス権を確認して再試行してください。",
  "此位置未通过安全检查，不能读取。":
    "この場所はセキュリティチェックに合格しなかったため、読み込めません。",
  "当前 Core 未提供可确认的位置身份，需更新后再选择。":
    "現在の Core は検証可能な場所の識別情報を提供していません。更新してから選択してください。",
  暂不支持历史导入: "履歴のインポートには未対応",
  "当前来源不可读取，请重新检测。":
    "現在のソースを読み込めません。もう一度確認してください。",
  "来源目录暂时不可读取，请核对位置和权限。":
    "ソースのフォルダを一時的に読み込めません。場所と権限を確認してください。",
  "来源目录身份已变化，请返回重新检测。":
    "ソースのフォルダの識別情報が変更されました。戻ってもう一度確認してください。",
  "来源权限已变化，请返回重新确认。":
    "ソースの権限が変更されました。戻ってもう一度確認してください。",
  "此来源尚未获得读取授权。": "このソースにはまだ読み取り権限がありません。",
  "自定义来源位置需要重新确认。":
    "カスタムのソースの場所をもう一度確認する必要があります。",
  "当前 Core 未启用历史导入。":
    "現在の Core では履歴のインポートが有効になっていません。",
  onboardingImportRefused:
    "インポートが許可されていません（{reason}）。ソースを確認して再試行してください。",
  确认自定义位置: "カスタムの場所を確認",
  关闭位置确认: "場所の確認を閉じる",
  "此位置来自当前配置，并非应用默认目录。确认位置不会开始读取历史。":
    "この場所はアプリの既定のフォルダではなく、現在の設定に基づくものです。場所を確認しても履歴の読み込みは始まりません。",
  onboardingConfirmHistory: "{name} の履歴の場所であることを確認",
  确认此位置: "この場所を確認",
  "对话正在汇入，记忆从这里开始":
    "会話を取り込み中です。記憶はここから始まります",
  "导入进行中 · 顺手连接应用": "インポート中 · アプリも接続しましょう",
  "对话已保存，记忆尚待生成。":
    "会話は保存されました。記憶はまだ作成されていません。",
  "导入在后台继续，无需等待。":
    "インポートはバックグラウンドで続きます。待つ必要はありません。",
  "正在重新核对来源；已保存数量仍按任务回执显示。":
    "ソースを再確認中です。保存済みの件数はジョブの記録に基づいて表示されます。",
  "本次历史导入已暂停，已保存的对话还在。":
    "今回の履歴のインポートは一時停止中です。保存済みの会話は引き続き利用できます。",
  "导入等待处理，已保存的对话还在。":
    "インポートは処理待ちです。保存済みの会話は引き続き利用できます。",
  停止后续操作: "残りの操作を停止",
  暂停导入: "インポートを一時停止",
  继续导入: "インポートを再開",
  "配置模型，沉淀记忆": "モデルを設定して記憶を作成",
  导入与原文: "インポートと原文",
  "本次导入已结束。已保存的对话无需重复导入。":
    "今回のインポートは完了しました。保存済みの会話を再度インポートする必要はありません。",
  "对话先保存在本机。你可以同时设置后续连接。":
    "会話はまずこのデバイスに保存されます。その間に接続を設定できます。",
  "导入不会自动开放应用读取权限。配置模型后，才会开始生成记忆。":
    "インポートしても、アプリに読み取り権限が自動的に付与されることはありません。記憶はモデルを設定してから作成されます。",
  onboardingHistoryWindow:
    "大量の会話データが見つかりました。使用量を抑え、アプリ間のタイムラインをそろえるため、今回はすべてのアプリで過去 3 日間の会話のみをインポートします。",
  导入说明: "インポートについて",
  正在导入你的会话: "会話をインポート中",
  "后台将持续为你导入，可以放心进入下一步":
    "インポートはバックグラウンドで続きます。次に進んで問題ありません",
  未开始的导入: "開始できなかったインポート",
  "未能开始导入，其他已接受任务不受影响。":
    "インポートを開始できませんでした。受け付け済みのほかのジョブには影響ありません。",
  查看未导入的应用: "インポートされていないアプリを表示",
  让记忆继续生长: "記憶を育て続ける",
  连接你的应用: "アプリを接続",
  "适用于本次选择且支持的应用。":
    "選択したアプリのうち、この接続に対応しているものが対象です。",
  一键接入应用: "アプリをまとめて接続",
  "安装记忆连接，让所选应用用上 MemHub 中的相关记忆。":
    "記憶の接続をインストールし、選択したアプリが MemHub の関連する記憶を使えるようにします。",
  "当前使用独立测试数据目录，不会修改本机应用的连接配置。":
    "このインスタンスは独立したテスト用データフォルダを使用しているため、ローカルのアプリの接続設定は変更されません。",
  "点击底部按钮后，后台分别接入各应用；某个应用失败不影响其他应用。":
    "下のボタンをクリックすると、バックグラウンドで各アプリが個別に接続されます。1 つが失敗してもほかのアプリには影響しません。",
  "暂时无法连接应用，当前不会安装连接。":
    "現在アプリに接続できません。接続はインストールされません。",
  onboardingBatchStopped:
    "残りの操作を停止しました。送信済みの操作は記録で確認してください。",
  onboardingBatchResult: "確認済み {confirmed} 件、未確認 {failed} 件。",
  "已确认项保留，请核对未确认任务后重试。":
    " 確認済みの操作は保持されます。未確認のジョブを確認してから再試行してください。",
  本次应用导入进度: "選択したアプリのインポート進捗",
  正在导入: "インポート中",
  已暂停: "一時停止中",
  已中断: "中断",
  导入遇到问题: "インポートで問題が発生",
  导入完成: "インポート完了",
  已取消: "キャンセル済み",
  已保存: "保存済み",
  onboardingAppProgress: "{name} のインポート進捗",
  onboardingSavedMessages: "{count} 件のメッセージを保存済み",
  onboardingScannedOutsideRange:
    "{count} 件をスキャンしました。期間内の会話はありません",
  onboardingScannedNothingToSave:
    "{count} 件をスキャンしました。保存する会話はありません",
  onboardingScannedFilteringRecent:
    "{count} 件をスキャンしました。過去 3 日間に絞り込み中",
  onboardingScannedFiltering: "{count} 件をスキャンしました。絞り込み中",
  onboardingScannedNotSaved:
    "{count} 件をスキャンしました。まだ保存されていません",
  没有可保存的历史对话: "保存する会話履歴はありません",
  读取授权不可用: "読み取り権限を利用できません",
  onboardingReadErrors: " · 読み込みエラー {count} 件",
  "配置详情，按需要调整": "設定の詳細（必要に応じて調整）",
  "连接未通过，配置已保留": "接続に失敗しました。設定は保持されています",
  "选好模型，让记忆开始沉淀": "モデルを選んで記憶の作成を始める",
  "连接设置已保存\n成功项保留，不重复执行。":
    "接続設定を保存しました。\n成功した手順は保持され、繰り返し実行されません。",
  "已选择：持续同步 · 一键接入": "選択済み：継続的な同期 · ワンクリック接続",
  "沉淀记忆 · 使用你选择的模型": "記憶を作成 · 選択したモデルを使用",
  "当前无法启用模型，已填写内容保留。":
    "現在モデルを有効にできません。入力内容は保持されています。",
  "模型连接测试通过，可以启用并开始沉淀记忆。":
    "モデルの接続テストに成功しました。有効にすると記憶の作成が始まります。",
  "先测试模型连通性；测试通过后再启用。":
    "まずモデルの接続をテストし、成功してから有効にしてください。",
  "跳过，先进入应用": "スキップしてアプリへ",
  "正在测试模型…": "モデルをテスト中…",
  测试模型连通性: "モデルの接続をテスト",
  "正在启用…": "有効化中…",
  确认启用: "確認して有効化",
  核对提交状态: "送信状況を確認",
  模型配置: "モデル設定",
  模型参数: "モデルのパラメーター",
  "选择服务商，填入 API Key。核对已有配置后再继续。":
    "プロバイダーを選んで API キーを入力してください。既存の設定を確認してから続行してください。",
  模型服务商: "モデルのプロバイダー",
  选择服务商: "プロバイダーを選択",
  "已安全保存，留空保留": "安全に保存済み（空欄のままで維持）",
  "输入服务商提供的 API Key": "プロバイダーから提供された API キーを入力",
  "连接未通过，请检查 API Key、模型与接口地址后重试。配置已保留。":
    "接続に失敗しました。API キー、モデル、エンドポイントを確認して再試行してください。設定は保持されています。",
  "需要允许 MemHub 访问钥匙串。系统询问时请选择“始终允许”。":
    "MemHub にキーチェーンへのアクセスを許可してください。確認画面が表示されたら「常に許可」を選択してください。",
  "留空保留已保存密钥；填写新密钥后需重新测试。":
    "空欄のままにすると保存済みのキーを維持します。新しいキーを入力した場合は、もう一度テストしてください。",
  "密钥保存在系统安全存储中，不写入对话或日志。":
    "キーはシステムの安全な保管場所に保存され、会話やログには書き込まれません。",
  配置详情与高级设置: "設定の詳細と高度な設定",
  服务商待选择: "プロバイダー未選択",
  模型待填写: "モデル未入力",
  本地向量模型: "ローカルの埋め込みモデル",
  向量模型待填写: "埋め込みモデル未入力",
  思考已关闭: "思考モード無効",
  思考未关闭: "思考モード有効",
  "预设已填好，只有自定义服务或特殊需求才需要修改。":
    "プリセットは入力済みです。カスタムサービスや特別な要件がある場合のみ変更してください。",
  返回服务商选择: "プロバイダーの選択に戻る",
  模型名称: "モデル名",
  "Anthropic（暂不支持）": "Anthropic（未対応）",
  "HY 托管（当前不可用）": "HY マネージド（現在利用不可）",
  向量模型: "埋め込みモデル",
  "本地 ONNX · 内置": "ローカル ONNX · 内蔵",
  "超时（秒）": "タイムアウト（秒）",
  最大并发: "最大同時実行数",
  上下文窗口: "コンテキストウィンドウ",
  "自动 / 可选": "自動 / 任意",
  关闭思考模式: "思考モードを無効化",
  "自定义向量配置可展开；远程向量服务须遵守凭据边界。":
    "カスタムの埋め込み設定を展開できます。リモートの埋め込みサービスは認証情報の境界を守る必要があります。",
  "切换服务商或模型后，需要重新测试并启用。":
    "プロバイダーまたはモデルを変更した場合は、もう一度テストして有効にしてください。",
  向量模型名称: "埋め込みモデル名",
  向量协议: "埋め込みのプロトコル",
  "同源 OpenAI-compatible（共用 Key）":
    "同一オリジンの OpenAI 互換（キーを共用）",
  "向量 Base URL": "埋め込みの Base URL",
  "远程向量地址须与生成服务同源；本机地址仍需校验。":
    "リモートの埋め込みエンドポイントは生成サービスと同じオリジンである必要があります。ローカルのエンドポイントも検証が必要です。",
  "放弃旧记录，重新配置": "古い記録を破棄して再設定",
  模型提交状态: "モデルの送信状況",
  "正在测试连接…": "接続をテスト中…",
  "正在提交保存与启用，请等待回执…":
    "保存と有効化を送信中です。確認をお待ちください…",
  "操作结果未确认，已填写内容保留，请核对后重试。":
    "結果を確認できていません。入力内容は保持されています。確認してから再試行してください。",
  本次沉淀: "今回の記憶の設定",
  让记忆从这里开始: "ここから記憶の作成を始める",
  对话来源: "会話のソース",
  尚无已选来源: "ソースが選択されていません",
  onboardingSourcesSummary: "{names} など、合計 {count} 個のアプリ",
  onboardingImportedConversations:
    "{count} 件の会話をインポートしました。今後このデバイスに同期される内容は、引き続きソースのスイッチで制御されます。",
  "处理服务：": "処理サービス：",
  待选择: "未選択",
  "相关对话会发送给所选服务商。":
    "関連する会話は選択したプロバイダーに送信されます。",
  "测试连接只发送测试内容。启用后会使用此模型整理已导入和之后同步的会话。":
    "接続テストではテスト用の内容のみを送信します。有効にすると、インポート済みの会話と今後同期される会話がこのモデルで処理されます。",
  "当前不能在此启用模型。": "現在ここではモデルを有効にできません。",
  "配置已修改，需要重新测试。":
    "設定が変更されました。もう一度テストしてください。",
  onboardingIntegerRange: "{min}〜{max} の整数を入力してください。",
  "请填写配置名称。": "設定名を入力してください。",
  "请填写实际可用的模型 ID。": "利用可能なモデル ID を入力してください。",
  "请填写有效的模型接口地址。":
    "有効なモデルのエンドポイント URL を入力してください。",
  "请填写 API Key。": "API キーを入力してください。",
  "请填写向量模型 ID。": "埋め込みモデルの ID を入力してください。",
  "配置结果尚未确认，请在模型设置中核对后重试。":
    "設定の結果を確認できていません。モデルの設定で確認してから再試行してください。",
  "模型连接测试通过，可以启用。":
    "モデルの接続テストに成功しました。有効にできます。",
  "连接未通过，请核对模型、密钥和接收方。":
    "接続に失敗しました。モデル、キー、送信先を確認してください。",
  "配置或测试结果暂未确认，请核对后重试。":
    "設定またはテストの結果を確認できませんでした。確認してから再試行してください。",
  "索引更新没有完成，仍在使用原来的搜索模型。请检查连接后重试。":
    "インデックスの更新が完了しませんでした。以前の検索モデルが引き続き使用されています。接続を確認して再試行してください。",
  "上次模型测试结果仍待核对，请稍后重试。":
    "前回のモデルテスト結果をまだ確認できていません。しばらくしてから再試行してください。",
  "模型写入暂时关闭。请等待索引维护完成后重试。":
    "モデルへの書き込みは一時停止されています。インデックスのメンテナンスが完了してから再試行してください。",
  "模型未能确认启用，请在设置中核对。":
    "モデルが有効になったことを確認できませんでした。設定で確認してください。",
  "已保存的模型配置暂时无法读取，不会重复创建。请重试刷新。":
    "保存済みのモデル設定を一時的に読み込めません。重複して作成されることはありません。もう一度更新してください。",
  所选连接已启用: "選択したアプリの接続が有効になりました",
  本次未选择应用连接: "今回はアプリの接続が選択されていません",
  尚未填写接收方: "送信先未入力",
  本机内置向量模型: "内蔵のローカル埋め込みモデル",
  "操作未确认，草稿已保留。":
    "操作を確認できませんでした。下書きは保持されています。",
  "操作已完成，但配置列表暂未刷新。已保留操作回执，请刷新核对，不要重复保存。":
    "操作は完了しましたが、設定一覧はまだ更新されていません。記録は保持されています。更新して確認してください。再度保存しないでください。",
  "暂时无法刷新模型配置，已确认的配置仍保留。请重试刷新。":
    "モデルの設定を更新できませんでした。確認済みの設定は保持されています。もう一度更新してください。",
  "未找到上次创建记录。确认放弃旧记录后可重新配置；系统不会自动重试。":
    "前回の作成記録が見つかりません。破棄すると再設定できます。自動的に再試行されることはありません。",
  "创建结果仍待核对，不会重复创建。请稍后再次核对。":
    "作成結果の確認がまだ必要です。重複して作成されることはありません。しばらくしてからもう一度確認してください。",
  "已发现模型配置，旧创建记录仍保留。请先核对已有配置。":
    "モデルの設定が見つかりました。古い作成記録は保持されています。まず既存の設定を確認してください。",
  "旧创建记录尚未安全解除，配置仍保持锁定。请重新核对。":
    "古い作成記録はまだ安全に解除されていません。設定はロックされたままです。もう一度確認してください。",
  "配置未保存，请检查模型地址和参数后重试；已填写内容保留。":
    "設定は保存されませんでした。モデルのエンドポイントと設定を確認して再試行してください。入力内容は保持されています。",
  "创建结果尚未确认，请核对已保存结果，不要重复提交。":
    "作成結果を確認できていません。保存済みの結果を確認してください。再度送信しないでください。",
  "模型配置已变化，旧测试结果不会用于启用，请重新核对配置。":
    "モデルの設定が変更されました。古いテスト結果は有効化に使用されません。設定をもう一度確認してください。",
  "暂时无法核对测试服务，本次未发送测试，请恢复连接后重试。":
    "テストサービスを確認できなかったため、テストは送信されていません。接続を回復してから再試行してください。",
  "另一个模型的测试结果尚未确认，请先在该模型上核对测试结果。":
    "別のモデルのテスト結果を確認できていません。まずそのモデルの結果を確認してください。",
  "测试结果尚未确认。再次点击测试连接将核对原操作，不会重复发送测试。":
    "テスト結果を確認できていません。もう一度「接続をテスト」をクリックすると、元の操作を確認します。テストが重複して送信されることはありません。",
  "请先点击测试连接，通过后再设为当前模型。":
    "まず接続をテストし、成功してから現在のモデルに設定してください。",
  "这是当前正在使用的模型，请先切换到别的模型再删除。":
    "このモデルは現在使用中です。削除する前に別のモデルに切り替えてください。",
  "模型配置操作未成功，请核对输入后重试。":
    "モデル設定の操作に失敗しました。入力内容を確認して再試行してください。",
  "它没有 MCP 通道，无法发放可撤销的记忆凭证":
    "このアプリには、取り消し可能な記憶の認証情報を発行する MCP チャネルがありません",
  当前安装不支持一键插入:
    "このインストールはワンクリック接続に対応していません",
  桌面端的连接组件不可用: "デスクトップの接続コンポーネントを利用できません",
  安装后没有拿到安装回执: "インストールの確認が返されませんでした",
  记忆使用权限没有获批: "記憶へのアクセスが承認されませんでした",
  配置文件被改过: "設定ファイルが編集されています",
  "有无法确认的残留，需要人工核对": "手動で確認が必要な残存項目があります",
  权限存储暂不可用: "権限の保存領域を利用できません",
  "请先把 MemHub 拖到“应用程序”文件夹，再接入应用":
    "アプリに接続する前に、MemHub を「アプリケーション」フォルダに移動してください",
  本机运行时暂不可用: "ローカルのランタイムを利用できません",
  桌面端读取接入状态超时:
    "デスクトップの接続状況の読み込みがタイムアウトしました",
  "apps.apps": "アプリ",
  "apps.allApps": "すべてのアプリ",
  "apps.home": "アプリのホーム",
  "apps.back": "ホームに戻る",
  "apps.overview": "記憶の概要",
  "apps.memories": "保存済みの記憶",
  "apps.rawConversations": "元の会話",
  "apps.rawSaving": "保存中",
  "apps.rawSavingCompact": "保存中",
  "apps.rawSavingStatus": "保存状態",
  "apps.rawSavingDetail": "生成モデルを使わず元の会話を保存します",
  "apps.rawNoExcerpt": "表示できるユーザーの原文がありません",
  "apps.rawConversation": "元の会話",
  "apps.rawSaved": "元の会話を保存しました",
  "apps.rawEmpty": "保存済みの元の会話はまだありません",
  "apps.rawCheckedEmpty": "確認済み：表示できる元の会話の更新はありません",
  "apps.memoryCheckedEmpty": "確認済み：表示できる記憶の更新はありません",
  "apps.recentEmptyCompact": "更新なし",
  "apps.rawWaiting": "保存された元の会話がここに表示されます",
  "apps.checkedRecent": "現在の内容を確認済み",
  "apps.currentMode": "現在のモード",
  "apps.mode.lite": "基本メモリ（lite）",
  "apps.mode.pro": "スマートメモリ（pro）",
  "apps.mode.ultra": "深層メモリ（ultra）",
  "apps.readingCount": "ローカルの記憶を確認中…",
  "apps.rawReadingCount": "元の会話を確認中…",
  "apps.startingRetry":
    "バックグラウンドサービスを起動中です。自動的に更新されます",
  "apps.countWillUpdate": "記憶の件数は自動的に更新されます",
  "apps.rawCountWillUpdate": "元の会話の件数は自動的に更新されます",
  "apps.showingPrevious": "前回のデータを表示しています。自動的に更新されます",
  "apps.unreadable": "利用できません",
  "apps.unit": "件",
  "apps.scope": "アプリの権限の範囲内で使用",
  "apps.processing": "処理状況",
  "apps.running": "処理中",
  "apps.paused": "一時停止中",
  "apps.stoppedNew": "新しい記憶は作成されていません",
  "apps.checkingProcessing": "処理状況を確認中",
  "apps.checkingStatus": "確認中",
  "apps.unchecked": "確認が必要",
  "apps.organizingNew": "新しい会話を処理中",
  "apps.recentUpdate": "最近の更新",
  "apps.checkingRecent": "確認中",
  "apps.recentWillUpdate": "最新の更新は自動的に表示されます",
  "apps.notGenerated": "まだ作成されていません",
  "apps.saved": "記憶を保存しました",
  "apps.separate": "履歴と記憶は別々に処理されます",
  "apps.recentCall": "最近の呼び出し",
  "apps.activityUnavailable": "アクティビティを利用できません",
  "apps.firstBatch": "最初の記憶",
  "apps.callsUnavailable": "呼び出し履歴を読み込めませんでした",
  "apps.memoryUnavailable": "記憶の概要はまだ準備できていません",
  "apps.importing": "会話をインポート中です。まもなく記憶が作成されます",
  "apps.firstRunning": "最初の記憶を作成中",
  "apps.firstQueued": "最初の記憶を準備中",
  "apps.readingActivity": "記憶の概要を準備中",
  "apps.emptyMemories": "作成された記憶はまだありません",
  "apps.callReadFailure":
    "保存済みの記憶は引き続き表示できます。再試行しても新しい呼び出しは行われません。",
  "apps.memoryReadFailure":
    "サービスが回復すると自動的に更新されます。今すぐ再試行することもできます。",
  "apps.savingConversations": "会話はバックグラウンドで保存中です。",
  "apps.waitingMemories":
    "履歴は保存されました。新しい記憶はここに表示されます。",
  "apps.reread": "再試行",
  "apps.viewCalls": "履歴を表示",
  "apps.viewMemory": "記憶を表示",
  "apps.backgroundDown":
    "バックグラウンドサービスが実行されていないため、アプリは現在記憶を照会できません",
  "apps.restart": "再起動",
  "apps.runtime": "実行状況",
  "apps.backgroundRunning": "バックグラウンドで実行中",
  "apps.connectionUnchecked": "接続の確認が必要",
  "apps.disconnected": "サービス未接続",
  "apps.authorizedOnly": "承認された内容のみを処理",
  "apps.sync": "会話の同期",
  "apps.organizing": "記憶の処理",
  "apps.access": "アプリのアクセス",
  "apps.checkingAccess": "アプリの接続チェック",
  "apps.checking": "確認中…",
  "apps.recheck": "再確認",
  "apps.accessState": "アプリの接続状況",
  "apps.staleAccess":
    "アプリの接続状況を読み込めませんでした。最後に確認した状態を表示しています。",
  "apps.reconnect": "再接続",
  "apps.myApps": "マイアプリ",
  "apps.list": "アプリ一覧",
  "apps.syncEnabled": "継続的な同期が有効",
  "apps.recallAllowed": "承認済みの記憶をリクエスト可能",
  "apps.historyAllowed": "履歴へのアクセスを承認済み",
  "apps.connectionPending": "接続未完了",
  "apps.details": "詳細を表示",
  "apps.listUnavailable": "アプリ一覧を利用できません",
  "apps.emptyApps": "表示できるアプリはまだありません",
  "apps.checkingNow": "確認中",
  "apps.checkingState": "状態を確認中",
  "apps.notEnabled": "無効",
  "apps.enableFailed": "有効にできませんでした",
  "apps.unknownTime": "日時不明",
  "apps.today": "今日",
  "apps.newMemory": "新しい記憶",
  "apps.updatedMemory": "更新された記憶",
  "apps.unlinked": "未接続",
  "apps.lastRead": "前回の読み込み：",
  "apps.recentSync": "最終同期 {time}",
  "apps.connectedCount": "{count} 個のアプリを接続済み",
  "apps.connectedCount.one": "{count} 個のアプリを接続済み",
  "apps.pendingCount": "{count} 個のアプリの確認が必要",
  "apps.pendingCount.one": "{count} 個のアプリの確認が必要",
  "apps.mixedCount": "{count} 個のアプリを接続済み · 確認待ち：{pending}",
  "apps.mixedCount.one": "{count} 個のアプリを接続済み · 確認待ち：{pending}",
  "apps.unknownAccess":
    "アプリの接続状況を取得できないため、接続済みのアプリの数を確認できません。",
  "apps.unknownPermissions":
    "{names} の記憶の権限を読み込めませんでした。接続状況は未確認です。",
  "apps.enabledCount": "{count} 個のアプリが有効",
  "apps.enabledCount.one": "{count} 個のアプリが有効",
  "apps.appCount": "{count} 個のアプリ",
  "apps.appCount.one": "{count} 個のアプリ",
  "apps.viewAll": "全 {count} 個のアプリを表示",
  "apps.viewAll.one": "全 {count} 個のアプリを表示",
  "apps.checkedAt": "最終確認：{time}",
  "apps.revision": "バージョン {count}",
  "apps.conversationTime": "会話日時",
  "apps.importTime": "インポート日時",
  "apps.writeTime": "書き込み日時",
  "apps.updateTime": "更新日時",
  "apps.incompleteCalls":
    " · 一部の呼び出しが記録されていないため、一覧が不完全な可能性があります",
  "apps.timeoutWarning":
    "{host} は最近、記憶を照会できませんでした。バックグラウンドサービスが混み合っている可能性があります。自動的に回復します。",
  "apps.autoHostFailed": "{name} は自動的に接続できませんでした{reason}。",
  "apps.failureReason": "：{reason}",
  "apps.autoSyncFailed":
    "{name} の継続的な同期を自動的に有効にできませんでした。{reason}",
  "apps.noAutoRetry": "自動的に再試行されることはありません。",
  "apps.cardUnavailable":
    "{label}は一時的に利用できません。ほかの機能には影響なく、自動的に回復します。",
  "apps.navMemories": "記憶",
  "apps.navigation": "メインナビゲーション",
  "apps.skip": "メインコンテンツへスキップ",
  "apps.local": "ローカルに保存",
  "apps.localHint":
    "記憶はこのデバイスに保存されます。クラウドのモデルや承認済みのアシスタントを使う場合は、関連する内容がそのサービスに送信されます。",
  "memory:记忆抽取": "記憶の抽出",
  "memory:关系归纳": "関係の統合",
  "memory:深度整理": "詳細な処理",
  "memory:跨域整理": "ドメイン横断の処理",
  "memory:当前模型已变化": "現在のモデルが変更されました",
  "memory:本地会话内容已变化": "ローカルの会話内容が変更されました",
  "memory:这段对话含过长原文，暂未整理；原文已保留":
    "この会話には処理できないほど長いテキストが含まれています。原文は保持されています",
  "memory:记忆库分页": "記憶ライブラリのページ",
  "memory:上一页": "前のページ",
  "memory:下一页": "次のページ",
  "memory:等待可用生成模型": "利用可能な生成モデルを待機中",
  "memory:还没有启用生成模型": "生成モデルがまだ有効になっていません",
  "memory:缺少生成模型 API Key": "生成モデルの API キーがありません",
  "memory:模型凭据暂不可用": "モデルの認証情報を一時的に利用できません",
  "memory:本机凭据存储暂不可用":
    "ローカルの認証情報の保存領域を一時的に利用できません",
  "memory:需要授权访问本机钥匙串。请重新打开 App 并完成一次授权":
    "キーチェーンへのアクセスを承認する必要があります。アプリを開き直してアクセスを許可してください。",
  "memory:读取本机钥匙串超时。重新保存一遍模型配置即可恢复":
    "キーチェーンの読み込みがタイムアウトしました。モデルの設定をもう一度保存すると回復します。",
  "memory:写入本机钥匙串超时。重新保存一遍模型配置即可恢复":
    "キーチェーンへの書き込みがタイムアウトしました。モデルの設定をもう一度保存すると回復します。",
  "memory:模型认证失败。更新 key 后，到设置里点一次检测，会自动接着处理":
    "モデルの認証に失敗しました。キーを更新し、設定でチェックを実行すると処理が再開されます。",
  "memory:模型账户余额不足。充值或换 key 后，到设置里点一次检测，会自动接着处理":
    "モデルのアカウント残高が不足しています。チャージするかキーを変更し、設定でチェックを実行すると処理が再開されます。",
  "memory:模型服务拒绝访问。更新权限后，到设置里点一次检测，会自动接着处理":
    "モデルサービスがアクセスを拒否しました。権限を更新し、設定でチェックを実行すると処理が再開されます。",
  "memory:模型服务限流": "モデルサービスのレート制限中",
  "memory:模型服务暂不可用": "モデルサービスを一時的に利用できません",
  "memory:模型服务响应超时": "モデルサービスがタイムアウトしました",
  "memory:模型请求不兼容，请核对配置":
    "モデルへのリクエストに互換性がありません。設定を確認してください",
  "memory:模型请求未成功": "モデルへのリクエストに失敗しました",
  "memory:HY Memory 不可用": "HY Memory を利用できません",
  "memory:HY Memory 启动失败": "HY Memory を起動できませんでした",
  "memory:暂时没能整理，稍后会自动重试":
    "まだ処理できていません。自動的に再試行します",
  "memory:这次没能整理成功，正在重试":
    "今回は処理できませんでした。再試行中です",
  "memory:生成模型换过了，这批会重新整理":
    "生成モデルが変更されたため、これらの項目は再処理されます",
  "memory:整理方式换过了，这批会重新整理":
    "処理の設定が変更されたため、これらの項目は再処理されます",
  "memory:已提炼": "抽出済み",
  "memory:旧版确认记录": "旧形式の確認記録",
  "memory:创建": "作成",
  "memory:纠正": "修正",
  "memory:调整使用范围": "利用範囲を変更",
  "memory:撤销修改": "変更を取り消し",
  "memory:模型更新": "モデルによる更新",
  "memory:被新记忆取代": "新しい記憶に置き換え",
  "memory:其他操作": "その他の操作",
  "memory:删除（记录）": "削除",
  "memory:修改已保存，但读取最新列表失败。已停止使用旧回答，请重新读取。":
    "変更は保存されましたが、最新の一覧を読み込めませんでした。古い回答は使用されなくなりました。一覧を再読み込みしてください。",
  "memory:纠正已保存，但读取最新列表失败。已停止使用旧回答，请重新读取。":
    "修正は保存されましたが、最新の一覧を読み込めませんでした。古い回答は使用されなくなりました。一覧を再読み込みしてください。",
  "memory:未收到操作结果，请重试。":
    "結果を受け取れませんでした。もう一度お試しください。",
  "memory:批量处理结果已保留，但列表刷新失败，请重新读取记忆库。":
    "一括操作の結果は保持されていますが、一覧を更新できませんでした。記憶ライブラリを再読み込みしてください。",
  "memory:批量处理结果已保留，但列表刷新失败，稍后会自动重新读取。":
    "一括操作の結果は保持されていますが、一覧を更新できませんでした。しばらくすると自動的に再読み込みされます。",
  "memory:召回范围已保存，但列表刷新失败。请稍后重新打开记忆页。":
    "呼び出し範囲は保存されましたが、一覧を更新できませんでした。しばらくしてから「記憶」を開き直してください。",
  "memory:原始对话暂时无法读取": "元の会話を読み込めませんでした",
  "memory:没有匹配的原始对话": "一致する元の会話はありません",
  "memory:还没有导入的原始对话": "インポートされた元の会話はまだありません",
  "memory:查看已读内容": "保存済みの内容を表示",
  "memory:已保存原文": "原文を保存済み",
  "memory:刷新已读内容": "保存済みの内容を更新",
  "memory:正在核对本机已保存记录…": "ローカルに保存された記録を確認中…",
  "memory:原始消息": "元のメッセージ",
  "memory:会话": "会話",
  "memory:用户发言": "ユーザーの発言",
  "memory:Agent 发言": "エージェントの発言",
  "memory:作者未知": "発言者不明",
  "memory:原始时间未知": "元の日時不明",
  "memory:工具记录": "ツールの記録",
  "memory:原始时间来自已保存记录。": "元の日時は保存済みの記録に基づきます。",
  "memory:已保存的原文样例": "保存済みの原文の例",
  "memory:最多展示 3 段，不代表全部历史或推荐记忆。":
    "最大 3 件まで表示します。履歴のすべてやおすすめの記憶ではありません。",
  "memory:查看完整对话": "会話全体を表示",
  "memory:原文较长，此处仅展示前 600 个字符。":
    "原文が長いため、ここでは最初の 600 文字のみを表示しています。",
  "memory:这个来源还没有保存原文。":
    "このソースから保存された原文はまだありません。",
  "memory:完整对话": "会話全体",
  "memory:关闭完整对话": "会話全体を閉じる",
  "memory:用户": "ユーザー",
  "memory:暂时无法读取已保存内容，旧回执已收起。请检查授权或重试。":
    "保存済みの内容を読み込めませんでした。以前の記録は非表示にしました。権限を確認するか、再試行してください。",
  "memory:这段原文暂不可读，可能已被撤回授权。请刷新回执。":
    "この原文は利用できません。アクセスが取り消された可能性があります。記録を更新してください。",
  "memory:外部记忆召回范围": "外部からの記憶の呼び出し範囲",
  "memory:外部记忆召回": "外部からの記憶の呼び出し",
  "memory:调整召回范围": "呼び出し範囲を調整",
  "memory:所有关联工具": "接続済みのすべてのツール",
  "memory:已停止外部记忆召回": "外部からの記憶の呼び出しを停止中",
  "memory:原有限定范围": "以前の限定範囲",
  "memory:选择召回范围": "呼び出し範囲を選択",
  "memory:停止外部记忆召回": "外部からの記憶の呼び出しを停止",
  "memory:正在保存…": "保存中…",
  "memory:保存召回范围": "呼び出し範囲を保存",
  "memory:取消范围修改": "範囲の変更をキャンセル",
  "memory:原文检索仍按来源授权；已带出的内容不会撤回。":
    "原文の検索は引き続きソースの権限に従います。すでに提供された内容は取り消されません。",
  "memory:记忆已更新，请重新打开这条记忆后再调整范围。":
    "記憶が変更されました。開き直してから範囲を調整してください。",
  "memory:范围修改未提交，当前范围保持不变。请修正后重试。":
    "範囲の変更は送信されませんでした。現在の範囲は変更されていません。問題を修正してから再試行してください。",
  "memory:保存结果尚未确认，请重试。":
    "保存結果を確認できていません。もう一度お試しください。",
  "memory:原话已保存在本机；当前内容、模型接收方或授权版本尚未通过核对，未继续整理。":
    "原文はこのデバイスに保存されています。内容、モデルの送信先、または権限のバージョンが確認を通過しなかったため、処理は続行されませんでした。",
  "memory:HY 未返回可核对的已有记忆，未创建替代内容；原始记录已保留，可重试处理。":
    "HY から検証可能な既存の記憶が返されなかったため、置き換えは作成されませんでした。元の記録は保持されています。処理を再試行できます。",
  "memory:历史引用尚未绑定到原始来源，当前批次未完成，原话已保留。":
    "過去の引用がまだ元のソースに関連付けられていません。このバッチは未完了です。原文は保持されています。",
  "memory:记忆内容未通过来源与作者核验，当前批次未完成，原话已保留。":
    "記憶の内容がソースと発言者の検証を通過しませんでした。このバッチは未完了です。原文は保持されています。",
  "memory:所选记忆已不可读取，请重新读取记忆库。":
    "選択した記憶は読み込めなくなりました。記憶ライブラリを再読み込みしてください。",
  "memory:还没连上本地 Core，正在重试…":
    "ローカルの Core にまだ接続されていません。再試行中…",
  "memory:记忆内容或使用权限已变化，回答已停止。请重新提问。":
    "記憶の内容またはアクセス権限が変更されたため、回答を停止しました。もう一度質問してください。",
  "memory:这条记忆的来源授权已撤回，暂时不能读取。":
    "この記憶のソースへのアクセスが取り消されたため、現在は読み込めません。",
  "memory:记忆已被更新，请刷新后重试。":
    "この記憶は更新されました。更新してから再試行してください。",
  "memory:已删除的记忆不能修改。": "削除済みの記憶は編集できません。",
  "memory:这条记忆已经删除。": "この記憶は削除されました。",
  "memory:这条记忆已被更新的记忆取代，只能查看或删除。":
    "この記憶は新しい記憶に置き換えられたため、表示または削除のみ可能です。",
  "memory:没有更早的修改可以撤销。": "取り消せる以前の変更はありません。",
  "memory:来源原话已不存在，无法建立这条记忆。":
    "ソースの原文が存在しないため、この記憶を作成できません。",
  "memory:这次操作与已完成的请求不一致。":
    "この操作は完了済みのリクエストと一致しません。",
  "memory:输入内容不符合要求。": "入力内容が要件を満たしていません。",
  "memory:本地记忆库正忙，请稍后重试。":
    "ローカルの記憶ライブラリが処理中です。しばらくしてからもう一度お試しください。",
  "memory:记忆": "記憶",
  "memory:记忆空间": "記憶スペース",
  "memory:全部应用": "すべてのアプリ",
  "memory:来源": "ソース",
  "memory:主题": "トピック",
  "memory:归纳": "統合",
  "memory:全部记忆": "すべての記憶",
  "memory:来源待核对": "ソースの確認が必要",
  "memory:所选来源待核对": "選択したソースの確認が必要",
  "memory:未识别来源": "不明なソース",
  "memory:这个来源下还没有记忆": "このソースの記憶はまだありません",
  "memory:只在最相关的 {count} 条记忆里查找关键词":
    "キーワードは関連性の高い上位 {count} 件の記憶のみで照合されます",
  "memory:未知来源": "不明なソース",
  "memory:记忆读取失败": "記憶を読み込めませんでした",
  "memory:正在读取…": "読み込み中…",
  "memory:正在更新…": "更新中…",
  "memory:读取失败": "読み込めませんでした",
  "memory:正在核对来源…": "ソースを確認中…",
  "memory:正在导入历史会话，记忆将陆续出现":
    "過去の会話をインポート中です。記憶は順次表示されます",
  "memory:记忆工作台": "記憶のワークスペース",
  "memory:来源与主题": "ソースとトピック",
  "memory:记忆或原始对话": "記憶または元の会話",
  "memory:原始对话": "元の会話",
  "memory:地图组织方式": "マップのグループ化",
  "memory:筛选地图记忆": "マップの記憶を絞り込む",
  "memory:搜索原始对话": "元の会話を検索",
  "memory:搜索记忆": "記憶を検索",
  "memory:筛选记忆…": "記憶を絞り込む…",
  "memory:向记忆提问": "記憶に質問",
  "memory:询问全部已授权可用记忆…": "承認済みで利用可能なすべての記憶に質問…",
  "memory:发送问题": "質問を送信",
  "memory:刷新地图记忆": "マップの記憶を更新",
  "memory:关闭新增记忆提示": "新しい記憶の通知を閉じる",
  "memory:地图记忆列表": "マップの記憶一覧",
  "memory:记忆读取失败，请重试。":
    "記憶を読み込めませんでした。もう一度お試しください。",
  "memory:重新读取地图记忆": "マップの記憶を再読み込み",
  "memory:没有匹配的记忆": "一致する記憶はありません",
  "memory:本页没有匹配的主题记忆":
    "このページに一致するトピックの記憶はありません",
  "memory:还没有已生成的记忆": "作成された記憶はまだありません",
  "memory:地图记忆分页": "マップの記憶のページ",
  "memory:上一页记忆": "記憶の前のページ",
  "memory:下一页记忆": "記憶の次のページ",
  "memory:记忆关系图": "記憶の関係グラフ",
  "memory:记忆关联": "記憶のつながり",
  "memory:刚导入的原话，还不是沉淀后的记忆":
    "インポートされたばかりの原文で、まだ記憶にはなっていません",
  "memory:来源归属": "ソースとのつながり",
  "memory:可在沉淀记忆后查看视图": "記憶を作成するとグラフを表示できます",
  "memory:正在读取记忆地图…": "記憶マップを読み込み中…",
  "memory:查看选中来源原文": "選択したソースの原文を表示",
  "memory:查看选中记忆原文": "選択した記憶の原文を表示",
  "memory:查看原文": "元の会話を表示",
  "memory:整理已有关系": "既存のつながりを構築",
  "memory:正在读取原文…": "元の会話を読み込み中…",
  "memory:选择一条原始对话": "元の会話を選択",
  "memory:记忆暂时无法读取": "記憶を一時的に利用できません",
  "memory:当前筛选没有可显示的记忆":
    "この絞り込み条件で表示できる記憶はありません",
  "memory:正在读取记忆…": "記憶を読み込み中…",
  "memory:已保存的对话还未生成记忆": "保存済みの会話はまだ記憶になっていません",
  "memory:还没有可显示的记忆": "表示できる記憶はまだありません",
  "memory:连线表示来源归属": "線はソースとのつながりを表します",
  "memory:记忆详情": "記憶の詳細",
  "memory:来源原文": "ソースの原文",
  "memory:关联节点": "関連ノード",
  "memory:关闭记忆详情": "記憶の詳細を閉じる",
  "memory:关闭详情": "詳細を閉じる",
  "memory:正在读取这条记忆…": "この記憶を読み込み中…",
  "memory:这条记忆的原文暂时无法读取，选择已保留。":
    "この記憶の原文を読み込めませんでした。選択は保持されています。",
  "memory:重试读取原文": "会話の読み込みを再試行",
  "memory:已沉淀的记忆": "作成済みの記憶",
  "memory:正在读取记忆详情…": "記憶の詳細を読み込み中…",
  "memory:沉淀记忆后查看详情": "記憶を作成すると詳細を表示できます",
  "memory:时间未知": "日時不明",
  "memory:来源原文已不可读取": "ソースの原文を利用できません",
  "memory:关联记忆": "関連する記憶",
  "memory:正在读取完整对话…": "会話全体を読み込み中…",
  "memory:重试读取完整对话": "会話全体の読み込みを再試行",
  "memory:看完整对话": "会話全体を表示",
  "memory:今天": "今日",
  "memory:你": "あなた",
  "memory:助手": "アシスタント",
  "memory:工具": "ツール",
  "memory:未知": "不明",
  "memory:作者待核对": "発言者の確認が必要",
  "memory:你说": "あなたの発言",
  "memory:助手原话": "アシスタントの元の発言",
  "memory:已沉淀": "作成済み",
  "memory:已删除": "削除済み",
  "memory:已被取代": "置き換え済み",
  "memory:状态待核对": "状態の確認が必要",
  "memory:编辑": "編集",
  "memory:原始依据": "元の根拠",
  "memory:这段原文当前不可查看。": "この原文は現在利用できません。",
  "memory:版本记录": "バージョン履歴",
  "memory:更多管理": "その他の操作",
  "memory:撤销上次修改": "直前の変更を取り消す",
  "memory:删除记忆": "記憶を削除",
  "memory:编辑记忆": "記憶を編集",
  "memory:返回记忆": "記憶に戻る",
  "memory:修改这条记忆，原始对话保持不变。":
    "この記憶を編集します。元の会話は変更されません。",
  "memory:记忆内容": "記憶の内容",
  "memory:使用范围保持不变": "利用範囲は変更されません",
  "memory:已有新版本": "新しいバージョンがあります",
  "memory:你的草稿已保留，先查看新版本再决定如何修改。":
    "下書きは保持されています。変更する前に新しいバージョンを確認してください。",
  "memory:查看新版本": "新しいバージョンを表示",
  "memory:基于此版本继续编辑": "このバージョンから編集を続ける",
  "memory:取消": "キャンセル",
  "memory:保存": "保存",
  "memory:重试": "再試行",
  "memory:删除": "削除",
  "memory:对话": "会話",
  "memory:工作": "仕事",
  "memory:编码": "コーディング",
  "memory:保存修改": "変更を保存",
  "memory:对应记忆": "関連する記憶",
  "memory:查看记忆": "記憶を表示",
  "memory:删除这条记忆？": "この記憶を削除しますか？",
  "memory:删除后不再用于新的召回，来源 App 中的原始对话不会删除。":
    "今後の照会には使われなくなります。ソースアプリ内の元の会話は削除されません。",
  "memory:已经发送给应用的内容无法远程收回。":
    "すでにアプリへ送信された内容をリモートで取り消すことはできません。",
  "memory:放弃尚未保存的修改？": "保存されていない変更を破棄しますか？",
  "memory:来源权限已变化或原文不可用。":
    "ソースの権限が変更されたか、原文を利用できません。",
  "memory:暂时无法读取新版本，草稿已保留。":
    "新しいバージョンを読み込めませんでした。下書きは保持されています。",
  "memory:修改未提交；草稿已保留。请按提示修正后重试。":
    "変更は送信されませんでした。下書きは保持されています。問題を修正してから再試行してください。",
  "memory:修改尚未确认，草稿已保留。请先刷新核对，避免重复提交。":
    "変更が確認できていません。下書きは保持されています。重複して送信しないよう、更新して確認してから再試行してください。",
  "memory:删除未提交。请核对这条记忆的状态后重试。":
    "削除は送信されませんでした。この記憶の状態を確認してから再試行してください。",
  "memory:删除尚未确认。请重新核对记忆状态，不会自动扩大删除范围。":
    "削除が確認できていません。記憶の状態を確認してください。削除範囲が自動的に広がることはありません。",
  "memory:撤销未提交。请按提示修正后重试。":
    "取り消しは送信されませんでした。問題を修正してから再試行してください。",
  "memory:撤销尚未确认，请先核对当前记忆版本，避免重复撤销。":
    "取り消しが確認できていません。二重に取り消さないよう、現在のバージョンを確認してから再試行してください。",
  "memory:添加记忆": "記憶を追加",
  "memory:关闭新增记忆": "記憶の追加を閉じる",
  "memory:关闭": "閉じる",
  "memory:记下这件事": "書き留める",
  "memory:例如：项目周会不排在周五。":
    "例：プロジェクトの定例会議は金曜日に入れない。",
  "memory:整理模型：": "処理モデル：",
  "memory:尚未配置，原话仍可保存": "未設定ですが、原文は保存できます",
  "memory:正在读取": "読み込み中",
  "memory:配置模型": "モデルを設定",
  "memory:保存中…": "保存中…",
  "memory:保存原话": "原文を保存",
  "memory:最近添加": "最近追加した記憶",
  "memory:原话已保存，等待可用模型": "原文を保存済み・利用可能なモデルを待機中",
  "memory:原话已保存，等待整理": "原文を保存済み・処理待ち",
  "memory:正在等待整理": "処理待ち",
  "memory:正在整理": "処理中",
  "memory:原话已保存，整理未完成": "原文を保存済み・処理未完了",
  "memory:整理已完成": "処理完了",
  "memory:原话已保存，未提取出可用记忆":
    "原文を保存済み・利用可能な記憶は抽出されませんでした",
  "memory:最近添加的内容读取失败。": "最近追加した記憶を読み込めませんでした。",
  "memory:整理状态暂时不可用，原话已保存。":
    "処理状況を一時的に利用できません。原文は保存されています。",
  "memory:已保存，正在后台沉淀记忆": "保存済み・バックグラウンドで記憶を作成中",
  "memory:未能确认保存结果，输入已保留。重试不会重复添加。":
    "保存結果を確認できませんでした。入力内容は保持されています。再試行しても重複して追加されることはありません。",
  "memory:模型服务要求检查余额或付款状态，原话仍已保存。":
    "モデルサービスから残高または支払い状況の確認を求められています。原文は保存されたままです。",
  "memory:这台设备打不开 3D 视图，已显示关联列表。":
    "このデバイスでは 3D 表示を利用できないため、つながりを一覧で表示しています。",
  "memory:图谱工具": "グラフの操作",
  "memory:放大": "拡大",
  "memory:放大图谱": "グラフを拡大",
  "memory:缩小": "縮小",
  "memory:缩小图谱": "グラフを縮小",
  "memory:重置视角": "表示をリセット",
  "memory:重置图谱视角": "グラフの表示をリセット",
  "memory:查看读取详情": "読み込みの詳細を表示",
  "memory:原文暂时无法读取": "原文を読み込めませんでした",
  "memory:暂时无法读取这段对话，已保留当前记忆。":
    "この会話を読み込めませんでした。現在の記憶は選択されたままです。",
  "memory:已清除筛选，定位到关联记忆。":
    "関連する記憶を表示するため、絞り込みを解除しました。",
  "memory:所选记忆不在当前筛选内，已取消选择。":
    "選択した記憶はこの絞り込み条件に含まれないため、選択を解除しました。",
  "memory:列表分隔符": "、",
  "memory:冒号": "：",
  "memory:手动添加": "手動で追加",
  "memory:导入的已有记忆": "インポートした既存の記憶",
  "memory:记忆与原文": "記憶と原文",
  "memory:第 {revision} 版": "バージョン {revision}",
  "memory:对话时间 ": "会話日時 ",
  "memory:沉淀时间 ": "作成日時 ",
  "memory:原对话 ": "元の会話 ",
  "memory:{count} 条记录没有原始时间，未用导入时间代替。":
    "{count} 件の記録には元の日時がありません。インポート日時での代用はしていません。",
  "memory:作者数量按当前记录分类统计，不代表记忆抽取已完成或语义已核实。":
    "発言者の数は保存済みの記録に基づくもので、記憶の抽出の完了や内容の検証を意味するものではありません。",
  "memory:统计于 ": "集計日時 ",
  "memory: · 导入数量仍在更新，可刷新核对":
    " · インポート件数は更新中です。更新して確認できます",
  "memory:对话于 {day}": "{day} の会話",
  "memory:导入于 {day}": "{day} にインポート",
  "memory:写入于 {day}": "{day} に書き込み",
  "memory:已开始处理所选对话": "選択した会話を処理中",
  "memory:处理请求已受理": "処理のリクエストを受け付けました",
  "memory:模型连接已通过。首批记忆生成后，会逐步出现在记忆列表里。":
    "モデルの接続を確認しました。記憶は作成され次第、一覧に表示されます。",
  "memory:已导入 {count} 段对话，整理成记忆后会出现在这里。":
    "{count} 件の会話をインポートしました。処理が終わると記憶がここに表示されます。",
  "memory:历史已保存在本机，记忆就绪后会出现在这里。":
    "履歴はこのデバイスに保存されています。記憶の準備ができるとここに表示されます。",
  "memory:连接应用并导入历史后，可以开始沉淀记忆。":
    "アプリを接続して履歴をインポートすると、記憶の作成を始められます。",
  "memory:当前已生成 0 条记忆。请求已受理不等于已经完成，原始对话、模型处理任务和记忆数量分别统计。":
    "作成された記憶はまだありません。リクエストの受け付けは完了を意味しません。元の会話、処理ジョブ、記憶はそれぞれ別に集計されます。",
  "memory:返回应用": "アプリに戻る",
  "memory:共 {count} 条 · 正在核对来源…": "合計 {count} 件 · ソースを確認中…",
  "memory:共 {count} 条 · 正在核对来源….one":
    "合計 {count} 件 · ソースを確認中…",
  "memory:共 {count} 条": "合計 {count} 件",
  "memory:共 {count} 条.one": "合計 {count} 件",
  "memory: · 正在核对…": " · 確認中…",
  "memory:新增 {count} 条记忆": "新しい記憶 {count} 件",
  "memory:新增 {count} 条记忆.one": "新しい記憶 {count} 件",
  "memory:沉淀于 ": "作成日時 ",
  "memory:共 {count} 条，第 {page} / {pages} 页":
    "合計 {count} 件 · {page} / {pages} ページ",
  "memory:共 {count} 条，第 {page} / {pages} 页.one":
    "合計 {count} 件 · {page} / {pages} ページ",
  "memory:已沉淀 {count} 条记忆": "記憶 {count} 件を作成済み",
  "memory:已沉淀 {count} 条记忆.one": "記憶 {count} 件を作成済み",
  "memory:关键词“{query}”": "キーワード「{query}」",
  "memory:已沉淀 {count} 条{filter} · 当前展示 {shown} 条":
    "作成済みの記憶 {count} 件{filter} · {shown} 件を表示中",
  "memory:已沉淀 {count} 条{filter} · 当前展示 {shown} 条.one":
    "作成済みの記憶 {count} 件{filter} · {shown} 件を表示中",
  "memory:（{filter}）": "（{filter}）",
  "memory:第 {page} 页": "{page} ページ",
  "memory:正在等待整理：": "処理待ち：",
  "memory:记忆未在生成": "記憶は作成されていません",
  "memory:还没有开始生成记忆：{reason}（{count} 条待处理）":
    "記憶は作成されていません：{reason}（未処理 {count} 件）",
  "memory:去配置生成模型": "生成モデルを設定",
  "memory:模型暂停处理": "モデル一時停止中",
  "memory:模型配置有问题（{reason}），记忆暂停处理，已有 {count} 条在排队":
    "モデル設定に問題があります（{reason}）。記憶の処理を一時停止中です。{count} 件が待機中です",
  "memory:模型配置有问题（{reason}），记忆暂停处理，已有 {count} 条在排队.one":
    "モデル設定に問題があります（{reason}）。記憶の処理を一時停止中です。{count} 件が待機中です",
  "memory:API Key 无效或没有权限": "API キーが無効か権限がありません",
  "memory:找不到模型或地址": "モデルまたはアドレスが見つかりません",
  "memory:请求过于频繁，被服务商限流":
    "リクエストが多すぎるため、プロバイダーに制限されています",
  "memory:模型响应超时": "モデルの応答がタイムアウトしました",
  "memory:模型服务出错": "モデルサービスでエラーが発生しました",
  "memory:无法连接模型服务": "モデルサービスに接続できません",
  "memory:连接模型服务失败": "モデルサービスへの接続に失敗しました",
  "calls.directHost": "直接呼び出し（診断）",
  "calls.headline.timedOut": "{host} は時間内に記憶を照会できませんでした",
  "calls.headline.unavailable": "{host} は現在記憶を照会できません",
  "calls.headline.denied": "{host} には記憶の照会が許可されていません",
  "calls.headline.empty": "{host} では一致する記憶が見つかりませんでした",
  "calls.headline.found": "{host} が関連する項目を {count} 件見つけました",
  "calls.headline.found.one": "{host} が関連する項目を {count} 件見つけました",
  "calls.headline.rawOnly": "{host} が元の会話を {count} 件見つけました",
  "calls.headline.rawOnly.one": "{host} が元の会話を {count} 件見つけました",
  "calls.headline.memoryOnly": "{host} が記憶を {count} 件見つけました",
  "calls.headline.memoryOnly.one": "{host} が記憶を {count} 件見つけました",
  "calls.headline.mixed":
    "{host} が記憶 {memories} 件 · 元の会話 {raw} 件を見つけました",
  "calls.headline.mixed.one":
    "{host} が記憶 {memories} 件 · 元の会話 {raw} 件を見つけました",
  "calls.headline.completed": "{host} の呼び出しが完了しました",
  "calls.detail.timedOut":
    "記憶を処理中のため照会がタイムアウトし、今回の回答には記憶が含まれていません。",
  "calls.detail.unavailable":
    "MemHub が一時的に利用できないため、今回の回答には記憶が含まれていません。",
  "calls.detail.denied": "現在の権限ではこの照会は許可されていません。",
  "calls.detail.partialDelivered":
    "{host} に提供済み · 一部の内容はまだ処理中です",
  "calls.detail.partial": "一部の内容はまだ処理中です",
  "calls.detail.delivered": "{host} に提供済み",
  "calls.detail.notDelivered": "アプリには提供されていません",
  "calls.detail.empty": "今回は一致する記憶がありませんでした",
  "calls.detail.success": "照会に成功",
  "calls.title": "使用履歴",
  "calls.query": "今回の記憶の照会",
  "calls.backApps": "アプリに戻る",
  "calls.backHistory": "履歴に戻る",
  "calls.filter": "アプリで絞り込む",
  "calls.allApps": "すべてのアプリ",
  "calls.recent": "最近の履歴",
  "calls.refresh": "履歴を更新",
  "calls.clearPage": "このページを消去",
  "calls.clearAll": "すべての履歴を消去",
  "calls.clearAllShort": "すべて消去",
  "calls.loading": "使用履歴を読み込み中",
  "calls.clearing": "選択した履歴を消去中",
  "calls.readFailed":
    "履歴を読み込めませんでした。更新してもう一度お試しください。",
  "calls.unavailable":
    "使用履歴を利用できません。しばらくしてから更新してください。",
  "calls.incomplete":
    "一部の呼び出しが記録されていないため、一覧が不完全な可能性があります。",
  "calls.callDetail": "呼び出しの詳細",
  "calls.source": " · ソース：{source}",
  "calls.stats": "原文 {raw} 件 · 記憶 {memories} 件を検出 · {seconds} 秒",
  "calls.metadata": "記録の詳細",
  "calls.privacy":
    "プライバシー保護のため、元の質問と回答はここに保存されません。保存されるのは日時、ソース、結果のみです。",
  "calls.exactTime": "正確な日時",
  "calls.app": "アプリ",
  "calls.type": "種類",
  "calls.result": "結果",
  "calls.tool": "記憶を照会",
  "calls.notRecorded": "記録なし",
  "calls.missingRow":
    "この記録は現在のページにありません。履歴に戻って探してください。",
  "calls.list": "呼び出し履歴",
  "calls.emptyPage": "このページに記録はありません",
  "calls.empty": "使用履歴はまだありません",
  "calls.pagination": "履歴のページ",
  "calls.previous": "前のページ",
  "calls.next": "次のページ",
  "calls.policy": "{days} 日間、最大 {count} 件まで保持",
  "calls.confirmAllTitle": "すべての使用履歴を消去しますか？",
  "calls.confirmPageTitle": "このページの {count} 件の記録を消去しますか？",
  "calls.confirmPageTitle.one": "このページの {count} 件の記録を消去しますか？",
  "calls.confirmAllDescription":
    "このページや絞り込み条件に限らず、保持されているすべての呼び出し記録を削除します。元の会話、記憶、権限、保持ルールは変更されません。この操作は元に戻せません。",
  "calls.confirmPageDescription":
    "これらの呼び出し記録のみを削除します。元の会話、記憶、権限、保持ルールは変更されません。この操作は元に戻せません。",
  "calls.confirmAll": "すべて消去する",
  "calls.confirmPage": "消去する",
  "calls.cancel": "キャンセル",
  "calls.clearedAll": "すべての履歴を消去しました。",
  "calls.clearedPage": "選択した履歴を消去しました。",
  "calls.clearAllFailed":
    "使用履歴を消去できませんでした。既存の記録は変更されていません。確認して再試行してください。",
  "calls.clearPageFailed":
    "選択した履歴を消去できませんでした。既存の記録は変更されていません。確認して再試行してください。",
  "calls.clearUnknown":
    "結果を確認できませんでした。再読み込みした履歴を確認してください。消去は自動的に再試行されていません。",
  "calls.references": "関連する内容",
  "calls.referencesLoading": "関連する内容を読み込み中",
  "calls.referencesFailed":
    "関連する内容を読み込めませんでした。更新してもう一度お試しください。",
  "calls.raw": "元の会話",
  "calls.memory": "記憶",
  "calls.changed": "バージョン変更あり",
  "calls.pending": "確認が必要",
  "calls.notAvailable": "利用不可",
  "calls.openRaw": "元の会話を表示",
  "calls.openMemory": "記憶を表示",
  "calls.noReferences": "関連する内容は見つかりませんでした",
  "calls.rawBody": "元の会話の本文",
  "calls.rawLoading": "元の会話を読み込み中",
  "calls.rawFailed": "元の会話を利用できないか、バージョンが変更されています。",
  "calls.state.grounded": "照会に成功",
  "calls.state.empty": "照会に成功・一致する記憶なし",
  "calls.state.partial": "照会に成功・一部の内容は処理中",
  "calls.state.timed_out": "照会がタイムアウト・記憶の提供なし",
  "calls.state.unavailable": "MemHub は一時的に利用できません",
  "calls.state.denied": "この照会は許可されていません",
  "calls.return.timed_out":
    "MemHub は記憶をインポート・処理中です。照会が時間内に完了しなかったため、{host} は記憶なしで回答しました。処理が完了すると通常どおりに戻ります。",
  "calls.return.unavailable":
    "MemHub が一時的に利用できないため、今回の回答には記憶が含まれていません。",
  "calls.return.denied":
    "現在の権限ではこの照会は許可されていないため、アプリに記憶は提供されていません。",
  "calls.return.partial": "一部の内容はまだ処理中です。",
  "clock.unknown": "日時不明",
  "clock.today": "今日 {clock}",
  settings: "設定",
  languageTitle: "表示言語",
  languageLabel: "言語",
  languageSystem: "システムの言語を使用",
  languageChinese: "簡体字中国語",
  languageEnglish: "英語",
  languageSpanish: "スペイン語",
  languageDescription:
    "表示言語を選択します。この設定によって会話や記憶の内容が変わることはありません。",
  languageSaveError:
    "言語の設定を保存できませんでした。もう一度お試しください。",
  languageLoadError:
    "言語の設定を読み込めませんでした。もう一度お試しください。",
  nativeErrorTitle: "MemHub を起動できませんでした",
  startupPreparing: "起動中です。記憶サービスを準備しています…",
  startupUpgradeHint:
    "初回起動時や記憶データのアップグレード時は、数分かかることがあります。完了すると MemHub が自動的に開きます。",
  "migration.dialogMessage":
    "MemHub のアップグレード中にデータ移行が完了しませんでした。",
  "migration.dialogDetail":
    "データを保護するため、MemHub は起動しません。移行ログを GitHub の Issue に添付して送ってください。（エラーコード：{code}）",
  "migration.dataRestored": "データはアップグレード前の状態に復元されました。",
  "migration.logPath": "ログ：{path}",
  "migration.openLog": "ログを開く",
  "migration.quit": "終了",
  "migration.issueHeading": "## アップグレード時の移行に失敗",
  "migration.issueUpgrade":
    "- MemHub を {fromVersion}（build {fromBuild}）から {toVersion}（build {toBuild}）にアップグレードする際、データ移行に失敗しました。",
  "migration.issueCode": "- エラーコード：{code}",
  "migration.issueStep": "- 失敗したステップ：{step}",
  "migration.issueRestored":
    "- アップグレード前の状態にデータを復元：{restored}",
  "migration.issueSystem": "- システム：{system}",
  "migration.issueArch": "（{arch}）",
  "migration.issueLog": "- 移行ログ（ローカル）：{path}",
  "migration.issueAttach":
    "上記のログファイルをここにドラッグして添付してから送信してください。",
  "migration.unknown": "不明",
  "migration.yes": "はい",
  "migration.no": "いいえ",
};
