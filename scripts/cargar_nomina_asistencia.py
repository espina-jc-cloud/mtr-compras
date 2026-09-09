"""
Carga inicial de la nómina de Asistencia desde la planilla física de portería
("CONTROL DE INGRESO A PLANTA", foto del 04/09/2026).

Las 19 personas van en el ORDEN DEL PAPEL, no alfabético: quien carga lee la
planilla de arriba hacia abajo y si la pantalla no respeta ese orden se saltean
filas. Las cuatro últimas (Lagamma, Ortiz, Ríos Tomás, Romero Ángel) están
agregadas a mano al pie de la planilla y se respetan en esa posición.

Pendiente de completar por el usuario (no bloquea nada):
  - sector de cada persona → hoy quedan sin asignar
  - legajo y DNI          → opcionales
  - planta                → asumo una sola planilla

Idempotente: si la persona ya existe (por apellido + nombre) la saltea, así se
puede correr las veces que haga falta sin duplicar.

Uso:  .venv/bin/python scripts/cargar_nomina_asistencia.py
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import SessionLocal
from app import models  # noqa: F401 — registra Supplier para la relación de Persona
from app.models_asistencia import AsistenciaPersona, AsistenciaJornadaTipo

# (apellido, nombre) en el orden exacto de la planilla física.
NOMINA = [
    ("Achaval",      "Edgardo F."),
    ("Alcaraz",      "Kevin Jonatán"),
    ("Báez",         "Vanina"),
    ("González",     "Leonardo S."),
    ("Mayor Duarte", "Gustavo"),
    ("Mendoza",      "Enzo"),
    ("Morales",      "Juan Ignacio"),
    ("Morales",      "Santiago Nicolás"),
    ("Ojea",         "Mario Osvaldo"),
    ("Ríos",         "Carlos Alberto"),
    ("Romero",       "Agustín Emanuel"),
    ("Romero",       "Claudio E."),
    ("Romero",       "C. Ezequiel"),
    ("Salinas",      "Iván"),
    ("Silva",        "Maximiliano"),
    # ── Agregados a mano al pie de la planilla ────────────────────────────────
    ("Lagamma",      "Facundo"),
    ("Ortiz",        "Miguel"),
    ("Ríos",         "Tomás"),
    ("Romero",       "Ángel"),
]


def run():
    db = SessionLocal()
    creados, salteados = 0, 0
    try:
        jornada = (
            db.query(AsistenciaJornadaTipo)
            .filter(AsistenciaJornadaTipo.activo == True)  # noqa: E712
            .order_by(AsistenciaJornadaTipo.id)
            .first()
        )
        if jornada is None:
            print("✗ No hay jornada tipo cargada. Corré primero: python3 migrate.py")
            return

        for i, (apellido, nombre) in enumerate(NOMINA, start=1):
            ya_esta = (
                db.query(AsistenciaPersona)
                .filter(
                    AsistenciaPersona.apellido == apellido,
                    AsistenciaPersona.nombre == nombre,
                )
                .first()
            )
            if ya_esta:
                salteados += 1
                continue

            db.add(AsistenciaPersona(
                apellido=apellido,
                nombre=nombre,
                tipo="mtr",
                jornada_tipo_id=jornada.id,
                orden_planilla=i * 10,   # deja lugar para intercalar sin renumerar
                activo=True,
            ))
            creados += 1

        db.commit()
        print(f"✅ Nómina de asistencia: {creados} personas creadas, "
              f"{salteados} ya existían.")
        print(f"   Jornada asignada: {jornada.nombre}")
        print("   Sin sector asignado — completar desde la UI para que el "
              "ranking por sector tenga sentido.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run()
