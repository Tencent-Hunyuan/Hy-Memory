"""D98: the SDK's verbatim fallback must stay off.

Dev history: when the model extracted no fact, the coding extractor stored the
raw dialogue as a working fact, and with no words of the person's it took the
first 600 characters of the rendered trace. MemHub showed that as the memory
content, which is where `[0] assistant: ... [1] tool(?): ...` came from
(D87). The SDK fix remains present in 2.0.3: it does not store that fallback by
default.

This pins the default here, so a future bump that turns it back on, or our own
code enabling it, fails a test instead of putting rendered traces in the
memory list again.

The second test is the reason the first one is not enough: a config default
only holds if nothing overrides it.
"""

from pathlib import Path


def test_the_sdk_defaults_the_fallback_off() -> None:
    from hy_memory.config import CodingConfig

    config = CodingConfig()
    assert config.verbatim_fallback_enabled is False


def test_nothing_in_this_repo_enables_it() -> None:
    """A default is only a default while nobody sets it."""
    root = Path(__file__).resolve().parents[2]
    offenders: list[str] = []
    for path in list((root / "src").rglob("*.py")) + list((root / "scripts").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "MEMORY_CODING_VERBATIM_FALLBACK_ENABLED" in text:
            offenders.append(str(path))
        if "verbatim_fallback_enabled" in text and "True" in text:
            offenders.append(f"{path} (sets it)")
    assert not offenders, offenders
