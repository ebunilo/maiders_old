import datetime
import math
from io import BytesIO
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import crud, schemas
from app.database import get_db
from app.pdf import build_customer_ledger_pdf, build_supplier_ledger_pdf

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory="app/templates")


def format_money(value) -> str:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "0.00"
    return f"{value:,.2f}"


templates.env.filters["money"] = format_money


def relative_url(url) -> str:
    """Path + query only, so pagination/search links stay same-origin behind
    a reverse proxy or TLS terminator (request.url's scheme/host reflect
    what the app process saw, not what the browser is using)."""
    return url.path + (f"?{url.query}" if url.query else "")


templates.env.filters["relurl"] = relative_url


def _pagination(total: int, page: int, page_size: int) -> dict:
    pages = max(1, math.ceil(total / page_size)) if page_size else 1
    window_start = max(1, page - 2)
    window_end = min(pages, page + 2)
    window = list(range(window_start, window_end + 1))
    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": pages,
        "window": window,
        "has_prev": page > 1,
        "has_next": page < pages,
    }


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    summary = crud.get_dashboard_summary(db)
    top_debtors = crud.get_top_debtors(db, limit=10)
    monthly = crud.get_monthly_totals(db, months=12)
    monthly_data = [
        {"month": m.strftime("%b %Y"), "total_dr": float(dr), "total_cr": float(cr)}
        for m, dr, cr in monthly
    ]

    supplier_summary = crud.get_supplier_dashboard_summary(db)
    top_creditors = crud.get_top_creditors(db, limit=10)
    supplier_monthly = crud.get_supplier_monthly_totals(db, months=12)
    supplier_monthly_data = [
        {"month": m.strftime("%b %Y"), "total_dr": float(dr), "total_cr": float(cr)}
        for m, dr, cr in supplier_monthly
    ]

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "summary": summary,
            "top_debtors": top_debtors,
            "monthly_data": monthly_data,
            "supplier_summary": supplier_summary,
            "top_creditors": top_creditors,
            "supplier_monthly_data": supplier_monthly_data,
            "active": "dashboard",
        },
    )


@router.get("/customers", response_class=HTMLResponse)
def customers_page(
    request: Request,
    search: str | None = None,
    page: int = 1,
    db: Session = Depends(get_db),
):
    page_size = 25
    results, total = crud.list_customers(db, search=search, page=page, page_size=page_size)
    ctx = {
        "customers": results,
        "search": search or "",
        "pagination": _pagination(total, page, page_size),
        "active": "customers",
    }
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(request, "partials/customers_table.html", ctx)
    return templates.TemplateResponse(request, "customers.html", ctx)


@router.get("/customers/new", response_class=HTMLResponse)
def new_customer_form(request: Request):
    return templates.TemplateResponse(
        request,
        "partials/customer_form.html",
        {"error": None, "code": "", "name": ""},
    )


@router.post("/customers/new", response_class=HTMLResponse)
def create_customer_from_form(
    request: Request,
    code: str = Form(...),
    name: str = Form(...),
    db: Session = Depends(get_db),
):
    code = code.strip()
    name = name.strip()
    if crud.get_customer_by_code(db, code):
        return templates.TemplateResponse(
            request,
            "partials/customer_form.html",
            {"error": "A customer with that code already exists.", "code": code, "name": name},
        )
    crud.create_customer(db, schemas.CustomerCreate(code=code, name=name))

    response = templates.TemplateResponse(
        request,
        "partials/customer_form_success.html",
        {"code": code, "name": name},
    )
    # Lets any results table on the current page (e.g. the customers list)
    # refresh itself without this modal needing to know if one is present.
    response.headers["HX-Trigger"] = "customerCreated"
    return response


@router.get("/customers/{customer_id}", response_class=HTMLResponse)
def customer_detail(
    customer_id: int,
    request: Request,
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
    form_id: str | None = None,
    page: int = 1,
    db: Session = Depends(get_db),
):
    customer = crud.get_customer(db, customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    balance = crud.get_customer_balance(db, customer_id)
    page_size = 30
    rows, total = crud.list_transactions_for_customer(
        db,
        customer_id,
        date_from=date_from,
        date_to=date_to,
        form_id=form_id,
        page=page,
        page_size=page_size,
    )
    form_ids = crud.list_form_ids(db)
    pdf_query = {
        k: v
        for k, v in {"date_from": date_from, "date_to": date_to, "form_id": form_id}.items()
        if v
    }
    pdf_url = f"/customers/{customer_id}/ledger.pdf"
    if pdf_query:
        pdf_url += f"?{urlencode(pdf_query)}"
    ctx = {
        "customer": customer,
        "balance": balance,
        "rows": rows,
        "form_ids": form_ids,
        "filters": {
            "date_from": date_from or "",
            "date_to": date_to or "",
            "form_id": form_id or "",
        },
        "pdf_url": pdf_url,
        "pagination": _pagination(total, page, page_size),
        "active": "customers",
    }
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(request, "partials/statement_table.html", ctx)
    return templates.TemplateResponse(request, "customer_detail.html", ctx)


@router.get("/customers/{customer_id}/ledger.pdf")
def customer_ledger_pdf(
    customer_id: int,
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
    form_id: str | None = None,
    db: Session = Depends(get_db),
):
    customer = crud.get_customer(db, customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    balance = crud.get_customer_balance(db, customer_id)
    rows = crud.get_full_customer_statement(
        db, customer_id, date_from=date_from, date_to=date_to, form_id=form_id
    )
    pdf_bytes = build_customer_ledger_pdf(
        customer,
        balance,
        rows,
        {"date_from": date_from, "date_to": date_to, "form_id": form_id},
        datetime.datetime.now(),
    )
    safe_code = "".join(c if c.isalnum() else "_" for c in customer.code).strip("_")
    filename = f"ledger_{safe_code}_{datetime.date.today().isoformat()}.pdf"
    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.get("/transactions", response_class=HTMLResponse)
def transactions_page(
    request: Request,
    search: str | None = None,
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
    form_id: str | None = None,
    page: int = 1,
    db: Session = Depends(get_db),
):
    page_size = 30
    rows, total = crud.list_transactions(
        db,
        search=search,
        date_from=date_from,
        date_to=date_to,
        form_id=form_id,
        page=page,
        page_size=page_size,
    )
    form_ids = crud.list_form_ids(db)
    ctx = {
        "rows": rows,
        "form_ids": form_ids,
        "filters": {
            "search": search or "",
            "date_from": date_from or "",
            "date_to": date_to or "",
            "form_id": form_id or "",
        },
        "pagination": _pagination(total, page, page_size),
        "active": "transactions",
    }
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(request, "partials/transactions_table.html", ctx)
    return templates.TemplateResponse(request, "transactions.html", ctx)


@router.get("/transactions/new", response_class=HTMLResponse)
def new_transaction_form(request: Request):
    return templates.TemplateResponse(
        request,
        "partials/transaction_form.html",
        {
            "txn": None,
            "today": datetime.date.today().isoformat(),
        },
    )


@router.get("/transactions/customer-lookup", response_class=HTMLResponse)
def transaction_customer_lookup(
    request: Request, customer_name: str = "", db: Session = Depends(get_db)
):
    query = customer_name.strip()
    matches = crud.find_customers_by_name(db, query) if query else []
    return templates.TemplateResponse(
        request,
        "partials/customer_suggestions.html",
        {"matches": matches, "query": query},
    )


@router.post("/transactions/new", response_class=HTMLResponse)
def create_transaction_from_form(
    request: Request,
    customer_id: str | None = Form(None),
    customer_name: str = Form(...),
    date_posted: datetime.date = Form(...),
    details: str | None = Form(None),
    amount_dr: float = Form(0),
    amount_cr: float = Form(0),
    payment_mode: str | None = Form(None),
    bank_name: str | None = Form(None),
    db: Session = Depends(get_db),
):
    data = schemas.TransactionCreate(
        # The hidden customer_id field is blank ("") whenever the user
        # types a brand-new customer name instead of picking a suggestion --
        # int | None = Form(None) would 422 on that empty string.
        customer_id=int(customer_id) if customer_id else None,
        customer_name=customer_name,
        date_posted=date_posted,
        details=details,
        amount_dr=amount_dr,
        amount_cr=amount_cr,
        payment_mode=payment_mode,
        bank_name=bank_name,
    )
    crud.create_transaction(db, data)

    rows, total = crud.list_transactions(db, page=1, page_size=30)
    form_ids = crud.list_form_ids(db)
    ctx = {
        "rows": rows,
        "form_ids": form_ids,
        "filters": {"search": "", "date_from": "", "date_to": "", "form_id": ""},
        "pagination": _pagination(total, 1, 30),
        "active": "transactions",
        "flash": "Transaction added successfully.",
    }
    return templates.TemplateResponse(request, "partials/transactions_table.html", ctx)


@router.delete("/transactions/{transaction_id}", response_class=HTMLResponse)
def delete_transaction_from_ui(transaction_id: int, request: Request, db: Session = Depends(get_db)):
    txn = crud.get_transaction(db, transaction_id)
    if txn:
        crud.delete_transaction(db, txn)
    return HTMLResponse("")


# --- Suppliers ---------------------------------------------------------


@router.get("/suppliers", response_class=HTMLResponse)
def suppliers_page(
    request: Request,
    search: str | None = None,
    page: int = 1,
    db: Session = Depends(get_db),
):
    page_size = 25
    results, total = crud.list_suppliers(db, search=search, page=page, page_size=page_size)
    ctx = {
        "suppliers": results,
        "search": search or "",
        "pagination": _pagination(total, page, page_size),
        "active": "suppliers",
    }
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(request, "partials/suppliers_table.html", ctx)
    return templates.TemplateResponse(request, "suppliers.html", ctx)


@router.get("/suppliers/{supplier_id}", response_class=HTMLResponse)
def supplier_detail(
    supplier_id: int,
    request: Request,
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
    form_id: str | None = None,
    page: int = 1,
    db: Session = Depends(get_db),
):
    supplier = crud.get_supplier(db, supplier_id)
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")
    balance = crud.get_supplier_balance(db, supplier_id)
    page_size = 30
    rows, total = crud.list_supplier_transactions_for_supplier(
        db,
        supplier_id,
        date_from=date_from,
        date_to=date_to,
        form_id=form_id,
        page=page,
        page_size=page_size,
    )
    form_ids = crud.list_supplier_form_ids(db)
    pdf_query = {
        k: v
        for k, v in {"date_from": date_from, "date_to": date_to, "form_id": form_id}.items()
        if v
    }
    pdf_url = f"/suppliers/{supplier_id}/ledger.pdf"
    if pdf_query:
        pdf_url += f"?{urlencode(pdf_query)}"
    ctx = {
        "supplier": supplier,
        "balance": balance,
        "rows": rows,
        "form_ids": form_ids,
        "filters": {
            "date_from": date_from or "",
            "date_to": date_to or "",
            "form_id": form_id or "",
        },
        "pdf_url": pdf_url,
        "pagination": _pagination(total, page, page_size),
        "active": "suppliers",
    }
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(request, "partials/supplier_statement_table.html", ctx)
    return templates.TemplateResponse(request, "supplier_detail.html", ctx)


@router.get("/suppliers/{supplier_id}/ledger.pdf")
def supplier_ledger_pdf(
    supplier_id: int,
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
    form_id: str | None = None,
    db: Session = Depends(get_db),
):
    supplier = crud.get_supplier(db, supplier_id)
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")
    balance = crud.get_supplier_balance(db, supplier_id)
    rows = crud.get_full_supplier_statement(
        db, supplier_id, date_from=date_from, date_to=date_to, form_id=form_id
    )
    pdf_bytes = build_supplier_ledger_pdf(
        supplier,
        balance,
        rows,
        {"date_from": date_from, "date_to": date_to, "form_id": form_id},
        datetime.datetime.now(),
    )
    safe_code = "".join(c if c.isalnum() else "_" for c in supplier.code).strip("_")
    filename = f"supplier_ledger_{safe_code}_{datetime.date.today().isoformat()}.pdf"
    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.get("/supplier-transactions", response_class=HTMLResponse)
def supplier_transactions_page(
    request: Request,
    search: str | None = None,
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
    form_id: str | None = None,
    page: int = 1,
    db: Session = Depends(get_db),
):
    page_size = 30
    rows, total = crud.list_supplier_transactions(
        db,
        search=search,
        date_from=date_from,
        date_to=date_to,
        form_id=form_id,
        page=page,
        page_size=page_size,
    )
    form_ids = crud.list_supplier_form_ids(db)
    ctx = {
        "rows": rows,
        "form_ids": form_ids,
        "filters": {
            "search": search or "",
            "date_from": date_from or "",
            "date_to": date_to or "",
            "form_id": form_id or "",
        },
        "pagination": _pagination(total, page, page_size),
        "active": "supplier_transactions",
    }
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            request, "partials/supplier_transactions_table.html", ctx
        )
    return templates.TemplateResponse(request, "supplier_transactions.html", ctx)


@router.get("/supplier-transactions/new", response_class=HTMLResponse)
def new_supplier_transaction_form(request: Request):
    return templates.TemplateResponse(
        request,
        "partials/supplier_transaction_form.html",
        {"today": datetime.date.today().isoformat()},
    )


@router.get("/supplier-transactions/supplier-lookup", response_class=HTMLResponse)
def supplier_transaction_supplier_lookup(
    request: Request, supplier_name: str = "", db: Session = Depends(get_db)
):
    query = supplier_name.strip()
    matches = crud.find_suppliers_by_name(db, query) if query else []
    return templates.TemplateResponse(
        request,
        "partials/supplier_suggestions.html",
        {"matches": matches, "query": query},
    )


@router.post("/supplier-transactions/new", response_class=HTMLResponse)
def create_supplier_transaction_from_form(
    request: Request,
    supplier_id: str | None = Form(None),
    supplier_name: str = Form(...),
    date_posted: datetime.date = Form(...),
    details: str | None = Form(None),
    amount_cr: float = Form(0),
    payment_mode: str | None = Form(None),
    account_used: str | None = Form(None),
    item_name: str | None = Form(None),
    measures: str | None = Form(None),
    quantity: str | None = Form(None),
    unit_cost: str | None = Form(None),
    vehicle_no: str | None = Form(None),
    db: Session = Depends(get_db),
):
    # Left blank for a payment (credit) entry, where quantity/unit cost
    # don't apply -- the browser still submits the field as "", which
    # float-parses to a 422 if passed through as-is.
    quantity_val = float(quantity) if quantity else None
    unit_cost_val = float(unit_cost) if unit_cost else None
    data = schemas.SupplierTransactionCreate(
        # Blank ("") whenever the user types a brand-new supplier name
        # instead of picking a suggestion -- see customer_id above.
        supplier_id=int(supplier_id) if supplier_id else None,
        supplier_name=supplier_name,
        date_posted=date_posted,
        details=details,
        # Debit (goods received) is derived from quantity x unit cost rather
        # than typed in directly, so it always matches the line-item detail.
        amount_dr=(quantity_val or 0) * (unit_cost_val or 0),
        amount_cr=amount_cr,
        payment_mode=payment_mode,
        account_used=account_used,
        item_name=item_name,
        measures=measures,
        quantity=quantity_val,
        unit_cost=unit_cost_val,
        vehicle_no=vehicle_no,
    )
    crud.create_supplier_transaction(db, data)

    rows, total = crud.list_supplier_transactions(db, page=1, page_size=30)
    form_ids = crud.list_supplier_form_ids(db)
    ctx = {
        "rows": rows,
        "form_ids": form_ids,
        "filters": {"search": "", "date_from": "", "date_to": "", "form_id": ""},
        "pagination": _pagination(total, 1, 30),
        "active": "supplier_transactions",
        "flash": "Supplier transaction added successfully.",
    }
    return templates.TemplateResponse(
        request, "partials/supplier_transactions_table.html", ctx
    )


@router.delete("/supplier-transactions/{transaction_id}", response_class=HTMLResponse)
def delete_supplier_transaction_from_ui(
    transaction_id: int, request: Request, db: Session = Depends(get_db)
):
    txn = crud.get_supplier_transaction(db, transaction_id)
    if txn:
        crud.delete_supplier_transaction(db, txn)
    return HTMLResponse("")
