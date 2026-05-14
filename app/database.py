import os
import sys
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from dotenv import load_dotenv

load_dotenv()

_raw = os.getenv("DATABASE_URL", "").strip()

# String vacío → tratar como "no definida"
if not _raw:
    _raw = "sqlite:///./mtr_compras.db"

# Railway entrega postgres:// o postgresql:// sin driver explícito
if _raw.startswith("postgres://"):
    _raw = _raw.replace("postgres://", "postgresql+psycopg2://", 1)
elif _raw.startswith("postgresql://") and "+psycopg2" not in _raw:
    _raw = _raw.replace("postgresql://", "postgresql+psycopg2://", 1)

DATABASE_URL = _raw
_is_sqlite = DATABASE_URL.startswith("sqlite")

# Log seguro: nunca imprime usuario/password ni URL completa
_is_railway = bool(os.getenv("RAILWAY_ENVIRONMENT"))
if _is_sqlite:
    if _is_railway:
        print("✗ FATAL: corriendo en Railway sin PostgreSQL configurado. "
              "Agregá el plugin Postgres y referenciá DATABASE_URL.", file=sys.stderr)
        sys.exit(1)
    else:
        print("[DB] SQLite local → mtr_compras.db")
else:
    try:
        _host = DATABASE_URL.split("@")[-1].split("/")[0]
        print(f"[DB] PostgreSQL → {_host}")
    except Exception:
        print("[DB] PostgreSQL")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
