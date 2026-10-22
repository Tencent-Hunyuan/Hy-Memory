import hmac
import ipaddress
from collections.abc import Callable
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, status

from one_memory.config import Settings

DEVELOPMENT_ORIGIN = "http://127.0.0.1:5173"
DESKTOP_ORIGIN = "one-memory://app"
ALLOWED_RENDERER_ORIGINS = frozenset({DEVELOPMENT_ORIGIN, DESKTOP_ORIGIN})
UI_SESSION_COOKIE = "one_memory_session"
LOOPBACK_HOSTNAMES = frozenset({"127.0.0.1", "localhost", "::1"})


def renderer_origins(settings: Settings) -> frozenset[str]:
    return frozenset({settings.development_origin, DESKTOP_ORIGIN})


def is_loopback_origin(origin: str) -> bool:
    """Whether a page origin is this machine talking to itself.

    The web app is served by the Core on the same machine, so a browser shows
    one of the loopback names for it rather than the single dev-server port
    the allow list happened to name. Accepting loopback is what lets the page
    write at all; it is not a loosening beyond the machine, because every
    other hostname is still refused.
    """
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = parsed.hostname
    if not host:
        return False
    if host in LOOPBACK_HOSTNAMES:
        return True
    # A prefix check is not enough: "127.attacker.example" is a perfectly valid
    # DNS name that resolves wherever its owner wants (including 127.0.0.1 via
    # DNS rebinding). Parse the address and ask the standard library.
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def local_api_guard(
    settings: Settings,
    *,
    valid_ui_session: Callable[[str], bool] | None = None,
) -> Callable[[Request], None]:
    expected_token = settings.api_token.get_secret_value()

    def require_local_api(request: Request) -> None:
        origin = request.headers.get("origin")
        if (
            origin is not None
            and origin not in renderer_origins(settings)
            and not is_loopback_origin(origin)
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="origin_not_allowed",
            )
        authorization = request.headers.get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        bearer_ok = scheme.lower() == "bearer" and hmac.compare_digest(token, expected_token)
        if not bearer_ok:
            cookie = request.cookies.get(UI_SESSION_COOKIE)
            if valid_ui_session is None or cookie is None or not valid_ui_session(cookie):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="invalid_local_token",
                    headers={"WWW-Authenticate": "Bearer"},
                )

    return require_local_api
