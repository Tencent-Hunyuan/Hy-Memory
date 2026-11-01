"""Refuse query parameters a route does not declare.

FastAPI ignores an unknown query parameter. For a memory read that is a
silent widening: `memory-library?user_id=__nobody__` against a Core that did
not know `user_id` answered with the whole library, and the caller believed
it was isolated. Routes that carry this dependency answer such a request with
the same 422 a malformed body gets.
"""

from fastapi import Request
from fastapi.dependencies.models import Dependant
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute


def _declared_query_names(dependant: Dependant) -> set[str]:
    names = {parameter.alias for parameter in dependant.query_params}
    for dependency in dependant.dependencies:
        names |= _declared_query_names(dependency)
    return names


async def forbid_unknown_query_params(request: Request) -> None:
    route = request.scope.get("route")
    if not isinstance(route, APIRoute):
        return
    unknown = sorted(set(request.query_params) - _declared_query_names(route.dependant))
    if unknown:
        raise RequestValidationError(
            [
                {
                    "type": "extra_forbidden",
                    "loc": ("query", name),
                    "msg": "Extra inputs are not permitted",
                    "input": None,
                }
                for name in unknown
            ]
        )
