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

Fase 2 — Cierre + Factura cooperativa + Conciliación:
  - OperationLiveInvoice: cabecera de factura cooperativa (status: draft→reviewed→approved_for_payment)
  - OperationLiveInvoiceTonnageLine: líneas del parte diario con dato RECIBIDO y dato REVISADO MTR.
  - OperationLiveInvoiceLaborLine: líneas de jornales con dato RECIBIDO y dato REVISADO MTR.
  - OperationLiveInvoiceCargoLine: ítems especiales (pala, ADM, etc.).
  - OperationLiveInvoiceTotals: resumen económico calculado + declarado.
  - OperationLiveReconciliation: dictamen final por producto (usa SIEMPRE _revisado, nunca _recibido).

  Regla central: ningún dato del parte cooperativo llega a la conciliación
  sin pasar por revisión humana de MTR (invoice_review). Los campos _revisado
  son NULL hasta que MTR los confirme/corrija explícitamente.

Naming: tablas en plural para entidades contables, mass-noun para colectivos.
  Tabla principal: operation_live_sessions (ya existente).
"""

from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, Date, Numeric, UniqueConstraint
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

    # Fase 2: timestamps de cierre formal y reconciliación
    # status avanza: active → closed → reconciled
    closed_at     = Column(DateTime, nullable=True)
    reconciled_at = Column(DateTime, nullable=True)

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
    photos = relationship(
        "OperationLivePhoto",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="OperationLivePhoto.created_at",
        primaryjoin="OperationLivePhoto.session_id == OperationLiveSession.id",
    )
    invoices = relationship(
        "OperationLiveInvoice",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="OperationLiveInvoice.id",
    )
    reconciliations = relationship(
        "OperationLiveReconciliation",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="OperationLiveReconciliation.product",
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

    # Fase 2: tipo de jornada para clasificación tarifaria en factura cooperativa.
    # El usuario lo confirma EXPLÍCITAMENTE al crear/cerrar el turno — sin derivación automática.
    # Valores: 'habil' | 'inhabil' | 'extraordinario'
    turno_tipo       = Column(String, nullable=False, default="habil", server_default="habil")

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
    photos = relationship(
        "OperationLivePhoto",
        back_populates="shift",
        cascade="all, delete-orphan",
        order_by="OperationLivePhoto.created_at",
        primaryjoin="OperationLivePhoto.shift_id == OperationLiveShift.id",
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
    tipo_guinche     = Column(String, nullable=True)      # 'fiscal' | 'abordo'
    tipo_grampa      = Column(String, nullable=True)      # 'fiscal' | 'abordo'

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


# ── Fase 2: Factura Cooperativa + Conciliación ────────────────────────────────
#
# Flujo de estados de la factura:
#   draft → reviewed → approved_for_payment
#   (cualquier estado puede volver a 'disputed' si MTR abre una observación)
#
# Regla de oro: la conciliación SIEMPRE usa _revisado, nunca _recibido.
# Los campos _revisado son NULL hasta que MTR los confirme/corrija en invoice_review.
# Una factura no puede pasar a 'reviewed' sin que MTR revise línea por línea.


class OperationLiveInvoice(Base):
    """
    Cabecera de la factura de la cooperativa.

    Una sesión puede tener a lo sumo UNA factura activa (status != 'disputed').
    Si hay una factura en 'disputed', se puede crear una nueva versión.

    upload_method='photo_assisted' está reservado para Fase 3 (extracción por foto).
    En Fase 2, solo se usa 'manual'.
    """
    __tablename__ = "operation_live_invoices"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    session_id     = Column(
        Integer, ForeignKey("operation_live_sessions.id"), nullable=False, index=True
    )

    invoice_number = Column(String, nullable=True)    # nro del documento, nullable hasta tenerlo
    invoice_date   = Column(Date, nullable=True)
    upload_method  = Column(String, nullable=False, default="manual")
    # upload_method: 'manual' | 'photo_assisted' (Fase 3)

    # Estado del flujo de revisión MTR
    status = Column(String, nullable=False, default="draft")
    # status: 'draft' | 'reviewed' | 'approved_for_payment' | 'disputed'

    # Auditoría de revisión MTR (paso obligatorio antes de 'reviewed')
    reviewed_by  = Column(String, nullable=True)
    reviewed_at  = Column(DateTime, nullable=True)

    # Auditoría de validación oficial
    validated_by = Column(String, nullable=True)
    validated_at = Column(DateTime, nullable=True)

    # Notas internas: qué dijo el parte, discrepancias, contexto operativo
    origen_nota  = Column(Text, nullable=True)

    created_at   = Column(DateTime, default=datetime.utcnow)

    session       = relationship("OperationLiveSession", back_populates="invoices")
    tonnage_lines = relationship(
        "OperationLiveInvoiceTonnageLine",
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="OperationLiveInvoiceTonnageLine.id",
    )
    labor_lines   = relationship(
        "OperationLiveInvoiceLaborLine",
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="OperationLiveInvoiceLaborLine.id",
    )
    cargo_lines   = relationship(
        "OperationLiveInvoiceCargoLine",
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="OperationLiveInvoiceCargoLine.id",
    )
    totals        = relationship(
        "OperationLiveInvoiceTotals",
        back_populates="invoice",
        uselist=False,
        cascade="all, delete-orphan",
    )


class OperationLiveInvoiceTonnageLine(Base):
    """
    Una línea del Parte Diario de la factura cooperativa.

    Separación explícita entre dato recibido (del parte cooperativo) y
    dato revisado (confirmado/corregido por MTR).

    INVARIANTE: la conciliación usa SOLO los campos _revisado.
    Si _revisado es NULL, la línea no está revisada y no entra al cálculo.

    product es nullable: la factura cooperativa no siempre especifica
    el producto por línea. Si viene vacío, reconciliation_data() intentará
    inferirlo por contexto del operativo. Si no puede, lo marca como
    'SIN_PRODUCTO' y lo excluye de la conciliación automática.
    """
    __tablename__ = "operation_live_invoice_tonnage_lines"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    invoice_id = Column(
        Integer, ForeignKey("operation_live_invoices.id"), nullable=False, index=True
    )

    # Identificación de la línea en el parte diario
    shift_date          = Column(Date, nullable=False)
    turno_range         = Column(String, nullable=False)    # "07-19", "19-07"
    guinche_tipo        = Column(String, nullable=False)    # 'abordo' | 'fiscal'
    guinche_descripcion = Column(String, nullable=True)     # para casos atípicos

    # Producto — nullable: no siempre viene en la factura por línea
    product = Column(String, nullable=True)

    # ── DATO RECIBIDO (parte cooperativo tal como llegó a MTR) ────────────
    tns_habiles_recibido          = Column(Numeric(10, 2), nullable=True)
    tns_inhabiles_recibido        = Column(Numeric(10, 2), nullable=True)
    tns_extraordinarias_recibido  = Column(Numeric(10, 2), nullable=True)
    manos_recibido                = Column(Integer, nullable=True)

    # ── DATO REVISADO MTR (confirmado/corregido por gente de MTR) ─────────
    # NULL = aún no revisado; igual al recibido = confirmado; distinto = corregido
    tns_habiles_revisado          = Column(Numeric(10, 2), nullable=True)
    tns_inhabiles_revisado        = Column(Numeric(10, 2), nullable=True)
    tns_extraordinarias_revisado  = Column(Numeric(10, 2), nullable=True)
    manos_revisado                = Column(Integer, nullable=True)

    # Tarifas (del documento; se disputan a nivel de invoice, no de línea)
    tarifa_habiles           = Column(Numeric(12, 2), nullable=True)
    tarifa_inhabiles         = Column(Numeric(12, 2), nullable=True)
    tarifa_extraordinarias   = Column(Numeric(12, 2), nullable=True)

    # Auditoría de la revisión
    diferencia_nota = Column(Text, nullable=True)    # por qué se corrigió si hubo diferencia
    revisado_por    = Column(String, nullable=True)
    revisado_at     = Column(DateTime, nullable=True)

    invoice = relationship("OperationLiveInvoice", back_populates="tonnage_lines")


class OperationLiveInvoiceLaborLine(Base):
    """
    Una línea de jornales de la factura cooperativa.

    Mismo patrón de dualidad recibido/revisado que las líneas de tonelaje.
    MTR revisa cantidad y precio unitario por función y turno.
    """
    __tablename__ = "operation_live_invoice_labor_lines"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    invoice_id = Column(
        Integer, ForeignKey("operation_live_invoices.id"), nullable=False, index=True
    )

    shift_date  = Column(Date, nullable=False)
    turno_range = Column(String, nullable=False)
    turno_tipo  = Column(String, nullable=False, default="habil")
    # turno_tipo: 'habil' | 'inhabil' | 'extraordinario'

    funcion       = Column(String, nullable=False)
    # funcion: 'guinchero' | 'maquinista' | 'palero' | 'otro'
    funcion_texto = Column(String, nullable=True)    # si funcion = 'otro'

    # ── DATO RECIBIDO ─────────────────────────────────────────────────────
    cantidad_recibido        = Column(Integer, nullable=True)
    precio_unitario_recibido = Column(Numeric(12, 2), nullable=True)

    # ── DATO REVISADO MTR ─────────────────────────────────────────────────
    cantidad_revisado        = Column(Integer, nullable=True)
    precio_unitario_revisado = Column(Numeric(12, 2), nullable=True)

    diferencia_nota = Column(Text, nullable=True)
    revisado_por    = Column(String, nullable=True)
    revisado_at     = Column(DateTime, nullable=True)

    invoice = relationship("OperationLiveInvoice", back_populates="labor_lines")


class OperationLiveInvoiceCargoLine(Base):
    """
    Ítems especiales de la factura: pala a la orden, horas pala, ADM, otros.

    Sin dualidad recibido/revisado: son hechos del documento.
    Si MTR los disputa, lo hace a nivel de invoice.status = 'disputed' + nota.
    """
    __tablename__ = "operation_live_invoice_cargo_lines"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    invoice_id = Column(
        Integer, ForeignKey("operation_live_invoices.id"), nullable=False, index=True
    )

    tipo        = Column(String, nullable=False, default="otro")
    # tipo: 'pala_orden' | 'pala_horas' | 'adm_resumen' | 'otro'
    descripcion     = Column(String, nullable=False)
    cantidad        = Column(Numeric(10, 3), nullable=True)
    unidad          = Column(String, nullable=True)      # 'uds', 'hs', etc.
    precio_unitario = Column(Numeric(12, 2), nullable=True)
    subtotal        = Column(Numeric(14, 2), nullable=True)   # guardado, no solo calculado

    invoice = relationship("OperationLiveInvoice", back_populates="cargo_lines")


class OperationLiveInvoiceTotals(Base):
    """
    Resumen económico de la factura. Una sola fila por factura (uselist=False).

    Guarda el total declarado (ingresado por MTR del PDF) y el total calculado
    (computado por invoice_utils.calculate_invoice_totals()).
    La diferencia entre ambos es la validación económica antes de aprobar.

    Las bases _recibido y _revisado se guardan separadas para trazabilidad.
    """
    __tablename__ = "operation_live_invoice_totals"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    invoice_id = Column(
        Integer, ForeignKey("operation_live_invoices.id"), unique=True, nullable=False
    )

    # Bases — trazabilidad de recibido vs revisado
    base_tonelaje_recibido  = Column(Numeric(14, 2), nullable=True)
    base_jornales_recibido  = Column(Numeric(14, 2), nullable=True)
    base_tonelaje_revisado  = Column(Numeric(14, 2), nullable=True)
    base_jornales_revisado  = Column(Numeric(14, 2), nullable=True)

    # Porcentajes (configurables por operativo)
    supa_pct         = Column(Numeric(5, 2), nullable=False, default=9.5)
    contrib_coop_pct = Column(Numeric(5, 2), nullable=False, default=85.0)
    iva_pct          = Column(Numeric(5, 2), nullable=False, default=21.0)
    iibb_pct         = Column(Numeric(5, 2), nullable=True)

    # Montos calculados
    supa_monto         = Column(Numeric(14, 2), nullable=True)
    contrib_coop_monto = Column(Numeric(14, 2), nullable=True)
    adm_total          = Column(Numeric(14, 2), nullable=True)
    iva_monto          = Column(Numeric(14, 2), nullable=True)
    iibb_monto         = Column(Numeric(14, 2), nullable=True)

    # Total del documento vs total calculado por el sistema
    total_factura_declarado  = Column(Numeric(14, 2), nullable=True)   # del PDF, ingresado por MTR
    total_factura_calculado  = Column(Numeric(14, 2), nullable=True)   # calculado automáticamente

    invoice = relationship("OperationLiveInvoice", back_populates="totals")


class OperationLiveReconciliation(Base):
    """
    Dictamen de conciliación por producto.

    INVARIANTE CENTRAL: tns_coop_*_revisado viene EXCLUSIVAMENTE de los
    campos _revisado de invoice_tonnage_lines. Nunca de _recibido.
    Si alguna línea tiene _revisado = NULL, no entra al cálculo y la
    reconciliación de ese producto queda en status='pendiente'.

    Una fila por (session, product). UniqueConstraint lo garantiza.
    """
    __tablename__ = "operation_live_reconciliations"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(
        Integer, ForeignKey("operation_live_sessions.id"), nullable=False, index=True
    )

    product = Column(String, nullable=False)

    # Fuente MTR — suma de bodega_data del operativo (dato propio de MTR)
    tns_mtr = Column(Numeric(10, 2), nullable=True)

    # Fuente cooperativa — SIEMPRE desde datos _revisado de invoice_tonnage_lines
    tns_coop_abordo_revisado = Column(Numeric(10, 2), nullable=True)
    tns_coop_fiscal_revisado = Column(Numeric(10, 2), nullable=True)
    tns_coop_total_revisado  = Column(Numeric(10, 2), nullable=True)

    # Diferencias calculadas
    diferencia_abordo = Column(Numeric(10, 2), nullable=True)   # tns_mtr - tns_coop_abordo
    diferencia_fiscal = Column(Numeric(10, 2), nullable=True)   # tns_mtr - tns_coop_fiscal

    # Dictamen MTR
    status = Column(String, nullable=False, default="pendiente")
    # status: 'pendiente' | 'aprobado' | 'observado' | 'ajustado'
    observacion  = Column(Text, nullable=True)
    ajuste_final = Column(Numeric(10, 2), nullable=True)    # valor acordado si status='ajustado'

    dictamen_by = Column(String, nullable=True)
    dictamen_at = Column(DateTime, nullable=True)

    session = relationship("OperationLiveSession", back_populates="reconciliations")

    __table_args__ = (
        UniqueConstraint(
            "session_id", "product",
            name="uq_reconciliation_session_product",
        ),
    )


class OperationLivePhoto(Base):
    """
    Foto asociada a un operativo live.

    Niveles de asociación:
      - session_id siempre presente (foto pertenece al operativo)
      - shift_id nullable: si presente, foto vinculada a ese turno
      - bodega_number nullable: si presente, foto de esa bodega específica

    public_id guarda el Cloudinary public_id para poder borrar desde la API.
    """
    __tablename__ = "operation_live_photos"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    session_id     = Column(
        Integer, ForeignKey("operation_live_sessions.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    shift_id       = Column(
        Integer, ForeignKey("operation_live_shifts.id", ondelete="CASCADE"),
        nullable=True, index=True,
    )
    bodega_number  = Column(Integer, nullable=True)

    file_url       = Column(Text, nullable=False)
    public_id      = Column(String(255), nullable=False)  # Cloudinary public_id

    caption        = Column(Text, nullable=True)
    uploaded_by    = Column(String(120), nullable=True)   # nombre del usuario

    # Categoría opcional para ordenar visualmente
    # Valores sugeridos: 'barco' | 'tapas' | 'mercaderia' | 'equipos' | 'demora' | 'otro'
    category       = Column(String(60), nullable=True)

    created_at     = Column(DateTime, default=datetime.utcnow)

    session = relationship(
        "OperationLiveSession",
        back_populates="photos",
        foreign_keys=[session_id],
    )
    shift = relationship(
        "OperationLiveShift",
        back_populates="photos",
        foreign_keys=[shift_id],
    )
