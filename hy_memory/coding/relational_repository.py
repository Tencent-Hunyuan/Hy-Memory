"""Shared relational queries behind the business repository contract."""

import json
import logging
import threading
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
from typing import Any, Dict, Iterator, List, Mapping, Optional, Set, Tuple, Union

from .repository_base import MemoryRepository
from .types import (
    CodingMemory, WorkingFact, ConsolidationScope, ConsolidationSnapshot,
    ConsolidationReceipt, ConsolidationConflict,
)
from .evidence import project, reference, identity, canonical, validate_sessions

logger = logging.getLogger(__name__)

# Authoritative visibility, applied before pagination/counting and after ANN recall.
_VISIBLE = """(coding_memory_meta.consolidation_id IS NULL OR EXISTS (
    SELECT 1 FROM consolidation_receipt cr
    WHERE cr.consolidation_id = coding_memory_meta.consolidation_id
      AND cr.is_current = 1 AND cr.basis_valid = 1))"""

def _scope_where(
    workspace_id: Optional[str], branch: Optional[str]
) -> Tuple[str, List[Any]]:
    """把作用域条件翻译成 SQL 布尔表达式 + 绑定参数。不过滤时返回恒真的 "TRUE"。"""
    if not workspace_id:
        return "TRUE", []
    sql = "workspace_id = ?"
    params: List[Any] = [workspace_id]
    if branch:
        sql += " AND (branch = ? OR branch IS NULL)"
        params.append(branch)
    return sql, params

def _in_clause(column: str, values: Optional[List[str]]) -> Tuple[str, List[Any]]:
    """SQL ``column IN (...)`` + parameters; empty values mean no condition."""
    if not values:
        return "", []
    placeholders = ",".join("?" for _ in values)
    return f"{column} IN ({placeholders})", list(values)

def _source_where(
    include_sources: Optional[List[str]], exclude_sources: Optional[List[str]],
) -> Tuple[str, List[Any]]:
    """Caller-source filter as SQL (``1`` when unfiltered); exclusion wins."""
    clauses: List[str] = []
    params: List[Any] = []
    if include_sources:
        clauses.append(f"source IN ({','.join('?' for _ in include_sources)})")
        params.extend(include_sources)
    if exclude_sources:
        clauses.append(f"source NOT IN ({','.join('?' for _ in exclude_sources)})")
        params.extend(exclude_sources)
    return (" AND ".join(clauses) or "TRUE"), params

def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"

def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        # 去掉末尾 Z
        return datetime.fromisoformat(s.rstrip("Z"))
    except Exception:
        return None

def _from_epoch(value: Any) -> Optional[datetime]:
    """memory_at 列（秒级 epoch）→ datetime；NULL 或坏值返回 None。"""
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value))
    except Exception:
        return None

def _to_epoch(value: Optional[datetime]) -> Optional[int]:
    return int(value.timestamp()) if isinstance(value, datetime) else None

def _row_to_memory(row: Mapping[str, Any]) -> CodingMemory:
    """Database row → CodingMemory"""
    return CodingMemory(
        experience_id=row["experience_id"],
        user_id=row["user_id"],
        agent_id=row["agent_id"],
        task=row["task"],
        search_keys=json.loads(row["search_keys"] or "[]"),
        solution=row["solution"],
        boundary_envs=row["boundary_envs"],
        workspace_id=row["workspace_id"],
        branch=row["branch"],
        session_id=row["session_id"],
        files=json.loads(row["files"] or "[]"),
        source_fact_ids=json.loads(row["source_fact_ids"] or "[]"),
        confidence=row["confidence"],
        source=row["source"],
        extraction_source=row["extraction_source"],
        memory_at=_from_epoch(row["memory_at"]),
        created_at=_parse_dt(row["created_at"]),
        updated_at=_parse_dt(row["updated_at"]),
        consolidation_id=row["consolidation_id"],
        evidence_refs=json.loads(row["evidence_refs"] or "[]"),
    )

def _row_to_fact(row: Mapping[str, Any]) -> WorkingFact:
    """Database row → WorkingFact"""
    return WorkingFact(
        fact_id=row["fact_id"],
        user_id=row["user_id"],
        agent_id=row["agent_id"],
        session_id=row["session_id"],
        workspace_id=row["workspace_id"],
        branch=(row["branch"] if "branch" in row.keys() else None),
        source=row["source"],
        statement=row["statement"],
        files=json.loads(row["files"] or "[]"),
        # 老库刚 ADD COLUMN 时可能取不到这两列，用 keys() 兜一下（同 fact_chain_summary）
        subject=(row["subject"] or "" if "subject" in row.keys() else ""),
        details=(row["details"] or "" if "details" in row.keys() else ""),
        fact_link=row["fact_link"],
        derived_experience_ids=json.loads(row["derived_experience_ids"] or "[]"),
        status=row["status"],
        # 老库刚 ADD COLUMN 时可能取不到这一列，用 keys() 兜一下
        fact_chain_summary=(row["fact_chain_summary"] or ""
                            if "fact_chain_summary" in row.keys() else ""),
        memory_at=_from_epoch(row["memory_at"]),
        created_at=_parse_dt(row["created_at"]),
    )


class RelationalMemoryRepository(MemoryRepository):
    """Share query semantics while adapters own connections and schema setup."""

    supports_consolidation = True

    def get_experience_for_withdrawal(self, experience_id: str, user_id: Optional[str]) -> Optional[CodingMemory]:
        with self._lock:
            memory = self._load_material("experience", experience_id)
            return memory if memory and (user_id is None or memory.user_id == user_id) else None

    def _load_material(self, kind: str, key: str) -> Optional[Union[CodingMemory, WorkingFact]]:
        table, column, decode = (("working_fact", "fact_id", _row_to_fact) if kind == "fact"
                                  else ("coding_memory_meta", "experience_id", _row_to_memory))
        row = self._execute(f"SELECT * FROM {table} WHERE {column} = ?", (key,)).fetchone()
        return decode(row) if row else None

    @contextmanager
    def _changing_source(self, kind: str, key: str) -> Iterator[Optional[Union[CodingMemory, WorkingFact]]]:
        """Source lock precedes dependency lookup, including edits waiting on publish."""
        with self._business_transaction():
            self._lock_sources([(kind, key)])
            before = self._load_material(kind, key)
            yield before
            after = self._load_material(kind, key)
            if ((project(before) if before else None) != (project(after) if after else None)):
                dependencies = self._execute(
                    "SELECT DISTINCT cr.group_key FROM consolidation_receipt cr "
                    "JOIN consolidation_read_set rs ON rs.consolidation_id=cr.consolidation_id "
                    "WHERE rs.kind=? AND rs.material_id=?", (kind, key),
                ).fetchall()
                self._lock_groups([r["group_key"] for r in dependencies])
                self._execute(
                    "UPDATE consolidation_receipt SET basis_valid=0 WHERE consolidation_id IN ("
                    "SELECT consolidation_id FROM consolidation_read_set WHERE kind=? AND material_id=?)",
                    (kind, key),
                )

    def _receipt(self, consolidation_id: str, user_id: str) -> Optional[ConsolidationReceipt]:
        row = self._execute("SELECT * FROM consolidation_receipt WHERE consolidation_id=?",
                            (consolidation_id,)).fetchone()
        if row is None:
            return None
        scope = ConsolidationScope(**json.loads(row["scope_json"]))
        if scope.user_id != user_id:
            return None
        read_set = []
        for ref in self._execute(
            "SELECT kind,material_id,session_id,fingerprint,memory_at FROM consolidation_read_set "
            "WHERE consolidation_id=? ORDER BY kind,material_id", (consolidation_id,),
        ).fetchall():
            ref = dict(ref)
            current = self._load_material(ref["kind"], ref["material_id"])
            ref["status"] = ("missing" if current is None else "unchanged"
                             if reference(project(current)).fingerprint == ref["fingerprint"] else "changed")
            read_set.append(ref)
        ids = json.loads(row["experience_ids"])
        withdrawn = json.loads(row["withdrawn_ids"])
        memories = [self._load_material("experience", key) for key in ids if key not in withdrawn]
        return ConsolidationReceipt(
            consolidation_id=row["consolidation_id"], group_key=row["group_key"],
            basis_digest=row["basis_digest"], scope=scope,
            session_ids=json.loads(row["session_ids"]), decision=row["decision"],
            is_current=bool(row["is_current"]), basis_valid=bool(row["basis_valid"]),
            activation_epoch=row["activation_epoch"], experience_ids=ids,
            withdrawn_ids=withdrawn, read_set=read_set,
            experiences=[m for m in memories if m is not None],
            attempt_id=row["attempt_id"], created_at=row["created_at"],
        )

    def get_consolidation(self, consolidation_id: str, user_id: str) -> Optional[ConsolidationReceipt]:
        with self._lock, self._business_transaction(write=False):
            return self._receipt(consolidation_id, user_id)

    def consolidation_snapshot(self, scope: ConsolidationScope, session_ids: List[str]) -> ConsolidationSnapshot:
        sessions = validate_sessions(session_ids)
        where = ["user_id=?", "agent_id=?", "source=?"]
        params = [scope.user_id, scope.agent_id, scope.source]
        for column in ("workspace_id", "branch"):
            value = getattr(scope, column)
            where.append(f"{column} IS NULL" if value is None else f"{column}=?")
            if value is not None:
                params.append(value)
        where.append(f"session_id IN ({','.join('?' for _ in sessions)})")
        params.extend(sessions)
        clause = " AND ".join(where)
        with self._lock, self._business_transaction(write=False):
            facts = self._execute(f"SELECT * FROM working_fact WHERE {clause} "
                                  "AND status IN ('active','superseded')", params).fetchall()
            memories = self._execute(f"SELECT * FROM coding_memory_meta WHERE {clause} "
                                     "AND consolidation_id IS NULL", params).fetchall()
            materials = [project(_row_to_fact(r)) for r in facts]
            materials.extend(project(_row_to_memory(r)) for r in memories)
            materials.sort(key=lambda m: (m["kind"], m["material_id"]))
            if {m["session_id"] for m in materials} != set(sessions):
                raise ValueError("every selected session must have materials in the exact scope")
            refs = [reference(m) for m in materials]
            group, basis, cid = identity(scope, sessions, refs)
            current = self._execute(
                "SELECT consolidation_id,activation_epoch FROM consolidation_receipt "
                "WHERE group_key=? AND is_current=1", (group,),
            ).fetchone()
            baseline = (current["consolidation_id"], current["activation_epoch"]) if current else None
            existing = self._receipt(cid, scope.user_id)
            return ConsolidationSnapshot(scope, sessions, materials, refs, group, basis, cid,
                                         baseline, existing if existing and existing.basis_valid else None)

    def publish_consolidation(
        self, snapshot: ConsolidationSnapshot, memories: List[CodingMemory], attempt_id: str, *, reuse: bool = False,
    ) -> ConsolidationReceipt:
        """The sole publication protocol. No model or vector operation holds SQL locks."""
        with self._lock, self._business_transaction():
            self._lock_sources([(r.kind, r.material_id) for r in snapshot.read_set])
            for ref in snapshot.read_set:
                current = self._load_material(ref.kind, ref.material_id)
                if current is None or reference(project(current)) != ref:
                    raise ConsolidationConflict("frozen consolidation evidence changed")
            self._lock_groups([snapshot.group_key])
            row = self._execute(
                "SELECT consolidation_id,activation_epoch FROM consolidation_receipt "
                "WHERE group_key=? AND is_current=1", (snapshot.group_key,),
            ).fetchone()
            baseline = (row["consolidation_id"], row["activation_epoch"]) if row else None
            existing = self._receipt(snapshot.consolidation_id, snapshot.scope.user_id)
            # Same-basis simultaneous attempts return the winner's authoritative manifest.
            if baseline != snapshot.baseline:
                if (snapshot.baseline is None and existing and existing.is_current
                        and existing.basis_valid and existing.activation_epoch == 1):
                    return existing
                if (existing and existing.is_current and existing.basis_valid
                        and baseline[0] == snapshot.consolidation_id
                        and snapshot.baseline is not None
                        and baseline[1] == snapshot.baseline[1] + 1):
                    return existing
                raise ConsolidationConflict("consolidation activation changed")
            if existing and existing.is_current and existing.basis_valid:
                return existing
            if reuse and (not existing or not existing.basis_valid):
                raise ConsolidationConflict("historical decision requires review")
            epoch = self._execute(
                "SELECT MAX(activation_epoch) AS epoch FROM consolidation_receipt WHERE group_key=?",
                (snapshot.group_key,),
            ).fetchone()["epoch"] or 0
            self._execute("UPDATE consolidation_receipt SET is_current=0 WHERE group_key=?",
                          (snapshot.group_key,))
            if reuse:
                self._execute(
                    "UPDATE consolidation_receipt SET is_current=1,activation_epoch=? WHERE consolidation_id=?",
                    (epoch + 1, snapshot.consolidation_id),
                )
            else:
                if existing:
                    for key in existing.experience_ids:
                        self._execute("DELETE FROM coding_memory_meta WHERE experience_id=?", (key,))
                for memory in memories:
                    self._insert_consolidated(memory, snapshot)
                ids = [m.experience_id for m in memories]
                self._execute(
                    "INSERT INTO consolidation_receipt "
                    "(consolidation_id,group_key,basis_digest,scope_json,session_ids,decision,is_current,"
                    "basis_valid,activation_epoch,experience_ids,withdrawn_ids,attempt_id,created_at) "
                    "VALUES (?,?,?,?,?,?,1,1,?,?,?,?,?) ON CONFLICT(consolidation_id) DO UPDATE SET "
                    "decision=excluded.decision,is_current=1,basis_valid=1,activation_epoch=excluded.activation_epoch,"
                    "experience_ids=excluded.experience_ids,attempt_id=excluded.attempt_id",
                    (snapshot.consolidation_id, snapshot.group_key, snapshot.basis_digest,
                     canonical(asdict(snapshot.scope)), canonical(snapshot.session_ids),
                     "published" if memories else "no_result", epoch + 1, canonical(ids), "[]", attempt_id,
                     _now_iso()),
                )
                self._execute("DELETE FROM consolidation_read_set WHERE consolidation_id=?",
                              (snapshot.consolidation_id,))
                for ref in snapshot.read_set:
                    self._execute("INSERT INTO consolidation_read_set "
                                  "(consolidation_id,kind,material_id,session_id,fingerprint,memory_at) "
                                  "VALUES (?,?,?,?,?,?)", (snapshot.consolidation_id, ref.kind, ref.material_id,
                                                          ref.session_id, ref.fingerprint, ref.memory_at))
            return self._receipt(snapshot.consolidation_id, snapshot.scope.user_id)

    def _insert_consolidated(self, memory: CodingMemory, snapshot: ConsolidationSnapshot) -> None:
        scope = snapshot.scope
        if (memory.consolidation_id != snapshot.consolidation_id or memory.session_id is not None
                or memory.memory_at is not None or any(getattr(memory, key) != getattr(scope, key)
                for key in ("user_id", "agent_id", "source", "workspace_id", "branch"))):
            raise ValueError("invalid consolidated product scope")
        data = memory.to_dict()
        for key in ("search_keys", "files", "source_fact_ids", "evidence_refs"):
            data[key] = canonical(data[key])
        columns = list(data)
        self._execute(f"INSERT INTO coding_memory_meta ({','.join(columns)}) "
                      f"VALUES ({','.join('?' for _ in columns)})", [data[c] for c in columns])

    def __init__(self) -> None:
        self._conn = None
        self._trace_conn = None
        self._lock = threading.RLock()
        self._trace_lock = threading.RLock()

    def _execute(self, sql: str, params=(), *, trace: bool = False):
        connection = self._trace_conn if trace else self._conn
        if connection is None:
            raise RuntimeError("Business repository is not initialized")
        return connection.execute(sql, params)

    def close(self) -> None:
        for lock, attribute in ((self._lock, "_conn"), (self._trace_lock, "_trace_conn")):
            with lock:
                connection = getattr(self, attribute)
                if connection is not None:
                    connection.close()
                    setattr(self, attribute, None)

    def list_by_workspace(
        self, user_id: str, workspace_id: str, branch: Optional[str],
        limit: int, offset: int,
        include_sources: Optional[List[str]] = None,
        exclude_sources: Optional[List[str]] = None,
    ) -> List["CodingMemory"]:
        scope_sql, scope_params = _scope_where(workspace_id, branch)
        source_sql, source_params = _source_where(include_sources, exclude_sources)
        with self._lock:
            assert self._conn is not None
            sql = (f"SELECT * FROM coding_memory_meta WHERE user_id = ? AND {scope_sql} "
                   f"AND {source_sql} AND {_VISIBLE} "
                   f"ORDER BY updated_at DESC, experience_id ASC LIMIT ? OFFSET ?")
            params: List[Any] = ([user_id] + scope_params + source_params
                                 + [limit, offset])
            cur = self._execute(sql, params)
            return [_row_to_memory(r) for r in cur.fetchall()]

    def stats(
        self, user_id: str, workspace_id: Optional[str], branch: Optional[str],
        include_sources: Optional[List[str]] = None,
        exclude_sources: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        scope_sql, scope_params = _scope_where(workspace_id, branch)
        source_sql, source_params = _source_where(include_sources, exclude_sources)
        with self._lock:
            assert self._conn is not None
            row = self._execute(
                f"SELECT COUNT(*) AS n, MAX(created_at) AS created, "
                f"MAX(updated_at) AS updated FROM coding_memory_meta "
                f"WHERE user_id = ? AND {scope_sql} AND {source_sql} AND {_VISIBLE}",
                [user_id] + scope_params + source_params,
            ).fetchone()
            facts = self._execute(
                f"SELECT COUNT(*) AS n FROM working_fact "
                f"WHERE user_id = ? AND status = 'active' AND {scope_sql} "
                f"AND {source_sql}",
                [user_id] + scope_params + source_params,
            ).fetchone()
        return {
            "count": int(row["n"] or 0),
            "latest_created_at": _parse_dt(row["created"]) if row["created"] else None,
            "latest_updated_at": _parse_dt(row["updated"]) if row["updated"] else None,
            "facts": int(facts["n"] or 0),
        }

    def upsert_experience(self, memory: CodingMemory) -> None:
        with self._lock, self._changing_source("experience", memory.experience_id) as previous:
            if memory.consolidation_id or (previous and previous.consolidation_id):
                raise ValueError("consolidated experiences cannot be overwritten")
            assert self._conn is not None
            self._execute(
                """
                INSERT INTO coding_memory_meta (
                    experience_id, user_id, agent_id, task, search_keys, solution,
                    boundary_envs, workspace_id, branch, session_id,
                    files, source_fact_ids, confidence, source, extraction_source,
                    memory_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(experience_id) DO UPDATE SET
                    user_id         = excluded.user_id,
                    agent_id        = excluded.agent_id,
                    task            = excluded.task,
                    search_keys     = excluded.search_keys,
                    solution        = excluded.solution,
                    boundary_envs   = excluded.boundary_envs,
                    workspace_id    = excluded.workspace_id,
                    branch          = excluded.branch,
                    session_id      = excluded.session_id,
                    files           = excluded.files,
                    source_fact_ids = excluded.source_fact_ids,
                    confidence      = excluded.confidence,
                    source          = excluded.source,
                    extraction_source = excluded.extraction_source,
                    memory_at       = excluded.memory_at,
                    updated_at      = excluded.updated_at
                """,
                (
                    memory.experience_id, memory.user_id, memory.agent_id,
                    memory.task, json.dumps(memory.search_keys, ensure_ascii=False),
                    memory.solution, memory.boundary_envs,
                    memory.workspace_id, memory.branch, memory.session_id,
                    json.dumps(memory.files, ensure_ascii=False),
                    json.dumps(memory.source_fact_ids, ensure_ascii=False),
                    memory.confidence, memory.source, memory.extraction_source,
                    _to_epoch(memory.memory_at),
                    (memory.created_at or datetime.utcnow()).isoformat(),
                    (memory.updated_at or datetime.utcnow()).isoformat(),
                ),
            )

    def delete_experience(self, experience_id: str, user_id: str) -> None:
        with self._lock:
            previous = self._load_material("experience", experience_id)
            if previous is None or previous.user_id != user_id:
                return
            if previous.consolidation_id:
                # Derived withdrawal takes the group lock before touching products.
                with self._business_transaction():
                    receipt = self._receipt(previous.consolidation_id, user_id)
                    self._lock_groups([receipt.group_key])
                    receipt = self._receipt(previous.consolidation_id, user_id)
                    withdrawn = sorted(set(receipt.withdrawn_ids + [experience_id]))
                    self._execute("DELETE FROM coding_memory_meta WHERE experience_id=? AND user_id=?",
                                  (experience_id, user_id))
                    self._execute("UPDATE consolidation_receipt SET withdrawn_ids=? WHERE consolidation_id=?",
                                  (canonical(withdrawn), previous.consolidation_id))
            else:
                with self._changing_source("experience", experience_id):
                    self._execute("DELETE FROM coding_memory_meta WHERE experience_id=? AND user_id=?",
                                  (experience_id, user_id))

    def delete_fact(self, fact_id: str, user_id: str) -> None:
        with self._lock, self._changing_source("fact", fact_id):
            assert self._conn is not None
            self._execute(
                "DELETE FROM working_fact WHERE fact_id = ? AND user_id = ?",
                (fact_id, user_id),
            )

    def get_experience(
        self, experience_id: str, user_id: Optional[str]
    ) -> Optional[CodingMemory]:
        with self._lock:
            assert self._conn is not None
            if user_id:
                cur = self._execute(
                    "SELECT * FROM coding_memory_meta WHERE experience_id = ? AND user_id = ? AND " + _VISIBLE,
                    (experience_id, user_id),
                )
            else:
                cur = self._execute(
                    "SELECT * FROM coding_memory_meta WHERE experience_id = ? AND " + _VISIBLE,
                    (experience_id,),
                )
            row = cur.fetchone()
            return _row_to_memory(row) if row else None

    def get_experiences(
        self, experience_ids: List[str], user_id: Optional[str],
        include_sources: Optional[List[str]], exclude_sources: Optional[List[str]],
    ) -> List[CodingMemory]:
        if not experience_ids:
            return []
        with self._lock:
            assert self._conn is not None
            placeholders = ",".join("?" * len(experience_ids))
            if user_id:
                sql = f"SELECT * FROM coding_memory_meta WHERE experience_id IN ({placeholders}) AND user_id = ?"
                params = list(experience_ids) + [user_id]
            else:
                sql = f"SELECT * FROM coding_memory_meta WHERE experience_id IN ({placeholders})"
                params = list(experience_ids)
            if include_sources:
                marks = ",".join("?" * len(include_sources))
                sql += f" AND source IN ({marks})"
                params.extend(include_sources)
            if exclude_sources:
                marks = ",".join("?" * len(exclude_sources))
                sql += f" AND source NOT IN ({marks})"
                params.extend(exclude_sources)
            sql += " AND " + _VISIBLE
            cur = self._execute(sql, params)
            rows = cur.fetchall()
            # 保留输入顺序
            by_id = {r["experience_id"]: _row_to_memory(r) for r in rows}
            return [by_id[eid] for eid in experience_ids if eid in by_id]

    def scoped_experience_ids(
        self,
        user_id: str,
        agent_ids: Optional[List[str]],
        session_ids: Optional[List[str]],
        workspace_id: Optional[str],
        branch: Optional[str],
    ) -> Tuple[Set[str], int]:
        """返回 (落在授权作用域内的 experience_id 集合, 该 user 的经验总数)。

        一次查询同时拿到过滤结果和选择率分母：前者供向量命中做交集，后者定放大倍数。
        agent_ids / session_ids 为空表示该维度不筛；非空按 IN 精确匹配。
        """
        scope_sql, scope_params = _scope_where(workspace_id, branch)
        agent_sql, agent_params = _in_clause("agent_id", agent_ids)
        if agent_sql:
            scope_sql = f"({agent_sql}) AND ({scope_sql})"
            scope_params = [*agent_params, *scope_params]
        if session_ids:
            session_sql, session_params = _in_clause("session_id", session_ids)
            marks = ",".join("?" for _ in session_ids)
            scope_sql = (f"({scope_sql}) AND ((consolidation_id IS NULL AND {session_sql}) OR "
                         "(consolidation_id IS NOT NULL AND NOT EXISTS ("
                         "SELECT 1 FROM consolidation_read_set rs "
                         "WHERE rs.consolidation_id=coding_memory_meta.consolidation_id "
                         f"AND rs.session_id NOT IN ({marks}))))")
            scope_params += session_params + list(session_ids)
        with self._lock:
            assert self._conn is not None
            cur = self._execute(
                f"SELECT experience_id, ({scope_sql}) AS in_scope "
                f"FROM coding_memory_meta WHERE user_id = ? AND {_VISIBLE}",
                scope_params + [user_id],
            )
            rows = cur.fetchall()
        return {r["experience_id"] for r in rows if r["in_scope"]}, len(rows)

    def count_active_facts(
        self,
        user_id: str,
        agent_ids: Optional[List[str]],
        session_ids: Optional[List[str]],
        workspace_id: Optional[str],
        branch: Optional[str],
    ) -> Tuple[int, int]:
        """返回 (作用域内 active 事实数, 该 user 的 active 事实总数)。

        事实层的过滤在 get_facts 拿到对象之后用 _scope_matches 做，这里只要选择率。
        agent_ids / session_ids 为空表示该维度不筛；非空按 IN 精确匹配。
        """
        scope_sql, scope_params = _scope_where(workspace_id, branch)
        agent_sql, agent_params = _in_clause("agent_id", agent_ids)
        if agent_sql:
            scope_sql = f"({agent_sql}) AND ({scope_sql})"
            scope_params = [*agent_params, *scope_params]
        session_sql, session_params = _in_clause("session_id", session_ids)
        if session_sql:
            scope_sql = f"({session_sql}) AND ({scope_sql})"
            scope_params = [*session_params, *scope_params]
        with self._lock:
            assert self._conn is not None
            row = self._execute(
                f"SELECT COUNT(*) AS total, "
                f"SUM(CASE WHEN {scope_sql} THEN 1 ELSE 0 END) AS scoped "
                f"FROM working_fact WHERE user_id = ? AND status = 'active'",
                scope_params + [user_id],
            ).fetchone()
        return int(row["scoped"] or 0), int(row["total"] or 0)

    def upsert_fact(self, fact: WorkingFact) -> None:
        with self._lock, self._changing_source("fact", fact.fact_id):
            assert self._conn is not None
            self._execute(
                """
                INSERT INTO working_fact (
                    fact_id, user_id, agent_id, session_id, workspace_id, branch,
                    source, statement, files, subject, details,
                    fact_link, derived_experience_ids,
                    status, fact_chain_summary, memory_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(fact_id) DO UPDATE SET
                    user_id                = excluded.user_id,
                    agent_id               = excluded.agent_id,
                    session_id             = excluded.session_id,
                    workspace_id           = excluded.workspace_id,
                    branch                 = excluded.branch,
                    source                 = excluded.source,
                    statement              = excluded.statement,
                    files                  = excluded.files,
                    subject                = excluded.subject,
                    details                = excluded.details,
                    fact_link              = excluded.fact_link,
                    derived_experience_ids = excluded.derived_experience_ids,
                    status                 = excluded.status,
                    fact_chain_summary     = excluded.fact_chain_summary,
                    memory_at              = excluded.memory_at
                """,
                (
                    fact.fact_id, fact.user_id, fact.agent_id,
                    fact.session_id, fact.workspace_id, fact.branch, fact.source,
                    fact.statement,
                    json.dumps(fact.files, ensure_ascii=False),
                    fact.subject or "",
                    fact.details or "",
                    fact.fact_link,
                    json.dumps(fact.derived_experience_ids, ensure_ascii=False),
                    fact.status,
                    fact.fact_chain_summary or "",
                    _to_epoch(fact.memory_at),
                    (fact.created_at or datetime.utcnow()).isoformat(),
                ),
            )

    def get_facts(
        self, fact_ids: List[str], user_id: Optional[str]
    ) -> List[WorkingFact]:
        if not fact_ids:
            return []
        with self._lock:
            assert self._conn is not None
            placeholders = ",".join("?" * len(fact_ids))
            sql = f"SELECT * FROM working_fact WHERE fact_id IN ({placeholders})"
            params: List[Any] = list(fact_ids)
            if user_id:
                sql += " AND user_id = ?"
                params.append(user_id)
            cur = self._execute(sql, params)
            by_id = {r["fact_id"]: _row_to_fact(r) for r in cur.fetchall()}
            # 保留输入顺序
            return [by_id[fid] for fid in fact_ids if fid in by_id]

    def get_fact_ancestors(
        self, fact_id: str, user_id: Optional[str], max_depth: int
    ) -> List[WorkingFact]:
        with self._lock:
            assert self._conn is not None
            sql = "SELECT * FROM working_fact WHERE fact_link = ?"
            if user_id:
                sql += " AND user_id = ?"
            # 同一条被顶掉两次不该发生（supersede 只对 active 生效），真撞上就取最早的
            sql += " ORDER BY created_at ASC LIMIT 1"

            out: List[WorkingFact] = []
            seen = {fact_id}
            cur_id = fact_id
            for _ in range(max_depth):
                params = [cur_id] + ([user_id] if user_id else [])
                row = self._execute(sql, params).fetchone()
                if row is None:
                    break
                parent = _row_to_fact(row)
                if parent.fact_id in seen:
                    logger.warning(
                        f"[coding-store] fact_link cycle at {parent.fact_id}, stop walking"
                    )
                    break
                seen.add(parent.fact_id)
                out.append(parent)
                cur_id = parent.fact_id
            out.reverse()          # 反查是由近及远，调用方要时间正序
            return out

    def supersede_fact(self, old_fact_id: str, new_fact_id: str) -> bool:
        with self._lock:
            assert self._conn is not None
            cur = self._execute(
                "UPDATE working_fact SET status = 'superseded', fact_link = ? "
                "WHERE fact_id = ? AND status = 'active'",
                (new_fact_id, old_fact_id),
            )
            return cur.rowcount > 0

    def set_fact_derived_experiences(self, fact_id: str, experience_ids: List[str]) -> None:
        self._set_links("working_fact", "fact_id", fact_id, "derived_experience_ids", experience_ids)

    def set_experience_source_facts(self, experience_id: str, fact_ids: List[str]) -> None:
        self._set_links("coding_memory_meta", "experience_id", experience_id, "source_fact_ids", fact_ids)

    def _set_links(
        self, table: str, key_column: str, key: str, column: str, value: List[str]
    ) -> None:
        with self._lock:
            if table == "coding_memory_meta":
                memory = self._load_material("experience", key)
                if memory and memory.consolidation_id:
                    raise ValueError("consolidated experiences cannot receive source backfill")
            assert self._conn is not None
            self._execute(
                f"UPDATE {table} SET {column} = ? WHERE {key_column} = ?",
                (json.dumps(value, ensure_ascii=False), key),
            )

    def list_user_tasks(self, user_id: str, limit: int) -> List[str]:
        with self._lock:
            assert self._conn is not None
            cur = self._execute(
                f"SELECT task FROM coding_memory_meta WHERE user_id = ? AND {_VISIBLE} "
                "ORDER BY updated_at DESC LIMIT ?",
                (user_id, limit),
            )
            return [r["task"] for r in cur.fetchall()]

    def append_trace(
        self,
        request_id: str,
        user_id: str,
        agent_id: str,
        session_id: Optional[str],
        workspace_id: Optional[str],
        branch: Optional[str],
        source: str,
        payload: List[Dict[str, Any]],
    ) -> None:
        with self._trace_lock, self._trace_transaction(user_id, agent_id):
            assert self._trace_conn is not None
            self._execute_trace(
                "INSERT INTO coding_trace "
                "(request_id,user_id,agent_id,session_id,workspace_id,branch,source,messages_json,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(request_id) DO NOTHING",
                (
                    request_id, user_id, agent_id or "default_agent", session_id,
                    workspace_id, branch, source,
                    json.dumps(payload, ensure_ascii=False), _now_iso(),
                ),
            )
            # last-K 只需近期轨迹；保留 100 次 write 足够覆盖异常大的单-turn 分布，
            # 同时避免原始工具轨迹在业务库中无限增长。
            self._execute_trace(
                "DELETE FROM coding_trace WHERE user_id=? AND agent_id=? AND request_id NOT IN ("
                "SELECT request_id FROM coding_trace WHERE user_id=? AND agent_id=? "
                "ORDER BY created_at DESC, request_id DESC LIMIT 100)",
                (user_id, agent_id or "default_agent", user_id, agent_id or "default_agent"),
            )

    def recent_trace_payloads(
        self, user_id: str, agent_id: str, source: str, limit: int,
    ) -> List[List[Dict[str, Any]]]:
        with self._trace_lock:
            assert self._trace_conn is not None
            rows = self._execute_trace(
                "SELECT messages_json FROM coding_trace "
                "WHERE user_id=? AND agent_id=? AND source=? "
                "ORDER BY created_at DESC LIMIT ?",
                (user_id, agent_id or "default_agent", source, max(1, limit)),
            ).fetchall()
        out: List[List[Dict[str, Any]]] = []
        for row in reversed(rows):
            try:
                payload = json.loads(row["messages_json"] or "[]")
            except Exception:
                payload = []
            if isinstance(payload, list):
                out.append(payload)
        return out

    def _execute_trace(self, sql: str, params=()):
        return self._execute(sql, params, trace=True)
