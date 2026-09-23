import os
import sys
import subprocess
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from app.routers import auth, dashboard, purchases, suppliers, documents, users, quotes, equipment, maintenance, fuel, invoices
from app.routers import buques
from app.routers import fuel_invoices
from app.routers import operations
from app.routers import operations_live
from app.routers import daily_operations
from app.routers import despachos
from app.routers import tariffs
from app.routers import projects
from app.routers import transporte
from app.routers import arribos
from app.routers import search
from app.routers import carga_publica
from app.routers import servicios
from app.routers import polinomica
from app.routers import asistencia
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

# Archivos estáticos (logo, etc.) — servidos en /static.
if os.path.isdir("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")


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
app.include_router(search.router)
app.include_router(carga_publica.router)   # /carga — público, sin login
app.include_router(servicios.router)
app.include_router(dashboard.router)
app.include_router(purchases.router)
app.include_router(suppliers.router)
app.include_router(documents.router)
app.include_router(users.router)
app.include_router(quotes.router)
app.include_router(equipment.router)
app.include_router(maintenance.router)
app.include_router(fuel_invoices.router)
app.include_router(fuel.router)
app.include_router(invoices.router)
app.include_router(projects.router)
app.include_router(transporte.router)
# Live DEBE registrarse antes que operations para que /operations/live
# no sea capturado por /operations/{op_id} (que intenta parsear "live" como int).
app.include_router(operations_live.router)
app.include_router(daily_operations.router)
app.include_router(buques.router)   # /operations/buques — antes que operations (/{op_id})
app.include_router(arribos.router)   # /operations/arribos — antes que operations (/{op_id})
app.include_router(operations.router)
app.include_router(despachos.router)
app.include_router(tariffs.router)
app.include_router(polinomica.router)
app.include_router(asistencia.router)
app.include_router(operations.api_router)


def _una_pasada_del_buzon() -> str:
    """Una revisión del buzón. Corre en un hilo — no puede tocar el event loop."""
    from app.database import SessionLocal
    from app import asistencia_mail

    db = SessionLocal()
    try:
        regs = asistencia_mail.procesar_buzon(db, origen="buzon")
        if not regs:
            return "sin novedades"
        return " · ".join(
            f"{r.estado}: {r.dias_aplicados} días, {r.filas_aplicadas} filas"
            + (f", {r.dias_pendientes} para revisar" if r.dias_pendientes else "")
            + (f" — {r.error}" if r.error else "")
            for r in regs)
    finally:
        db.close()


# ── Buzón de planillas: revisión periódica ────────────────────────────────────
# La planilla llega todas las mañanas por mail. En vez de un cron externo se usa
# una tarea del propio proceso: no agrega dependencias ni otro servicio a
# Railway, y si el proceso se cae, al reiniciar vuelve sola.
# Se desactiva con ASISTENCIA_MAIL_INTERVALO_MIN=0.
async def _revisar_buzon_periodicamente():
    import asyncio
    from app.database import SessionLocal

    intervalo = int(os.getenv("ASISTENCIA_MAIL_INTERVALO_MIN", "20") or 0)
    if intervalo <= 0:
        return
    await asyncio.sleep(60)   # no competir con migrate.py en el arranque

    while True:
        try:
            from app import asistencia_mail
            if asistencia_mail.configurado():
                # En un hilo: IMAP es bloqueante y acá adentro frenaría el loop
                # de asyncio, o sea toda la app, mientras dura la descarga.
                resumen = await asyncio.to_thread(_una_pasada_del_buzon)
                # flush explícito: la salida de Railway está bufferada y sin
                # esto no se ve una línea hasta que se llenan 8 KB.
                print(f"[buzon] {resumen}", flush=True)
        except Exception as e:
            print(f"[buzon] ERROR inesperado: {type(e).__name__}: {e}", flush=True)
        await asyncio.sleep(intervalo * 60)


def _una_pasada_de_arribos() -> str:
    """Una revisión del correo de arribos. Corre en un hilo, como la de horas."""
    from app.database import SessionLocal
    from app import arribos_sync

    db = SessionLocal()
    try:
        from app import balanza_sync

        r = arribos_sync.sincronizar(db)
        nom, lu = r["nominaciones"], r["lineup"]
        if not r["ok"]:
            return f"ERROR: {r['error']}"
        altas = len(nom.get("altas", []))
        tocados = len(lu.get("tocados", []))
        # Los resúmenes de buque llegan a la misma casilla y no tiene sentido
        # abrirla dos veces: se revisan en la misma pasada.
        b = balanza_sync.sincronizar(db)
        cerrados = len(b.get("altas", [])) if b["ok"] else 0
        actualizados = len(b.get("reemplazos", [])) if b["ok"] else 0
        partes = []
        if altas:
            partes.append(f"{altas} alta(s) por nominación")
        if tocados:
            partes.append(f"{tocados} actualizado(s) por el line-up {lu.get('fecha') or ''}")
        if cerrados:
            partes.append(f"{cerrados} resumen(es) de buque nuevo(s)")
        if actualizados:
            partes.append(f"{actualizados} resumen(es) actualizado(s)")
        if not b["ok"]:
            partes.append(f"balanza: {b['error']}")
        return " · ".join(partes) or "sin novedades"
    finally:
        db.close()


# ── Correo de Próximos Arribos: revisión periódica ───────────────────────────
# Las nominaciones y los line-up llegan de a poco a lo largo del día, así que
# no hace falta la frecuencia del buzón de horas. Una vez por hora alcanza.
# Se desactiva con ARRIBOS_MAIL_INTERVALO_MIN=0.
async def _revisar_arribos_periodicamente():
    import asyncio

    intervalo = int(os.getenv("ARRIBOS_MAIL_INTERVALO_MIN", "60") or 0)
    if intervalo <= 0:
        return
    await asyncio.sleep(120)   # después de migrate.py y del buzón de horas

    while True:
        try:
            from app import asistencia_mail          # misma casilla, misma config
            if asistencia_mail.configurado():
                resumen = await asyncio.to_thread(_una_pasada_de_arribos)
                print(f"[arribos] {resumen}", flush=True)
        except Exception as e:
            print(f"[arribos] ERROR inesperado: {type(e).__name__}: {e}", flush=True)
        await asyncio.sleep(intervalo * 60)


@app.on_event("startup")
async def start_arribos_task():
    import asyncio
    asyncio.create_task(_revisar_arribos_periodicamente())


@app.on_event("startup")
async def start_buzon_task():
    import asyncio
    asyncio.create_task(_revisar_buzon_periodicamente())


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
