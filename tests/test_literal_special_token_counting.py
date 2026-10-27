"""User text may quote tokenizer control spellings without using control tokens."""

import pytest
import tiktoken

from hy_memory.core.embed_service import _count_tokens, _truncate_for_embedding
from hy_memory.utils.token_count import count_tokens


@pytest.mark.parametrize('counter', [count_tokens, _count_tokens])
@pytest.mark.parametrize('marker', ['<|endoftext|>', '<|fim_prefix|>'])
def test_literal_special_tokens_are_counted_as_ordinary_text(marker, counter):
    text = marker + '你好世界' * 20
    expected = len(tiktoken.get_encoding('cl100k_base').encode_ordinary(text))
    assert counter(text) == expected


def test_literal_special_token_does_not_bypass_embedding_limit():
    text = '<|endoftext|>' + '你好世界' * 3000
    encoder = tiktoken.get_encoding('cl100k_base')
    truncated = _truncate_for_embedding(text)
    assert truncated != text
    assert len(encoder.encode_ordinary(truncated)) <= 8192 - 64
    assert truncated.startswith('<|endoftext|>')


def test_short_literal_special_token_text_is_preserved():
    text = 'Explain <|endoftext|> as a literal string.'
    assert _truncate_for_embedding(text) == text
