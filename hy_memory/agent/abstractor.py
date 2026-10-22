"""
Agent Memory V2 - Abstractor 摘要智能体 (System 2 Pro Pipeline)

负责 System 2 pro pipeline 的高阶摘要能力：
  1. Session 摘要生成 (L3 层 — System 2 Sleep Replay 调用)
  2. Schema 归纳 (L5 层 — System 2 Schema Miner 调用)
  3. Profile 摘要生成 (Context Assembly 调用)

注意：L3_SUMMARY 由 Episode / Topic summary 机制负责，本模块不生成摘要。
"""

from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
import json
import logging

from .llm_provider import LLMProvider
from ..config import LLMConfig as GlobalLLMConfig

logger = logging.getLogger(__name__)


# ================================================================
# 数据结构
# ================================================================
@dataclass
class SessionSummaryResult:
    """Session 摘要结果 (V2)"""
    success: bool
    summary: str = ""
    key_topics: List[str] = field(default_factory=list)
    emotional_tone: str = ""
    new_facts_count: int = 0
    tokens_used: int = 0
    error: Optional[str] = None


@dataclass
class SchemaAbstractResult:
    """Schema 归纳结果 (V2)"""
    success: bool
    central_proposition: str = ""
    supporting_summary: str = ""
    expected_inferences: List[str] = field(default_factory=list)
    confidence: float = 0.0
    tokens_used: int = 0
    error: Optional[str] = None


@dataclass
class ProfileSummaryResult:
    """Profile 摘要结果 (V2)"""
    success: bool
    core: str = ""
    personality: str = ""
    active_schemas: List[str] = field(default_factory=list)
    gotchas: List[str] = field(default_factory=list)
    note: str = ""
    tokens_used: int = 0
    error: Optional[str] = None


# ================================================================
# Prompt 模板
# ================================================================

SESSION_SUMMARY_PROMPT = """Generate a summary of the following conversation session. The summary language MUST match the conversation language.

Session content:
---
{session_content}
---

Existing user profile:
{user_profile}

Current time: {current_time}

Output in JSON format:
{{
  "summary": "A brief session summary (max 200 words, in the same language as the conversation)",
  "key_topics": ["key topic 1", "topic 2"],
  "emotional_tone": "overall emotional tone (e.g., positive/neutral/anxious/happy)",
  "new_facts_count": 0
}}"""

SCHEMA_ABSTRACT_PROMPT = """Below is a set of related facts mentioned by the user across multiple sessions. Induce an abstract "mental model" (Schema).

Related facts:
{facts}

A good Schema should:
- Describe a recurring behavioral pattern or thinking style of the user
- Not be a simple enumeration of facts, but an abstract pattern
- Help predict how the user would react in similar situations

Output in JSON format:
{{
  "central_proposition": "core proposition (one sentence describing this pattern)",
  "supporting_summary": "evidence overview (why you believe this pattern exists)",
  "expected_inferences": ["expected inference based on this pattern 1", "inference 2"],
  "confidence": 0.0-1.0
}}"""

PROFILE_SUMMARY_PROMPT = """Generate a structured user profile summary based on the following data. The summary language MUST match the profile data language.

Profile data:
{profile_data}

Active mental models:
{active_schemas}

Known gotchas/notes:
{gotchas}

Output in JSON format:
{{
  "core": "core description (e.g., name, age, location, occupation)",
  "personality": "personality/style description (e.g., pragmatic, tech-oriented)",
  "active_schemas": ["active behavioral patterns"],
  "gotchas": ["gotchas/safety notes to be aware of"],
  "note": "other notes"
}}"""


# ================================================================
# 核心实现
# ================================================================

class Abstractor:
    """
    摘要智能体 (System 2 Pro Pipeline)

    负责 System 2 的高阶摘要：Session 摘要 / Schema 归纳 / Profile 摘要。
    L3_SUMMARY 由 Episode / Topic summary 机制负责。
    """

    def __init__(
        self,
        llm_provider: LLMProvider,
        llm_config: Optional[GlobalLLMConfig] = None,
    ):
        self.llm = llm_provider
        self._llm_config = llm_config or GlobalLLMConfig()
        self._call_count = 0
        self._total_tokens = 0
        logger.debug("Abstractor initialized")

    async def generate_session_summary(
        self,
        session_content: str,
        user_profile: str = "",
        current_time: str = "",  # 由调用方传入，不传则为空字符串
    ) -> SessionSummaryResult:
        """
        生成 Session 摘要 (L3 层)。
        由 System 2 Sleep Replay Worker 调用。
        """
        try:
            prompt = SESSION_SUMMARY_PROMPT.format(
                session_content=session_content,
                user_profile=user_profile or "(暂无画像)",
                current_time=current_time,  # 由调用方决定，不传则为空字符串
            )
            response = await self.llm.complete(
                prompt=prompt,
                max_tokens=self._llm_config.agent_max_tokens,
            )
            self._call_count += 1
            self._total_tokens += response.tokens_used

            data = self._parse_json(response.content)

            return SessionSummaryResult(
                success=True,
                summary=data.get("summary", response.content.strip()),
                key_topics=data.get("key_topics", []),
                emotional_tone=data.get("emotional_tone", "中性"),
                new_facts_count=data.get("new_facts_count", 0),
                tokens_used=response.tokens_used,
            )
        except Exception as e:
            logger.error(f"generate_session_summary failed: {e}")
            return SessionSummaryResult(success=False, error=str(e))

    # ================================================================
    # V2 Schema 归纳 (L4.5)
    # ================================================================

    async def abstract_schema(
        self,
        facts: List[str],
    ) -> SchemaAbstractResult:
        """
        从一组关联事实中归纳 Schema (心智模型)。
        由 System 2 Schema Miner 调用。
        """
        try:
            facts_text = "\n".join(f"  - {f}" for f in facts)
            prompt = SCHEMA_ABSTRACT_PROMPT.format(facts=facts_text)

            response = await self.llm.complete(
                prompt=prompt,
                max_tokens=self._llm_config.agent_max_tokens,
            )
            self._call_count += 1
            self._total_tokens += response.tokens_used

            data = self._parse_json(response.content)

            return SchemaAbstractResult(
                success=True,
                central_proposition=data.get("central_proposition", ""),
                supporting_summary=data.get("supporting_summary", ""),
                expected_inferences=data.get("expected_inferences", []),
                confidence=float(data.get("confidence", 0.5)),
                tokens_used=response.tokens_used,
            )
        except Exception as e:
            logger.error(f"abstract_schema failed: {e}")
            return SchemaAbstractResult(success=False, error=str(e))

    # ================================================================
    # V2 Profile 摘要 (L5)
    # ================================================================

    async def generate_profile_summary(
        self,
        profile_data: str,
        active_schemas: str = "",
        gotchas: str = "",
    ) -> ProfileSummaryResult:
        """
        生成 Profile 摘要，用于 Memory Context Package 的 Layer 0。
        由 Context Assembly 调用。
        """
        try:
            prompt = PROFILE_SUMMARY_PROMPT.format(
                profile_data=profile_data or "(暂无画像数据)",
                active_schemas=active_schemas or "(无)",
                gotchas=gotchas or "(无)",
            )
            response = await self.llm.complete(
                prompt=prompt,
                max_tokens=self._llm_config.agent_max_tokens,
            )
            self._call_count += 1
            self._total_tokens += response.tokens_used

            data = self._parse_json(response.content)

            return ProfileSummaryResult(
                success=True,
                core=data.get("core", ""),
                personality=data.get("personality", ""),
                active_schemas=data.get("active_schemas", []),
                gotchas=data.get("gotchas", []),
                note=data.get("note", ""),
                tokens_used=response.tokens_used,
            )
        except Exception as e:
            logger.error(f"generate_profile_summary failed: {e}")
            return ProfileSummaryResult(success=False, error=str(e))

    # ================================================================
    # 工具方法
    # ================================================================

    @staticmethod
    def _parse_json(text: str) -> Dict[str, Any]:
        """从 LLM 输出中解析 JSON"""
        text = text.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            import re
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
            "avg_tokens_per_call": (
                self._total_tokens / self._call_count
                if self._call_count > 0 else 0
            ),
        }
