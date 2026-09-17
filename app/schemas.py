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
    customer_id: int | None = None
    customer_code: str | None = None
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


class SupplierBase(BaseModel):
    code: str
    name: str


class SupplierCreate(SupplierBase):
    pass


class SupplierOut(SupplierBase):
    model_config = ConfigDict(from_attributes=True)

    id: int


class SupplierWithBalance(SupplierOut):
    total_dr: Decimal
    total_cr: Decimal
    balance: Decimal
    transaction_count: int


class SupplierTransactionBase(BaseModel):
    ref_no: str | None = None
    date_posted: datetime.date
    details: str | None = None
    amount_dr: Decimal = Decimal("0")
    amount_cr: Decimal = Decimal("0")
    payment_mode: str | None = None
    account_used: str | None = None
    trans_no: str | None = None
    form_id: str | None = None
    item_name: str | None = None
    measures: str | None = None
    quantity: Decimal | None = None
    unit_cost: Decimal | None = None
    vehicle_no: str | None = None


class SupplierTransactionCreate(SupplierTransactionBase):
    supplier_id: int | None = None
    supplier_code: str | None = None
    supplier_name: str | None = None


class SupplierTransactionOut(SupplierTransactionBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    supplier_id: int


class SupplierDashboardSummary(BaseModel):
    total_suppliers: int
    total_transactions: int
    total_dr: Decimal
    total_cr: Decimal
    net_payable: Decimal


class TopSupplier(BaseModel):
    code: str
    name: str
    balance: Decimal
