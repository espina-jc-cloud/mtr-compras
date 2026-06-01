import os
import sys
import subprocess
from fastapi import FastAPI, Request, Depends
from fastapi.responses import RedirectResponse, JSONResponse
from app.routers import auth, dashboard, purchases, suppliers, documents, users, quotes, equipment, maintenance, fuel
from app.routers import operations
from app.routers import operations_live
from app.deps import require_role

# ── Startup security check ─────────────────────────────────────────────────────
_DB_URL   = os.getenv("DATABASE_URL", "")
_SK       = os.getenv("SECRET_KEY", "")
_INSECURE = "dev-secret-key-CHANGE-IN-PRODUCTION-insecure"
if _DB_URL and not _DB_URL.startswith("sqlite") and (not _SK or _SK == _INSECURE):
    sys.stderr.write(
        "FATAL: SECRET_KEY insegura en entorno de producción. "
        "Configurá SECRET_KEY antes de iniciar.\n"
    )
    sys.exit(1)

app = FastAPI(title="MTR Gestión")

app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(purchases.router)
app.include_router(suppliers.router)
app.include_router(documents.router)
app.include_router(users.router)
app.include_router(quotes.router)
app.include_router(equipment.router)
app.include_router(maintenance.router)
app.include_router(fuel.router)
# Live DEBE registrarse antes que operations para que /operations/live
# no sea capturado por /operations/{op_id} (que intenta parsear "live" como int).
app.include_router(operations_live.router)
app.include_router(operations.router)
app.include_router(operations.api_router)

@app.get("/")
async def root():
    return RedirectResponse(url="/dashboard")

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/api/debug")
async def debug(current_user=Depends(require_role("admin", "superadmin"))):
    try:
        git_sha = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"]).decode().strip()
    except Exception:
        git_sha = "unknown"
    from app.database import SessionLocal
    from app import models
    db = SessionLocal()
    counts = {
        "users":               db.query(models.User).count(),
        "suppliers":           db.query(models.Supplier).count(),
        "purchases":           db.query(models.Purchase).count(),
        "documents":           db.query(models.Document).count(),
        "quotes":              db.query(models.Quote).count(),
        "equipment":           db.query(models.Equipment).count(),
        "maintenance_records": db.query(models.MaintenanceRecord).count(),
        "fuel_loads":          db.query(models.FuelLoad).count(),
        "operations":          db.query(models.Operation).count(),
        "operation_trips":     db.query(models.OperationTrip).count(),
    }
    db.close()
    return {"git_sha": git_sha, "counts": counts}
