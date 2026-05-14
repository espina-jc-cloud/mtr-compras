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
