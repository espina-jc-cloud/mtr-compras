from fastapi import Request, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from app.database import get_db
from app.auth import decode_token, get_token_from_cookie
from app import models

def get_current_user(request: Request, db: Session = Depends(get_db)):
    token = get_token_from_cookie(request)
    if not token:
        raise HTTPException(status_code=401, detail="No autenticado")
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token inválido")
    user = db.query(models.User).filter(models.User.id == payload.get("sub")).first()
    if not user or not user.active:
        raise HTTPException(status_code=401, detail="Usuario no encontrado")
    return user

def get_current_user_optional(request: Request, db: Session = Depends(get_db)):
    token = get_token_from_cookie(request)
    if not token:
        return None
    payload = decode_token(token)
    if not payload:
        return None
    return db.query(models.User).filter(models.User.id == payload.get("sub")).first()

def require_role(*roles):
    def checker(current_user=Depends(get_current_user)):
        if current_user.role not in roles:
            raise HTTPException(status_code=403, detail="Sin permisos")
        return current_user
    return checker


def require_compras_access(current_user=Depends(get_current_user)):
    """Bloquea tecnico y operador de ver módulos de compras/cotizaciones/proveedores."""
    if current_user.role in ("tecnico", "operador"):
        raise HTTPException(status_code=403, detail="Sin acceso al módulo de compras.")
    return current_user


def require_no_operador(current_user=Depends(get_current_user)):
    """Bloquea operador de módulos que no son combustible: mantenimiento, equipos."""
    if current_user.role == "operador":
        raise HTTPException(status_code=403, detail="Sin acceso a este módulo.")
    return current_user


def require_projects_access(current_user=Depends(get_current_user)):
    """Bloquea operador de ver el módulo proyectos."""
    if current_user.role == "operador":
        raise HTTPException(status_code=403, detail="Sin acceso al módulo de proyectos.")
    return current_user
