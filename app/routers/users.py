import json
from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from app.database import get_db
from app.permissions import require_perm, MODULES, ALL_KEYS_SET, role_defaults, user_grants, has_custom_permissions
from app.auth import hash_password
from app import models
from app.templates import templates

router = APIRouter(prefix="/admin")

ROLES = ["planta", "autorizador", "admin", "superadmin"]
PLANTS = ["MTR1", "MTR2", "ROSARIO", "TODAS"]

# Acceso a la administración de usuarios → permiso "usuarios.usuarios".
_guard = require_perm("usuarios.usuarios")


def _parse_permissions(form_mode: str, checked_keys: list[str]):
    """Devuelve el valor a guardar en User.permissions.

    mode == "default" → None (el usuario hereda los defaults de su rol).
    mode == "custom"  → JSON con la lista de claves válidas marcadas.
    """
    if form_mode != "custom":
        return None
    keys = sorted({k for k in checked_keys if k in ALL_KEYS_SET})
    return json.dumps(keys)


def _form_ctx(current_user, edit_user=None, error=None):
    """Contexto común del form (alta/edición): catálogo + estado de permisos."""
    if edit_user is not None:
        grants = user_grants(edit_user)
        custom = has_custom_permissions(edit_user)
    else:
        grants = set()
        custom = False
    # Mapa rol → claves por defecto (para que el form muestre el preview en JS).
    role_default_map = {r: sorted(role_defaults(r)) for r in (ROLES + ["superadmin"])}
    return {
        "user": current_user,
        "edit_user": edit_user,
        "roles": ROLES,
        "plants": PLANTS,
        "modules": MODULES,
        "granted": grants,            # set de claves actualmente concedidas (para tildar)
        "is_custom": custom,          # ¿tiene permisos personalizados?
        "role_default_map": role_default_map,
        "error": error,
    }


@router.get("/users", response_class=HTMLResponse)
async def list_users(request: Request, db: Session = Depends(get_db), current_user=Depends(_guard)):
    users = db.query(models.User).order_by(models.User.name).all()
    return templates.TemplateResponse(request, "admin/users.html", {
        "user": current_user, "users": users,
        "has_custom_permissions": has_custom_permissions,
    })


@router.get("/users/new", response_class=HTMLResponse)
async def new_user_form(request: Request, current_user=Depends(_guard)):
    return templates.TemplateResponse(request, "admin/user_form.html", _form_ctx(current_user))


@router.post("/users/new")
async def create_user(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    role: str = Form(...),
    plant: str = Form(...),
    perm_mode: str = Form("default"),
    perms: list[str] = Form([]),
    db: Session = Depends(get_db),
    current_user=Depends(_guard),
):
    def _err(msg):
        ctx = _form_ctx(current_user, error=msg)
        return templates.TemplateResponse(request, "admin/user_form.html", ctx)

    if role not in ROLES:
        return _err(f"Rol inválido: '{role}'.")
    if plant not in PLANTS:
        return _err(f"Planta inválida: '{plant}'.")
    if db.query(models.User).filter(models.User.email == email).first():
        return _err("El email ya está registrado")

    new_user = models.User(
        name=name, email=email, hashed_password=hash_password(password),
        role=role, plant=plant,
        permissions=_parse_permissions(perm_mode, perms),
    )
    db.add(new_user)
    db.commit()
    return RedirectResponse(url="/admin/users?ok=Usuario+creado", status_code=303)


@router.get("/users/{user_id}/edit", response_class=HTMLResponse)
async def edit_user_form(user_id: int, request: Request, db: Session = Depends(get_db), current_user=Depends(_guard)):
    u = db.query(models.User).filter(models.User.id == user_id).first()
    if not u:
        return RedirectResponse(url="/admin/users?err=Usuario+no+encontrado", status_code=303)
    return templates.TemplateResponse(request, "admin/user_form.html", _form_ctx(current_user, edit_user=u))


@router.post("/users/{user_id}/edit")
async def update_user(
    user_id: int,
    request: Request,
    name: str = Form(...),
    role: str = Form(...),
    plant: str = Form(...),
    password: str = Form(""),
    perm_mode: str = Form("default"),
    perms: list[str] = Form([]),
    db: Session = Depends(get_db),
    current_user=Depends(_guard),
):
    u = db.query(models.User).filter(models.User.id == user_id).first()
    if not u:
        return RedirectResponse(url="/admin/users?err=Usuario+no+encontrado", status_code=303)

    def _err(msg):
        return templates.TemplateResponse(request, "admin/user_form.html",
                                          _form_ctx(current_user, edit_user=u, error=msg))

    if role not in ROLES:
        return _err(f"Rol inválido: '{role}'.")
    if plant not in PLANTS:
        return _err(f"Planta inválida: '{plant}'.")

    u.name = name
    u.role = role
    u.plant = plant
    if password.strip():
        u.hashed_password = hash_password(password.strip())
    # superadmin nunca se restringe: se fuerza a defaults (= todo).
    u.permissions = None if role == "superadmin" else _parse_permissions(perm_mode, perms)
    db.commit()
    return RedirectResponse(url="/admin/users?ok=Cambios+guardados", status_code=303)


@router.post("/users/{user_id}/toggle")
async def toggle_user(user_id: int, db: Session = Depends(get_db), current_user=Depends(_guard)):
    u = db.query(models.User).filter(models.User.id == user_id).first()
    u.active = not u.active
    db.commit()
    return RedirectResponse(url="/admin/users?ok=Estado+del+usuario+actualizado", status_code=303)
