"""Prompt registry contracts: every language defines every prompt correctly.

en and zh-Hans are pinned by sha256 to the strings shipped before the
registry existed, so existing users see byte-identical prompts.
"""

import hashlib
import json
import re
from string import Formatter

import pytest

from hy_memory.prompts import (
    FORMAT_FIELDS,
    PROMPT_NAMES,
    SUPPORTED_LANGUAGES,
    SYSTEM2_MATERIALS_FIELDS,
    get_prompt,
)

_PINNED_SHA256 = {
    "en": {
        "EXTRACT_SYSTEM_PROMPT": "2e362cbeb2d2ed5cb71a830979b645c56460e95afe00ad3238bf986c0d631936",
        "STRUCTURE_METADATA_PROMPT": "b64b3d33484240d1ac8b7db2488864a166beb9d677a9270c3701f0fb0ce55987",
        "EXTRACT_FEW_SHOT": "c6c3b5ce137e5d18bfd492c5d36ad170e0c29e1755a94b29cc705edeee14d3f1",
        "EXTRACT_PROMPT": "d72586990e9b8cce115cc9092da7ac9bccc47ad990b9d7fe81250dcb45671276",
        "EXTRACT_PROMPT_NO_INTENTION": "26a2c2efcd32551a424d21d1c0ff6677951f97edeac68db7eec4bb714cc30ea6",
        "NO_INTENTION_SECTION_PATTERN": "b45f2d33cd009d7b7f25085dcf384ac7a4da2e8fde42f823c9eec9f661cd7d02",
        "NO_INTENTION_MEMORY_SECTION": "702b1fc9811acaf502a1c99184e1c406470187b351b2643395776c6bce3c9f25",
        "NO_INTENTION_REPLACEMENTS": "63b0f145520cbc1480ae4f9f4c5d1692e6c3e56a662cb6c47e468e21bf09ea40",
        "INTENTION_DISABLED_NOTE": "3979b55177eda6748239a37b8eb522fb1051ea240836118f4a5d972121839af2",
        "USER_NAME_LINE": "5bc710c0a52712aa8ace10555de186a87f7051787ed61971c461acd81e6cb7e8",
        "SEARCH_QUERY_PROMPT": "90617660bb4c20460e295802dc2ff1ab7447d51e3e6ef5ac77b9d10594696de5",
        "RECONCILE_PROMPT": "bb3dcc2cc65a315d4ce52195a681efbd32527a81d085c61f0fb052799d1a3a85",
        "RECONCILE_STRUCTURE_METADATA_PROMPT": "9fe65cb30b08a9de47e5db5ba512bace739b5c85a36395e61b2cebb02d3d7f5b",
        "RECONCILE_FEW_SHOT": "0fbe1b979b86e4ca133d15b24607284a19ab4523010e770c51a5d865b9cc8528",
        "CODING_EXTRACT_PROMPT": "53a056cfb1deabd613a23ea26058e821d52c4f863f89b706547a974aeca008ac",
        "CODING_EXTRACT_PROMPT_CODING": "3e987f1b6cfb7a17cfec11f4918729fc3fe118268471ca46d57e84fd3de9e6b4",
        "CODING_CONVERSATION_BLOCK": "c0d2bb215e8a02f0590f586d81daa7955d39a94bed9e37657d2b922d81b29e7d",
        "CODING_EMPTY_BLOCK": "552e423045fcbd949252397b4bee754f0b8f99b1bbbe41edc9b25dbfc81b146a",
        "CODING_RECONCILE_PROMPT": "00bca0e7f44f421470363c3a6d35a3da8df1993c685f514a8d5ae93023427c75",
        "CODING_FACT_RECONCILE_PROMPT": "1db547157bce2910030772cbf2286b77ddefa4936ff3e4ed05ff2088019110be",
        "CODING_CHAIN_SUMMARY_PROMPT": "dac479310ce51dbea8f0c25f00de6060d65a524af1f3a1c686a5df19c29724df",
        "EPISODE_SUMMARY_PROMPT": "d93e2d2fcf9f424c3de524ac82d36a41ad5c3bf4c702a0e487085e856f65ef5e",
        "TOPIC_SUMMARY_PROMPT": "a5bb0a74491f382139a52fee46a055c22efa694ab03fc148cb894d08961a1d2d",
        "SYSTEM2_AGENT_PROMPT": "c2254e94d0bc239ccda9cbd79b98508128417bb224d27792f2ea89b837c5e05e",
        "SYSTEM2_OUTPUT_INSTRUCTIONS": "cbbd0786c4f3ff8b69f69a46a7476a6037ec722801718579392f2e6e26c8c2e7",
        "SYSTEM2_SCHEMA_EVIDENCE_GATE": "6e858894061a9f2107a21f8b6cd547d42deaac69a40d59e11d338cbf2de892e4",
        "SYSTEM2_SINGLE_CALL_REPLACEMENTS": "7879edaf5a3681b0ddfaeb65cf76fed305ab88914c05fb0fffe5d5dfd51cf21d",
        "SYSTEM2_MATERIALS": "3ff1546a7a3ca150f1bae324d94939ce864de229b29e0245692621084b04e52a",
    },
    "zh-Hans": {
        "EXTRACT_SYSTEM_PROMPT": "fc8de8505c744091fcdc24a2cfd7a4e6f62a1332c18fa39d191a7f39aa0a7f4a",
        "STRUCTURE_METADATA_PROMPT": "dac3aca6671dc5ca0766425df1463c01a516ed515c7b38a7925c1b53ffb115a6",
        "EXTRACT_FEW_SHOT": "6e2ad8bb1f054cc32f3501e42cdc865b246934ecb660d9572e342be8d900f244",
        "EXTRACT_PROMPT": "2e065893433ba2af68b55d41c3dc7e056f630dd4b41ed431e557751101e51be5",
        "EXTRACT_PROMPT_NO_INTENTION": "66adb01ad790a46ad0c13ff2b30aa79c0e29bcbb2e191bc3cf3e544fe6e4956b",
        "NO_INTENTION_SECTION_PATTERN": "27110fb7913b57e205372ede892a1656682a2635e77e119952a68a864cfdf646",
        "NO_INTENTION_MEMORY_SECTION": "1d698eb697aa0e079d33bcc450ec0036dbafc600b1ec30ca80a6a18def6894f6",
        "NO_INTENTION_REPLACEMENTS": "211f2bacfbc1852fc3f11dfb67acdd44c534f703b1612dfc59d1c569ff3a39b2",
        "INTENTION_DISABLED_NOTE": "50fe78d881568a3a17a0cd3c993836eceafd719f762cfb37c7884b448b74b174",
        "USER_NAME_LINE": "9f702915ddcd42d5c16ed7a0da3256e69d33991f4b88b14c2d3c04cc22c95150",
        "SEARCH_QUERY_PROMPT": "91b760eec1d2ed2bc02f1688acb911b0786cadd06eea88645722db9691baf25c",
        "RECONCILE_PROMPT": "32f5162b496c07fec643a96158eae87aa12c1dfae0ffd2084f5aa71b71377869",
        "RECONCILE_STRUCTURE_METADATA_PROMPT": "720640c185bd2c4e1b0c1694e1236b23e1923b7a02d948282a049de7dd4dfb8a",
        "RECONCILE_FEW_SHOT": "c96ffff49ac71bf52ccef0d1173ea8dfba31c1542f3cfeab939b4a906e037e58",
        "CODING_EXTRACT_PROMPT": "b87d5df66ef153baaf38f32a59ec0641e6f3bac4f0014056b37f61278d176c59",
        "CODING_EXTRACT_PROMPT_CODING": "1d6bb3c538dd31f310b3d123617bf73ee5a1c4474f3176f042d9f2af62c3d997",
        "CODING_CONVERSATION_BLOCK": "3253e7c9d81136475b435d07f47f7cf5fe315d1c9d8b8b31ed15da8db7829d72",
        "CODING_EMPTY_BLOCK": "c7bcc6d27f3abaaa5410e40c17f0c5ca79781a74e176695193c22aa4a8064d62",
        "CODING_RECONCILE_PROMPT": "4b62e6e214b70403e6e041e0d00cd7d5545100759beda9c2b6d5a5a6235452dc",
        "CODING_FACT_RECONCILE_PROMPT": "74ed3b045f11a4aeb29fcaf43407e3f814c4e25f0469d7f3c3b4f57e5973431c",
        "CODING_CHAIN_SUMMARY_PROMPT": "2d1445b2ade72102750920644ff2e086416dd58152419fd5d43114e75a35d270",
        "EPISODE_SUMMARY_PROMPT": "999bd17d779380248681e6e992fad0daa5e785e7476898a28888f6e51fef42ec",
        "TOPIC_SUMMARY_PROMPT": "a4b96d5f3e317e0b5c4b047d9fbbe5c3378c55c5814dc5765201ea15d5c7beee",
        "SYSTEM2_AGENT_PROMPT": "7c681573ca35dca00bd79dd81b56cfb4e820db76c7bedcea620f15394a42cf48",
        "SYSTEM2_OUTPUT_INSTRUCTIONS": "2d6da105e45e333494122c03c4416a5a9ad90cdce3603ace7251731e1acafcc4",
        "SYSTEM2_SCHEMA_EVIDENCE_GATE": "cdfefe924a0d504593a04466cc48009c4583d9f6771fe8770a62e56f9ff4be1c",
        "SYSTEM2_SINGLE_CALL_REPLACEMENTS": "bac38a8ad4a03037d59b33360ad3b7de4729f454c3b5291f20a1d5cb8893043b",
        "SYSTEM2_MATERIALS": "0780c720db4f30bec96028353056e03b9fb60c8805cca7d74ff127566353ca15",
    },
}


_NEW_ONLY = {"OUTPUT_LANGUAGE_DIRECTIVE", "LANGUAGE_NAME"}
_TRANSLATED = [code for code in SUPPORTED_LANGUAGES if code not in ("en", "zh-Hans")]

_JSON_KEY_RE = re.compile(r'"([A-Za-z_][A-Za-z0-9_]*)"\s*:')
_ENUM_RE = re.compile(r'"([A-Z][A-Z0-9_]+)"')


def _sha(value) -> str:
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _fields(template: str) -> set:
    return {name for _, name, _, _ in Formatter().parse(template) if name is not None}


@pytest.mark.parametrize("language", ["en", "zh-Hans"])
def test_en_and_zh_hans_are_byte_identical_to_previous_release(language):
    pins = _PINNED_SHA256[language]
    assert set(pins) == set(PROMPT_NAMES) - _NEW_ONLY
    changed = [name for name, digest in pins.items()
               if _sha(get_prompt(name, language)) != digest]
    assert changed == []


def test_legacy_names_still_point_at_registry_strings():
    from hy_memory.agent import extractor, prompts_zh, reconciler
    from hy_memory.coding import extractor as coding_extractor
    from hy_memory.coding import prompts_zh as coding_zh

    assert extractor.EXTRACT_SYSTEM_PROMPT is get_prompt("EXTRACT_SYSTEM_PROMPT", "en")
    assert extractor.EXTRACT_FEW_SHOT_EN is get_prompt("EXTRACT_FEW_SHOT", "en")
    assert reconciler.RECONCILE_PROMPT is get_prompt("RECONCILE_PROMPT", "en")
    assert prompts_zh.EXTRACT_SYSTEM_PROMPT_ZH is get_prompt("EXTRACT_SYSTEM_PROMPT", "zh-Hans")
    assert prompts_zh.RECONCILE_FEW_SHOT_ZH is get_prompt("RECONCILE_FEW_SHOT", "zh-Hans")
    assert coding_extractor.EXTRACT_PROMPT_CODING is get_prompt("CODING_EXTRACT_PROMPT_CODING", "en")
    assert coding_zh.CHAIN_SUMMARY_PROMPT_ZH is get_prompt("CODING_CHAIN_SUMMARY_PROMPT", "zh-Hans")


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_language_defines_every_prompt(language):
    for name in PROMPT_NAMES:
        value = get_prompt(name, language)
        en = get_prompt(name, "en")
        assert type(value) is type(en), name
        if isinstance(value, str):
            assert value.strip(), name


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_format_prompts_keep_en_placeholders_and_render(language):
    for name, expected in FORMAT_FIELDS.items():
        template = get_prompt(name, language)
        assert _fields(template) == set(expected), name
        assert _fields(get_prompt(name, "en")) == set(expected), name
        rendered = template.format(**{field: f"<{field}>" for field in expected})
        for field in expected:
            assert f"<{field}>" in rendered, (name, field)


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_json_keys_and_enum_values_match_en(language):
    for name in PROMPT_NAMES:
        value, en = get_prompt(name, language), get_prompt(name, "en")
        if not isinstance(value, str):
            continue
        assert set(_JSON_KEY_RE.findall(value)) == set(_JSON_KEY_RE.findall(en)), name
        assert set(_ENUM_RE.findall(value)) == set(_ENUM_RE.findall(en)), name


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_system2_materials_entries(language):
    materials = get_prompt("SYSTEM2_MATERIALS", language)
    assert set(materials) == set(SYSTEM2_MATERIALS_FIELDS)
    for key, expected in SYSTEM2_MATERIALS_FIELDS.items():
        template = materials[key]
        assert _fields(template) == set(expected), key
        template.format(**{field: 1 for field in expected})


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_no_intention_fragments_match_their_own_prompt(language):
    system_prompt = get_prompt("EXTRACT_SYSTEM_PROMPT", language)
    pattern = get_prompt("NO_INTENTION_SECTION_PATTERN", language)
    assert re.search(pattern, system_prompt, flags=re.S), language
    for old, new in get_prompt("NO_INTENTION_REPLACEMENTS", language):
        assert isinstance(old, str) and isinstance(new, str)
    assert get_prompt("NO_INTENTION_REPLACEMENTS", language)[0][0] in system_prompt


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_system2_single_call_replacements_match_their_own_prompt(language):
    base = get_prompt("SYSTEM2_AGENT_PROMPT", language)
    pairs = get_prompt("SYSTEM2_SINGLE_CALL_REPLACEMENTS", language)
    assert pairs and pairs[0][0] in base
    if language not in ("en", "zh-Hans"):
        missing = [old for old, _ in pairs if old not in base]
        assert missing == []


@pytest.mark.parametrize("language", _TRANSLATED)
def test_translated_languages_are_not_copies(language):
    for name in ("EXTRACT_SYSTEM_PROMPT", "RECONCILE_PROMPT", "CODING_EXTRACT_PROMPT",
                 "SYSTEM2_AGENT_PROMPT", "OUTPUT_LANGUAGE_DIRECTIVE"):
        value = get_prompt(name, language)
        assert value != get_prompt(name, "en"), name
        assert value != get_prompt(name, "zh-Hans"), name


def test_zh_hant_uses_traditional_script():
    hant = get_prompt("EXTRACT_SYSTEM_PROMPT", "zh-Hant")
    # 记/记忆/对话 are Simplified-only forms; the Traditional prompt uses 記/對.
    for simplified in ("记忆", "对话", "用户"):
        assert simplified not in hant
    for traditional in ("記憶", "對話"):
        assert traditional in hant
