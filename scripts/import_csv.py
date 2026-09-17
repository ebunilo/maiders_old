"""
Import the legacy "Customer Transactions" CSV export into the PostgreSQL
ledger database.

Usage:
    python -m scripts.import_csv "Customer Transactions_114458.csv"

Cleaning rules applied:
- CustomerID is the natural customer key (it's a short code, even though it
  looks like a name); CustomerName is kept as the display name. Both are
  whitespace-trimmed.
- Placeholder values used by the source system (" ========== ") are
  converted to NULL for InvoiceNo / PaymentMode / BankName / TransNo.
- AmountDr / AmountCr are parsed as decimals.
- DatePosted ("YYYY-MM-DD 00:00:00") is parsed to a plain date.
- Rows are inserted in bulk per customer for speed; existing data for a
  customer is left untouched unless --reset is passed (which truncates the
  transactions and customers tables first).
"""

import argparse
import csv
import sys
from decimal import Decimal, InvalidOperation

from sqlalchemy import text

from app.database import Base, SessionLocal, engine
from app.models import Customer, Transaction

PLACEHOLDER = "=========="


def clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value or PLACEHOLDER in value.replace(" ", ""):
        return None
    return value


def parse_decimal(value: str) -> Decimal:
    value = (value or "0").strip()
    try:
        return Decimal(value) if value else Decimal("0")
    except InvalidOperation:
        return Decimal("0")


def parse_date(value: str):
    value = value.strip().split(" ")[0]
    year, month, day = value.split("-")
    import datetime

    return datetime.date(int(year), int(month), int(day))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path")
    parser.add_argument("--reset", action="store_true", help="Truncate existing data first")
    parser.add_argument("--batch-size", type=int, default=1000)
    args = parser.parse_args()

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    if args.reset:
        print("Resetting existing tables...")
        db.execute(text("TRUNCATE TABLE transactions RESTART IDENTITY CASCADE"))
        db.execute(text("TRUNCATE TABLE customers RESTART IDENTITY CASCADE"))
        db.commit()

    customer_cache: dict[str, Customer] = {
        c.code: c for c in db.query(Customer).all()
    }

    inserted = 0
    skipped = 0
    batch: list[Transaction] = []

    with open(args.csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = row["CustomerID"].strip()
            name = (row["CustomerName"] or code).strip()
            if not code:
                skipped += 1
                continue

            customer = customer_cache.get(code)
            if customer is None:
                customer = Customer(code=code, name=name)
                db.add(customer)
                db.flush()  # assign an id without committing
                customer_cache[code] = customer

            try:
                date_posted = parse_date(row["DatePosted"])
            except (ValueError, IndexError):
                skipped += 1
                continue

            txn = Transaction(
                customer_id=customer.id,
                ref_no=clean(row.get("RefNo")),
                date_posted=date_posted,
                details=clean(row.get("Details")),
                amount_dr=parse_decimal(row.get("AmountDr")),
                amount_cr=parse_decimal(row.get("AmountCr")),
                invoice_no=clean(row.get("InvoiceNo")),
                payment_mode=clean(row.get("PaymentMode")),
                bank_name=clean(row.get("BankName")),
                trans_no=clean(row.get("TransNo")),
                form_id=clean(row.get("FormID")),
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

    print(f"Done. Imported {inserted} transactions, skipped {skipped}, "
          f"{len(customer_cache)} customers total.")
    db.close()


if __name__ == "__main__":
    main()
