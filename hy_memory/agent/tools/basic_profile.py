"""
基础画像（L0_BASIC_INFO）upsert 模块。

把用户稳定的结构化属性（name / age / location / occupation / employer，可配置）
合并写入 L0_BASIC_INFO memory 演化链。

特点：
- 每次更新只存 diff 字段，创建新版本节点。
- 旧版本标记为 superseded，通过 supersedes/superseded_by 链关联。
- 用户通过 get_user_profile() 单独读取 L0_BASIC_INFO；Search 不再返回 L0。
- 字段表（fields schema）由 MemoryConfig.basic_profile.fields 配置。

设计变更（2026-06）：
- 删除原 LLM function-calling tool `update_basic_user_profile`（弱模型乱填）。
- 改为：extractor prompt 直接要求 LLM 在 JSON 输出中返回 basic_info；writer
  收到 basic_info 后调用本模块 upsert_basic_profile() 入库。
- 字段表可配置，效果不绑死 5 个固定字段。

公共 API：
- upsert_basic_profile(...)        — 主入口，writer 直接调
- basic_info_node_id(...)          — deterministic id（仍保留，便于回退查询）
- render_l0_evolution_chain(...)   — L0 演化链渲染
"""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
import uuid
import weakref
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ================================================================
# 默认字段顺序（用于 _render_content 内容拼接顺序）
# 注：实际接受哪些字段由调用方传入 allowed_fields 决定。
# ================================================================

BASIC_FIELDS: List[str] = ["name", "age", "location", "occupation", "employer"]


# ================================================================
# deterministic node id（保留，便于回退查询历史数据）
# ================================================================

_BASIC_INFO_NAMESPACE = uuid.UUID("00000000-0000-0000-0000-000000babb1e")  # stable sentinel
USER_LEVEL_AGENT_ID = ""
USER_LEVEL_SESSION_ID = ""

# 同进程、同事件循环内串行化同一用户的 L0 更新，避免并发请求同时创建多个
# latest 链头。Lock 按 event loop 分桶，防止多 client/loop 复用同一 Lock 报错。
# 向量库没有跨进程事务/CAS，因此召回侧仍会对历史或跨副本分叉做防御性折叠。
_LOOP_USER_LOCKS = weakref.WeakKeyDictionary()


def _user_lock(user_id: str) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    locks = _LOOP_USER_LOCKS.get(loop)
    if locks is None:
        locks = weakref.WeakValueDictionary()
        _LOOP_USER_LOCKS[loop] = locks
    lock = locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        locks[user_id] = lock
    return lock


def basic_info_node_id(user_id: str) -> str:
    """返回只由 user_id 决定的 user-level L0 deterministic id。"""
    key = f"basic_info::{user_id or 'default'}"
    return str(uuid.uuid5(_BASIC_INFO_NAMESPACE, key))


# ================================================================
# content 渲染
# ================================================================

def _render_content(kv: Dict[str, Any], field_order: Optional[List[str]] = None) -> str:
    """
    把 KV 渲染成人类可读的自然语言串。
    与其他 memory 保持 "The user's ..." 风格一致。

    field_order: 可选，控制字段拼接顺序。未指定时按 kv 字典序。
    """
    order = field_order or sorted(kv.keys())
    parts: List[str] = []
    for key in order:
        if key not in kv:
            continue
        value = kv[key]
        if value is None:
            continue
        s = str(value).strip()
        if not s:
            continue
        parts.append(f"{key} is {s}")
    if not parts:
        return ""
    return "The user's " + ", ".join(parts) + "."


def render_l0_evolution_chain(l0_nodes: list) -> str:
    """
    按属性分组、时间正序展示 L0 全部历史版本。

    输入：L0_BASIC_INFO 节点列表（含 superseded 的旧版本）
    输出：格式化的字符串，例如：
        用户姓名: 张三
        用户年龄: (2026-01-15 用户称年龄25) → (2026-05-10 用户称年龄26)
        用户职业: (2026-01-15 用户说自己是程序员) → (2026-03-20 用户说自己是架构师)
    """
    from datetime import datetime as _dt

    # 按时间正序排列
    sorted_nodes = sorted(
        l0_nodes,
        key=lambda n: (getattr(n, 'gmt_created', None) or getattr(n, 'valid_from', None) or _dt.min),
    )

    # 收集每个属性的历史版本（动态发现 key，不再绑死 BASIC_FIELDS）
    field_history: Dict[str, List[tuple]] = {}
    for node in sorted_nodes:
        kv = {}
        if hasattr(node, 'custom') and isinstance(node.custom, dict):
            kv = node.custom.get("basic_info_kv") or {}
        ts = getattr(node, 'gmt_created', None) or getattr(node, 'valid_from', None)
        ts_str = ts.strftime("%Y-%m-%d") if ts else "unknown"
        for field_name, val in kv.items():
            field_history.setdefault(field_name, []).append((ts_str, val))

    # 渲染：内置 5 字段使用中文 label，其余使用原 key
    field_labels = {
        "name": "用户姓名",
        "age": "用户年龄",
        "location": "用户所在地",
        "occupation": "用户职业",
        "employer": "用户雇主",
    }
    # 顺序：先 BASIC_FIELDS 中已知的，再剩下的按字母序
    ordered_keys: List[str] = [k for k in BASIC_FIELDS if k in field_history]
    for k in sorted(field_history.keys()):
        if k not in ordered_keys:
            ordered_keys.append(k)

    lines = []
    for field_name in ordered_keys:
        history = field_history[field_name]
        if not history:
            continue
        label = field_labels.get(field_name, field_name)
        if len(history) == 1:
            lines.append(f"{label}: {history[0][1]}")
        else:
            chain = " → ".join(f"({ts} {v})" for ts, v in history)
            lines.append(f"{label}: {chain}")

    return "\n".join(lines) if lines else ""


# ================================================================
# 输入 sanitize
# ================================================================

def _sanitize_kv(
    kv: Dict[str, Any],
    allowed_fields: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    只保留 allowed_fields 中的 key；丢弃 null / 空串 / "null" / "none"。
    age 字段强制 int。
    """
    if allowed_fields is None:
        allowed_fields = BASIC_FIELDS
    allowed_set = set(allowed_fields)

    result: Dict[str, Any] = {}
    for k, v in kv.items():
        if k not in allowed_set:
            continue
        if v is None:
            continue
        if isinstance(v, str):
            s = v.strip()
            if not s or s.lower() in ("null", "none"):
                continue
            result[k] = s
        elif isinstance(v, bool):
            # bool 是 int 子类，特判排在前面
            result[k] = "yes" if v else "no"
        elif isinstance(v, int):
            result[k] = int(v)
        elif isinstance(v, float):
            try:
                if k == "age":
                    result[k] = int(v)
                else:
                    result[k] = float(v)
            except (TypeError, ValueError):
                continue
        else:
            # 其他非预期类型（list/dict）忽略
            continue
    return result


# ================================================================
# 占位符守卫（P0-1）
# ================================================================
# 弱模型（如 qwen3.5-2b）在用户什么都没说时，会把 schema 字段说明里的词当值
# 填进 basic_info：{"name": "用户"} / "The user" / "N/A"。每个占位符都会
# supersede 掉真实值，演化链变成 费天翔 → Fei → 用户 → The user。
# 规则：占位符永远不入库；读侧组装 KV 时占位符也不覆盖已有真实值。

_PLACEHOLDER_VALUES = frozenset({
    "用户", "该用户", "本用户", "用户本人",
    "the user", "the user's", "user", "a user", "this user",
    "n/a", "na", "none", "null", "nil", "unknown", "unspecified",
    "not provided", "not specified", "not mentioned",
    "未提供", "未知", "不详", "未说明", "未提及", "无",
    "-", "?",
    # 其他提示词语言里的「用户」/「未知」（显式 language 时模型可能照抄）
    "使用者", "該使用者", "用戶", "該用戶", "不詳", "無",
    "ユーザー", "ユーザ", "不明", "なし",
    "사용자", "유저", "알 수 없음", "없음",
    "el usuario", "usuario", "la usuaria", "desconocido", "ninguno",
    "der nutzer", "nutzer", "der benutzer", "benutzer", "unbekannt", "keine",
    "o usuário", "usuário", "o utilizador", "utilizador", "desconhecido", "nenhum",
    "l'utilisateur", "utilisateur", "inconnu", "aucun",
    "пользователь", "неизвестно", "нет",
    "pengguna", "tidak diketahui", "tidak ada",
})

# 内置字段的中文标签：弱模型也会把字段说明的中文词当值。
_FIELD_LABELS: Dict[str, tuple] = {
    "name": ("姓名", "名字", "用户名", "用户姓名", "用户名字"),
    "age": ("年龄", "用户年龄"),
    "location": ("所在地", "地点", "城市", "用户所在地"),
    "occupation": ("职业", "工作", "用户职业"),
    "employer": ("雇主", "公司", "用户雇主"),
}

# 去掉标点/符号后再比较，让 "The user." / "用户。" / "N/A" 都命中。
# "-" / "?" 本身就是纯标点，剥完为空串，同样判为占位符。
_PUNCT_RE = re.compile(r"[\s\.,;:!?'\"`~()\[\]{}<>/\\|_\-\u3000-\u303f\uff00-\uff0f\uff1a-\uff20]+")


def _placeholder_key(value: str) -> str:
    s = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
    s = s.replace("\u2019", "'")
    s = _PUNCT_RE.sub(" ", s)
    return " ".join(s.split())


_PLACEHOLDER_KEYS = frozenset(_placeholder_key(v) for v in _PLACEHOLDER_VALUES)


def is_placeholder_value(field_name: str, value: Any) -> bool:
    """value 是否为占位符（空 / 用户 / The user / N/A / 字段名本身 等）。

    大小写不敏感、首尾空白与标点不敏感。数字（如 age=30）永远不是占位符。
    """
    if value is None:
        return True
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return False
    key = _placeholder_key(value)
    if not key:
        return True
    if key in _PLACEHOLDER_KEYS:
        return True
    field_key = _placeholder_key(field_name)
    if field_key and key in (field_key, field_key.replace("_", " ")):
        return True
    for label in _FIELD_LABELS.get(field_name, ()):
        if key == _placeholder_key(label):
            return True
    return False


def _drop_placeholders(kv: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in kv.items() if not is_placeholder_value(k, v)}


# ================================================================
# 公共入口：upsert_basic_profile
# ================================================================

@dataclass
class BasicProfileUpsertResult:
    """upsert 结果。failed 时 node_id="" 且 error 非 None。"""
    success: bool
    node_id: str = ""
    content: str = ""
    diff_kv: Dict[str, Any] = None  # type: ignore[assignment]
    changed: bool = False
    created: bool = False
    supersedes: Optional[str] = None
    reason: str = ""
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "node_id": self.node_id,
            "content": self.content,
            "diff_kv": self.diff_kv or {},
            "changed": self.changed,
            "created": self.created,
            "supersedes": self.supersedes,
            "reason": self.reason,
            "error": self.error,
        }


async def upsert_basic_profile(
    *,
    user_id: str,
    kv: Dict[str, Any],
    vector_store,
    embed_service,
    allowed_fields: Optional[List[str]] = None,
    client_meta: Optional[Dict[str, Any]] = None,
) -> BasicProfileUpsertResult:
    """按 user_id 串行更新全局唯一的 L0 Basic Info 链。"""
    normalized_user_id = (user_id or "").strip()
    if not normalized_user_id:
        return BasicProfileUpsertResult(success=False, error="user_id required")
    async with _user_lock(normalized_user_id):
        return await _upsert_basic_profile_unlocked(
            user_id=normalized_user_id,
            kv=kv,
            vector_store=vector_store,
            embed_service=embed_service,
            allowed_fields=allowed_fields,
            client_meta=client_meta,
        )


async def _upsert_basic_profile_unlocked(
    *,
    user_id: str,
    kv: Dict[str, Any],
    vector_store,
    embed_service,
    allowed_fields: Optional[List[str]] = None,
    client_meta: Optional[Dict[str, Any]] = None,
) -> BasicProfileUpsertResult:
    """
    把 LLM 提取出的 basic_info kv 入库到 user-level L0_BASIC_INFO 演化链。

    kv 中字段不在 allowed_fields 里的会被丢弃（防止 LLM 编出多余字段）。
    新版本节点：deterministic node_id，新 UUID。
    旧版本：is_latest=False, status=SUPERSEDED, superseded_by 指向新节点。

    client_meta 是调用方写入时透传的业务 meta。L0 每次变更都新建节点、旧节点转
    SUPERSEDED，与 writer 的 SUPERSEDE 同构，故同样只带本次 meta 不继承旧链，
    避免长链上无限累积。历史 meta 随旧节点留在演化链里仍可查。
    """
    user_id = (user_id or "").strip()
    # L0 Basic Info 从输入、唯一性到落盘都只属于 user_id。

    new_kv = _sanitize_kv(kv or {}, allowed_fields=allowed_fields)
    # 单调性守卫：占位符永远不覆盖已有值，过滤后为空就不写新版本、不产生新链头。
    new_kv = _drop_placeholders(new_kv)
    if not new_kv:
        return BasicProfileUpsertResult(
            success=True, changed=False, reason="no valid fields after sanitize"
        )

    # 按 user_id 一次取回全部 L0，再同时计算链头和完整 KV。旧实现连续执行
    # list_by_user(limit=100) 与 list_by_user(limit=1000)，远端 Qdrant 拥塞时一次
    # basic_info 更新会白白多等一轮网络请求。
    all_l0_nodes = await _list_l0_nodes(vector_store, user_id)
    latest_nodes = _latest_l0_heads_from_nodes(all_l0_nodes)
    existing_full_kv = _assemble_full_kv_from_nodes(
        all_l0_nodes, allowed_fields=allowed_fields,
    )

    diff_kv: Dict[str, Any] = {}
    for k, v in new_kv.items():
        old_v = existing_full_kv.get(k)
        if old_v is None or str(v) != str(old_v):
            diff_kv[k] = v

    # 历史 agent-level L0 或多链头即使没有字段变化，也创建一个 canonical user-level
    # 节点并 supersede 全部旧链头，完成惰性迁移和去重。
    needs_migration = (
        (not latest_nodes and bool(existing_full_kv))
        or len(latest_nodes) > 1
        or any(
            (getattr(n, "agent_id", "") or "") != USER_LEVEL_AGENT_ID
            for n in latest_nodes
        )
    )
    if not diff_kv and not needs_migration:
        return BasicProfileUpsertResult(
            success=True, changed=False, reason="no diff from existing"
        )

    stored_kv = diff_kv if diff_kv else existing_full_kv
    content = _render_content(stored_kv, field_order=allowed_fields)
    if not content:
        return BasicProfileUpsertResult(success=False, error="rendered content is empty")

    # 重算 embedding
    try:
        embedding = await embed_service.embed_queued(content)
    except Exception as e:
        return BasicProfileUpsertResult(success=False, error=f"embed failed: {e}")

    from ...models.memory import MemoryNode, MemoryLayer, MemoryStatus, SourceType

    new_node_id = str(uuid.uuid4())
    supersedes_ids = [n.node_id for n in latest_nodes if getattr(n, "node_id", "")]
    supersedes_id = supersedes_ids[0] if supersedes_ids else None

    node_custom: Dict[str, Any] = {"basic_info_kv": stored_kv}
    if needs_migration:
        node_custom["migrated_from_agent_level"] = True
    if client_meta:
        node_custom["meta"] = [dict(client_meta)]

    new_node = MemoryNode(
        node_id=new_node_id,
        user_id=user_id,
        agent_id=USER_LEVEL_AGENT_ID,
        session_id=USER_LEVEL_SESSION_ID,
        layer=MemoryLayer.L0_BASIC_INFO,
        content=content,
        # L0 走的是旁路写入（不经 MemoryWriter），显式补齐事件时间，保证
        # created_after 在所有后端都使用同一字段语义。
        memory_at=datetime.now(),
        source_type=SourceType.EXPLICIT,
        status=MemoryStatus.ACTIVE,
        is_latest=True,
        supersedes=supersedes_ids or None,
        embedding=embedding,
        custom=node_custom,
        tags=["basic_info"],
    )

    try:
        await vector_store.upsert(new_node)
    except Exception as e:
        return BasicProfileUpsertResult(success=False, error=f"upsert failed: {e}")

    # 把全部历史 active/latest 链头标记为 superseded，收敛为一个 user-level 链头。
    supersede_failures: List[str] = []
    for old_head in latest_nodes:
        try:
            await vector_store.update_payload(
                old_head.node_id,
                {
                    "is_latest": False,
                    "superseded_by": [new_node_id],
                    "status": MemoryStatus.SUPERSEDED.value,
                },
            )
        except Exception as e:
            supersede_failures.append(old_head.node_id)
            logger.warning(
                f"[basic_profile] failed to supersede old L0 node "
                f"{old_head.node_id}: {e}"
            )

    logger.info(
        f"[basic_profile] user={user_id} scope=user "
        f"new L0 version={new_node_id} "
        f"diff={diff_kv} supersedes={supersedes_ids} failures={supersede_failures}"
    )

    return BasicProfileUpsertResult(
        success=True,
        node_id=new_node_id,
        content=content,
        diff_kv=diff_kv,
        changed=True,
        created=True,
        supersedes=supersedes_id,
    )


# ================================================================
# 内部：查找 L0 节点 / 组装 full kv
# ================================================================

def _is_valid_l0_head(node) -> bool:
    """链头必须是合法 L0 节点：layer==L0_BASIC_INFO 且带 basic_info_kv 结构。

    防御 defense-in-depth：即便上游查询（list_by_user / get_by_id）因后端
    layer 过滤失效等原因返回了非 L0 节点（如 l1_raw），也不把它当链头去
    supersede，避免生成跨层脏链（raw 被串进 L0 演化链）。
    """
    from ...models.memory import MemoryLayer
    if node is None:
        return False
    if getattr(node, "layer", None) != MemoryLayer.L0_BASIC_INFO:
        return False
    custom = getattr(node, "custom", None)
    return isinstance(custom, dict) and "basic_info_kv" in custom


def _node_timestamp(node) -> float:
    value = getattr(node, "gmt_created", None) or getattr(node, "valid_from", None)
    if isinstance(value, datetime):
        try:
            return value.timestamp()
        except (OSError, ValueError):
            return 0.0
    return 0.0


async def _find_latest_l0_heads(vector_store, user_id: str) -> List[Any]:
    """按 user_id 返回全部合法 active L0 链头，canonical user-level 节点优先。"""
    nodes = await _list_l0_nodes(vector_store, user_id)
    return _latest_l0_heads_from_nodes(nodes)


async def _list_l0_nodes(vector_store, user_id: str) -> List[Any]:
    """一次取回用户的全部 L0 节点，供 head 选择和 KV 组装共同复用。"""
    from ...models.memory import MemoryLayer
    return await vector_store.list_by_user(
        user_id=user_id,
        layers=[MemoryLayer.L0_BASIC_INFO],
        limit=1000,
        with_vectors=False,
    )


def _latest_l0_heads_from_nodes(nodes: List[Any]) -> List[Any]:
    from ...models.memory import MemoryStatus
    active = [
        n for n in nodes
        if getattr(n, "status", None) == MemoryStatus.ACTIVE
    ]
    valid = [n for n in active if _is_valid_l0_head(n)]
    heads = [n for n in valid if getattr(n, "is_latest", False)] or valid
    heads.sort(
        key=lambda n: (
            (getattr(n, "agent_id", "") or "") == USER_LEVEL_AGENT_ID,
            _node_timestamp(n),
        ),
        reverse=True,
    )
    return heads


async def _find_latest_l0(vector_store, user_id: str):
    """返回该用户唯一/优先 L0 链头。"""
    heads = await _find_latest_l0_heads(vector_store, user_id)
    if heads:
        return heads[0]

    # 极旧后端可能无法通过 list_by_user 找到 canonical deterministic 节点。
    legacy = await vector_store.get_by_id(basic_info_node_id(user_id))
    if _is_valid_l0_head(legacy):
        return legacy
    return None



async def _assemble_full_kv(
    vector_store,
    user_id: str,
    allowed_fields: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """按 user_id 跨历史 Agent 链组装完整 KV。"""
    all_nodes = await _list_l0_nodes(vector_store, user_id)
    return _assemble_full_kv_from_nodes(all_nodes, allowed_fields=allowed_fields)


def _assemble_full_kv_from_nodes(
    all_nodes: List[Any],
    *,
    allowed_fields: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """由已读取的 L0 节点组装完整 KV，不再触发第二次远端查询。"""
    all_nodes.sort(key=_node_timestamp)

    allowed_set = set(allowed_fields) if allowed_fields else None
    full_kv: Dict[str, Any] = {}
    for n in all_nodes:
        if not isinstance(n.custom, dict):
            continue
        kv = n.custom.get("basic_info_kv") or {}
        if not isinstance(kv, dict):
            continue
        for k, v in kv.items():
            if allowed_set is not None and k not in allowed_set:
                continue
            # 历史上已落盘的占位符版本（如 name=用户）不覆盖更早的真实值。
            if is_placeholder_value(k, v):
                continue
            full_kv[k] = v
    return full_kv
