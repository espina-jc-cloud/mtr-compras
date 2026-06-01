from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, Numeric, UniqueConstraint
from sqlalchemy.orm import relationship
from app.database import Base

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False, index=True)
    hashed_password = Column(String, nullable=False)
    role = Column(String, nullable=False)  # planta | autorizador | admin | superadmin
    plant = Column(String, nullable=False)  # MTR1 | MTR2 | ROSARIO | TODAS
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    purchases_requested = relationship("Purchase", foreign_keys="Purchase.requested_by_id", back_populates="requester")
    purchases_authorized = relationship("Purchase", foreign_keys="Purchase.authorized_by_id", back_populates="authorizer")

class Supplier(Base):
    __tablename__ = "suppliers"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    cuit = Column(String)
    contact_name = Column(String)
    contact_phone = Column(String)
    email = Column(String)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    purchases = relationship("Purchase", back_populates="supplier")

class Purchase(Base):
    __tablename__ = "purchases"
    id = Column(Integer, primary_key=True, index=True)
    plant = Column(String, nullable=False)  # MTR1 | MTR2
    area = Column(String, nullable=False)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=False)
    description = Column(Text, nullable=False)
    reason = Column(Text, nullable=False)
    estimated_amount = Column(Numeric(12, 2))
    actual_amount = Column(Numeric(12, 2))
    requested_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    requested_at = Column(DateTime, default=datetime.utcnow)
    authorized_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    authorized_at = Column(DateTime, nullable=True)
    status = Column(String, default="pendiente")
    rejection_reason = Column(Text)
    purchase_order_ref = Column(String)
    notes = Column(Text)
    amount_alert = Column(Boolean, default=False)
    purchase_date = Column(DateTime, nullable=True)   # fecha real de compra
    deleted_at = Column(DateTime, nullable=True)      # soft delete
    deleted_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    supplier = relationship("Supplier", back_populates="purchases")
    requester = relationship("User", foreign_keys=[requested_by_id], back_populates="purchases_requested")
    authorizer = relationship("User", foreign_keys=[authorized_by_id], back_populates="purchases_authorized")
    documents = relationship("Document", back_populates="purchase", cascade="all, delete-orphan")
    audit_logs = relationship("AuditLog", back_populates="purchase", cascade="all, delete-orphan")
    quote = relationship("Quote", back_populates="purchase", uselist=False, foreign_keys="Quote.purchase_id")

class Document(Base):
    __tablename__ = "documents"
    id = Column(Integer, primary_key=True, index=True)
    purchase_id = Column(Integer, ForeignKey("purchases.id"), nullable=False)
    doc_type = Column(String, nullable=False)  # remito | factura | otro
    file_url = Column(String, nullable=False)
    filename = Column(String, nullable=False)
    invoice_number = Column(String)   # usado también como nº de remito
    invoice_date = Column(String)
    invoice_amount = Column(Numeric(12, 2))
    remito_date = Column(String, nullable=True)  # fecha del remito
    uploaded_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    uploaded_at = Column(DateTime, default=datetime.utcnow)

    purchase = relationship("Purchase", back_populates="documents")
    uploader = relationship("User")

class AuditLog(Base):
    __tablename__ = "audit_log"
    id = Column(Integer, primary_key=True, index=True)
    purchase_id = Column(Integer, ForeignKey("purchases.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    action = Column(String, nullable=False)
    old_status = Column(String)
    new_status = Column(String)
    comment = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    purchase = relationship("Purchase", back_populates="audit_logs")
    user = relationship("User")


class Quote(Base):
    __tablename__ = "quotes"
    id = Column(Integer, primary_key=True, index=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=True)
    supplier_name_text = Column(String, nullable=True)   # texto libre si no está en sistema
    plant = Column(String, nullable=False)
    area = Column(String, nullable=False)
    requested_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    quote_date = Column(DateTime, nullable=False)
    valid_until = Column(DateTime, nullable=True)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    estimated_total = Column(Numeric(12, 2), nullable=True)
    currency = Column(String, default="ARS")  # ARS / USD
    status = Column(String, default="borrador")
    notes = Column(Text, nullable=True)
    purchase_id = Column(Integer, ForeignKey("purchases.id"), nullable=True)
    deleted_at = Column(DateTime, nullable=True)
    deleted_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    supplier = relationship("Supplier", foreign_keys=[supplier_id])
    requester = relationship("User", foreign_keys=[requested_by_id])
    purchase = relationship("Purchase", foreign_keys=[purchase_id], back_populates="quote")
    documents = relationship("QuoteDocument", back_populates="quote", cascade="all, delete-orphan")
    items = relationship("QuoteItem", back_populates="quote", cascade="all, delete-orphan", order_by="QuoteItem.id")
    audit_logs = relationship("QuoteAuditLog", back_populates="quote", cascade="all, delete-orphan")


class QuoteDocument(Base):
    __tablename__ = "quote_documents"
    id = Column(Integer, primary_key=True, index=True)
    quote_id = Column(Integer, ForeignKey("quotes.id"), nullable=False)
    file_url = Column(String, nullable=False)
    filename = Column(String, nullable=False)
    doc_type = Column(String, default="pdf")   # pdf / imagen / otro
    uploaded_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    uploaded_at = Column(DateTime, default=datetime.utcnow)

    quote = relationship("Quote", back_populates="documents")
    uploader = relationship("User")


class QuoteItem(Base):
    __tablename__ = "quote_items"
    id = Column(Integer, primary_key=True, index=True)
    quote_id = Column(Integer, ForeignKey("quotes.id"), nullable=False)
    description = Column(String, nullable=False)
    quantity = Column(Numeric(10, 3), nullable=True)
    unit = Column(String, nullable=True)
    unit_price = Column(Numeric(12, 2), nullable=True)
    subtotal = Column(Numeric(12, 2), nullable=True)

    quote = relationship("Quote", back_populates="items")


class QuoteAuditLog(Base):
    __tablename__ = "quote_audit_log"
    id = Column(Integer, primary_key=True, index=True)
    quote_id = Column(Integer, ForeignKey("quotes.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    action = Column(String, nullable=False)
    comment = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    quote = relationship("Quote", back_populates="audit_logs")
    user = relationship("User")


# ─── Mantenimiento ────────────────────────────────────────────────────────────

class Equipment(Base):
    __tablename__ = "equipment"
    id = Column(Integer, primary_key=True, index=True)
    code = Column(String, nullable=False, unique=True)   # MC1, CG5, A7
    name = Column(String, nullable=False)
    plant = Column(String, nullable=False)               # MTR1 / MTR2
    category = Column(String, nullable=True)             # fijo / flota / infraestructura
    work_type_code = Column(Integer, nullable=True)      # 261-266
    brand = Column(String, nullable=True)
    model_name = Column(String, nullable=True)           # "model" reservado en Python
    active = Column(Boolean, default=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    records = relationship("MaintenanceRecord", back_populates="equipment")


class MaintenanceRecord(Base):
    __tablename__ = "maintenance_records"
    id = Column(Integer, primary_key=True, index=True)
    plant = Column(String, nullable=False)
    equipment_id = Column(Integer, ForeignKey("equipment.id"), nullable=True)
    equipment_text = Column(String, nullable=True)       # texto libre si no está en sistema
    location_text = Column(String, nullable=True)        # "Foso cinta 3", "Techo nave 2"
    work_type_code = Column(Integer, nullable=True)      # 261-266
    maintenance_type = Column(String, default="correctivo")  # correctivo / preventivo
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    work_date = Column(DateTime, nullable=False)         # fecha real del trabajo
    performed_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    performed_by_text = Column(String, nullable=True)    # "Raúl", "Empresa X"
    contractor_company = Column(String, nullable=True)
    is_contractor = Column(Boolean, default=False)
    entered_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    supervised_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    did_lubrication = Column(Boolean, nullable=True)
    did_cleaning = Column(Boolean, nullable=True)
    hours_worked = Column(Numeric(6, 2), nullable=True)
    workers_count = Column(Integer, nullable=True, default=1)
    hourly_rate = Column(Numeric(12, 2), nullable=True)
    labor_cost = Column(Numeric(12, 2), nullable=True)
    parts_cost = Column(Numeric(12, 2), nullable=True)
    total_cost = Column(Numeric(12, 2), nullable=True)
    status = Column(String, default="cerrado")           # abierto / en_progreso / cerrado
    purchase_id = Column(Integer, ForeignKey("purchases.id"), nullable=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=True)
    deleted_at = Column(DateTime, nullable=True)
    deleted_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    equipment = relationship("Equipment", back_populates="records")
    performer = relationship("User", foreign_keys=[performed_by_id])
    entered_by = relationship("User", foreign_keys=[entered_by_id])
    supervised_by = relationship("User", foreign_keys=[supervised_by_id])
    purchase = relationship("Purchase", foreign_keys=[purchase_id])
    supplier = relationship("Supplier", foreign_keys=[supplier_id])
    documents = relationship("MaintenanceDocument", back_populates="record", cascade="all, delete-orphan")
    audit_logs = relationship("MaintenanceAuditLog", back_populates="record", cascade="all, delete-orphan")


class MaintenanceDocument(Base):
    __tablename__ = "maintenance_documents"
    id = Column(Integer, primary_key=True, index=True)
    record_id = Column(Integer, ForeignKey("maintenance_records.id"), nullable=False)
    file_url = Column(String, nullable=False)
    filename = Column(String, nullable=False)
    doc_type = Column(String, default="foto")    # foto / pdf / remito / otro
    uploaded_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    uploaded_at = Column(DateTime, default=datetime.utcnow)

    record = relationship("MaintenanceRecord", back_populates="documents")
    uploader = relationship("User")


class MaintenanceAuditLog(Base):
    __tablename__ = "maintenance_audit_log"
    id = Column(Integer, primary_key=True, index=True)
    record_id = Column(Integer, ForeignKey("maintenance_records.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    action = Column(String, nullable=False)
    comment = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    record = relationship("MaintenanceRecord", back_populates="audit_logs")
    user = relationship("User")


# ─── Combustible ──────────────────────────────────────────────────────────────

class FuelLoad(Base):
    __tablename__ = "fuel_loads"
    id               = Column(Integer, primary_key=True, index=True)

    fuel_date        = Column(DateTime, nullable=False, index=True)   # fecha real de carga

    responsible_text = Column(String, nullable=False)                 # quien cargó (libre)
    entered_by_id    = Column(Integer, ForeignKey("users.id"), nullable=False)

    vehicle_plate    = Column(String, nullable=False, index=True)     # patente o "Bidón"/"PASTO"
    vehicle_type     = Column(String, default="vehiculo")             # vehiculo/bidon/equipo

    fuel_type        = Column(String, nullable=False)                 # gasoil_premium/nafta/nafta_premium
    liters           = Column(Numeric(8, 3), nullable=False)
    station          = Column(String, default="Hipolito")
    amount           = Column(Numeric(12, 2), nullable=True)

    company          = Column(String, nullable=False, index=True)     # MTR SA / INGEE
    plant            = Column(String, nullable=True)                  # MTR1 / MTR2 (solo MTR SA)
    order_number     = Column(String, nullable=True)

    receipt_url      = Column(String, nullable=True)
    receipt_filename = Column(String, nullable=True)

    odometer_km      = Column(Integer, nullable=True)
    hourmeter        = Column(Numeric(8, 1), nullable=True)
    notes            = Column(Text, nullable=True)

    deleted_at       = Column(DateTime, nullable=True)
    deleted_reason   = Column(Text, nullable=True)
    created_at       = Column(DateTime, default=datetime.utcnow)
    updated_at       = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    entered_by       = relationship("User", foreign_keys=[entered_by_id])


# ─── Operativos portuarios ─────────────────────────────────────────────────

class Operation(Base):
    __tablename__ = "operations"
    id              = Column(Integer, primary_key=True, index=True)
    raw_name        = Column(String, nullable=False)         # nombre original del Excel
    ship_name       = Column(String, nullable=False, index=True)  # normalizado
    operation_type  = Column(String, nullable=False, default="vessel")  # vessel / special
    client          = Column(String, nullable=True)
    product         = Column(String, nullable=True)
    start_date      = Column(DateTime, nullable=True)
    end_date        = Column(DateTime, nullable=True)
    declared_trips  = Column(Integer, nullable=True)
    actual_trips    = Column(Integer, default=0)
    total_neto_kg   = Column(Integer, default=0)
    total_origen_kg = Column(Integer, default=0)
    total_diff_kg   = Column(Integer, default=0)
    avg_duration_min = Column(Numeric(8, 2), nullable=True)
    avg_tons_per_trip = Column(Numeric(10, 3), nullable=True)
    avg_tons_per_hour = Column(Numeric(10, 3), nullable=True)
    source_file     = Column(String, nullable=True)
    created_at      = Column(DateTime, default=datetime.utcnow)

    trips = relationship("OperationTrip", back_populates="operation", cascade="all, delete-orphan")
    product_totals = relationship("OperationProductTotal", back_populates="operation", cascade="all, delete-orphan")
    cargo_summaries = relationship("OperationCargoSummary", back_populates="operation")


class OperationTrip(Base):
    __tablename__ = "operation_trips"
    id            = Column(Integer, primary_key=True, index=True)
    operation_id  = Column(Integer, ForeignKey("operations.id"), nullable=False, index=True)
    trip_code     = Column(Integer, unique=True, nullable=False, index=True)
    entry_date    = Column(DateTime, nullable=True)
    entry_time    = Column(String, nullable=True)   # "HH:MM:SS"
    exit_date     = Column(DateTime, nullable=True)
    exit_time     = Column(String, nullable=True)   # "HH:MM:SS"
    plate         = Column(String, nullable=True)
    tara_kg       = Column(Integer, nullable=True)
    bruto_kg      = Column(Integer, nullable=True)
    neto_kg       = Column(Integer, nullable=True)
    origen_kg     = Column(Integer, nullable=True)
    diff_kg       = Column(Integer, nullable=True)
    shift_number  = Column(Integer, nullable=True)  # 1/2/3/4
    duration_min  = Column(Numeric(8, 2), nullable=True)
    client        = Column(String, nullable=True)
    product       = Column(String, nullable=True)
    created_at    = Column(DateTime, default=datetime.utcnow)

    operation = relationship("Operation", back_populates="trips")


class OperationProductTotal(Base):
    """
    Stores per-product discharge totals combining depot (from trips) and
    Costado Vapor (from the CV Excel). One row per operation+product combination.
    Idempotency key: (source_file, raw_ship_name, product, cv_start_date).
    """
    __tablename__ = "operation_product_totals"

    id                    = Column(Integer, primary_key=True, index=True)
    operation_id          = Column(Integer, ForeignKey("operations.id"), nullable=True, index=True)
    raw_ship_name         = Column(String, nullable=False)
    ship_name             = Column(String, nullable=False)
    client                = Column(String, nullable=True)
    product               = Column(String, nullable=False)
    cv_start_date         = Column(DateTime, nullable=True)
    cv_end_date           = Column(DateTime, nullable=True)
    cv_excel_tons         = Column(Numeric(12, 3), nullable=False, default=0)
    depot_tons            = Column(Numeric(12, 3), nullable=False, default=0)
    costado_vapor_tons    = Column(Numeric(12, 3), nullable=False, default=0)
    total_discharged_tons = Column(Numeric(12, 3), nullable=False, default=0)
    match_status          = Column(String, nullable=False, default="matched")  # matched/unmatched/ambiguous
    notes                 = Column(String, nullable=True)
    source_file           = Column(String, nullable=True)
    source_year           = Column(Integer, nullable=True)
    created_at            = Column(DateTime, default=datetime.utcnow)

    operation = relationship("Operation", back_populates="product_totals")


class OperationCargoSummary(Base):
    """
    Cargo summary from 'Operativos barcos' Excel — source of truth for discharge totals.
    CV is explicit (not inferred). Replaces OperationProductTotal in the long run.
    Kept separate during transition; OperationProductTotal preserved for backward compat.

    Invariant:  depot_kg + (cv_kg or 0) ≈ total_ship_kg  (±5 000 kg tolerance)
    Idempotency: (source_file, ship_name, client, product, start_date)
    """
    __tablename__ = "operation_cargo_summaries"

    id            = Column(Integer, primary_key=True, index=True)
    operation_id  = Column(Integer, ForeignKey("operations.id"), nullable=True, index=True)

    # Identity — from Excel
    raw_ship_name = Column(String, nullable=False)
    ship_name     = Column(String, nullable=False, index=True)   # normalized (no M/V prefix)
    client        = Column(String, nullable=True)
    product       = Column(String, nullable=False)
    start_date    = Column(DateTime, nullable=True)
    end_date      = Column(DateTime, nullable=True)
    trip_count    = Column(Integer, nullable=True)

    # Tonnage stored in kg for consistency with the operations module
    depot_kg      = Column(Numeric(14, 0), nullable=False, default=0)  # neto column
    cv_kg         = Column(Numeric(14, 0), nullable=True)               # CV column; NULL = no CV
    total_ship_kg = Column(Numeric(14, 0), nullable=False, default=0)   # Total del Barco

    # Metadata
    match_status  = Column(String, nullable=False, default="unmatched")  # matched / unmatched
    source_file   = Column(String, nullable=False)
    notes         = Column(String, nullable=True)
    created_at    = Column(DateTime, default=datetime.utcnow)

    operation = relationship("Operation", back_populates="cargo_summaries")

    __table_args__ = (
        UniqueConstraint(
            "source_file", "ship_name", "client", "product", "start_date",
            name="uq_cargo_summary",
        ),
    )


# ── Módulo Operativos en Tiempo Real ─────────────────────────────────────────
# Los modelos están en un archivo separado para mantener legibilidad.
# Se importan aquí para que Base.metadata.create_all() los incluya
# en la migración automáticamente.
from app.models_live import (  # noqa: E402, F401
    OperationLiveSession,
    OperationLiveSessionProduct,
    OperationLiveShift,
    OperationLiveBodegaData,
    OperationLiveDelay,
    OperationLiveEquipment,
    OperationLiveStaff,
    # Fase 2: Factura cooperativa + Conciliación
    OperationLiveInvoice,
    OperationLiveInvoiceTonnageLine,
    OperationLiveInvoiceLaborLine,
    OperationLiveInvoiceCargoLine,
    OperationLiveInvoiceTotals,
    OperationLiveReconciliation,
)
