"""
Import histórico de cargas de combustible desde el Google Sheet exportado.
Uso: python3 import_fuel_excel.py [--path /ruta/al/archivo.xlsx] [--dry-run]

Columnas esperadas en el Excel:
  Marca temporal | Fecha de Carga | Nombre del Responsable | Patente |
  Tipo de Combustible | Litros | Estación de Servicio | Monto |
  Comprobante | Empresa | Número de Orden de Carga
"""
import sys
import os
import re
import argparse
from datetime import datetime
from decimal import Decimal, InvalidOperation

# ── Agrega el path del proyecto al sys.path ───────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import openpyxl
from app.database import SessionLocal, engine
from app import models

models.Base.metadata.create_all(bind=engine)

# ── Config ────────────────────────────────────────────────────────────────────

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
    "mtr sa":  "MTR SA",
    "mtr":     "MTR SA",
    "ingee":   "INGEE",
    "ingée":   "INGEE",
}

DEFAULT_EXCEL = os.path.join(
    os.path.expanduser("~"), "Downloads",
    "MTR - REGISTRO DE CARGA DE COMBUSTIBLE  (1).xlsx",
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _infer_vehicle_type(plate: str) -> str:
    p = plate.strip().upper()
    if p in ("BIDÓN", "BIDON", "BIDO", "BIDON 200L"):
        return "bidon"
    if re.match(r'^[A-Z]{2,3}\d{3}[A-Z]{0,2}$', p):
        return "vehiculo"
    return "equipo"


def _parse_decimal(val) -> Decimal | None:
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


def _parse_date(val) -> datetime | None:
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


def _normalize(val) -> str:
    return str(val).strip().lower() if val else ""


def _map_fuel_type(val) -> str:
    key = _normalize(val)
    for pattern, code in FUEL_TYPE_MAP.items():
        if pattern in key:
            return code
    return "gasoil_premium"  # default seguro


def _map_company(val) -> str:
    key = _normalize(val)
    for pattern, name in COMPANY_MAP.items():
        if pattern in key:
            return name
    return "MTR SA"  # default


def _parse_order_number(val) -> str | None:
    if val is None:
        return None
    s = str(val).strip()
    if not s or s == "0":
        return None
    # Extraer solo dígitos si viene como "1234.0"
    try:
        n = int(float(s))
        return str(n) if n > 0 else None
    except (ValueError, OverflowError):
        return s or None


# ── Import principal ──────────────────────────────────────────────────────────

def import_fuel(path: str, dry_run: bool = False):
    print(f"📂 Leyendo: {path}")
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    # Header row
    header = [str(c).strip() if c else "" for c in rows[0]]
    print(f"   Columnas: {header}")
    data_rows = rows[1:]
    print(f"   Filas de datos: {len(data_rows)}")

    # Mapeo de columnas por nombre (robusto a cambios de orden)
    col = {h.lower(): i for i, h in enumerate(header)}

    def get(row, name_fragment):
        """Encuentra columna por substring del nombre."""
        for k, i in col.items():
            if name_fragment in k:
                return row[i] if i < len(row) else None
        return None

    db = SessionLocal()

    # Busca el usuario "sistema" o el primer superadmin para asignar entered_by
    sys_user = (
        db.query(models.User).filter(models.User.role == "superadmin").first()
        or db.query(models.User).first()
    )
    if not sys_user:
        print("❌ No hay usuarios en la DB. Creá un admin primero.")
        db.close()
        return

    print(f"   entered_by: {sys_user.name} (id={sys_user.id})")

    inserted = skipped = errors = 0

    for i, row in enumerate(data_rows, start=2):
        if all(v is None or str(v).strip() == "" for v in row):
            continue  # fila vacía

        try:
            # ── Fecha ──
            raw_date = get(row, "fecha de carga") or get(row, "fecha")
            fuel_dt = _parse_date(raw_date)
            if not fuel_dt:
                print(f"   ⚠  fila {i}: fecha inválida '{raw_date}' — skip")
                skipped += 1
                continue

            # ── Patente ──
            plate = str(get(row, "patente") or "").strip().upper()
            if not plate:
                print(f"   ⚠  fila {i}: patente vacía — skip")
                skipped += 1
                continue

            # ── Litros ──
            liters = _parse_decimal(get(row, "litros"))
            if not liters:
                print(f"   ⚠  fila {i}: litros inválidos '{get(row, 'litros')}' — skip")
                skipped += 1
                continue
            if liters > 5000:
                print(f"   ⚠  fila {i}: litros sospechosos {liters} (>5000) plate={plate} — skip (probable error de carga)")
                skipped += 1
                continue

            # ── Resto ──
            fuel_type        = _map_fuel_type(get(row, "tipo de combustible") or get(row, "tipo"))
            company          = _map_company(get(row, "empresa"))
            amount           = _parse_decimal(get(row, "monto"))
            station          = str(get(row, "estaci") or "Hipolito").strip() or "Hipolito"
            responsible_text = str(get(row, "responsable") or get(row, "nombre") or "").strip()
            order_number     = _parse_order_number(get(row, "orden"))

            # Marca temporal → created_at si disponible
            raw_ts   = get(row, "marca temporal") or get(row, "timestamp")
            created  = _parse_date(raw_ts) or fuel_dt

            # Duplicado: misma patente + misma fecha (solo aviso, no skip)
            existing = db.query(models.FuelLoad).filter(
                models.FuelLoad.vehicle_plate == plate,
                models.FuelLoad.fuel_date    == fuel_dt,
                models.FuelLoad.deleted_at.is_(None),
            ).first()
            if existing:
                print(f"   ⚠  fila {i}: duplicado {plate} {fuel_dt.date()} — skip")
                skipped += 1
                continue

            load = models.FuelLoad(
                fuel_date        = fuel_dt,
                responsible_text = responsible_text or "Importado",
                entered_by_id    = sys_user.id,
                vehicle_plate    = plate,
                vehicle_type     = _infer_vehicle_type(plate),
                fuel_type        = fuel_type,
                liters           = liters,
                station          = station,
                amount           = amount,
                company          = company,
                order_number     = order_number,
                created_at       = created,
                updated_at       = created,
            )

            if not dry_run:
                db.add(load)
            inserted += 1

        except Exception as e:
            print(f"   ❌ fila {i}: excepción — {e}")
            errors += 1

    if not dry_run:
        db.commit()
        print(f"\n✅ Importados: {inserted}  |  Omitidos: {skipped}  |  Errores: {errors}")
    else:
        print(f"\n🔍 DRY RUN — se insertarían: {inserted}  |  Omitidos: {skipped}  |  Errores: {errors}")

    db.close()


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import fuel Excel data")
    parser.add_argument("--path",    default=DEFAULT_EXCEL, help="Ruta al archivo .xlsx")
    parser.add_argument("--dry-run", action="store_true", help="Solo leer, no insertar")
    args = parser.parse_args()

    if not os.path.exists(args.path):
        print(f"❌ Archivo no encontrado: {args.path}")
        sys.exit(1)

    import_fuel(args.path, dry_run=args.dry_run)
