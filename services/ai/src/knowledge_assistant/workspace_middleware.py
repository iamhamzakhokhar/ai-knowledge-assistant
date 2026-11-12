"""Check browser mutation provenance and size before FastAPI parses the body."""

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_MAX_BODY = 4096


class WorkspaceRequestMiddleware:
    def __init__(self, app: ASGIApp, allowed_origins: tuple[str, ...]) -> None:
        self.app = app
        self.allowed_origins = allowed_origins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        root_path = scope.get("root_path", "").rstrip("/")
        if root_path and path.startswith(root_path + "/"):
            path = path[len(root_path) :]
        is_workspace = path == "/api/v1/teams" or path.startswith(
            ("/api/v1/auth/", "/api/v1/teams/")
        )
        if scope["type"] != "http" or not is_workspace:
            await self.app(scope, receive, send)
            return

        async def send_private(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["Cache-Control"] = "no-store"
            await send(message)

        if scope["method"] in {"GET", "HEAD", "OPTIONS"}:
            await self.app(scope, receive, send_private)
            return
        headers = Headers(scope=scope)
        origins = headers.getlist("origin")
        if len(origins) != 1 or origins[0] not in self.allowed_origins:
            await JSONResponse({"detail": "Origin is not allowed"}, 403)(
                scope, receive, send_private
            )
            return
        if headers.get("content-type", "").partition(";")[0].strip().lower() != "application/json":
            await JSONResponse({"detail": "Expected application/json"}, 415)(
                scope, receive, send_private
            )
            return

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > _MAX_BODY:
                await JSONResponse({"detail": "Request is too large"}, 413)(
                    scope, receive, send_private
                )
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break

        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if replayed:
                return await receive()
            replayed = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay, send_private)
