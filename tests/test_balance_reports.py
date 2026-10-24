"""Debtors/creditors reports and their ageing split (crud level)."""

import datetime
import random
from collections import deque
from decimal import Decimal

import pytest

from app import crud
from conftest import AS_OF, days_before

D = Decimal


def section(sections, title):
    return next(s for s in sections if s["title"] == title)


def row_for(sec, party):
    rows = [r for r in sec["rows"] if r["id"] == party.id]
    return rows[0] if rows else None


# --- Hand-worked cases --------------------------------------------------------


def test_payment_settles_oldest_invoice_first(db, make_customer):
    # 100 invoiced 120 days ago, 50 invoiced 10 days ago, 100 paid: the old
    # invoice is settled, so the 50 still owed is all recent.
    c = make_customer(
        "Ada",
        (days_before(120), 100, 0),
        (days_before(10), 50, 0),
        (days_before(5), 0, 100),
    )
    row = row_for(section(crud.debtors_report(db, as_of=AS_OF, ageing=True), "Customers"), c)
    assert row["balance"] == D("50")
    assert row["ageing"] == [D("50"), D("0"), D("0"), D("0")]


def test_partial_payment_ages_remainder_across_invoices(db, make_customer):
    # Invoices of 100 (100 days ago), 200 (45 days ago), 300 (15 days ago);
    # 350 paid. Oldest first: the 100 and 200 are cleared and 50 of the 300,
    # leaving 250 -- all from the 15-day-old invoice.
    c = make_customer(
        "Bola",
        (days_before(100), 100, 0),
        (days_before(45), 200, 0),
        (days_before(15), 300, 0),
        (days_before(1), 0, 350),
    )
    row = row_for(section(crud.debtors_report(db, as_of=AS_OF, ageing=True), "Customers"), c)
    assert row["balance"] == D("250")
    assert row["ageing"] == [D("250"), D("0"), D("0"), D("0")]

    # Pay only 50 instead: 50 of the oldest is cleared, so the rest spreads
    # across every bucket the invoices fall in.
    c2 = make_customer(
        "Chidi",
        (days_before(100), 100, 0),
        (days_before(45), 200, 0),
        (days_before(15), 300, 0),
        (days_before(1), 0, 50),
    )
    row = row_for(section(crud.debtors_report(db, as_of=AS_OF, ageing=True), "Customers"), c2)
    assert row["balance"] == D("550")
    assert row["ageing"] == [D("300"), D("200"), D("0"), D("50")]


def test_fully_paid_customer_is_on_neither_report(db, make_customer):
    c = make_customer("Dayo", (days_before(40), 500, 0), (days_before(20), 0, 500))
    debtors = section(crud.debtors_report(db, as_of=AS_OF), "Customers")
    creditors = crud.creditors_report(db, as_of=AS_OF)
    assert row_for(debtors, c) is None
    assert all(row_for(s, c) is None for s in creditors)


def test_overpaid_customer_is_only_a_creditor(db, make_customer):
    c = make_customer("Emeka", (days_before(40), 300, 0), (days_before(20), 0, 450))
    assert row_for(section(crud.debtors_report(db, as_of=AS_OF), "Customers"), c) is None

    creditors = crud.creditors_report(db, as_of=AS_OF, ageing=True)
    assert row_for(section(creditors, "Suppliers"), c) is None
    row = row_for(section(creditors, "Customers in credit (overpaid)"), c)
    # Reported as a positive amount we owe, aged by the overpayment's date.
    assert row["balance"] == D("150")
    assert row["ageing"] == [D("150"), D("0"), D("0"), D("0")]


def test_supplier_we_owe_is_a_creditor(db, make_supplier):
    # Supplier side is inverted: dr = goods received (we owe more).
    s = make_supplier("Fola Farms", (days_before(70), 1000, 0), (days_before(10), 0, 400))
    row = row_for(section(crud.creditors_report(db, as_of=AS_OF, ageing=True), "Suppliers"), s)
    assert row["balance"] == D("600")
    assert row["ageing"] == [D("0"), D("0"), D("600"), D("0")]


# --- "As of" date ------------------------------------------------------------


def test_as_of_excludes_later_transactions(db, make_customer):
    c = make_customer(
        "Gbenga",
        (days_before(70), 100, 0),
        (AS_OF + datetime.timedelta(days=1), 900, 0),  # later invoice
        (AS_OF + datetime.timedelta(days=2), 0, 100),  # later payment
    )
    row = row_for(section(crud.debtors_report(db, as_of=AS_OF, ageing=True), "Customers"), c)
    assert row["balance"] == D("100")
    assert row["total_dr"] == D("100") and row["total_cr"] == D("0")
    assert row["last_activity"] == days_before(70)
    # Aged by the 70-day-old invoice, not the newer one after the date.
    assert row["ageing"] == [D("0"), D("0"), D("100"), D("0")]

    # A customer whose only activity is after the date isn't listed at all.
    late = make_customer("Halima", (AS_OF + datetime.timedelta(days=3), 50, 0))
    assert row_for(section(crud.debtors_report(db, as_of=AS_OF), "Customers"), late) is None


def test_age_is_measured_from_as_of_not_today(db, make_customer):
    # 40 days before the as-of date -- years old relative to today, but it
    # must land in 31-60 for a report run as of AS_OF.
    c = make_customer("Ifeoma", (days_before(40), 100, 0))
    row = row_for(section(crud.debtors_report(db, as_of=AS_OF, ageing=True), "Customers"), c)
    assert row["ageing"] == [D("0"), D("100"), D("0"), D("0")]


@pytest.mark.parametrize(
    "days, bucket",
    [(0, 0), (30, 0), (31, 1), (60, 1), (61, 2), (90, 2), (91, 3), (400, 3)],
)
def test_bucket_boundaries(db, make_customer, days, bucket):
    assert crud._age_bucket(days) == bucket
    c = make_customer(f"Edge {days}", (days_before(days), 100, 0))
    row = row_for(section(crud.debtors_report(db, as_of=AS_OF, ageing=True), "Customers"), c)
    expected = [D("0")] * 4
    expected[bucket] = D("100")
    assert row["ageing"] == expected


# --- Invariants, checked against an independent oracle on random ledgers -----


def oracle_ageing(entries, side, reference):
    """Forward replay of the ledger: each charge on `side` opens an item,
    and every settlement (now or carried over from an earlier overpayment)
    clears the oldest open items first. Deliberately a different procedure
    from crud._age_balances, which walks back from the newest entries."""
    open_items = deque()
    credit = D("0")
    for date_posted, dr, cr in sorted(entries, key=lambda e: e[0]):
        charge, settle = (dr, cr) if side == "dr" else (cr, dr)
        if charge:
            open_items.append([date_posted, charge])
        credit += settle
        while credit and open_items:
            take = min(credit, open_items[0][1])
            open_items[0][1] -= take
            credit -= take
            if not open_items[0][1]:
                open_items.popleft()
    buckets = [D("0")] * 4
    for date_posted, amount in open_items:
        days = (reference - date_posted).days
        buckets[0 if days <= 30 else 1 if days <= 60 else 2 if days <= 90 else 3] += amount
    return buckets


def random_ledger(rng, n_parties):
    ledgers = []
    for i in range(n_parties):
        entries = []
        for _ in range(rng.randint(1, 12)):
            amount = D(rng.randint(1, 500_000)) / 100
            dr, cr = (amount, D("0")) if rng.random() < 0.55 else (D("0"), amount)
            entries.append((days_before(rng.randint(0, 200)), dr, cr))
        ledgers.append((f"Party {i:02d}", entries))
    return ledgers


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_ageing_matches_oracle_and_sums_to_balance(db, make_customer, make_supplier, seed):
    rng = random.Random(seed)
    customers = [(make_customer(n, *e), e) for n, e in random_ledger(rng, 40)]
    suppliers = [(make_supplier(n, *e), e) for n, e in random_ledger(rng, 25)]

    debtors = crud.debtors_report(db, as_of=AS_OF, ageing=True)
    creditors = crud.creditors_report(db, as_of=AS_OF, ageing=True)
    checks = [
        (section(debtors, "Customers"), customers, "dr"),
        (section(creditors, "Customers in credit (overpaid)"), customers, "cr"),
        (section(creditors, "Suppliers"), suppliers, "dr"),
    ]
    for sec, parties, side in checks:
        expected_ids = set()
        for party, entries in parties:
            dr = sum(e[1] for e in entries)
            cr = sum(e[2] for e in entries)
            outstanding = dr - cr if side == "dr" else cr - dr
            row = row_for(sec, party)
            if outstanding <= 0:
                assert row is None, f"{party.name} listed with no {side} balance"
                continue
            expected_ids.add(party.id)
            assert row["balance"] == outstanding
            assert sum(row["ageing"]) == row["balance"]
            assert row["ageing"] == oracle_ageing(entries, side, AS_OF), party.name
        assert {r["id"] for r in sec["rows"]} == expected_ids
        # Section totals: each bucket column adds up, and the columns add up
        # to the section total.
        for i in range(4):
            assert sec["ageing_totals"][i] == sum(r["ageing"][i] for r in sec["rows"])
        assert sum(sec["ageing_totals"]) == sec["total"] == sum(r["balance"] for r in sec["rows"])


@pytest.mark.xfail(
    strict=True,
    reason="Known gap: a negative amount (e.g. a reversal) lets the balance "
    "exceed that side's positive entries, and the fallback silently ages the "
    "excess as 'Over 90 days'. The legacy CSVs contain no negative amounts, "
    "but the transaction schemas don't forbid them.",
)
def test_negative_amount_is_not_misfiled_as_over_90_days(db, make_customer):
    # A recent 100 invoice and a -20 credit (a reversed payment): 120 owed,
    # all of it recent -- nothing should be over 90 days old.
    c = make_customer("Jide", (days_before(5), 100, 0), (days_before(3), 0, -20))
    row = row_for(section(crud.debtors_report(db, as_of=AS_OF, ageing=True), "Customers"), c)
    assert row["balance"] == D("120")
    assert row["ageing"][3] == D("0")


# --- Sorting ------------------------------------------------------------------


def test_sorting(db, make_customer):
    make_customer("banda", (days_before(1), 10, 0))
    make_customer("Achebe", (days_before(1), 30, 0))
    make_customer("Chukwu", (days_before(1), 20, 0))

    by_name = section(crud.debtors_report(db, as_of=AS_OF), "Customers")["rows"]
    assert [r["name"] for r in by_name] == ["Achebe", "banda", "Chukwu"]  # case-insensitive

    by_balance = section(crud.debtors_report(db, as_of=AS_OF, sort="balance"), "Customers")["rows"]
    assert [r["name"] for r in by_balance] == ["Achebe", "Chukwu", "banda"]
