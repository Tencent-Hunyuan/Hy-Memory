"""Working/coding relational schema and legacy SQLite migrations."""

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS coding_memory_meta (
    experience_id    TEXT PRIMARY KEY,
    user_id          TEXT NOT NULL,
    agent_id         TEXT NOT NULL DEFAULT 'default_agent',
    task             TEXT NOT NULL,
    search_keys      TEXT NOT NULL DEFAULT '[]',
    solution         TEXT NOT NULL,
    boundary_envs    TEXT NOT NULL DEFAULT '',
    workspace_id     TEXT,
    branch           TEXT,
    session_id       TEXT,
    files            TEXT NOT NULL DEFAULT '[]',
    source_fact_ids  TEXT NOT NULL DEFAULT '[]',
    confidence       REAL NOT NULL DEFAULT 0.7,
    source           TEXT NOT NULL DEFAULT 'default',
    extraction_source TEXT NOT NULL DEFAULT 'auto_extract',
    memory_at        INTEGER,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
"""

# 事实层（episodic）。与经验表同库不同表；无 TTL，靠 SUPERSEDE 软删保新鲜。
_CREATE_FACT_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS working_fact (
    fact_id                TEXT PRIMARY KEY,
    user_id                TEXT NOT NULL,
    agent_id               TEXT NOT NULL DEFAULT 'default_agent',
    session_id             TEXT,
    workspace_id           TEXT,
    branch                 TEXT,
    source                 TEXT NOT NULL DEFAULT 'default',
    statement              TEXT NOT NULL,
    files                  TEXT NOT NULL DEFAULT '[]',
    subject                TEXT NOT NULL DEFAULT '',
    details                TEXT NOT NULL DEFAULT '',
    fact_link              TEXT,
    derived_experience_ids TEXT NOT NULL DEFAULT '[]',
    status                 TEXT NOT NULL DEFAULT 'active',
    fact_chain_summary     TEXT NOT NULL DEFAULT '',
    memory_at              INTEGER,
    created_at             TEXT NOT NULL
);
-- 反查前驱（谁的 fact_link 指向我）走这个索引，写演变总结时要顺着链往回走
CREATE INDEX IF NOT EXISTS idx_working_fact_link ON working_fact(fact_link);
"""

_CREATE_TRACE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS coding_trace (
    request_id    TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    agent_id      TEXT NOT NULL DEFAULT 'default_agent',
    session_id    TEXT,
    workspace_id  TEXT,
    branch        TEXT,
    source        TEXT NOT NULL DEFAULT 'default',
    messages_json TEXT NOT NULL DEFAULT '[]',
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trace_user_created
    ON coding_trace(user_id, agent_id, source, created_at DESC);
"""

_INDEX_NAMES = ("idx_coding_user", "idx_coding_user_workspace", "idx_coding_updated")

_CREATE_INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_coding_user ON coding_memory_meta(user_id);",
    "CREATE INDEX IF NOT EXISTS idx_coding_user_workspace ON coding_memory_meta(user_id, workspace_id);",
    "CREATE INDEX IF NOT EXISTS idx_coding_user_source ON coding_memory_meta(user_id, source);",
    "CREATE INDEX IF NOT EXISTS idx_coding_updated ON coding_memory_meta(updated_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_fact_user ON working_fact(user_id);",
    "CREATE INDEX IF NOT EXISTS idx_fact_user_status ON working_fact(user_id, status);",
    "CREATE INDEX IF NOT EXISTS idx_fact_user_workspace ON working_fact(user_id, workspace_id);",
    "CREATE INDEX IF NOT EXISTS idx_fact_user_source ON working_fact(user_id, source);",
    "CREATE INDEX IF NOT EXISTS idx_fact_created ON working_fact(created_at DESC);",
]

# 旧 schema：主键叫 memory_id，且带一个不再使用的 boundary_scope 列。
_MIGRATE_COPY_SQL = """
INSERT INTO coding_memory_meta (
    experience_id, user_id, agent_id, task, search_keys, solution,
    boundary_envs, workspace_id, branch, session_id,
    files, confidence, source, created_at, updated_at
)
SELECT
    memory_id, user_id, agent_id, task, search_keys, solution,
    boundary_envs, workspace_id, branch, session_id,
    files, confidence, source, created_at, updated_at
FROM coding_memory_meta_legacy;
"""

# 后加的列：老库用 ADD COLUMN 补，不必重建表
_ADDED_COLUMNS = {
    "coding_memory_meta": {
        "source_fact_ids": "TEXT NOT NULL DEFAULT '[]'",
        "extraction_source": "TEXT NOT NULL DEFAULT 'auto_extract'",
        # 对话时间，秒级 epoch（同 chat）。老库里的行是 NULL：那时没存过。
        "memory_at": "INTEGER",
    },
    # 早期版本存过一列 fact_chain（机械累积的历代说法），已废弃：同样的信息顺着
    # fact_link 反查就有。老库里那一列留着不管，有默认值不影响插入。
    "working_fact": {
        "fact_chain_summary": "TEXT NOT NULL DEFAULT ''",
        # 事实层比经验层晚补这一列，老库里已有的行会是 NULL。作用域过滤按
        # "NULL = 没记录分支"处理，不当成"记录了别的分支"，见 _scope_where。
        "branch": "TEXT",
        # 覆盖闸门的硬锚点，和它配套的可寻址原文。老库里的行是空串：subject 空
        # 就退回纯语义 + LLM 判覆盖（改动前的行为），details 空就只有摘要可读。
        "subject": "TEXT NOT NULL DEFAULT ''",
        "details": "TEXT NOT NULL DEFAULT ''",
        "source": "TEXT NOT NULL DEFAULT 'default'",
        "memory_at": "INTEGER",
    },
}
