from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import crud, schemas
from app.database import get_db

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=schemas.DashboardSummary)
def summary(db: Session = Depends(get_db)):
    return crud.get_dashboard_summary(db)


@router.get("/top-debtors", response_model=list[schemas.TopCustomer])
def top_debtors(limit: int = 10, db: Session = Depends(get_db)):
    return crud.get_top_debtors(db, limit=limit)


@router.get("/monthly-totals")
def monthly_totals(months: int = 12, db: Session = Depends(get_db)):
    rows = crud.get_monthly_totals(db, months=months)
    return [
        {"month": m.date().isoformat(), "total_dr": dr, "total_cr": cr} for m, dr, cr in rows
    ]
