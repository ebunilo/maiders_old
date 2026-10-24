"""Reconcile the reports against the real legacy exports: import both CSVs,
then check every party's report balance against its statement, which sums
the running balance transaction by transaction in Python instead of in SQL."""

import sys
from decimal import Decimal
from pathlib import Path

import pytest

from app import crud, models
from scripts import import_csv, import_supplier_csv

CUSTOMER_CSV = Path("Customer Transactions_114458.csv")
SUPPLIER_CSV = Path("Supplier Ledger_114158.csv")

pytestmark = pytest.mark.skipif(
    not (CUSTOMER_CSV.exists() and SUPPLIER_CSV.exists()), reason="legacy CSV exports not present"
)


def statement_balance(statement) -> Decimal:
    return statement[-1][1] if statement else Decimal("0")


def test_reports_reconcile_with_statements_on_legacy_data(db, monkeypatch):
    for module, path in ((import_csv, CUSTOMER_CSV), (import_supplier_csv, SUPPLIER_CSV)):
        monkeypatch.setattr(sys, "argv", ["import", str(path)])
        module.main()

    debtors = {r["id"]: r for r in crud.debtors_report(db, ageing=True)[0]["rows"]}
    suppliers_sec, overpaid_sec = crud.creditors_report(db, ageing=True)
    owed = {r["id"]: r for r in suppliers_sec["rows"]}
    overpaid = {r["id"]: r for r in overpaid_sec["rows"]}
    assert debtors and owed, "import produced no balances to check"

    for c in db.query(models.Customer).all():
        balance = statement_balance(crud.get_full_customer_statement(db, c.id))
        if balance > 0:
            assert debtors[c.id]["balance"] == balance, c.name
            assert c.id not in overpaid
        elif balance < 0:
            assert overpaid[c.id]["balance"] == -balance, c.name
            assert c.id not in debtors
        else:
            assert c.id not in debtors and c.id not in overpaid, c.name

    for s in db.query(models.Supplier).all():
        balance = statement_balance(crud.get_full_supplier_statement(db, s.id))
        if balance > 0:
            assert owed[s.id]["balance"] == balance, s.name
        else:
            assert s.id not in owed, s.name

    for row in [*debtors.values(), *owed.values(), *overpaid.values()]:
        assert sum(row["ageing"]) == row["balance"], row["name"]
