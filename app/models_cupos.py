"""
Módulo Despachos — Cupos y Despachos operativos de camiones.

Soporta dos fuentes de importación:
  - Nutrien (cupos de carga programados)
  - CNA    (despachos reales IN/OUT con trazabilidad completa)
"""
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, ForeignKey,
    Text, Date, Numeric, Index,
)
from sqlalchemy.orm import relationship
from app.database import Base


# ── Estados posibles ──────────────────────────────────────────────────────────
DESPACHO_ESTADOS = [
    ("programado",   "Programado"),
    ("arribo",       "Arribó"),
    ("cargado",      "Cargado"),
    ("no_vino",      "No vino"),
    ("reprogramado", "Reprogramado"),
    ("cancelado",    "Cancelado"),
    ("parcial",      "Parcial"),
    ("novedad",      "Con novedad"),
]

DESPACHO_ESTADO_LABELS = dict(DESPACHO_ESTADOS)

# CSS badge classes por estado
ESTADO_CSS = {
    "programado":   "bg-blue-50 text-blue-700 border border-blue-200",
    "arribo":       "bg-amber-50 text-amber-700 border border-amber-200",
    "cargado":      "bg-emerald-50 text-emerald-700 border border-emerald-200",
    "no_vino":      "bg-red-50 text-red-700 border border-red-200",
    "reprogramado": "bg-purple-50 text-purple-700 border border-purple-200",
    "cancelado":    "bg-gray-100 text-gray-500 border border-gray-200",
    "parcial":      "bg-orange-50 text-orange-700 border border-orange-200",
    "novedad":      "bg-yellow-50 text-yellow-700 border border-yellow-200",
}


class ImportBatch(Base):
    """Registra cada importación de Excel como un lote identificable."""
    __tablename__ = "despacho_import_batches"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    batch_uuid  = Column(String(36), unique=True, nullable=False, index=True)
    source_type = Column(String(20), nullable=False)   # 'nutrien' | 'cna'
    filename    = Column(String(300))
    sheet_name  = Column(String(200))
    row_count   = Column(Integer, default=0)
    imported_at = Column(DateTime, default=datetime.utcnow)
    imported_by = Column(String(120))                  # user name

    registros = relationship("CupoDespacho", back_populates="batch",
                             cascade="all, delete-orphan")


class CupoDespacho(Base):
    """
    Registro unificado de cupo (Nutrien) o despacho (CNA).

    Fecha programada vs fecha real:
      scheduled_date  = fecha que vino en el archivo (plan original)
      actual_date     = fecha en que realmente operó (puede ser None o diferir)
    """
    __tablename__ = "cupos_despachos"

    id          = Column(Integer, primary_key=True, autoincrement=True)

    # ── Import metadata ───────────────────────────────────────────────────────
    batch_id    = Column(Integer, ForeignKey("despacho_import_batches.id",
                                             ondelete="CASCADE"), nullable=True,
                         index=True)
    source_type = Column(String(20), nullable=False, index=True)
    # 'nutrien' | 'cna'
    document_type = Column(String(20))
    # 'cupo' | 'despacho'
    row_hash    = Column(String(64), index=True)
    # SHA256 de campos clave — para detectar duplicados

    # ── Fechas ────────────────────────────────────────────────────────────────
    scheduled_date = Column(Date, index=True)    # fecha del archivo (plan)
    actual_date    = Column(Date, nullable=True) # fecha real operada

    # ── Referencias ──────────────────────────────────────────────────────────
    st_sd_od       = Column(String(100), index=True)  # Nutrien: ST/SD/OD
    external_ref   = Column(String(100))              # NP o FC (CNA) / ST/SD (Nutrien)
    order_number   = Column(String(100))              # OC
    remito         = Column(String(100))

    # ── Cliente / Destino ─────────────────────────────────────────────────────
    cliente        = Column(String(300))
    cuit_cliente   = Column(String(30))
    destinatario   = Column(String(300))          # Nutrien campo específico
    destino        = Column(String(400))           # CNA campo específico
    ac             = Column(String(200))           # Nutrien: agrocentro

    # ── Producto ──────────────────────────────────────────────────────────────
    producto       = Column(String(200), index=True)
    cantidad_mt    = Column(Numeric(12, 3))       # toneladas métricas (Nutrien)
    kg_oc          = Column(Numeric(14, 2))       # kg de la OC (CNA)
    neto           = Column(Numeric(14, 2))       # neto real despachado
    presentacion   = Column(String(100))          # Granel, Big Bag, Bolsas 50kg…

    # ── Origen / Modo ─────────────────────────────────────────────────────────
    origen         = Column(String(200))
    in_out         = Column(String(10))            # IN | OUT (CNA)
    modo_transporte = Column(String(50))           # C04, C08, etc. (Nutrien BASE)

    # ── Transporte ────────────────────────────────────────────────────────────
    transporte     = Column(String(300))
    cuit_transporte = Column(String(30))

    # ── Chofer ────────────────────────────────────────────────────────────────
    chofer         = Column(String(200))
    dni_chofer     = Column(String(20))
    patente_chasis  = Column(String(20))
    patente_acoplado = Column(String(20))

    # ── Estado y seguimiento ─────────────────────────────────────────────────
    status         = Column(String(30), default="programado", index=True)
    notes          = Column(Text)

    # ── Reprogramación ────────────────────────────────────────────────────────
    reprogrammed_from_id = Column(Integer, ForeignKey("cupos_despachos.id"),
                                  nullable=True)
    reprogrammed_to_date = Column(Date, nullable=True)

    # ── Auditoría ─────────────────────────────────────────────────────────────
    imported_by    = Column(String(120))
    created_at     = Column(DateTime, default=datetime.utcnow)
    updated_at     = Column(DateTime, default=datetime.utcnow,
                            onupdate=datetime.utcnow)

    # ── Relaciones ────────────────────────────────────────────────────────────
    batch      = relationship("ImportBatch", back_populates="registros")
    reprog_origin = relationship("CupoDespacho", foreign_keys=[reprogrammed_from_id],
                                 remote_side=[id], uselist=False)


# Índice compuesto para filtros frecuentes
Index("ix_cupos_fecha_status", CupoDespacho.scheduled_date, CupoDespacho.status)
Index("ix_cupos_source_fecha",  CupoDespacho.source_type,   CupoDespacho.scheduled_date)
