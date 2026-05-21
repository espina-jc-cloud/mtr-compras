#!/usr/bin/env python3
"""
Crea tablas y genera el primer superadmin si no existe.
Se ejecuta automáticamente al iniciar en Railway.

Variables de entorno:
  FIRST_ADMIN_EMAIL     (default: admin@mtr.com)
  FIRST_ADMIN_PASSWORD  (requerida en producción — falla si no está)
"""
import os
import sys
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text
from app.database import engine, Base, DATABASE_URL, SessionLocal
from app import models
from app.auth import hash_password


def _add_column(conn, table, column, col_type):
    """Agrega una columna si no existe. Compatible con SQLite y PostgreSQL."""
    try:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
        conn.commit()
        print(f"  + columna {table}.{column} agregada")
    except Exception:
        pass  # ya existe


def run():
    is_prod = not DATABASE_URL.startswith("sqlite")

    Base.metadata.create_all(bind=engine)
    print(f"✓ Tablas creadas ({DATABASE_URL.split('@')[-1] if '@' in DATABASE_URL else DATABASE_URL})")

    # ── Migraciones seguras de columnas nuevas ────────────────────────────────
    with engine.connect() as conn:
        _add_column(conn, "purchases", "purchase_date",  "TIMESTAMP")
        _add_column(conn, "purchases", "deleted_at",     "TIMESTAMP")
        _add_column(conn, "purchases", "deleted_reason", "TEXT")
        _add_column(conn, "documents", "remito_date",    "VARCHAR")
    print("✓ Columnas nuevas verificadas")

    admin_email = os.getenv("FIRST_ADMIN_EMAIL", "admin@mtr.com")
    admin_password = os.getenv("FIRST_ADMIN_PASSWORD", "")

    if not admin_password:
        if is_prod:
            print("✗ ERROR: FIRST_ADMIN_PASSWORD no está definida. Abortando.", file=sys.stderr)
            sys.exit(1)
        else:
            admin_password = "changeme123"
            print("⚠ Usando contraseña local por defecto: changeme123")

    db = SessionLocal()
    existing = db.query(models.User).filter(models.User.email == admin_email).first()
    if not existing:
        admin = models.User(
            name="Administrador",
            email=admin_email,
            hashed_password=hash_password(admin_password),
            role="superadmin",
            plant="TODAS",
        )
        db.add(admin)
        db.commit()
        print(f"✓ Superadmin creado: {admin_email}")
    else:
        print(f"✓ Superadmin ya existe: {admin_email}")

    db.close()


if __name__ == "__main__":
    run()
