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
    customer = get_or_create_customer(db, data.customer_code, data.customer_name)
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
