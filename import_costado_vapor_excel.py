#!/usr/bin/env python3
"""
Import Costado Vapor (CV) Excel data into operation_product_totals table.

Usage:
  python3 import_costado_vapor_excel.py           # dry-run (default)
  python3 import_costado_vapor_excel.py --commit  # write to DB
"""
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime, timedelta

EXCEL_PATH  = "/Users/juancruzespina/Downloads/cv .xlsx"
SOURCE_FILE = "cv_history.xlsx"
DATE_WINDOW = 10  # days

# ── Normalizer dictionaries ────────────────────────────────────────────────────

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

DEPOT_PRODUCT_ALIASES = {
    "MOP":   "CLORURO DE POTASIO",
    "AMSUL": "SULFATO DE AMONIO",
}

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
    "M/V CL ZHANGJIAJIE":  "M/V CL ZHANGJIAJIE",
    "M/V Endless Horizon":  "M/V ENDLESS HORIZON",
    "M/V Yasa Moon\xa0":   "YASA MOON",
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


def norm_key(v):
    """Normalize ship name for matching: strip accents, uppercase, remove M/V prefix."""
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
    """Normalize CV Excel product name."""
    if raw is None:
        return "(sin producto)"
    s = str(raw).strip()
    return PRODUCT_FIX_CV.get(s, s.upper() if s else "(sin producto)")


def parse_excel(path):
    """Parse CV Excel. Returns list of records."""
    try:
        import openpyxl
    except ImportError:
        print("ERROR: openpyxl not installed. Run: pip install openpyxl")
        sys.exit(1)

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    # Header at row 5 (0-indexed 4), data rows 8-37 (0-indexed 7-36)
    data_rows = rows[7:37]  # 0-indexed 7 to 36 inclusive

    records = []
    last_ship = None
    last_start = None
    last_end = None

    for i, row in enumerate(data_rows):
        def col(j):
            return row[j] if j < len(row) else None

        raw_ship = col(1)
        raw_start = col(2)
        raw_end   = col(3)
        raw_op    = col(4)
        raw_client = col(5)
        raw_product = col(6)
        raw_tons   = col(7)

        # Inherit ship/dates from previous row when col[1] is empty
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

        # Parse dates
        def to_dt(val):
            if val is None:
                return None
            if isinstance(val, datetime):
                return val.replace(hour=0, minute=0, second=0, microsecond=0)
            try:
                return datetime.strptime(str(val)[:10], "%Y-%m-%d")
            except Exception:
                return None

        start_dt = to_dt(raw_start)
        end_dt   = to_dt(raw_end)

        # Map raw ship name to normalized
        ship_norm = SHIP_FIX.get(raw_ship, raw_ship)

        # Anomaly: year 2026 → 2025 on Amani
        if "AMANI" in raw_ship.upper() or "AMANI" in ship_norm.upper():
            if start_dt and start_dt.year == 2026:
                start_dt = start_dt.replace(year=2025)
                print(f"  [fix] Amani start_date year 2026→2025: {start_dt.date()}")
            if end_dt and end_dt.year == 2026:
                end_dt = end_dt.replace(year=2025)
                print(f"  [fix] Amani end_date year 2026→2025: {end_dt.date()}")

        # Anomaly: swap end < start on NIKITIS
        if "NIKITIS" in raw_ship.upper() or "NIKITIS" in ship_norm.upper():
            if start_dt and end_dt and end_dt < start_dt:
                start_dt, end_dt = end_dt, start_dt
                print(f"  [fix] NIKITIS dates swapped: {start_dt.date()} – {end_dt.date()}")

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
            "row_idx":       i + 8,  # 1-based data row
        })

    return records


def load_operations_and_trips(db):
    """Load all operations and compute depot tons per op+product."""
    from app import models

    all_ops   = db.query(models.Operation).all()
    all_trips = db.query(models.OperationTrip).all()

    # depot_by_op_prod: {op_id → {normalized_product → tons}}
    depot_by_op_prod = defaultdict(lambda: defaultdict(float))
    for t in all_trips:
        if t.neto_kg:
            prod_norm = DEPOT_PRODUCT_ALIASES.get(t.product, t.product) if t.product else "(sin producto)"
            depot_by_op_prod[t.operation_id][prod_norm] += t.neto_kg / 1000

    return all_ops, depot_by_op_prod


def match_operation(cv_rec, all_ops, depot_by_op_prod):
    """
    Match a CV record to an operation.
    Returns (op, status, notes) where status is 'matched'/'unmatched'/'ambiguous'.
    """
    mk = cv_rec["match_key"]
    start_dt = cv_rec["cv_start_date"]
    prod = cv_rec["product"]

    candidates = [op for op in all_ops if norm_key(op.ship_name) == mk]

    if not candidates:
        return None, "unmatched", f"No operation found for ship '{cv_rec['ship_name']}'"

    # Filter by date window
    if start_dt:
        date_candidates = []
        for op in candidates:
            ref_date = op.start_date or op.end_date
            if ref_date:
                dist = abs((start_dt - ref_date).days)
                if dist <= DATE_WINDOW:
                    date_candidates.append((op, dist))
            else:
                date_candidates.append((op, DATE_WINDOW))
        if date_candidates:
            candidates_with_dist = date_candidates
        else:
            # No date-filtered match: use all with large distance
            candidates_with_dist = [(op, 999) for op in candidates]
    else:
        candidates_with_dist = [(op, 0) for op in candidates]

    if not candidates_with_dist:
        return None, "unmatched", f"No operation within {DATE_WINDOW} days of {start_dt}"

    # Score: prefer op that has depot for the cv product (has_prod score=0 vs 1)
    scored = []
    for op, dist in candidates_with_dist:
        has_prod = 0 if prod in depot_by_op_prod.get(op.id, {}) else 1
        scored.append((has_prod, dist, op))

    scored.sort(key=lambda x: (x[0], x[1]))
    best_score, best_dist, best_op = scored[0]

    # Check for ties
    ties = [x for x in scored if x[0] == best_score and x[1] == best_dist]
    if len(ties) > 1:
        notes = f"Ambiguous: {len(ties)} ops with same score/dist"
        return best_op, "ambiguous", notes

    return best_op, "matched", None


def run(commit=False):
    """Main import logic."""
    print(f"{'[DRY-RUN]' if not commit else '[COMMIT]'} Importing CV Excel: {EXCEL_PATH}")

    print("\nParsing Excel...")
    records = parse_excel(EXCEL_PATH)
    print(f"  Records parsed: {len(records)}")

    # Setup DB
    from dotenv import load_dotenv
    load_dotenv()

    from app.database import SessionLocal
    from app import models

    db = SessionLocal()

    try:
        all_ops, depot_by_op_prod = load_operations_and_trips(db)
        print(f"  Operations in DB: {len(all_ops)}")

        stats = {"matched": 0, "unmatched": 0, "ambiguous": 0, "inserted": 0, "updated": 0}
        total_cv_excel = 0.0
        total_depot    = 0.0
        total_vapor    = 0.0

        print(f"\n{'Row':<4} {'Ship':<30} {'Product':<25} {'CV Excel t':>10} {'Depot t':>10} {'Vapor t':>10} {'Status':<12} {'Notes'}")
        print("-" * 120)

        for rec in records:
            op, status, notes = match_operation(rec, all_ops, depot_by_op_prod)
            stats[status] = stats.get(status, 0) + 1

            op_id = op.id if op else None
            prod  = rec["product"]

            # Depot tons for this op+product
            depot_t = 0.0
            if op_id and prod in depot_by_op_prod.get(op_id, {}):
                depot_t = depot_by_op_prod[op_id][prod]
            elif op_id:
                # Warn: no depot match for this product
                dep_prods = list(depot_by_op_prod.get(op_id, {}).keys())
                if dep_prods:
                    notes = (notes or "") + f" [depot has: {', '.join(dep_prods[:3])}]"

            cv_t = rec["cv_excel_tons"]
            # CV Excel = total discharged (depot + costado vapor)
            # costado_vapor = cv_excel - depot  (>= 0)
            # total_discharged = cv_excel (when cv >= depot) or depot (warning)
            if depot_t > cv_t + 1.0:  # 1 t tolerance for rounding
                notes = (notes or "") + f" [⚠ depot({depot_t:.1f}t) > cv_excel({cv_t:.1f}t)]"
            costado_vapor    = max(cv_t - depot_t, 0.0)
            total_discharged = cv_t if cv_t >= depot_t else depot_t

            total_cv_excel += cv_t
            total_depot    += depot_t
            total_vapor    += costado_vapor

            ship_display = rec["ship_name"][:28]
            prod_display = prod[:23]
            print(f"{rec['row_idx']:<4} {ship_display:<30} {prod_display:<25} {cv_t:>10.1f} {depot_t:>10.1f} {costado_vapor:>10.1f} {status:<12} {notes or ''}")

            if commit:
                # Idempotency check
                existing = db.query(models.OperationProductTotal).filter(
                    models.OperationProductTotal.source_file    == SOURCE_FILE,
                    models.OperationProductTotal.raw_ship_name  == rec["raw_ship_name"],
                    models.OperationProductTotal.product        == prod,
                    models.OperationProductTotal.cv_start_date  == rec["cv_start_date"],
                ).first()

                if existing:
                    # UPDATE
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
                    existing.source_year           = rec["cv_start_date"].year if rec["cv_start_date"] else None
                    stats["updated"] += 1
                else:
                    # INSERT
                    row = models.OperationProductTotal(
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
                        source_file           = SOURCE_FILE,
                        source_year           = rec["cv_start_date"].year if rec["cv_start_date"] else None,
                    )
                    db.add(row)
                    stats["inserted"] += 1

        if commit:
            db.commit()
            print(f"\n[COMMITTED]")

        print(f"\n{'='*60}")
        print(f"Records read:     {len(records)}")
        print(f"Matched:          {stats['matched']}")
        print(f"Unmatched:        {stats['unmatched']}")
        print(f"Ambiguous:        {stats.get('ambiguous', 0)}")
        if commit:
            print(f"Inserted:         {stats['inserted']}")
            print(f"Updated:          {stats['updated']}")
        print(f"")
        print(f"Total CV Excel:   {total_cv_excel:>10.1f} t")
        print(f"Total depot:      {total_depot:>10.1f} t")
        print(f"Total vapor:      {total_vapor:>10.1f} t  ({total_vapor/total_cv_excel*100:.1f}% of CV Excel)" if total_cv_excel > 0 else "")

    except Exception as e:
        db.rollback()
        import traceback
        traceback.print_exc()
        print(f"\nERROR: {e}")
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    commit = "--commit" in sys.argv
    run(commit=commit)
