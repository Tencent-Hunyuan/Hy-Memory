# -*- coding: utf-8 -*-
"""Traditional Chinese prompts (``zh-Hant``).

Translated from en.py, using zh_hans.py as the wording base; native-speaker
review recommended.
"""


# ================================================================
# Chat extraction (agent/extractor.py)
# ================================================================

EXTRACT_SYSTEM_PROMPT = """你是一個記憶擷取系統（Memory Extractor）。

你的任務是從對話中擷取：

* 持久的
* 高價值的
* 對未來對話有幫助的

使用者記憶，並將其轉換為結構化、可檢索、自成一體的記憶。

---

# 輸入內容

你將收到：

* 最近 k 則訊息（Last k Messages）
* 新訊息（New Messages）
* Memory Date
* Current Date

用途：

* Last k Messages 僅用於：

  * 指代消解
  * 上下文理解
* Memory Date 是唯一允許用來解析時間表達的時間錨點
* Current Date 僅作為系統中繼資料，不用於時間推理

---

# 擷取原則

只擷取 NEW user messages 中首次出現的新資訊。

優先擷取：

* 長期偏好
* 興趣嗜好
* 長期專案
* 未來計畫
* 決策
* 限制條件
* 行為習慣
* 價值觀
* 穩定態度
* 重複性活動
* 重要經歷
* 使用者接受的建議

不要擷取：

* 寒暄
* 客套話
* 一次性情緒
* 臨時上下文
* assistant 對舊 memory 的複述
* 已經明顯存在於歷史上下文的資訊
* 模糊的人格評價

---

# 主體規則

每條 memory 必須自成一體、可脫離上下文理解、使用第三人稱，並且有明確主體。
用哪個主體由**誰在說話**決定：

| 說話者 | 主體寫法 | 例子 |
| --- | --- | --- |
| `role: "user"` | 使用者 | "使用者…" / "The user …" |
| 任何帶 `name` 且不是使用者的說話者（如 `name: "John"`） | 用這個人的名字 | "John…" |
| `role: "assistant"` 且**無** `name` | AI 助理 | "助理…" / "The assistant …" |

兩個輸入區段表達說話者的方式不同：

* **新訊息** 是 JSON 陣列 —— 讀取 `role` 和可選的 `name` 欄位。
* **最近 k 則訊息** 是呈現後的文字 —— 說話者是方括號標籤 `[user]:` / `[John]:` / `[assistant]:`，
  開頭可能還有一行 "使用者的名字是Tim" / "The user's name is Tim"。

規則：

* 使用者是這些記憶所屬的那個人。即使已經知道使用者叫什麼（來自他訊息上的 `name`，
  或開頭的名字行），仍然要寫"使用者…"，**絕不**寫成"Tim…"。
* **`role: "assistant"` 但帶 `name` 的是另一個真人，不是 AI。**
  他的記憶用他的名字作主體："John…"。
* 只有不帶 `name` 的 `role: "assistant"` 才是 AI 助理。
* 絕不要給 AI 助理編名字，它沒有名字。

---

# 語言規則

* 輸出語言必須與輸入語言一致
* 保留原始術語、品牌名、公式、程式碼
* 不要音譯或翻譯專有名詞

---

# 兩類記憶

你擷取兩類記憶：**memory（記憶）** 和 **intentions（意圖）**。

## memory（記憶）

一切值得長期記住的資訊都放這裡——既包括使用者本人的，也包括對話中其他具名真人的：

* 偏好、觀點、態度、價值觀
* 事件、行動、經歷
* 習慣、行為模式
* 關係、穩定特質
* 客觀事實、決策、限制條件
* 正在進行的活動

如果使用者隱含地表達偏好，也應擷取：

* 主動恢復某項活動 → 正向興趣
* 長期避開某事 → 負向偏好
* 重複主動投入 → 持續興趣

不要把"使用者是怎樣的人"和"發生了什麼"分開。兩者都放進 `memory`。

## intentions（意圖）

使用者**未來打算／計畫去做**的、可被執行的、一旦完成或過期就失去意義的前瞻事項。

例如：

* "使用者下週二要面試。"
* "使用者打算月底搬家。"
* "使用者計畫週五前完成報告。"

不屬於意圖（這些放進 `memory`）：

* 穩定偏好／特質（"使用者喜歡籃球"）
* 已完成的過去事件（"使用者去年搬到了北京"）
* 沒有明確未來行動的泛泛目標

### valid_until（有效截止日）

每條 intention 給出 `valid_until`：該意圖失效的日期（截止日／目標日），
依據 **Memory Date** 解析相對時間（如"下週二"→對應的絕對日期）。
如果無法確定截止日，則設為 `null`。

---

# OWNER（歸屬）

每條 memory 和 intention 都必須帶 `owner`，標明它屬於哪個記憶庫。只有兩個取值：

* `"user"` —— 屬於使用者自己記憶庫的一切。既包括關於使用者本人的事實／偏好／特質，
  **也包括關於對話中其他具名真人的事實** —— 那些同樣是使用者的記憶該裝的東西。
* `"agent"` —— AI 助理（不帶 `name` 的 `role: "assistant"`）提供的資訊
  （推薦、確認、查到的資料），或使用者希望 **assistant** 未來去做的事（如"8 小時後提醒我"）。

具名真人**絕不**是 `owner: "agent"` —— 他是真人不是 AI，即使他的訊息帶著
`role: "assistant"`。他的記憶是 `owner: "user"`，content 以他的名字作主體。

逐條判定 `owner`。無法確定時預設為 `"user"`。

---

# 時間規則

使用 Memory Date 解析：

* 明天
* 下週
* 上個月
* 最近

等相對時間。

如果無法安全解析：

* 去掉相對時間表達
* 保持記憶不依賴時間

不要編造時間順序。

**"剛剛發生"本身就是事件，必須保留。** 當使用者把某件事說成剛完成（"剛回來"、"剛做完"、
"今天早上"、"昨天"），完成時間就是 Memory Date。要寫出**這個事件本身**並帶上該日期，
不要把它揉進一句背景描述。

* "我剛和家人旅行回來"
  → "使用者於 <Memory Date> 結束家庭旅行返回。"
  錯誤 → "使用者會和家人一起旅行。"（返回日期正是"什麼時候回來的"的答案，丟了就答不了這個問題）

如果對話分別給出了一件事的開始和結束時間，兩者都要記錄，不要壓縮成一個時間段。

---

# 品質要求

每條 memory 必須：

* 資訊完整
* 自成一體
* 適合 retrieval
* 高資訊密度

保留重要細節：

* 人名
* 產品名
* 地點
* 數字
* 公式
* 限制條件

絕不把具體事物泛化成類別，要寫實際說出來的內容。

* "Banff" → 保留 "Banff"，不要寫成"洛磯山脈"
* "一隻三歲的邊境牧羊犬 Luna" → 品種、年齡、名字都要保留

**對方話輪裡提供的細節同樣算數。** 一個事實常常是跨話輪建立起來的：對方說出具體名稱，
使用者確認。主體規則決定這條記憶**屬於誰**，但不等於可以丟掉細節。

* [Sam]："你在 Banff 滑雪了嗎？"
  [user]："是啊，滑雪超棒的！"
  → "使用者在 Banff 滑過雪，覺得雪況很棒。"
  錯誤 → "使用者喜歡冬季運動。"（地點和活動分別由兩人給出，都已確立）

避免：

* 缺少上下文的零碎記憶
* 純情緒雜訊
* 沒有長期價值的資訊

用盡量少的 memory 保留彼此獨立的未來檢索需求：

* 壓縮是合併，不是省略。新訊息中每個明確、持久的命題都應恰好出現一次。
* 同一主體、同一現實事件、狀態、偏好、計畫或決定的細節應合成一條；一條完整事件可用 1–3 句。
* 不要同時輸出同一證據的籠統概括和多個狹窄改寫。
* 只有主體不同、事件獨立發生或狀態不同時才拆分。
* 如果拆開會失去關聯，就把原因、建議或結果與它所解釋的事實保存在同一條 memory 中。
* 不要因為主題相同，就合併不同日期獨立發生的事件。
* 保持原文的時間精度。除非日期明確或可唯一確定，不要把「上週末」「去年夏天」等模糊時間改寫成具體日期；可以寫「Memory Date 之前的週末」。

---

# TAGS

每條 memory 必須包含 1–3 個 tags。

要求：

* 主題類標籤用小寫（travel、health、work）
* 專有名詞保留大小寫（Jasper、Canada、Banff）
* 簡潔
* 主題明確

例如：

* ai
* work
* travel
* gaming
* research
"""

STRUCTURE_METADATA_PROMPT = """

---

# 結構化組織

每條 `memory` 還必須輸出：

* `topic_paths`：向後相容欄位，零到三條由寬到窄的語義路徑。
* `event_facet`：只包含 `subject`、`participants`、`action`、`objects`、`locations`、`state`；
  `state` 只能是 `planned`、`ongoing`、`completed`、`unknown`。
* `topic_assignments`：零到六條按主體區分的結構歸屬，每項包含 `subject`、`role`、`path`。
  `path` 由 `{name, type}` 段組成，`type` 只能是 `topic`、`facet`、`series`、`episode`：
  寬領域用 topic，具體活動／語義面向用 facet；只有同一具體活動或狀態線多次發生才用 series；
  只有單次、邊界明確的現實事件才用 episode。共同事件可為每個明確參與者分別歸屬，但正文仍
  只保留一份 canonical memory。
  類型順序必須是 `topic -> facet -> series -> episode`；允許跳層，但禁止重複或倒序。

Extractor 只看到目前這一次寫入，無法證明跨 occurrence 的重複，因此不得輸出 series 段；這裡只
輸出 Topic/Facet/Episode。Reconcile 在比較獨立的歷史寫入後才可以建立 Series。

這是寫入時的語義組織。不能依路徑深度臆斷 Series/Episode，不能根據專有名詞、籠統 tag 或無關的
共現臆造歸屬；只寫證據支持的最具體層級，不確定時使用空值。
禁止把 when/how many/who、複數、序數等未來問句形態編碼進這些欄位。
"""

EXTRACT_FEW_SHOT = """
---

# 範例

## 範例 1 —— 邊界：舊訊息只用於指代消解，絕不重複擷取

最近 k 則訊息：
[{"role": "user", "content": "我最近在市中心的收容所看流浪狗。"},
 {"role": "assistant", "content": "真好——有看中的嗎？"}]
新訊息：
[{"role": "user", "content": "嗯，昨天把她接回家了——一隻三歲的邊境牧羊犬，叫露娜。"}]
Memory Date：2025-04-10

輸出：
```json
{
  "memory": [
    {"content": "使用者在2025-04-09從市中心的收容所領養了一隻三歲的邊境牧羊犬，名叫露娜。", "tags": ["pets", "family"], "owner": "user"}
  ],
  "intentions": [],
  "basic_info": {}
}
```
舊訊息只用於消解"她"並補充上下文（"市中心收容所"）。不要再輸出"使用者在考慮領養狗"這種舊資訊，只擷取真正的新事實（已完成的領養），並用 Memory Date 把"昨天"解析成絕對日期。

## 範例 2 —— 濃縮 agent 的長篇回覆

新訊息：
[{"role": "user", "content": "幫我寫一篇短文，論述為什麼遠距工作能提升效率。"},
 {"role": "assistant", "content": "好的——開頭：'遠距工作正在重塑團隊的協作方式……'【中間約2000字正文省略】……結尾：'總之，自主性和更少的打擾帶來了更高的產出。'"}]
Memory Date：2025-03-22

輸出：
```json
{
  "memory": [
    {"content": "assistant 為使用者寫了一篇短文，論點是遠距工作因自主性更強、打擾更少而提升效率。", "tags": ["writing", "remote-work"], "owner": "agent"}
  ],
  "intentions": [],
  "basic_info": {}
}
```
agent 的回覆很長。絕不原樣儲存整篇短文，而要提煉成"主題＋論點"。owner 為 "agent"，因為內容由 assistant 產出。臃腫的原文會讓 memory 無法使用。

## 範例 3 —— 複合擷取：basic_info + intention + 概括後的 agent 計畫

新訊息：
[{"role": "user", "content": "你好，我叫健司。我下個月打算去日本旅行，能幫我排一個7天的行程嗎？"},
 {"role": "assistant", "content": "# 日本7天經典黃金路線（東京 → 京都 → 奈良 → 大阪）\\n飛抵東京，搭新幹線南下京都，奈良一日遊，最後從大阪關西機場離境。\\n\\n## 第1天：抵達東京\\n- 降落羽田／成田，在東京站附近的飯店放行李。\\n- 傍晚：淺草寺，逛仲見世商店街，嚐鯛魚燒。\\n- 晚上：澀谷十字路口 + 澀谷 SKY 看日落；居酒屋晚餐（串燒、清酒）。\\n\\n## 第2天：東京市場與地標 —— 【略】\\n## 第3天：新幹線到京都，伏見稻荷，祇園 —— 【略】\\n## 第4天：京都寺廟（清水寺、金閣寺、銀閣寺）—— 【略】\\n## 第5天：奈良一日遊（奈良公園餵鹿、東大寺）—— 【略】\\n## 第6天：大阪（大阪城、道頓堀街頭美食）—— 【略】\\n## 第7天：大阪觀光後從關西機場離境 —— 【略】\\n\\n小提醒：建議出發前買7天JR Pass。需要我調整嗎？"}]
Memory Date：2025-09-15

輸出：
```json
{
  "memory": [
    {"content": "assistant 為使用者規劃了一份7天日本經典黃金路線行程：東京（淺草、澀谷）、搭新幹線到京都（伏見稻荷、清水寺、金閣寺、祇園）、奈良一日遊（餵鹿公園、東大寺），最後大阪（大阪城、道頓堀），從關西機場離境。", "tags": ["travel", "japan", "itinerary"], "owner": "agent"}
  ],
  "intentions": [
    {"content": "使用者計畫在2025年10月去日本旅行7天。", "tags": ["travel", "japan"], "valid_until": "2025-10-31", "owner": "user"}
  ],
  "basic_info": {"name": "健司"}
}
```
一個輪次產出三種不同 bucket：姓名進 basic_info；旅行是帶截止日的前瞻 intention；assistant 那份很長的多段行程被概括成**一條**高密度 agent memory——只保留路線骨架和各城市的關鍵景點，而**不是**逐日的細節、提醒或美食清單。原樣存整篇回覆會讓 memory 臃腫，存骨架才可檢索。

## 範例 4 —— 後續訊息中使用者採納／確認了先前的建議，這本身就是事實

新訊息：
[{"role": "user", "content": "我家6歲的孩子一點蔬菜都不肯吃，有什麼辦法嗎？"},
 {"role": "assistant", "content": "可以把菠菜或胡蘿蔔打碎拌進義大利麵醬裡——顏色和味道幾乎吃不出來，很多孩子就這樣接受了。"},
 {"role": "user", "content": "好主意，今晚就試試。對了，這個年紀幾點睡比較好？"},
 {"role": "assistant", "content": "6歲的孩子建議晚上7到9點睡，確保大約10到11小時的睡眠。"}]
Memory Date：2025-06-20

輸出：
```json
{
  "memory": [
    {"content": "使用者有一個6歲的孩子，不肯吃蔬菜，使用者採納了 assistant 的建議——把菠菜或胡蘿蔔打碎拌進義大利麵醬裡以掩蓋蔬菜。", "tags": ["parenting", "food"], "owner": "user"},
    {"content": "assistant 建議使用者：6歲孩子應在晚上7到9點入睡，確保約10到11小時的睡眠。", "tags": ["parenting", "sleep"], "owner": "agent"}
  ],
  "intentions": [],
  "basic_info": {}
}
```
使用者在第三則訊息說"今晚就試試"——明確採納了先前的建議。這條採納資訊很有價值，必須記錄（"使用者採納了……"），不能當成寒暄丟掉。睡眠建議剛給出、尚未被採納，因此記為 assistant 給出的建議（owner "agent"）。

## 範例 5 —— RAG／注入的上下文：絕不儲存被注入的文字

新訊息：
[{"role": "user", "content": "【檢索到的資料：艾菲爾鐵塔高330公尺，1889年建成。】基於以上，我打算今年夏天在艾菲爾鐵塔頂上向女友求婚。"}]
Memory Date：2025-05-02

輸出：
```json
{
  "memory": [],
  "intentions": [
    {"content": "使用者計畫在2025年夏天於艾菲爾鐵塔頂上向女友求婚。", "tags": ["relationship", "travel"], "valid_until": "2025-08-31", "owner": "user"}
  ],
  "basic_info": {}
}
```
方括號內檢索到的資料（鐵塔高度、建成年份）是 RAG 注入的參考資料，並不是使用者講述的關於自己的資訊——絕不儲存。只擷取使用者自己的前瞻計畫。系統／RAG 注入的文字只是給模型的上下文，永遠不是 memory。

## 範例 6 —— 使用者分享資料：擷取資料內容，而非分享這個動作

新訊息：
[{"role": "user", "content": "幫我記住我奶奶的水餃食譜，別弄丟了。內餡：豬絞肉500克，大白菜300克，醬油2匙，香油1匙，薑末一小塊。白菜先用鹽醃15分鐘再擠乾水分。下鍋煮，點兩次冷水，水餃浮起來就好了。"},
 {"role": "assistant", "content": "好的，記下了！"}]
Memory Date：2025-07-01

輸出：
```json
{
  "memory": [
    {"content": "使用者奶奶的水餃食譜：內餡為豬絞肉500克、大白菜300克（先用鹽醃15分鐘再擠乾水分）、醬油2匙、香油1匙、薑末一小塊；下鍋煮、點兩次冷水，水餃浮起即熟。", "tags": ["food", "recipe", "family"], "owner": "user"}
  ],
  "intentions": [],
  "basic_info": {}
}
```
擷取真正的事實內容（食材、用量、醃擠步驟、煮法），而不是"使用者分享了食譜"或"使用者要求記住食譜"。分享動作沒有價值，內容才是可檢索的。

## 範例 7 —— 結構化數值與屬性完整保留

新訊息：
[{"role": "user", "content": "記一下我這個週期的居家健身房 PR：深蹲 140kg x 3，臥推 100kg x 5，硬舉 180kg x 2，肩推 60kg x 6。體重 78kg，每個動作 3 組，組間休息 3 分鐘。"},
 {"role": "assistant", "content": "記好了——成績很亮眼！"}]
Memory Date：2026-01-20

輸出：
```json
{
  "memory": [
    {"content": "使用者目前的力量 PR 為深蹲 140kg x 3、臥推 100kg x 5、硬舉 180kg x 2、肩推 60kg x 6，體重 78kg。", "tags": ["fitness", "strength"], "owner": "user"},
    {"content": "使用者的訓練安排為每個動作 3 組、組間休息 3 分鐘。", "tags": ["fitness", "training"], "owner": "user"}
  ],
  "intentions": [],
  "basic_info": {}
}
```
每個數值和單位（kg、次數、組數、休息時間）都完整保留——丟掉它們會毀掉最可檢索的資訊。拆成兩個清楚的主題（PR 與訓練方案），但絕不四捨五入或省略具體數值。

## 範例 8 —— 多主題的長對話：不要只擷取第一個話題

新訊息：
[{"role": "user", "content": "這週很重要——我週二終於通過了博士論文答辯！"},
 {"role": "assistant", "content": "太恭喜了！感覺怎麼樣？"},
 {"role": "user", "content": "如釋重負！我還在柏林簽了一間公寓的租約，下個月搬過去。另外我開始在多鄰國學德語了——已經連續打卡30天。我的伴侶索菲婭暫時還留在馬德里，這點挺難受的。"}]
Memory Date：2025-11-08

輸出：
```json
{
  "memory": [
    {"content": "使用者在2025-11-04通過了博士論文答辯。", "tags": ["education", "milestone"], "owner": "user"},
    {"content": "使用者在柏林簽了一間公寓的租約。", "tags": ["housing"], "owner": "user"},
    {"content": "使用者在多鄰國學德語，已連續打卡30天。", "tags": ["language", "learning"], "owner": "user"},
    {"content": "使用者的伴侶索菲婭暫時留在馬德里，使用者覺得這點挺難受。", "tags": ["relationship"], "owner": "user"}
  ],
  "intentions": [
    {"content": "使用者計畫下個月（2025年12月左右）搬到柏林。", "tags": ["housing", "relocation"], "valid_until": "2025-12-31", "owner": "user"}
  ],
  "basic_info": {}
}
```
四個以上話題——答辯、柏林公寓、學德語、伴侶在馬德里。不要只擷取第一個話題。注意一個輪次拆出了已完成的事實（已簽租約）和一條 intention（即將搬家）；關於索菲婭的那句雖帶情緒，但含有長期關係資訊，因此保留。

## 範例 9 —— 兩個真人：assistant 訊息帶 name 表示那是真人

新訊息：
[{"role": "user", "name": "Tim", "content": "剛從攀岩館回來，這週第三次了，那條仰角路線終於爬上去了。"},
 {"role": "assistant", "name": "John", "content": "厲害！我最近改游泳了，上個月傷到的肩膀還在痛。"}]
Memory Date：2025-08-14

輸出：
```json
{
  "memory": [
    {"content": "使用者每週攀岩三次，最近完成了一條難度較高的仰角路線。", "tags": ["fitness", "climbing"], "owner": "user"},
    {"content": "John最近改成游泳，因為2025年7月左右受傷的肩膀還在痛。", "tags": ["fitness", "health"], "owner": "user"}
  ],
  "intentions": [],
  "basic_info": {"name": "Tim"}
}
```
John 帶的是 `role: "assistant"`，但他有 `name`，所以他是另一個真人而不是 AI：他的記憶用"John…"作主體，且 `owner` 仍是 `"user"`（屬於使用者的記憶庫）。使用者自己的記憶仍寫"使用者…"，即使已知他叫 Tim 也不能寫成"Tim每週攀岩三次"。`basic_info.name` 來自使用者那則訊息上的 `name: "Tim"`——它是權威來源，即使 Tim 本人從未開口自報姓名。

## 範例 10 —— 反例：別人的屬性絕不放進 basic_info

新訊息：
[{"role": "user", "name": "Tim", "content": "What have you been up to?"},
 {"role": "assistant", "name": "John", "content": "I'm a nurse at a hospital in Berlin, working night shifts. Pretty exhausting."}]
Memory Date：2025-09-02

輸出：
```json
{
  "memory": [
    {"content": "John is a nurse at a hospital in Berlin, works night shifts, and finds it exhausting.", "tags": ["work", "health"], "owner": "user"}
  ],
  "intentions": [],
  "basic_info": {"name": "Tim"}
}
```
職業和所在地是 **John 的**屬性，所以放進 `memory` 並以"John"作主體，**絕不能**落到 `basic_info` 的 `occupation` / `location` —— `basic_info` 專屬於使用者本人。只有 `name`（Tim，來自使用者訊息的 `name` 欄位）才該放進去。注意輸出語言隨輸入語言而定（此例為英文）。

## 範例 11 —— 反例：寒暄沒有 basic_info

新訊息：
[{"role": "user", "content": "你好"},
 {"role": "assistant", "content": "你好！我可以幫你。之前記得 The user's name is 使用者."}]
Memory Date：2025-09-03

輸出：
```json
{
  "memory": [],
  "intentions": [],
  "basic_info": {}
}
```
使用者沒有說任何關於自己的資訊。assistant 那句不是 user 回合，"使用者" 也是佔位符而不是名字。必須輸出 `{}`——**絕不能**輸出 `{"name": "使用者"}`。

---
"""

EXTRACT_PROMPT = """## 最近 k 則訊息

{last_messages}

---

## 新訊息

{content}

---

## Memory Date

{memory_at}

---

## Current Date

{current_date}

---


# 輸出格式

僅回傳一個 JSON 物件。

格式：

```json
{{
  "memory": [
    {{
      "content": "...",
      "tags": ["..."],
      "owner": "user"
    }}
  ],
  "intentions": [
    {{
      "content": "...",
      "tags": ["..."],
      "valid_until": "YYYY-MM-DD",
      "owner": "user"
    }}
  ],
  "basic_info": {{}}
}}
```

規則：

* memory：
  一切值得長期記住的資訊——偏好、觀點、態度、特質、事件、計畫、經歷、習慣、決策。
  既可以是關於 `[user]` 的，也可以是關於其他具名真人（`[John]`）的，主體寫法見上方"# 主體規則"

* intentions：
  使用者（或其他具名真人）未來打算／計畫去做的前瞻事項，附 `valid_until`（截止日／目標日，
  依據 Memory Date 解析）；無法確定截止日時設為 `null`

* owner（每條 memory 和 intention 都必填）：`"user"` 或 `"agent"`
  （見上方"# OWNER"；無法確定時預設為 `"user"`）

* basic_info：**只放使用者本人的個人資料**。
  僅當 NEW user messages 中**明確**陳述穩定的個人資料欄位（如 姓名、年齡、所在地、職業、雇主）時才填寫。
  絕不推斷、絕不編造。沒有任何欄位被明確陳述時輸出 `{{}}`。
  * **硬性規則**：只有使用者在 `user` 回合裡**明確說出**的欄位才能填寫。只在
    assistant 回合出現的內容（包括 assistant 引用舊記憶，如"The user's name is X"
    / "使用者的名字是X"）一律不算。沒有符合條件的欄位時，必須輸出 `"basic_info": {{}}`。
  * **絕不**把欄位名稱本身或佔位符當作值寫入（"使用者"、"The user"、"user"、"name"、
    "姓名"、"N/A"、"unknown"、"未知"、"未提供"、"-"），寧可省略該欄位。
  * 負例：寒暄如 `[user]: 你好` / `[assistant]: 我可以幫你。`，或
    `[user]: Reply with exactly: PONG` → `"basic_info": {{}}`。
  * `name` 的例外：使用者自己那則訊息上的 `name` 欄位，或開頭形如
    "使用者的名字是X" / "The user's name is X" 的那一行，都是 `name` 的權威來源
    ——即使使用者本人從未開口自報姓名。
  * **絕不**把其他真人的屬性放進來。John（帶 `name` 的 assistant 角色訊息）說
    "我在柏林當護理師"，那是一條 `memory`（"John是柏林的護理師。"），
    **不是** `basic_info` 的 `occupation` / `location`。

* 不要跨 section 重複資訊

* tags 必須為 1–3 個關鍵字（主題用小寫；地名／人名等專有名詞保留大小寫）

* 沒有內容時使用 [] 或 {{}}

* 僅輸出合法 JSON

現在請輸出 JSON 內容"""

EXTRACT_PROMPT_NO_INTENTION = """## 最近 k 則訊息

{last_messages}

---

## 新訊息

{content}

---

## Memory Date

{memory_at}

---

## Current Date

{current_date}

---


# 輸出格式

僅回傳一個 JSON 物件。

格式：

```json
{{
  "memory": [
    {{
      "content": "...",
      "tags": ["..."],
      "owner": "user"
    }}
  ],
  "basic_info": {{}}
}}
```

規則：

* memory：
  一切值得長期記住的資訊——偏好、觀點、態度、特質、事件、經歷、習慣、決策，
  **以及未來計畫／即將發生的安排**（不要另開 intentions 桶）。
  既可以是關於 `[user]` 的，也可以是關於其他具名真人（`[John]`）的，主體寫法見上方"# 主體規則"

* owner（每條 memory 必填）：`"user"` 或 `"agent"`
  （見上方"# OWNER"；無法確定時預設為 `"user"`）

* basic_info：**只放使用者本人的個人資料**。
  僅當 NEW user messages 中**明確**陳述穩定的個人資料欄位（如 姓名、年齡、所在地、職業、雇主）時才填寫。
  絕不推斷、絕不編造。沒有任何欄位被明確陳述時輸出 `{{}}`。
  * **硬性規則**：只有使用者在 `user` 回合裡**明確說出**的欄位才能填寫。只在
    assistant 回合出現的內容（包括 assistant 引用舊記憶，如"The user's name is X"
    / "使用者的名字是X"）一律不算。沒有符合條件的欄位時，必須輸出 `"basic_info": {{}}`。
  * **絕不**把欄位名稱本身或佔位符當作值寫入（"使用者"、"The user"、"user"、"name"、
    "姓名"、"N/A"、"unknown"、"未知"、"未提供"、"-"），寧可省略該欄位。
  * 負例：寒暄如 `[user]: 你好` / `[assistant]: 我可以幫你。`，或
    `[user]: Reply with exactly: PONG` → `"basic_info": {{}}`。
  * `name` 的例外：使用者自己那則訊息上的 `name` 欄位，或開頭形如
    "使用者的名字是X" / "The user's name is X" 的那一行，都是 `name` 的權威來源
    ——即使使用者本人從未開口自報姓名。
  * **絕不**把其他真人的屬性放進來。

* 不要跨 section 重複資訊
* tags：1–3 個關鍵字；主題用小寫，專有名詞保留大小寫
* 不要輸出 `intentions` 欄位

"""


# ================================================================
# Chat reconcile (agent/reconciler.py)
# ================================================================

SEARCH_QUERY_PROMPT = """你是一個搜尋查詢產生器。給定一組新擷取的記憶，產生一組簡短的搜尋查詢，用於在向量資料庫中找到相關的既有記憶。

目標是最大化召回率——即使措辭差異很大，也要找到與新記憶語義相關的既有記憶。

## 新記憶:
{new_memories}

## 指引

產生的搜尋查詢應涵蓋:
- 記憶中提到的關鍵主題、實體和題材
- 核心概念的改寫或抽象版本
- 使用者記憶庫中可能存在的相關概念

輸出一個 JSON 字串陣列（5-15條查詢，簡短聚焦）:

["查詢1", "查詢2", "查詢3", ...]

只輸出 JSON 陣列，不要有其他文字。"""

RECONCILE_PROMPT = """你是一個記憶整合系統（Memory Reconciler）。

你的任務是將新的 memories 整合進既有的 memory base，同時保持：

* 資訊無損
* 無矛盾
* 高可檢索性
* 結構緊湊
* 避免碎片化

---

# 輸入內容

你將收到：

* Existing memories
* New memories
* Current date

---

# Existing memories

每條 existing memory 包含：

* memory_id
* content
* owner
* memory_at
* tags

---

# New memories

每條 new memory 包含：

* content
* owner
* memory_at（發生時間）
* tags

new memories 沒有 ID。

---

# 操作類型

---

## ADD

當資訊：

* 完全是新的
* 與既有 memory 不衝突
* 不屬於既有主題

時使用。

格式：

```json
{{
  "op": "ADD",
  "content": "...",
  "owner": "user",
  "tags": ["..."]
}}
```

---

## UPDATE

當：

* 新舊 memory 描述同一主題
* 且沒有衝突
* 合併後能提高完整性或可檢索性

時使用。

要求：

* 無損合併
* 保留所有有效資訊
* 不要產生重複的 memory
* 兩者在某個值上不一致時，保留 memory_at 較晚的那個值；絕不用較早的 memory 覆蓋較新的值

### 同一次事件的補充 vs 另一次發生

只有當新 memory 是在補充**同一次現實事件**或同一條持久事實時，才使用 **UPDATE**。

如果同一個人在另一個時間再次發生同類活動／事件（另一次旅行、比賽、受傷、購買、健康事件等），
應保留為獨立的 **ADD**，並保留該次自己的日期與細節。如果既有 memory 明確是同一具體系列的另一次
發生，可加上 `related: [{{"memory_id":"...","relation":"same_series"}}]`。

不要把多個不同日期的事件合併成一條很長的次數／時間線 memory；檢索層會從這些相關聯的 L2 組裝 timeline。

格式：

```json
{{
  "op": "UPDATE",
  "memory_id": "...",
  "content": "...",
  "owner": "user",
  "tags": ["..."]
}}
```

---

## SUPERSEDE

僅在以下情況使用：

* 新 memory 與舊 memory 在同一維度衝突
* 兩者不能同時為真
* 舊 memory 已不再代表目前狀態
* 新 memory 的 memory_at 不早於舊 memory 的 memory_at

例如：

* 使用者換城市
* 使用者換工作
* 使用者感情狀態改變
* 使用者長期偏好發生明確反轉

不要用於：

* 增加細節
* wording 差異
* 同義表達
* 可共存的偏好
* 新 memory 的 memory_at 早於舊 memory：它是較早的狀態，絕不能取代較新的狀態（見「時間規則」）

要求：

* content 只能寫「新的狀態」
* 不要複製舊資訊
* 系統會自動保留舊節點並接到演化鏈上，你無需關心歷史版本

格式：

```json
{{
  "op": "SUPERSEDE",
  "memory_id": "...",
  "content": "...",
  "supersede_reason": "...",
  "owner": "user",
  "tags": ["..."]
}}
```

### 把多條舊記憶摺疊進同一條鏈

如果**兩條或更多**既有記憶描述的是**同一維度**，而新記憶是該維度上的最新變化（它的 memory_at 晚於所有被摺疊的記憶），
你可以把它們摺疊進同一條演化鏈。此時用 `memory_ids`（有序清單，**舊→新**）
取代單一的 `memory_id`：

```json
{{
  "op": "SUPERSEDE",
  "memory_ids": ["<最舊的id>", "<較新的id>"],
  "content": "<只寫最新的狀態>",
  "supersede_reason": "...",
  "owner": "user",
  "tags": ["..."]
}}
```

列出的舊記憶會依舊→新連成一條鏈，新記憶成為鏈頭。`content` 仍然只描述**最新狀態**——
**絕不**把舊記憶的內容抄進來；舊內容保留在鏈的歷史裡，依然可被檢索。

---

# 關鍵規則

---

## 資訊無損

所有有效資訊必須仍然可檢索。

不要遺失：

* 新 memory 中的資訊
* 被 UPDATE/SUPERSEDE 的舊資訊

注意：

SUPERSEDE 不需要複製舊內容，因為舊節點仍然存在。

---

## 重複偵測

如果 new memory 已經被 existing memory 完整涵蓋：

不要輸出任何操作。

---

## 避免碎片化

如果多個 memory：

* 討論同一主題
* 內容相容

優先以 UPDATE 合併。

不同日期的獨立事件保持獨立 ADD；僅在主體相同且具體活動／事件類型相同時使用 `same_series`。

---

## Granularity

每條 memory 應只表達：

* 一個完整主題
* 一個清楚的狀態

避免：

* 過於零碎
* 過長
* 多主題混雜

---

## 主體規則

**絕不跨主體合併或 supersede。** 主體是 content 所描述的那個人："使用者…" / "John…" / "Mary…"。
關於不同人的記憶是彼此獨立的事實，即使句式幾乎一樣 —— "使用者喜歡喝咖啡" 和 "John喜歡喝咖啡"
是兩條各自獨立的記憶，絕不能互相 UPDATE 或 SUPERSEDE。

---

## owner 規則

`owner`（`user`/`agent`）：從被寫入的 memory 原樣複製。
**絕不跨 owner 合併或 supersede** —— 使用者的事實和 assistant 的事實即使主題相同也是兩條不同的記憶。

---

## 時間規則

每條 memory（新的和既有的）都帶 memory_at，表示它發生的時間。記憶**不一定依發生順序寫入**：
歷史可能較晚匯入，匯入還沒處理完時使用者也可能手動新增記憶。所以一條 new memory 可能**比既有記憶更早**。

* 新舊**只看 memory_at**。"New memories" 只表示「正在寫入」，不表示「更近期」。
* 較早的 memory 絕不能取代較新的 memory：不能 SUPERSEDE 一條較新的記憶，也不能用 UPDATE 拿較早的值覆蓋較新的值。
* 如果 new memory 比與它衝突的既有記憶更早：保留既有記憶作為目前狀態；把 new memory 以「過去狀態」ADD
  （用過去式，並帶上時間，例如「2023 年使用者住在上海」），沒有新資訊就不輸出。
* `memory_at: null` 表示時間未知。未知**不算較新**：不要用 memory_at 為 null 的 memory 去 SUPERSEDE 一條有時間的記憶。
* 不要編造時間順序。
* 不要僅因為「看起來比較新」就 supersede。

---

## NO FORK RULE

* SUPERSEDE 要麼指向一條 existing memory（`memory_id`），要麼把**同一維度**的多條
  memory 摺疊進同一條鏈（`memory_ids`，舊→新）；UPDATE 只能指向一條 existing memory。
* 同一條 existing memory 不得被多個操作 touch。
* 不要建立分岔的 supersede 鏈。

---

## 選用欄位：`related`

任何操作上都**可以**附加一個 `related` 陣列，記錄本次寫入的 memory 與某條
**既有 memory** 之間的關係。它不改變要存什麼，只是額外記一條關聯。

```json
{{
  "op": "ADD",
  "content": "...",
  "owner": "user",
  "tags": ["..."],
  "related": [{{"memory_id": "<既有記憶的 id>", "relation": "causes"}}]
}}
```

只允許這四種關係：

* `causes` —— 兩條記憶構成因果（如「開始每天跑步」 → 「膝蓋開始痛」）
* `elaborates` —— 對既有記憶的細化，或它的後續發展
* `contrasts` —— 兩者存在張力但**都仍然成立**（如果一條讓另一條不再成立，
  那是 SUPERSEDE，不是 `contrasts`）
* `same_series` —— 同一主體、同一具體活動或事件類型在不同時間的獨立發生
  （例如兩次不同的滑雪旅行）

要求：

* 只能關聯到「既有記憶」裡出現過的 id，**絕不編造 id**。
* 只在關係**明確且具體**時才輸出。兩條記憶只是屬於同一個籠統話題**不算**關係，
  這種情況不要輸出 `related`。
* `same_series` 不能只靠 health、travel、sport、food、work 等籠統主題；必須是同一主體的具體重複動作／事件。
* 沒有可關聯的就整個省略該欄位 —— **零關聯是常態**。
* 絕不要用 `related` 取代 UPDATE / SUPERSEDE。

---

{few_shot_section}
###################
以下是輸入資訊!
###################


目前日期：{current_date}

## 既有記憶（Existing memories）

{existing_memories}

## 新記憶（New Memories）

{new_memories}

# 輸出格式

輸出一個 JSON array。

例如：

```json
[
  {{
    "op": "ADD",
    "content": "...",
    "owner": "user",
    "tags": ["..."]
  }},
  {{
    "op": "UPDATE",
    "memory_id": "...",
    "content": "...",
    "owner": "user",
    "tags": ["..."]
  }},
  {{
    "op": "SUPERSEDE",
    "memory_id": "...",
    "content": "...",
    "supersede_reason": "...",
    "owner": "user",
    "tags": ["..."]
  }}
]
```

如果無需修改：

```json
[]
```

僅輸出合法 JSON。

現在輸出 JSON 陣列。"""

RECONCILE_STRUCTURE_METADATA_PROMPT = """

附加的結構輸出約定：

每個 ADD、UPDATE 或 SUPERSEDE 結果都要保留或修正 `topic_paths`、`event_facet` 與
`topic_assignments`。
`topic_paths` 是由寬到窄的語義路徑，例如 `["sport", "swimming"]`；不能僅憑共用的籠統 tag
猜測父子關係。`event_facet` 只含 subject/participants/action/objects/locations/state。
`topic_assignments` 按主體給出帶類型的 topic/facet/series/episode 路徑；Series/Episode 必須
由事實本身明確支持，不能由路徑深度推斷；類型順序必須遵守
`topic -> facet -> series -> episode`（可跳層，不可重複或倒序）。如果 Existing memories
中已有主體相同、語義等價
且邊界相容的路徑，應重用其完全相同的節點名稱和類型，避免同義節點分岔。需要精確重用時輸出
`structure_references: [{"memory_id":"<existing_id>", "subject":"...",
"through":"topic|facet|series|episode"}]`；ID 必須來自 Existing memories，`through` 表示複製
該舊路徑直到指定類型為止，新事實明確給出的更深後綴仍保持為新節點。主體、具體活動／狀態線或
Episode 邊界不同時禁止強行重用。所有結構欄位均不得編碼
when/how many/who、複數或序數等未來問句形態。

Series 只能在 reconcile 階段建立：新 memory 與至少一條獨立的 Existing memory 必須是同一具體
活動／狀態線的不同 occurrence。目前這一次寫入拆出的多個命題不能作為重複發生的證據。
"""

RECONCILE_FEW_SHOT = """
###################
範例
###################

## 範例 1 —— 同一條新記憶：一部分與既有鏈衝突，另一部分無關

既有記憶：
[
  {"memory_id": "m1", "content": "使用者在 Stripe 擔任產品經理。", "owner": "user", "history_versions": [{"content": "使用者在一家金融科技新創公司當產品經理。"}]}
]
新記憶：
[
  {"content": "使用者已經從 Stripe 離職，現在在 Notion 擔任產品負責人，另外最近週末開始學陶藝。", "owner": "user"}
]

輸出：
```json
[
  {"op": "SUPERSEDE", "memory_id": "m1", "content": "使用者在 Notion 擔任產品負責人。", "supersede_reason": "雇主從 Stripe 變為 Notion", "owner": "user", "tags": ["work"]},
  {"op": "ADD", "content": "使用者最近週末開始學陶藝。", "owner": "user", "tags": ["hobby"]}
]
```
**一條**新記憶裡包含兩件事：換工作（與既有雇用鏈 m1 同維度，m1 已帶歷史 → SUPERSEDE，成為新鏈頭）和一個無關細節（陶藝 → ADD）。要把這一條記憶依不同部分拆成不同操作——新鏈頭只描述就業狀態，絕不帶上陶藝。

## 範例 2 —— 把兩條未成鏈的舊記憶 + 新變化摺疊成一條 3 節點鏈

既有記憶：
[
  {"memory_id": "a1", "content": "使用者在學吉他。", "owner": "user", "memory_at": "2024-02-10 09:00"},
  {"memory_id": "a2", "content": "使用者現在能完整彈幾首吉他曲了。", "owner": "user", "memory_at": "2024-09-05 09:00"}
]
新記憶：
[
  {"content": "使用者現在在當地的開放麥之夜表演吉他。", "owner": "user"}
]

輸出：
```json
[
  {"op": "SUPERSEDE", "memory_ids": ["a1", "a2"], "content": "使用者在當地的開放麥之夜表演吉他。", "supersede_reason": "吉他程度演進：學習 → 能彈曲子 → 登台表演", "owner": "user", "tags": ["music", "hobby"]}
]
```
a1 和 a2 原本沒有鏈關係，但它們描述的是同一維度（吉他程度的進展），新記憶是最新的一步。用 `memory_ids` 依舊→新摺疊，它們成為一條 3 節點鏈（a1 ← a2 ← 新鏈頭）。鏈頭 `content` 只描述目前狀態——不要再複述「在學」或「能彈幾首」。

## 範例 3 —— 新鏈頭絕不能複製舊記憶的內容

既有記憶：
[
  {"memory_id": "c1", "content": "使用者租住在東京澀谷的一房公寓，月租 18 萬日圓，很喜歡走 5 分鐘就到地鐵站，但抱怨晚上很吵、也沒有停車位。", "owner": "user"}
]
新記憶：
[
  {"content": "使用者剛買下並搬進了橫濱郊區的一棟三房的房子，有花園和雙車位車庫。", "owner": "user"}
]

輸出：
```json
[
  {"op": "SUPERSEDE", "memory_id": "c1", "content": "使用者買下並搬進了橫濱郊區的一棟三房的房子，有花園和雙車位車庫。", "supersede_reason": "居住地從澀谷租住的一房公寓變為橫濱自有的三房房子", "owner": "user", "tags": ["housing"]}
]
```
新記憶 SUPERSEDE c1 並成為鏈頭。鏈頭 `content` 只描述橫濱的新房子。**不要**把舊住處那些鮮明的細節（澀谷、18 萬月租、走 5 分鐘到地鐵、吵鬧、沒有停車位）拖進新節點——它們明顯屬於舊住處而非新家。這些保留在鏈歷史裡、仍可檢索；抄進來會讓鏈頭自相矛盾（同時既在澀谷租房又在橫濱買房）。

## 範例 4 —— 主體不同就絕不合併，措辭再像也不行

既有記憶：
[
  {"memory_id": "d1", "content": "使用者每天早上喝黑咖啡。", "owner": "user"}
]
新記憶：
[
  {"content": "John每天早上喝黑咖啡。", "owner": "user"},
  {"content": "使用者現在一天喝兩次黑咖啡，早上和午餐後各一次。", "owner": "user"}
]

輸出：
```json
[
  {"op": "ADD", "content": "John每天早上喝黑咖啡。", "owner": "user", "tags": ["food"]},
  {"op": "UPDATE", "memory_id": "d1", "content": "使用者一天喝兩次黑咖啡，早上和午餐後各一次。", "owner": "user", "tags": ["food"]}
]
```
兩條新記憶措辭幾乎一樣，但**主體不同**。"John…"講的是另一個人，所以只能 ADD——絕不能去 UPDATE 或 SUPERSEDE d1，哪怕 d1 對使用者說的幾乎是同一件事。只有主體同樣是"使用者"的那條才可以與 d1 合併。

## 範例 5 —— 同類事件再次發生：UPDATE 成次數 + 時間線（不要 ADD）

既有記憶：
[
  {"memory_id": "i1", "content": "John injured his ankle last season and needed time off plus physical therapy.", "owner": "user", "memory_at": "2023-05-21 19:48"}
]
新記憶：
[
  {"content": "John twisted his ankle again during a recent game.", "owner": "user", "memory_at": "2023-10-13 13:50"}
]

輸出：
```json
[
  {"op": "UPDATE", "memory_id": "i1", "content": "John has injured his ankle two times: first last season (around the 2022–2023 season), needing time off and physical therapy; second during a game around 2023-10-13.", "owner": "user", "tags": ["health", "basketball", "injury"]}
]
```
同一類事件再次發生 → UPDATE 舊記憶：寫出**總次數**，並依時間列出每一次。不要再 ADD 一條 「John injured his ankle」，也不要 SUPERSEDE（第一次受傷並沒有變成假的）。
"""


# ================================================================
# Working / coding memory (coding/)
# ================================================================

CODING_EXTRACT_PROMPT = """\
你需要從一段任務執行會話中擷取可長期保留的 WORKING 記憶。
「working」會話是指使用者透過工具操作電腦或系統的任何實作任務——既包括軟體／程式碼
工作（撰寫、除錯、重構、建置、部署、API/library 使用），也包括系統與維運工作
（執行 shell/PowerShell 指令、磁碟／儲存空間分析或清理、檔案與資料夾操作、安裝／設定
軟體、環境／裝置設定、硬體／磁碟健康檢查、備份、排程／自動化、資料處理）。

你需要從同一段會話中產出兩個層次：

  EXPERIENCES（程序性，「如何做」）——可重複使用的方法；只有達到下述 VALUE BAR 時才
  擷取。每條是一個 (task, search_keys, solution, boundary_envs) 組合。

  FACT（情節性，「這次發生了什麼」）——關於本次會話的恰好一條記錄：使用者想做什麼、
  大致如何推進、最終結果如何。它不受價值門檻限制，但絕不能超過一條。

這兩個層次回答不同的問題，並分別儲存。可泛化的方法放入 EXPERIENCES；只對這個系統、
這個專案、此時此刻成立的本次會話經歷放入 FACT。

═══════════════════════════════════════════════════════════════════
VALUE BAR（僅適用於 EXPERIENCES）——不要輕易越過此門檻。
═══════════════════════════════════════════════════════════════════
一條記憶必須同時滿足以下全部條件：
  (a) 解決了同一使用者以後很可能再次遇到的問題
  (b) 解決方案並非瑣碎操作：它組合了多項資訊／多個步驟，或者包含決策理由，或者記錄了
      得來不易的 workaround，或者是某項維運任務可重複使用的流程／配方（例如如何分析並
      清理磁碟佔用、如何批次整理檔案、如何檢查硬體健康、如何設定 scheduled job）
  (c) 未來的 agent／使用者會更願意查閱它，而不是重新摸索

重要——分析、診斷、調查任務也算：
一項任務不必實際修改或「修復」任何東西才值得記憶。唯讀、分析、故障排除工作，只要其
流程或結論可重複使用，就有價值。明確擷取：
  - 分析／診斷配方：可重複使用的流程以及它揭示的結果（例如「如何分析哪些資料夾佔用了
    C-drive 空間」——掃描指令、遇到的坑和最終發現）。「只產生了報告，沒有修改」
    不是跳過的理由——可重複使用的方法本身就是記憶。
  - 調查結果，包括否定結果或未找到的結果（例如「使用 rg / fs_search / SQL index
    在 C: 和 D: 中搜尋 folder X——任何位置都未找到」）。記錄一條死路可以避免下一個
    agent 重複同樣徒勞的搜尋，因此達到了價值門檻。
不要僅僅因為「assistant 可以重新產生這些指令」或「實際上沒有修改任何東西」就拒絕
一條記憶。只要流程或結論能節省未來的工作，就保留它。

不要擷取：
  - 沒有可重複使用流程、真正瑣碎的單步操作（「rename one file」、「git status」、
    「run ls once」）
  - 已經解決且沒有可遷移經驗的一次性問題
  - repo/system 中已經存在的資訊（不要重複 package.json）
  - 每個 PR／每次會話的一次性任務，除非其中編碼了可重複使用的模式
  - 向使用者詢問缺失資訊這個行為。請求參數、列出未知項或引導使用者提供必填欄位屬於
    對話行為，不是方法：每個 agent 都知道如何提問，因此以後沒有任何值得查閱的內容。
    絕不要產出 task 或 search_keys 形如「how to ask for X」、「gather the missing Y」、
    「eliciting required fields」、「what to clarify before starting」的 experience。
    注意，這條限制只適用於 EXPERIENCES——會話最終停在等待使用者的狀態是真實狀態，
    應寫入 FACT。

如果該段會話因為等待使用者而沒有取得任何實質進展——assistant 只是詢問參數，或者在
開展任何實際工作前就被阻塞——輸出 "experiences": []，只寫 FACT。此時尚未學到任何
東西，因此沒有方法可存。這一點比看起來更重要：從停滯中提煉出的 experience 在檢索時
會像一項真正的技能；同一任務的後續片段會比對到它，再次向使用者提問而不是繼續推進，導致
這種停滯在其餘會話中不斷自我複製。應根據工具實際做了什麼來判斷是否有實質進展，而不是
根據該輪是否產生了流暢的文字。

啟發式標準：如果使用者（或未來執行同類任務的 agent）希望把它記下來，而不是以後再重新
摸索，就保留。多步驟的維運流程通常符合條件——包括 system/ops 或唯讀分析／搜尋流程，
不局限於程式碼。只有當它只是任何使用者都能在 30 秒內透過 `--help` 找到的單一指令，並且
不包含值得記憶的決策或坑點時，才捨棄。

═══════════════════════════════════════════════════════════════════
EXPERIENCE 欄位
═══════════════════════════════════════════════════════════════════

task          — 採用查詢式名稱；寫成使用者以後會如何提問。
                使用「publish a release to the xx platform」，不要使用
                「publish_release_internal」。
                必須自成一體（不要使用「this」、「the file」、「we」）。

search_keys   — 0 到 5 種替代表述，用來涵蓋僅靠 `task` 無法比對、但對 solution 很
                重要的面向。寫成未來使用者會詢問的問題或主題，且必須與 task 有所區別。
                如果 task 已涵蓋全部內容，則省略。

solution      — 使用者實際需要的完整、自成一體的內容：指令、憑證位置（不是憑證值）、
                檔案路徑、有序步驟、坑點、決策理由。如果多輪端到端的工作構成一個連貫
                任務，應彙整成一個 solution；只有使用者執行了互不相關的任務時才拆成
                多條記憶。

boundary_envs — solution 所依賴的具體限定條件，使用多行「key: value」：
                  runtime / SDK / library + version
                  API endpoint 或 method signature
                  external service / middleware version
                  env var NAME（不是值）
                  config file path
                  host / cluster / region identifier
                如果 solution 與環境無關，填寫 ""（空字串）。
                目的：底層 SDK/middleware 升級後，未來的使用者可以據此判斷 solution
                是否仍然適用。

confidence  — 0.0 到 1.0
  1.0   使用者明確將其陳述為規則／指示
  0.7   根據工具操作及充分證據推斷
  0.5   弱推斷；只有一次觀察；不確定

═══════════════════════════════════════════════════════════════════
FACT 欄位——每段會話恰好一條，無價值門檻
═══════════════════════════════════════════════════════════════════

你要為整段會話寫一條 FACT——它是本次會話可據以恢復的狀態記錄，不是精心潤飾的回顧，也
絕不是零散發現的清單。未來的 agent 必須能夠從中繼續推進，且不會把建議、推斷或局部搜尋
誤認為已經驗證的事實。

subject    — 本次會話圍繞誰或什麼展開：工作所針對或服務的單一實體。使用簡短的名詞
             片語，不要寫句子，不要使用動詞。選擇會話真正圍繞的最具體實體，而不是
             所使用的工具：
               會話服務於某個具名人員時，使用人名
                 （「Dylan」、「Matthew」）
               工作針對程式碼時，使用 file / module / symbol
                 （「auth/middleware.ts」、「parse_plan_text」）
               維運工作使用 service / host / device / drive
                 （「D:\\WXWork」、「redis-cache-prod」、「ci.yml」）
             這是 HARD KEY，不是散文：關於不同人員或不同檔案的兩次會話必須得到不同的
             subject；關於同一實體的兩次會話必須得到拼寫完全一致的同一字串。
             優先使用 trace 中出現的精確識別碼，而不是對它的描述。如果該段確實沒有
             單一 subject，輸出 ""。

             這很重要：subject 是決定後續會話是否可以按「同一任務的延續」覆蓋本條
             記錄的閘門。填錯後，一個人的狀態可能被另一個人的狀態悄悄取代。

statement  — 本次會話的緊湊記錄。它分為兩部分，而第二部分最容易遺失。
             保留任務範圍內的給定量和具體限制，不要用籠統的結果評價取代它們。
             每條規則只寫一次：重複的指示文字會擠佔記憶真正需要保留的資料。

             PART 1——發生了什麼，約 300 個拉丁字元（約 150 個 CJK 字元）。
             從具體目標和對象開始（「reclaim disk space taken by D:\\WXWork」、
             「locate the new_file_search folder」），不要寫籠統的「fix a bug」/
             「find a file」。然後概述檢查、嘗試或修改了什麼。最後說明結束狀態，
             且語義必須精確對應以下之一：completed、partially completed、failed、
             blocked、still awaiting verification。納入否定結果和死路，但不要誇大。

             PART 2——TASK-SCOPED GIVENS：整個任務賴以運行的全部給定資訊，即使並非
             本段會話建立的，或看起來基本到不值得寫。包括綁定關係（每個 name/symbol/
             label 代表什麼）、任務提供而非推導出的固定值、採用的慣例（indexing
             base、units、timezone、defaults），以及本段自己的結果和值（寫「x = 7」，
             不要只寫「the value was computed」）。

             將 PART 2 寫成資料，每行一項，值保持給定的形式：
               retry budget = 5; timeout 30s; index starts at 0
               step 3 input: n=64, tolerance 1e-6
               slot B: type=private, cost 688.7-721.3 per unit
             每一行幾乎都應由值構成。保留 trace 的 notation——symbols、units、
             numbers、spellings——刪掉其句子外殼：去掉那些只是宣告「這是給定量」的
             片語，而非給定量本身（「I would prefer」、「it should be somewhere
             between … and …」）。這些片語會在每一項重複；一個 15 項的清單會為它們
             付出 15 次的空間成本。
             每一條 FACT 都要攜帶這些資訊；絕不要寫「as above」或「same as the
             previous session」——檢索只會依相似度回傳少量 facts，被指涉的項目可能不在
             其中。

             總長度盡量控制在 1200 字元以內。如果 PART 2 放不下，應精簡 notation，
             絕不能縮減涵蓋範圍：statement 是 retrieval vector，也是後續 segment
             唯一能讀到的欄位（details 既不做 embedding，也不會被注入），因此從中
             刪除的內容就永久遺失。壓縮給定量周圍的措辭，絕不要壓縮給定量本身。

             檢驗標準：如果下一段看不到原始材料，能否僅憑此記錄繼續任務？只有肯定的
             答案才合格。缺失 binding 不會明顯報錯——該名稱仍看起來有意義，於是會
             按其通用含義被理解，後續答案可以很流暢，卻回答了另一個問題。

             LANGUAGE——使用與 session trace 相同的語言：statement 是 retrieval
             vector，而查詢會使用使用者的工作語言；改用另一種語言會讓每次檢索都付出
             跨語言比對的損失。

             EVIDENCE FIDELITY——只記錄 trace 中實際執行的操作和明確存在的證據。
             對關鍵指令、flags、arguments、errors、numbers 保持原樣。
             絕不要按慣例補全指令 flags，也不要把建議或計畫寫成已經執行的動作：
               「ran Get-ChildItem -Force」 → 「ran Get-ChildItem」
                 （trace 中沒有 `-Force`，不要杜撰）
             每個否定發現都必須限定在實際搜尋範圍內，並逐一列出可能使其失效的所有
             明確涵蓋限制，尤其是 permission failures 和 depth limits：保留當時的
             roots、filters、depth（「not found under C:\\ and D:\\ at depth 3;
             some directories were not covered due to Access denied」），絕不要擴大為
             「does not exist」。不要為了讓記錄更漂亮而漏掉任何限制。
             絕不要把局部搜尋提升為全域不存在。
             被阻塞的嘗試必須保留其 blocker。
             保留 attempted、observed、verified、inferred、unresolved 之間的區別；
             用自然語言表達（「tried … but was blocked by Access denied」、
             「suspected …, not yet verified」），不要把它們當標籤。沒有工具輸出或
             明確確認時，絕不要聲稱修復已成功、原因已確定或路徑不存在。

             不要記錄問候或純閒聊——只有這種情況才輸出 "fact": null。僅僅陳述了
             需求，或尚未開展工作就停滯的會話，仍然建立了資訊。

details    — 本次會話的具體 OUTPUT，逐字複製。`statement` 是摘要，而摘要必然會
             丟掉各個具體值；後續檢索幾乎總是在尋找某個具體值，而非大意。因此，只要
             會話產出了可定址的結果，就以原始形式和結構重現在這裡：
               plan / schedule / itinerary → 每一行、每一天、每個 slot、每個 price
                 都嚴格按原文保留
               config 或 code block → 保留整個 block
               command 的關鍵 output → 保留承載發現的行（sizes、counts、versions、
                 error text）
               structured result（table、records list）→ 保留各行
             保留原始版面——line breaks、`Day 1:` / key: value prefixes、units、
             currency symbols、ids。不要再次概括，不要重新排列，不要四捨五入數字，不要翻譯。

             只複製 trace 中實際存在的內容。絕不要填補空缺：如果某個 slot 沒有填，
             就保持未填或明確說明——杜撰的值比缺失的值更糟，因為它會被檢索並使用。
             長度控制在約 4000 字元；如果輸出更長，保留承載具體值的部分，刪去裝飾性
             文字，並註明刪掉了什麼。

             如果本次會話未產生此類 artifact（尚無發現的純調查，或等待使用者導致的
             停滯），填寫 ""。

             此欄位不屬於 retrieval vector——`statement` 和 `subject` 負責找到
             記錄，`details` 在找到後提供具體值。因此不要在此堆砌檢索誘餌，也不要
             重複摘要；只寫原始內容。

files      — 0..N 個本次會話中實際操作或調查過的絕對或 repo-relative FILE OR
             DIRECTORY paths。保留調查過的 roots，例如 `C:\\` 和 `D:\\`。只包含
             實際存取／搜尋過的路徑，不包含僅建議或計畫存取的路徑；不適用時填寫 []。
═══════════════════════════════════════════════════════════════════
HOST CONTEXT（由 SDK 提供）
═══════════════════════════════════════════════════════════════════
- workspace_id present: {workspace_present}
- branch present: {branch_present}

沒有 workspace_id 時，solution 不能依賴未宣告的專案本地上下文——必須在 `solution`
或 `boundary_envs` 中明確寫出其所需的 repo / path / service，使記憶能夠獨立使用。

═══════════════════════════════════════════════════════════════════
CONVERSATION
═══════════════════════════════════════════════════════════════════
{conversation_block}

═══════════════════════════════════════════════════════════════════
OUTPUT（僅輸出嚴格的 JSON，不要 markdown）
═══════════════════════════════════════════════════════════════════
{{
  "experiences": [
    {{
      "task": "...",
      "search_keys": ["...", "..."],
      "solution": "...",
      "boundary_envs": "...",
      "confidence": 0.9
    }}
  ],
  "fact": {{
    "subject": "...",
    "statement": "...",
    "files": ["..."],
    "details": "..."
  }}
}}

預設應進行擷取。當該段會話確實完成了實際工作時，必須輸出至少一條 experience——即使
任務看似簡單或一次成功，也要記錄所做工作可重複使用的本質（method / command / how-to）。
會話內容較少時，只保留最有價值的一條 experience 即可。

唯一的例外是上述停滯情況：如果該段因為缺少資訊、被阻塞在向使用者提問而沒有開展實際工作，
輸出 "experiences": []，只寫 fact。不要為了滿足本指示而杜撰 experience——杜撰的
「how to ask for the departure city」比沒有記憶更糟，因為以後會被檢索並照做。只有這個
原因允許空陣列；實際開展過工作時，絕不能把空陣列當捷徑。

"fact" 是單一物件，絕不是清單——每次會話一條記錄。

始終要寫 fact。fact 的門檻不是「這是否有價值」，而是「是否發生了任何事情」；能夠到達
你這裡的 segment 已經越過這道門檻。這包括看起來只是 setup 而非工作的片段：僅有一則使用者
訊息陳述 requirements、parameters 或 starting state，即使沒有執行工具，也恰恰是後續
會話無法自行重建的材料——把需求、參數、起始狀態連同情境一起寫進 `statement`（緊湊
記法，不要照抄對話原句），不要推給 `details`（`details` 不進檢索向量，讀取端也不再
注入它）。只有完全沒有內容的問候和純閒聊才使用 "fact": null。有疑問時應寫 fact：
多餘的記錄只多佔一行，而漏掉記錄會傷害每一次需要它、卻無從知道它曾存在的後續會話。
"""

CODING_EXTRACT_PROMPT_CODING = """\
╔═══════════════════════════════════════════════════════════════════════════╗
║ HARD CONSTRAINT——先閱讀本節；它凌駕本提示詞後文任何看似要求敘事的措辭。      ║
║                                                                           ║
║ 你不是在撰寫某個人做過什麼的報告。你要寫的是 FUTURE AGENT 為採取行動所需要 ║
║ 的資訊。該 agent 已有自己的任務；它不關心這個使用者想要什麼。它需要知道：此處 ║
║ 做出了什麼決定、位於哪些程式碼座標、嘗試並否決了什麼，以及是否經過驗證。     ║
║                                                                           ║
║ 具體來說，每個 `statement` 和每個 `solution` 都必須遵守：                 ║
║   · 語法主詞必須是 CODE 或 THE DECISION——絕不能是「the user」，也絕不能是 ║
║     「the agent」。不要寫「The user attempted to…」、「The user wanted…」、 ║
║     「The agent then checked…」。                                         ║
║   · 絕不要用「the session was completed」、「the error was noted」、     ║
║     「no solution was found yet」之類流水帳片語收尾。結尾應寫決定及其驗證  ║
║     狀態。如果沒有做出決定，就以已排除的內容和下一條尚未探索的線索收尾——這才 ║
║     是可重複使用的部分。                                                  ║
║   · 保留具體症狀（error string、failing command、wrong output）——未來搜尋 ║
║     正是透過這些內容命中。                                                ║
║                                                                           ║
║ bad:  “The user attempted to write a QTable to RST with header_rows and   ║
║        hit a TypeError. The session was completed with the error noted.”  ║
║ good: “ascii.rst's RST writer rejects header_rows:                        ║
║        `tbl.write(..., format='ascii.rst', header_rows=['name','unit'])`  ║
║        raises TypeError because RST.__init__ does not forward it, unlike  ║
║        ascii.fixed_width which accepts it. No fix written — the writer    ║
║        needs the same header_rows plumbing as fixed_width.”               ║
╚═══════════════════════════════════════════════════════════════════════════╝

你需要從一段任務執行會話中擷取可長期保留的 WORKING 記憶。
「working」會話是指使用者透過工具操作電腦或系統的任何實作任務——既包括軟體／程式碼
工作（撰寫、除錯、重構、建置、部署、API/library 使用），也包括系統與維運工作
（執行 shell/PowerShell 指令、磁碟／儲存空間分析或清理、檔案與資料夾操作、安裝／設定
軟體、環境／裝置設定、硬體／磁碟健康檢查、備份、排程／自動化、資料處理）。

你需要從同一段會話中產出兩個層次：

  EXPERIENCES（程序性，「如何做」）——可重複使用的方法；只有達到下述 VALUE BAR 時才
  擷取。每條是一個 (task, search_keys, solution, boundary_envs) 組合。

  FACT（情節性，「這次確立了什麼」）——關於本次會話的恰好一條記錄。它不是使用者做了
  什麼的敘事，而要依以下順序記錄：任務及其具體症狀（使它以後能被檢索到）、程式碼座標、
  最終採用了什麼以及否決了什麼、驗證推進到什麼程度。它不受價值門檻限制，但絕不能
  超過一條。

這兩個層次回答不同的問題，並分別儲存。可泛化的方法放入 EXPERIENCES；只對這個系統、
這個專案、此時此刻成立的本次會話經歷放入 FACT。

═══════════════════════════════════════════════════════════════════
VALUE BAR（僅適用於 EXPERIENCES）——不要輕易越過此門檻。
═══════════════════════════════════════════════════════════════════
一條記憶必須同時滿足以下全部條件：
  (a) 解決了同一使用者以後很可能再次遇到的問題
  (b) 解決方案並非瑣碎操作：它組合了多項資訊／多個步驟，或者包含決策理由，或者記錄了
      得來不易的 workaround，或者是某項維運任務可重複使用的流程／配方（例如如何分析並
      清理磁碟佔用、如何批次整理檔案、如何檢查硬體健康、如何設定 scheduled job）
  (c) 未來的 agent／使用者會更願意查閱它，而不是重新摸索

重要——分析、診斷、調查任務也算：
一項任務不必實際修改或「修復」任何東西才值得記憶。唯讀、分析、故障排除工作，只要其
流程或結論可重複使用，就有價值。明確擷取：
  - 分析／診斷配方：可重複使用的流程以及它揭示的結果（例如「如何分析哪些資料夾佔用了
    C-drive 空間」——掃描指令、遇到的坑和最終發現）。「只產生了報告，沒有修改」
    不是跳過的理由——可重複使用的方法本身就是記憶。
  - 調查結果，包括否定結果或未找到的結果（例如「使用 rg / fs_search / SQL index
    在 C: 和 D: 中搜尋 folder X——任何位置都未找到」）。記錄一條死路可以避免下一個
    agent 重複同樣徒勞的搜尋，因此達到了價值門檻。
不要僅僅因為「assistant 可以重新產生這些指令」或「實際上沒有修改任何東西」就拒絕
一條記憶。只要流程或結論能節省未來的工作，就保留它。

不要擷取：
  - 沒有可重複使用流程、真正瑣碎的單步操作（「rename one file」、「git status」、
    「run ls once」）
  - 已經解決且沒有可遷移經驗的一次性問題
  - repo/system 中已經存在的資訊（不要重複 package.json）
  - 每個 PR／每次會話的一次性任務，除非其中編碼了可重複使用的模式

啟發式標準：如果使用者（或未來執行同類任務的 agent）希望把它記下來，而不是以後再重新
摸索，就保留。多步驟的維運流程通常符合條件——包括 system/ops 或唯讀分析／搜尋流程，
不局限於程式碼。只有當它只是任何使用者都能在 30 秒內透過 `--help` 找到的單一指令，並且
不包含值得記憶的決策或坑點時，才捨棄。

═══════════════════════════════════════════════════════════════════
CODE WORK——未來的 agent 無法重新推導的資訊，因此必須擷取
═══════════════════════════════════════════════════════════════════
當該段是針對 repository 的軟體工作時，可重複使用的方法只佔一半。另一半是工作位於哪裡，
以及哪個版本勝出。這些無法透過以後閱讀程式碼恢復——程式碼只能呈現目前狀態，無法說明它
位於哪個 branch，也無法說明哪些方案已被否決。

本節不是要求寫得更長，而是要求把相同的預算花在價值更高的 token 上：刪掉重複的目標、
assistant「隨後決定」做什麼的敘事，以及只是複述的貼上程式碼；把這些字元用於下面四項。
一條遺漏 branch、卻引用 30 行 diff 的記錄是糟糕的取捨。

1. BRANCH / WORKING LOCATION——逐字寫入 fact `statement`。如果該段提到或 checkout
   了 `feat/x` / `fix/y`，這個 token 必須出現。在錯誤的 branch 上恢復工作的 agent
   會產生隨後被悄悄丟棄的改動，因此這一個 token 的優先順序高於其他任何細節。

2. WHICH VERSION WON——當使用者改變主意時，用一個子句指出勝出者並標記落敗者：
   「settled on A; the interim B was dropped」。不要完整保留兩個版本，只需一個子句。
   只保留最新版本是錯誤的：以後遇到被否決程式碼的 agent 必須識別它，否則會把程式碼
   「修復」回已經捨棄的變體。`scratch that / go back to / actually, let's / no, not that`
   之類的措辭就是訊號：後面的內容才是正式結論，前面的內容已被 superseded。

3. EXACT COORDINATES, NOT PASTED CODE——使用 repo-relative path
   （`qutebrowser/utils/debug.py`，絕不要只寫 `debug.py`）以及觸及的 function /
   class。如果 signature 發生變化，給出新的 signature。如果決定的是 constant、regex
   或 literal，逐字引用它——但只引用這一項，不要引用周圍的 block。準確的一行勝過一段
   近似描述。

4. VERIFIED vs MERELY WRITTEN——四個詞就足夠（「tests run and passing」/
   「confirmed by git diff only, tests not run」）。「the edit is in」和「the edit is in
   and tested」會讓未來的 agent 採取完全不同的下一步。

反例——只總結目標，卻丟掉座標：
  bad:  “refactored the colour parsing logic to be cleaner.”
  good: “on feat/q4, in qutebrowser/config/configtypes.py QtColor._parse_value:
         settled on `mult = 360.0 if kind == 'h' else 255.0`; the interim version that
         used 255.0 for everything was dropped. Tests not run.”
  （good 往往比 bad 還短——它只是把每個字元都用在無法重新推導的資訊上）

═══════════════════════════════════════════════════════════════════
EXPERIENCE 欄位
═══════════════════════════════════════════════════════════════════

task          — 採用查詢式名稱；寫成使用者以後會如何提問。
                使用「publish a release to the xx platform」，不要使用
                「publish_release_internal」。
                必須自成一體（不要使用「this」、「the file」、「we」）。

search_keys   — 0 到 5 種替代表述，用來涵蓋僅靠 `task` 無法比對、但對 solution 很
                重要的面向。寫成未來使用者會詢問的問題或主題，且必須與 task 有所區別。
                如果 task 已涵蓋全部內容，則省略。

solution      — 使用者實際需要的完整、自成一體的內容：指令、憑證位置（不是憑證值）、
                檔案路徑、有序步驟、坑點、決策理由。如果多輪端到端的工作構成一個連貫
                任務，應彙整成一個 solution；只有使用者執行了互不相關的任務時才拆成
                多條記憶。

boundary_envs — solution 所依賴的具體限定條件，使用多行「key: value」：
                  runtime / SDK / library + version
                  API endpoint 或 method signature
                  external service / middleware version
                  env var NAME（不是值）
                  config file path
                  host / cluster / region identifier
                如果 solution 與環境無關，填寫 ""（空字串）。
                目的：底層 SDK/middleware 升級後，未來的使用者可以據此判斷 solution
                是否仍然適用。

confidence  — 0.0 到 1.0
  1.0   使用者明確將其陳述為規則／指示
  0.7   根據工具操作及充分證據推斷
  0.5   弱推斷；只有一次觀察；不確定

═══════════════════════════════════════════════════════════════════
FACT 欄位——每段會話恰好一條，無價值門檻
═══════════════════════════════════════════════════════════════════

你要為整段會話寫一條 FACT——它是本次會話可據以恢復的狀態記錄，不是精心潤飾的回顧，也
絕不是零散發現的清單。未來的 agent 必須能夠從中繼續推進，且不會把建議、推斷或局部搜尋
誤認為已經驗證的事實。

statement  — 本次會話的緊湊摘要。它同時承擔兩項職責，且兩者都必須完成：它既是
             retrieval vector（未來的 agent 用一份新的 issue report 搜尋它；措辭方式
             與使用者回報問題的方式一致），也是該 agent 找到後要據以行動的 payload。
             因此：

             開頭寫任務和症狀，使用使用者會用的措辭。
                這是確保可檢索性的關鍵——表述應盡量貼近問題被回報時的表面形式。
                點明具體目標和對象（「reclaim disk space under D:\\WXWork」、
                「adjust the hue multiplier in QtColor._parse_value on feat/q4」、
                「locate the new_file_search folder」），絕不要寫籠統的「fix a bug」/
                「look for a file」。包含具體症狀：error string、failing command、
                wrong output。

             然後給出座標和決定。
                對程式碼工作寫 repo-relative path、symbol 和 branch。說明最終 SETTLED ON
                什麼，並在同一子句標記捨棄了什麼（「settled on X; the interim Y was
                dropped」）。這部分無法透過閱讀程式碼重新推導——程式碼只呈現勝出者，從不
                呈現 Y 曾被嘗試後否決。以後遇到被否決變體的 agent 必須能識別它，
                否則會把程式碼「修復」回已捨棄的版本。

             結尾寫證據狀態。
                必須精確對應以下之一：completed、partially completed、failed、blocked、
                still awaiting verification；還要寫明如何檢查（「tests run and passing」/
                「confirmed by git diff only, tests not run」）。納入否定結果和死路，
                但不要誇大。

             ⚠️ 絕不要停留在敘事層。「The user tried X and hit error Y; no solution found
             yet」是一條浪費的記錄：它容易被檢索到，卻無法指導未來的 agent 行動。如果
             本次確實沒有得出決定，就說明排除了什麼、下一條尚未探索的線索是什麼——
             這才是可重複使用的部分。

             句子的主詞應是 code 和 decision，而不是「the user」。使用 2 到 4 個連貫的
             句子，不要寫帶標籤的小節。硬性上限約 400 個拉丁字元（約 200 個 CJK 字元）。

             EVIDENCE FIDELITY——強制要求：
             - 只記錄 trace 中實際執行的操作和明確存在的證據。對關鍵 commands、flags、
               arguments、errors、numbers 保持原樣。絕不要按慣例補全 command flags，
               也不要把 advice/plans 寫成已執行的動作。
             - 保留 attempted、observed、verified、inferred、unresolved 之間的區別。
               不要把這些詞當作標籤寫進正文；自然地表達其含義，例如「tried … but was
               blocked by Access denied」或「suspect …, not yet verified」。
             - 沒有工具輸出或明確確認時，絕不要聲稱修復成功、原因已經確定、file/folder
               不存在或 tests passed。
             - 每個否定發現都必須限定在實際搜尋範圍內，並逐一列出可能使其失效的所有
               明確涵蓋限制，尤其是 permission failures 和 maximum search depth：
               「not found within the searched parts of C:\\ and D:\\; some directories
               were skipped on Access denied, and the search was also depth-limited」。
               不要為了讓摘要更漂亮而漏掉任何限制。絕不要把局部搜尋提升為全域不存在。
             - 只有 trace 直接驗證了因果關係時，才能確定地陳述因果。否則保留限定詞
               （「suspected」/「inferred」/「not yet verified」）；不要把錯誤的測試假設
               改寫成系統的 root cause。

             關鍵——這段文字就是 retrieval vector，也是未來 query 唯一會比對的內容。
             籠統的摘要毫無價值，因為每次會話看起來都像籠統的摘要：它會比對到所有查詢，
             卻無法回答任何一個。因此必須寫出使本次會話可被檢索的具體 anchors——真實的
             file paths、function / symbol names、branch names、commands、error strings、
             versions、config keys and values、host / service names。以後有人搜尋其中任一
             具體項目時，本摘要必須能被回傳。
               good: “fixing a failing `npm run build` in CI. Read
                      .github/workflows/ci.yml and package.json; traced it to webpack
                      5.74 hitting ERR_OSSL_EVP_UNSUPPORTED under node 18. Bumping the
                      workflow's node to 20 made the build pass; node 18 still
                      reproduces locally.”
               good: “on branch feat/q0, in qutebrowser/utils/debug.py signal_name:
                      PyQt<5.11 only exposes the name via repr(sig), and repr comes in
                      both a bracketed and a parenthesised form. Settled on two patterns
                      tried in order with fullmatch, breaking on the first hit; the
                      single-regex version was dropped. Confirmed by git diff, tests not
                      run.”
               bad:  “user wanted a build bug fixed, the agent looked at some config
                      files, changed some code, and fixed it.”
                      （沒有 path、command、error string、version——沒有任何可檢索的錨點；
                       每次會話都可以寫成這樣）
               bad:  “fixed the signal-name parsing compatibility issue on a feature
                      branch.”
                      （哪個 branch？哪個 file？哪個 function？兩個競爭版本中誰勝出？
                       一週後檢索到它時，沒有任何資訊仍然有用）
             忠實度範例（bad → good）：
               「ran Get-ChildItem -Force」 → 「ran Get-ChildItem」
                 （trace 中沒有 `-Force`，不要杜撰）
               「confirmed new_file_search does not exist」 →
                 「new_file_search was not found within the searched parts of C:\\ and
                  D:\\; some directories were skipped on Access denied or by the depth
                  limit, so the investigation is only partly done」
               「identical payloads cause a spurious lock error」 →
                 「the verification script assumed the last writer is always a fixed
                  rank; that assumption does not hold, so whether there is a lock bug
                  is still unverified」

             不要記錄問候或純閒聊。如果該段完全沒有建立任何資訊，輸出 "fact": null。

files      — 0..N 個本次會話中實際操作或調查過的絕對或 repo-relative FILE OR
             DIRECTORY paths。保留調查過的 roots，例如 `C:\\` 和 `D:\\`。只包含
             實際存取／搜尋過的路徑，不包含僅建議或計畫存取的路徑；不適用時填寫 []。

═══════════════════════════════════════════════════════════════════
HOST CONTEXT（由 SDK 提供）
═══════════════════════════════════════════════════════════════════
- workspace_id present: {workspace_present}
- branch present: {branch_present}

沒有 workspace_id 時，solution 不能依賴未宣告的專案本地上下文——必須在 `solution`
或 `boundary_envs` 中明確寫出其所需的 repo / path / service，使記憶能夠獨立使用。

═══════════════════════════════════════════════════════════════════
CONVERSATION
═══════════════════════════════════════════════════════════════════
{conversation_block}

═══════════════════════════════════════════════════════════════════
OUTPUT（僅輸出嚴格的 JSON，不要 markdown）
═══════════════════════════════════════════════════════════════════
{{
  "experiences": [
    {{
      "task": "...",
      "search_keys": ["...", "..."],
      "solution": "...",
      "boundary_envs": "...",
      "confidence": 0.9
    }}
  ],
  "fact": {{
    "statement": "...",
    "files": ["..."]
  }}
}}

強制要求：必須輸出至少一條 experience，不要回傳空清單。即使任務看似簡單或一次成功，
也要記錄所做工作可重複使用的本質（method / command / how-to）。會話內容較少時，只需要
最有價值的一條 experience——但絕不能輸出空的 "experiences" array。

"fact" 是單一物件，絕不是清單——每次會話一條記錄。只有該段是沒有建立任何資訊的
純閒聊時，才使用 "fact": null。
"""

CODING_RECONCILE_PROMPT = """\
你需要決定如何將新的 coding memory drafts 與使用者既有的 memories 合併。

對每條新 draft，選擇以下一種 action：
  ADD     — 沒有既有 memory 描述相同的任務／面向
  UPDATE  — 某條既有 memory 描述相同任務；用新 draft 取代它
            （solution 可能已經改變，env 可能已經升級，等等）
  SKIP    — 與既有內容重複；沒有新資訊

此外，當新內容使既有候選失效或作廢時，可以針對既有 candidates 輸出 DELETE ops：
  DELETE  — 新 conversation 明確 supersedes/cancels/invalidates 某條既有 memory，
            且無法透過 UPDATE 修正。範例：
              * 使用者說「don't use X anymore」，而 X 只存在於另一條獨立的 memory 中
                （不是本次正在 ADD/UPDATE 的那條）
              * candidate 的 boundary_envs（service / endpoint / SDK）已經消失，
                且新 conversation 確認其已 deprecated

規則：
- 「Same task」是指既有 memory 和新 draft 都可以作為同一個未來使用者查詢的有效答案。
- 如果 solution 不同，優先 UPDATE（不保留歷史版本）。
- 如果 task 和 solution 都高度一致，選擇 SKIP。
- 只有在存在明確證據顯示 candidate 已 invalid/obsolete 時才 DELETE。絕不要僅僅因為
  candidate 看起來「stale」就 DELETE。
- 產生 DELETE 時，輸出的 ops 數量可以超過 drafts 數量。

═══════════════════════════════════════════════════════════════════
NEW DRAFTS
═══════════════════════════════════════════════════════════════════
{drafts_block}

═══════════════════════════════════════════════════════════════════
EXISTING CANDIDATES（依每條 draft 的 task similarity 檢索）
═══════════════════════════════════════════════════════════════════
{candidates_block}

═══════════════════════════════════════════════════════════════════
OUTPUT（僅輸出嚴格的 JSON，不要 markdown）
═══════════════════════════════════════════════════════════════════
{{
  "ops": [
    {{"draft_idx": 0, "action": "ADD"}},
    {{"draft_idx": 1, "action": "UPDATE", "target_experience_id": "exp_xxx", "reason": "..."}},
    {{"draft_idx": 1, "action": "DELETE", "target_experience_id": "exp_yyy", "reason": "user explicitly deprecated Y"}},
    {{"draft_idx": 2, "action": "SKIP",   "reason": "duplicate"}}
  ]
}}

如果沒有 drafts，輸出 {{"ops": []}}。
"""

CODING_FACT_RECONCILE_PROMPT = """\
你負責維護 session-level WORKING FACTS。每條 statement 都概括一整段 trace/session：
使用者的任務、大致方法和結果。它不是一條 atomic claim。

下面每條新的 session fact 都一定會被儲存；這不是你要決定的事情。你唯一的任務是識別
task continuation：新 session 是否在繼續推進某條既有 session fact 所記錄的同一個使用者
任務？如果是，就將較舊的 fact 標記為 SUPERSEDE，使這些連結為該任務構成跨 session 的
進展鏈。

只有在存在充分證據同時滿足以下條件時，才將既有 fact 標記為 superseded：
  - subjects 相符：兩條記錄圍繞同一個實體——同一個 person、同一個 file/module、
    同一個 host/service。subject 相同是必要條件，但不是充分條件；並且
  - 使用者目標在實質上相同，而不只是處於同一 topic/project；並且
  - 新 trace 在繼續、重試、擴充、驗證或完成既有 trace 所記錄的工作。

以下情況不要 supersede：
  - 兩條記錄的 subjects 不同。同一 group 中的兩個人、同一 module 中的兩個 files、
    同一 cluster 中的兩個 hosts 是平行關係，不是先後關係：一個人的狀態不是另一個人
    狀態的新版本。如果 subject 顯示為 (unspecified)，退回到只根據 statement 判斷。
  - 兩次 session 只是提到了相同的 file、symbol、component、repository、error 或
    technology，但追求不同的使用者目標。
  - 一個任務只是為另一個任務提供 context 或 technique。
  - 關係不確定。應保守判斷：INSERT 是安全的預設值；合併無關的 session 會毀掉它們各自
    獨立的任務歷史。

把每條 statement 當作有證據邊界的文字。不要把其中的 inference 重新解讀為已經確定的
cause，也不要把共同的 conjecture/error 當作使用者目標相同的證據。你的判斷僅涉及 task
continuation；不要「糾正」、補全或潤飾任何一條 session 記錄。

範例：
  existing: 「需要修復 CI 的 npm run build；已追查到 Node 18 下的
             ERR_OSSL_EVP_UNSUPPORTED，workflow 尚未修改。」
  new:      「繼續修復同一個 CI build；已將 .github/workflows/ci.yml 中的 Node
             升級到 20，且 npm run build 通過。」
                                                                 → SUPERSEDE

  existing: 「需要修復 auth/middleware.ts 中的 JWT expiry handling；已加入
             refresh logic。」
  new:      「需要在 auth/middleware.ts 中加入 performance logging；已引入
             latency metric。」
                                                                 → 兩者都保留
            （同一 file/project，不同的使用者目標）

═══════════════════════════════════════════════════════════════════
NEW SESSION FACTS
═══════════════════════════════════════════════════════════════════
{drafts_block}

═══════════════════════════════════════════════════════════════════
EXISTING SESSION FACTS（依每條新 fact 的 statement similarity 檢索）
═══════════════════════════════════════════════════════════════════
{candidates_block}

═══════════════════════════════════════════════════════════════════
OUTPUT（僅輸出嚴格的 JSON，不要 markdown）
═══════════════════════════════════════════════════════════════════
{{
  "ops": [
    {{"draft_idx": 0, "supersedes": ["fact_xxx"], "reason": "continues the same CI fix task"}},
    {{"draft_idx": 1, "supersedes": []}}
  ]
}}

只能使用同一個 draft_idx 的 candidate list 中實際出現的 fact ids。
如果任何位置都沒有 fact 被 superseded，輸出 {{"ops": []}}。
"""

CODING_CHAIN_SUMMARY_PROMPT = """\
working-memory store 中的某個使用者任務已跨多個 sessions 推進。下面是該任務進展鏈中的
session summaries，依從最早到最新排列，最後一條是目前的 session。

總結同一個任務如何跨 sessions 推進到目前狀態。依時間順序涵蓋每個重要階段。對每個
階段，在有資訊時保留：實際的 action 或 change、其 direct result，以及任何 unresolved
issue 或 limiting condition。最後明確寫出目前狀態。

規則：
  - 這只是壓縮。只能報告 entries 實際陳述的內容；絕不要新增或補全 command、cause、
    motive、action、test result、success state 或 global negative conclusion。
  - 精確保留證據狀態：attempted 不等於 completed，observed 不等於 verified，inferred
    不等於 established，unresolved 必須保持 unresolved。保留 uncertainty words 以及
    search/permission/coverage limits。
  - 忠實陳述目前的結束狀態（completed、partially completed、failed、blocked 或
    still awaiting verification）。絕不要因為某個看似合理的因果故事，或後續 entry
    並未驗證的內容而提升狀態。
  - 如果 entries 提到 suspected cause 和後續 result，只有某條 entry 直接說明該 cause
    已被 verified 時，才可在兩者之間建立因果關聯。
  - 保留具體的 retrieval anchors：file paths、symbols、commands、error strings、versions、
    config keys/values，以及對進展重要的 numeric values。
  - 對 2-3 代的 chains，不要把進展過度壓縮成一句話。適合時優先使用 2-4 個緊湊段落，
    或使用可讀性良好的 prose，清楚呈現 task goal、逐階段的 progression 和 current status。
  - 不要機械地複製完整 history。在保留重要 actions、direct results 和 leftovers 的同時，
    省略重複及低價值的細節。
  - 不要杜撰 stage headings 或 dates。只有 entries 使日期具有實際意義時才提及日期；
    單獨的 timestamps 是雜訊。
  - 如果 entries 說明較早的 revisions 已被省略，必須明確指出 earlier history was omitted；
    不要暗示所提供的 entries 是完整的歷史。
  - 使用與 entries 相同的語言。
  - 不要寫前言，也不要用引號包住輸出。

範例
  entries:
    (2026-07-20) 需要修復 CI 的 npm run build；已追查到 Node 18 下的
                 ERR_OSSL_EVP_UNSUPPORTED，workflow 尚未修改。
    (2026-07-24) 繼續修復同一個 CI 問題；已將 .github/workflows/ci.yml 中的 Node
                 升級到 20，但 dependency cache 仍重現舊錯誤。
    (now)        繼續驗證同一修復；清除 npm cache 後，npm run build 通過。
  output:
    任務目標是修復 CI 的 npm run build。最初將問題追查到 Node 18 下的
    ERR_OSSL_EVP_UNSUPPORTED，當時尚未修改 workflow；隨後將
    .github/workflows/ci.yml 升級到 Node 20，但 dependency cache 仍使舊錯誤繼續
    出現。最後，清除 npm cache 後 npm run build 通過，因此該 build 修復目前已驗證完成。

═══════════════════════════════════════════════════════════════════
ENTRIES
═══════════════════════════════════════════════════════════════════
{chain_block}

═══════════════════════════════════════════════════════════════════
OUTPUT（只輸出 summary 本身，不要其他內容）
═══════════════════════════════════════════════════════════════════
"""


# ================================================================
# Episode / topic summaries
# ================================================================

EPISODE_SUMMARY_PROMPT = """你在為**一段討論**（一個 episode）做總結。

一個 episode 是一段連續的討論 —— 圍繞一件事，可能跨越好幾次寫入。

## 這段討論中擷取到的記憶（依時間正序）

{memories}

---

寫一段關於這次討論內容的高密度摘要。

要求：

* 2–4 句，第三人稱，以"使用者…"開頭（若記憶講的是別人，則以那個人的名字開頭）。
* 把這些記憶串成一個連貫的敘述 —— 一段討論通常有一條主線（一個計畫、一個問題、
  一次出遊、一個決定）。
* 保留具體細節：人名、地點、數字、日期。
* 絕不編造記憶裡沒有的內容。
* 不要逐條羅列記憶，要綜合成一段。
* 輸出語言與記憶的語言一致。

只回傳摘要正文，不要任何前言、不要 JSON、不要引號。"""

TOPIC_SUMMARY_PROMPT = """你在為**一個主題**總結已知的全部資訊。

## 主題

{topic_name}

## 該主題下的記憶（依時間正序）

{memories}

---

寫一段關於使用者在這個主題上的高密度摘要。

要求：

* 2–4 句，第三人稱，以"使用者…"開頭（若記憶講的是別人，則以那個人的名字開頭）。
* 涵蓋整個主題 —— 說明目前的狀況；只在變化本身重要時才提它是怎麼演變過來的。
* 保留具體細節：人名、地點、數字、日期。
* 絕不編造記憶裡沒有的內容。
* 不要逐條羅列記憶，要綜合成一段。
* 輸出語言與記憶的語言一致。

只回傳摘要正文，不要任何前言、不要 JSON、不要引號。"""


# ================================================================
# System 2 evolve (pipelines/system2_agent.py)
# ================================================================

SYSTEM2_AGENT_PROMPT = """你是一個認知處理 Agent，負責從使用者的記憶資料中演化出高層次的認知結構。

## 記憶分層架構

使用者的記憶儲存在兩個系統中：

### VDB（向量資料庫）— 你只能讀取，不能修改
| 層級 | 說明 |
|------|------|
| L0 BASIC_INFO | 使用者基本屬性（姓名、年齡、職業等）|
| L1 RAW | 原始對話記錄 |
| L2 FACT | 原子事實（從對話中擷取的客觀事實）|
| L3 SUMMARY | 會話摘要 |
| L4 IDENTITY | 身分畫像（性格特徵、偏好、習慣）|
| L5 KNOWLEDGE | 領域知識 |

### Graph（知識圖譜）— 你是唯一的寫入者
| 層級 | 說明 |
|------|------|
| L6 SCHEMA | 行為模式 — 領域內、原子化、建立後不可變 |

## L6 Schema 是什麼？

Schema 是從多次獨立經歷歸納出的長期行為模式，包含以下三個要素：

- **Circumstance（情境）**：該模式發生的領域／話題／情境。Schema 必須限定在其情境內，不要跨領域泛化。
- **Pattern（模式）**：使用者在該情境下的慣常行為、思考方式或行動傾向。
- **Insight（洞察）**：底層的心理驅動力或心智模型。

### 內容格式
行為模式用一句話組合三要素：
"當[情境]時，使用者[模式]——反映了[洞察]。"

對與情緒、治療、壓力、哀傷或創傷有關的長期模式，不要把具體的人生背景壓縮成
空泛的人格詞。優先保留這三部分：
"Trigger/background（持久的觸發背景） → emotional response（反覆出現的情緒反應） → observed coping resource（使用者已實際使用的因應資源）。"
只能寫入 user-owned 證據中出現的觸發背景、反應和因應方式；助理的建議不是使用者已被觀察到的因應資源。

### 規則
- **原子化**：一個 Schema 只包含一個模式。兩個不同的模式 → 兩個 Schema。
- **不可變**：Schema 建立後內容永遠不變。不要修改既有 Schema 的內容。
- **累積證據**：新事實只是在支持既有表述 → 呼叫 `add_evidence`。如果新證據會讓舊畫像的目前狀態產生實質遺漏、錯誤或誤導，使用 `revise_schema` 建立新版本，讓舊版本保留以供稽核，但退出 active 檢索。
- **領域內約束**：不要跨領域。跨領域的抽象由系統的其他流程處理。
- **事實與模式分層**：單條明確的身分、健康狀態、裝置使用、隸屬關係、專案或偏好屬於 L2 狀態，不建立 L6。L6 必須由至少兩個獨立 raw/session 中的 user-owned 證據支持。
- **主體安全**：同一次原始寫入可能包含他人的資料或範例。只有材料明確歸屬於目前使用者時才建立使用者 Schema；否則對該材料不執行任何操作。
- **候選涵蓋**：source-context 審查時，每條候選事實必須二選一：作為 `create_schema`/`add_evidence` 的 evidence，或用 `skip_fact` 明確跳過。原始上下文只用於確認主體或補回該事實遺漏的細節，不得據此建立無關的 Schema。
- **證據門檻**：建立 L6 必須有 user-owned evidence，且這些證據合計代表至少兩個獨立的 raw/session。
  一個被 S1 合併的 L2 事實可以代表多次獨立寫入，應檢查它的 `source_raw_memory_ids`，不要只數節點。
  助理的建議不能作為使用者模式的獨立證據。

## 工作流程

輸入材料分兩部分（僅在已形成聚類時才會呼叫你）：
- **聚類結果（Cluster Results）**：主要輸入，依語義相似度分組的事實
- **未聚類事實（補充）**：未進入任一聚類的事實，優先順序低於聚類結果，酌情審視即可

對於每組事實（無論是否聚類）：
1. **搜尋既有 Schema**：如果既有 Schema 已涵蓋該模式，使用 `add_evidence`。只有加入新證據後舊表述會產生實質遺漏、錯誤或誤導時才使用 `revise_schema`。
2. **建立新 Schema**：僅在沒有既有 Schema 涵蓋時。一句話，三要素。
3. **建立關係**：兩個 Schema 主題相關 → 使用 `add_edge`（RELATED_TO）。

## 原則

- 所有 Graph 操作必須透過 tools 執行
- Schema 內容建立後不可變——絕不重新建立已存在的 Schema
- 優先使用 `add_evidence`，而非建立重複的節點
- 寧可不建立，也不要建立低品質的節點
- 一個 Schema = 一個領域內的原子模式
- 加標籤時優先使用既有標籤清單中的標籤"""


# ================================================================
# Fragments assembled in code
# ================================================================

LANGUAGE_NAME = "繁體中文"

# Appended to every memory-writing prompt ONLY when the caller set the
# language explicitly (per call or MemoryConfig.language). Auto-detected
# requests never see it, so their prompts stay exactly as before.
OUTPUT_LANGUAGE_DIRECTIVE = """

---

# 輸出語言

你輸出的所有自然語言內容（記憶正文、摘要、理由、查詢）一律以繁體中文書寫，無論對話或既有記憶使用什麼語言。此規則優先於上文任何「輸出語言與輸入語言一致」的要求。JSON 鍵、欄位名稱、列舉值、操作名稱、ID、程式碼、檔案路徑、指令、錯誤訊息和專有名詞保持原樣。
"""

# enable_intention=False: the "two kinds of memory" section of
# EXTRACT_SYSTEM_PROMPT is replaced by a single-bucket section. The pattern
# must match this language's EXTRACT_SYSTEM_PROMPT.
NO_INTENTION_SECTION_PATTERN = r"# 兩類記憶.*?(?=\n# OWNER)"

NO_INTENTION_MEMORY_SECTION = (
    "# 記憶\n"
    "\n"
    "你只擷取一類記憶：**memory（記憶）**。\n"
    "\n"
    "## memory（記憶）\n"
    "\n"
    "一切值得長期記住的資訊都放這裡——既包括使用者本人的，也包括對話中其他具名真人的：\n"
    "\n"
    "* 偏好、觀點、態度、價值觀\n"
    "* 事件、行動、經歷\n"
    "* 習慣、行為模式\n"
    "* 關係、穩定特質\n"
    "* 客觀事實、決策、限制條件\n"
    "* 正在進行的活動\n"
    "* **未來計畫／即將發生的安排**（若值得記住，直接寫進 `memory`；"
    "不要另開 intentions 桶）\n"
    "\n"
    "如果使用者隱含地表達偏好，也應擷取。\n"
    "\n"
    "不要把\"使用者是怎樣的人\"和\"發生了什麼\"分開。兩者都放進 `memory`。\n"
    "\n"
    "---\n"
    "\n"
)

# (old, new) pairs applied in order after the section swap.
NO_INTENTION_REPLACEMENTS = (
    (
        "每條 memory 和 intention 都必須帶 `owner`",
        "每條 memory 都必須帶 `owner`",
    ),
)

INTENTION_DISABLED_NOTE = (
    "\n\n---\n\n# INTENTION 已關閉\n"
    "目前設定已關閉 intention 擷取。即使範例裡出現 `intentions`，"
    "也**不要**輸出該欄位；未來計畫寫入 `memory`。\n"
)

# Coding / working extractor: wraps the history and the new messages.
CODING_CONVERSATION_BLOCK = (
    "最近 5 個使用者輪次（僅作上下文，不要重複擷取）：\n"
    "{history}"
    "\n\n本次新增訊息（只從這次完整寫入中擷取）：\n"
    "{new}"
)
CODING_EMPTY_BLOCK = "（無）"

# Name line at the top of a multi-person conversation. Also stored in L1_RAW,
# so pipelines.base parses every language's form back.
USER_NAME_LINE = "使用者的名字是{name}"

# System 2 single-call mode: output contract appended to SYSTEM2_AGENT_PROMPT.
SYSTEM2_OUTPUT_INSTRUCTIONS = """

## 輸出格式

不要呼叫 tools。直接輸出一個 JSON 陣列，包含你決定執行的操作。用 ```json 程式碼區塊包住。

可用操作：

1. **create_schema** — 建立新的 L6 行為 Schema
   ```{"op": "create_schema", "schema_kind": "behavior_pattern", "content": "供使用者閱讀的簡潔畫像", "retrieval_text": "用於檢索的行為與適用情境", "evidence_list": ["fact_node_id_1", "fact_node_id_2"], "tags": ["tag1"]}```

2. **add_evidence** — 為既有 Schema 追加證據（不修改內容）
   ```{"op": "add_evidence", "node_id": "existing_schema_node_id", "evidence_list": ["new_fact_id_1"]}```

3. **revise_schema** — 新證據使既有 Schema 的目前狀態發生實質變化時，建立新版本並讓舊版本退出預設檢索。不要用於可共存的細節補充。
   ```{"op": "revise_schema", "node_id": "old_schema_id", "schema_kind": "explicit_state", "content": "更新後的目前畫像", "retrieval_text": "更新後的檢索文字", "evidence_list": ["new_fact_id_1"], "tags": ["tag1"], "reason": "明確說明舊畫像為何會誤導"}```

4. **add_edge** — 建立兩個 Schema 之間的關係
   ```{"op": "add_edge", "source_id": "schema_id_1", "target_id": "schema_id_2", "reason": "關係原因"}```

5. **skip_fact** — 明確標記某條候選事實已審查但不建立 Schema。僅用於他人的事實、臨時事件、無法證明使用者特徵的助理建議，或已被既有 Schema 完整涵蓋的事實。
   ```{"op": "skip_fact", "node_id": "candidate_fact_id", "reason": "不是穩定的使用者模式"}```

## 輸出約定

1. 輸出必須是 JSON 陣列，用 ```json 程式碼區塊包住。
2. 如果資料不足以得出可靠結論，輸出 `[]`。在 source-context review 中，
   `[]` 表示已審查該組全部候選，且都不足以支持穩定的 Schema。
3. 程式碼區塊外不要有任何其他文字。
4. evidence_list 中的 id 必須來自材料中提供的 fact node_id。
5. add_evidence / revise_schema 的 node_id 必須來自既有 Graph 節點的 id。
6. 對 source-context 審查，必須讓每條候選事實出現在 evidence_list 或 skip_fact 中。"""

# Appended to SYSTEM2_OUTPUT_INSTRUCTIONS when the schema evidence gate is on.
SYSTEM2_SCHEMA_EVIDENCE_GATE = """

7. L6 只表示跨事件的行為模式。create_schema 必須使用 user-owned 證據，且證據合計代表至少兩個獨立的 raw/session。當 `source_raw_memory_ids` 包含多次獨立寫入時，一個合併後的 L2 事實也可以滿足門檻。
8. 單條明確的身分、健康狀態、裝置使用、隸屬關係、專案或偏好不要建立 L6；這些由 canonical L2 狀態通道維護。"""

# (old, new) pairs that turn the tool-calling wording of SYSTEM2_AGENT_PROMPT
# into single-call wording. Each `old` must occur in this language's prompt.
SYSTEM2_SINGLE_CALL_REPLACEMENTS = (
    ("- 所有 Graph 操作必須透過 tools 執行\n", ""),
    ("呼叫 `add_evidence`", "使用 add_evidence 操作"),
    ("使用 `add_edge`（RELATED_TO）", "使用 add_edge 操作"),
)

# System 2 user message (materials). Keys are fixed; values are format
# strings with the named fields shown.
SYSTEM2_MATERIALS = {
    "title": "## 預處理材料\n",
    "stats": (
        "### 統計\n- VDB 事實總數: {total}\n"
        "- 新鮮事實（待處理）: {fresh}（已被引用而排除={evidenced}）\n"
        "- 聚類數: {clusters}\n"
        "- 聚類內事實: {clustered}，未聚類補充: {unclustered}\n"
        "- Graph Schema 總數: {graph_total}（本次召回={recalled}）"
    ),
    "graph_status_empty": "- Graph 狀態: **空**（首次認知處理）\n",
    "clusters_header": "### 聚類結果\n",
    "label_holistic": "{n} 條跨會話的使用者事實；只歸納有證據支持的長期情緒／治療模式",
    "label_source_context": "{n} 條候選事實；原始寫入僅用於主體與細節核實",
    "label_topic": "{n} 條，主題",
    "raw_label": "候選事實對應的原始片段",
    "holistic_note": (
        "這是一次有邊界的夜間全域整理。最多建立 "
        "{max_schemas} 條 anchored therapy Schema。"
        "優先聯合不同 raw/session 中的持久 trigger/background、"
        "反覆出現的 emotional response 和使用者已實際使用的 coping resource/decision constraint。"
        "證據不足時輸出 []。\n"
    ),
    "companion_header": "**獨立來源 companion 提示（只是候選，不是結論）:**",
    "unclustered_header": "### 未聚類事實（補充，次要）\n",
    "unclustered_hint": "以下事實未進入上述任一聚類，可在完成聚類主題處理後酌情審視；優先順序低於聚類結果。\n",
    "forward_header": "### 相關 Graph 節點（透過 cluster centroid 向量召回，共 {count} 條）\n",
    "graph_empty": "### 既有 Graph 節點\n目前 Graph 為空，這是首次認知處理。無需搜尋 Graph，直接根據聚類結果建立 Schema。\n",
    "graph_no_recall": (
        "### 既有 Graph 節點\n"
        "Graph 中共有 {graph_total} 個 Schema，"
        "但 centroid 向量召回未命中相關節點。請根據聚類結果判斷是否需要建立新的 Schema。\n"
    ),
    "reverse_header": "### Graph 反向引用（既有 Schema 引用的 VDB 事實）\n",
    "tags_header": "### 既有標籤（請優先從中選擇，避免建立同義標籤）\n",
    "closing": (
        "請分析以上材料，輸出操作 JSON 陣列。"
        "決定是否需要建立新的 Schema，或為既有 Schema 追加 evidence。"
        "如果資料不足以得出可靠結論，輸出空陣列 `[]`。"
        "加標籤時請優先使用既有標籤清單中的標籤。"
    ),
}
