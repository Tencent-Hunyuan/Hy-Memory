"""
Agent Memory V2 - Emotion Analyzer (情绪分析 Agent)

V2 设计文档 §3.1:

  Emotional Tagging: 每个记忆节点携带情绪效价 (valence) 和唤醒度 (arousal)。
  - valence: [-1, 1] 负面到正面
  - arousal: [0, 1] 平静到激动

  情绪标注用于:
  1. Write Path: 对新写入的 L2 事实标注情绪
  2. Read Path: 情绪加权 (高唤醒度记忆抗衰减, 敏感内容标注 EMOTIONALLY_SENSITIVE)
  3. System 2: Sleep Replay 优先重放高情绪唤醒度的记忆
  4. Reconsolidation: 检测情感色调变化

  杏仁核类比: 情绪标记使重要的、情感性的记忆更容易被召回。
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

EMOTION_ANALYSIS_PROMPT = """分析以下对话内容的情绪特征。

对话内容:
---
{content}
---

请从两个维度分析:
1. 情感效价 (valence): 范围 [-1.0, 1.0]
   - -1.0: 非常负面 (悲伤、愤怒、恐惧)
   - 0.0:  中性
   - 1.0:  非常正面 (开心、兴奋、满足)

2. 情绪唤醒度 (arousal): 范围 [0.0, 1.0]
   - 0.0: 非常平静 (放松、无聊)
   - 0.5: 适中
   - 1.0: 非常激动 (兴奋、愤怒、恐惧)

输出 JSON:
{{
  "valence": 0.0,
  "arousal": 0.0,
  "dominant_emotion": "主要情绪标签",
  "is_sensitive": false,
  "sensitivity_reason": "如果敏感，说明原因(否则null)",
  "emotional_keywords": ["关键情绪词"]
}}

常见情绪-维度映射参考:
  开心/满足: valence=0.6~0.9, arousal=0.3~0.6
  兴奋/激动: valence=0.7~1.0, arousal=0.7~1.0
  平静/放松: valence=0.2~0.5, arousal=0.0~0.2
  焦虑/担忧: valence=-0.3~-0.6, arousal=0.5~0.8
  悲伤/失落: valence=-0.5~-0.9, arousal=0.1~0.4
  愤怒/不满: valence=-0.6~-1.0, arousal=0.6~1.0
  中性/叙述: valence=0.0, arousal=0.1~0.3"""

BATCH_EMOTION_PROMPT = """分析以下多段内容的情绪特征。

{items}

对每段内容输出:
{{
  "results": [
    {{
      "index": 0,
      "valence": 0.0,
      "arousal": 0.0,
      "dominant_emotion": "情绪标签"
    }}
  ]
}}"""


# ================================================================
# 数据结构
# ================================================================

@dataclass
class EmotionAnalysisConfig:
    """情绪分析配置"""
    max_tokens: int = 400
    # None = 跟随 config.llm.temperature（写死会绕过用户配置，
    # 且在只接受特定 temperature 的网关上直接 400）
    temperature: Optional[float] = None

    # 敏感度阈值: arousal > 此值 且 valence < 0 时标记为敏感
    sensitivity_arousal_threshold: float = 0.6
    sensitivity_valence_threshold: float = -0.3

    # 批量分析每批大小
    batch_size: int = 10


@dataclass
class EmotionResult:
    """情绪分析结果"""
    success: bool
    valence: float = 0.0
    arousal: float = 0.0
    dominant_emotion: str = "neutral"
    is_sensitive: bool = False
    sensitivity_reason: Optional[str] = None
    emotional_keywords: List[str] = field(default_factory=list)
    tokens_used: int = 0
    error: Optional[str] = None


# ================================================================
# 核心实现
# ================================================================

class EmotionAnalyzer:
    """
    情绪分析 Agent。

    对文本内容进行情绪效价 + 唤醒度双维标注。
    """

    def __init__(
        self,
        llm: LLMProvider,
        config: Optional[EmotionAnalysisConfig] = None,
    ):
        self.llm = llm
        self.config = config or EmotionAnalysisConfig()
        self._call_count = 0
        self._total_tokens = 0
        logger.info("EmotionAnalyzer initialized")

    async def analyze(self, content: str) -> EmotionResult:
        """
        分析单段内容的情绪。

        Args:
            content: 对话文本

        Returns:
            EmotionResult 包含 valence, arousal, dominant_emotion 等
        """
        try:
            prompt = EMOTION_ANALYSIS_PROMPT.format(content=content)
            response = await self.llm.complete(
                prompt=prompt,
                max_tokens=self.config.max_tokens,
            )
            self._call_count += 1
            self._total_tokens += response.tokens_used

            data = self._parse_json(response.content)

            valence = self._clamp(float(data.get("valence", 0.0)), -1.0, 1.0)
            arousal = self._clamp(float(data.get("arousal", 0.0)), 0.0, 1.0)

            # 判断敏感性
            is_sensitive = data.get("is_sensitive", False)
            if not is_sensitive:
                is_sensitive = (
                    arousal > self.config.sensitivity_arousal_threshold
                    and valence < self.config.sensitivity_valence_threshold
                )

            return EmotionResult(
                success=True,
                valence=valence,
                arousal=arousal,
                dominant_emotion=data.get("dominant_emotion", "neutral"),
                is_sensitive=is_sensitive,
                sensitivity_reason=data.get("sensitivity_reason"),
                emotional_keywords=data.get("emotional_keywords", []),
                tokens_used=response.tokens_used,
            )

        except Exception as e:
            logger.error(f"EmotionAnalyzer.analyze failed: {e}")
            return EmotionResult(success=False, error=str(e))

    async def analyze_batch(
        self, contents: List[str],
    ) -> List[EmotionResult]:
        """
        批量分析情绪。

        对于大量内容，分批处理以减少 LLM 调用次数。
        """
        results = []
        batch_size = self.config.batch_size

        for i in range(0, len(contents), batch_size):
            batch = contents[i:i + batch_size]

            if len(batch) == 1:
                result = await self.analyze(batch[0])
                results.append(result)
                continue

            # 批量 Prompt
            items_text = "\n".join(
                f"[{j}] {c}" for j, c in enumerate(batch)
            )
            prompt = BATCH_EMOTION_PROMPT.format(items=items_text)

            try:
                response = await self.llm.complete(
                    prompt=prompt,
                    max_tokens=self.config.max_tokens * len(batch),
                )
                self._call_count += 1
                self._total_tokens += response.tokens_used

                data = self._parse_json(response.content)
                batch_results = data.get("results", [])

                for j, c in enumerate(batch):
                    if j < len(batch_results):
                        r = batch_results[j]
                        valence = self._clamp(float(r.get("valence", 0.0)), -1.0, 1.0)
                        arousal = self._clamp(float(r.get("arousal", 0.0)), 0.0, 1.0)
                        results.append(EmotionResult(
                            success=True,
                            valence=valence,
                            arousal=arousal,
                            dominant_emotion=r.get("dominant_emotion", "neutral"),
                            tokens_used=response.tokens_used // len(batch),
                        ))
                    else:
                        results.append(EmotionResult(
                            success=True, valence=0.0, arousal=0.0,
                        ))

            except Exception as e:
                logger.warning(f"Batch emotion analysis failed, falling back: {e}")
                for c in batch:
                    result = await self.analyze(c)
                    results.append(result)

        return results

    # ================================================================
    # 工具方法
    # ================================================================

    @staticmethod
    def _clamp(value: float, min_v: float, max_v: float) -> float:
        return max(min_v, min(max_v, value))

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
            # A greedy regex spans the first '{' to the last '}'. Trailing
            # prose with another object then forms an invalid blob and the
            # whole analysis is dropped. Decode just the first object.
            start = text.find("{")
            if start != -1:
                try:
                    parsed, _ = json.JSONDecoder().raw_decode(text[start:])
                    if isinstance(parsed, dict):
                        return parsed
                except json.JSONDecodeError:
                    pass
        return {}

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "call_count": self._call_count,
            "total_tokens": self._total_tokens,
        }
