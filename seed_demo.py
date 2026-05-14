#!/usr/bin/env python3
"""
Script de datos demo para MTR Compras.
Crea usuarios, proveedores y compras con historial realista por proveedor.
USO: python3 seed_demo.py [--reset]
"""
import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from app.database import engine, Base, SessionLocal
from app import models
from app.auth import hash_password

SAMPLE_PDF = "https://www.w3.org/WAI/WCAG21/Techniques/pdf/sample.pdf"
SAMPLE_IMG = "https://upload.wikimedia.org/wikipedia/commons/thumb/2/2f/Culinary_fruits_front_view.jpg/220px-Culinary_fruits_front_view.jpg"


def reset_demo(db):
    print("→ Limpiando datos demo anteriores...")
    db.query(models.AuditLog).delete()
    db.query(models.Document).delete()
    db.query(models.Purchase).delete()
    db.query(models.Supplier).delete()
    db.query(models.User).filter(models.User.email != "admin@mtr.com").delete()
    db.commit()
    print("✓ Demo anterior eliminada")


def add_audit(db, purchase_id, user_id, action, old_status, new_status, comment="", ago_hours=0):
    db.add(models.AuditLog(
        purchase_id=purchase_id, user_id=user_id, action=action,
        old_status=old_status, new_status=new_status, comment=comment,
        created_at=datetime.utcnow() - timedelta(hours=ago_hours)
    ))


def add_remito(db, purchase_id, uploader_id, filename, ago_days=0):
    db.add(models.Document(
        purchase_id=purchase_id, doc_type="remito",
        file_url=SAMPLE_PDF, filename=filename,
        uploaded_by_id=uploader_id,
        uploaded_at=datetime.utcnow() - timedelta(days=ago_days)
    ))


def add_factura(db, purchase_id, uploader_id, filename, invoice_number, invoice_date, amount, ago_days=0):
    db.add(models.Document(
        purchase_id=purchase_id, doc_type="factura",
        file_url=SAMPLE_PDF, filename=filename,
        invoice_number=invoice_number, invoice_date=invoice_date,
        invoice_amount=amount,
        uploaded_by_id=uploader_id,
        uploaded_at=datetime.utcnow() - timedelta(days=ago_days)
    ))


def make_purchase(db, plant, area, supplier_id, description, reason, estimated_amount,
                  requester_id, status, ago_days=0, authorized_by_id=None, authorized_at=None,
                  amount_alert=False, notes="", rejection_reason=""):
    p = models.Purchase(
        plant=plant, area=area, supplier_id=supplier_id,
        description=description, reason=reason,
        estimated_amount=estimated_amount,
        requested_by_id=requester_id, status=status,
        notes=notes, amount_alert=amount_alert,
        rejection_reason=rejection_reason,
        authorized_by_id=authorized_by_id,
        authorized_at=authorized_at,
        created_at=datetime.utcnow() - timedelta(days=ago_days),
        requested_at=datetime.utcnow() - timedelta(days=ago_days),
    )
    db.add(p)
    db.flush()
    return p


def run(reset=False):
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    if reset:
        reset_demo(db)

    # Sincronizar clave del superadmin
    su = db.query(models.User).filter(models.User.email == "admin@mtr.com").first()
    if su:
        su.hashed_password = hash_password("mtr1234")
        db.commit()

    # ── USUARIOS ─────────────────────────────────────────────
    print("\n→ Creando usuarios demo...")
    user_defs = [
        dict(name="Carlos Rodríguez",  email="carlos@mtr.com",  password="mtr1234", role="planta",      plant="MTR1"),
        dict(name="María González",    email="maria@mtr.com",   password="mtr1234", role="planta",      plant="MTR2"),
        dict(name="Roberto Sánchez",   email="roberto@mtr.com", password="mtr1234", role="autorizador", plant="TODAS"),
        dict(name="Laura Fernández",   email="laura@mtr.com",   password="mtr1234", role="admin",       plant="ROSARIO"),
    ]
    U = {}
    for ud in user_defs:
        ex = db.query(models.User).filter(models.User.email == ud["email"]).first()
        if ex:
            U[ud["email"]] = ex
            print(f"  ↩ {ud['email']}")
        else:
            u = models.User(name=ud["name"], email=ud["email"],
                            hashed_password=hash_password(ud["password"]),
                            role=ud["role"], plant=ud["plant"])
            db.add(u)
            db.flush()
            U[ud["email"]] = u
            print(f"  ✓ {ud['name']} ({ud['role']})")
    db.commit()
    for ud in user_defs:
        U[ud["email"]] = db.query(models.User).filter(models.User.email == ud["email"]).first()

    carlos  = U["carlos@mtr.com"]
    maria   = U["maria@mtr.com"]
    roberto = U["roberto@mtr.com"]
    laura   = U["laura@mtr.com"]

    # ── PROVEEDORES ───────────────────────────────────────────
    print("\n→ Creando proveedores demo...")
    supplier_defs = [
        dict(name="Ferretería San Nicolás",       cuit="20-11234567-8", contact_name="Juan Pérez",    contact_phone="336 423-1234", email="ventas@ferresnick.com"),
        dict(name="Distribuidora Eléctrica Norte", cuit="30-22345678-9", contact_name="Ana López",    contact_phone="336 434-5678", email="pedidos@denorte.com"),
        dict(name="Lubricantes del Paraná",        cuit="20-33456789-0", contact_name="Carlos Gómez", contact_phone="336 445-9012", email="lubrisan@gmail.com"),
        dict(name="Seguridad Industrial MTR",      cuit="30-44567890-1", contact_name="Sandra Ruiz",  contact_phone="011 4567-8901", email="sandra@seguridadindustrial.com"),
        dict(name="Neumáticos Litoral",            cuit="20-55678901-2", contact_name="Diego Molina", contact_phone="336 456-3456", email="info@neumaticoslitoral.com"),
    ]
    S = {}
    for sd in supplier_defs:
        ex = db.query(models.Supplier).filter(models.Supplier.name == sd["name"]).first()
        if ex:
            S[sd["name"]] = ex
            print(f"  ↩ {sd['name']}")
        else:
            s = models.Supplier(**sd)
            db.add(s)
            db.flush()
            S[sd["name"]] = s
            print(f"  ✓ {sd['name']}")
    db.commit()
    for sd in supplier_defs:
        S[sd["name"]] = db.query(models.Supplier).filter(models.Supplier.name == sd["name"]).first()

    ferreteria  = S["Ferretería San Nicolás"]
    electrica   = S["Distribuidora Eléctrica Norte"]
    lubricantes = S["Lubricantes del Paraná"]
    seguridad   = S["Seguridad Industrial MTR"]
    neumaticos  = S["Neumáticos Litoral"]

    print("\n→ Creando compras demo...")

    # ═══════════════════════════════════════════════════════════
    # FERRETERÍA SAN NICOLÁS — 6 compras, historial completo
    # ═══════════════════════════════════════════════════════════

    # 1. PAGADA — 45 días atrás
    p = make_purchase(db, "MTR1", "Mantenimiento", ferreteria.id,
        "Tornillería variada M6/M8/M10 x 200 unidades + tuercas galvanizadas",
        "Reparación estructura metálica sector prensa línea 1",
        15200.00, carlos.id, "pagada", ago_days=45,
        authorized_by_id=roberto.id,
        authorized_at=datetime.utcnow() - timedelta(days=44))
    add_audit(db, p.id, carlos.id,  "created",  None,        "pendiente", ago_hours=45*24+2)
    add_audit(db, p.id, roberto.id, "approved", "pendiente", "aprobada",  ago_hours=44*24)
    add_audit(db, p.id, carlos.id,  "received", "aprobada",  "recibida",  ago_hours=42*24)
    add_audit(db, p.id, laura.id,   "invoiced", "recibida",  "facturada", ago_hours=38*24)
    add_audit(db, p.id, laura.id,   "paid",     "facturada", "pagada",    ago_hours=30*24)
    add_remito(db, p.id,  carlos.id, "remito_tornilleria_R-1001.pdf", ago_days=42)
    add_factura(db, p.id, laura.id,  "factura_ferreteria_A-0001-00001122.pdf",
                "A-0001-00001122", "2025-03-01", 15200.00, ago_days=38)
    print(f"  ✓ #{p.id} PAGADA — Ferretería (tornillería, 45d)")

    # 2. PAGADA — 30 días atrás
    p = make_purchase(db, "MTR2", "Mantenimiento", ferreteria.id,
        "Llave de impacto 1/2\" 750Nm + set de dados 1/4\" y 1/2\"",
        "Reposición herramienta extraviada, necesaria para cambio de rodamientos",
        31200.00, maria.id, "pagada", ago_days=30,
        authorized_by_id=roberto.id,
        authorized_at=datetime.utcnow() - timedelta(days=29))
    add_audit(db, p.id, maria.id,   "created",  None,        "pendiente", ago_hours=30*24+2)
    add_audit(db, p.id, roberto.id, "approved", "pendiente", "aprobada",  ago_hours=29*24)
    add_audit(db, p.id, maria.id,   "received", "aprobada",  "recibida",  ago_hours=27*24)
    add_audit(db, p.id, laura.id,   "invoiced", "recibida",  "facturada", ago_hours=24*24)
    add_audit(db, p.id, laura.id,   "paid",     "facturada", "pagada",    ago_hours=20*24)
    add_remito(db, p.id,  maria.id, "foto_remito_herramientas_R-2041.jpg", ago_days=27)
    add_factura(db, p.id, laura.id, "factura_ferreteria_A-0001-00001540.pdf",
                "A-0001-00001540", "2025-03-16", 31200.00, ago_days=24)
    print(f"  ✓ #{p.id} PAGADA — Ferretería (herramientas, 30d)")

    # 3. FACTURADA con alerta monto — 20 días
    p = make_purchase(db, "MTR1", "Producción", ferreteria.id,
        "Cadena de transmisión #50 x 3 metros + piñones 17T y 21T",
        "Rotura cadena línea empaque, paro de producción",
        22000.00, carlos.id, "facturada", ago_days=20,
        authorized_by_id=roberto.id,
        authorized_at=datetime.utcnow() - timedelta(days=19),
        amount_alert=True)
    add_audit(db, p.id, carlos.id,  "created",  None,        "pendiente", ago_hours=20*24+2)
    add_audit(db, p.id, roberto.id, "approved", "pendiente", "aprobada",  ago_hours=19*24)
    add_audit(db, p.id, carlos.id,  "received", "aprobada",  "recibida",  ago_hours=16*24)
    add_audit(db, p.id, laura.id,   "invoiced", "recibida",  "facturada", ago_hours=12*24)
    add_remito(db, p.id,  carlos.id, "remito_cadenas_R-2089.pdf", ago_days=16)
    add_factura(db, p.id, laura.id,  "factura_ferreteria_B-0001-00002211.pdf",
                "B-0001-00002211", "2025-04-10", 26400.00, ago_days=12)  # +20% de alerta
    print(f"  ✓ #{p.id} FACTURADA ⚠ — Ferretería (cadena transmisión, alerta monto)")

    # 4. RECIBIDA sin factura — 8 días
    p = make_purchase(db, "MTR1", "Mantenimiento", ferreteria.id,
        "Disco de corte 230mm x10 + disco desbaste 115mm x5, marca Norton",
        "Reposición stock consumibles taller",
        8400.00, carlos.id, "recibida", ago_days=8,
        authorized_by_id=roberto.id,
        authorized_at=datetime.utcnow() - timedelta(days=7))
    add_audit(db, p.id, carlos.id,  "created",  None,        "pendiente", ago_hours=8*24+2)
    add_audit(db, p.id, roberto.id, "approved", "pendiente", "aprobada",  ago_hours=7*24)
    add_audit(db, p.id, carlos.id,  "received", "aprobada",  "recibida",  ago_hours=5*24)
    add_remito(db, p.id, carlos.id, "remito_discos_corte_R-3012.pdf", ago_days=5)
    print(f"  ✓ #{p.id} RECIBIDA sin factura — Ferretería (discos, 8d)")

    # 5. APROBADA sin remito — 3 días
    p = make_purchase(db, "MTR2", "Mantenimiento", ferreteria.id,
        "Pintura epoxi bicomponente gris 4L + thinner x2 litros",
        "Pintura piso sector fundición, desgaste por tráfico de autoelevadores",
        12600.00, maria.id, "aprobada", ago_days=3,
        authorized_by_id=roberto.id,
        authorized_at=datetime.utcnow() - timedelta(days=2))
    add_audit(db, p.id, maria.id,   "created",  None,        "pendiente", ago_hours=3*24+2)
    add_audit(db, p.id, roberto.id, "approved", "pendiente", "aprobada",  ago_hours=2*24)
    print(f"  ✓ #{p.id} APROBADA sin remito — Ferretería (pintura, 3d)")

    # 6. PENDIENTE — 1 día
    p = make_purchase(db, "MTR1", "Mantenimiento", ferreteria.id,
        "Tornillería variada: tuercas M8, M10, bulones galvanizados x50",
        "Reparación de estructura metálica del sector prensa",
        18500.00, carlos.id, "pendiente", ago_days=1, notes="Urgente para el lunes")
    add_audit(db, p.id, carlos.id, "created", None, "pendiente", ago_hours=25)
    print(f"  ✓ #{p.id} PENDIENTE — Ferretería (tornillería, hoy)")

    # ═══════════════════════════════════════════════════════════
    # LUBRICANTES DEL PARANÁ — 5 compras (proveedor frecuente)
    # ═══════════════════════════════════════════════════════════

    # PAGADA — 60 días (compra mensual anterior)
    p = make_purchase(db, "MTR1", "Producción", lubricantes.id,
        "Aceite hidráulico ISO 46 x 20 litros (2 tambores)",
        "Mantenimiento preventivo bomba hidráulica línea 1 y 2",
        38500.00, carlos.id, "pagada", ago_days=60,
        authorized_by_id=roberto.id,
        authorized_at=datetime.utcnow() - timedelta(days=59))
    add_audit(db, p.id, carlos.id,  "created",  None,        "pendiente", ago_hours=60*24+2)
    add_audit(db, p.id, roberto.id, "approved", "pendiente", "aprobada",  ago_hours=59*24)
    add_audit(db, p.id, carlos.id,  "received", "aprobada",  "recibida",  ago_hours=57*24)
    add_audit(db, p.id, laura.id,   "invoiced", "recibida",  "facturada", ago_hours=54*24)
    add_audit(db, p.id, laura.id,   "paid",     "facturada", "pagada",    ago_hours=48*24)
    add_remito(db, p.id,  carlos.id, "remito_aceite_hidraulico_R-0040.pdf", ago_days=57)
    add_factura(db, p.id, laura.id,  "factura_lubricantes_A-0001-00003910.pdf",
                "A-0001-00003910", "2025-02-28", 38500.00, ago_days=54)
    print(f"  ✓ #{p.id} PAGADA — Lubricantes (aceite hidráulico, 60d)")

    # PAGADA — 30 días (compra mensual)
    p = make_purchase(db, "MTR1", "Producción", lubricantes.id,
        "Grasa multipropósito NLGI 2 x 18 kg (6 tarros)",
        "Stock de mantenimiento predictivo, consumo mensual promedio 4 tarros",
        24600.00, carlos.id, "pagada", ago_days=30,
        authorized_by_id=roberto.id,
        authorized_at=datetime.utcnow() - timedelta(days=29))
    add_audit(db, p.id, carlos.id,  "created",  None,        "pendiente", ago_hours=30*24+2)
    add_audit(db, p.id, roberto.id, "approved", "pendiente", "aprobada",  ago_hours=29*24)
    add_audit(db, p.id, carlos.id,  "received", "aprobada",  "recibida",  ago_hours=27*24)
    add_audit(db, p.id, laura.id,   "invoiced", "recibida",  "facturada", ago_hours=25*24)
    add_audit(db, p.id, laura.id,   "paid",     "facturada", "pagada",    ago_hours=22*24)
    add_remito(db, p.id,  carlos.id, "remito_grasa_R-0089.pdf", ago_days=27)
    add_factura(db, p.id, laura.id,  "factura_lubricantes_A-0001-00004521.pdf",
                "A-0001-00004521", "2025-03-28", 24600.00, ago_days=25)
    print(f"  ✓ #{p.id} PAGADA — Lubricantes (grasa, 30d)")

    # FACTURADA sin pagar — 15 días
    p = make_purchase(db, "MTR2", "Producción", lubricantes.id,
        "Aceite de corte soluble x 20L + aceite de transmisión 85W140 x 4L",
        "Reposición taller maquinado bloque B y caja reductora",
        19800.00, maria.id, "facturada", ago_days=15,
        authorized_by_id=roberto.id,
        authorized_at=datetime.utcnow() - timedelta(days=14))
    add_audit(db, p.id, maria.id,   "created",  None,        "pendiente", ago_hours=15*24+2)
    add_audit(db, p.id, roberto.id, "approved", "pendiente", "aprobada",  ago_hours=14*24)
    add_audit(db, p.id, maria.id,   "received", "aprobada",  "recibida",  ago_hours=12*24)
    add_audit(db, p.id, laura.id,   "invoiced", "recibida",  "facturada", ago_hours=9*24)
    add_remito(db, p.id,  maria.id, "remito_aceite_corte_R-0112.pdf", ago_days=12)
    add_factura(db, p.id, laura.id,  "factura_lubricantes_A-0001-00005102.pdf",
                "A-0001-00005102", "2025-04-22", 19800.00, ago_days=9)
    print(f"  ✓ #{p.id} FACTURADA sin pagar — Lubricantes (aceite corte, 15d)")

    # RECIBIDA sin factura — 5 días
    p = make_purchase(db, "MTR1", "Mantenimiento", lubricantes.id,
        "Aceite hidráulico ISO 46 x 20 litros + aditivo antidesgaste",
        "Mantenimiento preventivo mensual bombas hidráulicas",
        42000.00, carlos.id, "recibida", ago_days=5,
        authorized_by_id=roberto.id,
        authorized_at=datetime.utcnow() - timedelta(days=4))
    add_audit(db, p.id, carlos.id,  "created",  None,        "pendiente", ago_hours=5*24+2)
    add_audit(db, p.id, roberto.id, "approved", "pendiente", "aprobada",  ago_hours=4*24)
    add_audit(db, p.id, carlos.id,  "received", "aprobada",  "recibida",  ago_hours=2*24)
    add_remito(db, p.id, carlos.id, "remito_aceite_hidra_R-0134.pdf", ago_days=2)
    print(f"  ✓ #{p.id} RECIBIDA sin factura — Lubricantes (aceite, 5d)")

    # PENDIENTE — hoy
    p = make_purchase(db, "MTR2", "Producción", lubricantes.id,
        "Aceite de motor 15W40 mineral x 20L (compra mensual)",
        "Mantenimiento preventivo compresor Ingersoll Rand y generador de emergencia",
        28500.00, maria.id, "pendiente", ago_days=2)
    add_audit(db, p.id, maria.id, "created", None, "pendiente", ago_hours=2*24+1)
    print(f"  ✓ #{p.id} PENDIENTE — Lubricantes (aceite motor, 2d)")

    # ═══════════════════════════════════════════════════════════
    # DISTRIBUIDORA ELÉCTRICA NORTE — 5 compras (proyectos)
    # ═══════════════════════════════════════════════════════════

    # PAGADA — 50 días
    p = make_purchase(db, "MTR1", "Mantenimiento", electrica.id,
        "Tablero eléctrico IP65 + riel DIN + borneras x20 + cable 2.5mm²",
        "Nuevo tablero comando bomba contraincendios sector norte",
        67000.00, carlos.id, "pagada", ago_days=50,
        authorized_by_id=roberto.id,
        authorized_at=datetime.utcnow() - timedelta(days=49))
    add_audit(db, p.id, carlos.id,  "created",  None,        "pendiente", ago_hours=50*24+2)
    add_audit(db, p.id, roberto.id, "approved", "pendiente", "aprobada",  ago_hours=49*24)
    add_audit(db, p.id, carlos.id,  "received", "aprobada",  "recibida",  ago_hours=46*24)
    add_audit(db, p.id, laura.id,   "invoiced", "recibida",  "facturada", ago_hours=42*24)
    add_audit(db, p.id, laura.id,   "paid",     "facturada", "pagada",    ago_hours=35*24)
    add_remito(db, p.id,  carlos.id, "remito_tablero_R-4200.pdf", ago_days=46)
    add_factura(db, p.id, laura.id,  "factura_electrica_B-0001-00008811.pdf",
                "B-0001-00008811", "2025-03-08", 67000.00, ago_days=42)
    print(f"  ✓ #{p.id} PAGADA — Eléctrica (tablero, 50d)")

    # PAGADA — 10 días
    p = make_purchase(db, "MTR1", "Mantenimiento", electrica.id,
        "Cable SPTF 3x2.5mm² x 100 metros, marca Prysmian",
        "Tendido eléctrico sector maquinado bloque B",
        78500.00, carlos.id, "pagada", ago_days=22,
        authorized_by_id=roberto.id,
        authorized_at=datetime.utcnow() - timedelta(days=21))
    add_audit(db, p.id, carlos.id,  "created",  None,        "pendiente", ago_hours=22*24+2)
    add_audit(db, p.id, roberto.id, "approved", "pendiente", "aprobada",  ago_hours=21*24)
    add_audit(db, p.id, carlos.id,  "received", "aprobada",  "recibida",  ago_hours=19*24)
    add_audit(db, p.id, laura.id,   "invoiced", "recibida",  "facturada", ago_hours=16*24)
    add_audit(db, p.id, laura.id,   "paid",     "facturada", "pagada",    ago_hours=10*24)
    add_remito(db, p.id,  carlos.id, "remito_cable_R-4521.pdf", ago_days=19)
    add_factura(db, p.id, laura.id,  "factura_electrica_B-0001-00009340.pdf",
                "B-0001-00009340", "2025-04-06", 78500.00, ago_days=16)
    print(f"  ✓ #{p.id} PAGADA — Eléctrica (cable, 22d)")

    # FACTURADA con alerta monto — 20 días
    p = make_purchase(db, "MTR2", "Mantenimiento", electrica.id,
        "Variador de frecuencia WEG CFW500 0.5CV monofásico + tablero",
        "Reemplazo variador quemado bomba de agua contraincendios",
        95000.00, maria.id, "facturada", ago_days=20,
        authorized_by_id=roberto.id,
        authorized_at=datetime.utcnow() - timedelta(days=19),
        amount_alert=True)
    add_audit(db, p.id, maria.id,   "created",  None,        "pendiente", ago_hours=20*24+2)
    add_audit(db, p.id, roberto.id, "approved", "pendiente", "aprobada",  ago_hours=19*24)
    add_audit(db, p.id, maria.id,   "received", "aprobada",  "recibida",  ago_hours=17*24)
    add_audit(db, p.id, laura.id,   "invoiced", "recibida",  "facturada", ago_hours=15*24)
    add_remito(db, p.id,  maria.id, "remito_variador_WEG_R-2211.pdf", ago_days=17)
    add_factura(db, p.id, laura.id,  "factura_electrica_B-0001-00012890.pdf",
                "B-0001-00012890", "2025-04-20", 113500.00, ago_days=15)
    print(f"  ✓ #{p.id} FACTURADA ⚠ — Eléctrica (variador WEG, alerta monto)")

    # RECIBIDA sin factura — 10 días
    p = make_purchase(db, "MTR1", "Mantenimiento", electrica.id,
        "Disyuntor termomagnético 3x40A + diferencial 2x25A 30mA",
        "Protecciones tablero principal sector fundición",
        14500.00, carlos.id, "recibida", ago_days=10,
        authorized_by_id=roberto.id,
        authorized_at=datetime.utcnow() - timedelta(days=9))
    add_audit(db, p.id, carlos.id,  "created",  None,        "pendiente", ago_hours=10*24+2)
    add_audit(db, p.id, roberto.id, "approved", "pendiente", "aprobada",  ago_hours=9*24)
    add_audit(db, p.id, carlos.id,  "received", "aprobada",  "recibida",  ago_hours=7*24)
    add_remito(db, p.id, carlos.id, "remito_disyuntores_R-4899.pdf", ago_days=7)
    print(f"  ✓ #{p.id} RECIBIDA sin factura — Eléctrica (disyuntores, 10d)")

    # PENDIENTE — 1 día
    p = make_purchase(db, "MTR2", "Mantenimiento", electrica.id,
        "Luminarias LED 100W industriales x6 + driver externo",
        "Reemplazo iluminación sector almacén, ahorro energético",
        52000.00, maria.id, "pendiente", ago_days=1)
    add_audit(db, p.id, maria.id, "created", None, "pendiente", ago_hours=26)
    print(f"  ✓ #{p.id} PENDIENTE — Eléctrica (luminarias LED, 1d)")

    # ═══════════════════════════════════════════════════════════
    # SEGURIDAD INDUSTRIAL MTR — 5 compras (EPP recurrente)
    # ═══════════════════════════════════════════════════════════

    # PAGADA — 35 días
    p = make_purchase(db, "MTR1", "Seguridad", seguridad.id,
        "Guantes de nitrilo talla L x 100 pares, resistencia química",
        "Stock mensual EPP laboratorio de pintura, protocolo ISO 45001",
        12800.00, carlos.id, "pagada", ago_days=35,
        authorized_by_id=roberto.id,
        authorized_at=datetime.utcnow() - timedelta(days=34))
    add_audit(db, p.id, carlos.id,  "created",  None,        "pendiente", ago_hours=35*24+2)
    add_audit(db, p.id, roberto.id, "approved", "pendiente", "aprobada",  ago_hours=34*24)
    add_audit(db, p.id, carlos.id,  "received", "aprobada",  "recibida",  ago_hours=32*24)
    add_audit(db, p.id, laura.id,   "invoiced", "recibida",  "facturada", ago_hours=29*24)
    add_audit(db, p.id, laura.id,   "paid",     "facturada", "pagada",    ago_hours=25*24)
    add_remito(db, p.id,  carlos.id, "remito_guantes_R-7721.pdf", ago_days=32)
    add_factura(db, p.id, laura.id,  "factura_seguridad_A-0001-00009834.pdf",
                "A-0001-00009834", "2025-03-24", 12800.00, ago_days=29)
    print(f"  ✓ #{p.id} PAGADA — Seguridad (guantes, 35d)")

    # PAGADA — 5 días (cascos)
    p = make_purchase(db, "MTR1", "Seguridad", seguridad.id,
        "Cascos de seguridad tipo 1 x10 unidades, color amarillo",
        "Reposición stock EPP sector fundición, cascos con vida útil vencida",
        56000.00, carlos.id, "pagada", ago_days=18,
        authorized_by_id=roberto.id,
        authorized_at=datetime.utcnow() - timedelta(days=17))
    add_audit(db, p.id, carlos.id,  "created",  None,        "pendiente", ago_hours=18*24+2)
    add_audit(db, p.id, roberto.id, "approved", "pendiente", "aprobada",  ago_hours=17*24)
    add_audit(db, p.id, carlos.id,  "received", "aprobada",  "recibida",  ago_hours=15*24)
    add_audit(db, p.id, laura.id,   "invoiced", "recibida",  "facturada", ago_hours=12*24)
    add_audit(db, p.id, laura.id,   "paid",     "facturada", "pagada",    ago_hours=8*24)
    add_remito(db, p.id,  carlos.id, "remito_cascos_R-7890.pdf", ago_days=15)
    add_factura(db, p.id, laura.id,  "factura_seguridad_A-0001-00010211.pdf",
                "A-0001-00010211", "2025-04-10", 56000.00, ago_days=12)
    print(f"  ✓ #{p.id} PAGADA — Seguridad (cascos, 18d)")

    # FACTURADA sin pagar — 7 días
    p = make_purchase(db, "MTR2", "Seguridad", seguridad.id,
        "Calzado de seguridad dieléctrico talla 42 x5 pares + talla 44 x3",
        "Reposición anual calzado sector eléctrico, norma IRAM 3601",
        48000.00, maria.id, "facturada", ago_days=12,
        authorized_by_id=roberto.id,
        authorized_at=datetime.utcnow() - timedelta(days=11))
    add_audit(db, p.id, maria.id,   "created",  None,        "pendiente", ago_hours=12*24+2)
    add_audit(db, p.id, roberto.id, "approved", "pendiente", "aprobada",  ago_hours=11*24)
    add_audit(db, p.id, maria.id,   "received", "aprobada",  "recibida",  ago_hours=9*24)
    add_audit(db, p.id, laura.id,   "invoiced", "recibida",  "facturada", ago_hours=7*24)
    add_remito(db, p.id,  maria.id, "remito_calzado_R-8012.pdf", ago_days=9)
    add_factura(db, p.id, laura.id,  "factura_seguridad_A-0001-00010890.pdf",
                "A-0001-00010890", "2025-04-21", 48000.00, ago_days=7)
    print(f"  ✓ #{p.id} FACTURADA sin pagar — Seguridad (calzado, 12d)")

    # APROBADA sin remito — 4 días
    p = make_purchase(db, "MTR1", "Seguridad", seguridad.id,
        "Protectores auditivos tipo copa NRR30 x20 + recambio almohadillas x40",
        "Renovación stock EPP sector compresores, medición ruido 95dB",
        18500.00, carlos.id, "aprobada", ago_days=4,
        authorized_by_id=roberto.id,
        authorized_at=datetime.utcnow() - timedelta(days=3))
    add_audit(db, p.id, carlos.id,  "created",  None,        "pendiente", ago_hours=4*24+2)
    add_audit(db, p.id, roberto.id, "approved", "pendiente", "aprobada",  ago_hours=3*24)
    print(f"  ✓ #{p.id} APROBADA sin remito — Seguridad (protectores auditivos, 4d)")

    # RECHAZADA — 25 días
    p = make_purchase(db, "MTR2", "Administración", seguridad.id,
        "Kit de primeros auxilios avanzado + desfibrilador portátil DEA",
        "Equipamiento obligatorio nuevas instalaciones según resolución 905/2024",
        320000.00, maria.id, "rechazada", ago_days=25,
        rejection_reason="Monto supera límite de aprobación del autorizador. Requiere aprobación de gerencia y licitación.")
    add_audit(db, p.id, maria.id,   "created",  None,        "pendiente", ago_hours=25*24+2)
    add_audit(db, p.id, roberto.id, "rejected", "pendiente", "rechazada",
              "Monto supera límite de aprobación. Requiere licitación.", ago_hours=24*24)
    print(f"  ✓ #{p.id} RECHAZADA — Seguridad (desfibrilador, 25d)")

    # ═══════════════════════════════════════════════════════════
    # NEUMÁTICOS LITORAL — 4 compras (esporádicas, montos altos)
    # ═══════════════════════════════════════════════════════════

    # PAGADA — 90 días
    p = make_purchase(db, "MTR1", "Logística", neumaticos.id,
        "Neumático 11R22.5 para camión planta x4 unidades, marca Bridgestone",
        "Renovación tren trasero camión interno planta MTR1, desgaste irregular",
        284000.00, carlos.id, "pagada", ago_days=90,
        authorized_by_id=roberto.id,
        authorized_at=datetime.utcnow() - timedelta(days=89))
    add_audit(db, p.id, carlos.id,  "created",  None,        "pendiente", ago_hours=90*24+2)
    add_audit(db, p.id, roberto.id, "approved", "pendiente", "aprobada",  ago_hours=89*24)
    add_audit(db, p.id, carlos.id,  "received", "aprobada",  "recibida",  ago_hours=86*24)
    add_audit(db, p.id, laura.id,   "invoiced", "recibida",  "facturada", ago_hours=82*24)
    add_audit(db, p.id, laura.id,   "paid",     "facturada", "pagada",    ago_hours=75*24)
    add_remito(db, p.id,  carlos.id, "remito_neumaticos_camion_R-9001.pdf", ago_days=86)
    add_factura(db, p.id, laura.id,  "factura_neumaticos_A-0002-00000412.pdf",
                "A-0002-00000412", "2025-01-31", 284000.00, ago_days=82)
    print(f"  ✓ #{p.id} PAGADA — Neumáticos (camión x4, 90d)")

    # PAGADA — 30 días (autoelevador)
    p = make_purchase(db, "MTR2", "Logística", neumaticos.id,
        "Neumático 11R22.5 para autoelevador Clark x2 unidades",
        "Neumático delantero izquierdo con corte profundo, riesgo de explosión",
        89000.00, maria.id, "pagada", ago_days=30,
        authorized_by_id=roberto.id,
        authorized_at=datetime.utcnow() - timedelta(days=29))
    add_audit(db, p.id, maria.id,   "created",  None,        "pendiente", ago_hours=30*24+2)
    add_audit(db, p.id, roberto.id, "approved", "pendiente", "aprobada",  ago_hours=29*24)
    add_audit(db, p.id, maria.id,   "received", "aprobada",  "recibida",  ago_hours=27*24)
    add_audit(db, p.id, laura.id,   "invoiced", "recibida",  "facturada", ago_hours=24*24)
    add_audit(db, p.id, laura.id,   "paid",     "facturada", "pagada",    ago_hours=20*24)
    add_remito(db, p.id,  maria.id, "remito_neumaticos_autoelevador_R-9088.pdf", ago_days=27)
    add_factura(db, p.id, laura.id,  "factura_neumaticos_A-0002-00000521.pdf",
                "A-0002-00000521", "2025-03-28", 89000.00, ago_days=24)
    print(f"  ✓ #{p.id} PAGADA — Neumáticos (autoelevador x2, 30d)")

    # APROBADA sin remito — 3 días
    p = make_purchase(db, "MTR1", "Logística", neumaticos.id,
        "Neumático 185R14C para camioneta Ford Transit x4 + balanceo",
        "Renovación neumáticos camioneta de reparto, desgaste uniforme al límite",
        68000.00, carlos.id, "aprobada", ago_days=3,
        authorized_by_id=roberto.id,
        authorized_at=datetime.utcnow() - timedelta(days=2))
    add_audit(db, p.id, carlos.id,  "created",  None,        "pendiente", ago_hours=3*24+2)
    add_audit(db, p.id, roberto.id, "approved", "pendiente", "aprobada",  ago_hours=2*24)
    print(f"  ✓ #{p.id} APROBADA sin remito — Neumáticos (camioneta, 3d)")

    # RECHAZADA — 12 días (fuera de alcance)
    p = make_purchase(db, "MTR2", "Administración", neumaticos.id,
        "Monitor 27 pulgadas 4K para oficina, marca LG",
        "Mejora de productividad en área de control de calidad",
        180000.00, maria.id, "rechazada", ago_days=12,
        rejection_reason="No corresponde a insumos operativos. Gestionar por presupuesto IT con aprobación de gerencia.")
    add_audit(db, p.id, maria.id,   "created",  None,        "pendiente", ago_hours=12*24+2)
    add_audit(db, p.id, roberto.id, "rejected", "pendiente", "rechazada",
              "No corresponde a insumos operativos. Gestionar por presupuesto IT con aprobación de gerencia.",
              ago_hours=11*24)
    print(f"  ✓ #{p.id} RECHAZADA — Neumáticos (monitor IT, 12d)")

    db.commit()
    db.close()

    print("\n" + "="*60)
    print("  SEED COMPLETADO — 25 compras, 5 proveedores, 4 usuarios")
    print("="*60)
    print()
    print("  Accesos (contraseña: mtr1234)")
    print("  ──────────────────────────────────────")
    print("  carlos@mtr.com    Planta MTR1")
    print("  maria@mtr.com     Planta MTR2")
    print("  roberto@mtr.com   Autorizador")
    print("  laura@mtr.com     Administración (Rosario)")
    print("  admin@mtr.com     Superadmin")
    print()
    print("  Historial por proveedor:")
    print("  ──────────────────────────────────────")
    print("  Ferretería San Nicolás       6 compras")
    print("  Lubricantes del Paraná       5 compras")
    print("  Distribuidora Eléctrica      5 compras")
    print("  Seguridad Industrial MTR     5 compras")
    print("  Neumáticos Litoral           4 compras")
    print()


if __name__ == "__main__":
    do_reset = "--reset" in sys.argv
    run(reset=do_reset)
