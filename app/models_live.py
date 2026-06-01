"""
Modelos para el módulo Operativos en Tiempo Real.

Diseño central:
  - Una sesión live por barco (sin producto global).
  - Los productos viven en OperationLiveSessionProduct (uno por producto del barco).
  - Cada bodega en cada turno es una fila en OperationLiveBodegaData con su producto.
  - MTR y Cooperativa son columnas separadas en bodega_data — nunca se fusionan.
  - Sin UNIQUE constraint en bodega_data(shift_id, bodega_number): una bodega puede
    tener más de una fila si cambió de producto a mitad de turno o si hay dos cargas
    parciales distintas. Esto es explícitamente intencional.
"""

from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, Date
from sqlalchemy.orm import relationship
from app.database import Base


class OperationLiveSession(Base):
    """
    Sesión live de un operativo de barco.

    No tiene producto propio: los productos están en OperationLiveSessionProduct
    (uno por producto que lleva el barco). Esto resuelve multiproducto de raíz.

    operation_id es nullable: se puede linkear a la tabla operations (balanza)
    en Fase 2. No se usa en Fase 1.
    """
    __tablename__ = "operation_live_sessions"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    operation_id  = Column(
        Integer, ForeignKey("operations.id"), nullable=True, index=True
    )  # Fase 2: link a balanza. NULL en Fase 1.

    ship_name     = Column(String, nullable=False, index=True)
    status        = Column(String, nullable=False, default="active")
    # status: 'active' | 'paused' | 'finished'

    created_at    = Column(DateTime, default=datetime.utcnow)
    finished_at   = Column(DateTime, nullable=True)
    created_by    = Column(String, nullable=True)   # nombre libre, sin FK a users
    closed_by     = Column(String, nullable=True)
    closing_notes = Column(Text, nullable=True)

    products = relationship(
        "OperationLiveSessionProduct",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="OperationLiveSessionProduct.id",
    )
    shifts = relationship(
        "OperationLiveShift",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="OperationLiveShift.shift_number",
    )


class OperationLiveSessionProduct(Base):
    """
    Un producto del barco dentro de la sesión live.

    Cada producto tiene su propio cliente y sus propios kg contratados.
    Sin UNIQUE constraint: en casos excepcionales el mismo producto puede tener
    dos partidas con clientes distintos — se cargan como dos filas.

    product siempre se guarda normalizado vía normalize_product() para ser
    consistente con operation_trips y operation_cargo_summaries.
    """
    __tablename__ = "operation_live_session_products"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    session_id    = Column(
        Integer, ForeignKey("operation_live_sessions.id"), nullable=False, index=True
    )
    product       = Column(String, nullable=False)
    # ^ siempre normalizado vía normalize_product() al guardar
    client        = Column(String, nullable=True)
    kg_contracted = Column(Integer, nullable=True)
    # ^ NULL = "sin total cargado": la barra de progreso no se muestra
    notes         = Column(Text, nullable=True)

    session = relationship("OperationLiveSession", back_populates="products")


class OperationLiveShift(Base):
    """
    Un turno = un Parte Nº dentro de la sesión.

    Contiene los datos generales del turno (quién, cuándo, manos).
    Los datos de toneladas por bodega están en OperationLiveBodegaData.

    Los acumulados de la sesión incluyen TODOS los turnos (abiertos y cerrados)
    para que el dashboard muestre el operativo vivo en todo momento.
    Esto es comportamiento explícito e intencional.
    """
    __tablename__ = "operation_live_shifts"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    session_id       = Column(
        Integer, ForeignKey("operation_live_sessions.id"), nullable=False, index=True
    )
    shift_number     = Column(Integer, nullable=False)  # Parte Nº 1, 2, 3…
    shift_date       = Column(Date, nullable=False)
    shift_start      = Column(String, nullable=False)   # 'HH:MM'
    shift_end        = Column(String, nullable=True)    # NULL si turno abierto

    supervisor_mtr   = Column(String, nullable=True)
    apuntador        = Column(String, nullable=True)
    manos            = Column(Integer, nullable=True)   # guinches activos

    status           = Column(String, nullable=False, default="open")
    # status: 'open' | 'closed' | 'adjusted'

    await_adjustment = Column(Boolean, default=False)   # "esperar ajuste del despachante"
    is_final_shift   = Column(Boolean, default=False)   # "descarga finalizada en este turno"
    notes            = Column(Text, nullable=True)
    created_at       = Column(DateTime, default=datetime.utcnow)

    session    = relationship("OperationLiveSession", back_populates="shifts")
    bodega_data = relationship(
        "OperationLiveBodegaData",
        back_populates="shift",
        cascade="all, delete-orphan",
        order_by="OperationLiveBodegaData.bodega_number, OperationLiveBodegaData.id",
    )
    delays = relationship(
        "OperationLiveDelay",
        back_populates="shift",
        cascade="all, delete-orphan",
        order_by="OperationLiveDelay.desde",
    )
    equipment = relationship(
        "OperationLiveEquipment",
        back_populates="shift",
        cascade="all, delete-orphan",
        order_by="OperationLiveEquipment.desde",
    )
    staff = relationship(
        "OperationLiveStaff",
        back_populates="shift",
        cascade="all, delete-orphan",
        order_by="OperationLiveStaff.id",
    )


class OperationLiveBodegaData(Base):
    """
    Datos de toneladas por bodega por turno, separados por producto.

    Una fila por (turno, bodega, producto). Sin UNIQUE constraint:
    - Si una bodega cambia de producto a mitad de turno → dos filas.
    - Si se cargan datos parciales en dos momentos → dos filas.
    Esto es explícitamente intencional para no perder información.

    El producto SIEMPRE viene del dropdown de session_products.
    El POST valida server-side que el producto exista en session_products
    de esa sesión → 400 si no existe.

    MTR y Cooperativa son columnas separadas permanentemente.
    delta = kg_total_mtr - kg_coop se calcula en Python (live_utils.py),
    nunca se almacena en DB.

    Acumulados: el dashboard incluye TODAS las filas de la sesión
    (turnos abiertos y cerrados) para mostrar el operativo en tiempo real.
    """
    __tablename__ = "operation_live_bodega_data"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    shift_id         = Column(
        Integer, ForeignKey("operation_live_shifts.id"), nullable=False, index=True
    )
    bodega_number    = Column(Integer, nullable=False)
    product          = Column(String, nullable=False)
    # ^ normalize_product() aplicado al guardar; validado contra session_products

    measurement      = Column(String, default="fiscal")  # 'fiscal' | 'grampa'

    # ── Datos MTR (jefe de turno) ────────────────────────────────────────────
    viajes_mtr       = Column(Integer, nullable=True)
    kg_deposito_mtr  = Column(Integer, nullable=False, default=0)
    kg_directo_mtr   = Column(Integer, nullable=False, default=0)
    kg_cv_mtr        = Column(Integer, nullable=False, default=0)
    # kg_total_mtr = kg_deposito_mtr + kg_directo_mtr + kg_cv_mtr  (calculado en Python)

    # ── Datos Cooperativa (Parte Diario CPSN) ────────────────────────────────
    viajes_coop      = Column(Integer, nullable=True)
    kg_coop          = Column(Integer, nullable=True)
    # delta = kg_total_mtr - kg_coop  (calculado en Python)

    created_at       = Column(DateTime, default=datetime.utcnow)
    updated_at       = Column(DateTime, nullable=True, onupdate=datetime.utcnow)

    shift = relationship("OperationLiveShift", back_populates="bodega_data")


class OperationLiveDelay(Base):
    """
    Demora registrada dentro de un turno.

    hasta puede ser NULL si la demora aún está abierta al momento de cargarla.
    motivo_tipo es un enum soft (string) para poder filtrar y agrupar;
    motivo_texto es el campo libre siempre disponible.
    """
    __tablename__ = "operation_live_delays"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    shift_id      = Column(
        Integer, ForeignKey("operation_live_shifts.id"), nullable=False, index=True
    )
    bodega_number = Column(Integer, nullable=True)
    product       = Column(String, nullable=True)  # opcional: qué producto afectó
    desde         = Column(String, nullable=False)  # 'HH:MM'
    hasta         = Column(String, nullable=True)   # NULL = demora aún abierta
    motivo_tipo   = Column(String, nullable=True)
    # motivo_tipo: 'espera_estiba' | 'falta_camiones' | 'lluvia' | 'cambio_turno'
    #              'falla_equipo' | 'espera_ajuste' | 'falla_energia' | 'otro'
    motivo_texto  = Column(Text, nullable=True)
    created_at    = Column(DateTime, default=datetime.utcnow)

    shift = relationship("OperationLiveShift", back_populates="delays")


class OperationLiveEquipment(Base):
    """
    Equipo operando en el turno (pala, retro, autoelevador, guinche, etc.).
    Necesario para verificar la factura cooperativa (cobran por horas de máquina).
    """
    __tablename__ = "operation_live_equipment"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    shift_id      = Column(
        Integer, ForeignKey("operation_live_shifts.id"), nullable=False, index=True
    )
    empresa       = Column(String, nullable=True)   # 'MTR' | 'COOP' | texto libre
    tipo          = Column(String, nullable=True)
    # tipo: 'pala' | 'retro' | 'autoelevador' | 'guinche' | 'tractor' | 'otro'
    bodega_number = Column(Integer, nullable=True)
    desde         = Column(String, nullable=True)   # 'HH:MM'
    hasta         = Column(String, nullable=True)   # 'HH:MM'
    comentarios   = Column(Text, nullable=True)
    created_at    = Column(DateTime, default=datetime.utcnow)

    shift = relationship("OperationLiveShift", back_populates="equipment")


class OperationLiveStaff(Base):
    """
    Personal declarado por la cooperativa para este turno.
    Permite verificar la factura de mano de obra.
    """
    __tablename__ = "operation_live_staff"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    shift_id      = Column(
        Integer, ForeignKey("operation_live_shifts.id"), nullable=False, index=True
    )
    funcion       = Column(String, nullable=False)
    # funcion: 'guinchero' | 'limpieza' | 'pinche' | 'maquinista_retro'
    #           'maquinista_autoelevador' | 'apuntador' | 'otro'
    funcion_texto = Column(Text, nullable=True)    # si funcion = 'otro'
    cantidad      = Column(Integer, nullable=False, default=1)
    turno_range   = Column(String, nullable=True)  # '00A06' | '06A12' | '12A18' | '18A00'
    empresa       = Column(String, nullable=False, default="coop")  # 'coop' | 'mtr'
    created_at    = Column(DateTime, default=datetime.utcnow)

    shift = relationship("OperationLiveShift", back_populates="staff")
