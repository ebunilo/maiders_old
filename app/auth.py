from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

PUBLIC_PATHS = {"/login", "/logout", "/healthz", "/favicon.ico"}
PUBLIC_PREFIXES = ("/static/",)


class AuthMiddleware(BaseHTTPMiddleware):
    """Requires a logged-in session for every route except the ones above."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES):
            return await call_next(request)

        if request.session.get("user_id"):
            return await call_next(request)

        if request.headers.get("hx-request") == "true":
            return Response(status_code=401, headers={"HX-Redirect": "/login"})
        if path.startswith("/api/"):
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)

        query = f"?{request.url.query}" if request.url.query else ""
        return RedirectResponse(url=f"/login?next={path}{query}", status_code=303)
