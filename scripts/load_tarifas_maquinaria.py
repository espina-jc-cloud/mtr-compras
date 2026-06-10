"""
Carga inicial del Tarifario — Alquiler de Maquinaria.

Fuente: Tarifario_MTR_Alquiler_Maquinaria.xlsx (hojas Precio Lista / Precio Piso /
Precio Premium / Adicionales / Mercado relevado), validado con el usuario el 2026-06-10.

Decisiones aprobadas:
  - 7 servicios separados, uno por tipo de equipo (categoría "Equipos").
  - Adicionales globales sin parent_id (operador, modalidades de combustible).
  - Combustible paquete completo: recargo base 15% (rango real 15-20% en obs).

Idempotente: si ya existe una tarifa activa equivalente, la saltea.
Transaccional: o se carga todo o nada.

Uso:  .venv/bin/python scripts/load_tarifas_maquinaria.py
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import date
from app.database import SessionLocal
from app import models  # noqa: F401 — registra Equipment para la relación de Tariff
from app.models_tariffs import Client, TariffService, Tariff  # noqa: F401

VALID_FROM = date(2026, 6, 10)
CREATED_BY = "Carga inicial — Excel Alquiler Maquinaria"

# ── 7 servicios de equipo ──────────────────────────────────────────────────────
# (nombre, orden)
EQUIPOS = [
    ("Autoelevador 7 TN",      210),
    ("Autoelevador 3,5 TN",    220),
    ("Pala cargadora 3 m³",    230),
    ("Mini cargadora",         240),
    ("Retroexcavadora",        250),
    ("Tijera 10 m",            260),
    ("Plataforma JLG 20 m",    270),
]

# Precios por equipo: {equipo: {tier: (día, semana, mes)}}
PRECIOS = {
    "Autoelevador 7 TN":   {"lista": (480, 1900, 5000), "piso": (400, 1600, 4200), "premium": (600, 2300, 6000)},
    "Autoelevador 3,5 TN": {"lista": (190,  750, 2000), "piso": (160,  620, 1650), "premium": (250,  950, 2500)},
    "Pala cargadora 3 m³": {"lista": (420, 1800, 5200), "piso": (350, 1500, 4500), "premium": (550, 2300, 6300)},
    "Mini cargadora":      {"lista": (310, 1300, 4000), "piso": (260, 1100, 3400), "premium": (400, 1650, 4800)},
    "Retroexcavadora":     {"lista": (450, 1850, 5000), "piso": (380, 1550, 4200), "premium": (580, 2300, 6000)},
    "Tijera 10 m":         {"lista": (140,  520, 1400), "piso": (115,  430, 1150), "premium": (180,  650, 1700)},
    "Plataforma JLG 20 m": {"lista": (620, 2100, 6000), "piso": (530, 1800, 5100), "premium": (800, 2700, 7200)},
}

# Flags por equipo: (incluye_operador, incluye_combustible)
# None = no aplica. Tijera eléctrica sin operador; JLG sin operador.
FLAGS = {
    "Tijera 10 m":         (None, None),
    "Plataforma JLG 20 m": (None, False),
}
FLAGS_DEFAULT = (False, False)

OBS_TIER = {
    "lista":   "Sin operador, sin combustible. Flete a coordinar.",
    "piso":    "Mínimo aceptable de negociación. Por debajo: aprobación del GM.",
    "premium": "Urgencias <24h, fin de semana, feriado, parada de planta o exclusividad. NO descontar.",
}
OBS_MES = " Mes = 30 días corridos."

# ── Adicionales globales (sin padre) ──────────────────────────────────────────
# (descripcion, precio, unidad, recargo_pct, obs)
ADICIONALES_GLOBALES = [
    ("Operador — jornada 8 hs",           90,   "dia",  None,
     "No aplica a Tijera 10 m ni Plataforma JLG 20 m."),
    ("Operador — semana (5 días)",        420,  "semana", None,
     "No aplica a Tijera 10 m ni Plataforma JLG 20 m."),
    ("Operador — mes (22 jornadas)",      1500, "mes",  None,
     "No aplica a Tijera 10 m ni Plataforma JLG 20 m."),
    ("Operador — hora extra",             15,   "hora", None,
     "Por hora excedente de la jornada de 8 hs. No aplica a Tijera ni JLG."),
    ("Operador — fin de semana / feriado", 130, "dia",  None,
     "Por día. No aplica a Tijera 10 m ni Plataforma JLG 20 m."),
    ("Combustible — paquete completo (con tope)", 0, "fijo", 15,
     "Incluye combustible con tope de uso normal (8 hs). Exceso se factura aparte. "
     "Rango habitual 15%-20% según negociación / operativo; 15% = valor base."),
    ("Combustible — pass-through con margen", 0, "fijo", 10,
     "MTR carga combustible y refactura +10% por logística. "
     "Default sin cargo: combustible a cargo del cliente (carga directa o reembolso con remito)."),
]

# ── Topes de combustible por equipo (internos, hijos de la tarifa lista/día) ──
# (equipo, costo_usd_dia, obs)
TOPES_COMBUSTIBLE = [
    ("Autoelevador 7 TN",   50,  "Tope uso normal 8 hs: 40-50 L/día gasoil (USD 1,1/L)."),
    ("Autoelevador 3,5 TN", 30,  "GLP. Tope uso normal 8 hs."),
    ("Pala cargadora 3 m³", 100, "Tope uso normal 8 hs: 80-100 L/día gasoil (USD 1,1/L)."),
    ("Mini cargadora",      70,  "Tope uso normal 8 hs: 50-70 L/día gasoil (USD 1,1/L)."),
    ("Retroexcavadora",     80,  "Tope uso normal 8 hs: 60-80 L/día gasoil (USD 1,1/L)."),
    ("Tijera 10 m",         0,   "Eléctrica: sin consumo de combustible."),
    ("Plataforma JLG 20 m", 40,  "Eléctrica/diesel según equipo. Tope uso normal 8 hs."),
]

# ── Benchmarks de mercado (internos) ──────────────────────────────────────────
# (equipo, plaza, precio, unidad, obs)
BENCHMARKS = [
    ("Autoelevador 7 TN",   None,          5000, "mes",
     "Día USD 500 · Semana 1.200/2.000 · Mes 3.000/5.000. Sin op., sin comb. Plaza s/d."),
    ("Autoelevador 3,5 TN", "Rosario",     1800, "mes",
     "Día USD 200 · Semana 400/600 · Mes 900/1.200/1.800. Operador mes USD 1.485."),
    ("Autoelevador 3,5 TN", "San Nicolás", 1250, "mes",
     "Única referencia local."),
    ("Pala cargadora 3 m³", "San Nicolás", 4600, "mes",
     "Día USD 322 · Semana 1.500 · Mes 4.600. Combustible día USD 89."),
    ("Pala cargadora 3 m³", "Rosario",     750,  "dia",
     "Día USD 500/750. Mercado más caro."),
    ("Mini cargadora",      "San Nicolás", 3500, "mes",
     "Día USD 245 · Semana 1.143 · Mes 3.500. Combustible día USD 76."),
    ("Mini cargadora",      "Rosario",     600,  "dia",
     "Día USD 450/600. Mercado más caro."),
    ("Retroexcavadora",     None,          500,  "dia",
     "Única referencia. Plaza s/d."),
    ("Tijera 10 m",         "Rosario",     1200, "mes",
     "Día USD 105/133 · Semana 365/400 · Mes 862/1.200. Eléctrica, sin operador."),
    ("Plataforma JLG 20 m", "San Nicolás", 5940, "mes",
     "Día USD 660 · Semana 1.980 · Mes 5.940. Premium fuerte."),
    ("Plataforma JLG 20 m", "Rosario",     3775, "mes",
     "Día USD 350 · Semana 2.030 · Mes 3.775. Mercado más bajo."),
]


def run():
    db = SessionLocal()
    stats = {"servicios": 0, "equipos": 0, "adicionales": 0, "topes": 0, "benchmarks": 0, "salteados": 0}
    try:
        # 1. Servicios (uno por equipo)
        svc_ids = {}
        for nombre, orden in EQUIPOS:
            svc = db.query(TariffService).filter(TariffService.nombre == nombre).first()
            if not svc:
                svc = TariffService(nombre=nombre, categoria="Equipos",
                                    unidad_default="dia", orden=orden, activo=True)
                db.add(svc)
                db.flush()
                stats["servicios"] += 1
            svc_ids[nombre] = svc.id

        def existe(service_id, tier, unidad, line_type, plaza=None, descripcion=None):
            q = db.query(Tariff).filter(
                Tariff.service_id == service_id,
                Tariff.price_tier == tier,
                Tariff.unidad == unidad,
                Tariff.line_type == line_type,
                Tariff.is_active == True,  # noqa: E712
            )
            if plaza is not None:
                q = q.filter(Tariff.plaza == plaza)
            if descripcion is not None:
                q = q.filter(Tariff.descripcion == descripcion)
            return q.first() is not None

        # 2. Equipos: 7 × 3 tiers × 3 escalas — los lista/día son los padres
        padres_lista_dia = {}
        for equipo, tiers in PRECIOS.items():
            sid = svc_ids[equipo]
            op, comb = FLAGS.get(equipo, FLAGS_DEFAULT)
            for tier, (p_dia, p_sem, p_mes) in tiers.items():
                for unidad, precio in (("dia", p_dia), ("semana", p_sem), ("mes", p_mes)):
                    if existe(sid, tier, unidad, "equipo"):
                        stats["salteados"] += 1
                        continue
                    obs = OBS_TIER[tier] + (OBS_MES if unidad == "mes" else "")
                    t = Tariff(
                        scope="base", line_type="equipo", price_tier=tier,
                        visibility="interna" if tier == "piso" else "comercial",
                        service_id=sid, precio=precio, moneda="USD", unidad=unidad,
                        incluye_operador=op, incluye_combustible=comb,
                        valid_from=VALID_FROM, is_active=True,
                        observaciones=obs, created_by=CREATED_BY,
                    )
                    db.add(t)
                    db.flush()
                    stats["equipos"] += 1
                    if tier == "lista" and unidad == "dia":
                        padres_lista_dia[equipo] = t.id

        # Si los padres ya existían (re-corrida), recuperarlos
        for equipo in PRECIOS:
            if equipo not in padres_lista_dia:
                t = db.query(Tariff).filter(
                    Tariff.service_id == svc_ids[equipo],
                    Tariff.price_tier == "lista", Tariff.unidad == "dia",
                    Tariff.line_type == "equipo",
                    Tariff.is_active == True,  # noqa: E712
                ).first()
                if t:
                    padres_lista_dia[equipo] = t.id

        # 3. Adicionales globales (sin padre) — servicio genérico "Alquiler de equipos"
        svc_alquiler = db.query(TariffService).filter(
            TariffService.nombre == "Alquiler de equipos").first()
        for desc, precio, unidad, recargo, obs in ADICIONALES_GLOBALES:
            if existe(svc_alquiler.id, "unica", unidad, "adicional", descripcion=desc):
                stats["salteados"] += 1
                continue
            db.add(Tariff(
                scope="base", line_type="adicional", price_tier="unica",
                visibility="comercial", service_id=svc_alquiler.id,
                descripcion=desc, precio=precio, moneda="USD", unidad=unidad,
                recargo_pct=recargo, parent_id=None,
                valid_from=VALID_FROM, is_active=True,
                observaciones=obs, created_by=CREATED_BY,
            ))
            stats["adicionales"] += 1

        # 4. Topes de combustible — internos, hijos de la tarifa lista/día del equipo
        for equipo, costo, obs in TOPES_COMBUSTIBLE:
            sid = svc_ids[equipo]
            desc = f"Tope combustible — {equipo}"
            if existe(sid, "unica", "dia", "adicional", descripcion=desc):
                stats["salteados"] += 1
                continue
            db.add(Tariff(
                scope="base", line_type="adicional", price_tier="unica",
                visibility="interna", service_id=sid,
                parent_id=padres_lista_dia.get(equipo),
                descripcion=desc, precio=costo, moneda="USD", unidad="dia",
                valid_from=VALID_FROM, is_active=True,
                observaciones=obs, created_by=CREATED_BY,
            ))
            stats["topes"] += 1

        # 5. Benchmarks de mercado — internos
        for equipo, plaza, precio, unidad, obs in BENCHMARKS:
            sid = svc_ids[equipo]
            if existe(sid, "unica", unidad, "benchmark", plaza=plaza):
                stats["salteados"] += 1
                continue
            db.add(Tariff(
                scope="base", line_type="benchmark", price_tier="unica",
                visibility="interna", service_id=sid, plaza=plaza,
                descripcion=f"Mercado relevado — {equipo}" + (f" ({plaza})" if plaza else ""),
                precio=precio, moneda="USD", unidad=unidad,
                valid_from=VALID_FROM, is_active=True,
                observaciones=obs, created_by=CREATED_BY,
            ))
            stats["benchmarks"] += 1

        db.commit()
        print("✅ Carga completa:")
        for k, v in stats.items():
            print(f"   {k}: {v}")

        # Verificación post-carga
        total = db.query(Tariff).filter(Tariff.created_by == CREATED_BY).count()
        comerciales = db.query(Tariff).filter(
            Tariff.created_by == CREATED_BY, Tariff.visibility == "comercial").count()
        internas = db.query(Tariff).filter(
            Tariff.created_by == CREATED_BY, Tariff.visibility == "interna").count()
        print(f"\n   Total tarifas de esta carga: {total} ({comerciales} comerciales, {internas} internas)")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run()
