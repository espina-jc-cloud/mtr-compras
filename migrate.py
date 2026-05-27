#!/usr/bin/env python3
"""
Crea tablas y genera el primer superadmin si no existe.
Se ejecuta automáticamente al iniciar en Railway.

Variables de entorno:
  FIRST_ADMIN_EMAIL     (default: admin@mtr.com)
  FIRST_ADMIN_PASSWORD  (requerida en producción — falla si no está)
"""
import os
import sys
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text
from app.database import engine, Base, DATABASE_URL, SessionLocal
from app import models
from app.auth import hash_password


def _add_column(conn, table, column, col_type):
    """Agrega una columna si no existe. Compatible con SQLite y PostgreSQL.

    IMPORTANTE: el conn.rollback() en el except es obligatorio para PostgreSQL.
    Cuando ADD COLUMN falla (columna ya existe), PostgreSQL pone la conexión en
    estado "aborted transaction" — sin rollback, todos los comandos siguientes
    también fallan, aunque la columna no exista todavía.
    SQLite ignora el rollback sin error, así que es seguro para ambos motores.
    """
    try:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
        conn.commit()
        print(f"  + columna {table}.{column} agregada")
    except Exception:
        conn.rollback()  # ← crítico para PostgreSQL: limpia el estado "aborted"


def run():
    is_prod = not DATABASE_URL.startswith("sqlite")

    Base.metadata.create_all(bind=engine)
    # Las tablas `operations` y `operation_trips` se crean automáticamente aquí.
    print(f"✓ Tablas creadas ({DATABASE_URL.split('@')[-1] if '@' in DATABASE_URL else DATABASE_URL})")

    # ── Migraciones seguras de columnas nuevas ────────────────────────────────
    with engine.connect() as conn:
        _add_column(conn, "purchases",  "purchase_date",  "TIMESTAMP")
        _add_column(conn, "purchases",  "deleted_at",     "TIMESTAMP")
        _add_column(conn, "purchases",  "deleted_reason", "TEXT")
        _add_column(conn, "documents",  "remito_date",    "VARCHAR")
        _add_column(conn, "fuel_loads", "plant",          "VARCHAR")
    print("✓ Columnas nuevas verificadas")

    admin_email = os.getenv("FIRST_ADMIN_EMAIL", "admin@mtr.com")
    admin_password = os.getenv("FIRST_ADMIN_PASSWORD", "")

    if not admin_password:
        if is_prod:
            print("✗ ERROR: FIRST_ADMIN_PASSWORD no está definida. Abortando.", file=sys.stderr)
            sys.exit(1)
        else:
            admin_password = "changeme123"
            print("⚠ Usando contraseña local por defecto: changeme123")

    db = SessionLocal()
    existing = db.query(models.User).filter(models.User.email == admin_email).first()
    if not existing:
        admin = models.User(
            name="Administrador",
            email=admin_email,
            hashed_password=hash_password(admin_password),
            role="superadmin",
            plant="TODAS",
        )
        db.add(admin)
        db.commit()
        print(f"✓ Superadmin creado: {admin_email}")
    else:
        print(f"✓ Superadmin ya existe: {admin_email}")

    # Stats de tablas operativas
    counts = {
        "operations":      db.query(models.Operation).count(),
        "operation_trips": db.query(models.OperationTrip).count(),
    }
    print(f"✓ Operativos: {counts['operations']} operations, {counts['operation_trips']} operation_trips")

    db.close()

    # ── AUTO-IMPORT histórico combustible ─────────────────────────────────────
    # TEMPORAL: se eliminará después del primer deploy exitoso en producción.
    # Corre SOLO si:  prod + fuel_history.xlsx presente + tabla fuel_loads vacía.
    xlsx_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fuel_history.xlsx")
    if is_prod and os.path.exists(xlsx_path):
        _import_fuel_history(xlsx_path)


def _import_fuel_history(xlsx_path: str):
    """Importa el historial de combustible desde el Excel exportado del Google Sheet."""
    import re
    import calendar
    from decimal import Decimal, InvalidOperation
    from datetime import datetime, timedelta

    try:
        import openpyxl
    except ImportError:
        print("✗ openpyxl no instalado — import combustible omitido")
        return

    db = SessionLocal()
    try:
        fuel_count = db.query(models.FuelLoad).count()
        if fuel_count > 0:
            print(f"✓ fuel_loads ya tiene {fuel_count} registros — import omitido")
            return

        # Usuario superadmin para entered_by
        sys_user = db.query(models.User).filter(models.User.role == "superadmin").first() \
                   or db.query(models.User).first()
        if not sys_user:
            print("✗ No hay usuarios — import combustible omitido")
            return

        # Helpers internos
        def _parse_decimal(val):
            if val is None:
                return None
            s = str(val).strip().replace(",", ".")
            if not s or s == "-":
                return None
            try:
                d = Decimal(s)
                return d if d > 0 else None
            except InvalidOperation:
                return None

        def _parse_date(val):
            if isinstance(val, datetime):
                return val.replace(hour=0, minute=0, second=0, microsecond=0)
            if val is None:
                return None
            s = str(val).strip()
            for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%y"):
                try:
                    return datetime.strptime(s, fmt)
                except ValueError:
                    continue
            return None

        FUEL_TYPE_MAP = {
            "gas oil premium": "gasoil_premium",
            "gasoil premium":  "gasoil_premium",
            "gasoil":          "gasoil_premium",
            "gas oil":         "gasoil_premium",
            "nafta premium":   "nafta_premium",
            "nafta":           "nafta",
            "super":           "nafta",
        }
        COMPANY_MAP = {
            "mtr sa": "MTR SA",
            "mtr":    "MTR SA",
            "ingee":  "INGEE",
            "ingée":  "INGEE",
        }

        def _map_fuel(val):
            k = str(val or "").strip().lower()
            for p, c in FUEL_TYPE_MAP.items():
                if p in k:
                    return c
            return "gasoil_premium"

        def _map_company(val):
            k = str(val or "").strip().lower()
            for p, n in COMPANY_MAP.items():
                if p in k:
                    return n
            return "MTR SA"

        def _infer_vtype(plate):
            p = plate.strip().upper()
            if p in ("BIDÓN", "BIDON", "BIDO", "BIDON 200L"):
                return "bidon"
            if re.match(r'^[A-Z]{2,3}\d{3}[A-Z]{0,2}$', p):
                return "vehiculo"
            return "equipo"

        def _order_num(val):
            if val is None:
                return None
            s = str(val).strip()
            if not s or s == "0":
                return None
            try:
                n = int(float(s))
                return str(n) if n > 0 else None
            except (ValueError, OverflowError):
                return s or None

        # Leer Excel
        wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        wb.close()

        header = rows[0]

        def get(row, fragment):
            for j, h in enumerate(header):
                if h and fragment.lower() in str(h).lower():
                    return row[j] if j < len(row) else None
            return None

        inserted = skipped_bad = skipped_empty = 0

        for i, row in enumerate(rows[1:], 2):
            if all(v is None or str(v).strip() == "" for v in row):
                skipped_empty += 1
                continue

            plate = str(get(row, "patente") or "").strip().upper()
            fuel_dt = _parse_date(get(row, "fecha de carga") or get(row, "fecha"))
            liters = _parse_decimal(get(row, "litros"))

            if not plate or not fuel_dt or not liters:
                skipped_bad += 1
                print(f"  ⚠  fila {i}: incompleto (plate={plate!r} date={fuel_dt} liters={liters}) — omitida")
                continue
            if liters > 5000:
                skipped_bad += 1
                print(f"  ⚠  fila {i}: litros={liters} > 5000 (error de carga) — omitida")
                continue

            raw_ts = get(row, "marca temporal") or get(row, "timestamp")
            created = _parse_date(raw_ts) or fuel_dt

            load = models.FuelLoad(
                fuel_date        = fuel_dt,
                responsible_text = str(get(row, "responsable") or get(row, "nombre") or "").strip() or "Importado",
                entered_by_id    = sys_user.id,
                vehicle_plate    = plate,
                vehicle_type     = _infer_vtype(plate),
                fuel_type        = _map_fuel(get(row, "tipo de combustible") or get(row, "tipo")),
                liters           = liters,
                station          = str(get(row, "estaci") or "Hipolito").strip() or "Hipolito",
                amount           = _parse_decimal(get(row, "monto")),
                company          = _map_company(get(row, "empresa")),
                order_number     = _order_num(get(row, "orden")),
                created_at       = created,
                updated_at       = created,
            )
            db.add(load)
            inserted += 1

        db.commit()
        print(
            f"✓ Combustible importado: {inserted} registros · "
            f"{skipped_bad} omitidos (datos inválidos) · "
            f"{skipped_empty} filas vacías ignoradas"
        )

    except Exception as e:
        db.rollback()
        print(f"✗ Error en import combustible: {e}")
    finally:
        db.close()


if __name__ == "__main__":
    run()
