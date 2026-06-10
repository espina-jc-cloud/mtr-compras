"""
Tanda 3 del Tarifario — SOLO catálogo de servicios de personal.

Por pedido expreso del usuario (2026-06-10):
  - NO se cargan tarifas de personal (las completa él manualmente en el sistema).
  - NO se carga nada relacionado con SUPA (ni benchmarks ni costos ni obs).
  - Solo se crean los 4 servicios de personal, categoría "Personal".

Idempotente: si el servicio ya existe, lo saltea.

Uso:  .venv/bin/python scripts/load_servicios_personal.py
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import SessionLocal
from app import models  # noqa: F401 — registra Equipment para la relación de Tariff
from app.models_tariffs import TariffService

SERVICIOS_PERSONAL = [
    # (nombre, unidad_default, orden)
    ("Supervisor de puerto", "hora", 310),
    ("Maquinista",           "hora", 320),
    ("Balancero",            "hora", 330),
    ("Palero",               "hora", 340),
]


def run():
    db = SessionLocal()
    creados, salteados = 0, 0
    try:
        for nombre, unidad, orden in SERVICIOS_PERSONAL:
            if db.query(TariffService).filter(TariffService.nombre == nombre).first():
                salteados += 1
                continue
            db.add(TariffService(
                nombre=nombre, categoria="Personal",
                unidad_default=unidad, orden=orden, activo=True,
            ))
            creados += 1
        db.commit()
        print(f"✅ Catálogo de personal: {creados} servicios creados, {salteados} ya existían.")
        print("   Sin tarifas cargadas — se completan manualmente en /tarifario/new.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run()
