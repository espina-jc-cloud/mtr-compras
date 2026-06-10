"""
Carga Tanda 2 del Tarifario — Tarifas de SERVICIOS por cliente.

Fuente: tarifas contractuales pasadas por el usuario (2026-06-10), separadas
por cliente. Confirmaciones del usuario:
  - CNA: ARS/ton.
  - Palletizado Nutrien: adicional hijo del embolsado de 50 kg.
  - Todo scope=cliente (precios negociados, NO tarifario base general).
  - IVA y condiciones de revisión: en observaciones.
  - "Sin cargo": precio 0 + observación con la condición.

Idempotente (por cliente + descripción) y transaccional.

Uso:  .venv/bin/python scripts/load_tarifas_servicios.py
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import date
from app.database import SessionLocal
from app import models  # noqa: F401 — registra Equipment para la relación de Tariff
from app.models_tariffs import Client, TariffService, Tariff

VALID_FROM = date(2026, 6, 10)
CREATED_BY = "Carga tanda 2 — Tarifas de servicios por cliente"

# ── Clientes ──────────────────────────────────────────────────────────────────
CLIENTES = [
    # (nombre, rubro, notas)
    ("Cemento Avellaneda", "Cemento",        "Alias comercial: CASA."),
    ("CNA",                "Fertilizantes",  None),
    ("Nutrien",            "Fertilizantes",  None),
]

# ── Servicios nuevos (familias; el detalle va en la descripción de cada tarifa) ─
SERVICIOS_NUEVOS = [
    # (nombre, categoria, unidad_default, orden)
    ("Pesaje y estiba en depósito",     "Depósito",                  "ton", 120),
    ("Embolsado y carga de camiones",   "Movimiento de mercadería",  "ton", 130),
    ("Despacho desde depósito",         "Depósito",                  "ton", 140),
    ("Reacondicionamiento de pallets",  "Movimiento de mercadería",  "ton", 150),
    ("Flete corto",                     "Transporte",                "ton", 160),
    ("Carga de camiones a granel",      "Movimiento de mercadería",  "ton", 170),
]

OBS_CASA = ("+ IVA. Horario de operación: lunes a viernes 08:00-16:00 hs, "
            "sábados 08:00-12:00 hs; no incluye feriados. "
            "Revisión trimestral según variación del precio del gasoil.")
OBS_CNA = "Tarifa sujeta a revisión según condiciones operativas y de mercado. ARS/ton."
OBS_NUTRIEN = ("Las tarifas de horarios inhábiles se adicionan a las tarifas base hábiles. "
               "Servicios incluyen prestaciones detalladas en contrato y adendas.")

# ── Tarifas: (cliente, servicio, descripcion, precio, moneda, unidad,
#              line_type, parent_desc, obs) ───────────────────────────────────
# parent_desc: descripción de la tarifa padre (mismo cliente) para adicionales.
TARIFAS = [
    # ── CASA ──────────────────────────────────────────────────────────────────
    ("Cemento Avellaneda", "Pesaje y estiba en depósito",
     "Pesaje y estiba de pallets en depósito", 1.90, "USD", "ton", "servicio", None, OBS_CASA),
    ("Cemento Avellaneda", "Carga de camiones",
     "Carga y pesaje de pallets", 1.90, "USD", "ton", "servicio", None, OBS_CASA),
    ("Cemento Avellaneda", "Reacondicionamiento de pallets",
     "Reacondicionamiento de pallets con film stretch", 3.50, "USD", "ton", "servicio", None, OBS_CASA),
    ("Cemento Avellaneda", "Almacenaje",
     "Almacenaje en depósito cubierto — sin cargo", 0, "USD", "fijo", "servicio", None,
     "Sin cargo. Bonificado dentro del acuerdo de servicios: depósito cubierto de "
     "40 m × 38 m. Sujeto al mismo horario de operación y revisión trimestral del acuerdo."),

    # ── CNA — Granel ──────────────────────────────────────────────────────────
    ("CNA", "Desestiba de buques",
     "Desestiba de productos a granel y carga de camiones — Puerto San Nicolás",
     15578.19, "ARS", "ton", "servicio", None, OBS_CNA),
    ("CNA", "Transporte",
     "Transporte a depósito MTR", 9718.40, "ARS", "ton", "servicio", None, OBS_CNA),
    ("CNA", "Pesaje y estiba en depósito",
     "Pesaje y estiba en depósito (granel)", 4580.78, "ARS", "ton", "servicio", None, OBS_CNA),
    ("CNA", "Despacho desde depósito",
     "Despacho a granel zarandeado", 4580.78, "ARS", "ton", "servicio", None, OBS_CNA),
    # ── CNA — Bolsones ────────────────────────────────────────────────────────
    ("CNA", "Pesaje y estiba en depósito",
     "Pesaje, ingreso y estiba en depósito (bolsones)", 4580.78, "ARS", "ton", "servicio", None, OBS_CNA),
    ("CNA", "Embolsado y carga de camiones",
     "Embolsado en bolsas de 25 kg y carga de camiones", 28924.10, "ARS", "ton", "servicio", None, OBS_CNA),
    ("CNA", "Embolsado y carga de camiones",
     "Embolsado en bolsas de 50 kg y carga de camiones", 22122.98, "ARS", "ton", "servicio", None, OBS_CNA),
    ("CNA", "Embolsado y carga de camiones",
     "Embolsado en bolsas de 1.000 kg y carga de camiones", 20512.86, "ARS", "ton", "servicio", None, OBS_CNA),
    ("CNA", "Desestiba de buques",
     "Desestiba de productos en bolsones y carga de camiones — Puerto San Nicolás",
     19075.33, "ARS", "ton", "servicio", None, OBS_CNA),
    ("CNA", "Despacho desde depósito",
     "Despacho en bolsones", 4580.78, "ARS", "ton", "servicio", None, OBS_CNA),

    # ── Nutrien — Granel (hábil + adicional inhábil) ──────────────────────────
    ("Nutrien", "Desestiba de buques",
     "Desestiba de graneles sólidos en Puerto San Nicolás (incluye CTSN) — horarios hábiles",
     12.54, "USD", "ton", "servicio", None, OBS_NUTRIEN),
    ("Nutrien", "Desestiba de buques",
     "Adicional horarios inhábiles — desestiba de graneles sólidos P. San Nicolás",
     4.12, "USD", "ton", "adicional",
     "Desestiba de graneles sólidos en Puerto San Nicolás (incluye CTSN) — horarios hábiles",
     OBS_NUTRIEN),
    ("Nutrien", "Transporte",
     "Transporte de graneles sólidos, pesaje e ingreso a depósito MTR S.A. — horarios hábiles",
     12.92, "USD", "ton", "servicio", None, OBS_NUTRIEN),
    ("Nutrien", "Transporte",
     "Adicional horarios inhábiles — transporte, pesaje e ingreso a depósito MTR S.A.",
     2.72, "USD", "ton", "adicional",
     "Transporte de graneles sólidos, pesaje e ingreso a depósito MTR S.A. — horarios hábiles",
     OBS_NUTRIEN),
    ("Nutrien", "Pesaje y estiba en depósito",
     "Pesaje e ingreso a depósito MTR S.A. — horarios hábiles",
     5.89, "USD", "ton", "servicio", None, OBS_NUTRIEN),
    ("Nutrien", "Pesaje y estiba en depósito",
     "Adicional horarios inhábiles — pesaje e ingreso a depósito MTR S.A.",
     1.77, "USD", "ton", "adicional",
     "Pesaje e ingreso a depósito MTR S.A. — horarios hábiles",
     OBS_NUTRIEN),
    ("Nutrien", "Flete corto",
     "Flete corto (Depósito MTR S.A.)", 7.03, "USD", "ton", "servicio", None, OBS_NUTRIEN),
    ("Nutrien", "Carga de camiones a granel",
     "Carga de camiones con productos puros a granel zarandeados",
     5.89, "USD", "ton", "servicio", None, OBS_NUTRIEN),
    # ── Nutrien — Embolsado ───────────────────────────────────────────────────
    ("Nutrien", "Embolsado y carga de camiones",
     "Embolsado en bolsas de 50 kg y carga simultánea sobre camiones — productos puros",
     28.05, "USD", "ton", "servicio", None, OBS_NUTRIEN),
    ("Nutrien", "Embolsado y carga de camiones",
     "Embolsado en bolsas de 25 kg y carga simultánea sobre camiones — productos puros",
     39.27, "USD", "ton", "servicio", None, OBS_NUTRIEN),
    ("Nutrien", "Embolsado y carga de camiones",
     "Llenado de bolsones de 600/1.000 kg con productos puros y carga de camiones",
     23.38, "USD", "ton", "servicio", None, OBS_NUTRIEN),
    ("Nutrien", "Embolsado y carga de camiones",
     "Adicional palletizado de bolsas de 50 kg (incluye pallets) y carga de camiones",
     24.31, "USD", "ton", "adicional",
     "Embolsado en bolsas de 50 kg y carga simultánea sobre camiones — productos puros",
     OBS_NUTRIEN),
]


def run():
    db = SessionLocal()
    stats = {"clientes": 0, "servicios": 0, "tarifas": 0, "adicionales": 0, "salteados": 0}
    try:
        # 1. Clientes
        cli_ids = {}
        for nombre, rubro, notas in CLIENTES:
            c = db.query(Client).filter(Client.nombre == nombre).first()
            if not c:
                c = Client(nombre=nombre, rubro=rubro, notas=notas, activo=True)
                db.add(c)
                db.flush()
                stats["clientes"] += 1
            cli_ids[nombre] = c.id

        # 2. Servicios nuevos
        for nombre, cat, unidad, orden in SERVICIOS_NUEVOS:
            if not db.query(TariffService).filter(TariffService.nombre == nombre).first():
                db.add(TariffService(nombre=nombre, categoria=cat,
                                     unidad_default=unidad, orden=orden, activo=True))
                stats["servicios"] += 1
        db.flush()

        svc_ids = {s.nombre: s.id for s in db.query(TariffService).all()}

        # 3. Tarifas — dos pasadas: primero servicios (padres), después adicionales
        creadas = {}  # (cliente, descripcion) → id

        def insertar(filas):
            for cliente, servicio, desc, precio, moneda, unidad, lt, parent_desc, obs in filas:
                cid = cli_ids[cliente]
                ya = db.query(Tariff).filter(
                    Tariff.client_id == cid,
                    Tariff.descripcion == desc,
                    Tariff.is_active == True,  # noqa: E712
                ).first()
                if ya:
                    creadas[(cliente, desc)] = ya.id
                    stats["salteados"] += 1
                    continue
                pid = None
                if parent_desc:
                    pid = creadas.get((cliente, parent_desc))
                    if pid is None:
                        raise RuntimeError(f"Padre no encontrado para adicional: {desc!r}")
                t = Tariff(
                    scope="cliente", client_id=cid,
                    line_type=lt, price_tier="unica", visibility="comercial",
                    service_id=svc_ids[servicio], parent_id=pid,
                    descripcion=desc, precio=precio, moneda=moneda, unidad=unidad,
                    valid_from=VALID_FROM, is_active=True,
                    observaciones=obs, created_by=CREATED_BY,
                )
                db.add(t)
                db.flush()
                creadas[(cliente, desc)] = t.id
                stats["tarifas"] += 1
                if lt == "adicional":
                    stats["adicionales"] += 1

        insertar([f for f in TARIFAS if f[6] == "servicio"])
        insertar([f for f in TARIFAS if f[6] == "adicional"])

        db.commit()
        print("✅ Tanda 2 cargada:")
        for k, v in stats.items():
            print(f"   {k}: {v}")

        for nombre in cli_ids:
            n = db.query(Tariff).filter(
                Tariff.client_id == cli_ids[nombre],
                Tariff.created_by == CREATED_BY).count()
            print(f"   · {nombre}: {n} tarifas")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run()
