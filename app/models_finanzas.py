"""
Módulo Finanzas — núcleo administrativo/financiero del ERP.

Tres piezas en esta primera etapa (Tesorería + maestros):
  - CentroCosto        → para imputar gastos/ingresos y armar presupuestos.
  - CuentaTesoreria    → cajas y cuentas bancarias (saldo real de la empresa).
  - MovimientoTesoreria → cada ingreso/egreso de plata, fuente de verdad del saldo.

Diseño:
  - El saldo de una cuenta = saldo_inicial + Σ(ingresos) − Σ(egresos) no anulados.
    NO se guarda un campo "saldo" mutable: se calcula. Evita descuadres.
  - Un movimiento puede vincularse a una compra (purchase_id), a un cliente o a
    un proveedor (contraparte), y a un centro de costo. Todo opcional.
  - Soft delete (deleted_at) para no romper históricos ni saldos auditables.

La condición de IVA de clientes y proveedores vive como columna en sus tablas
existentes (clients, suppliers) — ver migrate.py. No se duplica el maestro.
"""
from datetime import datetime, date
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Date,
    ForeignKey, Text, Numeric, Index,
)
from sqlalchemy.orm import relationship
from app.database import Base


# ── Catálogos (choices editables, no tablas) ───────────────────────────────────

# Condición frente al IVA (ARCA). Aplica a clientes y proveedores.
CONDICIONES_IVA = [
    ("responsable_inscripto", "Responsable Inscripto"),
    ("monotributo",           "Monotributo"),
    ("exento",                "Exento"),
    ("consumidor_final",      "Consumidor Final"),
    ("no_responsable",        "No Responsable"),
]
CONDICION_IVA_LABELS = dict(CONDICIONES_IVA)

TIPOS_CUENTA = [
    ("caja",  "Caja / Efectivo"),
    ("banco", "Cuenta bancaria"),
]
TIPO_CUENTA_LABELS = dict(TIPOS_CUENTA)

MONEDAS = [
    ("ARS", "Pesos (ARS)"),
    ("USD", "Dólares (USD)"),
]
MONEDA_LABELS = dict(MONEDAS)

TIPOS_MOVIMIENTO = [
    ("ingreso", "Ingreso"),
    ("egreso",  "Egreso"),
]
TIPO_MOVIMIENTO_LABELS = dict(TIPOS_MOVIMIENTO)

MEDIOS_PAGO = [
    ("efectivo",      "Efectivo"),
    ("transferencia", "Transferencia"),
    ("cheque",        "Cheque"),
    ("deposito",      "Depósito"),
    ("tarjeta",       "Tarjeta"),
    ("otro",          "Otro"),
]
MEDIO_PAGO_LABELS = dict(MEDIOS_PAGO)

# Tipo de contraparte de un movimiento (a quién le pagué / quién me pagó).
CONTRAPARTE_TIPOS = [
    ("cliente",    "Cliente"),
    ("proveedor",  "Proveedor"),
    ("interno",    "Interno / Transferencia"),
    ("otro",       "Otro"),
]
CONTRAPARTE_TIPO_LABELS = dict(CONTRAPARTE_TIPOS)


class CentroCosto(Base):
    """Centro de costo para imputar movimientos y, a futuro, presupuestar.

    Ej: ADMIN, FLOTA, PUERTO-MTR1, OBRA-XXX. Un proyecto puede mapear a uno.
    """
    __tablename__ = "centros_costo"

    id          = Column(Integer, primary_key=True, index=True)
    codigo      = Column(String(40), nullable=False, unique=True, index=True)
    nombre      = Column(String(200), nullable=False)
    plant       = Column(String, nullable=True)   # MTR1 | MTR2 | TODAS
    descripcion = Column(Text, nullable=True)
    activo      = Column(Boolean, default=True, index=True)
    created_at  = Column(DateTime, default=datetime.utcnow)

    movimientos = relationship("MovimientoTesoreria", back_populates="centro_costo")


class CuentaTesoreria(Base):
    """Caja o cuenta bancaria. El saldo se calcula, no se guarda."""
    __tablename__ = "cuentas_tesoreria"

    id            = Column(Integer, primary_key=True, index=True)
    nombre        = Column(String(200), nullable=False)
    tipo          = Column(String(20), nullable=False, default="caja")   # caja | banco
    banco         = Column(String(120), nullable=True)
    numero_cuenta = Column(String(60), nullable=True)
    cbu           = Column(String(40), nullable=True)
    alias_cbu     = Column(String(60), nullable=True)
    moneda        = Column(String(3), nullable=False, default="ARS")     # ARS | USD
    saldo_inicial = Column(Numeric(14, 2), nullable=False, default=0)
    activo        = Column(Boolean, default=True, index=True)
    notas         = Column(Text, nullable=True)
    created_at    = Column(DateTime, default=datetime.utcnow)
    updated_at    = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    movimientos = relationship(
        "MovimientoTesoreria",
        back_populates="cuenta",
        cascade="all, delete-orphan",
    )


class MovimientoTesoreria(Base):
    """Cada ingreso/egreso de dinero. Fuente de verdad del saldo de una cuenta."""
    __tablename__ = "movimientos_tesoreria"

    id           = Column(Integer, primary_key=True, index=True)
    cuenta_id    = Column(Integer, ForeignKey("cuentas_tesoreria.id"), nullable=False, index=True)
    fecha        = Column(Date, nullable=False, index=True)
    tipo         = Column(String(10), nullable=False)   # ingreso | egreso
    concepto     = Column(String(300), nullable=False)
    monto        = Column(Numeric(14, 2), nullable=False)
    moneda       = Column(String(3), nullable=False, default="ARS")
    medio        = Column(String(20), nullable=True)    # efectivo | transferencia | cheque...
    referencia   = Column(String(120), nullable=True)   # nº comprobante / cheque / transferencia

    # Contraparte (opcional): a quién le pagué o quién me pagó.
    contraparte_tipo = Column(String(20), nullable=True)  # cliente | proveedor | interno | otro
    client_id        = Column(Integer, ForeignKey("clients.id"),   nullable=True, index=True)
    supplier_id      = Column(Integer, ForeignKey("suppliers.id"), nullable=True, index=True)
    contraparte_text = Column(String(200), nullable=True)  # si no está en maestros

    # Vínculos opcionales con otros módulos.
    purchase_id     = Column(Integer, ForeignKey("purchases.id"),    nullable=True, index=True)
    centro_costo_id = Column(Integer, ForeignKey("centros_costo.id"), nullable=True, index=True)

    conciliado    = Column(Boolean, default=False, index=True)
    notas         = Column(Text, nullable=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at    = Column(DateTime, default=datetime.utcnow)
    deleted_at    = Column(DateTime, nullable=True, index=True)

    cuenta       = relationship("CuentaTesoreria", back_populates="movimientos")
    centro_costo = relationship("CentroCosto", back_populates="movimientos")
    client       = relationship("Client")
    supplier     = relationship("Supplier")
    purchase     = relationship("Purchase")
    created_by   = relationship("User")


# Índice compuesto para listados por cuenta + fecha (lo más consultado).
Index("ix_mov_cuenta_fecha", MovimientoTesoreria.cuenta_id, MovimientoTesoreria.fecha)
