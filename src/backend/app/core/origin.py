"""Origin is a parsed scheme/hostname/effective-port tuple, never a prefix."""

from urllib.parse import urlsplit

from starlette.requests import Request

from app.api.auth_errors import auth_error
from app.core.config import Settings, _valid_hostname


def parse_origin(value: str, *, referer: bool = False) -> tuple[str, str, int]:
    if any(c.isspace() or ord(c) < 32 or c == "\\" for c in value):
        raise ValueError("Invalid origin")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or not _valid_hostname(parsed.hostname)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or (not referer and (parsed.path or "?" in value or "#" in value))
    ):
        raise ValueError("Invalid origin")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not 1 <= port <= 65535 or parsed.port == 0:
        raise ValueError("Invalid origin")
    return parsed.scheme, parsed.hostname.lower(), port


def require_same_origin(request: Request, settings: Settings) -> None:
    origins = request.headers.getlist("origin")
    values = origins if origins else request.headers.getlist("referer")
    try:
        if len(values) != 1:
            raise ValueError("Origin required")
        actual = parse_origin(values[0], referer=not origins)
        if actual != parse_origin(settings.application_origin):
            raise ValueError("Origin mismatch")
    except ValueError:
        raise auth_error("INVALID_ORIGIN") from None
