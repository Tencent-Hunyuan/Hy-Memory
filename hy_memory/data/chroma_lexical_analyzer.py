"""Deterministic document/query analysis for the local Chroma FTS projection.

Only compiler-owned, ASCII-encoded tokens enter MATCH expressions. Encoding
retains symbol punctuation without relying on a custom SQLite tokenizer. The
analyzer never loads an NLP model or inspects paths on the filesystem.
"""

from dataclasses import dataclass
import re
from typing import Iterable, List, Tuple
import unicodedata


ANALYZER_VERSION = "2"

_CJK = r"[一-鿿㐀-䶿豈-﫿\U00020000-\U0002FA1F]+"
_TEXT_RUN = re.compile(_CJK + r"|[A-Za-z0-9]+")
_CJK_RUN = re.compile(_CJK)
_CAMEL_BOUNDARY = re.compile(
    r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])"
)
_PATH = re.compile(
    r"(?<![A-Za-z0-9_])(?:(?:[A-Za-z]:)?[\\/])?"
    r"(?:[\w@+~.-]+[\\/])+[\w@+~.-]+"
    r"|(?<![A-Za-z0-9_])[\w@+~-]+(?:\.[A-Za-z][A-Za-z0-9_+-]*)+(?![A-Za-z0-9_])"
)
_CODE_RUN = re.compile(
    r"[A-Za-z0-9_]+(?:[._+-][A-Za-z0-9_]+)*(?:\+\+|#)?"
)
_VERSION = re.compile(r"v?\d+(?:\.\d+)+(?:[-+][a-z0-9][a-z0-9._-]*)?", re.I)
_PLAIN_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9]*")

# A token that no user word can encode to: it prevents Chinese phrases from
# spanning punctuation, whitespace, content/tag boundaries or separate tags.
_BOUNDARY = "zboundary"


@dataclass(frozen=True)
class AnalyzedDocument:
    """Pre-tokenized column values stored in one ordinary FTS5 table."""

    terms: str
    symbols: str


@dataclass(frozen=True)
class CompiledQuery:
    """Safe column-scoped MATCH expressions; an empty string disables a branch."""

    terms_match: str
    symbols_match: str


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "")


def _encode(kind: str, value: str) -> str:
    return kind + value.encode("utf-8").hex()


def _term_groups(text: str) -> List[List[str]]:
    groups = []
    for match in _TEXT_RUN.finditer(text):
        value = match.group()
        if _CJK_RUN.fullmatch(value):
            pieces = (
                [value] if len(value) == 1
                else [value[i:i + 2] for i in range(len(value) - 1)]
            )
            groups.append([_encode("c", piece) for piece in pieces])
        else:
            # Keep complete camelCase terms, then their component words. All
            # spellings share the same case-folded ordinary-word namespace.
            pieces = list(dict.fromkeys([value.casefold()] + [
                piece.casefold() for piece in _CAMEL_BOUNDARY.split(value)
            ]))
            groups.extend([[_encode("w", piece)] for piece in pieces])
    return groups


def _symbols(text: str, *, document: bool) -> List[str]:
    symbols = []
    covered: List[Tuple[int, int]] = []
    for match in _PATH.finditer(text):
        path = match.group().rstrip(".").replace("\\", "/")
        if not path:
            continue
        covered.append(match.span())
        paths = [path]
        if document:
            # Keep the complete Unicode path and add an alias at outer
            # CJK/Latin boundaries: 配置文件在src/模块.py中 -> src/模块.py.
            # Interior Chinese components remain intact. A wholly Chinese
            # leading component is ambiguous and is deliberately preserved.
            alias = re.sub(r"^" + _CJK + r"(?=[A-Za-z0-9_@+~-])", "", path)
            alias = re.sub(r"(?<=[A-Za-z0-9_+-])" + _CJK + r"$", "", alias)
            if alias != path:
                paths.append(alias)
        for candidate in paths:
            symbols.append(_encode("p", candidate))
            basename = candidate.rsplit("/", 1)[-1]
            if document and basename != candidate:
                symbols.append(_encode("p", basename))

    for match in _CODE_RUN.finditer(text):
        if any(start < match.end() and match.start() < end for start, end in covered):
            continue
        value = match.group()
        if _VERSION.fullmatch(value):
            symbols.append(_encode("v", value.casefold()))
            continue
        letters = "".join(char for char in value if char.isalpha())
        code_shaped = (
            "_" in value or "-" in value or value.endswith(("++", "#"))
            or bool(_CAMEL_BOUNDARY.search(value))
            or (len(letters) >= 2 and letters.isupper())
            or (bool(letters) and any(char.isdigit() for char in value))
        )
        if code_shaped and letters:
            symbols.append(_encode("i", value.casefold()))

    # A lowercase query can find an uppercase error code or camelCase name.
    # Ordinary document words are not stored as symbols, so this does not give
    # every single English-word query a strong exact-match score.
    if not document and not symbols and _PLAIN_IDENTIFIER.fullmatch(text.strip()):
        symbols.append(_encode("i", text.strip().casefold()))
    return symbols


def analyze_document(content: str, tags: Iterable[str] = ()) -> AnalyzedDocument:
    """Analyze current content and tags with the same rules used by queries.

    Frequency is retained for BM25. Symbols preserve code punctuation; path
    separators are normalized but path case is preserved. Chinese runs use
    contiguous bigrams, with explicit boundaries between independent runs.
    """
    terms = []
    symbols = []
    for value in [content or "", *(tags or ())]:
        text = _normalize(value)
        for group in _term_groups(text):
            terms.extend(group)
            terms.append(_BOUNDARY)
        symbols.extend(_symbols(text, document=True))
    return AnalyzedDocument(" ".join(terms), " ".join(symbols))


def compile_query(query: str) -> CompiledQuery:
    """Compile literal user input into ordinary OR and exact-symbol AND queries.

    Each Chinese run is a phrase of adjacent bigrams. Every interpolated value
    is generated by the encoder, so quotes, OR, NEAR and paths in user input
    cannot become FTS operators or column filters.
    """
    text = _normalize(query)
    phrases = list(dict.fromkeys(
        '"' + " ".join(group) + '"' for group in _term_groups(text)
    ))
    symbols = list(dict.fromkeys(_symbols(text, document=False)))
    terms_match = "terms : (" + " OR ".join(phrases) + ")" if phrases else ""
    symbols_match = (
        "symbols : (" + " AND ".join('"' + token + '"' for token in symbols) + ")"
        if symbols else ""
    )
    return CompiledQuery(terms_match, symbols_match)
