# AUDITORÍA UX — MTR GESTIÓN

**Fecha:** 20/07/2026 · **Base:** working tree local (post-rediseño navy, Fases 1-4 + Polinómica CNA)
**Alcance:** experiencia de uso real. No cubre arquitectura, performance ni seguridad (ver auditoría técnica previa).
**Método:** recorrido de los 92 templates y 18 routers + mediciones sobre el código. Toda cifra citada fue medida, no estimada.

---

## A. Resumen ejecutivo

### Nota general de UX: **6,0 / 10**

### Principales fortalezas
1. **Shell de navegación post-rediseño**: sidebar agrupada por dominio, permission-aware, con estado activo por ruta. Un usuario nuevo encuentra los módulos.
2. **Centro de Operaciones**: responde "qué pasa ahora / qué viene / qué necesita atención" — el mejor punto de partida de jornada del sistema.
3. **Compras tiene el mejor patrón de "próxima acción"** del sistema: `action_buttons.html` muestra exactamente Aprobar/Rechazar/Recibir/Facturar/Pagar según estado. Es el modelo a copiar.
4. **Importadores con preview + confirm** (Operativos, Arribos, Despachos): el usuario ve qué va a entrar antes de confirmar, con detección de duplicados.
5. **Fase 4**: filtros auto-aplicados, "últimos filtros", orden por columna, foco automático — reduce clics reales en las listas anotadas.
6. **Design system navy consistente** en radios, sombras y color de marca.

### Principales debilidades
1. **El sistema es mayormente mudo al guardar**: 31 redirects POST devuelven al listado sin ningún mensaje; conviven 3 mecanismos distintos de feedback (`?saved=`, `?ok=`, `?imported=`) usados en solo 5 lugares. El usuario aprende a desconfiar y re-verifica a mano.
2. **El ciclo del buque obliga a re-tipear**: el nombre del barco se escribe a mano en **6 formularios distintos** (arribos, live, finalizados, transporte ×2, edición); cliente/producto son texto libre en **11 forms**. Cada re-tipeo es tiempo + riesgo de "NUTRIEN" ≠ "Nutrien AG".
3. **Pantallas monolíticas en el módulo más usado**: `shift_form` 1.118 líneas, `session_detail` 843, `shift_detail` 626, `invoice_form` 585. Operar un turno es navegar por scroll y memoria, no por estructura.
4. **Inconsistencia como norma**: 8+ textos distintos para el botón de guardar, 38 variantes de caja de error, 2 estilos de "volver", 0 breadcrumbs, filtros en card en unos módulos e inline en otros.
5. **Errores que expulsan del formulario**: 7 endpoints responden `HTTPException 422` crudo (pantalla JSON) en vez de re-mostrar el form con el error — el usuario pierde todo lo tipeado.
6. **Truncado silencioso**: los listados cortan en `limit(200/300/500)` sin decirlo; el usuario cree que ve todo.

### Por qué 6,0
La capa visible (navegación, estética, dashboard) está en nivel 7,5 tras el rediseño. Pero la UX operativa —lo que pasa *después* de apretar un botón— está en nivel 4-5: feedback ausente, formularios que castigan el error, re-tipeo estructural y la pantalla central del negocio (Live) sin arquitectura de información. El promedio ponderado por frecuencia de uso da 6,0: **se ve como un buen producto; todavía se trabaja como un sistema interno.**

---

## B. Mapa de recorridos y puntos de fricción

### R1 · Ciclo operativo de un buque (el recorrido central)
`Arribos → Live (turnos/personal/equipos/demoras/fotos) → Cierre → Factura → Conciliación → Finalizados → Transporte → Diarias`

| Paso | Fricción medida |
|---|---|
| Arribo → crear Live | **Cero conexión.** Se re-tipea buque, cliente, producto (ya cargados en el arribo). No hay botón "Iniciar operativo" desde el arribo. |
| Dentro del Live | `session_detail` = 843 líneas en una sola columna: acumulado, turnos, cierre, factura, conciliación y fotos compiten en el mismo scroll. Encontrar "cargar parte del turno 3" es scroll + reconocimiento. |
| Cargar un turno | `shift_form` = 1.118 líneas (bodegas × productos × personal × equipos × demoras en una página). Sin guardado parcial visible ni indicador de progreso; abandonar = duda sobre qué quedó. |
| Cerrar turno vs cerrar operativo | Dos conceptos con nombre parecido ("Cerrar") en la misma pantalla; el cierre formal exige turnos cerrados pero el motivo del bloqueo se descubre al intentar. |
| Cierre → Finalizados | El operativo Live cerrado y el "Operativo Finalizado" (import de balanza) son **entidades separadas** del mismo barco real; nada las vincula ni avisa del duplicado conceptual. |
| Transporte / Diarias | Cuarta y quinta carga del mismo nombre de barco. |

**Costo real:** para un buque típico, el nombre del barco se tipea 3-5 veces y el contexto (qué barco estoy operando) se pierde al saltar de módulo — no existe una vista "historia del buque".

### R2 · Compras
`Solicitud → Autorización → Recepción → Remito → Factura → Pago`
- **Lo bueno:** estados con badges, botones de próxima acción por estado, Conciliación agrupa lo trabado por causa ("recibidas sin factura", "aprobadas sin remito") — el usuario sí sabe qué falta y quién actúa.
- **Fricción:** la ficha de compra (430 líneas) mezcla historial, documentos y acciones sin tabs; asociar factura↔remito vive en el módulo Facturas separado del flujo de la compra; el autorizador no recibe ninguna señal activa (debe entrar a mirar).

### R3 · Despachos
- Importador con preview, hash anti-duplicado y batch reversible: **el mejor importador del sistema**.
- Fricción: filtros correctos pero la tabla corta en `limit(300)` sin aviso; los 8 estados se cambian de a uno (sin acción masiva); "reprogramar" está a 2 niveles de profundidad.

### R4 · Combustible
- Carga rápida bien pensada (patentes recientes, duplicado con confirmación explícita — buen patrón).
- Fricción: guardado redirige con `?saved=1` pero el detalle no lo destaca; asociación carga↔factura de combustible depende del módulo aparte sin link de ida/vuelta claro; filtro por responsable no existe (sí por patente/empresa/fecha).

### R5 · Transporte
- Nómina + historial simples y funcionales; export Word resuelto.
- Fricción: crear operativo re-tipea barco/cliente/producto; asignar choferes es fila por fila sin buscador dentro del modal de asignación; eliminar usa `confirm()` nativo; sin estados del operativo (¿en curso? ¿terminado?) más allá de fechas.

### R6 · Usuarios y permisos
- El editor de permisos con "defaults del rol / personalizar" + preview en vivo es claro.
- Fricción: las claves no explican su alcance (¿"Operativos" incluye importar?); no hay vista "qué ve este usuario" post-guardado; contraseña sin indicador de requisitos.

### R7 · Importadores (inventario)
| Importador | Instrucciones | Preview | Duplicados | Cancelar | Trazabilidad posterior |
|---|---|---|---|---|---|
| Despachos (Excel) | mínimas | ✅ con conteos | ✅ hash + skip | ✅ | ✅ batch + reversión |
| Operativos (Excel/HTML) | ✅ claras | ✅ con "Ya existe" | ✅ trip_code | ✅ | ⚠️ solo `source_file` |
| Arribos (PDF lineup) | ✅ claras | ✅ diff campo a campo | ✅ matching | ✅ | ✅ historial por arribo |
| Operaciones Diarias (HTML) | ✅ | ❌ **importa directo sin preview** | ⚠️ | ❌ | ✅ imports por día |
| Combustible histórico | — (solo migrate) | ❌ | ✅ | — | ❌ |

El patrón bueno existe (Despachos/Arribos); Diarias es el rezagado: un archivo equivocado entra sin confirmación.

### R8 · Uso global
- **Sin breadcrumbs** (0 en todo el sistema); el "volver" es una flecha ad-hoc con 2 estilos (9 `←` pelados, 15 `← Volver`).
- **Sin paginación visual**: cortes en 5/6/10/20/50/100/200/300/500 según el módulo, nunca comunicados.
- **Modales:** prácticamente no existen; todo es página nueva (coherente, pero encarece acciones chicas como "reprogramar").
- **Mobile:** barra inferior buena; pero las pantallas monolíticas del Live y el grid 380px sticky de Polinómica/Remito degradan fuerte en pantalla chica.
- **Nomenclatura:** "Operativos" (finalizados) vs "Operativos Live" vs "Operaciones Diarias" vs "operativo" en Transporte y en Polinómica-remito: cinco usos de la misma palabra para cosas distintas.

---

## C. Hallazgos por módulo

> Formato: **[Prioridad]** Pantalla · Problema → Evidencia → Impacto → Recomendación → Riesgo / Esfuerzo

### Operativos Live
- **[CRÍTICA]** `/operations/live/{id}` · Pantalla única de 843 líneas sin arquitectura de información → medido `session_detail.html`; secciones detectadas: acumulado, estadísticas, cierre, factura, conciliación, fotos, turnos → la pantalla más usada exige scroll+memoria; acciones clave enterradas → **Tabs: Resumen · Turnos · Factura · Conciliación · Fotos** con el acumulado como header persistente → Riesgo bajo (solo template) / Esfuerzo medio.
- **[CRÍTICA]** `/shift/new|edit` · Formulario de 1.118 líneas sin pasos ni progreso → medido → cargar un parte es la tarea más frecuente del supervisor; error a mitad de carga = pérdida de contexto → dividir en secciones colapsables con contador ("Bodegas 2/4 completas") y guardado como borrador explícito arriba → Bajo / Medio.
- **[ALTA]** Cierre de turno vs cierre de operativo comparten verbo y pantalla → confusión reportable; el bloqueo "todos los turnos cerrados" se descubre al fallar → precondición visible como checklist antes del botón → Bajo / Bajo.
- **[MEDIA]** Fotos al final del scroll sin miniaturas en el resumen → subirlas exige llegar al fondo → tab propio + botón "Agregar foto" en header → Bajo / Bajo.

### Ciclo del buque (transversal)
- **[CRÍTICA]** Sin conexión Arribo→Live→Finalizado→Transporte → 6 forms piden el buque a mano; 11 piden cliente/producto libres → re-tipeo diario + divergencia de nombres que rompe filtros e informes → botón "Iniciar operativo" en el arribo que precarga sesión Live; datalist de buques/clientes existentes en todos los forms (sin tocar modelos: solo autocompletar sobre distinct) → Bajo (datalist) a Medio (conversión) / Medio.
- **[ALTA]** No existe vista "historia del buque" → contexto perdido al saltar módulos → búsqueda global mínima (buque → matches en arribos/live/finalizados/transporte) → Bajo / Medio.

### Compras
- **[ALTA]** Ficha de compra 430 líneas sin tabs; documentos/acciones/historial mezclados → escaneo lento en la consulta diaria → tabs Resumen/Documentos/Historial (patrón ya definido en Etapa 2 pendiente) → Bajo / Medio.
- **[MEDIA]** Facturas (`/compras/facturas`) desconectadas de la ficha: asociar remito↔factura exige cambiar de módulo → link bidireccional y contador "N remitos sin factura" en la ficha → Bajo / Bajo.
- **[MEDIA]** El autorizador no tiene señal activa de pendientes (solo el chip del home si entra) → badge numérico en la sidebar junto a "Compras" → Bajo / Bajo.
- **[BAJA]** `?saved=1` existe en fuel pero no en compras: guardar compra vuelve mudo al listado → unificar toast → Bajo / Bajo.

### Despachos
- **[ALTA]** `limit(300)` sin indicador → con >300 registros el usuario cree ver todo → banner "Mostrando 300 de N — refiná filtros" hasta que exista paginación → Nulo / Bajo.
- **[MEDIA]** Cambio de estado uno-a-uno para el trabajo diario de cupeo → selección múltiple + acción masiva de estado → Medio / Medio.
- **[BAJA]** Preview de import excelente pero sin link posterior "ver qué trajo este batch" desde el listado → columna batch clickeable → Bajo / Bajo.

### Combustible
- **[MEDIA]** Sin filtro por responsable pese a capturarse → consulta "qué cargó X" imposible sin export → agregar select de responsable → Nulo / Bajo.
- **[MEDIA]** Carga↔factura de combustible sin navegación cruzada visible → dudas de conciliación → chips "factura asociada / sin factura" en la fila → Bajo / Bajo.
- **[BAJA]** El patrón anti-duplicado (checkbox "guardar de todas formas") es excelente y debería exportarse a otros forms → — / — .

### Transporte
- **[MEDIA]** Crear operativo re-tipea barco/cliente/producto → datalist compartido (ver ciclo del buque) → Bajo / Bajo.
- **[MEDIA]** Sin estado del operativo (abierto/cerrado) → el historial mezcla vivos y terminados → badge por fechas (en curso si `fecha_fin` vacía o futura) sin tocar modelo → Bajo / Bajo.
- **[BAJA]** Asignación de choferes sin buscador en listas largas → input filtro client-side → Bajo / Bajo.

### Arribos
- **[BAJA]** Tras "Confirmar actualizaciones" el detalle de qué cambió queda solo en historial por-arribo → toast con resumen ya existe (`?saved=`); enlazar al listado filtrado "actualizados hoy" → Bajo / Bajo.
- **[BAJA]** Board: los chips de columnas no indican que persisten → microcopy "se recuerda en este equipo" → Nulo / Bajo.

### Usuarios y permisos
- **[MEDIA]** Claves de permiso sin descripción de alcance → concesiones por adivinación → tooltip por submódulo con 1 línea ("Incluye importar y eliminar") → Nulo / Bajo.
- **[BAJA]** Sin resumen "este usuario ve: …" tras guardar → línea de chips en la fila del listado → Bajo / Bajo.

### Polinómica CNA
- **[MEDIA]** Remito: grid fijo 380px sticky colapsa mal en mobile (fix JS por width al cargar, no responsive real) → media query CSS → Nulo / Bajo.
- **[BAJA]** "Guardar remito" y "Descargar PDF" guardan ambos; la relación no es evidente → microcopy "el PDF guarda automáticamente el remito" → Nulo / Bajo.

### Formularios (transversal)
- **[ALTA]** 7 endpoints devuelven 422 JSON crudo ante error → el usuario ve una pantalla técnica y **pierde lo tipeado** → medido en routers (users, tarifas, finanzas-legacy, arribos, cuentas) → re-render del form con error y valores preservados → Bajo / Medio.
- **[ALTA]** 97 `required` viven solo en el navegador; los mensajes de error tienen 38 variantes visuales y 23 templates distintos renderizan `{{ error }}` cada uno a su modo → un solo partial `_error.html` + validación server homogénea → Bajo / Medio.
- **[MEDIA]** Campos sin ayuda contextual en datos ambiguos (ETB texto libre, % vs fracción en índices — Polinómica lo resuelve bien con microcopy; el resto no) → placeholder+hint estándar → Nulo / Bajo.

### Feedback (transversal)
- **[CRÍTICA]** 31 POST redirigen sin mensaje; 3 mecanismos de éxito distintos en solo 5 pantallas → "¿se guardó?" es la duda más frecuente del sistema → **un toast global** leído de un único query param (`?ok=`) renderizado en `base.html`, y migrar los 31 redirects → Bajo / Bajo-Medio.
- **[ALTA]** 23 `confirm()` nativos para acciones destructivas, algunos borrando cientos de registros → diálogo propio con contexto ("Vas a eliminar el operativo X y sus 153 viajes") y, para volúmenes grandes, tipeo del nombre → Bajo / Medio.

---

## D. Inconsistencias globales (inventario)

| Dimensión | Evidencia medida | Estado |
|---|---|---|
| Botón guardar | 8+ textos: "Guardar", "Guardar cambios", "Crear X", "+ Nuevo", "Registrar", "+ Agregar…", "Guardar mes y recalcular" | Definir 3: **Guardar** (edición) / **Crear {entidad}** (alta) / verbo específico solo si agrega info |
| Mensajes de éxito | `?saved=` (2), `?ok=` (1), `?imported=` (2), nada (31) | Unificar en `?ok=` + toast global |
| Cajas de error | 38 combinaciones de clases; 23 renders artesanales de `{{ error }}` | Partial único |
| Confirmaciones | 23 `confirm()` nativos, texto dispar | Componente de confirmación propio |
| Estados vacíos | 25 de ~40 listados los manejan; estilos mixtos (clase `.empty-state` nueva vs párrafos ad-hoc) | Completar con `.empty-state` + acción |
| "Volver" | 2 estilos (9 `←`, 15 `← Volver`); 0 breadcrumbs | Un patrón: `← {Nombre del listado}` |
| Filtros | 5 listas con `data-autofilter` (card u inline según módulo); purchases con HTMX propio; quotes/suppliers/tarifario con submit clásico | Extender data-autofilter y posición única (card sobre la tabla) |
| Truncado de listas | 10 valores distintos de `limit()` sin aviso | Banner "Mostrando N de M" |
| Badges de estado | 4 sistemas: clases `.status-*` (compras/mant/proyectos), dicts `ESTADO_CSS` (despachos), `ARRIBO_ESTADO_CSS`, estilos inline (live) | Un helper `badge(estado, dominio)` |
| Nomenclatura | "Operativo" significa 5 cosas según módulo; "Remito" 2 (documento de compra / remito de tarifas) | Glosario y renombres de labels (no de rutas) |
| Fechas | `fmt_ar` vs `fmt_date` vs `strftime` inline vs texto libre (ETB) | Usar filtros existentes siempre |

---

## E. Quick wins (bajo riesgo, sin tocar modelos ni reglas)

1. **Toast global en `base.html`** leyendo `?ok=` / `?err=` — y migrar los 31 redirects mudos agregando el parámetro. *(El de mayor retorno de todo el informe.)*
2. Banner **"Mostrando X de Y"** cuando un listado alcanza su `limit()`.
3. **Partial `_error.html` + `_empty.html`** y reemplazo mecánico en los 23/15 lugares.
4. **Datalists de buque/cliente/producto** (SELECT DISTINCT existentes) en los 6 forms que los tipean.
5. Unificar el patrón **"← Volver a {listado}"** en las 24 apariciones.
6. Estandarizar el **texto de botones** de guardado (3 variantes).
7. Filtro por **responsable** en Combustible.
8. **Tooltips de alcance** en los checkboxes de permisos.
9. **Preview en el importador de Operaciones Diarias** (reusar el patrón de Operativos: parse → tabla → confirmar; el parser ya existe).
10. Checklist visible de **precondiciones de cierre** del operativo Live.
11. Media query para el **remito de Polinómica** en mobile.
12. Chips "factura asociada / sin factura" en filas de Combustible.

---

## F. Plan de implementación UX

### Etapa 1 — Feedback y consistencia *(base de todo)*
- **Alcance:** toast global; migración de los 31 redirects; partials de error/vacío; textos de botones; banner de truncado; patrón "volver".
- **Archivos probables:** `base.html`, nuevo `templates/partials/{_toast,_error,_empty}.html`, retoques de 1 línea en ~30 templates y ~15 routers (solo el `url=` del redirect).
- **Dependencias:** ninguna. **Riesgos:** casi nulos (strings y partials).
- **Criterios de aceptación:** toda acción POST muestra confirmación o error visible; cero pantallas 422 crudas alcanzables desde forms; un solo estilo de error/vacío.
- **Pruebas manuales:** guardar/editar/borrar en cada módulo y verificar el toast; forzar un error de validación por módulo.

### Etapa 2 — Operativo Live
- **Alcance:** tabs en `session_detail`; secciones colapsables + progreso en `shift_form`; checklist de cierre; fotos accesibles desde header.
- **Archivos:** `templates/operations/live/{session_detail,shift_form,session_close}.html` (+CSS ya existente). Sin cambios de rutas ni modelos.
- **Riesgos:** medio (la pantalla más usada) → validar con un operativo real antes de deploy.
- **Aceptación:** llegar a "cargar parte del turno N" en ≤2 clics sin scroll; cierre bloqueado explica el porqué antes del intento.
- **Pruebas:** ciclo completo con una sesión QA existente (crear turno, cargar bodega, cerrar, facturar, conciliar).

### Etapa 3 — Formularios y prevención de errores
- **Alcance:** re-render con error+valores en los 7 endpoints 422; validación server de campos críticos; diálogo de confirmación propio para los 23 destructivos; hints estándar.
- **Archivos:** routers afectados (users, tariffs, arribos, transporte, fuel) + un componente JS de confirmación en `ui.js`.
- **Riesgos:** bajo-medio (tocar handlers, sin cambiar reglas).
- **Aceptación:** ningún error de validación pierde lo tipeado; ninguna acción destructiva usa `confirm()` nativo.

### Etapa 4 — Navegación y contexto
- **Alcance:** breadcrumb ligero (módulo › pantalla) en `base.html`; badges numéricos de pendientes en sidebar (compras por autorizar, turnos abiertos); búsqueda de buque global mínima.
- **Dependencias:** Etapa 1. **Riesgos:** bajo; el badge agrega 2-3 counts al request del shell (cachear 60s).
- **Aceptación:** desde cualquier pantalla se sabe dónde se está y se vuelve en 1 clic; el autorizador ve sus pendientes sin entrar a Compras.

### Etapa 5 — Flujo del buque
- **Alcance:** botón "Iniciar operativo Live" en el arribo (precarga buque/cliente/producto vía query params — sin FK todavía); datalists compartidos; enlaces cruzados "ver en Finalizados / Transporte" por nombre canónico.
- **Dependencias:** Etapas 1-2. **Riesgos:** medio (toca el flujo estrella; la precarga por querystring es reversible y no altera modelos).
- **Aceptación:** crear un Live desde un arribo sin tipear buque/cliente/producto; desde un Live, llegar a la historia del buque en 1 clic.
- **Nota:** la FK real (entidad Escala) queda para el plan técnico — acá solo UX reversible.

### Etapa 6 — Accesibilidad y responsive
- **Alcance:** contraste AA en textos secundarios (gray-400→500/600 en tamaños chicos), `aria-label` en botones-ícono, texto acompañando color en badges, pases mobile de Live y Polinómica.
- **Riesgos:** nulo. **Aceptación:** issues AA de contraste en 0 sobre las 10 pantallas más usadas; flujo de turno completable en 375px.

---

## G. Top 20 problemas UX (por impacto operativo)

1. Guardados mudos: 31 POST sin feedback + 3 mecanismos dispares.
2. `shift_form` de 1.118 líneas: la tarea más frecuente es la peor pantalla.
3. `session_detail` de 843 líneas sin tabs: el centro del negocio se navega por scroll.
4. Re-tipeo del buque en 6 forms / cliente-producto libres en 11.
5. Ciclo del buque fragmentado sin ningún enlace entre módulos.
6. Errores 422 crudos que expulsan del form y pierden lo tipeado (7 endpoints).
7. Truncado silencioso de listados (10 `limit()` sin aviso).
8. 23 confirm() nativos para acciones destructivas, algunas masivas.
9. Importador de Operaciones Diarias sin preview ni cancelación.
10. Cierre de turno vs cierre de operativo: mismo verbo, precondición opaca.
11. Ficha de compra sin tabs; factura↔remito en módulo separado.
12. Sin señal activa para el autorizador (pendientes invisibles fuera del home).
13. 38 variantes de caja de error / 23 renders artesanales.
14. Sin breadcrumbs; "volver" con 2 estilos.
15. Estados vacíos incompletos (25 de ~40) y de estilos mixtos.
16. Cambio de estado de despachos uno-a-uno (sin masivo).
17. "Operativo/Remito" con significados múltiples entre módulos.
18. Permisos sin descripción de alcance por clave.
19. Contraste sub-AA en metadatos + íconos sin aria (accesibilidad).
20. Grid del remito Polinómica y páginas Live degradadas en mobile.

## H. Top 20 mejoras UX (impacto × frecuencia × errores ÷ esfuerzo/riesgo)

1. Toast global + migrar los 31 redirects (bajo esfuerzo, toca todo).
2. Tabs en `session_detail` del Live.
3. Secciones con progreso en `shift_form`.
4. Datalists de buque/cliente/producto en los 6/11 forms.
5. Re-render con error en los 7 endpoints 422.
6. Preview en importador de Diarias (patrón ya existente).
7. Banner "Mostrando X de Y" en listados truncados.
8. Partials únicos de error y vacío.
9. Checklist de precondiciones de cierre en Live.
10. "Iniciar operativo" desde el arribo (precarga por querystring).
11. Diálogo de confirmación propio (reemplaza 23 confirm()).
12. Badges de pendientes en sidebar (autorizador, turnos abiertos).
13. Tabs en ficha de compra + enlace factura↔remito.
14. Estados masivos en Despachos.
15. Breadcrumb ligero global.
16. Unificación de textos de botones (3 variantes).
17. Tooltips de alcance en permisos.
18. Filtro por responsable en Combustible + chips de factura.
19. Contraste AA + aria en íconos.
20. Media queries Live/Polinómica para mobile.

---

*Informe generado el 20/07/2026. Sin cambios aplicados: solo inspección y medición.*
