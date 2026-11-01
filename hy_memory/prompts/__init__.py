"""Prompt registry: every multilingual LLM prompt, keyed by language code.

One module per language (``en.py``, ``zh_hans.py``, ``ja.py`` ...) defines the
same set of names (``PROMPT_NAMES``). Call sites look prompts up with
``get_prompt(name, language)`` instead of importing a language module, so a
new language only needs a new module and an entry in ``_MODULES``.

Language selection (``resolve_language``):

* explicit ``language`` (per call, else ``MemoryConfig.language`` /
  ``MEMORY_LANGUAGE``) wins and is validated by ``normalize_language``;
* otherwise the existing zh/en auto-detection runs unchanged
  (``utils.lang_detect.is_chinese`` → ``zh-Hans`` or ``en``). Auto-detection
  never picks any of the other languages.

When the language is explicit, ``with_output_language`` appends
``OUTPUT_LANGUAGE_DIRECTIVE`` so memories are written in that language even
if the conversation uses another one. Auto-detected requests keep the
"same language as the input" rule and their prompts are byte-identical to
earlier releases.
"""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import Any, Dict, Optional, Tuple

# Canonical codes, in documentation order.
SUPPORTED_LANGUAGES: Tuple[str, ...] = (
    "en",
    "zh-Hans",
    "zh-Hant",
    "ja",
    "es",
    "de",
    "pt",
    "fr",
    "ko",
    "ru",
    "id",
)

# Accepted aliases (matched case-insensitively, "_" treated as "-").
LANGUAGE_ALIASES: Dict[str, str] = {
    "zh": "zh-Hans",
    "zh-cn": "zh-Hans",
    "zh-sg": "zh-Hans",
    "zh-hans-cn": "zh-Hans",
    "zh-tw": "zh-Hant",
    "zh-hk": "zh-Hant",
    "zh-mo": "zh-Hant",
    "zh-hant-tw": "zh-Hant",
    "zh-hant-hk": "zh-Hant",
    "pt-br": "pt",
    "pt-pt": "pt",
    "en-us": "en",
    "en-gb": "en",
}

_MODULES: Dict[str, str] = {
    "en": "en",
    "zh-Hans": "zh_hans",
    "zh-Hant": "zh_hant",
    "ja": "ja",
    "es": "es",
    "de": "de",
    "pt": "pt",
    "fr": "fr",
    "ko": "ko",
    "ru": "ru",
    "id": "id",
}

# Every language module must define all of these.
PROMPT_NAMES: Tuple[str, ...] = (
    # chat extraction
    "EXTRACT_SYSTEM_PROMPT",
    "STRUCTURE_METADATA_PROMPT",
    "EXTRACT_FEW_SHOT",
    "EXTRACT_PROMPT",
    "EXTRACT_PROMPT_NO_INTENTION",
    "NO_INTENTION_SECTION_PATTERN",
    "NO_INTENTION_MEMORY_SECTION",
    "NO_INTENTION_REPLACEMENTS",
    "INTENTION_DISABLED_NOTE",
    "USER_NAME_LINE",
    # chat reconcile
    "SEARCH_QUERY_PROMPT",
    "RECONCILE_PROMPT",
    "RECONCILE_STRUCTURE_METADATA_PROMPT",
    "RECONCILE_FEW_SHOT",
    # working / coding memory
    "CODING_EXTRACT_PROMPT",
    "CODING_EXTRACT_PROMPT_CODING",
    "CODING_CONVERSATION_BLOCK",
    "CODING_EMPTY_BLOCK",
    "CODING_RECONCILE_PROMPT",
    "CODING_FACT_RECONCILE_PROMPT",
    "CODING_CHAIN_SUMMARY_PROMPT",
    # summaries
    "EPISODE_SUMMARY_PROMPT",
    "TOPIC_SUMMARY_PROMPT",
    # System 2 evolve
    "SYSTEM2_AGENT_PROMPT",
    "SYSTEM2_OUTPUT_INSTRUCTIONS",
    "SYSTEM2_SCHEMA_EVIDENCE_GATE",
    "SYSTEM2_SINGLE_CALL_REPLACEMENTS",
    "SYSTEM2_MATERIALS",
    # explicit-language output rule
    "OUTPUT_LANGUAGE_DIRECTIVE",
    "LANGUAGE_NAME",
)

# Prompts rendered with str.format(); the value lists the expected fields.
# The rest are appended verbatim (their braces are literal JSON).
FORMAT_FIELDS: Dict[str, Tuple[str, ...]] = {
    "EXTRACT_PROMPT": ("content", "current_date", "last_messages", "memory_at"),
    "EXTRACT_PROMPT_NO_INTENTION": ("content", "current_date", "last_messages", "memory_at"),
    "USER_NAME_LINE": ("name",),
    "SEARCH_QUERY_PROMPT": ("new_memories",),
    "RECONCILE_PROMPT": ("current_date", "existing_memories", "few_shot_section", "new_memories"),
    "CODING_EXTRACT_PROMPT": ("branch_present", "conversation_block", "workspace_present"),
    "CODING_EXTRACT_PROMPT_CODING": ("branch_present", "conversation_block", "workspace_present"),
    "CODING_CONVERSATION_BLOCK": ("history", "new"),
    "CODING_RECONCILE_PROMPT": ("candidates_block", "drafts_block"),
    "CODING_FACT_RECONCILE_PROMPT": ("candidates_block", "drafts_block"),
    "CODING_CHAIN_SUMMARY_PROMPT": ("chain_block",),
    "EPISODE_SUMMARY_PROMPT": ("memories",),
    "TOPIC_SUMMARY_PROMPT": ("memories", "topic_name"),
}

# Fields of each SYSTEM2_MATERIALS entry (all other entries have none).
SYSTEM2_MATERIALS_FIELDS: Dict[str, Tuple[str, ...]] = {
    "title": (),
    "stats": ("clustered", "clusters", "evidenced", "fresh", "graph_total", "recalled", "total", "unclustered"),
    "graph_status_empty": (),
    "clusters_header": (),
    "label_holistic": ("n",),
    "label_source_context": ("n",),
    "label_topic": ("n",),
    "raw_label": (),
    "holistic_note": ("max_schemas",),
    "companion_header": (),
    "unclustered_header": (),
    "unclustered_hint": (),
    "forward_header": ("count",),
    "graph_empty": (),
    "graph_no_recall": ("graph_total",),
    "reverse_header": (),
    "tags_header": (),
    "closing": (),
}

_loaded: Dict[str, ModuleType] = {}


def _supported_text() -> str:
    aliases = ", ".join(f"{k}->{v}" for k, v in LANGUAGE_ALIASES.items())
    return f"supported: {', '.join(SUPPORTED_LANGUAGES)} (aliases: {aliases})"


def normalize_language(language: Optional[str]) -> Optional[str]:
    """Return the canonical code for ``language``; None/"" stays None.

    Raises ValueError for an unknown code, listing the supported codes.
    """
    if language is None:
        return None
    if not isinstance(language, str):
        raise ValueError(
            f"language must be a string, got {type(language).__name__}; "
            + _supported_text()
        )
    raw = language.strip()
    if not raw:
        return None
    key = raw.replace("_", "-").lower()
    for code in SUPPORTED_LANGUAGES:
        if code.lower() == key:
            return code
    if key in LANGUAGE_ALIASES:
        return LANGUAGE_ALIASES[key]
    raise ValueError(f"unsupported language {language!r}; " + _supported_text())


def detect_language_code(content: str = "", messages=None) -> str:
    """The legacy automatic choice: ``zh-Hans`` if Chinese, else ``en``."""
    from ..utils.lang_detect import is_chinese

    return "zh-Hans" if is_chinese(content, messages=messages) else "en"


def resolve_language(
    language: Optional[str] = None,
    content: str = "",
    messages=None,
) -> str:
    """Explicit language if given (validated), else zh/en auto-detection."""
    code = normalize_language(language)
    if code is not None:
        return code
    return detect_language_code(content, messages=messages)


def _module(language: str) -> ModuleType:
    code = normalize_language(language) or "en"
    mod = _loaded.get(code)
    if mod is None:
        mod = importlib.import_module(f"{__name__}.{_MODULES[code]}")
        _loaded[code] = mod
    return mod


def get_prompt(name: str, language: Optional[str] = "en") -> Any:
    """Look up prompt ``name`` for ``language`` (canonical code or alias)."""
    if name not in PROMPT_NAMES:
        raise KeyError(f"unknown prompt {name!r}")
    return getattr(_module(language or "en"), name)


def with_output_language(
    prompt: str, language: Optional[str], explicit: bool,
) -> str:
    """Append the output-language rule when the language was set explicitly."""
    if not explicit or not language:
        return prompt
    return prompt + get_prompt("OUTPUT_LANGUAGE_DIRECTIVE", language)


__all__ = [
    "SUPPORTED_LANGUAGES",
    "LANGUAGE_ALIASES",
    "PROMPT_NAMES",
    "FORMAT_FIELDS",
    "SYSTEM2_MATERIALS_FIELDS",
    "normalize_language",
    "detect_language_code",
    "resolve_language",
    "get_prompt",
    "with_output_language",
]
