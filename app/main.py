import os
import subprocess
from datetime import timezone, timedelta
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from app.routers import auth, dashboard, purchases, suppliers, documents, users

app = FastAPI(title="MTR Compras")

templates = Jinja2Templates(directory="templates")

# ── Jinja2 filter: UTC → Buenos Aires (UTC-3, sin DST) ─────────────────────
_BAS_OFFSET = timedelta(hours=-3)

def _fmt_ar(dt, fmt="%d/%m/%Y %H:%M"):
    """Convierte datetime UTC naive a hora de Buenos Aires y formatea."""
    if dt is None:
        return ""
    ba = (dt.replace(tzinfo=timezone.utc) + _BAS_OFFSET)
    return ba.strftime(fmt)

templates.env.filters["fmt_ar"] = _fmt_ar

app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(purchases.router)
app.include_router(suppliers.router)
app.include_router(documents.router)
app.include_router(users.router)

@app.get("/")
async def root():
    return RedirectResponse(url="/dashboard")

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/api/debug")
async def debug():
    try:
        git_sha = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"]).decode().strip()
    except Exception:
        git_sha = "unknown"
    from app.database import SessionLocal
    from app import models
    db = SessionLocal()
    counts = {
        "users": db.query(models.User).count(),
        "suppliers": db.query(models.Supplier).count(),
        "purchases": db.query(models.Purchase).count(),
        "documents": db.query(models.Document).count(),
    }
    db.close()
    return {"git_sha": git_sha, "counts": counts}
