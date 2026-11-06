"""A shared EmbedService must not send futures across event loops."""

import asyncio

from hy_memory.config import MemoryConfig
from hy_memory.core.embed_service import EmbedService


def test_queued_embeddings_do_not_wait_for_another_event_loop():
    service = EmbedService(MemoryConfig.from_dict({'embedder': {'embedding_dims': 1}}))
    service._embed_config.enable_cache = False
    service._embed_config.queue_batch_window_ms = 1
    calls = []

    async def embed(texts):
        calls.append((asyncio.get_running_loop(), list(texts)))
        return [[float(len(text))] for text in texts], [{} for _ in texts]

    service._embed_texts_with_meta = embed
    first_loop = asyncio.new_event_loop()
    second_loop = asyncio.new_event_loop()
    try:
        first = first_loop.create_task(service.embed_queued('first'))
        first_loop.run_until_complete(asyncio.sleep(0))
        assert not first.done()

        async def second():
            return await asyncio.wait_for(service.embed_queued('second'), timeout=2.0)

        assert second_loop.run_until_complete(second()) == [6.0]
        assert first_loop.run_until_complete(asyncio.wait_for(first, timeout=2.0)) == [5.0]
        assert calls == [(second_loop, ['second']), (first_loop, ['first'])]
    finally:
        for loop in (first_loop, second_loop):
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()
