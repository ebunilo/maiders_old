import datetime
import difflib
from decimal import Decimal

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from app import models, schemas

PAGE_SIZE_DEFAULT = 25


def get_customer(db: Session, customer_id: int) -> models.Customer | None:
    return db.get(models.Customer, customer_id)


def get_customer_by_code(db: Session, code: str) -> models.Customer | None:
    return db.scalar(select(models.Customer).where(models.Customer.code == code))


def create_customer(db: Session, data: schemas.CustomerCreate) -> models.Customer:
    customer = models.Customer(code=data.code.strip(), name=data.name.strip())
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


def find_customers_by_name(db: Session, query: str, limit: int = 8) -> list[models.Customer]:
    like = f"%{query.strip()}%"
    stmt = (
        select(models.Customer)
        .where(models.Customer.name.ilike(like))
        .order_by(models.Customer.name)
        .limit(limit)
    )
    return list(db.scalars(stmt).all())


def _normalized_name(column):
    """SQL expression for a name with case and runs of whitespace ignored,
    so "ND  BEST" and "Nd Best" compare equal."""
    return func.lower(func.regexp_replace(func.trim(column), r"\s+", " ", "g"))


def _find_by_same_name(db: Session, party_model, name: str, exclude_id: int | None = None):
    """An existing customer/supplier whose name matches `name` ignoring
    case and spacing -- used to stop the same party being created twice."""
    normalized = " ".join(name.split()).lower()
    stmt = select(party_model).where(_normalized_name(party_model.name) == normalized)
    if exclude_id:
        stmt = stmt.where(party_model.id != exclude_id)
    return db.scalar(stmt.order_by(party_model.id).limit(1))


def _find_similar(db: Session, party_model, party, limit: int = 5) -> list:
    """Other customers/suppliers whose names closely resemble `party`'s
    (same name with different case/spacing, or a likely typo) -- the
    probable originals when `party` is a duplicate."""
    others = db.scalars(select(party_model).where(party_model.id != party.id)).all()
    by_name: dict[str, list] = {}
    for other in others:
        by_name.setdefault(" ".join(other.name.split()).lower(), []).append(other)
    target = " ".join(party.name.split()).lower()
    matches = difflib.get_close_matches(target, list(by_name), n=limit, cutoff=0.8)
    return [p for name in matches for p in by_name[name]][:limit]


def _delete_party(db: Session, party_model, txn_model, fk_column, party, move_to=None) -> int:
    """Delete a customer/supplier. If they have transactions, `move_to` is
    required and every transaction is reassigned to that party first
    (merging a duplicate into the original), so no ledger entries are lost.
    Returns the number of transactions moved."""
    label = party_model.__name__.lower()  # "customer" / "supplier"
    count = db.scalar(select(func.count(txn_model.id)).where(fk_column == party.id))
    if count and move_to is None:
        raise ValueError(f"This {label} has transactions; choose a {label} to move them to")
    if move_to is not None and move_to.id == party.id:
        raise ValueError(f"Cannot move transactions to the {label} being deleted")

    moved = 0
    if count:
        moved = db.execute(
            update(txn_model).where(fk_column == party.id).values({fk_column.key: move_to.id})
        ).rowcount
    # Core delete rather than db.delete(party): the ORM relationship
    # cascades deletes to any transactions it has loaded, which could be
    # stale after the bulk update above.
    db.execute(delete(party_model).where(party_model.id == party.id))
    db.commit()
    return moved


def find_customer_by_same_name(
    db: Session, name: str, exclude_id: int | None = None
) -> models.Customer | None:
    return _find_by_same_name(db, models.Customer, name, exclude_id)


def find_similar_customers(db: Session, customer: models.Customer) -> list[models.Customer]:
    return _find_similar(db, models.Customer, customer)


def resolve_transaction_customer(db: Session, data: schemas.TransactionCreate) -> models.Customer:
    """Find the EXISTING customer a new transaction belongs to, by id, code,
    or exact (case-insensitive) name. Never creates one: new customers are
    only added deliberately via the New Customer form, so a mistyped name
    can't silently spawn a duplicate customer."""
    if data.customer_id:
        customer = get_customer(db, data.customer_id)
        if customer:
            return customer

    if data.customer_code:
        customer = get_customer_by_code(db, data.customer_code.strip())
        if customer:
            return customer

    name = (data.customer_name or "").strip()
    if name:
        customer = db.scalar(
            select(models.Customer).where(func.lower(models.Customer.name) == name.lower())
        )
        if customer:
            return customer

    raise ValueError("Customer not found -- create it with New Customer first")


def delete_customer(
    db: Session, customer: models.Customer, move_to: models.Customer | None = None
) -> int:
    return _delete_party(
        db, models.Customer, models.Transaction, models.Transaction.customer_id, customer, move_to
    )


def list_customers(
    db: Session, search: str | None = None, page: int = 1, page_size: int = PAGE_SIZE_DEFAULT
):
    balance_expr = func.coalesce(func.sum(models.Transaction.amount_dr), 0) - func.coalesce(
        func.sum(models.Transaction.amount_cr), 0
    )

    stmt = (
        select(
            models.Customer,
            func.coalesce(func.sum(models.Transaction.amount_dr), 0).label("total_dr"),
            func.coalesce(func.sum(models.Transaction.amount_cr), 0).label("total_cr"),
            balance_expr.label("balance"),
            func.count(models.Transaction.id).label("transaction_count"),
        )
        .outerjoin(models.Transaction, models.Transaction.customer_id == models.Customer.id)
        .group_by(models.Customer.id)
        .order_by(models.Customer.name)
    )

    if search:
        like = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(models.Customer.name.ilike(like), models.Customer.code.ilike(like))
        )

    total = db.scalar(select(func.count()).select_from(stmt.subquery()))

    rows = db.execute(stmt.offset((page - 1) * page_size).limit(page_size)).all()
    results = [
        schemas.CustomerWithBalance(
            id=c.id,
            code=c.code,
            name=c.name,
            total_dr=total_dr,
            total_cr=total_cr,
            balance=balance,
            transaction_count=transaction_count,
        )
        for c, total_dr, total_cr, balance, transaction_count in rows
    ]
    return results, total


def get_customer_balance(db: Session, customer_id: int) -> dict:
    row = db.execute(
        select(
            func.coalesce(func.sum(models.Transaction.amount_dr), 0),
            func.coalesce(func.sum(models.Transaction.amount_cr), 0),
            func.count(models.Transaction.id),
        ).where(models.Transaction.customer_id == customer_id)
    ).one()
    total_dr, total_cr, count = row
    return {
        "total_dr": total_dr,
        "total_cr": total_cr,
        "balance": total_dr - total_cr,
        "transaction_count": count,
    }


def get_full_customer_statement(
    db: Session,
    customer_id: int,
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
    form_id: str | None = None,
) -> list[tuple[models.Transaction, Decimal]]:
    """All matching transactions for a customer, in chronological order,
    paired with the running balance after each one."""
    stmt = select(models.Transaction).where(models.Transaction.customer_id == customer_id)
    if date_from:
        stmt = stmt.where(models.Transaction.date_posted >= date_from)
    if date_to:
        stmt = stmt.where(models.Transaction.date_posted <= date_to)
    if form_id:
        stmt = stmt.where(models.Transaction.form_id == form_id)

    ordered = stmt.order_by(models.Transaction.date_posted, models.Transaction.id)
    all_rows = db.scalars(ordered).all()

    running = Decimal("0")
    with_balance = []
    for t in all_rows:
        running += (t.amount_dr or 0) - (t.amount_cr or 0)
        with_balance.append((t, running))
    return with_balance


def list_transactions_for_customer(
    db: Session,
    customer_id: int,
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
    form_id: str | None = None,
    page: int = 1,
    page_size: int = PAGE_SIZE_DEFAULT,
):
    with_balance = get_full_customer_statement(
        db, customer_id, date_from=date_from, date_to=date_to, form_id=form_id
    )
    total = len(with_balance)
    start = (page - 1) * page_size
    end = start + page_size
    page_rows = with_balance[start:end]
    return page_rows, total


def list_transactions(
    db: Session,
    search: str | None = None,
    customer_id: int | None = None,
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
    form_id: str | None = None,
    page: int = 1,
    page_size: int = PAGE_SIZE_DEFAULT,
):
    stmt = select(models.Transaction, models.Customer).join(
        models.Customer, models.Transaction.customer_id == models.Customer.id
    )
    if customer_id:
        stmt = stmt.where(models.Transaction.customer_id == customer_id)
    if date_from:
        stmt = stmt.where(models.Transaction.date_posted >= date_from)
    if date_to:
        stmt = stmt.where(models.Transaction.date_posted <= date_to)
    if form_id:
        stmt = stmt.where(models.Transaction.form_id == form_id)
    if search:
        like = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                models.Transaction.details.ilike(like),
                models.Transaction.ref_no.ilike(like),
                models.Transaction.invoice_no.ilike(like),
                models.Customer.name.ilike(like),
                models.Customer.code.ilike(like),
            )
        )

    total = db.scalar(select(func.count()).select_from(stmt.subquery()))

    stmt = stmt.order_by(
        models.Transaction.date_posted.desc(), models.Transaction.id.desc()
    )
    rows = db.execute(stmt.offset((page - 1) * page_size).limit(page_size)).all()
    return rows, total


def create_transaction(db: Session, data: schemas.TransactionCreate) -> models.Transaction:
    customer = resolve_transaction_customer(db, data)
    txn = models.Transaction(
        customer_id=customer.id,
        ref_no=data.ref_no,
        date_posted=data.date_posted,
        details=data.details,
        amount_dr=data.amount_dr,
        amount_cr=data.amount_cr,
        invoice_no=data.invoice_no,
        payment_mode=data.payment_mode,
        bank_name=data.bank_name,
        trans_no=data.trans_no,
        form_id=data.form_id,
    )
    db.add(txn)
    db.flush()
    if not txn.trans_no:
        txn.trans_no = f"TXN{txn.id:06d}"
    db.commit()
    db.refresh(txn)
    return txn


def get_transaction(db: Session, transaction_id: int) -> models.Transaction | None:
    return db.get(models.Transaction, transaction_id)


def update_transaction(
    db: Session, txn: models.Transaction, data: schemas.TransactionUpdate
) -> models.Transaction:
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(txn, field, value)
    db.commit()
    db.refresh(txn)
    return txn


def delete_transaction(db: Session, txn: models.Transaction) -> None:
    db.delete(txn)
    db.commit()


def get_dashboard_summary(db: Session) -> schemas.DashboardSummary:
    total_customers = db.scalar(select(func.count()).select_from(models.Customer))
    row = db.execute(
        select(
            func.count(models.Transaction.id),
            func.coalesce(func.sum(models.Transaction.amount_dr), 0),
            func.coalesce(func.sum(models.Transaction.amount_cr), 0),
        )
    ).one()
    total_transactions, total_dr, total_cr = row
    return schemas.DashboardSummary(
        total_customers=total_customers or 0,
        total_transactions=total_transactions or 0,
        total_dr=total_dr,
        total_cr=total_cr,
        net_outstanding=total_dr - total_cr,
    )


def get_top_debtors(db: Session, limit: int = 10) -> list[schemas.TopCustomer]:
    balance_expr = func.coalesce(func.sum(models.Transaction.amount_dr), 0) - func.coalesce(
        func.sum(models.Transaction.amount_cr), 0
    )
    stmt = (
        select(models.Customer.code, models.Customer.name, balance_expr.label("balance"))
        .join(models.Transaction, models.Transaction.customer_id == models.Customer.id)
        .group_by(models.Customer.id)
        .order_by(balance_expr.desc())
        .limit(limit)
    )
    rows = db.execute(stmt).all()
    return [schemas.TopCustomer(code=c, name=n, balance=b) for c, n, b in rows]


def get_monthly_totals(db: Session, months: int = 12):
    stmt = (
        select(
            func.date_trunc("month", models.Transaction.date_posted).label("month"),
            func.coalesce(func.sum(models.Transaction.amount_dr), 0).label("total_dr"),
            func.coalesce(func.sum(models.Transaction.amount_cr), 0).label("total_cr"),
        )
        .group_by("month")
        .order_by("month")
    )
    rows = db.execute(stmt).all()
    return rows[-months:] if months else rows


def list_form_ids(db: Session) -> list[str]:
    rows = db.scalars(
        select(models.Transaction.form_id).distinct().order_by(models.Transaction.form_id)
    ).all()
    return [r for r in rows if r]


# --- Suppliers -------------------------------------------------------------
# Mirrors the customer functions above. The key difference is the meaning of
# amount_dr/amount_cr: for a supplier, amount_dr is goods/value received
# (increases what we owe them) and amount_cr is a payment we made (decreases
# what we owe) -- so a positive balance means WE owe THEM, the opposite of a
# customer's balance.


def get_supplier(db: Session, supplier_id: int) -> models.Supplier | None:
    return db.get(models.Supplier, supplier_id)


def get_supplier_by_code(db: Session, code: str) -> models.Supplier | None:
    return db.scalar(select(models.Supplier).where(models.Supplier.code == code))


def create_supplier(db: Session, data: schemas.SupplierCreate) -> models.Supplier:
    supplier = models.Supplier(code=data.code.strip(), name=data.name.strip())
    db.add(supplier)
    db.commit()
    db.refresh(supplier)
    return supplier


def find_suppliers_by_name(db: Session, query: str, limit: int = 8) -> list[models.Supplier]:
    like = f"%{query.strip()}%"
    stmt = (
        select(models.Supplier)
        .where(models.Supplier.name.ilike(like))
        .order_by(models.Supplier.name)
        .limit(limit)
    )
    return list(db.scalars(stmt).all())


def generate_supplier_code(db: Session, name: str) -> str:
    """Derive a short, unique supplier code from a name (the legacy data has
    no separate supplier code), e.g. for the New Supplier form."""
    base = "".join(ch for ch in name.upper() if ch.isalnum()) or "SUPP"
    base = base[:12]
    code = base
    suffix = 1
    while db.scalar(select(models.Supplier.id).where(models.Supplier.code == code)):
        suffix += 1
        code = f"{base}{suffix}"
    return code


def find_supplier_by_same_name(
    db: Session, name: str, exclude_id: int | None = None
) -> models.Supplier | None:
    return _find_by_same_name(db, models.Supplier, name, exclude_id)


def find_similar_suppliers(db: Session, supplier: models.Supplier) -> list[models.Supplier]:
    return _find_similar(db, models.Supplier, supplier)


def resolve_supplier_transaction_supplier(
    db: Session, data: schemas.SupplierTransactionCreate
) -> models.Supplier:
    """Find the EXISTING supplier a new transaction belongs to, by id, code,
    or exact (case-insensitive) name. Never creates one -- see
    resolve_transaction_customer."""
    if data.supplier_id:
        supplier = get_supplier(db, data.supplier_id)
        if supplier:
            return supplier

    if data.supplier_code:
        supplier = get_supplier_by_code(db, data.supplier_code.strip())
        if supplier:
            return supplier

    name = (data.supplier_name or "").strip()
    if name:
        supplier = db.scalar(
            select(models.Supplier).where(func.lower(models.Supplier.name) == name.lower())
        )
        if supplier:
            return supplier

    raise ValueError("Supplier not found -- create it with New Supplier first")


def delete_supplier(
    db: Session, supplier: models.Supplier, move_to: models.Supplier | None = None
) -> int:
    return _delete_party(
        db,
        models.Supplier,
        models.SupplierTransaction,
        models.SupplierTransaction.supplier_id,
        supplier,
        move_to,
    )


def list_suppliers(
    db: Session, search: str | None = None, page: int = 1, page_size: int = PAGE_SIZE_DEFAULT
):
    balance_expr = func.coalesce(
        func.sum(models.SupplierTransaction.amount_dr), 0
    ) - func.coalesce(func.sum(models.SupplierTransaction.amount_cr), 0)

    stmt = (
        select(
            models.Supplier,
            func.coalesce(func.sum(models.SupplierTransaction.amount_dr), 0).label("total_dr"),
            func.coalesce(func.sum(models.SupplierTransaction.amount_cr), 0).label("total_cr"),
            balance_expr.label("balance"),
            func.count(models.SupplierTransaction.id).label("transaction_count"),
        )
        .outerjoin(
            models.SupplierTransaction,
            models.SupplierTransaction.supplier_id == models.Supplier.id,
        )
        .group_by(models.Supplier.id)
        .order_by(models.Supplier.name)
    )

    if search:
        like = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(models.Supplier.name.ilike(like), models.Supplier.code.ilike(like))
        )

    total = db.scalar(select(func.count()).select_from(stmt.subquery()))

    rows = db.execute(stmt.offset((page - 1) * page_size).limit(page_size)).all()
    results = [
        schemas.SupplierWithBalance(
            id=s.id,
            code=s.code,
            name=s.name,
            total_dr=total_dr,
            total_cr=total_cr,
            balance=balance,
            transaction_count=transaction_count,
        )
        for s, total_dr, total_cr, balance, transaction_count in rows
    ]
    return results, total


def get_supplier_balance(db: Session, supplier_id: int) -> dict:
    row = db.execute(
        select(
            func.coalesce(func.sum(models.SupplierTransaction.amount_dr), 0),
            func.coalesce(func.sum(models.SupplierTransaction.amount_cr), 0),
            func.count(models.SupplierTransaction.id),
        ).where(models.SupplierTransaction.supplier_id == supplier_id)
    ).one()
    total_dr, total_cr, count = row
    return {
        "total_dr": total_dr,
        "total_cr": total_cr,
        "balance": total_dr - total_cr,
        "transaction_count": count,
    }


def get_full_supplier_statement(
    db: Session,
    supplier_id: int,
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
    form_id: str | None = None,
) -> list[tuple[models.SupplierTransaction, Decimal]]:
    stmt = select(models.SupplierTransaction).where(
        models.SupplierTransaction.supplier_id == supplier_id
    )
    if date_from:
        stmt = stmt.where(models.SupplierTransaction.date_posted >= date_from)
    if date_to:
        stmt = stmt.where(models.SupplierTransaction.date_posted <= date_to)
    if form_id:
        stmt = stmt.where(models.SupplierTransaction.form_id == form_id)

    ordered = stmt.order_by(
        models.SupplierTransaction.date_posted, models.SupplierTransaction.id
    )
    all_rows = db.scalars(ordered).all()

    running = Decimal("0")
    with_balance = []
    for t in all_rows:
        running += (t.amount_dr or 0) - (t.amount_cr or 0)
        with_balance.append((t, running))
    return with_balance


def list_supplier_transactions_for_supplier(
    db: Session,
    supplier_id: int,
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
    form_id: str | None = None,
    page: int = 1,
    page_size: int = PAGE_SIZE_DEFAULT,
):
    with_balance = get_full_supplier_statement(
        db, supplier_id, date_from=date_from, date_to=date_to, form_id=form_id
    )
    total = len(with_balance)
    start = (page - 1) * page_size
    end = start + page_size
    page_rows = with_balance[start:end]
    return page_rows, total


def list_supplier_transactions(
    db: Session,
    search: str | None = None,
    supplier_id: int | None = None,
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
    form_id: str | None = None,
    page: int = 1,
    page_size: int = PAGE_SIZE_DEFAULT,
):
    stmt = select(models.SupplierTransaction, models.Supplier).join(
        models.Supplier, models.SupplierTransaction.supplier_id == models.Supplier.id
    )
    if supplier_id:
        stmt = stmt.where(models.SupplierTransaction.supplier_id == supplier_id)
    if date_from:
        stmt = stmt.where(models.SupplierTransaction.date_posted >= date_from)
    if date_to:
        stmt = stmt.where(models.SupplierTransaction.date_posted <= date_to)
    if form_id:
        stmt = stmt.where(models.SupplierTransaction.form_id == form_id)
    if search:
        like = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                models.SupplierTransaction.details.ilike(like),
                models.SupplierTransaction.ref_no.ilike(like),
                models.SupplierTransaction.item_name.ilike(like),
                models.Supplier.name.ilike(like),
                models.Supplier.code.ilike(like),
            )
        )

    total = db.scalar(select(func.count()).select_from(stmt.subquery()))

    stmt = stmt.order_by(
        models.SupplierTransaction.date_posted.desc(), models.SupplierTransaction.id.desc()
    )
    rows = db.execute(stmt.offset((page - 1) * page_size).limit(page_size)).all()
    return rows, total


def create_supplier_transaction(
    db: Session, data: schemas.SupplierTransactionCreate
) -> models.SupplierTransaction:
    supplier = resolve_supplier_transaction_supplier(db, data)
    txn = models.SupplierTransaction(
        supplier_id=supplier.id,
        ref_no=data.ref_no,
        date_posted=data.date_posted,
        details=data.details,
        amount_dr=data.amount_dr,
        amount_cr=data.amount_cr,
        payment_mode=data.payment_mode,
        account_used=data.account_used,
        trans_no=data.trans_no,
        form_id=data.form_id,
        item_name=data.item_name,
        measures=data.measures,
        quantity=data.quantity,
        unit_cost=data.unit_cost,
        vehicle_no=data.vehicle_no,
    )
    db.add(txn)
    db.flush()
    if not txn.trans_no:
        txn.trans_no = f"STX{txn.id:06d}"
    db.commit()
    db.refresh(txn)
    return txn


def get_supplier_transaction(db: Session, transaction_id: int) -> models.SupplierTransaction | None:
    return db.get(models.SupplierTransaction, transaction_id)


def delete_supplier_transaction(db: Session, txn: models.SupplierTransaction) -> None:
    db.delete(txn)
    db.commit()


def get_supplier_dashboard_summary(db: Session) -> schemas.SupplierDashboardSummary:
    total_suppliers = db.scalar(select(func.count()).select_from(models.Supplier))
    row = db.execute(
        select(
            func.count(models.SupplierTransaction.id),
            func.coalesce(func.sum(models.SupplierTransaction.amount_dr), 0),
            func.coalesce(func.sum(models.SupplierTransaction.amount_cr), 0),
        )
    ).one()
    total_transactions, total_dr, total_cr = row
    return schemas.SupplierDashboardSummary(
        total_suppliers=total_suppliers or 0,
        total_transactions=total_transactions or 0,
        total_dr=total_dr,
        total_cr=total_cr,
        net_payable=total_dr - total_cr,
    )


def get_top_creditors(db: Session, limit: int = 10) -> list[schemas.TopSupplier]:
    """Suppliers we currently owe the most money to."""
    balance_expr = func.coalesce(
        func.sum(models.SupplierTransaction.amount_dr), 0
    ) - func.coalesce(func.sum(models.SupplierTransaction.amount_cr), 0)
    stmt = (
        select(models.Supplier.code, models.Supplier.name, balance_expr.label("balance"))
        .join(models.SupplierTransaction, models.SupplierTransaction.supplier_id == models.Supplier.id)
        .group_by(models.Supplier.id)
        .order_by(balance_expr.desc())
        .limit(limit)
    )
    rows = db.execute(stmt).all()
    return [schemas.TopSupplier(code=c, name=n, balance=b) for c, n, b in rows]


def get_supplier_monthly_totals(db: Session, months: int = 12):
    stmt = (
        select(
            func.date_trunc("month", models.SupplierTransaction.date_posted).label("month"),
            func.coalesce(func.sum(models.SupplierTransaction.amount_dr), 0).label("total_dr"),
            func.coalesce(func.sum(models.SupplierTransaction.amount_cr), 0).label("total_cr"),
        )
        .group_by("month")
        .order_by("month")
    )
    rows = db.execute(stmt).all()
    return rows[-months:] if months else rows


def list_supplier_form_ids(db: Session) -> list[str]:
    rows = db.scalars(
        select(models.SupplierTransaction.form_id)
        .distinct()
        .order_by(models.SupplierTransaction.form_id)
    ).all()
    return [r for r in rows if r]


# --- Management reports ----------------------------------------------------
# A party's balance is sum(amount_dr) - sum(amount_cr) on both sides, but it
# means opposite things: a positive customer balance is owed TO us, a
# positive supplier balance is owed BY us. So "who owes whom" is picked per
# side with `side`: "dr" selects parties whose debits exceed their credits,
# "cr" the reverse, and the outstanding amount is always reported positive.

AGE_BUCKETS = ("0-30 days", "31-60 days", "61-90 days", "Over 90 days")

# How report rows can be ordered; the first is the default.
REPORT_SORTS = {"name": "Name (A-Z)", "balance": "Balance (highest first)"}


def _age_bucket(days: int) -> int:
    if days <= 30:
        return 0
    if days <= 60:
        return 1
    if days <= 90:
        return 2
    return 3


def _outstanding_balances(
    db: Session,
    party_model,
    txn_model,
    fk_column,
    side: str,
    as_of: datetime.date | None = None,
    sort: str = "name",
) -> list[dict]:
    """Every party whose balance is on `side`, with totals and last activity
    date, in alphabetical order (or largest outstanding amount first with
    sort="balance"). With `as_of`, only transactions
    posted on or before that date count, so the report can be re-run for a
    past period end."""
    total_dr = func.coalesce(func.sum(txn_model.amount_dr), 0)
    total_cr = func.coalesce(func.sum(txn_model.amount_cr), 0)
    outstanding = (total_dr - total_cr) if side == "dr" else (total_cr - total_dr)
    stmt = (
        select(
            party_model.id,
            party_model.code,
            party_model.name,
            total_dr.label("total_dr"),
            total_cr.label("total_cr"),
            outstanding.label("balance"),
            func.max(txn_model.date_posted).label("last_activity"),
        )
        .join(txn_model, fk_column == party_model.id)
        .group_by(party_model.id)
        .having(outstanding > 0)
    )
    name_order = (func.lower(party_model.name), party_model.id)
    if sort == "balance":
        stmt = stmt.order_by(outstanding.desc(), *name_order)
    else:
        stmt = stmt.order_by(*name_order)
    if as_of:
        stmt = stmt.where(txn_model.date_posted <= as_of)
    return [row._asdict() for row in db.execute(stmt).all()]


def _age_balances(
    db: Session,
    rows: list[dict],
    txn_model,
    fk_column,
    side: str,
    as_of: datetime.date | None = None,
) -> None:
    """Split each row's outstanding balance into AGE_BUCKETS (adds an
    `ageing` list to every row). Payments are assumed to settle the oldest
    entries first, so whatever is still outstanding is made up of the most
    recent entries on `side` -- e.g. a debtor's balance is aged by their
    latest invoices, walking back until the balance is covered. Age is
    measured from `as_of` (or today)."""
    reference = as_of or datetime.date.today()
    if not rows:
        return
    amount_col = txn_model.amount_dr if side == "dr" else txn_model.amount_cr
    stmt = (
        select(fk_column, txn_model.date_posted, amount_col)
        .where(fk_column.in_([r["id"] for r in rows]), amount_col > 0)
        .order_by(fk_column, txn_model.date_posted.desc(), txn_model.id.desc())
    )
    if as_of:
        stmt = stmt.where(txn_model.date_posted <= as_of)

    entries: dict[int, list[tuple[datetime.date, Decimal]]] = {}
    for party_id, date_posted, amount in db.execute(stmt).all():
        entries.setdefault(party_id, []).append((date_posted, amount))

    for row in rows:
        ageing = [Decimal("0")] * len(AGE_BUCKETS)
        remaining = Decimal(row["balance"])
        for date_posted, amount in entries.get(row["id"], []):
            if remaining <= 0:
                break
            take = min(remaining, Decimal(amount))
            ageing[_age_bucket((reference - date_posted).days)] += take
            remaining -= take
        # Can't normally happen (the balance never exceeds that side's
        # total), but never let the buckets drift from the balance.
        if remaining > 0:
            ageing[-1] += remaining
        row["ageing"] = ageing


def _report_section(
    db: Session,
    title: str,
    party: str,
    detail_prefix: str,
    party_model,
    txn_model,
    fk_column,
    side: str,
    as_of: datetime.date | None,
    ageing: bool,
    sort: str,
) -> dict:
    rows = _outstanding_balances(db, party_model, txn_model, fk_column, side, as_of, sort)
    if ageing:
        _age_balances(db, rows, txn_model, fk_column, side, as_of)
    return {
        "title": title,
        "party": party,
        "detail_prefix": detail_prefix,
        "rows": rows,
        "total": sum((r["balance"] for r in rows), Decimal("0")),
        "ageing_totals": [
            sum((r["ageing"][i] for r in rows), Decimal("0")) for i in range(len(AGE_BUCKETS))
        ]
        if ageing
        else None,
    }


def debtors_report(
    db: Session, as_of: datetime.date | None = None, ageing: bool = False, sort: str = "name"
) -> list[dict]:
    """Everyone who owes us money: customers with a debit balance."""
    return [
        _report_section(
            db, "Customers", "Customer", "/customers/",
            models.Customer, models.Transaction, models.Transaction.customer_id,
            "dr", as_of, ageing, sort,
        ),
    ]


def creditors_report(
    db: Session, as_of: datetime.date | None = None, ageing: bool = False, sort: str = "name"
) -> list[dict]:
    """Everyone we owe money to: suppliers with a balance in their favour,
    plus customers who have paid more than they've been billed."""
    return [
        _report_section(
            db, "Suppliers", "Supplier", "/suppliers/",
            models.Supplier, models.SupplierTransaction, models.SupplierTransaction.supplier_id,
            "dr", as_of, ageing, sort,
        ),
        _report_section(
            db, "Customers in credit (overpaid)", "Customer", "/customers/",
            models.Customer, models.Transaction, models.Transaction.customer_id,
            "cr", as_of, ageing, sort,
        ),
    ]
