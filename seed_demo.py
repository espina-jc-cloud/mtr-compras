#!/usr/bin/env python3
"""
Script de datos demo para MTR Compras.
Crea usuarios, proveedores y 10 compras en distintos estados.
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

# URL pública de ejemplo para documentos (imagen de muestra)
SAMPLE_DOC_URL = "https://www.w3.org/WAI/WCAG21/Techniques/pdf/sample.pdf"
SAMPLE_IMG_URL = "https://upload.wikimedia.org/wikipedia/commons/thumb/2/2f/Culinary_fruits_front_view.jpg/220px-Culinary_fruits_front_view.jpg"

def reset_demo(db):
    print("→ Limpiando datos demo anteriores...")
    db.query(models.AuditLog).delete()
    db.query(models.Document).delete()
    db.query(models.Purchase).delete()
    db.query(models.Supplier).delete()
    # Solo borrar usuarios demo, no el superadmin principal
    db.query(models.User).filter(models.User.email != "admin@mtr.com").delete()
    db.commit()
    print("✓ Demo anterior eliminada")

def add_audit(db, purchase_id, user_id, action, old_status, new_status, comment="", ago_hours=0):
    log = models.AuditLog(
        purchase_id=purchase_id,
        user_id=user_id,
        action=action,
        old_status=old_status,
        new_status=new_status,
        comment=comment,
        created_at=datetime.utcnow() - timedelta(hours=ago_hours)
    )
    db.add(log)

def run(reset=False):
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    if reset:
        reset_demo(db)

    # Asegurar que superadmin use la misma clave demo
    su = db.query(models.User).filter(models.User.email == "admin@mtr.com").first()
    if su:
        su.hashed_password = hash_password("mtr1234")
        db.commit()

    # ─── USUARIOS ──────────────────────────────────────────────
    print("\n→ Creando usuarios demo...")
    users = {}

    user_defs = [
        dict(name="Carlos Rodríguez",  email="carlos@mtr.com",    password="mtr1234", role="planta",      plant="MTR1"),
        dict(name="María González",    email="maria@mtr.com",     password="mtr1234", role="planta",      plant="MTR2"),
        dict(name="Roberto Sánchez",   email="roberto@mtr.com",   password="mtr1234", role="autorizador", plant="TODAS"),
        dict(name="Laura Fernández",   email="laura@mtr.com",     password="mtr1234", role="admin",       plant="ROSARIO"),
    ]

    for ud in user_defs:
        existing = db.query(models.User).filter(models.User.email == ud["email"]).first()
        if existing:
            users[ud["role"] if ud["role"] != "planta" else ("planta_mtr1" if ud["plant"] == "MTR1" else "planta_mtr2")] = existing
            print(f"  ↩ Ya existe: {ud['email']}")
        else:
            u = models.User(
                name=ud["name"], email=ud["email"],
                hashed_password=hash_password(ud["password"]),
                role=ud["role"], plant=ud["plant"]
            )
            db.add(u)
            db.flush()
            key = ud["role"] if ud["role"] != "planta" else ("planta_mtr1" if ud["plant"] == "MTR1" else "planta_mtr2")
            users[key] = u
            print(f"  ✓ {ud['name']} ({ud['role']} · {ud['plant']})")

    db.commit()

    # Re-fetch para tener IDs actualizados
    for ud in user_defs:
        key = ud["role"] if ud["role"] != "planta" else ("planta_mtr1" if ud["plant"] == "MTR1" else "planta_mtr2")
        users[key] = db.query(models.User).filter(models.User.email == ud["email"]).first()

    # ─── PROVEEDORES ───────────────────────────────────────────
    print("\n→ Creando proveedores demo...")
    suppliers = {}

    supplier_defs = [
        dict(name="Ferretería San Nicolás",  cuit="20-11234567-8", contact_name="Juan Pérez",    contact_phone="336 423-1234", email="ventas@ferresnick.com"),
        dict(name="Distribuidora Eléctrica Norte", cuit="30-22345678-9", contact_name="Ana López",  contact_phone="336 434-5678", email="pedidos@denorte.com"),
        dict(name="Lubricantes del Paraná",  cuit="20-33456789-0", contact_name="Carlos Gómez",  contact_phone="336 445-9012", email="lubrisan@gmail.com"),
        dict(name="Seguridad Industrial MTR", cuit="30-44567890-1", contact_name="Sandra Ruiz", contact_phone="011 4567-8901", email="sandra@seguridadindustrial.com"),
        dict(name="Neumáticos Litoral",       cuit="20-55678901-2", contact_name="Diego Molina", contact_phone="336 456-3456", email="info@neumaticoslitoral.com"),
    ]

    for sd in supplier_defs:
        existing = db.query(models.Supplier).filter(models.Supplier.name == sd["name"]).first()
        if existing:
            suppliers[sd["name"]] = existing
            print(f"  ↩ Ya existe: {sd['name']}")
        else:
            s = models.Supplier(**sd)
            db.add(s)
            db.flush()
            suppliers[sd["name"]] = s
            print(f"  ✓ {sd['name']}")

    db.commit()

    # Re-fetch
    for sd in supplier_defs:
        suppliers[sd["name"]] = db.query(models.Supplier).filter(models.Supplier.name == sd["name"]).first()

    # ─── COMPRAS ───────────────────────────────────────────────
    print("\n→ Creando compras demo...")

    def make_purchase(plant, area, supplier_name, description, reason, estimated_amount, requester, status,
                      notes="", ago_days=0, **kwargs):
        p = models.Purchase(
            plant=plant, area=area,
            supplier_id=suppliers[supplier_name].id,
            description=description, reason=reason,
            estimated_amount=estimated_amount,
            requested_by_id=requester.id,
            status=status,
            notes=notes,
            created_at=datetime.utcnow() - timedelta(days=ago_days),
            requested_at=datetime.utcnow() - timedelta(days=ago_days),
        )
        for k, v in kwargs.items():
            setattr(p, k, v)
        db.add(p)
        db.flush()
        return p

    # 1. PENDIENTE — recién creada
    p1 = make_purchase(
        "MTR1", "Mantenimiento", "Ferretería San Nicolás",
        "Tornillería variada: tuercas M8, M10, bulones galvanizados x50",
        "Reparación de estructura metálica del sector prensa",
        18500.00,
        users["planta_mtr1"], "pendiente", ago_days=1,
        notes="Urgente para el lunes"
    )
    add_audit(db, p1.id, users["planta_mtr1"].id, "created", None, "pendiente", ago_hours=25)
    print(f"  ✓ #{p1.id} PENDIENTE — Ferretería San Nicolás (MTR1)")

    # 2. PENDIENTE — también pendiente
    p2 = make_purchase(
        "MTR2", "Producción", "Lubricantes del Paraná",
        "Aceite hidráulico ISO 46 x 20 litros (2 tambores)",
        "Mantenimiento preventivo bomba hidráulica línea 3",
        42000.00,
        users["planta_mtr2"], "pendiente", ago_days=2
    )
    add_audit(db, p2.id, users["planta_mtr2"].id, "created", None, "pendiente", ago_hours=50)
    print(f"  ✓ #{p2.id} PENDIENTE — Lubricantes del Paraná (MTR2)")

    # 3. APROBADA sin remito
    p3 = make_purchase(
        "MTR1", "Seguridad", "Seguridad Industrial MTR",
        "Cascos de seguridad tipo 1 x10 unidades, color amarillo",
        "Reposición stock EPP sector fundición, cascos con vida útil vencida",
        56000.00,
        users["planta_mtr1"], "aprobada", ago_days=5,
        authorized_by_id=users["autorizador"].id,
        authorized_at=datetime.utcnow() - timedelta(days=4)
    )
    add_audit(db, p3.id, users["planta_mtr1"].id, "created", None, "pendiente", ago_hours=5*24+2)
    add_audit(db, p3.id, users["autorizador"].id, "approved", "pendiente", "aprobada", ago_hours=4*24)
    print(f"  ✓ #{p3.id} APROBADA sin remito — Seguridad Industrial (MTR1)")

    # 4. APROBADA sin remito — MTR2
    p4 = make_purchase(
        "MTR2", "Logística", "Neumáticos Litoral",
        "Neumático 11R22.5 para autoelevador Clark, 2 unidades",
        "Neumático delantero izquierdo con corte profundo, riesgo de explosión",
        89000.00,
        users["planta_mtr2"], "aprobada", ago_days=3,
        authorized_by_id=users["autorizador"].id,
        authorized_at=datetime.utcnow() - timedelta(days=2)
    )
    add_audit(db, p4.id, users["planta_mtr2"].id, "created", None, "pendiente", ago_hours=3*24+1)
    add_audit(db, p4.id, users["autorizador"].id, "approved", "pendiente", "aprobada", ago_hours=2*24)
    print(f"  ✓ #{p4.id} APROBADA sin remito — Neumáticos Litoral (MTR2)")

    # 5. RECIBIDA con remito, sin factura
    p5 = make_purchase(
        "MTR1", "Mantenimiento", "Distribuidora Eléctrica Norte",
        "Cable SPTF 3x2.5mm2 x 100 metros, marca Prysmian",
        "Tendido eléctrico sector maquinado bloque B",
        78500.00,
        users["planta_mtr1"], "recibida", ago_days=10,
        authorized_by_id=users["autorizador"].id,
        authorized_at=datetime.utcnow() - timedelta(days=9)
    )
    add_audit(db, p5.id, users["planta_mtr1"].id, "created", None, "pendiente", ago_hours=10*24+1)
    add_audit(db, p5.id, users["autorizador"].id, "approved", "pendiente", "aprobada", ago_hours=9*24)
    add_audit(db, p5.id, users["planta_mtr1"].id, "received", "aprobada", "recibida", ago_hours=7*24)
    doc5 = models.Document(
        purchase_id=p5.id, doc_type="remito",
        file_url=SAMPLE_DOC_URL, filename="remito_cable_electrico_R-4521.pdf",
        uploaded_by_id=users["planta_mtr1"].id,
        uploaded_at=datetime.utcnow() - timedelta(days=7)
    )
    db.add(doc5)
    print(f"  ✓ #{p5.id} RECIBIDA con remito sin factura — Distribuidora Eléctrica (MTR1)")

    # 6. RECIBIDA con remito, sin factura — MTR2
    p6 = make_purchase(
        "MTR2", "Mantenimiento", "Ferretería San Nicolás",
        "Llave de impacto 1/2 pulgada 750 Nm + set de dados",
        "Reposición herramienta extraviada, necesaria para cambio de rodamientos",
        31200.00,
        users["planta_mtr2"], "recibida", ago_days=8,
        authorized_by_id=users["autorizador"].id,
        authorized_at=datetime.utcnow() - timedelta(days=7)
    )
    add_audit(db, p6.id, users["planta_mtr2"].id, "created", None, "pendiente", ago_hours=8*24+1)
    add_audit(db, p6.id, users["autorizador"].id, "approved", "pendiente", "aprobada", ago_hours=7*24)
    add_audit(db, p6.id, users["planta_mtr2"].id, "received", "aprobada", "recibida", ago_hours=6*24)
    doc6 = models.Document(
        purchase_id=p6.id, doc_type="remito",
        file_url=SAMPLE_IMG_URL, filename="foto_remito_herramientas_20250104.jpg",
        uploaded_by_id=users["planta_mtr2"].id,
        uploaded_at=datetime.utcnow() - timedelta(days=6)
    )
    db.add(doc6)
    print(f"  ✓ #{p6.id} RECIBIDA con remito sin factura — Ferretería (MTR2)")

    # 7. FACTURADA sin pagar
    p7 = make_purchase(
        "MTR1", "Producción", "Lubricantes del Paraná",
        "Grasa multipropósito NLGI 2 x 18 kg (6 tarros)",
        "Stock de mantenimiento predictivo, consumo mensual promedio 4 tarros",
        24600.00,
        users["planta_mtr1"], "facturada", ago_days=15,
        authorized_by_id=users["autorizador"].id,
        authorized_at=datetime.utcnow() - timedelta(days=14)
    )
    add_audit(db, p7.id, users["planta_mtr1"].id, "created", None, "pendiente", ago_hours=15*24+1)
    add_audit(db, p7.id, users["autorizador"].id, "approved", "pendiente", "aprobada", ago_hours=14*24)
    add_audit(db, p7.id, users["planta_mtr1"].id, "received", "aprobada", "recibida", ago_hours=12*24)
    add_audit(db, p7.id, users["admin"].id, "invoiced", "recibida", "facturada", ago_hours=10*24)
    doc7r = models.Document(
        purchase_id=p7.id, doc_type="remito",
        file_url=SAMPLE_DOC_URL, filename="remito_grasa_R-0089.pdf",
        uploaded_by_id=users["planta_mtr1"].id,
        uploaded_at=datetime.utcnow() - timedelta(days=12)
    )
    doc7f = models.Document(
        purchase_id=p7.id, doc_type="factura",
        file_url=SAMPLE_DOC_URL, filename="factura_lubricantes_A-0001-00004521.pdf",
        invoice_number="A-0001-00004521",
        invoice_date="2025-04-28",
        invoice_amount=24600.00,
        uploaded_by_id=users["admin"].id,
        uploaded_at=datetime.utcnow() - timedelta(days=10)
    )
    db.add(doc7r)
    db.add(doc7f)
    print(f"  ✓ #{p7.id} FACTURADA sin pagar — Lubricantes del Paraná (MTR1)")

    # 8. FACTURADA con alerta de monto (factura supera estimado >10%)
    p8 = make_purchase(
        "MTR2", "Mantenimiento", "Distribuidora Eléctrica Norte",
        "Variador de frecuencia WEG CFW500 0.5CV monofásico + tablero",
        "Reemplazo variador quemado bomba de agua contraincendios",
        95000.00,
        users["planta_mtr2"], "facturada", ago_days=20,
        authorized_by_id=users["autorizador"].id,
        authorized_at=datetime.utcnow() - timedelta(days=19),
        amount_alert=True
    )
    add_audit(db, p8.id, users["planta_mtr2"].id, "created", None, "pendiente", ago_hours=20*24+1)
    add_audit(db, p8.id, users["autorizador"].id, "approved", "pendiente", "aprobada", ago_hours=19*24)
    add_audit(db, p8.id, users["planta_mtr2"].id, "received", "aprobada", "recibida", ago_hours=17*24)
    add_audit(db, p8.id, users["admin"].id, "invoiced", "recibida", "facturada", ago_hours=15*24)
    doc8r = models.Document(
        purchase_id=p8.id, doc_type="remito",
        file_url=SAMPLE_DOC_URL, filename="remito_variador_WEG_R-2211.pdf",
        uploaded_by_id=users["planta_mtr2"].id,
        uploaded_at=datetime.utcnow() - timedelta(days=17)
    )
    doc8f = models.Document(
        purchase_id=p8.id, doc_type="factura",
        file_url=SAMPLE_DOC_URL, filename="factura_electronica_B-0001-00012890.pdf",
        invoice_number="B-0001-00012890",
        invoice_date="2025-04-20",
        invoice_amount=113500.00,  # supera 10%: 95000 * 1.10 = 104500, factura = 113500
        uploaded_by_id=users["admin"].id,
        uploaded_at=datetime.utcnow() - timedelta(days=15)
    )
    db.add(doc8r)
    db.add(doc8f)
    print(f"  ✓ #{p8.id} FACTURADA con ⚠ alerta monto — Distribuidora Eléctrica (MTR2)")

    # 9. PAGADA — ciclo completo
    p9 = make_purchase(
        "MTR1", "Seguridad", "Seguridad Industrial MTR",
        "Guantes de nitrilo talla L x 100 pares, resistencia química",
        "Stock mensual EPP laboratorio de pintura, protocolo ISO 45001",
        12800.00,
        users["planta_mtr1"], "pagada", ago_days=30,
        authorized_by_id=users["autorizador"].id,
        authorized_at=datetime.utcnow() - timedelta(days=29)
    )
    add_audit(db, p9.id, users["planta_mtr1"].id, "created", None, "pendiente", ago_hours=30*24+1)
    add_audit(db, p9.id, users["autorizador"].id, "approved", "pendiente", "aprobada", ago_hours=29*24)
    add_audit(db, p9.id, users["planta_mtr1"].id, "received", "aprobada", "recibida", ago_hours=27*24)
    add_audit(db, p9.id, users["admin"].id, "invoiced", "recibida", "facturada", ago_hours=25*24)
    add_audit(db, p9.id, users["admin"].id, "paid", "facturada", "pagada", ago_hours=22*24)
    doc9r = models.Document(
        purchase_id=p9.id, doc_type="remito",
        file_url=SAMPLE_DOC_URL, filename="remito_guantes_nitrilo_R-7721.pdf",
        uploaded_by_id=users["planta_mtr1"].id,
        uploaded_at=datetime.utcnow() - timedelta(days=27)
    )
    doc9f = models.Document(
        purchase_id=p9.id, doc_type="factura",
        file_url=SAMPLE_DOC_URL, filename="factura_seguridad_A-0001-00009834.pdf",
        invoice_number="A-0001-00009834",
        invoice_date="2025-04-10",
        invoice_amount=12800.00,
        uploaded_by_id=users["admin"].id,
        uploaded_at=datetime.utcnow() - timedelta(days=25)
    )
    db.add(doc9r)
    db.add(doc9f)
    print(f"  ✓ #{p9.id} PAGADA — ciclo completo — Seguridad Industrial (MTR1)")

    # 10. RECHAZADA
    p10 = make_purchase(
        "MTR2", "Administración", "Neumáticos Litoral",
        "Monitor 27 pulgadas 4K para oficina, marca LG",
        "Mejora de productividad en área de control de calidad",
        180000.00,
        users["planta_mtr2"], "rechazada", ago_days=12,
        rejection_reason="No corresponde a insumos operativos. Gestionar por presupuesto IT con aprobación de gerencia."
    )
    add_audit(db, p10.id, users["planta_mtr2"].id, "created", None, "pendiente", ago_hours=12*24+1)
    add_audit(db, p10.id, users["autorizador"].id, "rejected", "pendiente", "rechazada",
              "No corresponde a insumos operativos. Gestionar por presupuesto IT con aprobación de gerencia.",
              ago_hours=11*24)
    print(f"  ✓ #{p10.id} RECHAZADA — Neumáticos Litoral (MTR2)")

    # Guardar IDs antes de cerrar la sesión
    ids = [p1.id, p2.id, p3.id, p4.id, p5.id, p6.id, p7.id, p8.id, p9.id, p10.id]
    db.commit()
    db.close()

    print("\n" + "="*55)
    print("  SEED COMPLETADO")
    print("="*55)
    print("\n  Usuarios de prueba (contraseña: mtr1234)")
    print("  ─────────────────────────────────────────")
    print("  carlos@mtr.com   → Planta MTR1")
    print("  maria@mtr.com    → Planta MTR2")
    print("  roberto@mtr.com  → Autorizador (ambas plantas)")
    print("  laura@mtr.com    → Administración (Rosario)")
    print("  admin@mtr.com    → Superadmin")
    print()
    print("  Compras creadas:")
    labels = [
        "PENDIENTE  — Tornillería (MTR1)",
        "PENDIENTE  — Aceite hidráulico (MTR2)",
        "APROBADA   — EPP cascos (MTR1, sin remito)",
        "APROBADA   — Neumáticos (MTR2, sin remito)",
        "RECIBIDA   — Cable eléctrico (MTR1, sin factura)",
        "RECIBIDA   — Herramientas (MTR2, sin factura)",
        "FACTURADA  — Grasa (MTR1, pendiente pago)",
        "FACTURADA  — Variador ⚠ alerta monto (MTR2)",
        "PAGADA     — Guantes EPP (MTR1, ciclo completo)",
        "RECHAZADA  — Monitor IT (MTR2)",
    ]
    for i, (pid, label) in enumerate(zip(ids, labels)):
        print(f"  #{pid:2}  {label}")
    print()

if __name__ == "__main__":
    do_reset = "--reset" in sys.argv
    run(reset=do_reset)
