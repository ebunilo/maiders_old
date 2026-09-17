from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from app.authz import ADMIN, home_for, path_allowed

PUBLIC_PATHS = {"/login", "/logout", "/healthz", "/favicon.ico"}
PUBLIC_PREFIXES = ("/static/",)


class AuthMiddleware(BaseHTTPMiddleware):
    """Requires a logged-in session for every route except the ones above,
    and restricts the rest to what the session's role is allowed to see."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES):
            return await call_next(request)

        if not request.session.get("user_id"):
            if request.headers.get("hx-request") == "true":
                return Response(status_code=401, headers={"HX-Redirect": "/login"})
            if path.startswith("/api/"):
                return JSONResponse({"detail": "Not authenticated"}, status_code=401)
            query = f"?{request.url.query}" if request.url.query else ""
            return RedirectResponse(url=f"/login?next={path}{query}", status_code=303)

        role = request.session.get("role", ADMIN)
        if path_allowed(role, path, request.method):
            return await call_next(request)

        home = home_for(role)
        if request.headers.get("hx-request") == "true":
            return Response(status_code=403, headers={"HX-Redirect": home})
        if path.startswith("/api/"):
            return JSONResponse({"detail": "Forbidden"}, status_code=403)
        return RedirectResponse(url=home, status_code=303)
