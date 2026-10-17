"""Explicit fixture-user confirmation through metadata-only public endpoints."""

from uuid import uuid4


async def authorized_request(client, source_id, *, confirm_environment_override=False):
    location = await client.post(f"/api/v1/sources/{source_id}/location")
    location.raise_for_status()
    assert location.json()["state"] == "found"
    return {
        "expected_root_id": location.json()["root_id"],
        "user_confirmed": True,
        "confirm_environment_override": confirm_environment_override,
    }


async def thaw_source(client, source_id, receipt):
    """Turn on the single Harness processing switch after authorization."""
    del receipt
    response = await client.post(
        f"/api/v1/sources/{source_id}/sync-permission",
        json={"enabled": True},
    )
    response.raise_for_status()
    assert response.json()["sync_enabled"] is True
    return response.json()


async def connection_request(client, source_ids, **options):
    roots = {}
    for source_id in source_ids:
        location = await client.post(f"/api/v1/sources/{source_id}/location")
        location.raise_for_status()
        assert location.json()["state"] == "found"
        roots[source_id] = location.json()["root_id"]
    return {
        "source_ids": source_ids,
        "request_id": str(uuid4()),
        "source_roots": roots,
        **options,
    }
