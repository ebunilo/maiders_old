"""The /reports pages over HTTP: role-aware links, query handling, and the
print layout's markup/CSS."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import auth as auth_middleware
from app import models
from app.authz import ADMIN, CUSTOMER_USER, SUPPLIER_USER
from app.main import app
from app.pdf import COMPANY_NAME
from app.routers import auth as auth_router
from app.security import hash_password
from conftest import AS_OF, days_before

PASSWORD = "test-password"


@pytest.fixture
def login(db, monkeypatch):
    """login(role) -> a TestClient signed in as a fresh user with that role.
    Non-admin roles are normally limited to office hours; lift that so the
    tests don't depend on when they run."""
    monkeypatch.setattr(auth_middleware, "login_restricted", lambda role, now=None: False)
    monkeypatch.setattr(auth_router, "login_restricted", lambda role, now=None: False)

    def make(role):
        username = f"user-{role}"
        db.add(models.User(username=username, password_hash=hash_password(PASSWORD), role=role))
        db.commit()
        client = TestClient(app)
        resp = client.post("/login", data={"username": username, "password": PASSWORD})
        assert resp.status_code == 200, resp.text
        return client

    return make


@pytest.fixture
def parties(make_customer, make_supplier):
    return {
        "debtor": make_customer("Owes Us", (days_before(10), 500, 0)),
        "overpaid": make_customer("Overpaid", (days_before(10), 100, 0), (days_before(5), 0, 300)),
        "supplier": make_supplier("We Owe", (days_before(10), 800, 0)),
    }


@pytest.mark.parametrize(
    "role, customer_links, supplier_links",
    [(ADMIN, True, True), (CUSTOMER_USER, True, False), (SUPPLIER_USER, False, True)],
)
def test_rows_only_link_to_statements_the_role_can_open(
    login, parties, role, customer_links, supplier_links
):
    client = login(role)
    html = client.get("/reports/creditors", params={"as_of": AS_OF.isoformat()}).text
    # Every role sees every row...
    assert "Overpaid" in html and "We Owe" in html
    # ...but only gets links it is allowed to follow.
    assert (f'href="/customers/{parties["overpaid"].id}"' in html) is customer_links
    assert (f'href="/suppliers/{parties["supplier"].id}"' in html) is supplier_links

    # And the links it gets actually open.
    if customer_links:
        assert client.get(f"/customers/{parties['overpaid'].id}", follow_redirects=False).status_code == 200
    if supplier_links:
        assert client.get(f"/suppliers/{parties['supplier'].id}", follow_redirects=False).status_code == 200


def test_debtors_page_shows_ageing_columns_and_totals(login, parties):
    html = login(ADMIN).get(
        "/reports/debtors", params={"as_of": AS_OF.isoformat(), "ageing": "1"}
    ).text
    for bucket in ("0-30 days", "31-60 days", "61-90 days", "Over 90 days"):
        assert bucket in html
    assert "Owes Us" in html and "Overpaid" not in html
    assert "₦500.00" in html
    assert "% of total" in html


def test_blank_as_of_and_unknown_sort_fall_back(login, parties):
    client = login(ADMIN)
    # A cleared date input submits as_of="" -- must not 422.
    resp = client.get("/reports/debtors", params={"as_of": "", "sort": "bogus"})
    assert resp.status_code == 200
    assert re.search(r'<option value="name"\s+selected>', resp.text)
    assert client.get("/reports/debtors", params={"as_of": "31/12/2026"}).status_code == 400


def test_print_layout_markup(login, parties):
    html = login(ADMIN).get("/reports/creditors", params={"as_of": AS_OF.isoformat()}).text
    # Letterhead only appears in print; the filter/print controls are hidden.
    assert 'class="report-header print-only"' in html
    assert COMPANY_NAME in html
    assert "As of 30 Jun 2026" in html
    assert re.search(r'class="[^"]*\bno-print\b[^"]*"[^>]*>\s*<div>\s*<h3', html)


def test_print_css_repeats_headers_on_each_a4_page():
    css = Path("app/static/styles.css").read_text()
    print_css = css[css.index("@media print") :]
    assert re.search(r"size:\s*A4", print_css)
    assert re.search(r"nav\.navbar,[^{]*\.no-print\s*{\s*display:\s*none", print_css)
    assert re.search(r"\.report-table thead\s*{\s*display:\s*table-header-group", print_css)
    assert re.search(r"\.report-table tr\s*{\s*page-break-inside:\s*avoid", print_css)
