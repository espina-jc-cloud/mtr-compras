from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, Numeric
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
