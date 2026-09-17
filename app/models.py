import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (UniqueConstraint("code", name="uq_customers_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(255), index=True)  # normalized CustomerID
    name: Mapped[str] = mapped_column(String(255))  # best-known CustomerName
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    transactions: Mapped[list["Transaction"]] = relationship(
        back_populates="customer", cascade="all, delete-orphan"
    )


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), index=True
    )
    ref_no: Mapped[str | None] = mapped_column(Text, nullable=True)
    date_posted: Mapped[datetime.date] = mapped_column(index=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    amount_dr: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    amount_cr: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    invoice_no: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payment_mode: Mapped[str | None] = mapped_column(String(100), nullable=True)
    bank_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    trans_no: Mapped[str | None] = mapped_column(String(255), nullable=True)
    form_id: Mapped[str | None] = mapped_column(String(20), index=True, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    customer: Mapped["Customer"] = relationship(back_populates="transactions")

    __table_args__ = (
        Index("ix_transactions_customer_date", "customer_id", "date_posted"),
    )


class Supplier(Base):
    __tablename__ = "suppliers"
    __table_args__ = (UniqueConstraint("code", name="uq_suppliers_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(255), index=True)
    name: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    transactions: Mapped[list["SupplierTransaction"]] = relationship(
        back_populates="supplier", cascade="all, delete-orphan"
    )


class SupplierTransaction(Base):
    """A purchase from, or payment to, a supplier. amount_dr is goods/value
    received (increases what we owe the supplier); amount_cr is a payment we
    made (decreases what we owe) -- the inverse relationship to Transaction,
    where amount_dr increases what the customer owes us."""

    __tablename__ = "supplier_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_id: Mapped[int] = mapped_column(
        ForeignKey("suppliers.id", ondelete="CASCADE"), index=True
    )
    ref_no: Mapped[str | None] = mapped_column(Text, nullable=True)
    date_posted: Mapped[datetime.date] = mapped_column(index=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    amount_dr: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    amount_cr: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    payment_mode: Mapped[str | None] = mapped_column(String(100), nullable=True)
    account_used: Mapped[str | None] = mapped_column(String(255), nullable=True)
    trans_no: Mapped[str | None] = mapped_column(String(255), nullable=True)
    form_id: Mapped[str | None] = mapped_column(String(20), index=True, nullable=True)
    item_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    measures: Mapped[str | None] = mapped_column(String(50), nullable=True)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(14, 3), nullable=True)
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    vehicle_no: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    supplier: Mapped["Supplier"] = relationship(back_populates="transactions")

    __table_args__ = (
        Index("ix_supplier_transactions_supplier_date", "supplier_id", "date_posted"),
    )
