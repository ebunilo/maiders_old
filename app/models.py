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
