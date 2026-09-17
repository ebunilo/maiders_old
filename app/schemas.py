import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class CustomerBase(BaseModel):
    code: str
    name: str


class CustomerCreate(CustomerBase):
    pass


class CustomerOut(CustomerBase):
    model_config = ConfigDict(from_attributes=True)

    id: int


class CustomerWithBalance(CustomerOut):
    total_dr: Decimal
    total_cr: Decimal
    balance: Decimal
    transaction_count: int


class TransactionBase(BaseModel):
    ref_no: str | None = None
    date_posted: datetime.date
    details: str | None = None
    amount_dr: Decimal = Decimal("0")
    amount_cr: Decimal = Decimal("0")
    invoice_no: str | None = None
    payment_mode: str | None = None
    bank_name: str | None = None
    trans_no: str | None = None
    form_id: str | None = None


class TransactionCreate(TransactionBase):
    customer_code: str
    customer_name: str | None = None


class TransactionUpdate(BaseModel):
    ref_no: str | None = None
    date_posted: datetime.date | None = None
    details: str | None = None
    amount_dr: Decimal | None = None
    amount_cr: Decimal | None = None
    invoice_no: str | None = None
    payment_mode: str | None = None
    bank_name: str | None = None
    trans_no: str | None = None
    form_id: str | None = None


class TransactionOut(TransactionBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    customer_id: int


class TransactionWithBalance(TransactionOut):
    running_balance: Decimal
    customer_code: str
    customer_name: str


class DashboardSummary(BaseModel):
    total_customers: int
    total_transactions: int
    total_dr: Decimal
    total_cr: Decimal
    net_outstanding: Decimal


class TopCustomer(BaseModel):
    code: str
    name: str
    balance: Decimal
