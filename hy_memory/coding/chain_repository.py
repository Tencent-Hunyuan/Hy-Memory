"""Relational version-chain repository; parsing and rendering stay in chain_store."""

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import ContextManager, List, Optional, Protocol, Sequence, Tuple

from ..config import BusinessStoreConfig

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


class ChainRepository(Protocol):
    """Atomic version-chain writes and owner-scoped reads, without vector access."""

    def transaction(self) -> ContextManager: ...
    def close(self) -> None: ...
    def upsert_thread(self, branch: str, file: str, symbol: str, lo: int, hi: int,
                      by_symbol: bool = False, pkey: str = "") -> int: ...
    def add_round(self, tid: int, said: str, patch: str, at: Optional[float] = None) -> None: ...
    def set_body(self, tid: int, body: str) -> None: ...
    def replace_symbols(self, rows: Sequence[Tuple[str, str, int, str]]) -> None: ...
    def round_texts(self, tid: int) -> List[tuple]: ...
    def threads_by_branch(self, branch: str) -> List[tuple]: ...
    def threads_by_files(self, files: Sequence[str]) -> List[tuple]: ...
    def all_threads(self) -> List[tuple]: ...
    def rounds_for_thread(self, tid: int) -> List[tuple]: ...
    def body_for_thread(self, tid: int) -> Optional[tuple]: ...
    def branches_for_threads(self, ids: Sequence[int]) -> List[tuple]: ...
    def branch_for_thread(self, tid: int) -> Optional[tuple]: ...
    def stats(self, all_owners: bool = False) -> dict: ...


class RelationalChainRepository:
    def __init__(self, owner: str, line_merge: int) -> None:
        self.owner = owner
        self.line_merge = line_merge
        self._lock = threading.RLock()
        self._conn = None

    def _execute(self, sql: str, params=()):
        if self._conn is None:
            raise RuntimeError("Chain repository is closed")
        return self._conn.execute(sql, params)

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def upsert_thread(self, branch, file, symbol, lo, hi, by_symbol=False, pkey=""):
        """找到同一条 thread 就复用, 否则新建。

        同 symbol 内按行区间分链: 区间重叠或相邻 ±self.line_merge 行算同一件事。
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
        cur = self._execute(
            "SELECT id, lo, hi FROM thread"
            " WHERE owner=? AND branch=? AND file=? AND symbol=? AND pkey=?",
            (self.owner, branch, file, symbol, pkey)).fetchall()
        if by_symbol and cur:
            tid, line_start, line_end = cur[0]
            self._execute("UPDATE thread SET updated_at=? WHERE id=?", (time.time(), tid))
            return tid
        for tid, line_start, line_end in cur:
            if lo <= line_end + self.line_merge and hi >= line_start - self.line_merge:
                self._execute("UPDATE thread SET lo=?, hi=?, updated_at=? WHERE id=?",
                                (min(line_start, lo), max(line_end, hi), time.time(), tid))
                return tid
        # 跨 symbol 的行区间归并(见上文注释)。只在**行号可信**时做:
        # by_symbol=True 说明 diff 是合成的、行号是假的, 拿假行号去撞别的符号会乱并。
        if not by_symbol:
            for tid, line_start, line_end, sym2 in self._execute(
                    "SELECT id, lo, hi, symbol FROM thread"
                    " WHERE owner=? AND branch=? AND file=? AND symbol!=? AND pkey=?",
                    (self.owner, branch, file, symbol, pkey)).fetchall():
                if lo <= line_end + self.line_merge and hi >= line_start - self.line_merge:
                    # symbol 收敛到**更具体**的那个, 判据是行区间更窄。
                    # 新增函数时第 1 版的 diff 落在外层函数的区间里, 第 2 版起才归到自己。
                    # 名字必须取窄的那个 —— 注入块的链标签 `└ <symbol>` 是 agent
                    # 区分"这条历史属于哪个函数"的唯一线索, 取成外层函数就等于没标。
                    keep = symbol if (hi - lo) < (line_end - line_start) else (sym2 or symbol)
                    self._execute(
                        "UPDATE thread SET lo=?, hi=?, symbol=?, updated_at=? WHERE id=?",
                        (min(line_start, lo), max(line_end, hi), keep, time.time(), tid))
                    return tid
        return self._insert_thread(
            "INSERT INTO thread(owner,branch,file,symbol,pkey,lo,hi,n_rounds,updated_at)"
            " VALUES(?,?,?,?,?,?,?,0,?)",
            (self.owner, branch, file, symbol, pkey, lo, hi, time.time()))

    def add_round(self, tid, said, patch, at=None):
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
        if self.branch_for_thread(tid) is None:
            raise ValueError("Version thread does not belong to this owner")
        row = self._execute(
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
            self._execute("UPDATE round SET patch=?, adds=? WHERE id=?",
                            (merged_patch, json.dumps(adds, ensure_ascii=False), rid))
            return
        seq = self._execute("SELECT COUNT(*) FROM round WHERE thread_id=?",
                              (tid,)).fetchone()[0] + 1
        # 去重但保序 —— 同一轮里重复的 return/赋值行白占预算
        adds = list(dict.fromkeys(a.strip() for a in new_adds))
        self._execute(
            "INSERT INTO round(thread_id,seq,said,patch,adds,created_at) VALUES(?,?,?,?,?,?)",
            (tid, seq, said, patch, json.dumps(adds, ensure_ascii=False), at or time.time()))
        self._execute("UPDATE thread SET n_rounds=?, updated_at=? WHERE id=?",
                        (seq, time.time(), tid))

    def stats(self, all_owners=False):
        with self._lock:
            return self._stats(all_owners)

    def _stats(self, all_owners=False):
        """库规模。默认只统计**本 owner** 的，与 decorate 看到的范围一致。

        all_owners=True 统计全库 —— 运维/排查用。日常报数默认只看当前 owner，
        避免把其他隔离域的数据计入当前导入规模。
        """
        def q(sql, params=()):
            return self._execute(sql, params).fetchone()[0]

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

    def set_body(self, tid: int, body: str) -> None:
        self._execute("UPDATE thread SET body=? WHERE id=? AND owner=?", (body, tid, self.owner))

    def replace_symbols(self, rows) -> None:
        with self.transaction():
            self._execute("DELETE FROM repo_symbol")
            for row in rows:
                self._execute("INSERT INTO repo_symbol VALUES(?,?,?,?)", row)

    def round_texts(self, tid: int) -> List[tuple]:
        with self._lock:
            return self._execute(
                "SELECT r.said,r.adds FROM round r JOIN thread t ON r.thread_id=t.id "
                "WHERE r.thread_id=? AND t.owner=?", (tid, self.owner),
            ).fetchall()

    def threads_by_branch(self, branch: str) -> List[tuple]:
        with self._lock:
            return self._execute(
                "SELECT id,file,symbol,lo,hi,n_rounds FROM thread "
                "WHERE owner=? AND branch=? ORDER BY updated_at", (self.owner, branch),
            ).fetchall()

    def threads_by_files(self, files) -> List[tuple]:
        if not files:
            return []
        marks = ",".join("?" for _ in files)
        with self._lock:
            return self._execute(
                f"SELECT id,file,symbol,lo,hi,n_rounds FROM thread WHERE owner=? "
                f"AND (file IN ({marks}) OR " + " OR ".join("file LIKE ?" for _ in files)
                + ") ORDER BY updated_at",
                (self.owner, *files, *(f"%/{file}" for file in files)),
            ).fetchall()

    def all_threads(self) -> List[tuple]:
        with self._lock:
            return self._execute(
                "SELECT id,file,symbol,lo,hi,n_rounds FROM thread WHERE owner=? ORDER BY updated_at",
                (self.owner,),
            ).fetchall()

    def rounds_for_thread(self, tid: int) -> List[tuple]:
        with self._lock:
            return self._execute(
                "SELECT r.seq,r.said,r.patch,r.created_at,r.adds FROM round r "
                "JOIN thread t ON r.thread_id=t.id WHERE r.thread_id=? AND t.owner=? ORDER BY r.seq",
                (tid, self.owner),
            ).fetchall()

    def body_for_thread(self, tid: int) -> Optional[tuple]:
        with self._lock:
            return self._execute("SELECT body FROM thread WHERE id=? AND owner=?", (tid, self.owner)).fetchone()

    def branches_for_threads(self, ids) -> List[tuple]:
        if not ids:
            return []
        marks = ",".join("?" for _ in ids)
        with self._lock:
            return self._execute(
                f"SELECT branch FROM thread WHERE owner=? AND id IN ({marks})", (self.owner, *ids),
            ).fetchall()

    def branch_for_thread(self, tid: int) -> Optional[tuple]:
        with self._lock:
            return self._execute("SELECT branch FROM thread WHERE id=? AND owner=?", (tid, self.owner)).fetchone()


class SqliteChainRepository(RelationalChainRepository):
    def __init__(self, store_dir: str, owner: str = "", line_merge: int = 20) -> None:
        super().__init__(owner, line_merge)
        os.makedirs(store_dir, exist_ok=True)
        self._conn = sqlite3.connect(os.path.join(store_dir, "hy_code.db"),
                                     timeout=30, check_same_thread=False)
        try:
            # The legacy index references owner/pkey, which older files may lack.
            self._conn.executescript(_SCHEMA.replace(
                "CREATE INDEX IF NOT EXISTS ix_thread_bf ON thread(owner, branch, file, pkey);", ""
            ))
            columns = {row[1] for row in self._conn.execute("PRAGMA table_info(thread)")}
            for column in ("pkey", "body", "owner"):
                if column not in columns:
                    self._conn.execute(f"ALTER TABLE thread ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
            self._conn.execute("DROP INDEX IF EXISTS ix_thread_bf")
            self._conn.execute("CREATE INDEX ix_thread_bf ON thread(owner, branch, file, pkey)")
            self._conn.commit()
        except BaseException:
            self.close()
            raise

    @contextmanager
    def transaction(self):
        with self._lock, self._conn:
            yield

    def _insert_thread(self, sql: str, params) -> int:
        return self._execute(sql, params).lastrowid


class PostgresChainRepository(RelationalChainRepository):
    def __init__(self, settings: BusinessStoreConfig, owner: str = "", line_merge: int = 20) -> None:
        from .repository_postgres import connect_postgres, execute_schema, initialize_namespace, namespace

        super().__init__(owner, line_merge)
        self.schema = namespace(settings, "chain")
        self._conn = connect_postgres(settings, mapping_rows=False)
        try:
            with initialize_namespace(self._conn, self.schema):
                ddl = _SCHEMA.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
                ddl = ddl.replace("thread_id INTEGER", "thread_id BIGINT").replace("REAL", "DOUBLE PRECISION")
                execute_schema(self._conn, ddl)
        except BaseException:
            self.close()
            raise

    def _execute(self, sql: str, params=()):
        return super()._execute(sql.replace("?", "%s"), params)

    def _insert_thread(self, sql: str, params) -> int:
        return self._execute(sql + " RETURNING id", params).fetchone()[0]

    @contextmanager
    def transaction(self):
        with self._lock, self._conn.transaction():
            # One owner can append from multiple service instances. Serialize its
            # find-or-create + version numbering, not just individual INSERTs.
            self._conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s),hashtext(%s))",
                (self.schema, self.owner),
            )
            yield


def create_chain_repository(config, store_dir: str, owner: str, line_merge: int) -> ChainRepository:
    if config is not None:
        config.business_store.validate()
        if config.business_store.backend == "postgres":
            return PostgresChainRepository(config.business_store, owner, line_merge)
    return SqliteChainRepository(store_dir, owner, line_merge)
