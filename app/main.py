import os
import subprocess
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, JSONResponse
from app.routers import auth, dashboard, purchases, suppliers, documents, users, quotes

app = FastAPI(title="MTR Compras")

app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(purchases.router)
app.include_router(suppliers.router)
app.include_router(documents.router)
app.include_router(users.router)
app.include_router(quotes.router)

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
        "quotes": db.query(models.Quote).count(),
    }
    db.close()
    return {"git_sha": git_sha, "counts": counts}
