from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.database import get_db
from app.deps import get_current_user
from app import models

router = APIRouter()
templates = Jinja2Templates(directory="templates")

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    plant: str = "",
    status: str = "",
    supplier_id: str = "",
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    query = db.query(models.Purchase)

    # Usuarios de planta solo ven sus compras
    if current_user.role == "planta":
        query = query.filter(models.Purchase.requested_by_id == current_user.id)
    elif current_user.role == "autorizador" and current_user.plant != "TODAS":
        query = query.filter(models.Purchase.plant == current_user.plant)

    if plant:
        query = query.filter(models.Purchase.plant == plant)
    if status:
        query = query.filter(models.Purchase.status == status)
    if supplier_id:
        query = query.filter(models.Purchase.supplier_id == int(supplier_id))

    purchases = query.order_by(models.Purchase.created_at.desc()).limit(50).all()

    # Conteos por estado
    all_q = db.query(models.Purchase)
    if current_user.role == "planta":
        all_q = all_q.filter(models.Purchase.requested_by_id == current_user.id)

    counts = {
        "pendiente": all_q.filter(models.Purchase.status == "pendiente").count(),
        "aprobada": all_q.filter(models.Purchase.status == "aprobada").count(),
        "recibida": all_q.filter(models.Purchase.status == "recibida").count(),
        "facturada": all_q.filter(models.Purchase.status == "facturada").count(),
    }

    suppliers = db.query(models.Supplier).filter(models.Supplier.active == True).all()

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user": current_user,
        "purchases": purchases,
        "counts": counts,
        "suppliers": suppliers,
        "filters": {"plant": plant, "status": status, "supplier_id": supplier_id}
    })
