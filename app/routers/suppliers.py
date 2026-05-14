from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.database import get_db
from app.deps import get_current_user, require_role
from app import models

router = APIRouter(prefix="/suppliers")
templates = Jinja2Templates(directory="templates")

@router.get("", response_class=HTMLResponse)
async def list_suppliers(request: Request, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    suppliers = db.query(models.Supplier).order_by(models.Supplier.name).all()
    return templates.TemplateResponse("suppliers/list.html", {"request": request, "user": current_user, "suppliers": suppliers})

@router.get("/new", response_class=HTMLResponse)
async def new_supplier_form(request: Request, current_user=Depends(require_role("admin", "superadmin"))):
    return templates.TemplateResponse("suppliers/form.html", {"request": request, "user": current_user, "supplier": None, "error": None})

@router.post("/new")
async def create_supplier(
    request: Request,
    name: str = Form(...),
    cuit: str = Form(""),
    contact_name: str = Form(""),
    contact_phone: str = Form(""),
    email: str = Form(""),
    db: Session = Depends(get_db),
    current_user=Depends(require_role("admin", "superadmin"))
):
    supplier = models.Supplier(name=name, cuit=cuit, contact_name=contact_name, contact_phone=contact_phone, email=email)
    db.add(supplier)
    db.commit()
    return RedirectResponse(url="/suppliers", status_code=303)

@router.get("/{supplier_id}/edit", response_class=HTMLResponse)
async def edit_supplier_form(supplier_id: int, request: Request, db: Session = Depends(get_db), current_user=Depends(require_role("admin", "superadmin"))):
    supplier = db.query(models.Supplier).filter(models.Supplier.id == supplier_id).first()
    return templates.TemplateResponse("suppliers/form.html", {"request": request, "user": current_user, "supplier": supplier, "error": None})

@router.post("/{supplier_id}/edit")
async def update_supplier(
    supplier_id: int,
    request: Request,
    name: str = Form(...),
    cuit: str = Form(""),
    contact_name: str = Form(""),
    contact_phone: str = Form(""),
    email: str = Form(""),
    active: str = Form("on"),
    db: Session = Depends(get_db),
    current_user=Depends(require_role("admin", "superadmin"))
):
    supplier = db.query(models.Supplier).filter(models.Supplier.id == supplier_id).first()
    supplier.name = name
    supplier.cuit = cuit
    supplier.contact_name = contact_name
    supplier.contact_phone = contact_phone
    supplier.email = email
    supplier.active = (active == "on")
    db.commit()
    return RedirectResponse(url="/suppliers", status_code=303)
