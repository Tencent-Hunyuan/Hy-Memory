"""RCL-04 against a real HY instance instead of a stand-in.

Enable it with a configured, redacted model-profile database:

    ONE_MEMORY_MODEL_TEST_DB=/path/to/profile.sqlite3 \
      uv run --directory apps/core pytest -q tests/test_recall_fusion_live.py

The fusion tests elsewhere drive a fake client, so they prove the aggregator
merges two lanes but not that the semantic lane is really HY. This one puts a
memory in through the product's own import path, lets real HY store it, and
asks two questions of the same live instance:

  a natural question  -- overlaps the stored wording on one word, so only
                         HY's semantics can reach the answer
  the stored wording  -- which the lexical lane can answer on its own

The second is not decoration. The natural question comes back with an empty
raw lane, and empty alone cannot tell an unauthorized lane from an unwired one
from a merely strict one. Asking both in the same run settles which.

Seeding goes through `add_extracted` on purpose. This test isolates semantic
storage and retrieval from generative extraction quality, which has separate
deterministic and live acceptance coverage.
"""

import asyncio
import os
import runpy
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from one_memory.api import create_app
from one_memory.config import Settings
from one_memory.recall import _terms
from one_memory.secrets import KeyringSecretStore
from tests.source_consent import connection_request

PROFILE_DB = os.environ.get("ONE_MEMORY_MODEL_TEST_DB")
pytestmark = [
    pytest.mark.live,
    pytest.mark.serial,
    pytest.mark.skipif(not PROFILE_DB, reason="explicit configured provider required"),
]

STORED = "项目周会固定避开周五，星期五不安排项目周会。"
# Shares no term with the stored wording at all, so the lexical half of the
# memory lane cannot reach it and a hit can only have come from HY. The test
# checks that disjointness rather than trusting this comment.
QUESTION = "团队碰头会应该躲开一周里的哪一天"
# The same fact in the stored wording: the control for the lexical lane.
QUOTED = "项目周会固定避开周五"


def test_both_lanes_answer_through_one_real_hy_instance(tmp_path, monkeypatch):
    from hy_memory.client import HyMemoryClient

    # This run is fast because `add_extracted` needs no generation, which makes
    # "real HY" easy to assert and impossible to see. Counting the calls on the
    # real client is the difference between the two.
    stored: list[list[str]] = []
    searched: list[str] = []
    real_add_extracted = HyMemoryClient.add_extracted
    real_search = HyMemoryClient.search

    def counted_add_extracted(self, contents, **kwargs):
        stored.append(list(contents))
        return real_add_extracted(self, contents, **kwargs)

    def counted_search(self, query=None, **kwargs):
        searched.append(query)
        return real_search(self, query, **kwargs)

    def forbidden(*args, **kwargs):
        pytest.fail("Already-extracted content must not be replayed as raw chat")

    monkeypatch.setattr(HyMemoryClient, "add_extracted", counted_add_extracted)
    monkeypatch.setattr(HyMemoryClient, "search", counted_search)
    monkeypatch.setattr(HyMemoryClient, "add", forbidden)

    async def scenario():
        helpers = runpy.run_path(
            str(Path(__file__).resolve().parents[3] / "scripts/run_matter_first_import.py")
        )
        directory = tmp_path / "data"
        directory.mkdir(parents=True)
        helpers["copy_model_configuration"](Path(PROFILE_DB), directory / "one-memory.sqlite3")
        app = create_app(
            Settings(
                data_dir=directory,
                source_home=tmp_path / "home",
                api_token=SecretStr("live-fusion"),
                live_sync_enabled=False,
            ),
            secret_store=KeyringSecretStore(),
        )
        async with (
            app.router.lifespan_context(app),
            AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer live-fusion"},
            ) as client,
        ):
            upload = await client.post(
                "/api/v1/import-files?filename=weekly.txt&input_kind=memory_content",
                content=STORED.encode(),
            )
            assert upload.status_code == 201, upload.text
            receipt = upload.json()
            (
                await client.post(
                    f"/api/v1/import-files/{receipt['upload_id']}/commit",
                    json={"confirmed": True},
                )
            ).raise_for_status()
            linked = await client.post(
                "/api/v1/sources/connect",
                json=await connection_request(
                    client,
                    [receipt["source_id"]],
                    enable_sync=True,
                ),
            )
            linked.raise_for_status()
            job = await app.state.import_supervisor.wait(linked.json()["jobs"][0]["job_id"])
            assert job.stored == 1 and job.errors == 0, job
            processor = app.state.memory_processing_supervisor
            await asyncio.gather(*tuple(processor._debounces.values()), return_exceptions=True)
            await asyncio.wait_for(asyncio.gather(*tuple(processor._workers.values())), timeout=300)

            # Imported content is not shared with every app by default. Sharing
            # it here is what the memory lane is later asked to find.
            memories = (await client.get("/api/v1/memories")).json()
            assert memories, "real HY stored nothing to search for"
            for item in memories:
                (
                    await client.post(
                        f"/api/v1/memories/{item['memory_id']}/correct",
                        json={
                            "request_id": f"share-{item['memory_id']}",
                            "expected_revision": item["revision"],
                            "content": item["content"],
                            "scopes": ["all-apps"],
                        },
                    )
                ).raise_for_status()

            async def search(query: str) -> dict:
                response = await client.post(
                    "/api/v1/context/search",
                    json={"query": query, "kinds": ["raw", "memory"]},
                )
                assert response.status_code == 200, response.text
                return response.json()

            return await search(QUESTION), await search(QUOTED)

    asked, quoted = asyncio.run(scenario())

    assert stored == [[STORED]], stored
    # Both questions reached the real client, so the memory lane below is HY
    # answering and not a cache, a fixture or a lexical result mislabelled.
    assert searched == [QUESTION, QUOTED], searched
    # The memory lane matches terms as well as taking HY's ids, so a shared
    # word would let it answer without HY and make the next assertion empty.
    assert not (set(_terms(STORED)) & set(_terms(QUESTION))), "the question is not disjoint"

    memory = asked["paths"]["memory"]
    assert memory["status"] == "ok" and memory["returned_count"] > 0, asked["paths"]
    remembered = [item for item in asked["items"] if item["kind"] == "memory"]
    assert any("周五" in item["content"] or "星期五" in item["content"] for item in remembered), (
        remembered
    )

    # Same instance, same authorization, wording the lexical lane can match.
    # Without this the empty raw lane above would be unreadable.
    assert quoted["paths"]["raw"]["status"] == "ok", quoted["paths"]
    assert quoted["paths"]["raw"]["returned_count"] > 0, quoted["paths"]
    assert {item["kind"] for item in quoted["items"]} == {"raw", "memory"}, quoted["items"]

    # So the finding is about reach, not plumbing: the lexical lane wants most
    # of the query's words present and drops a question overlapping on one.
    # The synthetic benchmark measured the same shape (lexical-only hit 0.429);
    # this is that result on a memory the product actually stored.
    assert asked["paths"]["raw"]["status"] == "empty", asked["paths"]
