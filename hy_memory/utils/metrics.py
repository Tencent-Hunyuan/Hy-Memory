"""
Agent Memory - 性能监控

提供性能指标收集和统计
"""

import math
import time
import functools
from typing import Dict, List, Any, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime
from collections import defaultdict
import threading


@dataclass
class MetricRecord:
    """单次调用的指标记录"""
    method_name: str
    duration_ms: float
    timestamp: str
    success: bool = True
    error: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)


class MemoryMetrics:
    """
    记忆系统指标收集器（单例模式）
    
    收集和统计各方法的执行时间
    """
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._records: Dict[str, List[MetricRecord]] = defaultdict(list)
        self._enabled = True
        self._print_enabled = False
        self._max_records = 1000  # 每个方法最多保留 1000 条记录
        self._initialized = True
    
    @property
    def enabled(self) -> bool:
        return self._enabled
    
    @enabled.setter
    def enabled(self, value: bool):
        self._enabled = value
    
    @property
    def print_enabled(self) -> bool:
        return self._print_enabled
    
    @print_enabled.setter
    def print_enabled(self, value: bool):
        self._print_enabled = value
    
    def record(self, record: MetricRecord) -> None:
        """记录一条指标"""
        if not self._enabled:
            return
        
        with self._lock:
            records = self._records[record.method_name]
            records.append(record)
            
            # 限制记录数量
            if len(records) > self._max_records:
                self._records[record.method_name] = records[-self._max_records:]
        
        if self._print_enabled:
            status = "✓" if record.success else "✗"
            print(f"[Memory] {status} {record.method_name}: {record.duration_ms:.2f}ms")
    
    def get_stats(self, method_name: Optional[str] = None) -> Dict[str, Any]:
        """
        获取统计信息
        
        Args:
            method_name: 方法名，如果为 None 则返回所有方法的统计
        
        Returns:
            统计信息字典
        """
        if method_name:
            return self._calc_stats(method_name, self._records.get(method_name, []))
        
        return {
            name: self._calc_stats(name, records)
            for name, records in self._records.items()
        }
    
    def _calc_stats(self, name: str, records: List[MetricRecord]) -> Dict[str, Any]:
        """计算单个方法的统计信息"""
        if not records:
            return {"method": name, "count": 0}
        
        durations = [r.duration_ms for r in records]
        success_count = sum(1 for r in records if r.success)
        
        return {
            "method": name,
            "count": len(records),
            "success_count": success_count,
            "error_count": len(records) - success_count,
            "total_ms": sum(durations),
            "avg_ms": sum(durations) / len(durations),
            "min_ms": min(durations),
            "max_ms": max(durations),
            "p50_ms": sorted(durations)[len(durations) // 2],
            # nearest-rank: the p95 value is the ceil(0.95*n)-th element,
            # i.e. index ceil(0.95*n) - 1. The old int(n*0.95) index returned
            # the maximum for n=20 and the 96th value for n=100.
            "p95_ms": sorted(durations)[max(0, math.ceil(len(durations) * 0.95) - 1)] if len(durations) >= 20 else max(durations),
        }
    
    def print_summary(self) -> None:
        """打印统计摘要"""
        stats = self.get_stats()
        
        if not stats:
            print("[Memory Metrics] 没有记录")
            return
        
        print("\n" + "=" * 80)
        print("Agent Memory Metrics Summary")
        print("=" * 80)
        print(f"{'Method':<30} {'Count':>8} {'Total(ms)':>12} {'Avg(ms)':>10} {'P50(ms)':>10} {'P95(ms)':>10}")
        print("-" * 80)
        
        total_time = 0
        total_count = 0
        
        for name, s in sorted(stats.items()):
            if s["count"] == 0:
                continue
            print(
                f"{s['method']:<30} {s['count']:>8} {s['total_ms']:>12.2f} "
                f"{s['avg_ms']:>10.2f} {s.get('p50_ms', 0):>10.2f} {s.get('p95_ms', 0):>10.2f}"
            )
            total_time += s["total_ms"]
            total_count += s["count"]
        
        print("-" * 80)
        print(f"{'TOTAL':<30} {total_count:>8} {total_time:>12.2f}")
        print("=" * 80 + "\n")
    
    def clear(self) -> None:
        """清空所有记录"""
        with self._lock:
            self._records.clear()
    
    def export(self) -> Dict[str, Any]:
        """导出所有指标"""
        return {
            "stats": self.get_stats(),
            "enabled": self._enabled,
            "record_count": sum(len(r) for r in self._records.values()),
        }


# 全局单例
metrics = MemoryMetrics()


def timed(method_name: Optional[str] = None):
    """
    方法计时装饰器
    
    Args:
        method_name: 自定义方法名，默认使用函数名
    
    Usage:
        @timed()
        def my_method(self, ...):
            ...
        
        @timed("custom_name")
        def another_method(self, ...):
            ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            if not metrics.enabled:
                return func(*args, **kwargs)
            
            name = method_name or func.__name__
            
            # 如果是类方法，添加类名前缀
            if args and hasattr(args[0], '__class__'):
                class_name = args[0].__class__.__name__
                name = f"{class_name}.{name}"
            
            start_time = time.perf_counter()
            success = True
            error = None
            
            try:
                result = func(*args, **kwargs)
                return result
            except Exception as e:
                success = False
                error = str(e)
                raise
            finally:
                duration_ms = (time.perf_counter() - start_time) * 1000
                
                record = MetricRecord(
                    method_name=name,
                    duration_ms=duration_ms,
                    timestamp=datetime.now().isoformat(),
                    success=success,
                    error=error,
                )
                metrics.record(record)
        
        return wrapper
    return decorator


def timed_async(method_name: Optional[str] = None):
    """异步方法计时装饰器"""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            if not metrics.enabled:
                return await func(*args, **kwargs)
            
            name = method_name or func.__name__
            
            if args and hasattr(args[0], '__class__'):
                class_name = args[0].__class__.__name__
                name = f"{class_name}.{name}"
            
            start_time = time.perf_counter()
            success = True
            error = None
            
            try:
                result = await func(*args, **kwargs)
                return result
            except Exception as e:
                success = False
                error = str(e)
                raise
            finally:
                duration_ms = (time.perf_counter() - start_time) * 1000
                
                record = MetricRecord(
                    method_name=name,
                    duration_ms=duration_ms,
                    timestamp=datetime.now().isoformat(),
                    success=success,
                    error=error,
                )
                metrics.record(record)
        
        return wrapper
    return decorator
