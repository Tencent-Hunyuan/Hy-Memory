import json

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from one_memory.external_memory_access import require_external_endpoint


def request(
    tools,
    scopes=("all-apps", "source:cursor"),
    method="POST",
    path="/api/v1/context/search",
    host_id=None,
):
    headers = {
        "x-one-memory-read-context": "external",
        "x-one-memory-installation-id": "00000000-0000-0000-0000-000000000001",
        "x-one-memory-installation-revision": "1",
        "x-one-memory-allowed-scopes": json.dumps(scopes),
        "x-one-memory-allowed-tools": json.dumps(tools),
    }
    if host_id is not None:
        headers["x-one-memory-host-id"] = host_id
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
        ("GET", "/api/v1/context/search"),
        ("POST", "/api/v1/memory-library"),
        ("GET", "/api/v1/conversations/evidence-id"),
    ],
)
def test_composite_read_does_not_open_other_routes(method, path):
    with pytest.raises(HTTPException):
        require_external_endpoint(
            request(["om_recall", "om_conversations"], method=method, path=path)
        )


def test_generic_scope_requires_exact_host_identity():
    from one_memory.external_memory_access import external_access

    scopes = ("all-apps", "raw-imported-all")
    generic = request(["om_recall", "om_conversations"], scopes=scopes, host_id="generic-local-mcp")
    assert external_access(generic.headers).scopes == frozenset(scopes)
    for host_id in (None, "cursor", "generic-local-mcp-other"):
        with pytest.raises(HTTPException) as error:
            require_external_endpoint(
                request(["om_recall", "om_conversations"], scopes=scopes, host_id=host_id)
            )
        assert error.value.status_code == 403


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/v1/conversations/search"),
        ("POST", "/api/v1/memories/ask"),
        ("POST", "/api/v1/matters/context/search"),
        ("POST", "/api/v1/diagnostics/trace"),
        ("POST", "/api/v1/boundary/peek"),
        ("POST", "/api/v1/call-events/123e4567-e89b-42d3-a456-426614174000/delivery"),
        ("GET", "/api/v1/context/search"),
        ("POST", "/api/v1/memories"),
    ],
)
def test_generic_host_rejects_all_non_recall_and_context_routes(method, path):
    with pytest.raises(HTTPException) as error:
        require_external_endpoint(
            request(
                ["om_recall", "om_conversations"],
                scopes=("all-apps", "raw-imported-all"),
                method=method,
                path=path,
                host_id="generic-local-mcp",
            )
        )
    assert error.value.status_code == 403
    assert error.value.detail == "external_endpoint_denied"


@pytest.mark.parametrize("path", ["/api/v1/recall", "/api/v1/context/search"])
def test_generic_host_allows_only_its_read_routes(path):
    require_external_endpoint(
        request(
            ["om_recall", "om_conversations"],
            scopes=("all-apps", "raw-imported-all"),
            path=path,
            host_id="generic-local-mcp",
        )
    )


def test_legacy_recall_still_requires_its_own_tool():
    with pytest.raises(HTTPException):
        require_external_endpoint(request(["om_conversations"], path="/api/v1/recall"))


def test_legacy_raw_route_still_requires_raw_source_authority():
    with pytest.raises(HTTPException):
        require_external_endpoint(
            request(
                ["om_conversations"],
                scopes=("all-apps",),
                method="GET",
                path="/api/v1/conversations/search",
            )
        )
