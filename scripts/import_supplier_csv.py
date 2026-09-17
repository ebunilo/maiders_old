"""
Import the legacy "Supplier Ledger" CSV export into the PostgreSQL ledger
database. This ingests supplier data into its own tables (suppliers /
supplier_transactions) and never touches the customers / transactions
tables, so it's safe to run independently of the customer import.

Usage:
    python -m scripts.import_supplier_csv "Supplier Ledger_114158.csv"

Cleaning rules applied:
- SupplierName is the only identity column in the source file (there's no
  separate supplier code, unlike CustomerID/CustomerName for customers), so
  a short code is generated from it, e.g. "YongXing Steel Company Ltd" ->
  "YONGXINGSTE". Suppliers are matched/deduped by exact (case-insensitive)
  name.
- Placeholder values used by the source system (" ========== ") are
  converted to NULL for PaymentMode / AccountUsed / ItemName / Measures /
  Quantity / FormID.
- UnitCost and VehicleNo also use a literal "0" as their "no value" sentinel
  (verified against the source data: every row where UnitCost or VehicleNo
  is "0" has no ItemName either), so "0" is treated as NULL for those two
  columns specifically -- everywhere else (AmountDr/AmountCr/RefNo) a literal
  0 is a real value and is kept as-is.
- AmountDr / AmountCr are parsed as decimals. AmountDr is goods/value
  received from the supplier (increases what we owe them); AmountCr is a
  payment we made to them (decreases what we owe) -- the inverse of the
  customer ledger, where AmountDr increases what the customer owes us.
- DatePosted ("YYYY-MM-DD 00:00:00") is parsed to a plain date.
- Rows are inserted in bulk per supplier for speed; existing data for a
  supplier is left untouched unless --reset is passed (which truncates only
  the supplier_transactions and suppliers tables first).
"""

import argparse
import csv
import sys
from decimal import Decimal, InvalidOperation

from sqlalchemy import text

from app.database import Base, SessionLocal, engine
from app.models import Supplier, SupplierTransaction

PLACEHOLDER = "=========="


def clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value or PLACEHOLDER in value.replace(" ", ""):
        return None
    return value


def clean_or_zero_null(value: str | None) -> str | None:
    """Like clean(), but also treats a literal "0" as NULL -- used for
    UnitCost/VehicleNo, where this source system uses "0" as its no-value
    sentinel instead of the usual placeholder string."""
    value = clean(value)
    if value == "0":
        return None
    return value


def parse_decimal(value: str | None) -> Decimal:
    value = (value or "0").strip()
    try:
        return Decimal(value) if value else Decimal("0")
    except InvalidOperation:
        return Decimal("0")


def parse_optional_decimal(value: str | None) -> Decimal | None:
    value = clean_or_zero_null(value)
    if value is None:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def parse_date(value: str):
    value = value.strip().split(" ")[0]
    year, month, day = value.split("-")
    import datetime

    return datetime.date(int(year), int(month), int(day))


def generate_supplier_code(existing_codes: set[str], name: str) -> str:
    base = "".join(ch for ch in name.upper() if ch.isalnum()) or "SUPP"
    base = base[:12]
    code = base
    suffix = 1
    while code in existing_codes:
        suffix += 1
        code = f"{base}{suffix}"
    existing_codes.add(code)
    return code


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path")
    parser.add_argument("--reset", action="store_true", help="Truncate existing supplier data first")
    parser.add_argument("--batch-size", type=int, default=1000)
    args = parser.parse_args()

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    if args.reset:
        print("Resetting existing supplier tables...")
        db.execute(text("TRUNCATE TABLE supplier_transactions RESTART IDENTITY CASCADE"))
        db.execute(text("TRUNCATE TABLE suppliers RESTART IDENTITY CASCADE"))
        db.commit()

    supplier_cache: dict[str, Supplier] = {
        s.name.strip().lower(): s for s in db.query(Supplier).all()
    }
    existing_codes = {s.code for s in supplier_cache.values()}

    inserted = 0
    skipped = 0
    batch: list[SupplierTransaction] = []

    with open(args.csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = (row.get("SupplierName") or "").strip()
            if not name:
                skipped += 1
                continue

            supplier = supplier_cache.get(name.lower())
            if supplier is None:
                supplier = Supplier(code=generate_supplier_code(existing_codes, name), name=name)
                db.add(supplier)
                db.flush()  # assign an id without committing
                supplier_cache[name.lower()] = supplier

            try:
                date_posted = parse_date(row["DatePosted"])
            except (ValueError, IndexError):
                skipped += 1
                continue

            txn = SupplierTransaction(
                supplier_id=supplier.id,
                ref_no=clean(row.get("RefNo")),
                date_posted=date_posted,
                details=clean(row.get("Details")),
                amount_dr=parse_decimal(row.get("AmountDr")),
                amount_cr=parse_decimal(row.get("AmountCr")),
                payment_mode=clean(row.get("PaymentMode")),
                account_used=clean(row.get("AccountUsed")),
                trans_no=clean(row.get("TransNo")),
                form_id=clean(row.get("FormID")),
                item_name=clean(row.get("ItemName")),
                measures=clean(row.get("Measures")),
                quantity=parse_optional_decimal(row.get("Quantity")),
                unit_cost=parse_optional_decimal(row.get("UnitCost")),
                vehicle_no=clean_or_zero_null(row.get("VehicleNo")),
            )
            batch.append(txn)
            inserted += 1

            if len(batch) >= args.batch_size:
                db.add_all(batch)
                db.commit()
                batch = []
                print(f"  ...{inserted} rows imported", file=sys.stderr)

    if batch:
        db.add_all(batch)
        db.commit()

    print(f"Done. Imported {inserted} supplier transactions, skipped {skipped}, "
          f"{len(supplier_cache)} suppliers total.")
    db.close()


if __name__ == "__main__":
    main()
