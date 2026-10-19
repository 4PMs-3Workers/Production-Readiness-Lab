"""Bound ASGI request bodies before routing and replay accepted bytes downstream."""

from starlette.types import Message, Receive

from app.core.errors import AppError


async def read_limited_body(receive: Receive, *, max_bytes: int) -> bytes | None:
    """Count received bytes rather than trusting client-provided length headers."""
    body = bytearray()
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return None
        chunk = message.get("body", b"")
        if len(body) + len(chunk) > max_bytes:
            raise AppError(413, "PAYLOAD_TOO_LARGE", "Request payload too large", "input")
        body.extend(chunk)
        if not message.get("more_body", False):
            return bytes(body)


def replay_body(body: bytes, receive: Receive) -> Receive:
    consumed = False

    async def replay() -> Message:
        nonlocal consumed
        if not consumed:
            consumed = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return replay
