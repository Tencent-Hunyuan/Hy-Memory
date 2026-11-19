"""
Agent Memory - KVStore 键值存储

提供键值对的快速存取能力。

功能：
- 键值存取（get/set/delete）
- 批量操作
- TTL 支持
- 多后端支持（Redis、RocksDB、Memory）

用途：
- 存储用户画像
- 存储任务状态
- 缓存热点数据

示例：
    kv = KVStore(config)
    
    # 存储
    kv.set("profile:user_123", {"name": "张三", "age": 30})
    
    # 获取
    profile = kv.get("profile:user_123")
    
    # 设置 TTL
    kv.set("session:xxx", {"token": "..."}, ttl=3600)
"""

from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
import json
import logging

logger = logging.getLogger(__name__)


class KVBackend(Enum):
    """KV 存储后端"""
    REDIS = "redis"
    ROCKSDB = "rocksdb"
    MEMORY = "memory"       # 内存存储


@dataclass
class KVStoreConfig:
    """
    KV 存储配置
    """
    backend: str = "memory"
    
    # Redis 配置
    host: str = "localhost"
    port: int = 6379
    db: int = 0
    password: str = ""
    
    # 通用配置
    prefix: str = "agentmem:"
    default_ttl: int = 0             # 0 表示永不过期
    max_size: int = 100000           # 最大键数量


@dataclass
class KVEntry:
    """KV 条目（含过期时间）"""
    value: Any
    expires_at: Optional[datetime] = None
    
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.now() > self.expires_at


class KVStore:
    """
    键值存储
    
    提供简单的键值存取能力。
    """
    
    def __init__(self, config: Optional[KVStoreConfig] = None):
        """
        初始化 KV 存储
        
        Args:
            config: 存储配置
        """
        self.config = config or KVStoreConfig()
        self._client = None
        
        self._init_client()
        logger.info(f"KVStore initialized, backend={self.config.backend}")
    
    def _init_client(self) -> None:
        """初始化存储客户端"""
        if self.config.backend == "redis":
            self._init_redis()
        elif self.config.backend == "memory":
            self._init_memory()
        else:
            raise ValueError(f"Unsupported backend: {self.config.backend}")
    
    def _init_redis(self) -> None:
        """初始化 Redis 客户端"""
        try:
            import redis
            
            self._client = redis.Redis(
                host=self.config.host,
                port=self.config.port,
                db=self.config.db,
                password=self.config.password or None,
                decode_responses=True,
            )
            # 测试连接
            self._client.ping()
            
        except ImportError:
            raise ImportError("redis is required. Install with: pip install redis")
    
    def _init_memory(self) -> None:
        """初始化内存存储"""
        self._memory_store: Dict[str, KVEntry] = {}
    
    def _make_key(self, key: str) -> str:
        """生成带前缀的键"""
        return f"{self.config.prefix}{key}"
    
    def get(self, key: str, default: Any = None) -> Any:
        """
        获取值
        
        Args:
            key: 键
            default: 默认值
        
        Returns:
            值或默认值
        """
        full_key = self._make_key(key)
        
        if self.config.backend == "memory":
            entry = self._memory_store.get(full_key)
            if entry is None:
                return default
            if entry.is_expired():
                del self._memory_store[full_key]
                return default
            return entry.value
        
        elif self.config.backend == "redis":
            value = self._client.get(full_key)
            if value is None:
                return default
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value
        
        return default
    
    def set(
        self,
        key: str,
        value: Any,
        ttl: int = None,
    ) -> bool:
        """
        设置值
        
        Args:
            key: 键
            value: 值
            ttl: 过期时间（秒），None 使用默认值
        
        Returns:
            是否成功
        """
        full_key = self._make_key(key)
        ttl = ttl if ttl is not None else self.config.default_ttl
        
        if self.config.backend == "memory":
            # 检查大小限制
            if len(self._memory_store) >= self.config.max_size:
                self._evict_expired()
            
            expires_at = None
            if ttl > 0:
                expires_at = datetime.now() + timedelta(seconds=ttl)
            
            self._memory_store[full_key] = KVEntry(
                value=value,
                expires_at=expires_at,
            )
            return True
        
        elif self.config.backend == "redis":
            value_str = json.dumps(value) if not isinstance(value, str) else value
            if ttl > 0:
                self._client.setex(full_key, ttl, value_str)
            else:
                self._client.set(full_key, value_str)
            return True
        
        return False
    
    def delete(self, key: str) -> bool:
        """
        删除键
        
        Args:
            key: 键
        
        Returns:
            是否成功
        """
        full_key = self._make_key(key)
        
        if self.config.backend == "memory":
            if full_key in self._memory_store:
                del self._memory_store[full_key]
                return True
        
        elif self.config.backend == "redis":
            return self._client.delete(full_key) > 0
        
        return False
    
    def exists(self, key: str) -> bool:
        """
        检查键是否存在
        
        Args:
            key: 键
        
        Returns:
            是否存在
        """
        full_key = self._make_key(key)
        
        if self.config.backend == "memory":
            entry = self._memory_store.get(full_key)
            if entry is None:
                return False
            if entry.is_expired():
                del self._memory_store[full_key]
                return False
            return True
        
        elif self.config.backend == "redis":
            return self._client.exists(full_key) > 0
        
        return False
    
    def keys(self, pattern: str = "*") -> List[str]:
        """
        获取匹配的键列表
        
        Args:
            pattern: 匹配模式
        
        Returns:
            键列表
        """
        full_pattern = self._make_key(pattern)
        prefix_len = len(self.config.prefix)
        
        if self.config.backend == "memory":
            import fnmatch
            result = []
            for key in self._memory_store.keys():
                if fnmatch.fnmatch(key, full_pattern):
                    result.append(key[prefix_len:])
            return result
        
        elif self.config.backend == "redis":
            keys = self._client.keys(full_pattern)
            return [k[prefix_len:] for k in keys]
        
        return []
    
    def mget(self, keys: List[str]) -> Dict[str, Any]:
        """
        批量获取
        
        Args:
            keys: 键列表
        
        Returns:
            键值字典
        """
        result = {}
        # A stored value may legitimately be None; get()'s default is also
        # None, so use a private sentinel to tell "missing" from "present
        # with value None" instead of dropping None-valued keys.
        missing = object()
        for key in keys:
            value = self.get(key, default=missing)
            if value is not missing:
                result[key] = value
        return result
    
    def mset(self, items: Dict[str, Any], ttl: int = None) -> bool:
        """
        批量设置
        
        Args:
            items: 键值字典
            ttl: 过期时间
        
        Returns:
            是否成功
        """
        for key, value in items.items():
            self.set(key, value, ttl)
        return True
    
    def _evict_expired(self) -> int:
        """清理过期键"""
        if self.config.backend != "memory":
            return 0
        
        expired_keys = [
            key for key, entry in self._memory_store.items()
            if entry.is_expired()
        ]
        
        for key in expired_keys:
            del self._memory_store[key]
        
        return len(expired_keys)
    
    def clear(self) -> None:
        """清空所有数据"""
        if self.config.backend == "memory":
            self._memory_store.clear()
        elif self.config.backend == "redis":
            keys = self._client.keys(f"{self.config.prefix}*")
            if keys:
                self._client.delete(*keys)
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        if self.config.backend == "memory":
            return {
                "backend": "memory",
                "total_keys": len(self._memory_store),
                "max_size": self.config.max_size,
            }
        elif self.config.backend == "redis":
            info = self._client.info()
            return {
                "backend": "redis",
                "connected_clients": info.get("connected_clients", 0),
                "used_memory": info.get("used_memory_human", ""),
            }
        return {}
