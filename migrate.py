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

from app.database import engine, Base, DATABASE_URL, SessionLocal
from app import models
from app.auth import hash_password


def run():
    is_prod = not DATABASE_URL.startswith("sqlite")

    Base.metadata.create_all(bind=engine)
    print(f"✓ Tablas creadas ({DATABASE_URL.split('@')[-1] if '@' in DATABASE_URL else DATABASE_URL})")

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
