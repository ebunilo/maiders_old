"""Shared fixtures. Tests run against a real, throwaway PostgreSQL database
named by TEST_DATABASE_URL -- never the app's own DATABASE_URL, since every
test wipes all tables. The app reads its database URL at import time, so it
is pointed at the test database here, before anything imports `app`."""

import datetime
import os
from decimal import Decimal

import pytest
from sqlalchemy.engine import make_url

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
if not TEST_DATABASE_URL:
    pytest.exit(
        "Set TEST_DATABASE_URL to a disposable PostgreSQL database, e.g.\n"
        "  TEST_DATABASE_URL=postgresql+psycopg2://postgres@localhost:5432/maiders_test",
        returncode=2,
    )
if not (make_url(TEST_DATABASE_URL).database or "").endswith("_test"):
    pytest.exit("Refusing to run: the TEST_DATABASE_URL database name must end in _test", returncode=2)

os.environ["DATABASE_URL"] = TEST_DATABASE_URL
# Don't let a developer's .env seed an admin user into the test database.
os.environ["ADMIN_USERNAME"] = ""
os.environ["ADMIN_PASSWORD"] = ""

from sqlalchemy import text  # noqa: E402

from app import models  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402

# Every report test pins "as of" to this date, so ages don't depend on today.
AS_OF = datetime.date(2026, 6, 30)


def days_before(days: int) -> datetime.date:
    return AS_OF - datetime.timedelta(days=days)


@pytest.fixture(scope="session", autouse=True)
def _schema():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture
def db():
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    session = SessionLocal()
    yield session
    session.close()


def _add_party(db, party_model, txn_model, fk, name, entries):
    party = party_model(code=name.upper().replace(" ", "")[:20], name=name)
    db.add(party)
    db.flush()
    for date_posted, dr, cr in entries:
        db.add(
            txn_model(
                **{fk: party.id},
                date_posted=date_posted,
                amount_dr=Decimal(str(dr)),
                amount_cr=Decimal(str(cr)),
            )
        )
    db.commit()
    return party


@pytest.fixture
def make_customer(db):
    """make_customer(name, (date, dr, cr), ...) -> Customer"""

    def make(name, *entries):
        return _add_party(db, models.Customer, models.Transaction, "customer_id", name, entries)

    return make


@pytest.fixture
def make_supplier(db):
    """make_supplier(name, (date, dr, cr), ...) -> Supplier"""

    def make(name, *entries):
        return _add_party(
            db, models.Supplier, models.SupplierTransaction, "supplier_id", name, entries
        )

    return make
