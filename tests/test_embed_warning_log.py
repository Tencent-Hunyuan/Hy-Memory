# -*- coding: utf-8 -*-
"""EmbedService INFO 日志：含 request_id / provider / model / api / 入参 / 出参(dims)。"""

from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

from hy_memory.config import MemoryConfig, EmbedderConfig
from hy_memory.core.embed_service import EmbedService, _embeddings_api_url
from hy_memory.utils.log_setup import set_request_id


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestEmbeddingsApiUrl:
    def test_join_v1(self):
        assert _embeddings_api_url("https://host/v1") == "https://host/v1/embeddings"

    def test_strip_trailing_slash(self):
        assert _embeddings_api_url("https://host/v1/") == "https://host/v1/embeddings"

    def test_empty(self):
        assert _embeddings_api_url("") == "/embeddings"


class TestEmbedInfoLog:
    def test_success_logs_info_with_dims_not_vector(self, caplog):
        async def _test():
            cfg = MemoryConfig(
                embedder=EmbedderConfig(
                    provider="openai",
                    model="text-embedding-test",
                    api_key="sk-test",
                    base_url="https://example.com/v1",
                    embedding_dims=8,
                    max_retries=0,
                )
            )
            svc = EmbedService(cfg)
            set_request_id("rid-embed-001")

            fake_usage = MagicMock()
            fake_usage.model_dump.return_value = {"prompt_tokens": 3, "total_tokens": 3}
            fake_item = MagicMock()
            fake_item.embedding = [0.123456789] * 8
            fake_resp = MagicMock()
            fake_resp.model = "text-embedding-test"
            fake_resp.usage = fake_usage
            fake_resp.data = [fake_item]

            fake_raw = MagicMock()
            fake_raw.status_code = 200
            fake_raw.headers = {"content-type": "application/json", "authorization": "secret"}
            fake_raw.parse.return_value = fake_resp

            create = AsyncMock(return_value=fake_raw)
            fake_client = MagicMock()
            fake_client.embeddings.with_raw_response.create = create

            with patch(
                "openai.AsyncOpenAI",
                return_value=fake_client,
            ), caplog.at_level(logging.INFO, logger="hy_memory.core.embed_service"):
                vectors, meta = await svc._embed_openai(["hello world"])

            assert len(vectors) == 1
            assert len(vectors[0]) == 8
            assert meta["http_status"] == 200

            info_msgs = [
                r.getMessage()
                for r in caplog.records
                if r.name == "hy_memory.core.embed_service"
                and r.levelno == logging.INFO
                and r.getMessage().startswith("[embed] {")
            ]
            assert len(info_msgs) == 1
            payload = json.loads(info_msgs[0][len("[embed] "):])
            assert payload["request_id"] == "rid-embed-001"
            assert payload["status"] == "ok"
            assert payload["provider"] == "openai"
            assert payload["model"] == "text-embedding-test"
            assert payload["api"] == "https://example.com/v1/embeddings"
            # 待向量化的原文不进 INFO，只记每条字符数
            assert "input" not in payload["request"]
            assert payload["request"]["input_chars"] == [11]
            assert "hello world" not in info_msgs[0]
            assert payload["request"]["dimensions"] == 8
            assert payload["response"]["vector_dims"] == [8]
            assert payload["response"]["vector_count"] == 1
            # 不得把向量数值打进日志（用哨兵值检测，避免与耗时等数字误撞）
            blob = json.dumps(payload)
            assert "0.123456789" not in blob
            assert "embedding" not in payload["response"]

        run(_test())
