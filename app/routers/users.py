from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.database import get_db
from app.deps import require_role
from app.auth import hash_password
from app import models

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="templates")

ROLES = ["planta", "autorizador", "admin", "superadmin"]
PLANTS = ["MTR1", "MTR2", "ROSARIO", "TODAS"]

@router.get("/users", response_class=HTMLResponse)
async def list_users(request: Request, db: Session = Depends(get_db), current_user=Depends(require_role("superadmin"))):
    users = db.query(models.User).order_by(models.User.name).all()
    return templates.TemplateResponse(request, "admin/users.html", {"user": current_user, "users": users})

@router.get("/users/new", response_class=HTMLResponse)
async def new_user_form(request: Request, current_user=Depends(require_role("superadmin"))):
    return templates.TemplateResponse(request, "admin/user_form.html", {
        "user": current_user,
        "edit_user": None, "roles": ROLES, "plants": PLANTS, "error": None
    })

@router.post("/users/new")
async def create_user(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    role: str = Form(...),
    plant: str = Form(...),
    db: Session = Depends(get_db),
    current_user=Depends(require_role("superadmin"))
):
    existing = db.query(models.User).filter(models.User.email == email).first()
    if existing:
        return templates.TemplateResponse(request, "admin/user_form.html", {
            "user": current_user,
            "edit_user": None, "roles": ROLES, "plants": PLANTS,
            "error": "El email ya está registrado"
        })
    new_user = models.User(name=name, email=email, hashed_password=hash_password(password), role=role, plant=plant)
    db.add(new_user)
    db.commit()
    return RedirectResponse(url="/admin/users", status_code=303)

@router.post("/users/{user_id}/toggle")
async def toggle_user(user_id: int, db: Session = Depends(get_db), current_user=Depends(require_role("superadmin"))):
    u = db.query(models.User).filter(models.User.id == user_id).first()
    u.active = not u.active
    db.commit()
    return RedirectResponse(url="/admin/users", status_code=303)
