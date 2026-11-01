# -*- coding: utf-8 -*-
"""HY Memory working/coding 链路使用的中文提示词模板（兼容入口）。

文本已迁到 ``hy_memory/prompts/zh_hans.py``（``CODING_*``），这里保留旧名字。
"""

from ..prompts.zh_hans import (  # noqa: F401
    CODING_CHAIN_SUMMARY_PROMPT as CHAIN_SUMMARY_PROMPT_ZH,
    CODING_EXTRACT_PROMPT as EXTRACT_PROMPT_ZH,
    CODING_EXTRACT_PROMPT_CODING as EXTRACT_PROMPT_CODING_ZH,
    CODING_FACT_RECONCILE_PROMPT as FACT_RECONCILE_PROMPT_ZH,
    CODING_RECONCILE_PROMPT as RECONCILE_PROMPT_ZH,
)
