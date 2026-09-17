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
from app.pdf import build_customer_ledger_pdf

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory="app/templates")


def format_money(value) -> str:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "0.00"
    return f"{value:,.2f}"


templates.env.filters["money"] = format_money


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
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "summary": summary,
            "top_debtors": top_debtors,
            "monthly_data": monthly_data,
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
    customer_id: int | None = Form(None),
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
        customer_id=customer_id,
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
