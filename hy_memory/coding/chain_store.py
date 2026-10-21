#!/usr/bin/env python3
"""
hy_code_mem —— 通用 coding memory 插件。给向量记忆层挂上「同一处代码被改了几次、每次改了什么」。

════════════════════════════════════════════════════════════════════════════
它是装饰器, 不是检索器
════════════════════════════════════════════════════════════════════════════
向量层(hy_mem / 任何 RAG)负责「给哪几条 fact」—— top-k、阈值、排序全归它。
本模块只负责「每条 fact 后面跟什么」: 把那条 fact 讲的那处代码, 在这条分支上的
**全部改动版本**(每版的用户原话 + 每版的真实 diff)按顺序挂在它下面。

不做相似度、不做 top-k、不选版本、不猜哪版是最终答案。判定权交给指令本身。

════════════════════════════════════════════════════════════════════════════
为什么必须存 diff, 而不是把 fact 写详细点
════════════════════════════════════════════════════════════════════════════
向量层的抽取 prompt 是**明文要求砍代码**的(hy_memory/coding/extractor.py):
    "A record that omits the branch but quotes 30 lines of diff is a bad trade."
    "EXACT COORDINATES, NOT PASTED CODE" / 硬上限 ~200 汉字
因此 fact 往往不会记下被放弃的版本, 也很少带代码片段。

这个取舍在通用场景下是**对的** —— 200 字预算里 `feat/x` 这一个 token 的价值远超 30 行
diff, 而代码"可以回去读"。但它假设的那个前提有个洞: **被撤掉那版的代码读不回来** ——
它不在工作区、不在 HEAD, 只在某次中间提交里。

所以本模块不跟那 200 字抢: 抽取 prompt 一个字不改, 这里零 LLM 地把每版真实 patch 存下来。
补的不是"抽取质量", 是 LLM 摘要必然丢掉的那一类信息。

════════════════════════════════════════════════════════════════════════════
两张表
════════════════════════════════════════════════════════════════════════════
    thread(id, owner, branch, file, symbol, lo, hi, n_rounds, updated_at)
        一条 thread = 某分支某文件某函数的某一块, 被反复改的历史
        owner = 隔离键("<user_id>|<workspace_id>")。**必须进键** —— 没有它时
        用户 A 的 feat/q4 和 B 的 feat/q4 会并成同一条链, A 能读到 B 的 diff。
        空串 = 未指定, 且只匹配空串(不是公共池), 所以单用户的老库行为不变。
    round(id, thread_id, seq, said, patch, adds, created_at)
        一条 round = 一次改动。seq = 第几版
    repo_symbol(name, path, line, kind)
        全仓 AST 索引。只用于"指令点名的这个函数在哪个文件", 不参与分链

`seq` 是 **observe() 第几次被调用**, 纯计数 —— 不做 diff 之间的比对、不推断谁覆盖谁,
所以不存在猜错的可能。第 1 版就是第一次调用, 物理事实。

thread 键三级降级。以下三档**都在 owner 之内**:
    ① hunk header 带 func/def/class 名  → (owner, branch, file, symbol, 行区间)
    ② 只有上下文文字, 无函数名          → (owner, branch, file, 行区间)
    ③ 裸 hunk, 只有行号                  → (owner, branch, file)
箭头函数/对象方法等写法 git 默认 hunk header 认不出函数名, 会落到 ②。② 用行区间聚类而不是直接降到文件粒度 —— 否则同一文件的
所有改动会挤成一条十几版的长链, 分不清哪版对应哪件事。

**不用** repo_symbol 按行号反查符号来补 ②: 它会系统性偏一个符号,
因为 patch 里的行号是改后的、而索引是改前建的。hunk header 才是对的。
"""
import os
import re
import json
import time
import sqlite3
import subprocess
import collections
import difflib

# 注入块的首行标记 —— 宿主靠它认出"这是个多行结构块, 别塞进 `- {r}` 的 bullet 位"
BLOCK_MARK = "【工作上下文】"

# 预算
MAX_CODE_LINES = int(os.environ.get("HCM_CODE_LINES", "6"))    # 每版显示几行 +
MAX_CODE_CHARS = int(os.environ.get("HCM_CODE_CHARS", "480"))   # 每版代码字符上限
# 每版原话字符上限。同一条链上各版原话的开头往往相同, 真正区分版本的词常在后段,
# 截得太短会让模型认不出哪版是哪版。
# ⚠️ 必须与 MAX_BLOCK_CHARS 一起调: 原话放长而整块上限不变, 会让更多块撞上限、
# 反而触发原话压缩。
MAX_SAID_CHARS = int(os.environ.get("HCM_SAID_CHARS", "800"))
MAX_BLOCK_CHARS = int(os.environ.get("HCM_BLOCK_CHARS", "14000"))  # 整块上限
# 整块上限与 BODY_MAX_CHARS 必须一起调, 单独放大 BLOCK 没用: body 过大会把 diff 行挤掉。
# diff 是"哪一版改了什么"的唯一线索, 与 body("完整样子是什么")互补, 缺一不可。
# 也不宜继续加大: 注入过多会让模型信息过载。
LINE_MERGE = int(os.environ.get("HCM_LINE_MERGE", "20"))        # 行区间相邻多少行算同一条
# 一条链最多几版带代码完整展示。超了就只留序号+原话摘要(序号绝不省, 序数指代要靠它数)
SHOW_ALL = int(os.environ.get("HCM_SHOW_ALL", "8"))
# 一条 fact 最多挂几条链(按与指令的 token 重叠排序后截断)。
# 链挂太多会撞整块上限、把各版原话压到认不出; 原话是这个机制的核心信息,
# 宁可少挂一条链也不把它压短。
# 0 = 不截。
TOPN_THREADS = int(os.environ.get("HCM_TOPN_THREADS", "3"))
# 最多给**几条 fact**挂链。其余 fact 不挂链, 照常留在 bullet 列表里(信息不丢, 只是不带 diff)。
# 块大小近似线性于挂链的 fact 数, 挂多了会超出整块上限并导致过载。
# 目标点的链只需要挂一次, 多挂的那几条 fact 往往是**同文件其他点**的链 —— 那才是噪声。
# 且 _rank_threads 已按指令 token 排序, 第一条 fact 命中目标链的概率最高。
TOPN_FACTS = int(os.environ.get("HCM_TOPN_FACTS", "1"))
# 链末尾"最后一版完整代码"的字符上限。注入过大会过载, 所以给单条链设个天花板。
BODY_MAX_CHARS = int(os.environ.get("HCM_BODY_CHARS", "900"))

_HUNK = re.compile(r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@[ \t]*(.*)$', re.M)
_SYM = re.compile(r'(?:func|def|class|function)\s+(?:\([^)]*\)\s*)?([A-Za-z_$][\w$]*)')
# 从对话里认 agent 的"整段替换"写法(_patch_from_edits 用)。这是空 commit 轮次里
# 常见的编辑形态: 一段 Python, 先 open(path) 读、再 s.replace(old, new)、最后写回。
# path/old/new 三个都要抓到才用, 抓不全就跳过 —— 抽错比抽不到更坏。
# 合成 diff 的标记: 放在 @@ 行的上下文位(_SYM 抓符号靠正则, 多个词不影响),
# observe 靠它认出"这段行号是假的" → 按 symbol 归并而不是按行区间。
# 样板行: 每一版都长一样, 对"这是哪一版"零信息(_pick_lines 用)。
# 函数/类头、控制流关键字、单独的括号、import/package。**不含**赋值和 return ——
# 那些才是版本之间真正的差别所在。
_BOILER = re.compile(
    r'^(?:\}|\{|\)|\(|else\b|\}\s*else|end\b'
    r'|(?:func|def|class|function|type|interface|impl)\b'
    r'|(?:for|while|if|switch|case|try|except|catch|with|elif|do)\b'
    r'|(?:import|from|package|use|include|#include)\b'
    r'|(?:continue|break|pass)\s*$'
    r')')
_SYNTH_MARK = "[synth-from-dialog]"
# ── 合成编辑的失败闸门 ───────────────────────────────────────────────────────
# _patch_from_edits 原来只要正则抓到 path/old/new 三段就当成"改动发生了", 不看它
# **有没有成功**。两类误判:
#   · agent 只是**提议**改法(还在问"要我执行吗"), 用户回"这个方案不对, 别改" → 照样存
#   · 编辑**失败**(AssertionError: old not in s) → 失败的那版也进了链
# 后果不对称: 链是"哪一版赢了"的唯一依据, 掺一个没生效的版本进去, agent 可能照着写。
# 这与 _patch_from_edits 自己的原则("抽错比抽不到更坏")矛盾 —— 补上。
#
# 判据必须区分大小写，否则 `failed` 一类源码标识符会被误判为编辑失败。
_EDIT_FAIL = re.compile(
    r'(?m)^\s*(?:AssertionError|IndentationError|SyntaxError'
    r'|FileNotFoundError|KeyError|ValueError)\b'
    r'|Traceback \(most recent call last\)'
    r'|\bold not in\b|substring not found'
    r'|returncode>[1-9]')
# 成功迹象必须一起看：编辑可能先报错、随后重试成功。
_EDIT_OK = re.compile(r'returncode>0<|REPLACED\s*=\s*1')
# 只扫描编辑动作后的有界窗口，避免把下一轮输出误归到本轮。
_EDIT_TAIL_WIN = int(os.environ.get("HCM_EDIT_TAIL_WIN", "600"))
_EDIT_PY = re.compile(
    r'path\s*=\s*["\'](?P<path>[^"\']+)["\'].*?'
    r'old\s*=\s*(?P<q1>"""|\'\'\')(?P<old>.*?)(?P=q1).*?'
    r'new\s*=\s*(?P<q2>"""|\'\'\')(?P<new>.*?)(?P=q2)',
    re.S)
_BR = re.compile(r'\b((?:feat|fix|feature|chore|refactor)/[A-Za-z0-9._\-]+)')
_USER = re.compile(r'(?ms)^USER:[ \t]*(.*?)(?=^(?:ASSISTANT|OBSERVATION|SUBMITTED):|\Z)')
# 兼容小写 `user:` / `assistant:` 及 `[tool:...]` / `[result:...]` 工具标记。
_USER_LC = re.compile(
    r'(?ms)^user:[ \t]*(.*?)(?=^(?:assistant|system|observation):|^\[(?:tool|result)[:\s]|\Z)')
# 宿主给指令加的固定前缀, 不是用户在说的内容
_SHELL = [
    re.compile(r'^\s*(?:user|human)\s*:\s*', re.I),
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS thread(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner TEXT NOT NULL DEFAULT '',
  branch TEXT NOT NULL, file TEXT NOT NULL, symbol TEXT NOT NULL DEFAULT '',
  pkey TEXT NOT NULL DEFAULT '',
  lo INTEGER NOT NULL DEFAULT 0, hi INTEGER NOT NULL DEFAULT 0,
  n_rounds INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL DEFAULT 0,
  body TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS ix_thread_bf ON thread(owner, branch, file, pkey);
CREATE TABLE IF NOT EXISTS round(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  thread_id INTEGER NOT NULL, seq INTEGER NOT NULL,
  said TEXT NOT NULL DEFAULT '', patch TEXT NOT NULL DEFAULT '',
  adds TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_round_t ON round(thread_id, seq);
CREATE TABLE IF NOT EXISTS repo_symbol(
  name TEXT NOT NULL, path TEXT NOT NULL, line INTEGER NOT NULL DEFAULT 0,
  kind TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS ix_sym_name ON repo_symbol(name);
"""


def _clean(s):
    return " ".join((s or "").split())


def _strip_shell(s):
    """去掉宿主套在指令外面的固定前缀, 只留用户真正说的内容。"""
    out = s or ""
    for p in _SHELL:
        out = p.sub("", out)
    return _clean(out)


class HyCodeMem:
    """一个 workspace(通常 = 一个仓)一个实例。observe() 写, recall() 读。"""

    def __init__(self, store_dir, repo_root=None, owner=""):
        os.makedirs(store_dir, exist_ok=True)
        self.dir = store_dir
        self.repo = repo_root
        # owner = 隔离键。thread 的业务键本身没有用户维度，不加 owner 会让不同
        # 用户/工作区的同名 branch/file/pkey 串成同一条 thread，造成数据泄漏。
        # 空串是"未指定"而不是"公共池": 空 owner 只匹配空 owner(见 _own 的 WHERE),
        # 因此旧的空 owner 数据不会被有 owner 的新写入污染。
        self.owner = str(owner or "")
        self.db = sqlite3.connect(os.path.join(store_dir, "hy_code.db"),
                                  timeout=30, check_same_thread=False)
        self.db.executescript(_SCHEMA)
        # ── 老库迁移: 补 thread.pkey ────────────────────────────────────────────
        # CREATE TABLE IF NOT EXISTS 对**已存在**的表一个字都不改, 所以较早版本
        # 灌的库(键 = branch,file,symbol)打开后表里没有 pkey 列, 而 _upsert_thread 的
        # SELECT 带了 `AND pkey=?` → `OperationalError: no such column: pkey`。
        # ⚠️ 这个崩溃**只在真 diff 上出现**: 假 diff 被 _get_patch 挡掉就直接 return 0,
        # 压根走不到那条 SELECT。
        # 迁移后老库里的行 pkey='' , 于是它们仍归并成一条链(与灌它们时的行为一致);
        # 要拿到拆分后的链必须重新导入；迁移只保证旧库可读。
        _cols = {r[1] for r in self.db.execute("PRAGMA table_info(thread)")}
        if "pkey" not in _cols:
            self.db.execute("ALTER TABLE thread ADD COLUMN pkey TEXT NOT NULL DEFAULT ''")
        # body: 该链最后一版的完整代码上下文。老库里为空 —— 块里就退回
        # "只有 diff"的老行为, 不崩; 要拿到 body 必须重灌。
        if "body" not in _cols:
            self.db.execute("ALTER TABLE thread ADD COLUMN body TEXT NOT NULL DEFAULT ''")
        # owner: 通用隔离键。旧库里为 ''，与不传 owner 的调用方匹配。
        # 同上, 不重灌拿不到隔离; 迁移只保证老库能打开、且不会被别人的数据串进来。
        if "owner" not in _cols:
            self.db.execute("ALTER TABLE thread ADD COLUMN owner TEXT NOT NULL DEFAULT ''")
        # 索引重建放在所有 ALTER 之后 —— 它引用 owner/pkey 两列, 列还没加上时会报
        # `no such column`。老库上这个 CREATE INDEX 是幂等的(IF NOT EXISTS), 但旧名
        # 的索引可能只覆盖 (branch,file,pkey), 所以先 DROP 掉同名的再建。
        self.db.execute("DROP INDEX IF EXISTS ix_thread_bf")
        self.db.execute("CREATE INDEX IF NOT EXISTS ix_thread_bf "
                        "ON thread(owner, branch, file, pkey)")
        self.db.commit()
        self._commit_cache = None

    # ────────────────────────────── 写入 ──────────────────────────────

    def observe(self, mem_text, meta=None):
        """agent 每改完一轮调一次。零 LLM、零 embedding。

        meta 可给 branch / file / commit / kind / key / seg —— 都是可选的加速项:
        branch 给了就不猜, commit 给了就不反查。全不给也能跑。
        """
        meta = meta or {}
        text = mem_text or ""
        if not text.strip():
            return 0
        if not self._pass_gate(text, meta):
            return 0

        branch = meta.get("branch") or self._guess_branch(text)
        patch = self._get_patch(text, meta)
        if not patch:
            return 0
        said = self._get_said(text)

        n = 0
        for file, symbol, lo, hi, hunk in self._split_hunks(patch):
            if meta.get("file") and file != meta["file"] and not meta.get("_multi"):
                pass  # meta.file 只是提示, 不做过滤 —— 一轮可能真的改了多个文件
            # 合成 diff(_patch_from_edits)的行号是假的(真实行号无从得知, 一律写 1)。
            # 直接交给 _upsert_thread 会因"区间不重叠"给同一个符号另起一条链 ——
            # 合成 diff 的行号不可信，因此按 symbol 归并。pkey 还必须进入 thread 键，
            # 否则同一文件/符号上的多个独立决策点会串成一条链，版本序数失去语义。
            tid = self._upsert_thread(branch, file, symbol, lo, hi,
                                      by_symbol=_SYNTH_MARK in hunk,
                                      pkey=meta.get("pkey") or "")
            self._add_round(tid, said, hunk, at=self._round_time(meta))
            # body: 这一轮改完后那段代码的完整样子(含未被改动的行)。每轮覆写 →
            # 灌完留下的是**最后一版**的全文, 正是 _body_at 需要的那一版。
            # 取不到就不动(老 body 保留 / 一直为空), 绝不因为它失败而丢掉这一轮。
            if self.BODY_CTX > 0 and _SYNTH_MARK not in hunk:
                # 合成 diff 的行号是假的(一律写 1), 拿它取上下文会取到文件头 —— 跳过。
                _c = meta.get("commit") or self._commit_for(
                    str(meta.get("key") or meta.get("pkey") or ""), meta.get("seg"))
                _b = self._body_at(_c, file, lo, hi)
                if _b:
                    self.db.execute("UPDATE thread SET body=? WHERE id=?", (_b, tid))
            n += 1
        if n:
            self.db.commit()
        return n

    def _pass_gate(self, text, meta):
        """噪音轮和判分轮不收。

        判分轮(定稿段)必须挡掉 —— 那是被考的那一轮, 收进去等于自己给自己漏答案。
        """
        kind = (meta.get("kind") or "").lower()
        if kind in ("noise", "eval", "final", "s2"):
            return False
        head = text[:24]
        return not (head.startswith("[噪音]") or head.startswith("[定稿"))

    def _guess_branch(self, text):
        # 尾部标点不属于分支名。字符类里含 `.`(分支名真的可能带点, 如 feat/v1.2),
        # 于是 "On branch feat/No16." 会把句号吃进去 → 查库必空。
        # 否则尾部标点会进入查询键，导致精确 branch 查询落空。
        f = [x.rstrip(".,;:!?") for x in _BR.findall(text)]
        f = [x for x in f if "/" in x and not x.endswith("/")]
        return max(set(f), key=f.count) if f else ""

    def _get_said(self, text):
        """只取用户原话。assistant 的复述是模型自己编的, 原话才是事实。

        大小写两套都要试；只认一种会匹配失败并退回 text[:400]，把 ingest 模板头
        错当成用户原话。
        """
        m = _USER.search(text) or _USER_LC.search(text)
        return _strip_shell(m.group(1) if m else text[:400])

    # ---- patch: 两条路 ----

    # diff 到此为止的标记 —— 宿主对话里 diff 后面常紧跟 agent 的下一步动作。
    # diff 后面可能紧跟 assistant/tool/result 对话文本；这些尾巴会：
    #   · 让 _hunks 把对话行当成 hunk 内容, 行号/symbol 归属跟着错
    #   · 白占 patch 存储和 _core_diff 的计算
    # 所以截到第一个对话标记为止。
    # 大小写两套角色标记和工具标记都要识别，否则整段后续对话会被误存为 patch。
    _DIFF_END = re.compile(
        r'^(?:ASSISTANT:|USER:|OBSERVATION:|SUBMITTED\s*$|```mswea|COMPLETE_TASK_AND_SUBMIT'
        r'|assistant:|user:|system:|\[tool:|\[result:)',
        re.M)

    def _get_patch(self, text, meta):
        """① 文本里直接带 diff → 用它(通用场景的主路径, 改完 diff 就在手边)
           ② 没有 → 从 git 反查(轨迹事后灌库时的主路径)
           ③ git 也拿不到 → 从对话里的**编辑动作**合成(见 _patch_from_edits)
        """
        i = text.find("diff --git")
        if i >= 0:
            body = text[i:]
            m = self._DIFF_END.search(body)
            return body[:m.start()] if m else body
        p = self._patch_from_git(meta)
        if p:
            return p
        return self._patch_from_edits(text)

    def _patch_from_edits(self, text):
        """从对话里 agent 的"写文件"动作合成一段 diff。

        为什么需要这条路: git 反查会落空。很多轮次的 commit 是空的 —— agent 明明写了文件,
        但改动没进那一轮的 commit(例如后续的 `git checkout` 把未提交的改动冲掉)。
        这些轮次一律 observe→0, 于是 thread/round 建不起来, 链上会缺版本, 注入块可能
        只剩错误的旧版。

        能救回的: 空 commit 轮次里常见的是 `old='''…''' new='''…'''` 这种 Python 替换
        或带 `REPLACED=` 的同类变体, old/new 两段代码都在对话里, 信息
        其实比 git diff 更全(连"从什么改成什么"都有)。剩下 sed -i / cat heredoc / 纯口头
        确认那些抽不出来, 不猜 —— 抽错比抽不到更坏。

        合成的 diff 只求能过 _split_hunks: 行号给 1(真实行号无从得知, 而 thread 按
        symbol+区间分链, 同一符号的多版仍会并到一条链上), 符号名放进 @@ 后面的上下文位
        让 _SYM 能抓到。
        """
        out = []
        for m in _EDIT_PY.finditer(text or ""):
            path, old, new = m.group("path"), m.group("old"), m.group("new")
            if not path or old is None or new is None or old == new:
                continue
            if self._edit_failed(text, m.end()):
                continue
            sym = ""
            sm = _SYM.search(new) or _SYM.search(old)
            if sm:
                sym = sm.group(0)          # 整个 "func Xxx" 片段, 让 _SYM 在 @@ 上下文里再抓一次
            else:
                # old/new 片段常是函数**中段**, 抓不到函数头 → 从整段文本里找它所属的函数
                seg = text[:m.start()]
                cands = list(_SYM.finditer(seg))
                sym = cands[-1].group(0) if cands else ""
            ol, nl = old.splitlines(), new.splitlines()
            body = "".join(f"-{x}\n" for x in ol) + "".join(f"+{x}\n" for x in nl)
            out.append(f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
                       f"@@ -1,{max(1,len(ol))} +1,{max(1,len(nl))} @@ {sym} {_SYNTH_MARK}\n{body}")
        return "".join(out)

    @staticmethod
    def _edit_failed(text, pos):
        """这个编辑动作有没有失败 / 是不是只是个没执行的提议。

        pos = 编辑动作正则匹配的结束位置；往后扫 _EDIT_TAIL_WIN 个字符看结果。

        判定：出现失败迹象，**且其后没有成功迹象**，才算失败。
        两个都要看：编辑可能先失败、随后重试成功。只看失败词会误拦真实改动。

        链掺进一个没生效的版本会误导未来 agent，因此宁可漏收也不要误收。
        """
        tail = text[pos:pos + _EDIT_TAIL_WIN]
        fm = _EDIT_FAIL.search(tail)
        if not fm:
            return False
        # 失败词之后还有成功迹象 → 重试成功了，不拦
        return not _EDIT_OK.search(tail[fm.end():])

    def _git(self, *args):
        if not self.repo:
            return ""
        try:
            r = subprocess.run(("git",) + args, cwd=self.repo, capture_output=True,
                               text=True, timeout=30, errors="replace")
            return r.stdout if r.returncode == 0 else ""
        except Exception:
            return ""

    def _patch_from_git(self, meta):
        """按 commit message 反查那一轮的提交。

        宿主每轮改完会提交一次, message 里带该轮的标识(key/seg)。给了 commit 就直接用。
        """
        c = meta.get("commit")
        if not c:
            key, seg = meta.get("key") or meta.get("pkey"), meta.get("seg")
            if not key:
                return ""
            c = self._commit_for(str(key), seg)
        return self._git("show", "--format=", "--unified=3", c) if c else ""

    def _commit_for(self, key, seg):
        """message 含 key(+seg) 的提交, 按时间正序取第 seg 个。

        一次性把 git log 读进内存建索引 —— 每轮单独 `git log --grep` 在 300+ 轮时
        会变成几百次进程启动。
        """
        if self._commit_cache is None:
            self._commit_cache = collections.defaultdict(list)
            out = self._git("log", "--reverse", "--format=%H%x01%s", "--all")
            for ln in out.splitlines():
                if "\x01" not in ln:
                    continue
                h, subj = ln.split("\x01", 1)
                self._commit_cache[subj.strip()].append(h)
                # 也按 message 里出现的 token 建一路(subject 格式可能带前缀)
                for tok in re.findall(r'[\w.\-/#]+', subj):
                    if len(tok) > 3:
                        self._commit_cache[tok].append(h)
        cand = self._commit_cache.get(f"{key}#{seg}") or self._commit_cache.get(key) or []
        if not cand:
            for k, v in self._commit_cache.items():
                if key in k:
                    cand = v
                    break
        if not cand:
            return ""
        i = int(seg) if isinstance(seg, int) or (isinstance(seg, str) and seg.isdigit()) else 0
        return cand[i] if i < len(cand) else cand[-1]

    # ---- 切 hunk, 定 thread 键 ----

    def _split_hunks(self, patch):
        """按 hunk 切开, 每个 hunk 定出 (file, symbol, lo, hi, 该 hunk 的 patch 文本)。"""
        out = []
        for blk in re.split(r'(?m)^diff --git ', patch or ""):
            if not blk.strip():
                continue
            m = re.search(r'^a/(\S+) b/(\S+)', blk)
            file = m.group(2) if m else ""
            if not file:
                m2 = re.search(r'(?m)^\+\+\+ b/(\S+)', blk)
                file = m2.group(1) if m2 else ""
            if not file:
                continue
            hs = list(_HUNK.finditer(blk))
            for i, h in enumerate(hs):
                lo = int(h.group(1))
                hi = lo + int(h.group(2) or 1)
                ctx = h.group(3)
                sm = _SYM.search(ctx)
                symbol = sm.group(1) if sm else ""
                end = hs[i + 1].start() if i + 1 < len(hs) else len(blk)
                out.append((file, symbol, lo, hi, blk[h.start():end]))
        return out

    def _upsert_thread(self, branch, file, symbol, lo, hi, by_symbol=False, pkey=""):
        """找到同一条 thread 就复用, 否则新建。

        同 symbol 内按行区间分链: 区间重叠或相邻 ±LINE_MERGE 行算同一件事。
        改函数签名(函数头)和改函数中段的解析逻辑, 是两件事, 不该混成一条链。

        by_symbol=True: 行号不可信(来自 _patch_from_edits 的合成 diff)→ 只按 symbol 认,
        直接并进该符号已有的链。宁可把"改签名"和"改中段"混成一条, 也不能让版本序号断成两截。

        ⚠️ **symbol 变了但行区间重叠时也必须归并**。
        只按 `symbol=?` 匹配时, symbol 不同就一定新建链 —— 而**新增一个函数**时
        symbol 会在演进中途改变: 第 1 版新函数还不在 repo_symbol 索引里, _symbol_of
        只能归到外层函数; 第 2 版起才归到自己。区间重叠却成了两条链, 而 _pick_versions
        只在单条 thread 内选版本, agent 看到的链就缺了真正的第 1 版。
        所以: 先按 symbol 精确找(优先, 保持原语义), 找不到再在**同 file 同 branch** 里
        按行区间找, 命中就归并并把 symbol 收敛到更具体的那个(非空优先)。
        """
        # ★ pkey 必须进 WHERE。见 observe 里 pkey 那段注释: 不带它时一个文件上的 N 个点
        # 会被串成一条链, 版本序号跨点连续 → 序数指代必错。
        cur = self.db.execute(
            "SELECT id, lo, hi FROM thread"
            " WHERE owner=? AND branch=? AND file=? AND symbol=? AND pkey=?",
            (self.owner, branch, file, symbol, pkey)).fetchall()
        if by_symbol and cur:
            tid, line_start, line_end = cur[0]
            self.db.execute("UPDATE thread SET updated_at=? WHERE id=?", (time.time(), tid))
            return tid
        for tid, line_start, line_end in cur:
            if lo <= line_end + LINE_MERGE and hi >= line_start - LINE_MERGE:
                self.db.execute("UPDATE thread SET lo=?, hi=?, updated_at=? WHERE id=?",
                                (min(line_start, lo), max(line_end, hi), time.time(), tid))
                return tid
        # 跨 symbol 的行区间归并(见上文注释)。只在**行号可信**时做:
        # by_symbol=True 说明 diff 是合成的、行号是假的, 拿假行号去撞别的符号会乱并。
        if not by_symbol:
            for tid, line_start, line_end, sym2 in self.db.execute(
                    "SELECT id, lo, hi, symbol FROM thread"
                    " WHERE owner=? AND branch=? AND file=? AND symbol!=? AND pkey=?",
                    (self.owner, branch, file, symbol, pkey)).fetchall():
                if lo <= line_end + LINE_MERGE and hi >= line_start - LINE_MERGE:
                    # symbol 收敛到**更具体**的那个, 判据是行区间更窄。
                    # 新增函数时第 1 版的 diff 落在外层函数的区间里, 第 2 版起才归到自己。
                    # 名字必须取窄的那个 —— 注入块的链标签 `└ <symbol>` 是 agent
                    # 区分"这条历史属于哪个函数"的唯一线索, 取成外层函数就等于没标。
                    keep = symbol if (hi - lo) < (line_end - line_start) else (sym2 or symbol)
                    self.db.execute(
                        "UPDATE thread SET lo=?, hi=?, symbol=?, updated_at=? WHERE id=?",
                        (min(line_start, lo), max(line_end, hi), keep, time.time(), tid))
                    return tid
        cx = self.db.execute(
            "INSERT INTO thread(owner,branch,file,symbol,pkey,lo,hi,n_rounds,updated_at)"
            " VALUES(?,?,?,?,?,?,?,0,?)",
            (self.owner, branch, file, symbol, pkey, lo, hi, time.time()))
        return cx.lastrowid

    # 该链最后一版的完整代码取多少行上下文。0 = 关掉这个特性(退回只给 diff)。
    # ⚠️ 按 **diff 的行号区间** 取, 不按符号名找函数边界 —— 后者覆盖不全
    # (Go 的方法、JS 的箭头函数、字段声明/常量块都抠不出来), 而行号区间对所有语言都通。
    BODY_CTX = int(os.environ.get("HCM_BODY_CTX", "18"))

    def _body_at(self, commit, file, lo, hi):
        """取 commit 里 file 的 [lo-CTX, hi+CTX] 那段完整代码(含未改动的行)。

        ── 为什么必须有 ─────────────────────────────────────────────────────────
        库里每版只存 diff, 而 diff **只有被改动的行**。"改完后的完整函数"里大半是
        从没被改过、因此从没进过任何一版 diff 的行, 逐版累加 diff 也推不出来。

        只给最后一版的全文, 比给每一版全文更好 —— 每版全文让模型信息过载。
        模型能自己从「最后一版全文 + 各版 diff」反推任意一版, 不需要都摊开。
        """
        if not commit or not file or self.BODY_CTX <= 0:
            return ""
        src = self._git("show", f"{commit}:{file}")
        if not src:
            return ""
        lines = src.splitlines()
        a = max(0, (lo or 1) - 1 - self.BODY_CTX)
        b = min(len(lines), (hi or lo or 1) + self.BODY_CTX)
        if b <= a:
            return ""
        return "\n".join(lines[a:b])

    def _add_round(self, tid, said, patch, at=None):
        """seq = 该 thread 已有 round 数 + 1。纯计数, 不做任何 diff 比对。

        at = 这一轮**真实发生**的时间(不是灌库时间)。事后灌库时 time.time() 全挤在几秒内,
        注入块里就成了一排 "+0秒", 毫无信息量。

        ⚠️ **同一轮的多个 hunk 必须合进同一版, 不能各记一版。**
        一轮对话常同时改一个函数的多处(签名 + 中段 + 返回), _hunks 切出 N 个 hunk,
        原来每个 hunk 走一次 _add_round → 同一句 said 被记成 N 版。
        后果: "第N版"的语义被污染, 注入块里出现一排内容不同、说明词完全相同的版本,
        既挤预算又让 agent 数不清"最开始那版"到底是哪版。
        做法: 同 thread + 同 said 已存在时, 把新 hunk **追加进那一版的 patch**, 不新增 seq。
        """
        row = self.db.execute(
            "SELECT id, seq, patch, adds FROM round WHERE thread_id=? AND said=? ORDER BY seq DESC LIMIT 1",
            (tid, said)).fetchone()
        new_adds = [ln[1:] for ln in patch.split("\n")
                    if ln.startswith("+") and not ln.startswith("+++") and ln[1:].strip()]
        if row:
            rid, seq, old_patch, old_adds_j = row
            merged_patch = (old_patch or "") + ("\n" if old_patch else "") + patch
            old_adds = json.loads(old_adds_j or "[]")
            adds = list(dict.fromkeys([a.strip() for a in old_adds]
                                      + [a.strip() for a in new_adds]))
            self.db.execute("UPDATE round SET patch=?, adds=? WHERE id=?",
                            (merged_patch, json.dumps(adds, ensure_ascii=False), rid))
            return
        seq = self.db.execute("SELECT COUNT(*) FROM round WHERE thread_id=?",
                              (tid,)).fetchone()[0] + 1
        # 去重但保序 —— 同一轮里重复的 return/赋值行白占预算
        adds = list(dict.fromkeys(a.strip() for a in new_adds))
        self.db.execute(
            "INSERT INTO round(thread_id,seq,said,patch,adds,created_at) VALUES(?,?,?,?,?,?)",
            (tid, seq, said, patch, json.dumps(adds, ensure_ascii=False), at or time.time()))
        self.db.execute("UPDATE thread SET n_rounds=?, updated_at=? WHERE id=?",
                        (seq, time.time(), tid))

    def _round_time(self, meta):
        """这一轮真实发生的时间。优先从那一轮的 git commit 取 author date;
        取不到就退回当前时间(实时场景下两者本来就一样)。"""
        c = meta.get("commit")
        if not c:
            key, seg = meta.get("key") or meta.get("pkey"), meta.get("seg")
            c = self._commit_for(str(key), seg) if key else None
        if c:
            out = self._git("show", "-s", "--format=%at", c).strip().splitlines()
            if out:
                try:
                    return float(out[0])
                except ValueError:
                    pass
        return time.time()

    # ---- 全仓符号索引(可选) ----

    def index_repo(self, root=None, exts=(".py", ".go", ".ts", ".js", ".java", ".rb")):
        """建全仓 AST 索引。只用于"指令点名的函数在哪个文件", 不参与分链。"""
        root = root or self.repo
        if not root:
            return 0
        pat = re.compile(
            r'(?m)^[ \t]*(?:export\s+)?(?:async\s+)?'
            r'(?:func|def|class|function|type|interface)\s+'
            r'(?:\([^)]*\)\s*)?([A-Za-z_$][\w$]*)')
        rows, n = [], 0
        for dp, dns, fns in os.walk(root):
            dns[:] = [d for d in dns if not d.startswith(".")
                      and d not in ("node_modules", "vendor", "dist", "build", "__pycache__")]
            for fn in fns:
                if not fn.endswith(exts):
                    continue
                p = os.path.join(dp, fn)
                try:
                    with open(p, "r", errors="replace") as f:
                        src = f.read()
                except Exception:
                    continue
                rel = os.path.relpath(p, root)
                for m in pat.finditer(src):
                    rows.append((m.group(1), rel, src.count("\n", 0, m.start()) + 1, ""))
                    n += 1
        if rows:
            self.db.execute("DELETE FROM repo_symbol")
            self.db.executemany("INSERT INTO repo_symbol VALUES(?,?,?,?)", rows)
            self.db.commit()
        return n

    # ────────────────────────────── 读取 ──────────────────────────────

    def decorate(self, facts, instruction="", scope=None):
        """核心接口: 给向量层选出的 facts 挂上 round。

        facts: [{"statement": str, "branch": str, "files": [str], "status": str}, ...]
                顺序 = 向量层给的顺序, 不重排(它的 top-k 是它的事)。
        scope: 可选的**同仓过滤**。给了就只挂 branch/文件路径属于这个 scope 的 thread。
        返回   (block, used_idx):
                block    = 要放在 bullet 列表**之前**的多行结构块(挂不上任何 round 时为 "")
                used_idx  = 已挂进 block 的 fact 下标; 宿主应把这些从 bullet 列表里去掉,
                            避免同一条 fact 出现两次。

        scope 用于防止共享索引中的跨仓污染。不同仓的语义相似事实可能同时进入
        top-k，但它们的 diff 对当前工作区不可能有用，因此挂链前必须做同仓过滤。
        """
        facts = facts or []
        # 高级消融开关: HCM_BLOCK_OFF=1 → 完全不出块, 也不从 bullet 删任何 fact。
        # 公共 opt-in 见 CodingConfig.chain_block_enabled /
        # MEMORY_CODING_CHAIN_BLOCK_ENABLED（默认关；客户端在调用 decorate 前拦截）。
        if os.environ.get("HCM_BLOCK_OFF") == "1":
            return "", []
        picked, used = [], []
        seen_tid = set()
        for i, fa in enumerate(facts):
            # 只给前 TOPN_FACTS 条**成功挂上链**的 fact 出块; 其余 fact 不挂链, 照常留在
            # bullet 列表(信息不丢, 只是不带 diff)。块大小近似线性于挂链 fact 数，
            # 因此必须限制数量以控制上下文长度。
            if TOPN_FACTS > 0 and len(picked) >= TOPN_FACTS:
                break
            th = self._threads_for(fa, scope=scope)
            if scope:
                th = [t for t in th if self._in_scope(t, scope)]
            th = [t for t in th if t[0] not in seen_tid]
            th = self._rank_threads(th, instruction)
            if not th:
                continue
            seen_tid.update(t[0] for t in th)
            picked.append((fa, th))
            used.append(i)
        if not picked:
            return "", []
        return self._render(picked, instruction), used

    def _rank_threads(self, th, instruction):
        """按与指令的 token 重叠给 thread 排序, 只留前 TOPN_THREADS 条。

        同一分支可能包含多个决策点。排序只使用指令中已有的文件名、符号名和
        代码 token，不能使用调用方未提供的隐藏 key。HCM_TOPN_THREADS=0 可关闭截断。
        """
        n = TOPN_THREADS
        if n <= 0 or len(th) <= n:
            return th
        want = self._tokens(instruction)
        if not want:
            return th[:n]
        scored = []
        for t in th:
            body = " ".join(
                f"{sd} {' '.join(json.loads(aj or '[]'))}"
                for sd, aj in self.db.execute(
                    "SELECT said,adds FROM round WHERE thread_id=?", (t[0],)))
            # 打分只看重叠个数; 平手时保持原顺序(updated_at) —— 稳定排序
            scored.append((len(want & self._tokens(body)), t))
        scored.sort(key=lambda x: -x[0])
        return [t for _, t in scored[:n]]

    @staticmethod
    def _in_scope(row, scope):
        """thread 行是否属于 scope。row = (id, file, symbol, lo, hi, n_rounds)。

        scope 通常是仓名。判据允许文件路径首段或任意段命中，以兼容
        `project/...` 与 `lib/project/...` 等布局。
        ⚠️ 不做模糊匹配: scope 认不出来时**放行**(返回 True), 免得把本仓的也滤掉 ——
        宁可留噪音也不能把唯一有用的那条弄丢。
        """
        f = (row[1] or "").replace("\\", "/")
        if not f or not scope:
            return True
        segs = set(f.split("/"))
        s = scope.lower()
        return (s in {x.lower() for x in segs}
                or f.lower().startswith(s + "/")
                or f"/{s}/" in f.lower())

    def _threads_for(self, fa, scope=None):
        """一条 fact → 它讲的那些 thread。三个条件求交, 全是精确查, 无相似度。

        scope: 仅在 branch 和 files 都认不出时用作**最后兜底**(需 HCM_SCOPE_FALLBACK=1)。
               见下面 else 分支的注释。
        """
        # 向量层返回的 fact 不一定带 branch 字段，所以也尝试从 statement 文本识别；
        # 认不出就不猜。
        br = fa.get("branch") or self._guess_branch(fa.get("statement") or "")
        files = fa.get("files") or []
        st = fa.get("statement") or ""
        base = {os.path.basename(f) for f in files if f}
        rows = []
        if br:
            rows = self.db.execute(
                "SELECT id,file,symbol,lo,hi,n_rounds FROM thread"
                " WHERE owner=? AND branch=? ORDER BY updated_at",
                (self.owner, br)).fetchall()
        if not rows and base:
            # 没有 branch 或 branch 查空时按 files 退回。branch 与 files 不能做成互斥
            # 分支，因为事实层和链层可能存在部分数据缺失。
            _q = ",".join("?" * len(base))
            rows = self.db.execute(
                f"SELECT id,file,symbol,lo,hi,n_rounds FROM thread"
                f" WHERE owner=? AND (file IN ({_q}) OR "
                + " OR ".join(["file LIKE ?"] * len(base)) +
                ") ORDER BY updated_at",
                (self.owner,) + tuple(base) + tuple(f"%/{b}" for b in base)).fetchall()
        if not rows and scope and os.environ.get("HCM_SCOPE_FALLBACK") == "1":
            # 最后兜底：按 scope 取本仓全部链。默认关闭，因为它扩大候选范围；启用前
            # 必须确认数据源不会包含当前任务的答案或其他自链接泄漏。
            rows = [r for r in self.db.execute(
                        "SELECT id,file,symbol,lo,hi,n_rounds FROM thread"
                        " WHERE owner=? ORDER BY updated_at", (self.owner,))
                    if self._in_scope(r, scope)]
        if not rows:
            return []
        # file: fact 的 files 命中(全路径或 basename); fact 没给 files 就不按文件筛
        cand = [r for r in rows if (not base) or r[1] in files
                or os.path.basename(r[1]) in base] or rows
        # symbol: statement 里点名了这个符号 → **排前面**, 而不是把其余筛掉。
        # ⚠️ 不能硬筛: fact 的 statement 是自然语言总结, **它提不提符号名是不确定的**,
        # 拿它当硬条件会让链在库里、file 也对得上的点挂不到自己的链。
        named = [r for r in cand if r[2] and re.search(r'\b%s\b' % re.escape(r[2]), st)]
        if named:
            _rest = [r for r in cand if r not in named]
            return named + _rest
        return cand

    def _render(self, picked, instruction):
        """渲染注入块。

        预算按 fact **均分**, 不是先到先得，避免第一条长链挤掉后续 facts。

        长链(超过 SHOW_ALL 版)不全展开: 按"该版代码/原话与指令的 token 重叠"选出相关的
        几版**完整展示**, 其余版本只留一行 said 摘要。所有版本的序号都保留 ——
        因为"用我第二次改的那版"这种指代必须能数清第几版, 缺号就废了。
        """
        want = self._tokens(instruction)
        files = list(dict.fromkeys(t[1] for _, th in picked for t in th))
        # branch 不能只看 fact：事实层可能没有 branch，而 thread 自身有。块头必须
        # 从挂上的 threads 中补出 branch，避免调用方丢失切分支所需信息。
        _br = picked[0][0].get("branch") or ""
        if not _br:
            for _fa, _th in picked:
                _b = self._branch_of_threads(_th)
                if _b:
                    _br = _b
                    break
        _head = f"{BLOCK_MARK} " + (f"分支 {_br} · " if _br else "") + " / ".join(files)[:160]
        L = [_head]
        share = max(600, (MAX_BLOCK_CHARS - sum(len(x) for x in L)) // max(1, len(picked)))

        for fa, th in picked:
            seg = ["\n- " + _clean(fa.get("statement") or "")]
            for tid, file, symbol, lo, hi, n in th:
                where = symbol if symbol else f"{os.path.basename(file)}:L{lo}-{hi}"
                seg.append(f"    └ {where}  (这条分支上改过 {n} 次)")
                rs = self.db.execute(
                    "SELECT seq,said,patch,created_at,adds FROM round WHERE thread_id=? ORDER BY seq",
                    (tid,)).fetchall()
                t0 = rs[0][3] if rs else 0
                full = self._pick_versions([(r[0], r[1], r[2]) for r in rs], want)
                # 替换对 + 撤销关系, 一次算完整条链(见 _rev_lines)
                _rv = self._rev_lines([(r[0], r[1], r[2]) for r in rs])
                for seq, said, patch, ct, adds_j in rs:
                    mark = "   ← 当前代码" if seq == n and n > 1 else ""
                    # 相对时间: 给出"这一版离第 1 版隔了多久"。裸 unix 时间戳要模型自己换算,
                    # 相对量它直接能读 —— 隔 3 分钟和隔 2 小时说明的事不一样(后者中间做了别的)。
                    # 顺序本身由 `第N版` 表达(seq 是 observe 调用计数, 物理事实), 时间是补充。
                    dt = self._reltime(ct - t0) if seq > 1 else ""
                    # 撤销标注: "这一版把第 N 版改回去了"。定稿指令常用序数指代
                    # ("回到最开始那版" / "不是最开始, 是第二次改的那版"), 有了这行,
                    # agent 不用自己比较各版本代码去推谁否定了谁。
                    _rl, _rev = _rv.get(seq, ([], []))
                    rvk = ("   ← 撤销了第" + "、".join(str(s) for s in _rev) + "版") if _rev else ""
                    if seq not in full:
                        # 略掉代码但保留序号和一句摘要 —— 序号不能缺
                        seg.append(f'       第{seq}版{dt} · "{said[:60]}"{rvk}{mark}')
                        continue
                    seg.append(f'       第{seq}版{dt} · "{said[:MAX_SAID_CHARS]}"{rvk}{mark}')                    # ⚠️ 三种代码行格式的取舍:
                    #   ① 只给 `+ 新增行`(adds)     —— 最短, 但丢了"从什么改成什么"。
                    #      例如三版都显示 `+ return X`, agent 会以为三行并存,
                    #      实际是同一行的三次替换, 于是认错了"最开始那版"。
                    #   ② `-/+` 两行对照(HCM_DIFF_VIEW=1) —— 语义全, 但长一倍 → 均分预算下
                    #      容易把别的 fact 挤出块，故只保留为调试开关。
                    #   ③ `~ 旧 → 新` 一行(现行默认) —— 长度≈①, 语义≈②。
                    # HCM_ADDS_ONLY=1 可退回 ①。
                    if os.environ.get("HCM_DIFF_VIEW") == "1":
                        lines = self._pick_lines(self._core_diff(patch), want)
                    elif os.environ.get("HCM_ADDS_ONLY") == "1":
                        try:
                            lines = ["+ " + x for x in json.loads(adds_j or "[]")][:MAX_CODE_LINES]
                        except Exception:
                            lines = []
                    else:
                        lines = self._pick_lines(_rl, want) if _rl else []
                    used_c = 0
                    for a in lines:
                        if used_c + len(a) > MAX_CODE_CHARS:
                            break
                        seg.append(f"              {a}")
                        used_c += len(a)
                # ── 链末尾: 最后一版的完整代码 ────────────────────────────────
                # 各版只给 diff, 而 diff 里没有"从未被改动过的行"(见 _body_at)。给最后一版
                # 的全文, 模型就能自己 全文 − 各版diff = 任意一版。
                _bd = self.db.execute("SELECT body FROM thread WHERE id=?", (tid,)).fetchone()
                _bd = (_bd[0] if _bd else "") or ""
                if _bd:
                    if len(_bd) > BODY_MAX_CHARS:
                        _bd = _bd[:BODY_MAX_CHARS] + "\n…(截断)"
                    seg.append(f"       ── 最后一版({n})落地后, 这段代码的完整样子 "
                               f"(前面各版的 diff 是相对它往前倒推的) ──")
                    for _l in _bd.splitlines():
                        seg.append(f"         {_l}")
            # ── 超额度时的裁剪顺序: 先压原话, 后砍代码行 ──────────────────────
            # 不能反过来(先把代码行全 pop 光, 再压原话): 定稿时要的是**代码**, 原话只是
            # "这是哪一版"的线索。先砍代码 = 砍掉答案、留下线索。
            # 原话压到一定长度仍够区分版本(它前面已含文件名+符号名等判别词), 所以先压原话,
            # 压到底还超才开始砍代码行。
            # ⚠️ 压缩下限 240, 不再往下压: 压得太短基本等于把原话废掉, 而原话是这个机制的
            # 核心(fact 只说最终状态、said 说每一版当时的想法)。宁可少挂一条链, 也不把原话
            # 压到认不出。
            _SAID_TIERS = (240,)
            for _cut in _SAID_TIERS:
                if sum(len(x) for x in seg) <= share:
                    break
                for _i, _x in enumerate(seg):
                    _mm = re.match(r'^(\s+第\d+版[^·]*· ")(.*)("[^"]*)$', _x)
                    if _mm and len(_mm.group(2)) > _cut:
                        seg[_i] = _mm.group(1) + _mm.group(2)[:_cut] + "…" + _mm.group(3)
            # 原话压到底仍超 → 才从尾部丢代码行(版本号行永不丢)
            while sum(len(x) for x in seg) > share and any(
                    # `~` 是 _rev_lines 的替换对前缀(见它的注释), 与 +/- 同属"代码行"
                    x.startswith("              ") and x.strip()[:1] in "+-~" for x in seg):
                for i in range(len(seg) - 1, -1, -1):
                    if seg[i].startswith("              ") and seg[i].strip()[:1] in "+-~":
                        seg.pop(i)
                        break
            # 最后才动 body(它是"完整代码"那一块, 9 缩进) —— 从尾部逐行丢, 保留开头
            # (函数签名在开头, 比末尾几行有用), 丢到只剩标题行时把标题也去掉。
            while sum(len(x) for x in seg) > share and any(
                    x.startswith("         ") and not x.startswith("          ") for x in seg):
                for i in range(len(seg) - 1, -1, -1):
                    if seg[i].startswith("         ") and not seg[i].startswith("          "):
                        seg.pop(i)
                        break
            if not any(x.startswith("         ") and not x.startswith("          ") for x in seg):
                seg = [x for x in seg if "这段代码的完整样子" not in x]
            L.extend(seg)
        # 兜底: 整块仍超 → 继续压原话摘要(序号与代码行都不动)
        # 兜底: 整块仍超 → 压到 240 为止(与 _SAID_TIERS 同下限, 不再压到认不出)。
        # 之前这里是 (80,40,24,14) —— 那会把 381 个版本压到 80 字符, 见上面的注释。
        for cut in (240,):
            if sum(len(x) for x in L) + len(L) <= MAX_BLOCK_CHARS:
                break
            for i, x in enumerate(L):
                mm = re.match(r'^(\s+第\d+版[^·]*· ")(.*)("[^"]*)$', x)
                if mm and len(mm.group(2)) > cut:
                    L[i] = mm.group(1) + mm.group(2)[:cut] + "…" + mm.group(3)
        # ── 分支提示放在**块尾**, 不是块头 ────────────────────────────────────────
        # 分支提示放块尾，使它靠近后续指令；长块开头的提示更容易被后续候选覆盖。
        if _br:
            L.append(f"\n⚠ 以上改动都在分支 `{_br}` 上。**先 `git checkout {_br}`** 再动手 ——"
                     f"下面别处提到的其它 `feat/...` 分支与这次要做的事无关, 不要切过去。")
        return "\n".join(L)

    def _branch_of_threads(self, th):
        """从挂上的 thread 里取 branch —— 按**多数投票**, 不是取第一条。

        挂上的链可能来自同一文件的多个任务，排序第一条未必代表当前分支。多数投票
        更稳；无法识别标准 branch 时返回空，让块头自然退化为无分支提示。
        """
        if not th:
            return ""
        ids = [str(t[0]) for t in th]
        q = ",".join("?" * len(ids))
        cnt = collections.Counter()
        for (b,) in self.db.execute(
                f"SELECT branch FROM thread WHERE id IN ({q})", tuple(ids)):
            if b and _BR.search(b):
                cnt[b] += 1
        if not cnt:
            return ""
        # 平票时取候选里排最前的那条(它与指令的 token 重叠最高)
        top = cnt.most_common()
        if len(top) > 1 and top[0][1] == top[1][1]:
            for t in th:
                row = self.db.execute("SELECT branch FROM thread WHERE id=?", (t[0],)).fetchone()
                if row and row[0] in cnt:
                    return row[0]
        return top[0][0]

    @staticmethod
    def _tokens(s):
        return {t.lower() for t in re.findall(r'[A-Za-z_$][\w$]{2,}', s or "")}

    def _pick_versions(self, rs, want):
        """长链里哪几版完整展示(带代码)。

        ≤SHOW_ALL 版全展开。超了就按与指令的 token 重叠打分, 取前 SHOW_ALL 版,
        并**强制保留第 1 版和最后一版** —— 第 1 版是"最开始那版"这类指代的目标,
        最后一版是当前代码, 两头都不能省。
        """
        n = len(rs)
        if n <= SHOW_ALL:
            return {r[0] for r in rs}
        scored = []
        for seq, said, adds_j in rs:
            try:
                body = said + " " + " ".join(json.loads(adds_j or "[]"))
            except Exception:
                body = said
            scored.append((len(want & self._tokens(body)), seq))
        scored.sort(key=lambda x: (-x[0], x[1]))
        keep = {rs[0][0], rs[-1][0]}
        for _, seq in scored:
            if len(keep) >= SHOW_ALL:
                break
            keep.add(seq)
        return keep

    def _rev_lines(self, rs):
        """把一条链的各版渲染成"替换对 + 撤销关系"。→ {seq: (lines, revokes)}

        lines   : 该版要显示的代码行, 形如 `~ 旧 → 新`(同一处的替换)或 `+ 新`(纯新增)
        revokes : 该版撤销了哪几版(seq 列表)

        ── 为什么是 `~ 旧 → 新` 而不是 `-/+` 两行 ────────────────────────────
        `-/+` 两行比单行替换表示更长，会在均分预算下挤掉其他 facts。
        但只给 `+` 又丢了语义: 三版都显示成 `+ return X` 时, agent 分不清这是"同一行的三个
        版本"还是"三行并存"。
        `~ 旧 → 新` 一行表达替换关系: 长度接近单个 `+` 行, 语义却与 `-/+` 等价。

        撤销关系使用 `Δt_del ∩ Δi_add ≠ ∅`：若当前删除行与历史新增行相交，标记为
        撤销对应版本。该关系可在入库侧确定，不需要额外 LLM 推理。
        """
        seq_add = {}      # seq -> 该版新增的行(strip 后)
        out = {}
        for seq, said, patch in rs:
            core = self._core_diff(patch or "")
            dels = [line[1:].strip() for line in core if line[:1] == "-" and line[1:].strip()]
            adds = [line[1:].strip() for line in core if line[:1] == "+" and line[1:].strip()]
            # 撤销: 本版删掉的行, 曾是之前哪几版加上的(先算再登记, 免得自己撞自己)
            rev = sorted(s for s, a in seq_add.items() if s < seq and (set(dels) & a))
            seq_add[seq] = set(adds)
            # 配对: 删和加数量相同的部分按位置配成替换对; 多出来的单列
            k = min(len(dels), len(adds))
            lines = [f"~ {dels[i]} → {adds[i]}" for i in range(k)]
            lines += [f"- {x}" for x in dels[k:]]
            lines += [f"+ {x}" for x in adds[k:]]
            out[seq] = (lines, rev)
        return out

    def _core_diff(self, patch):
        """从一版的 patch 里抽出**真正变化的行对**(带 -/+ 前缀), 而不是只给新增行。

        为什么摆 diff 而不是只摆 adds: 只给 `+` 行时模型只知道"这版是什么",
        不知道"**从什么改成什么**" —— 而区分版本靠的恰恰是版本之间的差别。
        例如摆 adds 只看到 `+ Split(packVer, "_")`,
        摆 diff 能看到 `- Split(packVer, "-")` / `+ Split(packVer, "_")` 这一对, 判别力完全不同。

        为什么要压缩: agent 有时用"整函数替换"的写法, patch 就把整个函数删一遍再加一遍,
        而真正变的只有几处。整段装不进预算, 关键行会被窗口截掉, 比不给更糟。
        用 difflib 对 -/+ 两侧再求一次差, 只留下真正的判别信息。
        """
        if not patch:
            return []
        old = [line[1:] for line in patch.splitlines()
               if line.startswith("-") and not line.startswith("---")]
        new = [line[1:] for line in patch.splitlines()
               if line.startswith("+") and not line.startswith("+++")]
        if not old:                      # 纯新增(新建函数), 没有可对照的旧版
            return ["+ " + x.strip() for x in new]
        core = [line for line in difflib.unified_diff(old, new, lineterm="", n=0)
                if line[:1] in "+-" and line[:3] not in ("---", "+++")]
        if not core:                     # 两侧一模一样(纯格式/位置变动)
            return []
        return [line[:1] + " " + line[1:].strip() for line in core]

    @staticmethod
    def _reltime(sec):
        """相对第 1 版隔了多久。给模型的是可直读的量, 不是 unix 时间戳。"""
        sec = max(0, int(sec))
        if sec < 90:
            return f"(+{sec}秒)"
        if sec < 5400:
            return f"(+{sec // 60}分钟)"
        if sec < 172800:
            return f"(+{sec // 3600}小时)"
        return f"(+{sec // 86400}天)"

    def _pick_lines(self, lines, want):
        """一版里显示哪几行。入参是 _core_diff 出的 -/+ 行(不再是 adds)。**不是取前 N 行。**

        取前 N 行会系统性地杀掉判别信息: 全函数替换那种 adds 前几行往往全是
        函数头和 for/if 样板, 而区分"这是哪一版"的行正好落在窗口外 —— 不同版本的判别行
        有的被展示、有的被截掉, 比不给更糟。

        排序规则(稳定, 同档保持代码原顺序):
          ① 与指令 token 有重叠的行 —— 但**别指望它**: _tokens 只抓英文标识符, 而指令常是
             中文口语("拆完之后把前面几段重新拼回去"), 交集常为空。所以它只是加分项。
          ② 有判别力的行: 赋值/返回/调用等实质语句。
          ③ 样板行: 函数头、for/if/else、单独的括号 —— 每一版都长一样, 对"是哪一版"零信息。
        """
        if len(lines) <= MAX_CODE_LINES:
            return lines
        def rank(a):
            s = a.strip().lstrip("+-~").strip()  # 去掉 diff/替换 前缀再判是不是样板
            if want & self._tokens(a):
                return 0
            if _BOILER.match(s):
                return 2
            return 1
        idx = sorted(range(len(lines)), key=lambda i: (rank(lines[i]), i))[:MAX_CODE_LINES]
        return [lines[i] for i in sorted(idx)]      # 显示回原始代码顺序


    # ---- 便利接口 ----

    def recall(self, instruction, facts=None):
        """只想拿块、不关心去重时用这个。facts 为 None 时退化成按指令认分支。"""
        if facts is not None:
            return self.decorate(facts, instruction)[0]
        br = self._guess_branch(instruction)
        if not br:
            return ""
        return self.decorate([{"statement": instruction, "branch": br, "files": []}],
                             instruction)[0]

    def stats(self, all_owners=False):
        """库规模。默认只统计**本 owner** 的，与 decorate 看到的范围一致。

        all_owners=True 统计全库 —— 运维/排查用。日常报数默认只看当前 owner，
        避免把其他隔离域的数据计入当前导入规模。
        """
        def q(sql, params=()):
            return self.db.execute(sql, params).fetchone()[0]

        if all_owners:
            return dict(threads=q("SELECT COUNT(*) FROM thread"),
                        rounds=q("SELECT COUNT(*) FROM round"),
                        symbols=q("SELECT COUNT(*) FROM repo_symbol"),
                        branches=q("SELECT COUNT(DISTINCT branch) FROM thread"),
                        multi=q("SELECT COUNT(*) FROM thread WHERE n_rounds>1"))
        o = (self.owner,)
        return dict(
            threads=q("SELECT COUNT(*) FROM thread WHERE owner=?", o),
            # round 表没有 owner 列（它挂在 thread 上），走 JOIN
            rounds=q("SELECT COUNT(*) FROM round r JOIN thread t ON r.thread_id=t.id"
                     " WHERE t.owner=?", o),
            symbols=q("SELECT COUNT(*) FROM repo_symbol"),
            branches=q("SELECT COUNT(DISTINCT branch) FROM thread WHERE owner=?", o),
            multi=q("SELECT COUNT(*) FROM thread WHERE owner=? AND n_rounds>1", o),
        )

    def close(self):
        try:
            self.db.commit()
            self.db.close()
        except Exception:
            pass
