import datetime
from decimal import Decimal

from sqlalchemy import func, or_, select
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


def get_or_create_customer(db: Session, code: str, name: str | None) -> models.Customer:
    code = code.strip()
    name = (name or code).strip()
    customer = get_customer_by_code(db, code)
    if customer:
        return customer
    customer = models.Customer(code=code, name=name)
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


def generate_customer_code(db: Session, name: str) -> str:
    """Derive a short, unique customer code from a name when the caller
    doesn't supply one (e.g. new customers added from the transaction form)."""
    base = "".join(ch for ch in name.upper() if ch.isalnum()) or "CUST"
    base = base[:12]
    code = base
    suffix = 1
    while db.scalar(select(models.Customer.id).where(models.Customer.code == code)):
        suffix += 1
        code = f"{base}{suffix}"
    return code


def resolve_transaction_customer(db: Session, data: schemas.TransactionCreate) -> models.Customer:
    """Find the customer a new transaction belongs to, preferring an explicit
    id or code, and falling back to an exact (case-insensitive) name match
    before creating a brand-new customer record with a generated code."""
    if data.customer_id:
        customer = get_customer(db, data.customer_id)
        if customer:
            return customer

    if data.customer_code:
        return get_or_create_customer(db, data.customer_code, data.customer_name)

    name = (data.customer_name or "").strip()
    if not name:
        raise ValueError("customer_name, customer_code, or customer_id is required")

    customer = db.scalar(
        select(models.Customer).where(func.lower(models.Customer.name) == name.lower())
    )
    if customer:
        return customer

    customer = models.Customer(code=generate_customer_code(db, name), name=name)
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


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


def get_or_create_supplier(db: Session, code: str, name: str | None) -> models.Supplier:
    code = code.strip()
    name = (name or code).strip()
    supplier = get_supplier_by_code(db, code)
    if supplier:
        return supplier
    supplier = models.Supplier(code=code, name=name)
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
    base = "".join(ch for ch in name.upper() if ch.isalnum()) or "SUPP"
    base = base[:12]
    code = base
    suffix = 1
    while db.scalar(select(models.Supplier.id).where(models.Supplier.code == code)):
        suffix += 1
        code = f"{base}{suffix}"
    return code


def resolve_supplier_transaction_supplier(
    db: Session, data: schemas.SupplierTransactionCreate
) -> models.Supplier:
    if data.supplier_id:
        supplier = get_supplier(db, data.supplier_id)
        if supplier:
            return supplier

    if data.supplier_code:
        return get_or_create_supplier(db, data.supplier_code, data.supplier_name)

    name = (data.supplier_name or "").strip()
    if not name:
        raise ValueError("supplier_name, supplier_code, or supplier_id is required")

    supplier = db.scalar(
        select(models.Supplier).where(func.lower(models.Supplier.name) == name.lower())
    )
    if supplier:
        return supplier

    supplier = models.Supplier(code=generate_supplier_code(db, name), name=name)
    db.add(supplier)
    db.commit()
    db.refresh(supplier)
    return supplier


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
