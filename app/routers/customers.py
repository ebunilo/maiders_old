from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import crud, schemas
from app.database import get_db

router = APIRouter(prefix="/api/customers", tags=["customers"])


@router.get("")
def list_customers(
    search: str | None = None,
    page: int = 1,
    page_size: int = 25,
    db: Session = Depends(get_db),
):
    results, total = crud.list_customers(db, search=search, page=page, page_size=page_size)
    return {"items": results, "total": total, "page": page, "page_size": page_size}


@router.post("", response_model=schemas.CustomerOut, status_code=201)
def create_customer(data: schemas.CustomerCreate, db: Session = Depends(get_db)):
    if crud.get_customer_by_code(db, data.code):
        raise HTTPException(status_code=409, detail="Customer code already exists")
    return crud.create_customer(db, data)


@router.get("/{customer_id}", response_model=schemas.CustomerWithBalance)
def get_customer(customer_id: int, db: Session = Depends(get_db)):
    customer = crud.get_customer(db, customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    balance = crud.get_customer_balance(db, customer_id)
    return schemas.CustomerWithBalance(
        id=customer.id, code=customer.code, name=customer.name, **balance
    )
