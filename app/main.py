from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app import models
from app.auth import AuthMiddleware
from app.config import settings
from app.database import Base, SessionLocal, engine
from app.routers import auth, customers, dashboard, pages, transactions
from app.security import hash_password

app = FastAPI(title="Customer Transactions Ledger")

Base.metadata.create_all(bind=engine)

if settings.admin_username and settings.admin_password:
    db = SessionLocal()
    try:
        if db.query(models.User).count() == 0:
            db.add(
                models.User(
                    username=settings.admin_username,
                    password_hash=hash_password(settings.admin_password),
                )
            )
            db.commit()
    finally:
        db.close()

# SessionMiddleware must run before AuthMiddleware so request.session exists;
# Starlette wraps middleware in the reverse of the order added, so it's
# added second here.
app.add_middleware(AuthMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie="maiders_session",
    max_age=settings.session_max_age_seconds,
    same_site="lax",
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


app.include_router(auth.router)
app.include_router(customers.router)
app.include_router(transactions.router)
app.include_router(dashboard.router)
app.include_router(pages.router)
