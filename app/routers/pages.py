import datetime
import math
from io import BytesIO
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import authz, crud, schemas
from app.database import get_db
from app.pdf import COMPANY_NAME, build_customer_ledger_pdf, build_supplier_ledger_pdf

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
    existing = crud.find_customer_by_same_name(db, name)
    if existing:
        return templates.TemplateResponse(
            request,
            "partials/customer_form.html",
            {
                "error": f'A customer named "{existing.name}" already exists '
                f"(code {existing.code}).",
                "code": code,
                "name": name,
            },
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


@router.get("/customers/lookup", response_class=HTMLResponse)
def customer_lookup(
    request: Request, q: str = "", exclude: int | None = None, db: Session = Depends(get_db)
):
    """Suggestions for the "move transactions to" picker in the delete
    customer dialog."""
    query = q.strip()
    matches = crud.find_customers_by_name(db, query) if query else []
    return templates.TemplateResponse(
        request,
        "partials/customer_suggestions.html",
        {
            "matches": [c for c in matches if c.id != exclude],
            "query": query,
            "select_fn": "selectMergeTarget",
            "empty_message": "No matching customer.",
        },
    )


def _delete_party_form(
    request: Request,
    party,
    party_label: str,
    base_path: str,
    balance: dict,
    similar: list,
    error: str | None = None,
    move_to=None,
):
    """The delete / merge-duplicate dialog, shared by customers and
    suppliers."""
    return templates.TemplateResponse(
        request,
        "partials/party_delete_form.html",
        {
            "party": party,
            "party_label": party_label,
            "base_path": base_path,
            "balance": balance,
            "similar": similar,
            "move_to": move_to,
            "error": error,
        },
    )


def _delete_customer_form(
    request: Request, db: Session, customer, error: str | None = None, move_to=None
):
    return _delete_party_form(
        request,
        customer,
        "Customer",
        "/customers",
        crud.get_customer_balance(db, customer.id),
        crud.find_similar_customers(db, customer),
        error,
        move_to,
    )


@router.get("/customers/{customer_id}/delete", response_class=HTMLResponse)
def delete_customer_form(customer_id: int, request: Request, db: Session = Depends(get_db)):
    customer = crud.get_customer(db, customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    return _delete_customer_form(request, db, customer)


@router.post("/customers/{customer_id}/delete", response_class=HTMLResponse)
def delete_customer_from_form(
    customer_id: int,
    request: Request,
    move_to_id: str | None = Form(None),
    db: Session = Depends(get_db),
):
    customer = crud.get_customer(db, customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    # Blank ("") when nothing was picked -- see customer_id in
    # create_transaction_from_form.
    move_to = crud.get_customer(db, int(move_to_id)) if move_to_id else None
    try:
        crud.delete_customer(db, customer, move_to=move_to)
    except ValueError as exc:
        return _delete_customer_form(request, db, customer, error=str(exc), move_to=move_to)
    # Land on the kept customer's statement so the merged result is visible.
    response = HTMLResponse("")
    response.headers["HX-Redirect"] = f"/customers/{move_to.id}" if move_to else "/customers"
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
        {
            "matches": matches,
            "query": query,
            "select_fn": "selectTxnCustomer",
            "empty_message": "No matching customer. Add them with + New Customer first.",
        },
    )


@router.post("/transactions/new", response_class=HTMLResponse)
def create_transaction_from_form(
    request: Request,
    customer_id: str | None = Form(None),
    date_posted: datetime.date = Form(...),
    details: str | None = Form(None),
    amount_dr: float = Form(0),
    amount_cr: float = Form(0),
    payment_mode: str | None = Form(None),
    bank_name: str | None = Form(None),
    db: Session = Depends(get_db),
):
    # A customer must be picked from the suggestions (which fills the hidden
    # customer_id); a typed name alone is never used to find or create one,
    # so a typo can't post to -- or spawn -- the wrong customer. The form
    # keeps Save disabled until then; this is the server-side backstop.
    # (Taken as str: the field is blank "" until picked, which int would 422 on.)
    if not customer_id or not customer_id.isdigit() or not crud.get_customer(db, int(customer_id)):
        raise HTTPException(status_code=400, detail="Select an existing customer")
    data = schemas.TransactionCreate(
        customer_id=int(customer_id),
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


@router.get("/suppliers/new", response_class=HTMLResponse)
def new_supplier_form(request: Request):
    return templates.TemplateResponse(
        request, "partials/supplier_form.html", {"error": None, "code": "", "name": ""}
    )


@router.post("/suppliers/new", response_class=HTMLResponse)
def create_supplier_from_form(
    request: Request,
    name: str = Form(...),
    code: str | None = Form(None),
    db: Session = Depends(get_db),
):
    name = name.strip()
    code = (code or "").strip()

    def form_error(message: str):
        return templates.TemplateResponse(
            request,
            "partials/supplier_form.html",
            {"error": message, "code": code, "name": name},
        )

    if code and crud.get_supplier_by_code(db, code):
        return form_error("A supplier with that code already exists.")
    existing = crud.find_supplier_by_same_name(db, name)
    if existing:
        return form_error(f'A supplier named "{existing.name}" already exists (code {existing.code}).')
    code = code or crud.generate_supplier_code(db, name)
    crud.create_supplier(db, schemas.SupplierCreate(code=code, name=name))

    response = templates.TemplateResponse(
        request, "partials/supplier_form_success.html", {"code": code, "name": name}
    )
    # Refreshes the suppliers list if it's the page underneath the modal.
    response.headers["HX-Trigger"] = "supplierCreated"
    return response


@router.get("/suppliers/lookup", response_class=HTMLResponse)
def supplier_lookup(
    request: Request, q: str = "", exclude: int | None = None, db: Session = Depends(get_db)
):
    """Suggestions for the "move transactions to" picker in the delete
    supplier dialog."""
    query = q.strip()
    matches = crud.find_suppliers_by_name(db, query) if query else []
    return templates.TemplateResponse(
        request,
        "partials/supplier_suggestions.html",
        {
            "matches": [s for s in matches if s.id != exclude],
            "query": query,
            "select_fn": "selectMergeTarget",
            "empty_message": "No matching supplier.",
        },
    )


def _delete_supplier_form(
    request: Request, db: Session, supplier, error: str | None = None, move_to=None
):
    return _delete_party_form(
        request,
        supplier,
        "Supplier",
        "/suppliers",
        crud.get_supplier_balance(db, supplier.id),
        crud.find_similar_suppliers(db, supplier),
        error,
        move_to,
    )


@router.get("/suppliers/{supplier_id}/delete", response_class=HTMLResponse)
def delete_supplier_form(supplier_id: int, request: Request, db: Session = Depends(get_db)):
    supplier = crud.get_supplier(db, supplier_id)
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")
    return _delete_supplier_form(request, db, supplier)


@router.post("/suppliers/{supplier_id}/delete", response_class=HTMLResponse)
def delete_supplier_from_form(
    supplier_id: int,
    request: Request,
    move_to_id: str | None = Form(None),
    db: Session = Depends(get_db),
):
    supplier = crud.get_supplier(db, supplier_id)
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")
    # Blank ("") when nothing was picked.
    move_to = crud.get_supplier(db, int(move_to_id)) if move_to_id else None
    try:
        crud.delete_supplier(db, supplier, move_to=move_to)
    except ValueError as exc:
        return _delete_supplier_form(request, db, supplier, error=str(exc), move_to=move_to)
    response = HTMLResponse("")
    response.headers["HX-Redirect"] = f"/suppliers/{move_to.id}" if move_to else "/suppliers"
    return response


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
        {
            "matches": matches,
            "query": query,
            "select_fn": "selectTxnSupplier",
            "empty_message": "No matching supplier. Add them with + New Supplier first.",
        },
    )


@router.post("/supplier-transactions/new", response_class=HTMLResponse)
def create_supplier_transaction_from_form(
    request: Request,
    supplier_id: str | None = Form(None),
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
    # A supplier must be picked from the suggestions -- a typed name is never
    # used to find or create one. See create_transaction_from_form.
    if not supplier_id or not supplier_id.isdigit() or not crud.get_supplier(db, int(supplier_id)):
        raise HTTPException(status_code=400, detail="Select an existing supplier")
    data = schemas.SupplierTransactionCreate(
        supplier_id=int(supplier_id),
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


# --- Management reports -------------------------------------------------
# Open to every role (see _REPORT_PREFIXES in app/authz.py), even though
# each report spans both customers and suppliers.


def _parse_as_of(as_of: str | None) -> datetime.date | None:
    # The report's date input is submitted as "" when cleared, which a
    # datetime.date query param would 422 on.
    if not as_of:
        return None
    try:
        return datetime.date.fromisoformat(as_of)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid as_of date")


def _parse_sort(sort: str | None) -> str:
    # Unknown or missing values fall back to the default (alphabetical).
    return sort if sort in crud.REPORT_SORTS else next(iter(crud.REPORT_SORTS))


def _balance_report(
    request: Request,
    kind: str,
    as_of: datetime.date | None,
    ageing: bool,
    sort: str,
    sections: list[dict],
):
    role = request.session.get("role", authz.ADMIN)
    for section in sections:
        # Only link a row to its statement if this role can open it --
        # e.g. a customer-user sees suppliers here but can't drill in.
        section["can_link"] = authz.path_allowed(role, section["detail_prefix"] + "0")
    return templates.TemplateResponse(
        request,
        "balance_report.html",
        {
            "kind": kind,
            "sections": sections,
            "count": sum(len(s["rows"]) for s in sections),
            "grand_total": sum((s["total"] for s in sections), 0),
            "ageing": ageing,
            "age_buckets": crud.AGE_BUCKETS,
            "sort": sort,
            "sorts": crud.REPORT_SORTS,
            "as_of": as_of,
            "generated_at": datetime.datetime.now(),
            "company_name": COMPANY_NAME,
            "active": "reports",
        },
    )


@router.get("/reports/debtors", response_class=HTMLResponse)
def debtors_report(
    request: Request,
    as_of: str | None = None,
    ageing: bool = False,
    sort: str | None = None,
    db: Session = Depends(get_db),
):
    as_of_date = _parse_as_of(as_of)
    sort = _parse_sort(sort)
    sections = crud.debtors_report(db, as_of=as_of_date, ageing=ageing, sort=sort)
    return _balance_report(request, "debtors", as_of_date, ageing, sort, sections)


@router.get("/reports/creditors", response_class=HTMLResponse)
def creditors_report(
    request: Request,
    as_of: str | None = None,
    ageing: bool = False,
    sort: str | None = None,
    db: Session = Depends(get_db),
):
    as_of_date = _parse_as_of(as_of)
    sort = _parse_sort(sort)
    sections = crud.creditors_report(db, as_of=as_of_date, ageing=ageing, sort=sort)
    return _balance_report(request, "creditors", as_of_date, ageing, sort, sections)
