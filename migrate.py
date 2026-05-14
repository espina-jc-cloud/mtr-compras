#!/usr/bin/env python3
import os
from dotenv import load_dotenv
load_dotenv()

from app.database import engine, Base, SessionLocal
from app import models
from app.auth import hash_password

def run():
    Base.metadata.create_all(bind=engine)
    print("✓ Tablas creadas")

    db = SessionLocal()
    admin_email = os.getenv("FIRST_ADMIN_EMAIL", "admin@mtr.com")
    admin_password = os.getenv("FIRST_ADMIN_PASSWORD", "changeme123")

    existing = db.query(models.User).filter(models.User.email == admin_email).first()
    if not existing:
        admin = models.User(
            name="Administrador",
            email=admin_email,
            hashed_password=hash_password(admin_password),
            role="superadmin",
            plant="TODAS"
        )
        db.add(admin)
        db.commit()
        print(f"✓ Usuario superadmin creado: {admin_email}")
    else:
        print(f"✓ Superadmin ya existe: {admin_email}")

    db.close()

if __name__ == "__main__":
    run()
