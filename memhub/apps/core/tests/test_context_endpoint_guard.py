import json

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from one_memory.external_memory_access import require_external_endpoint


def request(
    tools, scopes=("all-apps", "source:cursor"), method="POST", path="/api/v2/context/search"
):
    headers = {
        "x-one-memory-read-context": "external",
        "x-one-memory-installation-id": "00000000-0000-0000-0000-000000000001",
        "x-one-memory-installation-revision": "1",
        "x-one-memory-allowed-scopes": json.dumps(scopes),
        "x-one-memory-allowed-tools": json.dumps(tools),
    }
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
        }
    )


@pytest.mark.parametrize(
    "tools", [["om_recall"], ["om_conversations"], ["om_recall", "om_conversations"]]
)
def test_composite_route_accepts_existing_read_capabilities_for_later_body_validation(tools):
    require_external_endpoint(request(tools))


@pytest.mark.parametrize("tools", [["om_ask"], ["om_matters"], ["om_ask", "om_matters"]])
def test_other_capabilities_do_not_enable_composite_read(tools):
    with pytest.raises(HTTPException) as error:
        require_external_endpoint(request(tools))
    assert error.value.status_code == 403


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/v2/context/search"),
        ("POST", "/api/v1/context/search"),
        ("POST", "/api/v1/memory-library"),
        ("GET", "/api/v1/conversations/search"),
        ("GET", "/api/v1/conversations/evidence-id"),
        ("POST", "/api/v1/memories/ask"),
        ("POST", "/api/v1/boundary/peek"),
    ],
)
def test_composite_read_does_not_open_other_routes(method, path):
    with pytest.raises(HTTPException):
        require_external_endpoint(
            request(["om_recall", "om_conversations"], method=method, path=path)
        )


def test_legacy_recall_still_requires_its_own_tool():
    with pytest.raises(HTTPException):
        require_external_endpoint(request(["om_conversations"], path="/api/v1/recall"))
