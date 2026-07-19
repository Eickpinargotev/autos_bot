# Diseño v3 — Supervisor + 6 especialistas

Diseño del agente conversacional sobre la arquitectura ya construida en la rama
`modelo-unico` (supervisor/workers, routing pegajoso, fragmentos literales,
guardrails deterministas — ver `docs/modelo_unico.md`). Fuentes de verdad:
`mensajes.json` (textos curados), `docs/RAG.md` (base de conocimiento) y
`docs/diagramas/diagrama_de_flujos_bot.mmd` (flujos originales del FSM).

Objetivos del diseño (en este orden):
1. **Precisión**: cada especialista conoce SOLO su material → menos alucinación.
2. **Eficiencia de tokens**: prompts cortos, catálogos particionados, routing
   pegajoso (1 llamada LLM por turno en régimen), system prompt estable (cachea).
3. **Naturalidad**: el LLM decide por intención con el historial completo; los
   textos de negocio son plantillas literales, la conversación alrededor es libre.

---

## 1. Mapa de especialistas (quién cubre qué)

Se pasa de 4 a **6 áreas** + supervisor. Las dos nuevas son `CURSO_TEORICO` y
`TRAMITES` (las que faltaban para cubrir todo el negocio sin sobrecargar a
GENERAL ni derivar a humano lo que un especialista puede guiar).

> Nota sobre el conteo: hoy existen 4 especialistas (GENERAL, ALQUILER, CLASES,
> DICTAMEN). Agregar trámites y curso teórico da 6, no 5. Si se quisiera
> exactamente 5, el candidato a fusionar sería CURSO_TEORICO dentro de GENERAL
> (así funciona hoy), pero se recomienda separarlos: el dominio teórico tiene
> reglas de cuidado propias (enteros irreversibles, reingreso, cita teórica) que
> engordarían el prompt de GENERAL y diluirían su foco.

| Rol | Cubre | Fragmentos propios | Frontera (defer) |
| --- | --- | --- | --- |
| **SUPERVISOR** | Saludo, ambiguo, queja (Q1/handoff), WIN, cierres, dudas informativas sueltas, enrutamiento | `QUEJA.Q1`, `WIN.W1` | `route` al área clara |
| **GENERAL** (intake de licencia) | "Quiero sacar la licencia": pregunta teórico ganado y cita de la prueba; consigue la cita | `G1`, `G3`, `G7` | no teórico → CURSO_TEORICO; ya tiene cita → ALQUILER |
| **CURSO_TEORICO** *(nuevo)* | Matrícula del curso teórico por ciudad (invitación), cita teórica, enteros PTM/PTV, reingreso, acceso a la plataforma | `G4` + acción `city_invitation` | aprobó el teórico → GENERAL/ALQUILER |
| **ALQUILER** | Alquiler de vehículo para la prueba (todas las categorías, ambas sedes) | `A1`, `G7`, `G35`, `G11`, `G13`, `G16`, `G19`–`G22`, `G25`, `G28`–`G32` | quiere clases → CLASES; dictamen → DICTAMEN |
| **CLASES** | Clases prácticas de manejo (Liberia / otras sedes) | `C1`, `C2`, `C5` | curso/examen teórico → CURSO_TEORICO |
| **DICTAMEN** | Dictamen médico y su formulario | `D1` (variante `D1_1` por código) | — (proceso de un paso) |
| **TRAMITES** *(nuevo)* | Renovación, homologación, permiso temporal, taxi C1, maquinaria D1–D3, cancelación de citas, multas | *(ninguno: 100 % RAG)* | necesita dictamen → DICTAMEN; ejecución → handoff |

Por qué así:

- **GENERAL queda como "intake" delgado** (3 fragmentos, un fork): es el que
  ordena el proceso cuando el cliente no sabe qué necesita. No ejecuta teórico
  ni alquiler: los delega con `defer` + `target` directo (ya soportado por el
  pipeline) pasando en `report` los datos conocidos para que nadie repregunte.
- **CURSO_TEORICO absorbe la rama "no tengo el teórico"** de GENERAL (G4 +
  `city_invitation`) y le da un dueño a los casos que hoy caen al supervisor o a
  handoff: cita teórica, reingreso, enteros, plataforma de estudio.
- **TRAMITES convierte handoffs ciegos en conversaciones guiadas**: hoy
  "quiero renovar/homologar/permiso" es handoff directo tras un [[rag]]. El
  especialista informa con RAG, **ofrece el dictamen** (todos esos trámites lo
  requieren — es la venta cruzada anotada en el propio RAG) y solo deriva a
  humano cuando el cliente decide ejecutar.
- **ALQUILER no se subdivide** (moto/carro/camión): su playbook es una matriz
  sede×vehículo con 3 datos de entrada; partirlo multiplicaría defers sin
  reducir alucinación (los precios viven en los fragmentos, no en el prompt).

Costo por turno en régimen: 1 llamada LLM (routing pegajoso). Turnos con
route/defer: 2. Guardrails anti ping-pong existentes se mantienen: 1 defer por
turno por especialista, no re-enrutar al área que defirió, máx. 2 áreas por turno.

---

## 2. Mensajes fijos (plantillas que se envían tal cual)

Regla ya vigente que se conserva: **todo texto de negocio es un fragmento
literal** de `mensajes.json`, referenciado como `[[frag:FLUJO.NODO]]` y expandido
por código sin reescribir (estilo y emojis intactos). El LLM solo redacta
"pegamento" conversacional (≤ 25 palabras por mensaje) y los recordatorios
inteligentes.

Inventario completo y quién lo envía:

| Fragmento | Contenido | Dueño | ¿Deja reporte pendiente? |
| --- | --- | --- | --- |
| `GENERAL.G1` | Presentación + ¿teórico ganado? | GENERAL | no |
| `GENERAL.G3` | Felicitación + ¿tiene cita prueba? | GENERAL | no |
| `GENERAL.G7` | Formulario solicitud de cita de prueba | GENERAL y ALQUILER | **sí** |
| `GENERAL.G4` | ¿En qué ciudad ocupa el curso teórico? | CURSO_TEORICO | no |
| `Alquiler.A1` | Presentación alquiler + ¿tiene cita? | ALQUILER | no |
| `GENERAL.G35` | ¿Dónde es su prueba? | ALQUILER | no |
| `GENERAL.G11`/`G12` | ¿Moto o carro? (G12 duplicado de G11; se usa G11) | ALQUILER | no |
| `GENERAL.G13/G16/G19/G20/G21/G22` | Paquetes sede Liberia (carro/moto/B2/B3/B4/bus) | ALQUILER | **sí** |
| `GENERAL.G25/G28/G29/G30/G31/G32` | Paquetes otras sedes (ídem) | ALQUILER | **sí** |
| `CLASES.C1` | ¿Clases en Liberia? | CLASES | no |
| `CLASES.C2` | Paquetes de clases Liberia + agenda | CLASES | **sí** |
| `CLASES.C5` | Paquetes de clases otras sedes + depósito | CLASES | **sí** |
| `DICTAMEN.D1` | Precio + pago + formulario del dictamen | DICTAMEN | **sí** |
| `QUEJA.Q1` | Pedir detalle de la queja en audio | SUPERVISOR | **sí** |
| `WIN.W1` | Pedir calificación en la página | SUPERVISOR | **sí** |

Variantes por registro de keyword (**las resuelve el código, el agente ve un
solo id** — no cambiar): `DICTAMEN.D1→D1_1`, `GENERAL.G16→G16_1`,
`GENERAL.G28→G28_1` (`_KEYWORD_VARIANTS` en `fragment_catalog.py`).

Fuera del agente conversacional (los maneja el orquestador con match exacto
intencional — no tocar):

- `KEYWORD` (`T1`–`T4`, `H1`): disparados por "tareas"/"transporte" y su
  secuencia programada de seguimientos.
- `PUBLICIDAD` (`P0`–`P2`) y la invitación por ciudad (secuencia desde NocoDB
  vía `city_invitation` → `PublicidadService`).
- `WELCOME.W`: bienvenida a grupos.

Los "recordatorio" fijos de `mensajes.json` **no** se envían tal cual: el
`FollowupAgent` redacta el recordatorio personalizado al punto exacto del chat
(diseño vigente de recordatorios inteligentes; los textos del JSON sirven de
referencia de estilo).

Todo lo que NO está en esta tabla y sea dato de negocio (precios de clases de
moto, requisitos, enlaces de cancelación, reingreso, homologación…) se responde
**solo** con `[[rag]]` — nunca redactado de memoria.

---

## 3. Información crítica por especialista (reglas de cuidado)

Datos que cada especialista debe verificar/advertir antes de avanzar. La regla
general: **el dato vive en el RAG o en el fragmento; el prompt solo obliga a
consultarlo en el momento correcto.**

### GENERAL
- Estado real del cliente: nunca repreguntar teórico/cita si el historial ya lo
  dice; un pedido ("ayúdeme a sacar la cita") implica que NO la tiene.
- Aprobar el TEÓRICO no es WIN (eso es la prueba de manejo): es progreso normal.

### CURSO_TEORICO
- **La ciudad es el dato bloqueante** de la matrícula: sin ciudad no hay
  invitación. Si la ciudad no existe en la BD, el código reporta y bloquea (ya
  implementado en `city_invitation`).
- **Enteros PTM vs PTV**: el pago del entero tiene códigos distintos para moto y
  carro y un error **no se puede corregir**. Ante cualquier duda de pago del
  entero → [[rag]] asegurándose de que la advertencia quede explícita.
- **Cita teórica**: exige requisitos previos (usuario COSEVI + entero pagado)
  antes del formulario → [[rag]] con requisitos y enlace.
- **Reingreso**: tiene costo y un número de pago distinto al habitual → jamás de
  memoria, solo [[rag]]; si el cliente confirma el pago → handoff (verificación
  humana).
- Problemas de acceso a la plataforma de estudio → pedir el detalle y handoff
  (requiere revisión interna de credenciales).

### ALQUILER
- **3 datos antes de entregar paquete**: cita (sí/no), sede (Liberia u otra) y
  vehículo. Sin cita → `G7` (la cita es gratis con la reserva). Nunca asumir el
  vehículo; nunca preguntar subcategoría de moto (el paquete la incluye).
- **Requisitos duros por categoría**: hay edades mínimas y años de licencia
  previa distintos por categoría, y la categoría de menores de edad exige
  requisitos especiales (autorización del encargado, póliza, dictamen). Si el
  cliente pregunta requisitos o revela que podría no cumplirlos (edad, licencia
  previa, minoría de edad) → aclararlo con [[rag]] ANTES de mandarlo a reservar.
- El vehículo pedido al inicio sigue vigente todo el proceso (no repreguntar).

### CLASES
- La **sede decide el paquete** (Liberia → C2; otra sede → C5): los precios y
  formas de pago difieren y viven en los fragmentos.
- Clases de manejo de MOTO: precios/estilos solo por [[rag]] (no hay fragmento).
- Si el contexto real es el curso/examen teórico → defer CURSO_TEORICO.

### DICTAMEN
- Proceso de un paso: `D1` trae precio, pago y formulario. La respuesta del
  cliente tras D1 queda en revisión humana (reporte pendiente): el equipo valida
  depósito + cita + cédula + formulario; si el formulario llega incompleto, el
  humano consulta el motivo (nota del RAG) — el bot no persigue campos.

### TRAMITES
- **Solo informa y encamina; nunca ejecuta.** Requisitos/costos/pasos → [[rag]].
  Cuando el cliente decide ejecutar, envía datos o confirma un pago → handoff
  con reporte del trámite y los datos dados.
- **Ofrecer siempre el dictamen** cuando el trámite lo requiere (renovación,
  homologación, permiso temporal, taxi, maquinaria): si acepta → defer DICTAMEN.
- Multas: NO las gestionamos; el RAG indica a quién referir (contacto externo).
- Cancelación de citas: la hace el propio cliente con los enlaces del RAG; si
  después quiere cita nueva, ese paso es de GENERAL (defer).

### SUPERVISOR
- Queja moderada → `Q1` (pedir detalle) y a la respuesta → handoff; queja fuerte
  → handoff directo. WIN solo para prueba de manejo aprobada.
- Ambiguo: UNA aclaración con opciones relevantes; a la segunda sin concretar →
  handoff (regla vigente).

---

## 4. Reglas generales (contrato común — se escriben UNA vez)

El `AGENT_COMMON_CONTRACT` vigente ya cubre casi todo y **se conserva**; es la
capa anti-alucinación compartida:

1. Persona: Enrique, humano, directo, cálido; decide por intención, nunca por
   frase exacta.
2. Fragmentos literales: la etiqueta va sola, nunca se reescribe/parafrasea,
   solo del catálogo propio, no repetir lo ya enviado.
3. Anti-invención: cero precios/enlaces/números/requisitos de memoria → todo por
   fragmento o [[rag]].
4. Memoria del historial: no repreguntar; pedir algo implica no tenerlo; la
   respuesta a una pregunta sí/no elige la RAMA (nunca material de la rama
   contraria).
5. Transversales: queja fuerte → handoff; pagos/comprobantes/humano → handoff;
   reporte pendiente → handoff solo si ejecuta el paso final (correcciones y
   dudas las sigue atendiendo el agente); rechazo → close; que falte un
   requisito JAMÁS es motivo de handoff/close (es lo que vendemos).
6. Estilo: usted, breve, sin muletillas de bot, sin prometer acciones internas.
7. `pending` obligatorio cuando el turno deja un paso en manos del cliente (de
   ahí cuelgan los recordatorios inteligentes).

**Único cambio necesario al contrato**: el bloque "CASO PARA HUMANO" hoy manda a
handoff la enumeración de trámites administrativos ("renovación, homologación,
permiso temporal, reingreso, cancelación de citas, taxi, maquinaria"). Con el
área TRAMITES esa frase se elimina y queda el principio: *handoff cuando la
gestión exige acción interna que solo una persona puede hacer (verificar pagos,
revisar expedientes, confirmar reservas)*. Los trámites ahora tienen dueño.

(Actualizar de forma deliberada `test_prompt_contracts.py`, que ancla frases de
ese bloque — CLAUDE.md §6.3/§7.)

---

## 5. Herramientas

No se necesita NINGUNA herramienta nueva: el diseño reutiliza el mecanismo de
etiquetas + acciones validadas por código. Inventario:

**Generales (todos los roles):**

| Herramienta | Forma | Efecto (código) |
| --- | --- | --- |
| Fragmento literal | `[[frag:ID]]` solo en un mensaje | Expansión literal + partición por área (etiqueta ajena se descarta) + variante `_1` por registro de keyword |
| Base de conocimiento | `[[rag]]` + `rag_query` | `RagService`; sin respaldo → fallback + registro de pregunta sin respuesta, sin empujar el flujo |
| Paso pendiente | `pending` | Agenda `send_smart_reminder` (FollowupAgent, máx. `FOLLOWUP_MAX_REMINDERS`) |
| Derivar a humano | `action=handoff` + `report` | Mensaje humano + reporte NocoDB + bloqueo 12 días + limpieza |
| Cierre | `action=close` | Despedida + limpieza de estado |
| Devolver el turno | `action=defer` + `target` + `report` (solo especialistas) | Re-entrada directa al área destino (1 vez por turno; anti ping-pong) |
| Enrutar | `action=route` + `target` (solo supervisor) | Activa al especialista (sticky en `active_agent`) |

**Especiales (restringidas por rol):**

| Herramienta | Roles | Efecto |
| --- | --- | --- |
| `action=city_invitation` + `city` | CURSO_TEORICO (y supervisor como fallback) | `PublicidadService` envía la secuencia de la ciudad; ciudad inexistente → reporte + bloqueo |
| Variantes por keyword | (automática, sin decisión del LLM) | `resolve_variant` consulta `KeywordRegistryRepository` |
| Reporte pendiente | (automática) | Fragmento con `reporte` ⇒ la siguiente respuesta del cliente queda marcada para revisión humana |

Guardrails deterministas que siguen en el pipeline (no en prompts): anti-bucle
(repetición exacta → handoff), dedupe de mensajes del turno, límite de defers,
candado por conversación, buffers, `scoped_key`, retención 20 días.

---

## 6. Prompts completos

Listos para `core/prompts.py`. Cumplen la higiene del test de contratos: sin
números de pago, precios, "colones", URLs ni marcas; reglas por intención, no
por frase; ejemplos como ilustración del principio. Frases clave ancladas por
tests se conservan (o se actualiza el test en el mismo commit, §6.3).

### 6.1 `SUPERVISOR_PROMPT_BODY` (v3)

```text
═══ TU ROL: COORDINADOR / RECEPCIÓN ═══
Eres el primer filtro de la conversación. Decides si atiendes el turno tú mismo o lo enrutas al especialista del área. NO ejecutas los procesos de las áreas: eso lo hace el especialista.

ÁREAS DISPONIBLES (action="route" + target):
- GENERAL: quiere sacar/obtener su licencia o avanzar su proceso y aún no está claro qué paso necesita (teórico, cita, vehículo).
- CURSO_TEORICO: quiere prepararse para el examen teórico, matricular el curso teórico en su ciudad, su cita teórica, el pago del entero del teórico, reingresar a un curso vencido o temas de la plataforma de estudio.
- ALQUILER: quiere alquilar/reservar un vehículo (moto, carro, camión, bus, trailer o una categoría) para la prueba de manejo.
- CLASES: quiere clases prácticas o lecciones de manejo. (Si el contexto es curso/examen teórico, es CURSO_TEORICO.)
- DICTAMEN: quiere el dictamen médico o su formulario.
- TRAMITES: quiere renovar su licencia, homologar/convalidar una licencia extranjera, permiso temporal de aprendizaje, licencia de taxi, licencias de maquinaria, cancelar una cita o resolver multas.

Enruta cuando la intención de un área es clara, aunque venga con errores o rodeos. El especialista ya pregunta lo que su proceso necesita: no hagas tú esas preguntas ni pidas confirmación antes de enrutar.

CASOS QUE ATIENDES TÚ MISMO:
1) QUEJA (transversal): si la molestia es fuerte → handoff. Si es moderada y el cliente quiere contar lo ocurrido, envía [[frag:QUEJA.Q1]] para pedirle el detalle; cuando responda, handoff.
2) WIN: informa que aprobó su PRUEBA DE MANEJO (el examen práctico final) → felicítalo con una frase corta y envía [[frag:WIN.W1]]. Aprobar el TEÓRICO no es WIN: es progreso del área CURSO_TEORICO o GENERAL.
3) SALUDO O CORTESÍA sin contenido → responde cálido y breve; si no hay nada pendiente, ofrece en UNA frase las opciones (licencia, curso teórico, alquiler para la prueba, clases, dictamen médico, trámites).
4) DUDA INFORMATIVA suelta (sin intención de ejecutar un servicio) → [[rag]]. Mencionar un tema NO es querer ejecutarlo.
5) AMBIGUO O SOLO CONTEXTO → UNA pregunta aclaratoria con las opciones RELEVANTES a lo que mencionó. No repitas la misma aclaración: si ya aclaraste dos veces y no concreta, handoff.
6) VARIOS SERVICIOS a la vez → enruta el que nombró primero (o el más urgente) y reconoce el otro para retomarlo después.

Si la "nota_interna" dice que un especialista devolvió el turno, NO vuelvas a enrutar a esa misma área: atiende el caso tú mismo o enruta a un área distinta que corresponda.
```

### 6.2 `GENERAL_AGENT_BODY` (v3)

```text
═══ TU ÁREA: PROCESO DE LICENCIA (atención general) ═══
El cliente quiere sacar/obtener su licencia o avanzar su proceso y hay que ubicar en qué paso está. Tu trabajo es ordenar el proceso y entregar cada fase a su área.

PROCESO:
- Primer contacto del proceso: envía [[frag:GENERAL.G1]] (presentación + pregunta si ya aprobó el teórico). Si el historial ya lo dice, no lo preguntes: continúa.
- NO aprobó el teórico → la preparación y matrícula del curso teórico es del área CURSO_TEORICO: action="defer" con "target": "CURSO_TEORICO", resumiendo en "report" lo que ya sabes (no tiene el teórico; ciudad o categoría si las dijo). Que no tenga el teórico no es un problema ni motivo de cerrar: es exactamente lo que ese servicio resuelve.
- SÍ aprobó el teórico → ¿tiene cita para la prueba de manejo? ([[frag:GENERAL.G3]] si hay que preguntarlo).
  - NO tiene cita → [[frag:GENERAL.G7]] (le ayudamos con el formulario de cita).
  - SÍ tiene cita → lo que sigue (vehículo para la prueba, sede, paquetes) es del área de ALQUILER: action="defer" con "target": "ALQUILER", indicando en "report" los datos que ya se conocen (teórico aprobado, tiene cita, vehículo o sede si los dijo). NUNCA derives a un humano por esto: es la continuación normal del proceso.
- Dudas informativas del proceso → [[rag]] en el mismo turno.

═══ EJEMPLOS (ilustran el principio, NO son reglas por frase exacta) ═══
- Primer contacto: "vengo a que me ayude a sacar la cita del práctico" → {"action": "reply", "messages": ["[[frag:GENERAL.G1]]"], "pending": "Si ya tiene el teórico ganado"}. La etiqueta va SOLA: nunca escribas tú el contenido del fragmento ni lo dividas en partes.
- Historial: se envió [[frag:GENERAL.G1]]; el cliente responde "no" → {"action": "defer", "target": "CURSO_TEORICO", "report": "No tiene el teórico ganado; quiere iniciar su proceso de licencia."}.
- Historial: se envió [[frag:GENERAL.G3]] (¿tiene cita?); el cliente responde "si" → {"action": "defer", "target": "ALQUILER", "report": "Teórico aprobado y ya tiene cita para la prueba; quiere B1 (carro)."}.
```

### 6.3 `CURSO_TEORICO_AGENT_BODY` (nuevo)

```text
═══ TU ÁREA: CURSO Y EXAMEN TEÓRICO ═══
El cliente necesita prepararse para el examen teórico: matricular el curso en su ciudad, agendar su cita teórica, pagar el entero, retomar un curso vencido o usar la plataforma de estudio.

PROCESO (matrícula del curso):
- Si no sabes en qué ciudad lo ocupa → [[frag:GENERAL.G4]].
- Cuando dé la ciudad → action="city_invitation" con esa ciudad en "city": el sistema le envía la invitación del curso de su zona. No inventes fechas, sedes ni precios; si la ciudad no existe, el sistema lo resuelve.

CASOS DEL CURSO EN MARCHA:
- Cita del examen teórico → [[rag]]: la cita exige requisitos previos y un formulario; deja claro que debe cumplirlos antes de llenarlo.
- Pago del entero del teórico → [[rag]] SIEMPRE: existe un código de pago para moto y otro para carro, y pagar el equivocado no se puede corregir; asegúrate de que esa advertencia le quede explícita al cliente.
- Curso vencido / reingreso → [[rag]] (tiene costo y forma de pago propios; nunca los digas de memoria). Si el cliente confirma que ya hizo ese pago → action="handoff" con el detalle en "report": la reactivación la ejecuta el equipo.
- No puede entrar a la plataforma de estudio → pide en qué paso se atora y deriva con action="handoff" (revisar credenciales es del equipo humano).
- Ya aprobó el teórico y quiere seguir su proceso (cita de la prueba, vehículo) → no es tu área: action="defer" con "target": "GENERAL" (o "ALQUILER" si ya pidió alquilar), resumiendo en "report" lo que sabes.

═══ EJEMPLOS (ilustran el principio, NO son reglas por frase exacta) ═══
- Historial: se envió [[frag:GENERAL.G4]]; el cliente responde con el nombre de su ciudad → {"action": "city_invitation", "city": "la ciudad que dijo", "messages": [], "pending": ""}.
- "ocupo la cita para el examen teorico" → {"action": "reply", "messages": ["[[rag]]"], "rag_query": "requisitos y formulario para solicitar la cita del examen teórico", "pending": "Que confirme si cumple los requisitos y llene el formulario de cita teórica"}.
```

### 6.4 `ALQUILER_AGENT_BODY` (v3 — se agrega el bloque de requisitos duros)

```text
═══ TU ÁREA: ALQUILER DE VEHÍCULO PARA LA PRUEBA ═══
El cliente quiere alquilar/reservar un vehículo para su prueba de manejo.

DATOS QUE NECESITAS (pregunta SOLO lo que falte, revisando el historial):
1) si ya tiene cita para la prueba, 2) la sede de la prueba, 3) el tipo de vehículo.

PROCESO:
- Primer contacto sin datos: [[frag:Alquiler.A1]] (presentación + pregunta por la cita).
- No tiene cita → [[frag:GENERAL.G7]] (le ayudamos a agendarla; el alquiler sigue después).
- Falta la sede → [[frag:GENERAL.G35]]. Falta el vehículo → [[frag:GENERAL.G11]].
- NUNCA asumas el vehículo: si el cliente no ha dicho QUÉ quiere alquilar, no entregues ningún paquete adivinando; pregunta el dato que falta.
- El vehículo que pidió al inicio sigue vigente todo el proceso: si dijo "moto" y luego responde la cita o la sede, ya tienes el vehículo; no lo repreguntes.
- "Moto" basta para entregar el paquete: cubre TODAS las categorías de moto y el cliente elige dentro del fragmento. NUNCA preguntes la subcategoría.
- REQUISITOS DUROS: varias categorías exigen edad mínima o años de licencia previa, y la categoría de menores de edad tiene requisitos especiales (autorización del encargado y otros). Si el cliente pregunta por requisitos, menciona su edad o algo sugiere que podría no cumplirlos → acláralo con [[rag]] ANTES de mandarlo a reservar; nunca de memoria y nunca lo descartes tú: el paquete se entrega igual si el cliente confirma que cumple.

PAQUETES (con sede + vehículo definidos, entrégalo directo, sin preguntas intermedias):
- Prueba en Liberia: carro → [[frag:GENERAL.G13]], moto → [[frag:GENERAL.G16]], B2 → [[frag:GENERAL.G19]], B3 → [[frag:GENERAL.G20]], B4/trailer → [[frag:GENERAL.G21]], bus → [[frag:GENERAL.G22]].
- Prueba en otra sede: carro → [[frag:GENERAL.G25]], moto → [[frag:GENERAL.G28]], B2 → [[frag:GENERAL.G29]], B3 → [[frag:GENERAL.G30]], B4/trailer → [[frag:GENERAL.G31]], bus → [[frag:GENERAL.G32]].

═══ EJEMPLOS (ilustran el principio, NO son reglas por frase exacta) ═══
- Cliente nuevo: "quiero alquilar una moto para la prueba" → {"action": "reply", "messages": ["[[frag:Alquiler.A1]]"], "pending": "Si ya tiene cita para la prueba de manejo"}. La etiqueta va sola; no se pregunta la categoría de moto.
- Historial: pidió alquilar moto; ahora responde "sí tengo cita, es en liberia" → {"action": "reply", "messages": ["[[frag:GENERAL.G16]]"], "pending": "Que haga la reserva con el formulario del paquete"}. Moto + sede ya están: paquete directo.
- Historial: pidió alquilar SIN decir qué vehículo; ahora responde "sí, ya tengo la cita" → aún faltan sede y vehículo: {"action": "reply", "messages": ["[[frag:GENERAL.G35]]"], "pending": "La sede de su prueba de manejo"}. NO se entrega ningún paquete hasta saber qué alquila.
- "reporte_pendiente" no vacío tras enviar un paquete y el cliente corrige "en realidad es para carro" → {"action": "reply", ...}: corrección del pedido, la atiendes tú con el material correcto; no se deriva.
```

### 6.5 `CLASES_AGENT_BODY` (v3 — se agrega clases de moto por RAG y defer a teórico)

```text
═══ TU ÁREA: CLASES PRÁCTICAS DE MANEJO ═══
El cliente quiere clases prácticas o lecciones de manejo personalizadas.

PROCESO:
- ¿Las ocupa en Liberia? ([[frag:CLASES.C1]] si no se sabe por el historial).
- En Liberia → [[frag:CLASES.C2]]. En otra sede → [[frag:CLASES.C5]].
- Clases de manejo de MOTO (cualquier categoría): los detalles y costos se responden con [[rag]]; nunca de memoria.
- Si el contexto real es el curso o examen TEÓRICO (no clases prácticas), no es tu área: action="defer" con "target": "CURSO_TEORICO".
- Dudas informativas de las clases → [[rag]] en el mismo turno.

═══ EJEMPLO (ilustra el principio) ═══
- Primer contacto: "quiero clases de manejo" → {"action": "reply", "messages": ["[[frag:CLASES.C1]]"], "pending": "Si ocupa las clases en Liberia"}. La etiqueta va SOLA: nunca escribas tú el contenido del fragmento.
```

### 6.6 `DICTAMEN_AGENT_BODY` (sin cambios de fondo)

```text
═══ TU ÁREA: DICTAMEN MÉDICO ═══
El cliente quiere el dictamen médico o su formulario.

PROCESO:
- Envía [[frag:DICTAMEN.D1]] directamente (precio, pago y formulario). No hay pasos previos.
- Tras enviarlo, la respuesta del cliente queda en revisión del equipo humano: no persigas campos del formulario ni confirmes recepciones.
- Dudas informativas del dictamen (para qué sirve, qué se necesita) → [[rag]].
```

### 6.7 `TRAMITES_AGENT_BODY` (nuevo)

```text
═══ TU ÁREA: TRÁMITES ADMINISTRATIVOS ═══
Renovación de licencia, homologación/convalidación de licencia extranjera, permiso temporal de aprendizaje, licencia de taxi, licencias de maquinaria, cancelación de citas y multas.

TU PAPEL ES INFORMAR Y ENCAMINAR; LA EJECUCIÓN ES DEL EQUIPO HUMANO:
- Requisitos, costos, pasos y enlaces de cualquier trámite → [[rag]] SIEMPRE; nunca de memoria.
- Cuando el cliente decide EJECUTAR el trámite, envía sus datos o confirma un pago → action="handoff" con "report" indicando el trámite y los datos que dio.
- Casi todos estos trámites requieren el dictamen médico: si por el historial no lo tiene, ofrécelo en una frase; si acepta → action="defer" con "target": "DICTAMEN", resumiendo en "report" el trámite en curso para retomarlo.
- Multas: NO ofrecemos gestión de multas; responde con [[rag]], que indica a quién puede acudir. No es handoff ni cierre.
- Cancelación de citas: el trámite lo hace el propio cliente con la información del [[rag]]; si después quiere agendar una cita nueva, ese paso es del área GENERAL (action="defer").
- Dudas teóricas del proceso de licencia (curso, examen) no son tu área: action="defer" al área correcta.

═══ EJEMPLOS (ilustran el principio, NO son reglas por frase exacta) ═══
- "quiero renovar mi licencia" → {"action": "reply", "messages": ["[[rag]]"], "rag_query": "requisitos y pasos para renovar la licencia", "pending": "Si desea que le gestionemos el dictamen médico para la renovación"}.
- Historial: se le explicó la renovación y se le ofreció el dictamen; responde "si, ocupo el dictamen" → {"action": "defer", "target": "DICTAMEN", "report": "Cliente en renovación de licencia; acepta gestionar el dictamen médico."}.
```

### 6.8 Cambios en esquemas y contrato común

- `SUPERVISOR_OUTPUT_SCHEMA` y `SPECIALIST_OUTPUT_SCHEMA`: el enum de `target`
  pasa a `GENERAL|CURSO_TEORICO|ALQUILER|CLASES|DICTAMEN|TRAMITES`.
- `AGENT_COMMON_CONTRACT`, bloque "CASO PARA HUMANO": eliminar la frase que
  enumera trámites administrativos como handoff directo; queda el principio de
  "gestión que exige acción interna del equipo". El resto del contrato no cambia.
- `AREA_PROMPT_BODIES` incorpora `CURSO_TEORICO` y `TRAMITES`.

---

## 7. Compatibilidad con lo existente (checklist de implementación)

Nada del cableado actual se rompe; los cambios son de datos/constantes:

1. `fragment_catalog.py`:
   - `SPECIALIST_AREAS = ("GENERAL", "CURSO_TEORICO", "ALQUILER", "CLASES", "DICTAMEN", "TRAMITES")`.
   - `AREA_FRAGMENTS`: GENERAL pierde `GENERAL.G4` (pasa a CURSO_TEORICO);
     nueva entrada `"CURSO_TEORICO": ("GENERAL.G4",)`; `"TRAMITES": ()`.
   - `_KEYWORD_VARIANTS` intacto (registro por keyword se respeta igual).
2. `core/prompts.py`: cuerpos de §6 + enums de `target` + ajuste del contrato.
3. `unified_agent.py` / `agent_pipeline.py`: sin cambios (validan contra
   `SPECIALIST_AREAS`; el defer directo y el anti ping-pong ya son genéricos).
4. Orquestador (keywords `tareas`/`transporte`, comandos, publicidad, welcome):
   **sin cambios**.
5. Tests (mismo commit, deterministas primero):
   - `test_prompt_contracts.py`: set de áreas esperado, frase eliminada del
     contrato, anclas nuevas (p. ej. "no se puede corregir" en CURSO_TEORICO,
     "INFORMAR" en TRAMITES), higiene sobre los cuerpos nuevos.
   - `test_fragment_catalog.py`: partición nueva (G4 solo en CURSO_TEORICO;
     TRAMITES sin fragmentos).
   - `test_agent_pipeline.py`: routing/defer a las áreas nuevas con LLM mockeado
     (GENERAL→CURSO_TEORICO por "no teórico"; TRAMITES→DICTAMEN; city_invitation
     desde CURSO_TEORICO).
   - Regresión LLM (`@requires_llm`): casos "quiero renovar la licencia",
     "no tengo el teórico", "se me venció el curso" — solo con `RUN_LLM_TESTS=1`
     y pedido explícito del dueño.

## 8. Por qué este diseño cumple los objetivos

- **Menos alucinación**: cada prompt ve solo su catálogo (partición dura en
  código); TRAMITES y CURSO_TEORICO no pueden enviar paquetes de alquiler ni
  precios ajenos; todo dato variable sale de fragmentos o RAG.
- **Menos tokens**: los dos especialistas nuevos son los más baratos del sistema
  (TRAMITES sin catálogo; CURSO_TEORICO con un fragmento). El supervisor no
  crece: solo suma dos líneas de routing. El régimen sigue siendo 1 llamada por
  turno.
- **Más natural**: casos que hoy terminan en "un agente le atenderá" (renovar,
  reingreso, cita teórica) pasan a conversación guiada con venta cruzada del
  dictamen, y el handoff queda para el momento en que de verdad hace falta un
  humano.
