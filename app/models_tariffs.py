"""
Módulo Tarifario — precios de VENTA que MTR le cobra a cada cliente.

NO son costos internos ni tarifas de terceros: son los precios comerciales propios.

Tres tipos de tarifa (campo `scope` en una sola tabla):
  - base    → tarifa estándar (sin cliente)
  - cliente → precio para un cliente específico
  - spot    → operación puntual / negociada caso por caso

Versionado automático: al cambiar un precio, la tarifa vigente se archiva
(valid_to + is_active=False) y se crea una fila nueva con replaces_id apuntando
a la anterior. Así queda historia completa.

Diseñado para ser la fuente de precios de venta del futuro módulo Cotizador.
"""
from datetime import datetime, date
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Date,
    ForeignKey, Text, Numeric, Index,
)
from sqlalchemy.orm import relationship
from app.database import Base


# ── Catálogos (choices, no tablas — flexibles y editables) ─────────────────────
TARIFF_SCOPES = [
    ("base",    "Estándar"),
    ("cliente", "Por cliente"),
    ("spot",    "Spot / Puntual"),
]
TARIFF_SCOPE_LABELS = dict(TARIFF_SCOPES)

# Tipo de línea: qué clase de concepto se tarifa.
TARIFF_LINE_TYPES = [
    ("servicio",  "Servicio"),
    ("personal",  "Personal"),
    ("equipo",    "Equipo"),
    ("adicional", "Adicional"),
    ("benchmark", "Benchmark mercado"),
]
TARIFF_LINE_TYPE_LABELS = dict(TARIFF_LINE_TYPES)

# Nivel comercial del precio (eje separado de scope, que es a-quién-aplica).
TARIFF_PRICE_TIERS = [
    ("unica",   "Única"),
    ("lista",   "Lista"),
    ("piso",    "Piso (interno)"),
    ("premium", "Premium"),
    ("spot",    "Spot negociado"),
]
TARIFF_PRICE_TIER_LABELS = dict(TARIFF_PRICE_TIERS)

# Visibilidad: las internas (piso, benchmarks) solo las ven admin/superadmin.
TARIFF_VISIBILITIES = [
    ("comercial", "Comercial"),
    ("interna",   "Interna"),
]
TARIFF_VISIBILITY_LABELS = dict(TARIFF_VISIBILITIES)

# Roles que pueden ver tarifas internas (precio piso, benchmarks de mercado)
TARIFF_INTERNAL_ROLES = ("admin", "superadmin")

TARIFF_MONEDAS = [
    ("ARS", "Pesos (ARS)"),
    ("USD", "Dólares (USD)"),
]
TARIFF_MONEDA_LABELS = dict(TARIFF_MONEDAS)

TARIFF_UNIDADES = [
    ("ton",         "Por tonelada"),
    ("viaje",       "Por viaje"),
    ("camion",      "Por camión"),
    ("dia",         "Por día"),
    ("hora",        "Por hora"),
    ("mes",         "Por mes"),
    ("equipo",      "Por equipo"),
    ("contenedor",  "Por contenedor"),
    ("bulto",       "Por bulto"),
    ("m2",          "Por m²"),
    ("fijo",        "Monto fijo"),
]
TARIFF_UNIDAD_LABELS = dict(TARIFF_UNIDADES)

# Categorías sugeridas para agrupar servicios (el catálogo es editable)
TARIFF_CATEGORIAS = [
    "Movimiento de mercadería",
    "Depósito",
    "Transporte",
    "Equipos",
    "Servicios varios",
]

# Badge CSS por scope (mismo estilo visual que Despachos)
SCOPE_CSS = {
    "base":    "bg-gray-100 text-gray-700",
    "cliente": "bg-indigo-50 text-indigo-700",
    "spot":    "bg-amber-50 text-amber-700",
}

LINE_TYPE_CSS = {
    "servicio":  "bg-blue-50 text-blue-700",
    "personal":  "bg-purple-50 text-purple-700",
    "equipo":    "bg-emerald-50 text-emerald-700",
    "adicional": "bg-cyan-50 text-cyan-700",
    "benchmark": "bg-gray-100 text-gray-500",
}

TIER_CSS = {
    "unica":   "bg-gray-100 text-gray-600",
    "lista":   "bg-emerald-50 text-emerald-700",
    "piso":    "bg-red-50 text-red-700",
    "premium": "bg-amber-50 text-amber-700",
    "spot":    "bg-indigo-50 text-indigo-700",
}


class Client(Base):
    """Cliente comercial de MTR (Nutrien, CNA, mineras, etc.).

    Tabla formal para que el tarifario (y a futuro otros módulos) referencien
    clientes de forma estructurada en vez de texto libre.
    """
    __tablename__ = "clients"

    id          = Column(Integer, primary_key=True, index=True)
    nombre      = Column(String(200), nullable=False, index=True)
    cuit        = Column(String(20), nullable=True)
    rubro       = Column(String(120), nullable=True)   # "Fertilizantes", "Minería"...
    contacto    = Column(String(200), nullable=True)
    email       = Column(String(200), nullable=True)
    telefono    = Column(String(60), nullable=True)
    notas       = Column(Text, nullable=True)
    activo      = Column(Boolean, default=True, index=True)
    created_at  = Column(DateTime, default=datetime.utcnow)
    updated_at  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    tariffs = relationship("Tariff", back_populates="client")


class TariffService(Base):
    """Línea de servicio facturable (desestiba, carga de camión, almacenaje...).

    Catálogo editable: el usuario agrega servicios sin tocar código.
    """
    __tablename__ = "tariff_services"

    id              = Column(Integer, primary_key=True, index=True)
    nombre          = Column(String(200), nullable=False, index=True)
    categoria       = Column(String(120), nullable=True, index=True)
    unidad_default  = Column(String(30), nullable=True)   # sugerencia, editable por tarifa
    descripcion     = Column(Text, nullable=True)
    activo          = Column(Boolean, default=True, index=True)
    orden           = Column(Integer, default=100)        # para ordenar en listados
    created_at      = Column(DateTime, default=datetime.utcnow)

    tariffs = relationship("Tariff", back_populates="service")


class Tariff(Base):
    """Tarifa comercial (precio de venta).

    scope distingue base / cliente / spot.
    El versionado se maneja con valid_from/valid_to + is_active + replaces_id.
    """
    __tablename__ = "tariffs"

    id            = Column(Integer, primary_key=True, index=True)

    # ── Clasificación ─────────────────────────────────────────────────────────
    scope         = Column(String(20), nullable=False, default="base", index=True)
    service_id    = Column(Integer, ForeignKey("tariff_services.id"), nullable=False, index=True)
    client_id     = Column(Integer, ForeignKey("clients.id"), nullable=True, index=True)
    # client_id NULL ⇒ scope debe ser 'base'

    # Tipo de línea: servicio | personal | equipo | adicional | benchmark
    line_type     = Column(String(20), nullable=False, default="servicio", index=True)
    # Nivel comercial: unica | lista | piso | premium | spot
    price_tier    = Column(String(20), nullable=False, default="unica", index=True)
    # comercial | interna — las internas solo las ven admin/superadmin
    visibility    = Column(String(20), nullable=False, default="comercial", index=True)
    # Adicionales (operador, combustible) cuelgan de la tarifa del equipo padre
    parent_id     = Column(Integer, ForeignKey("tariffs.id"), nullable=True, index=True)

    # ── Flags de alquiler de equipos ───────────────────────────────────────────
    incluye_operador    = Column(Boolean, nullable=True)   # None = no aplica
    incluye_combustible = Column(Boolean, nullable=True)   # None = no aplica

    # ── Recargo porcentual (combustible +15%, pass-through +10%...) ───────────
    recargo_pct   = Column(Numeric(6, 2), nullable=True)

    # ── Plaza (solo benchmarks de mercado: Rosario, San Nicolás...) ───────────
    plaza         = Column(String(120), nullable=True)

    # ── Equipo (solo alquiler de equipos) ──────────────────────────────────────
    equipment_id  = Column(Integer, ForeignKey("equipment.id"), nullable=True)

    # ── Precio ──────────────────────────────────────────────────────────────────
    descripcion   = Column(Text, nullable=True)         # detalle de la línea
    precio        = Column(Numeric(14, 2), nullable=False)
    moneda        = Column(String(3), nullable=False, default="ARS")
    unidad        = Column(String(30), nullable=False, default="ton")

    # ── Vigencia / versionado ────────────────────────────────────────────────
    valid_from    = Column(Date, nullable=False, default=date.today, index=True)
    valid_to      = Column(Date, nullable=True)         # NULL = vigente sin fin
    is_active     = Column(Boolean, default=True, index=True)
    replaces_id   = Column(Integer, ForeignKey("tariffs.id"), nullable=True)

    # ── Condiciones comerciales ────────────────────────────────────────────────
    observaciones = Column(Text, nullable=True)         # mínimos, incluye/no incluye...

    # ── Auditoría ─────────────────────────────────────────────────────────────
    created_by    = Column(String(120), nullable=True)
    created_at    = Column(DateTime, default=datetime.utcnow)
    updated_at    = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # ── Relaciones ─────────────────────────────────────────────────────────────
    service   = relationship("TariffService", back_populates="tariffs")
    client    = relationship("Client", back_populates="tariffs")
    equipment = relationship("Equipment")
    replaces  = relationship("Tariff", remote_side=[id], foreign_keys=[replaces_id],
                             backref="replaced_by")
    parent    = relationship("Tariff", remote_side=[id], foreign_keys=[parent_id],
                             backref="adicionales")


Index("ix_tariffs_lookup", Tariff.scope, Tariff.service_id, Tariff.client_id, Tariff.is_active)
