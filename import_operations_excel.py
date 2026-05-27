#!/usr/bin/env python3
"""
Importador de Operativos portuarios desde Excel.

Columnas del Excel (0-indexed):
  col[0]  = Nombre del Barco  (header row) / ship name (operation rows)
  col[1]  = Cantidad de viajes (declared)
  col[2]  = código (trip code, integer)
  col[3]  = Fecha E (entry date)
  col[4]  = H Ent (entry time)
  col[5]  = Fecha Sal (exit date)
  col[6]  = H Sal (exit time)
  col[7]  = Patente
  col[8]  = Tara
  col[9]  = Bruto
  col[10] = Neto
  col[11] = Origen
  col[12] = Diferencia
  col[13] = Cliente
  col[14] = Producto

Uso:
  python import_operations_excel.py            # dry-run
  python import_operations_excel.py --commit   # importa a la DB
"""
import sys
import os
import argparse
from datetime import datetime, timedelta
from collections import Counter

sys.path.insert(0, os.path.dirname(__file__))
from dotenv import load_dotenv
load_dotenv()
from app.database import SessionLocal
from app import models

try:
    import openpyxl
except ImportError:
    print("ERROR: openpyxl no instalado. Ejecutar: pip install openpyxl")
    sys.exit(1)

EXCEL_PATH  = "/Users/juancruzespina/Downloads/Operativos barcos.xlsx"
SOURCE_FILE = "Operativos barcos.xlsx"

SHIP_ALIASES = {
    "MV ARGENMAR MISTRAL":  "ARGENMAR MISTRAL",
    "M/V ARGENMAR MISTRAL": "ARGENMAR MISTRAL",
}

SPECIAL_NAMES = {"INGRESO SOP", "ZONA FRANCA", "DEVOLUCIÓN BUNGE", "DEVOLUCIÓN BUNGE "}

PRODUCT_FIX = {
    "UREA GRANULA": "UREA GRANULADA",
}

# Header row marker
HEADER_MARKER = "Nombre del Barco"


def normalize_ship(name: str) -> str:
    s = name.strip()
    return SHIP_ALIASES.get(s, s)


def fix_product(val) -> str | None:
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    return PRODUCT_FIX.get(s, s)


def parse_time_str(val) -> str | None:
    """Convert openpyxl time cell to 'HH:MM:SS' string."""
    if val is None:
        return None
    import datetime as dt
    if isinstance(val, str):
        return val.strip() if val.strip() else None
    if isinstance(val, dt.time):
        return val.strftime("%H:%M:%S")
    if isinstance(val, dt.timedelta):
        total_sec = int(val.total_seconds())
        h = total_sec // 3600
        m = (total_sec % 3600) // 60
        s = total_sec % 60
        return f"{h:02d}:{m:02d}:{s:02d}"
    return str(val).strip() or None


def parse_date(date_val) -> datetime | None:
    """Convert openpyxl date cell to datetime (date only, time zeroed)."""
    if date_val is None:
        return None
    import datetime as dt
    if isinstance(date_val, dt.datetime):
        return date_val.replace(hour=0, minute=0, second=0, microsecond=0)
    if isinstance(date_val, dt.date):
        return datetime(date_val.year, date_val.month, date_val.day)
    return None


def combine_datetime(date_val, time_str: str | None) -> datetime | None:
    """Combine date cell and time string into datetime."""
    base = parse_date(date_val)
    if base is None:
        return None
    if time_str:
        parts = time_str.split(":")
        try:
            h = int(parts[0])
            m = int(parts[1]) if len(parts) > 1 else 0
            s = int(parts[2]) if len(parts) > 2 else 0
            return base.replace(hour=h % 24, minute=m, second=s)
        except (ValueError, IndexError):
            pass
    return base


def shift_from_time(time_str: str | None) -> int | None:
    """Return shift 1-4 based on hour in time_str."""
    if not time_str:
        return None
    try:
        h = int(time_str.split(":")[0])
    except (ValueError, IndexError):
        return None
    if 0 <= h <= 5:
        return 1
    elif 6 <= h <= 11:
        return 2
    elif 12 <= h <= 17:
        return 3
    else:
        return 4


def compute_duration_min(entry_dt: datetime | None, exit_dt: datetime | None) -> float | None:
    """Compute duration in minutes, handling midnight crossing."""
    if entry_dt is None or exit_dt is None:
        return None
    diff = exit_dt - entry_dt
    if diff.total_seconds() < 0:
        diff = diff + timedelta(days=1)
    minutes = diff.total_seconds() / 60
    if minutes < 0 or minutes > 1440:
        return None
    return round(minutes, 2)


def most_common(lst):
    filtered = [x for x in lst if x is not None]
    if not filtered:
        return None
    return Counter(filtered).most_common(1)[0][0]


def safe_int(v) -> int | None:
    if v is None:
        return None
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return None


def is_trip_row(row) -> bool:
    """True if col[2] is a positive integer (trip code)."""
    val = row[2] if len(row) > 2 else None
    if val is None:
        return False
    if isinstance(val, (int, float)):
        return float(val) == int(float(val)) and int(float(val)) > 0
    if isinstance(val, str):
        try:
            n = int(val.strip())
            return n > 0
        except ValueError:
            return False
    return False


def is_operation_header(row) -> bool:
    """True if col[0] is a non-empty string that is a ship name (not the Excel header)."""
    name_cell = row[0] if len(row) > 0 else None
    if name_cell is None:
        return False
    s = str(name_cell).strip()
    if not s:
        return False
    if HEADER_MARKER in s:
        return False
    # Must not be a trip row
    if is_trip_row(row):
        return False
    return True


def main():
    parser = argparse.ArgumentParser(description="Importar Operativos barcos.xlsx")
    parser.add_argument("--commit",  action="store_true", help="Guardar en DB (default: dry-run)")
    parser.add_argument("--dry-run", action="store_true", help="Solo mostrar (default)")
    args = parser.parse_args()
    commit = args.commit

    print(f"{'[COMMIT]' if commit else '[DRY-RUN]'} Leyendo {EXCEL_PATH}")

    wb = openpyxl.load_workbook(EXCEL_PATH, read_only=True, data_only=True)
    ws = wb.active
    all_rows = list(ws.iter_rows(values_only=True))
    wb.close()

    print(f"  Filas totales en Excel: {len(all_rows)}")

    # ── Parse pass ──────────────────────────────────────────────────────────
    operations_data = []
    current_op      = None
    ops_detected    = 0
    trip_rows_found = 0
    warnings        = []

    for row_idx, row in enumerate(all_rows):
        row = list(row)
        while len(row) < 15:
            row.append(None)

        # Skip completely empty rows
        if all(v is None or (isinstance(v, str) and not v.strip()) for v in row):
            continue

        # Skip the Excel column-header row
        if row[0] is not None and HEADER_MARKER in str(row[0]):
            continue

        if is_trip_row(row):
            if current_op is None:
                warnings.append(f"Fila {row_idx+1}: trip sin operación padre — omitida")
                continue

            trip_code      = int(float(row[2]))
            entry_time_str = parse_time_str(row[4])
            exit_time_str  = parse_time_str(row[6])
            entry_dt       = combine_datetime(row[3], entry_time_str)
            exit_dt        = combine_datetime(row[5], exit_time_str)
            duration       = compute_duration_min(entry_dt, exit_dt)

            trip = {
                "trip_code":    trip_code,
                "entry_date":   entry_dt,
                "entry_time":   entry_time_str,
                "exit_date":    exit_dt,
                "exit_time":    exit_time_str,
                "plate":        str(row[7]).strip() if row[7] is not None else None,
                "tara_kg":      safe_int(row[8]),
                "bruto_kg":     safe_int(row[9]),
                "neto_kg":      safe_int(row[10]),
                "origen_kg":    safe_int(row[11]),
                "diff_kg":      safe_int(row[12]),
                "client":       str(row[13]).strip() if row[13] is not None else None,
                "product":      fix_product(row[14]),
                "shift_number": shift_from_time(entry_time_str),
                "duration_min": duration,
            }
            current_op["trips"].append(trip)
            trip_rows_found += 1

        elif is_operation_header(row):
            raw_name  = str(row[0]).strip()
            ship_name = normalize_ship(raw_name)
            op_type   = "special" if ship_name.rstrip() in SPECIAL_NAMES else "vessel"

            declared = safe_int(row[1])
            current_op = {
                "raw_name":       raw_name,
                "ship_name":      ship_name,
                "operation_type": op_type,
                "declared_trips": declared,
                "trips":          [],
            }
            operations_data.append(current_op)
            ops_detected += 1
        # else: subtotal / empty row → skip

    print(f"  Operaciones detectadas: {ops_detected}")
    print(f"  Filas de viajes encontradas: {trip_rows_found}")
    special_count = sum(1 for o in operations_data if o["operation_type"] == "special")
    vessel_count  = ops_detected - special_count
    print(f"  Especiales: {special_count}  Barcos: {vessel_count}")

    if warnings:
        print(f"  Avisos ({len(warnings)}):")
        for w in warnings[:10]:
            print(f"    {w}")

    # Show sample in dry-run
    if not commit:
        for op in operations_data[:5]:
            trips = op["trips"]
            print(f"  Oper: {op['ship_name']!r} ({op['operation_type']}) decl={op['declared_trips']} real={len(trips)} viajes")
        specials = [o for o in operations_data if o["operation_type"] == "special"]
        print(f"  Especiales detectados: {[o['ship_name'] for o in specials]}")
        print("\n[DRY-RUN] No se guardó nada. Usar --commit para importar.")
        return

    # ── Insert pass ──────────────────────────────────────────────────────────
    db = SessionLocal()
    try:
        trips_imported    = 0
        trips_skipped_dup = 0

        for op_data in operations_data:
            trips = op_data["trips"]

            neto_list   = [t["neto_kg"]   for t in trips if t["neto_kg"]   is not None]
            origen_list = [t["origen_kg"] for t in trips if t["origen_kg"] is not None]
            diff_list   = [t["diff_kg"]   for t in trips if t["diff_kg"]   is not None]
            dur_list    = [t["duration_min"] for t in trips
                           if t["duration_min"] is not None and t["duration_min"] <= 240]

            total_neto   = sum(neto_list)
            total_origen = sum(origen_list)
            total_diff   = sum(diff_list)
            actual_trips = len(trips)

            avg_dur           = round(sum(dur_list) / len(dur_list), 2) if dur_list else None
            avg_tons_per_trip = round(total_neto / 1000 / actual_trips, 3) if actual_trips > 0 else None

            entry_dates = [t["entry_date"] for t in trips if t["entry_date"] is not None]
            start_date  = min(entry_dates) if entry_dates else None
            end_date    = max(entry_dates) if entry_dates else None

            if start_date and end_date and total_neto > 0:
                op_hours          = max((end_date - start_date).total_seconds() / 3600, 1.0)
                avg_tons_per_hour = round(total_neto / 1000 / op_hours, 3)
            else:
                avg_tons_per_hour = None

            client  = most_common([t["client"]  for t in trips])
            product = most_common([t["product"] for t in trips])

            op = models.Operation(
                raw_name          = op_data["raw_name"],
                ship_name         = op_data["ship_name"],
                operation_type    = op_data["operation_type"],
                client            = client,
                product           = product,
                start_date        = start_date,
                end_date          = end_date,
                declared_trips    = op_data.get("declared_trips"),
                actual_trips      = actual_trips,
                total_neto_kg     = total_neto,
                total_origen_kg   = total_origen,
                total_diff_kg     = total_diff,
                avg_duration_min  = avg_dur,
                avg_tons_per_trip = avg_tons_per_trip,
                avg_tons_per_hour = avg_tons_per_hour,
                source_file       = SOURCE_FILE,
            )
            db.add(op)
            db.flush()

            for t in trips:
                existing = db.query(models.OperationTrip).filter(
                    models.OperationTrip.trip_code == t["trip_code"]
                ).first()
                if existing:
                    trips_skipped_dup += 1
                    continue

                trip_obj = models.OperationTrip(
                    operation_id  = op.id,
                    trip_code     = t["trip_code"],
                    entry_date    = t["entry_date"],
                    entry_time    = t["entry_time"],
                    exit_date     = t["exit_date"],
                    exit_time     = t["exit_time"],
                    plate         = t["plate"],
                    tara_kg       = t["tara_kg"],
                    bruto_kg      = t["bruto_kg"],
                    neto_kg       = t["neto_kg"],
                    origen_kg     = t["origen_kg"],
                    diff_kg       = t["diff_kg"],
                    shift_number  = t["shift_number"],
                    duration_min  = t["duration_min"],
                    client        = t["client"],
                    product       = t["product"],
                )
                db.add(trip_obj)
                trips_imported += 1

        db.commit()
        print(f"\n[COMMIT] OK")
        print(f"  Operaciones importadas:        {len(operations_data)}")
        print(f"  Viajes importados:             {trips_imported}")
        print(f"  Viajes duplicados omitidos:    {trips_skipped_dup}")
        if warnings:
            print(f"  Avisos:                        {len(warnings)}")

    except Exception as e:
        db.rollback()
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
