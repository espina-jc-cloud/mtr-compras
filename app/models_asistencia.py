"""
Módulo Asistencia — control diario de asistencia, horas extra y personal
tercerizado.

Objetivo del módulo (ver DISCOVERY Asistencia MTR v0.2):
  MTR descubre las horas extra a fin de mes. Este módulo las muestra al día
  siguiente, con su motivo y su operativo asociado.

────────────────────────────────────────────────────────────────────────────────
ARQUITECTURA DE TRES CAPAS — la decisión central del módulo

    marcaciones          hechos crudos (IN / OUT). Append-only: nunca se
        │                editan ni se borran, se anulan y se crean de nuevo.
        ↓  derivar_bloques()
    bloques              tramos continuos IN→OUT, clasificados según si
        │                solapan o no la jornada esperada.
        ↓  recalcular_jornada()
    jornadas             una fila por persona × fecha operativa, con los
                         totales y las decisiones humanas.

La capa de bloques existe por los operativos de buque de 24 h: una persona
puede trabajar 08:00-16:00, irse, y volver 21:45-06:10 el mismo día operativo.
Con una sola dupla ingreso/egreso ese caso no se puede representar.

────────────────────────────────────────────────────────────────────────────────
REGLA DE ORO DEL RECÁLCULO

`asistencia_jornadas` tiene tres clases de campos:

  DERIVADOS   se recalculan siempre que cambian las marcaciones, la config,
              un feriado o la jornada esperada.
  DECIDIDOS   los fija una persona (extra aprobada, motivo, estado). El
              recálculo NUNCA los toca. Si el derivado cambia y había una
              decisión tomada, se marca `revisar=True` y aparece en el
              tablero — jamás se corrige en silencio.
  SNAPSHOT    la jornada esperada y el sector vigentes el día de los hechos.
              Se copian al crear la jornada, igual que hace
              TransporteOperativoAsignacion con los datos del chofer, para
              que un cambio de horario no reescriba la historia hacia atrás.

────────────────────────────────────────────────────────────────────────────────
ZONA HORARIA

El resto del sistema guarda UTC naive y renderiza restando 3 h (fmt_ar).
Acá NO: las marcaciones son hora de pared de la planta, tal cual figura en la
planilla de portería, y se guardan naive sin conversión. Un 08:00 en el papel
es un 08:00 en la base. Convertirlas sería introducir un error de 3 h en el
único módulo donde la hora exacta es el dato.

Los `created_at` administrativos sí siguen la convención del repo (utcnow).
"""
from datetime import datetime

from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Date, ForeignKey, Text,
    Numeric, UniqueConstraint, Index,
)
from sqlalchemy.orm import relationship

from app.database import Base


# ══════════════════════════════════════════════════════════════════════════════
# Catálogos y constantes
# ══════════════════════════════════════════════════════════════════════════════

TIPOS_PERSONA = [
    ("mtr",     "Personal MTR"),
    ("tercero", "Tercerizado"),
]

# Tipo de extra — separarlos es lo que permite distinguir "gente que se queda
# de más" (un problema de gestión de jornada) de "gente que viene fuera de
# horario por un barco" (un costo de operativo). Son dos conversaciones
# distintas y sumarlas en un solo número las esconde a las dos.
# Recargo de la hora extra. Es el eje que decide el costo, y por eso el que
# separa los totales en todos los tableros.
#   50 %  → exceso sobre la jornada exigida en día hábil, y sábado hasta las 13.
#   100 % → sábado desde las 13, domingo y feriado.
TIPOS_EXTRA = [
    ("50",  "Extra al 50 %"),
    ("100", "Extra al 100 %"),
]

ESTADOS_EXTRA = [
    ("na",          "Sin extra"),
    ("pendiente",   "Pendiente"),
    ("justificada", "Justificada"),
    ("aprobada",    "Aprobada"),
    ("rechazada",   "Rechazada"),
]
ESTADO_EXTRA_CSS = {
    "na":          "bg-gray-50 text-gray-500",
    "pendiente":   "bg-amber-50 text-amber-700",
    "justificada": "bg-blue-50 text-blue-700",
    "aprobada":    "bg-emerald-50 text-emerald-700",
    "rechazada":   "bg-red-50 text-red-700",
}

# Estado del registro — la calidad del dato, independiente de la extra.
ESTADOS_REGISTRO = [
    ("ok",             "Correcto"),
    ("sin_egreso",     "Sin egreso"),
    ("sin_ingreso",    "Sin ingreso"),
    ("inconsistente",  "Inconsistente"),
    ("ausente",        "Ausente"),
]
ESTADO_REGISTRO_CSS = {
    "ok":            "bg-emerald-50 text-emerald-700",
    "sin_egreso":    "bg-gray-100 text-gray-700",
    "sin_ingreso":   "bg-gray-100 text-gray-700",
    "inconsistente": "bg-red-50 text-red-700",
    "ausente":       "bg-gray-50 text-gray-400",
}

# Turnos de operativo — MISMOS rangos que usa el módulo Operativos Live
# (OperationLiveShift.turno_range). No se inventa una nomenclatura paralela.
TURNOS_OPERATIVO = [
    ("00A06", "00:00 a 06:00", "00:00", "06:00"),
    ("06A12", "06:00 a 12:00", "06:00", "12:00"),
    ("12A18", "12:00 a 18:00", "12:00", "18:00"),
    ("18A00", "18:00 a 00:00", "18:00", "00:00"),
]

# División del personal. Decisión del 07/09/2026: NO se usan sectores — la
# gente se divide por planta y nada más. Las tablas asistencia_sectores /
# .sector_id quedan inertes (cambio no destructivo), sin uso en la UI.
PLANTAS = [
    ("MTR1", "MTR1"),
    ("MTR2", "MTR2"),
]

DIAS_SEMANA = [
    (0, "Lunes"), (1, "Martes"), (2, "Miércoles"), (3, "Jueves"),
    (4, "Viernes"), (5, "Sábado"), (6, "Domingo"),
]


# ── Helpers de hora ───────────────────────────────────────────────────────────
# Los horarios esperados se guardan como 'HH:MM' (legibles al inspeccionar la
# base, consistente con el resto del repo: OperationLiveEquipment.desde/hasta,
# ServicioEquipo.hora_inicio). El cálculo trabaja en minutos desde medianoche.

def hhmm_a_min(valor):
    """'08:30' → 510. Devuelve None si el valor no es una hora válida."""
    if not valor:
        return None
    txt = str(valor).strip()
    if ":" not in txt:
        # Tolera '0800' y '800' — la carga rápida escribe así.
        txt = txt.zfill(4)
        txt = f"{txt[:2]}:{txt[2:]}"
    try:
        h, m = txt.split(":")[:2]
        h, m = int(h), int(m)
    except (ValueError, TypeError):
        return None
    if not (0 <= h <= 24 and 0 <= m <= 59):
        return None
    return h * 60 + m


def min_a_hhmm(minutos):
    """510 → '08:30'. Soporta valores ≥ 24 h para bloques que cruzan medianoche."""
    if minutos is None:
        return ""
    minutos = int(minutos)
    signo = "-" if minutos < 0 else ""
    minutos = abs(minutos)
    return f"{signo}{minutos // 60:02d}:{minutos % 60:02d}"


def min_a_texto(minutos):
    """510 → '8h30'. Formato corto para KPIs y tablas."""
    if minutos is None:
        return "—"
    minutos = int(minutos)
    if minutos == 0:
        return "—"
    signo = "-" if minutos < 0 else ""
    minutos = abs(minutos)
    h, m = minutos // 60, minutos % 60
    if h and m:
        return f"{signo}{h}h{m:02d}"
    if h:
        return f"{signo}{h}h"
    return f"{signo}{m}m"


# ══════════════════════════════════════════════════════════════════════════════
# Maestros
# ══════════════════════════════════════════════════════════════════════════════

class AsistenciaSector(Base):
    """Depósito, Puerto, Mantenimiento, Administración…

    Tabla y no enum: la lista va a cambiar y la edita el usuario desde la UI.
    """
    __tablename__ = "asistencia_sectores"

    id      = Column(Integer, primary_key=True, index=True)
    nombre  = Column(String(120), nullable=False, unique=True)
    orden   = Column(Integer, nullable=False, default=100)
    activo  = Column(Boolean, default=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    personas = relationship("AsistenciaPersona", back_populates="sector")


class AsistenciaJornadaTipo(Base):
    """Patrón horario reutilizable. Ej: 'MTR Planta' = L-V 08-16, sáb 08-12."""
    __tablename__ = "asistencia_jornadas_tipo"

    id     = Column(Integer, primary_key=True, index=True)
    nombre = Column(String(120), nullable=False, unique=True)

    # Minutos de pausa NO trabajada dentro de la jornada. 0 = jornada corrida.
    # Solo afecta minutos_trabajados; nunca al cálculo de la extra.
    pausa_min = Column(Integer, nullable=False, default=0)

    # Tolerancias propias del patrón. NULL = usar las de asistencia_config.
    # El turno de puerto y el de administración no toleran lo mismo.
    tolerancia_salida_min  = Column(Integer, nullable=True)
    tolerancia_entrada_min = Column(Integer, nullable=True)

    # Régimen rotativo con francos: el personal de portería cubre turnos de 8 h
    # que rotan (00-08, 08-16, 16-00) y compensa con francos, así que su balance
    # NO se mide día a día. Con esto en False no genera ni extra ni horas no
    # cumplidas; se le siguen registrando y mostrando las horas trabajadas.
    computa_extra = Column(Boolean, nullable=False, default=True)

    activo     = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    tramos = relationship(
        "AsistenciaJornadaTramo",
        back_populates="jornada_tipo",
        cascade="all, delete-orphan",
        order_by="AsistenciaJornadaTramo.dia_semana",
    )
    personas = relationship("AsistenciaPersona", back_populates="jornada_tipo")


class AsistenciaJornadaTramo(Base):
    """Un día de la semana dentro de un patrón horario.

    dia_semana usa la convención de Python (date.weekday()): 0 = lunes.
    laborable=False define el domingo sin necesidad de una fila especial.
    """
    __tablename__ = "asistencia_jornada_tramos"

    id              = Column(Integer, primary_key=True, index=True)
    jornada_tipo_id = Column(Integer, ForeignKey("asistencia_jornadas_tipo.id"),
                             nullable=False, index=True)
    dia_semana      = Column(Integer, nullable=False)   # 0=lun … 6=dom
    laborable       = Column(Boolean, nullable=False, default=True)
    # Horario de REFERENCIA, no una ventana obligatoria: lo que se exige es la
    # duración (hora_salida − hora_entrada), cumplible en cualquier franja.
    hora_entrada    = Column(String(5), nullable=True)  # 'HH:MM'
    hora_salida     = Column(String(5), nullable=True)  # 'HH:MM'

    # ── Franjas de recargo ───────────────────────────────────────────────────
    # hora_limite_normal: hasta qué hora del día se pueden cumplir las horas
    #   exigidas. NULL = todo el día (lunes a viernes). Sábado = '12:00'.
    # hora_desde_100: a partir de qué hora TODO es al 100 %, se haya cumplido
    #   la jornada o no. NULL = nunca. Sábado = '13:00'.
    hora_limite_normal = Column(String(5), nullable=True)
    hora_desde_100     = Column(String(5), nullable=True)

    jornada_tipo = relationship("AsistenciaJornadaTipo", back_populates="tramos")

    __table_args__ = (
        UniqueConstraint("jornada_tipo_id", "dia_semana",
                         name="uq_asistencia_tramo_dia"),
    )


class AsistenciaPersona(Base):
    """Maestro ÚNICO de personas: propias de MTR y tercerizadas.

    Una sola tabla con discriminador `tipo` en vez de dos tablas paralelas.
    Dos tablas obligarían a duplicar marcaciones, bloques, jornadas, auditoría,
    cálculo, tablero y export — todo el pipeline por partida doble, para
    volver a unirlo en cada pantalla.

    La regla "no mezclar horas MTR con horas tercerizadas" es de AGREGACIÓN,
    no de almacenamiento: ningún KPI suma los dos tipos. Se cumple mejor así,
    porque los totales están separados por diseño y no por convención.
    """
    __tablename__ = "asistencia_personas"

    id       = Column(Integer, primary_key=True, index=True)
    apellido = Column(String(150), nullable=False, index=True)
    nombre   = Column(String(150), nullable=False)
    legajo   = Column(String(30),  nullable=True)
    dni      = Column(String(20),  nullable=True, index=True)

    tipo = Column(String(10), nullable=False, default="mtr", index=True)  # mtr | tercero

    sector_id = Column(Integer, ForeignKey("asistencia_sectores.id"), nullable=True, index=True)

    # Solo tercerizados. Reutiliza el maestro de proveedores que ya existe:
    # la empresa de bolseros ES un proveedor, no una entidad nueva.
    proveedor_id = Column(Integer, ForeignKey("suppliers.id"), nullable=True, index=True)
    funcion      = Column(String(120), nullable=True)   # 'Bolsero', 'Maquinista'…

    # Subdivisión tal cual figura en la planilla de horas: 'M.T.R. S.A.',
    # 'INGEE S.R.L.', 'PORTERIA MTR I'. Todos son personal PROPIO; el grupo es
    # una división operativa que MTR quiere seguir viendo, no un proveedor.
    grupo        = Column(String(120), nullable=True, index=True)

    # Solo personal MTR. Los tercerizados sacan su esperado del pedido.
    jornada_tipo_id = Column(Integer, ForeignKey("asistencia_jornadas_tipo.id"),
                             nullable=True, index=True)

    planta = Column(String(20), nullable=True, index=True)  # MTR1 | MTR2 | ROSARIO

    # Orden en el que la persona figura en la planilla física. Quien carga lee
    # el papel de arriba hacia abajo; si la pantalla no respeta ese orden, se
    # saltean filas. No es cosmético.
    orden_planilla = Column(Integer, nullable=False, default=1000)

    activo      = Column(Boolean, default=True, index=True)
    fecha_alta  = Column(Date, nullable=True)
    fecha_baja  = Column(Date, nullable=True)
    observaciones = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    sector       = relationship("AsistenciaSector", back_populates="personas")
    jornada_tipo = relationship("AsistenciaJornadaTipo", back_populates="personas")
    proveedor    = relationship("Supplier")

    __table_args__ = (
        Index("ix_asistencia_personas_activo_orden", "activo", "orden_planilla"),
    )

    @property
    def nombre_completo(self):
        return f"{self.apellido}, {self.nombre}"


class AsistenciaExcepcionJornada(Base):
    """Override de jornada esperada para una persona en una fecha puntual.

    Turno rotativo por operativo, franco compensatorio, cambio de horario.
    Puede crearse en masa por sector desde la UI.
    """
    __tablename__ = "asistencia_excepciones_jornada"

    id         = Column(Integer, primary_key=True, index=True)
    persona_id = Column(Integer, ForeignKey("asistencia_personas.id"),
                        nullable=False, index=True)
    fecha      = Column(Date, nullable=False, index=True)

    laborable    = Column(Boolean, nullable=False, default=True)
    hora_entrada = Column(String(5), nullable=True)
    hora_salida  = Column(String(5), nullable=True)
    motivo       = Column(String(300), nullable=True)

    created_at    = Column(DateTime, default=datetime.utcnow)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    persona    = relationship("AsistenciaPersona")
    created_by = relationship("User", foreign_keys=[created_by_id])

    __table_args__ = (
        UniqueConstraint("persona_id", "fecha", name="uq_asistencia_excepcion"),
    )


class AsistenciaFeriado(Base):
    """Feriados y días no laborables de MTR."""
    __tablename__ = "asistencia_feriados"

    id     = Column(Integer, primary_key=True, index=True)
    fecha  = Column(Date, nullable=False, unique=True, index=True)
    nombre = Column(String(200), nullable=False)
    tipo   = Column(String(30), nullable=False, default="nacional")
    # tipo: nacional | puente | no_laborable_mtr

    created_at = Column(DateTime, default=datetime.utcnow)


class AsistenciaMotivo(Base):
    """Catálogo configurable de motivos de hora extra."""
    __tablename__ = "asistencia_motivos"

    id     = Column(Integer, primary_key=True, index=True)
    nombre = Column(String(150), nullable=False, unique=True)
    orden  = Column(Integer, nullable=False, default=100)

    requiere_detalle = Column(Boolean, default=False)
    # Si requiere_buque, la UI pide elegir una sesión live en vez de texto libre.
    requiere_buque   = Column(Boolean, default=False)

    activo     = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# ══════════════════════════════════════════════════════════════════════════════
# Capa 1 — Marcaciones (los hechos)
# ══════════════════════════════════════════════════════════════════════════════

class AsistenciaMarcacion(Base):
    """Un evento de entrada o salida. APPEND-ONLY.

    Nunca se hace UPDATE sobre `ts` ni DELETE de una fila. Corregir 08:00 → 09:00
    es marcar `anulada_at` en la vieja y crear una nueva con fuente='correccion'.
    Así la historia queda por construcción y no por disciplina del programador,
    que es lo que exige el requisito de auditoría.

    `ts` es hora local de planta, naive. Ver la nota de zona horaria arriba.
    """
    __tablename__ = "asistencia_marcaciones"

    id         = Column(Integer, primary_key=True, index=True)
    persona_id = Column(Integer, ForeignKey("asistencia_personas.id"),
                        nullable=False, index=True)

    ts   = Column(DateTime, nullable=False, index=True)
    tipo = Column(String(3), nullable=False)   # 'IN' | 'OUT'

    # Fecha operativa a la que se imputa. Para un IN es la fecha de `ts`; para
    # un OUT que cruza medianoche es la fecha del IN de su bloque. Se guarda
    # explícita para poder consultar un día sin reconstruir bloques.
    fecha_operativa = Column(Date, nullable=False, index=True)

    fuente = Column(String(20), nullable=False, default="planilla")
    # fuente: planilla | porteria | correccion | import | turno_operativo

    nota            = Column(Text, nullable=True)
    usuario_carga_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at      = Column(DateTime, default=datetime.utcnow)

    anulada_at    = Column(DateTime, nullable=True, index=True)
    anulada_por_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    anulada_motivo = Column(Text, nullable=True)

    persona       = relationship("AsistenciaPersona")
    usuario_carga = relationship("User", foreign_keys=[usuario_carga_id])
    anulada_por   = relationship("User", foreign_keys=[anulada_por_id])

    __table_args__ = (
        Index("ix_asistencia_marc_persona_fecha", "persona_id", "fecha_operativa"),
    )


# ══════════════════════════════════════════════════════════════════════════════
# Capa 2 — Bloques (derivado)
# ══════════════════════════════════════════════════════════════════════════════

class AsistenciaBloque(Base):
    """Un tramo continuo de presencia: un par IN→OUT.

    Es la capa que resuelve los operativos de buque de 24 h. Una persona puede
    tener dos bloques en la misma fecha operativa:

        bloque 1  07:58 → 16:05   solapa la jornada 08-16   → tipo 'jornada'
        bloque 2  21:45 → 06:10   no toca la jornada        → tipo 'llamado'

    Totalmente derivado de las marcaciones: se borra y se reconstruye en cada
    recálculo. Ningún dato humano vive acá.
    """
    __tablename__ = "asistencia_bloques"

    id         = Column(Integer, primary_key=True, index=True)
    persona_id = Column(Integer, ForeignKey("asistencia_personas.id"),
                        nullable=False, index=True)
    fecha_operativa = Column(Date, nullable=False, index=True)

    desde = Column(DateTime, nullable=False)
    hasta = Column(DateTime, nullable=True)   # NULL = bloque sin egreso

    minutos = Column(Integer, nullable=True)  # NULL mientras no haya egreso

    # 'jornada' si solapa aunque sea un minuto con el horario esperado;
    # 'llamado' si no lo toca; 'no_laborable' si el día no tiene jornada.
    tipo = Column(String(20), nullable=False, default="jornada")

    solapa_jornada = Column(Boolean, nullable=False, default=False)

    # Turno de operativo, cuando el bloque se cargó con el modo turno.
    turno_operativo = Column(String(10), nullable=True)   # 00A06 | 06A12 | …

    marcacion_in_id  = Column(Integer, ForeignKey("asistencia_marcaciones.id"), nullable=True)
    marcacion_out_id = Column(Integer, ForeignKey("asistencia_marcaciones.id"), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    persona = relationship("AsistenciaPersona")

    __table_args__ = (
        Index("ix_asistencia_bloques_persona_fecha", "persona_id", "fecha_operativa"),
    )


# ══════════════════════════════════════════════════════════════════════════════
# Capa 3 — Jornada (derivado + snapshot + decisiones)
# ══════════════════════════════════════════════════════════════════════════════

class AsistenciaJornada(Base):
    """Una fila por persona × fecha operativa. Es la unidad de gestión.

    Ver la REGLA DE ORO DEL RECÁLCULO en el encabezado del módulo: los campos
    están agrupados abajo por clase y el recálculo solo puede tocar el primer
    grupo.
    """
    __tablename__ = "asistencia_jornadas"

    id         = Column(Integer, primary_key=True, index=True)
    persona_id = Column(Integer, ForeignKey("asistencia_personas.id"),
                        nullable=False, index=True)
    fecha      = Column(Date, nullable=False, index=True)   # fecha OPERATIVA

    # ── SNAPSHOT — congelado al crear la jornada ─────────────────────────────
    # Si mañana cambia el horario o el sector de la persona, los días pasados
    # no se reescriben. Mismo criterio que TransporteOperativoAsignacion.
    laborable_esp            = Column(Boolean, nullable=False, default=True)
    hora_entrada_esp         = Column(String(5), nullable=True)
    hora_salida_esp          = Column(String(5), nullable=True)
    minutos_esperados        = Column(Integer, nullable=False, default=0)
    # La planta sale de la PLANILLA que se importó, no del maestro: Báez rota
    # entre plantas y en la planilla de MTR1 su jornada es de MTR1.
    planta_snap              = Column(String(20), nullable=True)
    grupo_snap               = Column(String(120), nullable=True)
    sector_snap              = Column(String(120), nullable=True)  # deprecado
    tipo_persona_snap        = Column(String(10), nullable=False, default="mtr")
    proveedor_snap           = Column(String(200), nullable=True)
    tolerancia_salida_snap   = Column(Integer, nullable=False, default=0)
    computa_extra_snap       = Column(Boolean, nullable=False, default=True)

    # ── DERIVADOS — se recalculan siempre ────────────────────────────────────
    ingreso_real       = Column(DateTime, nullable=True)   # primer IN del día
    egreso_real        = Column(DateTime, nullable=True)   # último OUT del día
    cantidad_bloques   = Column(Integer, nullable=False, default=0)
    minutos_presencia  = Column(Integer, nullable=False, default=0)
    minutos_trabajados = Column(Integer, nullable=False, default=0)

    # ── Extra por RECARGO — el eje que importa para el costo ─────────────────
    minutos_extra_50  = Column(Integer, nullable=False, default=0)
    minutos_extra_100 = Column(Integer, nullable=False, default=0)
    minutos_extra_detectada = Column(Integer, nullable=False, default=0)  # suma redondeada
    tipo_extra_dominante    = Column(String(20), nullable=True)  # '50' | '100'

    # Informativos / compatibilidad. `minutos_extra_prolongacion` quedó
    # deprecada al pasar de "extra contra la hora de salida" a "extra contra la
    # duración exigida": se deja en 0 y se dropea en una migración futura.
    minutos_extra_prolongacion = Column(Integer, nullable=False, default=0)
    # Minutos trabajados fuera del horario de referencia (ej. el turno noche).
    # NO es extra — con jornada por duración, la noche es jornada normal. Sirve
    # para ver cuánta gente se está movilizando fuera de horario por operativos.
    minutos_fuera_horario = Column("minutos_extra_llamado", Integer, nullable=False, default=0)
    # Extra de domingo o feriado: subconjunto de minutos_extra_100.
    minutos_extra_no_laborable = Column(Integer, nullable=False, default=0)

    minutos_defecto     = Column(Integer, nullable=False, default=0)  # salida anticipada
    entrada_anticipada  = Column(Integer, nullable=False, default=0)  # informativo
    llegada_tarde       = Column(Boolean, nullable=False, default=False)

    estado_registro = Column(String(20), nullable=False, default="ok", index=True)
    recalculada_at  = Column(DateTime, nullable=True)
    # Columna "Nota" del Excel diario: VACACIONES, BUQUE MANUCHAR, BARCO…
    # Es lo que decía el papel, no una decisión: el recálculo no la toca.
    nota_origen     = Column(String(300), nullable=True)

    # ── DECIDIDOS — solo los cambia una persona ──────────────────────────────
    minutos_extra_aprobada = Column(Integer, nullable=True)
    estado_extra    = Column(String(20), nullable=False, default="na", index=True)
    motivo_id       = Column(Integer, ForeignKey("asistencia_motivos.id"), nullable=True)
    motivo_detalle  = Column(String(300), nullable=True)

    # Atribución al operativo. FK real a la tabla del módulo Live: no se
    # duplica la entidad buque ni se escribe el nombre a mano.
    session_live_id = Column(Integer, ForeignKey("operation_live_sessions.id"),
                             nullable=True, index=True)

    aprobado_por_id      = Column(Integer, ForeignKey("users.id"), nullable=True)
    aprobado_at          = Column(DateTime, nullable=True)
    comentario_aprobacion = Column(Text, nullable=True)

    # True cuando un recálculo cambió la extra detectada de una jornada que ya
    # tenía decisión tomada (feriado cargado tarde, corrección posterior).
    # El sistema nunca corrige una aprobación en silencio: la marca y avisa.
    revisar = Column(Boolean, nullable=False, default=False, index=True)

    cerrada    = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    persona      = relationship("AsistenciaPersona")
    motivo       = relationship("AsistenciaMotivo")
    aprobado_por = relationship("User", foreign_keys=[aprobado_por_id])

    __table_args__ = (
        # Idempotencia de la carga: volver a cargar la misma planilla actualiza,
        # no duplica.
        UniqueConstraint("persona_id", "fecha", name="uq_asistencia_jornada"),
        Index("ix_asistencia_jornadas_fecha_estado", "fecha", "estado_extra"),
    )

    @property
    def minutos_extra_vigente(self):
        """La que vale para reportes de costo: aprobada si ya se decidió,
        detectada si todavía no. Nunca las suma."""
        if self.minutos_extra_aprobada is not None:
            return self.minutos_extra_aprobada
        return self.minutos_extra_detectada


# ══════════════════════════════════════════════════════════════════════════════
# Tercerizados
# ══════════════════════════════════════════════════════════════════════════════

class AsistenciaPedidoTercero(Base):
    """'Mandame 2 personas de 08:00 a 14:00.'

    El pedido es una entidad y no un comentario porque es la vara contra la
    cual se mide al tercero: sin pedido no se puede comparar lo solicitado con
    lo real, que es exactamente la diferencia que después no cuadra con la
    factura del proveedor.

    Deja preparado el control económico futuro sin implementarlo: horas reales
    salen de las jornadas, la tarifa sale del Tarifario existente (Tariff con
    owner='tercero'), y lo único que faltaría agregar es la factura recibida.
    """
    __tablename__ = "asistencia_pedidos_terceros"

    id           = Column(Integer, primary_key=True, index=True)
    proveedor_id = Column(Integer, ForeignKey("suppliers.id"), nullable=False, index=True)
    fecha        = Column(Date, nullable=False, index=True)

    funcion            = Column(String(120), nullable=True)   # 'Bolsero'
    cantidad_solicitada = Column(Integer, nullable=False, default=1)
    hora_desde         = Column(String(5), nullable=True)
    hora_hasta         = Column(String(5), nullable=True)
    turno_operativo    = Column(String(10), nullable=True)    # si vino por turno

    session_live_id = Column(Integer, ForeignKey("operation_live_sessions.id"), nullable=True)

    notas         = Column(Text, nullable=True)
    created_at    = Column(DateTime, default=datetime.utcnow)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    proveedor  = relationship("Supplier")
    created_by = relationship("User", foreign_keys=[created_by_id])

    @property
    def minutos_solicitados(self):
        d, h = hhmm_a_min(self.hora_desde), hhmm_a_min(self.hora_hasta)
        if d is None or h is None:
            return None
        if h <= d:            # cruza medianoche
            h += 24 * 60
        return (h - d) * (self.cantidad_solicitada or 0)


# ══════════════════════════════════════════════════════════════════════════════
# Gobierno: cierre y auditoría
# ══════════════════════════════════════════════════════════════════════════════

class AsistenciaCierre(Base):
    """Día o mes cerrado. Una vez cerrado, modificar requiere permiso superior."""
    __tablename__ = "asistencia_cierres"

    id      = Column(Integer, primary_key=True, index=True)
    periodo = Column(String(10), nullable=False)   # 'dia' | 'mes'
    desde   = Column(Date, nullable=False, index=True)
    hasta   = Column(Date, nullable=False)

    estado = Column(String(20), nullable=False, default="cerrado")  # cerrado | reabierto

    cerrado_por_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    cerrado_at     = Column(DateTime, default=datetime.utcnow)
    notas          = Column(Text, nullable=True)

    reabierto_por_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    reabierto_at     = Column(DateTime, nullable=True)
    reabierto_motivo = Column(Text, nullable=True)

    cerrado_por   = relationship("User", foreign_keys=[cerrado_por_id])
    reabierto_por = relationship("User", foreign_keys=[reabierto_por_id])

    __table_args__ = (
        UniqueConstraint("periodo", "desde", name="uq_asistencia_cierre"),
    )


class AsistenciaAuditLog(Base):
    """Auditoría del módulo. Mismo patrón que audit_log / maintenance_audit_log,
    con campos de valor porque acá se auditan VALORES y no solo transiciones.

    Se escribe siempre que cambia: una marcación (alta, anulación, corrección),
    la jornada esperada, la extra aprobada, el motivo, el estado, el alta o
    baja de una persona, la configuración, y el cierre o reapertura de un
    período.
    """
    __tablename__ = "asistencia_audit_log"

    id        = Column(Integer, primary_key=True, index=True)
    entidad   = Column(String(30), nullable=False, index=True)
    # entidad: jornada | marcacion | persona | excepcion | config | cierre | motivo
    entidad_id = Column(Integer, nullable=True, index=True)

    accion = Column(String(40), nullable=False)   # crear | editar | anular | aprobar | cerrar…
    campo  = Column(String(60), nullable=True)
    valor_anterior = Column(Text, nullable=True)
    valor_nuevo    = Column(Text, nullable=True)

    motivo  = Column(Text, nullable=True)   # obligatorio en correcciones
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    user = relationship("User")


class AsistenciaConfig(Base):
    """Fila única con los parámetros de cálculo. Editable desde /asistencia/config.

    Los defaults son los validados en el discovery v0.2.
    """
    __tablename__ = "asistencia_config"

    id = Column(Integer, primary_key=True, index=True)

    # ── Cálculo de la extra ──────────────────────────────────────────────────
    redondeo_min          = Column(Integer, nullable=False, default=15)
    minimo_computable_min = Column(Integer, nullable=False, default=15)
    tolerancia_salida_min  = Column(Integer, nullable=False, default=10)
    tolerancia_entrada_min = Column(Integer, nullable=False, default=10)

    # La entrada anticipada NO genera extra por defecto: llegar 15' antes es
    # hábito, no trabajo pedido. Si se activa, solo computa por encima del
    # mínimo, para que la costumbre no se convierta en 5 h/mes por persona.
    computar_entrada_anticipada  = Column(Boolean, nullable=False, default=False)
    minimo_entrada_anticipada_min = Column(Integer, nullable=False, default=30)

    # ── Integridad del dato ──────────────────────────────────────────────────
    max_horas_bloque = Column(Integer, nullable=False, default=16)
    # ^ Aplica al BLOQUE, no al día. Un bloque de 17 h es un olvido de firma;
    #   un día con dos bloques que suman 16 h 32 es un operativo nocturno y es
    #   perfectamente válido. Confundirlos es el error clásico del módulo.

    # ── Alertas ──────────────────────────────────────────────────────────────
    umbral_mes_1_min = Column(Integer, nullable=False, default=600)   # 10 h
    umbral_mes_2_min = Column(Integer, nullable=False, default=1200)  # 20 h
    umbral_mes_3_min = Column(Integer, nullable=False, default=1800)  # 30 h
    umbral_jornada_larga_min = Column(Integer, nullable=False, default=720)  # 12 h
    dias_racha_alerta = Column(Integer, nullable=False, default=3)
    pct_recurrencia_alerta = Column(Integer, nullable=False, default=60)
    pct_exceso_tercero_alerta = Column(Integer, nullable=False, default=10)

    # ── Gestión ──────────────────────────────────────────────────────────────
    motivo_obligatorio_aprobar = Column(Boolean, nullable=False, default=True)
    # Carga a día vencido: la fecha por defecto de la pantalla de carga es
    # AYER, no hoy. Evita el error más probable del sistema.
    dias_offset_carga = Column(Integer, nullable=False, default=1)

    updated_at    = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    updated_by = relationship("User", foreign_keys=[updated_by_id])
