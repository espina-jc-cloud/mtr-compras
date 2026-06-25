import os
import sys
import subprocess
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse
from app.routers import auth, dashboard, purchases, suppliers, documents, users, quotes, equipment, maintenance, fuel, invoices
from app.routers import operations
from app.routers import operations_live
from app.routers import daily_operations
from app.routers import despachos
from app.routers import tariffs
from app.routers import projects
from app.routers import transporte
from app.routers import finanzas
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


# ── Redirigir a /login cuando el browser pide HTML y no hay sesión ─────────────
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    # 401 desde el browser (acepta HTML) → redirect a login
    if exc.status_code == 401:
        accepts_html = "text/html" in request.headers.get("accept", "")
        if accepts_html:
            return RedirectResponse(url="/login", status_code=302)
    # Resto: devolver JSON normal
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


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
app.include_router(invoices.router)
app.include_router(projects.router)
app.include_router(transporte.router)
# Live DEBE registrarse antes que operations para que /operations/live
# no sea capturado por /operations/{op_id} (que intenta parsear "live" como int).
app.include_router(operations_live.router)
app.include_router(daily_operations.router)
app.include_router(operations.router)
app.include_router(despachos.router)
app.include_router(tariffs.router)
app.include_router(finanzas.router)
app.include_router(operations.api_router)


@app.on_event("startup")
async def run_db_migrations_on_startup():
    try:
        import migrate
        migrate.run()
    except Exception as e:
        print(f"[startup migrate] ERROR: {e}")

@app.get("/")
async def root():
    return RedirectResponse(url="/home")


@app.get("/debug/invoices-check")
async def debug_invoices_check():
    from app.database import SessionLocal
    from app import models
    import traceback

    db = SessionLocal()
    try:
        return {
            "ok": True,
            "invoice_count": db.query(models.Invoice).count(),
            "remito_count": db.query(models.Document).filter(models.Document.doc_type == "remito").count(),
        }
    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "traceback": traceback.format_exc(),
        }
    finally:
        db.close()

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
