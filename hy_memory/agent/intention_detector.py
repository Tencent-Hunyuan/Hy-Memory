"""
Agent Memory V2 - Intention Detector (意图检测 Agent)

V2 设计文档 §3.4 + §4.1:

  检测用户对话中的前瞻性意图 (Prospective Memory)。
  L7 层 (INTENTION): 记住"将来要做什么"。

  两种触发类型:
  1. TIME_BASED: 时间触发 (如 "下周一提醒我")
  2. EVENT_BASED: 事件触发 (如 "下次聊到旅行时")

  意图检测用于:
  1. Write Path (System 1): 轻量检测，快速写入 L7 Intention Queue
  2. Read Path: 检查当前对话是否触发了已有意图
  3. System 2: 深度分析意图细节，设置触发条件

  前额叶类比: 前瞻性记忆 — 在未来某个时刻自动触发的记忆。
"""

import json
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
import logging

from .llm_provider import LLMProvider

logger = logging.getLogger(__name__)


# ================================================================
# Prompt 模板
# ================================================================

INTENTION_DETECT_PROMPT = """分析以下对话内容，检测用户是否表达了任何未来的计划、意图或需求。

对话内容:
---
{content}
---

当前时间: {current_time}

请检测以下类型的意图:
1. 时间触发型: 有明确时间点的计划 ("下周一要开会", "三月份去旅行")
2. 事件触发型: 需要在特定情境下提醒的需求 ("下次聊到XX时记得提醒我", "如果有XX相关的信息告诉我")
3. 关怀型: 暗示未来可能需要关注的信号 ("最近压力好大", "考试快到了")

输出 JSON:
{{
  "intentions": [
    {{
      "content": "意图描述",
      "trigger_type": "time_based/event_based",
      "trigger_condition": "触发条件的自然语言描述",
      "trigger_time": "ISO格式时间(time_based时,否则null)",
      "trigger_event_pattern": "触发事件的模式描述(event_based时,否则null)",
      "priority": "high/medium/low",
      "expiry": "ISO格式过期时间(如果有,否则null)",
      "is_proactive_care": false
    }}
  ]
}}

关于 is_proactive_care:
  如果意图不是用户明确提出的，而是系统应该主动关怀的信号，设为 true。
  例如: 用户说"考试快到了好紧张" → 系统应在考试后关心结果。

如果没有检测到意图返回: {{"intentions": []}}"""

INTENTION_TRIGGER_CHECK_PROMPT = """判断当前对话内容是否触发了以下已有意图。

当前对话:
---
{current_content}
---

当前时间: {current_time}

已有意图列表:
{existing_intentions}

对每个意图判断是否被当前对话触发。

输出 JSON:
{{
  "triggered": [
    {{
      "intention_id": "被触发的意图ID",
      "trigger_reason": "触发原因",
      "confidence": 0.0-1.0
    }}
  ]
}}

如果没有意图被触发: {{"triggered": []}}"""


# ================================================================
# 数据结构
# ================================================================

@dataclass
class IntentionDetectorConfig:
    """意图检测器配置"""
    detect_max_tokens: int = 600
    # None = 跟随 config.llm.temperature
    detect_temperature: Optional[float] = None
    trigger_check_max_tokens: int = 500
    trigger_check_temperature: Optional[float] = None

    # 主动关怀检测
    enable_proactive_care: bool = True


@dataclass
class DetectedIntention:
    """检测到的意图"""
    content: str = ""
    trigger_type: str = "event_based"   # time_based / event_based
    trigger_condition: str = ""
    trigger_time: Optional[str] = None  # ISO format
    trigger_event_pattern: Optional[str] = None
    priority: str = "medium"            # high / medium / low
    expiry: Optional[str] = None
    is_proactive_care: bool = False


@dataclass
class IntentionDetectResult:
    """意图检测结果"""
    success: bool
    intentions: List[DetectedIntention] = field(default_factory=list)
    tokens_used: int = 0
    error: Optional[str] = None


@dataclass
class TriggeredIntentionItem:
    """被触发的意图"""
    intention_id: str = ""
    trigger_reason: str = ""
    confidence: float = 0.0


@dataclass
class IntentionTriggerResult:
    """意图触发检查结果"""
    success: bool
    triggered: List[TriggeredIntentionItem] = field(default_factory=list)
    tokens_used: int = 0
    error: Optional[str] = None


# ================================================================
# 核心实现
# ================================================================

class IntentionDetector:
    """
    意图检测 Agent。

    能力:
    1. detect(): 从对话中检测新意图 (Write Path)
    2. check_triggers(): 检查当前对话是否触发已有意图 (Read Path)
    """

    def __init__(
        self,
        llm: LLMProvider,
        config: Optional[IntentionDetectorConfig] = None,
    ):
        self.llm = llm
        self.config = config or IntentionDetectorConfig()
        self._call_count = 0
        self._total_tokens = 0
        logger.info("IntentionDetector initialized")

    async def detect(
        self,
        content: str,
        current_time: str = "",
    ) -> IntentionDetectResult:
        """
        从对话内容中检测意图。

        Args:
            content:      对话文本
            current_time: 当前时间 (ISO format)

        Returns:
            IntentionDetectResult 包含检测到的意图列表
        """
        try:
            from datetime import datetime as dt
            prompt = INTENTION_DETECT_PROMPT.format(
                content=content,
                current_time=current_time or dt.now().isoformat(timespec="seconds"),
            )

            response = await self.llm.complete(
                prompt=prompt,
                max_tokens=self.config.detect_max_tokens,
            )
            self._call_count += 1
            self._total_tokens += response.tokens_used

            data = self._parse_json(response.content)

            intentions = []
            for item in data.get("intentions", []):
                # 如果不启用主动关怀，跳过 proactive_care 意图
                if not self.config.enable_proactive_care and item.get("is_proactive_care"):
                    continue

                intentions.append(DetectedIntention(
                    content=item.get("content", ""),
                    trigger_type=item.get("trigger_type", "event_based"),
                    trigger_condition=item.get("trigger_condition", ""),
                    trigger_time=item.get("trigger_time"),
                    trigger_event_pattern=item.get("trigger_event_pattern"),
                    priority=item.get("priority", "medium"),
                    expiry=item.get("expiry"),
                    is_proactive_care=item.get("is_proactive_care", False),
                ))

            return IntentionDetectResult(
                success=True,
                intentions=intentions,
                tokens_used=response.tokens_used,
            )

        except Exception as e:
            logger.error(f"IntentionDetector.detect failed: {e}")
            return IntentionDetectResult(success=False, error=str(e))

    async def check_triggers(
        self,
        current_content: str,
        existing_intentions: List[Dict[str, Any]],
        current_time: str = "",
    ) -> IntentionTriggerResult:
        """
        检查当前对话是否触发了已有意图。

        Args:
            current_content:     当前对话内容
            existing_intentions: 已有意图列表 [{id, content, trigger_condition, ...}]
            current_time:        当前时间

        Returns:
            IntentionTriggerResult 包含被触发的意图列表
        """
        try:
            if not existing_intentions:
                return IntentionTriggerResult(success=True)

            from datetime import datetime as dt
            intentions_text = "\n".join(
                f"[{i.get('id', '?')}] {i.get('content', '')} "
                f"(触发条件: {i.get('trigger_condition', '?')})"
                for i in existing_intentions
            )

            prompt = INTENTION_TRIGGER_CHECK_PROMPT.format(
                current_content=current_content,
                current_time=current_time or dt.now().isoformat(timespec="seconds"),
                existing_intentions=intentions_text,
            )

            response = await self.llm.complete(
                prompt=prompt,
                max_tokens=self.config.trigger_check_max_tokens,
            )
            self._call_count += 1
            self._total_tokens += response.tokens_used

            data = self._parse_json(response.content)

            triggered = []
            for item in data.get("triggered", []):
                triggered.append(TriggeredIntentionItem(
                    intention_id=item.get("intention_id", ""),
                    trigger_reason=item.get("trigger_reason", ""),
                    confidence=float(item.get("confidence", 0.5)),
                ))

            return IntentionTriggerResult(
                success=True,
                triggered=triggered,
                tokens_used=response.tokens_used,
            )

        except Exception as e:
            logger.error(f"IntentionDetector.check_triggers failed: {e}")
            return IntentionTriggerResult(success=False, error=str(e))

    # ================================================================
    # 工具方法
    # ================================================================

    @staticmethod
    def _parse_json(text: str) -> Dict[str, Any]:
        """从 LLM 输出中解析 JSON"""
        import re
        text = text.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r'\{[\s\S]*\}', text)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
        return {}

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "call_count": self._call_count,
            "total_tokens": self._total_tokens,
        }
