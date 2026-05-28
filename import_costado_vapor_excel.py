#!/usr/bin/env python3
"""
Import Costado Vapor (CV) Excel data into operation_product_totals table.

Diseñado para cargas incrementales: cada archivo Excel se identifica por
--source-file. Reimportar el mismo archivo actualiza sin duplicar.
Importar un archivo nuevo (2026, etc.) agrega registros independientes.

Fórmula:
  cv_excel_tons       = Total desestibado según Excel CV (depot + costado vapor)
  depot_tons          = Toneladas a Depósito MTR (de operation_trips)
  costado_vapor_tons  = max(cv_excel_tons - depot_tons, 0)
  total_discharged    = depot_tons + costado_vapor_tons   # sin doble conteo

Clave de idempotencia: (source_file, raw_ship_name, product, cv_start_date)

Uso:
  python3 import_costado_vapor_excel.py                          # dry-run, 2025
  python3 import_costado_vapor_excel.py --commit                 # commit, 2025
  python3 import_costado_vapor_excel.py \\
      --xlsx ~/Downloads/cv_2026.xlsx \\
      --source-file cv_2026.xlsx --commit                        # importar 2026
"""
import argparse
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime

DATE_WINDOW = 10  # días de tolerancia para match operativo

# ── Normalización de nombres de producto ──────────────────────────────────────

PRODUCT_FIX_CV = {
    "Súoer Fosfato Triple":   "TRIPLE (STP)",
    "Súper Fosfato Triple":   "TRIPLE (STP)",
    "Super Fosfato Triple":   "TRIPLE (STP)",
    "Fosfato Monoamònico":    "MAP",
    "Fosfato Monoamónico":    "MAP",
    "Fosfato Diamónico":      "DAP",
    "Urea Granulada":         "UREA GRANULADA",
    "Cloturo de Potasio":     "CLORURO DE POTASIO",
    "Sulfato de Amonio":      "SULFATO DE AMONIO",
}

# Aliases en operation_trips que equivalen a productos CV
DEPOT_PRODUCT_ALIASES = {
    "MOP":   "CLORURO DE POTASIO",
    "AMSUL": "SULFATO DE AMONIO",
}

# Normalización de nombres de buque: Excel raw → nombre canónico en DB
SHIP_FIX = {
    "M/V Sider Liu":        "SIDER LIU",
    "M/V Arriabbiata":      "ARRABIATA",
    "M/V Ruen":             "MV/RUEN",
    "M/V Argenmar Mistral": "ARGENMAR MISTRAL",
    "M/V Seacon Bangkok":   "SEACON BANGKOK",
    "M/V Nava Ulysses":     "NAVA ULYSSES",
    "M/V Alithia":          "M/V ALITHIA",
    "M/V Genius Star IX":   "GENIUS STAR IX",
    "M/V Amani":            "M/V AMANI",
    "M/V Areti":            "M/V ARETI",
    "M/V CL ZHANGJIAJIE":   "M/V CL ZHANGJIAJIE",
    "M/V Endless Horizon":  "M/V ENDLESS HORIZON",
    "M/V Yasa Moon\xa0":    "YASA MOON",
    "M/V Beatrice":         "M/V BEATRICE",
    "M/V Bonny Island":     "M/V BONNY ISLAND",
    "M/V Sofía":            "M/V SOFIA",
    "M/V Vega":             "M/V VEGA",
    "M/V Ultralaz":         "M/V ULTRALAZ",
    "M/V Tenca Arrow":      "M/V TENCA ARROW",
    "M/V Adasun":           "ADASUN",
    "M/V IC Progress":      "M/V IC PROGRESS",
    "M/V Oborishte":        "OBORISHTE",
    "M/V ATHANASIA":        "ATHANASIA",
    "M/V NIKITIS":          "NIKITIS",
}

# ── Utilidades ────────────────────────────────────────────────────────────────

def norm_key(v):
    """Normaliza nombre de buque: sin tildes, mayúsculas, sin prefijo M/V."""
    if not v:
        return ""
    s = unicodedata.normalize("NFD", str(v).strip())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.upper()
    for pre in ("M/V ", "MV/", "MV "):
        if s.startswith(pre):
            s = s[len(pre):]
    return s.strip()


def fix_product_cv(raw):
    """Normaliza nombre de producto del Excel CV."""
    if raw is None:
        return "(sin producto)"
    s = str(raw).strip()
    return PRODUCT_FIX_CV.get(s, s.upper() if s else "(sin producto)")


def to_dt(val):
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        return datetime.strptime(str(val)[:10], "%Y-%m-%d")
    except Exception:
        return None


# ── Parser Excel ──────────────────────────────────────────────────────────────

def parse_excel(path):
    """
    Parsea Excel CV. Retorna lista de records.

    Estructura esperada:
      - Filas 0-6: cabeceras / título
      - Fila 7+:   datos (barco, inicio, fin, op, cliente, producto, toneladas)
    Escaneo dinámico: para cuando encuentra 3 filas consecutivas vacías.
    """
    try:
        import openpyxl
    except ImportError:
        print("ERROR: openpyxl no instalado. Ejecutar: pip install openpyxl")
        sys.exit(1)

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    all_rows = list(ws.iter_rows(values_only=True))
    wb.close()

    DATA_START = 7          # 0-indexed primera fila de datos
    EMPTY_STOP = 1          # 1 fila vacía → fin de tabla (los datos son contiguos)

    records = []
    last_ship  = None
    last_start = None
    last_end   = None
    consecutive_empty = 0

    for i, row in enumerate(all_rows[DATA_START:], start=DATA_START):
        def col(j, r=row):
            return r[j] if j < len(r) else None

        raw_ship    = col(1)
        raw_start   = col(2)
        raw_end     = col(3)
        raw_client  = col(5)
        raw_product = col(6)
        raw_tons    = col(7)

        # Detectar fila vacía
        if not any([raw_ship, raw_tons]):
            consecutive_empty += 1
            if consecutive_empty >= EMPTY_STOP:
                break
            continue
        consecutive_empty = 0

        # Heredar barco/fechas de fila anterior cuando col[1] está vacío
        if raw_ship and str(raw_ship).strip():
            last_ship  = str(raw_ship).strip()
            last_start = raw_start
            last_end   = raw_end
        else:
            raw_ship  = last_ship
            raw_start = last_start
            raw_end   = last_end

        if not raw_ship:
            continue
        if raw_tons is None:
            continue
        try:
            tons = float(raw_tons)
        except (TypeError, ValueError):
            continue
        if tons <= 0:
            continue

        start_dt = to_dt(raw_start)
        end_dt   = to_dt(raw_end)

        ship_norm = SHIP_FIX.get(raw_ship, raw_ship)

        # Anomalía: typo de año (ej. start=2026-06-27, end=2025-06-28 → start debería ser 2025)
        # Detectado cuando start.year == end.year + 1 y las fechas están cerca (≤ 30 días).
        # Aplica genéricamente para cualquier año, sin hardcodear.
        if start_dt and end_dt and start_dt.year == end_dt.year + 1:
            fixed_start = start_dt.replace(year=end_dt.year)
            if abs((fixed_start - end_dt).days) <= 30:
                print(f"  [fix] Typo de año en '{raw_ship}': "
                      f"{start_dt.date()} → {fixed_start.date()}")
                start_dt = fixed_start

        # Anomalía genérica: si end < start → swap
        if start_dt and end_dt and end_dt < start_dt:
            start_dt, end_dt = end_dt, start_dt
            print(f"  [fix] Fechas invertidas en '{raw_ship}': {start_dt.date()} – {end_dt.date()}")

        product_norm = fix_product_cv(raw_product)
        client_str   = str(raw_client).strip() if raw_client else None

        records.append({
            "raw_ship_name": raw_ship,
            "ship_name":     ship_norm,
            "match_key":     norm_key(ship_norm),
            "client":        client_str,
            "product":       product_norm,
            "cv_start_date": start_dt,
            "cv_end_date":   end_dt,
            "cv_excel_tons": tons,
            "row_idx":       i + 1,  # 1-based para mostrar
        })

    return records


# ── Match operativo ───────────────────────────────────────────────────────────

def load_operations_and_trips(db):
    from app import models
    all_ops   = db.query(models.Operation).all()
    all_trips = db.query(models.OperationTrip).all()

    depot_by_op_prod = defaultdict(lambda: defaultdict(float))
    for t in all_trips:
        if t.neto_kg:
            pn = DEPOT_PRODUCT_ALIASES.get(t.product, t.product) if t.product else "(sin producto)"
            depot_by_op_prod[t.operation_id][pn] += t.neto_kg / 1000

    return all_ops, depot_by_op_prod


def match_operation(cv_rec, all_ops, depot_by_op_prod):
    """
    Matchea un registro CV contra un operativo.
    Retorna (op, status, notes).
    Status: 'matched' | 'unmatched' | 'ambiguous'
    """
    mk       = cv_rec["match_key"]
    start_dt = cv_rec["cv_start_date"]
    prod     = cv_rec["product"]

    candidates = [op for op in all_ops if norm_key(op.ship_name) == mk]
    if not candidates:
        return None, "unmatched", f"No hay operativo para '{cv_rec['ship_name']}'"

    if start_dt:
        windowed = []
        for op in candidates:
            ref = op.start_date or op.end_date
            dist = abs((start_dt - ref).days) if ref else DATE_WINDOW
            if dist <= DATE_WINDOW:
                windowed.append((op, dist))
        if not windowed:
            windowed = [(op, 999) for op in candidates]
    else:
        windowed = [(op, 0) for op in candidates]

    scored = []
    for op, dist in windowed:
        has_prod = 0 if prod in depot_by_op_prod.get(op.id, {}) else 1
        scored.append((has_prod, dist, op))
    scored.sort(key=lambda x: (x[0], x[1]))

    best_score, best_dist, best_op = scored[0]
    ties = [x for x in scored if x[0] == best_score and x[1] == best_dist]
    if len(ties) > 1:
        return best_op, "ambiguous", f"Ambiguo: {len(ties)} ops con igual score/dist"

    return best_op, "matched", None


# ── Import principal ──────────────────────────────────────────────────────────

def run(xlsx_path: str, source_file: str, commit: bool = False):
    """Lógica principal de import."""
    mode = "[COMMIT]" if commit else "[DRY-RUN]"
    print(f"{mode} xlsx={xlsx_path}  source_file={source_file}")

    print("\nParsing Excel...")
    records = parse_excel(xlsx_path)
    print(f"  Registros parseados: {len(records)}")
    if not records:
        print("  Sin registros — abortando.")
        return

    from dotenv import load_dotenv
    load_dotenv()
    from app.database import SessionLocal
    from app import models

    db = SessionLocal()
    try:
        all_ops, depot_by_op_prod = load_operations_and_trips(db)
        print(f"  Operativos en DB: {len(all_ops)}")

        stats = {"matched": 0, "unmatched": 0, "ambiguous": 0,
                 "inserted": 0, "updated": 0, "skipped": 0}
        total_cv_excel = total_depot = total_vapor = 0.0

        print(f"\n{'Row':<4} {'Buque':<30} {'Producto':<25} "
              f"{'CV Excel t':>10} {'Depósito t':>10} {'Vapor t':>10} "
              f"{'Status':<12} {'Acción':<8} {'Notas'}")
        print("-" * 130)

        for rec in records:
            op, status, notes = match_operation(rec, all_ops, depot_by_op_prod)
            stats[status] = stats.get(status, 0) + 1

            op_id = op.id if op else None
            prod  = rec["product"]

            depot_t = 0.0
            if op_id and prod in depot_by_op_prod.get(op_id, {}):
                depot_t = depot_by_op_prod[op_id][prod]
            elif op_id:
                dep_prods = list(depot_by_op_prod.get(op_id, {}).keys())
                if dep_prods:
                    notes = (notes or "") + f" [depósito tiene: {', '.join(dep_prods[:3])}]"

            cv_t = rec["cv_excel_tons"]
            if depot_t > cv_t + 1.0:
                notes = (notes or "") + f" [⚠ depósito({depot_t:.1f}t) > cv_excel({cv_t:.1f}t)]"
            costado_vapor    = max(cv_t - depot_t, 0.0)
            total_discharged = depot_t + costado_vapor   # = max(cv_t, depot_t)

            total_cv_excel += cv_t
            total_depot    += depot_t
            total_vapor    += costado_vapor

            action = "-"
            if commit:
                # Clave de idempotencia
                existing = db.query(models.OperationProductTotal).filter(
                    models.OperationProductTotal.source_file   == source_file,
                    models.OperationProductTotal.raw_ship_name == rec["raw_ship_name"],
                    models.OperationProductTotal.product       == prod,
                    models.OperationProductTotal.cv_start_date == rec["cv_start_date"],
                ).first()

                source_year = rec["cv_start_date"].year if rec["cv_start_date"] else None

                if existing:
                    # ¿Hubo cambio real?
                    changed = (
                        existing.operation_id          != op_id or
                        abs(float(existing.cv_excel_tons or 0) - cv_t) > 0.01 or
                        abs(float(existing.depot_tons or 0) - depot_t) > 0.01 or
                        existing.match_status          != status
                    )
                    if changed:
                        existing.operation_id          = op_id
                        existing.ship_name             = rec["ship_name"]
                        existing.client                = rec["client"]
                        existing.cv_end_date           = rec["cv_end_date"]
                        existing.cv_excel_tons         = cv_t
                        existing.depot_tons            = depot_t
                        existing.costado_vapor_tons    = costado_vapor
                        existing.total_discharged_tons = total_discharged
                        existing.match_status          = status
                        existing.notes                 = notes
                        existing.source_year           = source_year
                        stats["updated"] += 1
                        action = "UPDATE"
                    else:
                        stats["skipped"] += 1
                        action = "SKIP"
                else:
                    db.add(models.OperationProductTotal(
                        operation_id          = op_id,
                        raw_ship_name         = rec["raw_ship_name"],
                        ship_name             = rec["ship_name"],
                        client                = rec["client"],
                        product               = prod,
                        cv_start_date         = rec["cv_start_date"],
                        cv_end_date           = rec["cv_end_date"],
                        cv_excel_tons         = cv_t,
                        depot_tons            = depot_t,
                        costado_vapor_tons    = costado_vapor,
                        total_discharged_tons = total_discharged,
                        match_status          = status,
                        notes                 = notes,
                        source_file           = source_file,
                        source_year           = source_year,
                    ))
                    stats["inserted"] += 1
                    action = "INSERT"

            ship_d = rec["ship_name"][:28]
            prod_d = prod[:23]
            print(f"{rec['row_idx']:<4} {ship_d:<30} {prod_d:<25} "
                  f"{cv_t:>10.1f} {depot_t:>10.1f} {costado_vapor:>10.1f} "
                  f"{status:<12} {action:<8} {notes or ''}")

        if commit:
            db.commit()
            print(f"\n[COMMITTED]")

        print(f"\n{'='*65}")
        print(f"  Registros leídos:   {len(records)}")
        print(f"  Matched:            {stats['matched']}")
        print(f"  Unmatched:          {stats['unmatched']}")
        print(f"  Ambiguous:          {stats.get('ambiguous', 0)}")
        if commit:
            print(f"  Insertados:         {stats['inserted']}")
            print(f"  Actualizados:       {stats['updated']}")
            print(f"  Sin cambios (skip): {stats['skipped']}")
        print(f"")
        if total_cv_excel > 0:
            print(f"  Total CV Excel:     {total_cv_excel:>10.1f} t")
            print(f"  Total depósito:     {total_depot:>10.1f} t")
            print(f"  Total vapor:        {total_vapor:>10.1f} t  "
                  f"({total_vapor / total_cv_excel * 100:.1f}% del CV Excel)")
        print(f"  source_file:        {source_file}")

    except Exception as e:
        db.rollback()
        import traceback
        traceback.print_exc()
        print(f"\nERROR: {e}")
        sys.exit(1)
    finally:
        db.close()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Importar Excel Costado Vapor a DB")
    p.add_argument(
        "--xlsx",
        default="/Users/juancruzespina/Downloads/cv .xlsx",
        help="Ruta al archivo Excel CV (default: ~/Downloads/cv .xlsx)",
    )
    p.add_argument(
        "--source-file",
        default="cv_history.xlsx",
        help="Identificador lógico del archivo (clave de idempotencia). "
             "Ej: cv_history.xlsx, cv_2026.xlsx (default: cv_history.xlsx)",
    )
    p.add_argument(
        "--commit",
        action="store_true",
        help="Escribir a DB (sin este flag: dry-run)",
    )
    args = p.parse_args()
    run(xlsx_path=args.xlsx, source_file=args.source_file, commit=args.commit)
