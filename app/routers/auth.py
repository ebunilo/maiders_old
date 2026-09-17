from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import models
from app.authz import ADMIN, home_for, path_allowed
from app.database import get_db
from app.security import verify_password

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory="app/templates")


def _landing_url(role: str, next_url: str | None) -> str:
    """The given `next` if it's a safe, same-site path this role may see;
    otherwise that role's own home page."""
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        if path_allowed(role, urlsplit(next_url).path):
            return next_url
    return home_for(role)


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/"):
    if request.session.get("user_id"):
        role = request.session.get("role", ADMIN)
        return RedirectResponse(url=_landing_url(role, next), status_code=303)
    return templates.TemplateResponse(
        request, "login.html", {"error": None, "next": next}
    )


@router.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
    db: Session = Depends(get_db),
):
    user = db.query(models.User).filter(models.User.username == username.strip()).first()
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Invalid username or password.", "next": next},
            status_code=401,
        )
    request.session.clear()
    request.session["user_id"] = user.id
    request.session["username"] = user.username
    request.session["role"] = user.role
    return RedirectResponse(url=_landing_url(user.role, next), status_code=303)


@router.get("/logout")
@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)
