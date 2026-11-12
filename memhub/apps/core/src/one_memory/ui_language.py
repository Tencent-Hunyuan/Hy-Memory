"""The display language a person chose, and the SDK prompt language it implies.

The App owns the choice (its language selector) and tells Core whenever it
changes, so Core can build every SDK client with it. It is one global value in
`config.json` (`ui_language`), beside `memory_mode` and `memory_features`;
absent means no language was set (a CLI/API-only install, or
`settings language system`). An App that follows the system sends the
language it resolved and shows, not null.

The owner's rule for the SDK:

- Any supported code, zh-Hans and en included: pass it as
  `MemoryConfig.language`, so extraction and the other prompts use that
  language. Every display language is passed explicitly (#243).
- No language at all (never set, as on a CLI/API-only install): pass no
  language. The SDK keeps detecting Chinese or English from the text, exactly
  as before this setting existed. The App does not leave it unset: it sends
  the language it shows, including when it follows the system.

The language never names a collection; a change rebuilds the SDK handles the
way a mode or feature change does (#218 use count).

No SDK import here: only `hy_sdk_runtime` and `hy_sdk_migration` import it.
`config_state` imports this module, so it imports nothing from there.
"""

from __future__ import annotations

from typing import Literal

UiLanguage = Literal[
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
]
UI_LANGUAGES: tuple[UiLanguage, ...] = (
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
#: Codes earlier App builds stored, and the code each one is now.
LEGACY_UI_LANGUAGES: dict[str, UiLanguage] = {"zh-CN": "zh-Hans"}
#: What status shows when no language is passed to the SDK.
SDK_LANGUAGE_AUTO = "auto"


def normalize_ui_language(value: object) -> UiLanguage | None:
    """A supported code (legacy ones mapped), or None for anything else."""
    if not isinstance(value, str):
        return None
    value = LEGACY_UI_LANGUAGES.get(value, value)
    return value if value in UI_LANGUAGES else None


def sdk_language(ui_language: str | None) -> str | None:
    """The `MemoryConfig.language` for a display language; None = SDK auto-detect.

    Every supported code is passed as is; only an unset (or unknown) value
    leaves the SDK to detect zh/en.
    """
    return normalize_ui_language(ui_language)
