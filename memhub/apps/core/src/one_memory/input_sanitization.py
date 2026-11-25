import re
from collections.abc import Callable
from dataclasses import dataclass

REDACTED_SECRET = "<redacted-secret>"

# A key clipped before its END marker is still a key: take it to the end.
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----(?:.*?-----END [A-Z0-9 ]*PRIVATE KEY-----|.*\Z)",
    re.DOTALL,
)
_AUTHORIZATION = re.compile(
    r"(?i)\b(Authorization\s*:\s*(?:Bearer|Basic|Token|ApiKey))"
    r"\s+[A-Za-z0-9._~+/=-]{8,}"
)
_CLI_SECRET = re.compile(
    r"(?i)(--(?:password|passwd|token|api[-_]?key|apikey|appkey|secret))"
    r"(\s+)([^\s\"']+|\"[^\"]+\"|'[^']+')"
)
_ASSIGNED_SECRET = re.compile(
    r"(?i)(?P<key_quote>[\"']?)(?P<key>password|passwd|token|api[-_]?key|"
    r"apikey|appkey|secret|access[-_]?key|secret[-_]?key)(?P=key_quote)"
    r"(?P<separator>\s*[:=]\s*)"
    r"(?P<value>[^\s,;\"']+|\"[^\"]+\"|'[^']+')"
)
_KNOWN_TOKEN = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{12,}|ghp_[A-Za-z0-9]{20,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|AKID[A-Za-z0-9]{12,})\b"
)
_URL_CREDENTIAL = re.compile(
    # Any scheme, not just http(s): a database or broker URL carries the
    # same `user:password@` block in a DATABASE_URL / REDIS_URL style value.
    r"(?P<scheme>[A-Za-z][A-Za-z0-9+.\-]*://)(?P<user>[^/\s:@]*):(?P<password>[^/\s@]+)@",
    re.IGNORECASE,
)


def sanitize_model_input(value: str) -> str:
    """Remove credentials before user-controlled history crosses into an LLM."""
    for pattern, replacement in _STEPS:
        value = pattern.sub(replacement, value)
    return value


def _redact_assignment(match: re.Match[str]) -> str:
    value = match.group("value")
    quote = value[0] if value.startswith(("'", '"')) else ""
    return (
        f"{match.group('key_quote')}{match.group('key')}{match.group('key_quote')}"
        f"{match.group('separator')}{quote}{REDACTED_SECRET}{quote}"
    )


_STEPS: tuple[tuple[re.Pattern[str], str | Callable[[re.Match[str]], str]], ...] = (
    (_PRIVATE_KEY, REDACTED_SECRET),
    (_AUTHORIZATION, rf"\1 {REDACTED_SECRET}"),
    (_CLI_SECRET, rf"\1\2{REDACTED_SECRET}"),
    (_ASSIGNED_SECRET, _redact_assignment),
    (_KNOWN_TOKEN, REDACTED_SECRET),
    (
        _URL_CREDENTIAL,
        lambda match: f"{match.group('scheme')}{match.group('user')}:{REDACTED_SECRET}@",
    ),
)


@dataclass(frozen=True)
class SanitizedCopySpan:
    """Exact unchanged output/source code-point intervals, never replacement text."""

    start_cp: int
    end_cp: int
    source_start_cp: int
    source_end_cp: int


@dataclass(frozen=True)
class SanitizedInput:
    text: str
    spans: tuple[SanitizedCopySpan, ...]
    excluded: tuple[tuple[int, int], ...]


def sanitize_model_input_with_spans(value: str) -> SanitizedInput:
    """Preserve exact unchanged ranges using the same six ordered sanitizer passes.

    Each entire regex replacement is conservatively synthetic, including any
    copied labels inside that replacement. Synthetic regions have no source
    citation. Gaps in source coordinates are explicit exclusions; callers must
    never concatenate across those gaps and claim a continuous original quote.
    This is not host-wrapper parsing, index admission or a persisted map schema.
    """
    original_length = len(value)
    spans = [SanitizedCopySpan(0, len(value), 0, len(value))] if value else []
    for pattern, replacement in _STEPS:
        value, spans = _mapped_sub(value, spans, pattern, replacement)
    excluded = []
    previous = 0
    for span in spans:
        if previous < span.source_start_cp:
            excluded.append((previous, span.source_start_cp))
        previous = span.source_end_cp
    if previous < original_length:
        excluded.append((previous, original_length))
    return SanitizedInput(value, tuple(spans), tuple(excluded))


def _mapped_sub(
    value: str,
    spans: list[SanitizedCopySpan],
    pattern: re.Pattern[str],
    replacement: str | Callable[[re.Match[str]], str],
) -> tuple[str, list[SanitizedCopySpan]]:
    pieces: list[str] = []
    mapped: list[SanitizedCopySpan] = []
    output_length = source_cursor = span_cursor = 0

    def copy(start: int, end: int) -> None:
        nonlocal output_length, span_cursor
        pieces.append(value[start:end])
        while span_cursor < len(spans) and spans[span_cursor].end_cp <= start:
            span_cursor += 1
        position = span_cursor
        while position < len(spans) and spans[position].start_cp < end:
            span = spans[position]
            left, right = max(start, span.start_cp), min(end, span.end_cp)
            if left < right:
                mapped.append(
                    SanitizedCopySpan(
                        output_length + left - start,
                        output_length + right - start,
                        span.source_start_cp + left - span.start_cp,
                        span.source_start_cp + right - span.start_cp,
                    )
                )
            position += 1
        output_length += end - start

    for match in pattern.finditer(value):
        copy(source_cursor, match.start())
        changed = replacement(match) if callable(replacement) else match.expand(replacement)
        pieces.append(changed)
        output_length += len(changed)
        source_cursor = match.end()
    copy(source_cursor, len(value))
    return "".join(pieces), mapped
