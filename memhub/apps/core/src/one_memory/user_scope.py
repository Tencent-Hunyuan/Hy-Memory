"""Who a memory belongs to: one Runtime, many `user_id`s.

A user is decided by which source root is read (a Hermes profile is its own
`state.db`), never by session or platform fields. The App never names a user
and keeps reading and writing `local`; every other harness stays on `local`
too. Only Hermes may connect a second root for another user.

In memory, a source's import lane is keyed by `lane_key(user_id, source_id)`.
For `local` that is the bare source id, so every existing key, health record
and log line stays byte-identical; another user's lane is `source@user`.
`@` cannot occur in a user id or a source id, so the key splits back exactly.
"""

import re
from typing import Annotated

from pydantic import Field

DEFAULT_USER_ID = "local"

#: `:` is excluded on purpose: the SDK joins ids with it when it keys history,
#: so a user id holding one could read another user's history.
USER_ID_PATTERN = r"^[A-Za-z0-9._-]{1,64}$"
_USER_ID = re.compile(USER_ID_PATTERN)

#: The only source whose roots may belong to a user other than `local`.
MULTI_USER_SOURCES = frozenset({"hermes"})

UserId = Annotated[str, Field(min_length=1, max_length=64, pattern=USER_ID_PATTERN)]

_LANE_SEPARATOR = "@"


def valid_user_id(value: str) -> bool:
    return bool(_USER_ID.fullmatch(value))


def lane_key(user_id: str, source_id: str) -> str:
    if user_id == DEFAULT_USER_ID:
        return source_id
    return f"{source_id}{_LANE_SEPARATOR}{user_id}"


def other_user_lane_prefix(source_id: str) -> str:
    """What every other user's lane of `source_id` starts with."""
    return f"{source_id}{_LANE_SEPARATOR}"


def split_lane(key: str) -> tuple[str, str]:
    """`(user_id, source_id)` of a lane key; a bare source id is `local`'s."""
    source_id, separator, user_id = key.partition(_LANE_SEPARATOR)
    if not separator or not valid_user_id(user_id):
        return DEFAULT_USER_ID, key
    return user_id, source_id


def owns(record_user_id: str | None, user_id: str) -> bool:
    """Whether a stored memory belongs to `user_id`.

    The SDK's `get`/`update`/`delete`/`history`/`get_write_outcome` take no
    user, so MemHub checks this before acting on an id. A memory written
    before MemHub named users carries `local` or nothing; both are `local`'s.
    """
    stored = record_user_id or DEFAULT_USER_ID
    return stored == user_id
