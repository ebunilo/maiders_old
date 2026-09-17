import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import crud, schemas
from app.database import get_db

router = APIRouter(prefix="/api/transactions", tags=["transactions"])


@router.get("")
def list_transactions(
    search: str | None = None,
    customer_id: int | None = None,
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
    form_id: str | None = None,
    page: int = 1,
    page_size: int = 25,
    db: Session = Depends(get_db),
):
    rows, total = crud.list_transactions(
        db,
        search=search,
        customer_id=customer_id,
        date_from=date_from,
        date_to=date_to,
        form_id=form_id,
        page=page,
        page_size=page_size,
    )
    items = [
        schemas.TransactionOut.model_validate(t).model_dump() | {"customer_code": c.code, "customer_name": c.name}
        for t, c in rows
    ]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.post("", response_model=schemas.TransactionOut, status_code=201)
def create_transaction(data: schemas.TransactionCreate, db: Session = Depends(get_db)):
    return crud.create_transaction(db, data)


@router.get("/{transaction_id}", response_model=schemas.TransactionOut)
def get_transaction(transaction_id: int, db: Session = Depends(get_db)):
    txn = crud.get_transaction(db, transaction_id)
    if not txn:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return txn


@router.put("/{transaction_id}", response_model=schemas.TransactionOut)
def update_transaction(
    transaction_id: int, data: schemas.TransactionUpdate, db: Session = Depends(get_db)
):
    txn = crud.get_transaction(db, transaction_id)
    if not txn:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return crud.update_transaction(db, txn, data)


@router.delete("/{transaction_id}", status_code=204)
def delete_transaction(transaction_id: int, db: Session = Depends(get_db)):
    txn = crud.get_transaction(db, transaction_id)
    if not txn:
        raise HTTPException(status_code=404, detail="Transaction not found")
    crud.delete_transaction(db, txn)
