import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from dotenv import load_dotenv

load_dotenv()

_url = os.getenv("DATABASE_URL", "sqlite:///./mtr_compras.db")

# Railway entrega postgresql:// — SQLAlchemy necesita postgresql+psycopg2://
if _url.startswith("postgres://"):
    _url = _url.replace("postgres://", "postgresql+psycopg2://", 1)
elif _url.startswith("postgresql://") and "+psycopg2" not in _url:
    _url = _url.replace("postgresql://", "postgresql+psycopg2://", 1)

DATABASE_URL = _url

_is_sqlite = DATABASE_URL.startswith("sqlite")

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
