# Auditoría del diagnóstico de conversaciones

El diagnóstico ZIP conserva sus tres archivos (`diagnostico.json`, `reporte.html`
y `README.txt`). El JSON usa la versión **2.0** y añade `audit`, `block_state`,
`silences` y `audit_coverage` sin quitar las secciones anteriores. La descarga
mantiene los permisos administrativos existentes y el ámbito del negocio.
También funciona si solamente queda auditoría. No se modifica el listado de
conversaciones ni se añade una pantalla para buscar chats ya eliminados.

## Conservación y privacidad

`conversation_audit` conserva cada suceso durante tres meses calendario. Se
purga diariamente desde la tarea de limpieza existente de Celery beat. El
historial del chat mantiene su retención actual de 20 días de inactividad;
ni borrarlo manualmente ni ejecutar `/d` elimina la auditoría.

La auditoría contiene descripciones, responsables conocidos, códigos de causa,
identificadores de configuración, referencias de mensajes y tareas, y fechas.
No contiene texto del cliente o del bot, anuncios, transcripciones, adjuntos,
prompts, payloads ni razones libres de reportes. Las causas de reportes se
clasifican por su proceso de origen; su texto puede contener datos del cliente
y no se copia. `template_id` identifica la clave configurada de una plantilla;
`keyword_id` identifica la fila de palabra clave, no el mensaje recibido.

Los tiempos del suceso y del registro se guardan como `TIMESTAMPTZ`. El HTML
presenta los sucesos con día/mes/año, horas, minutos, segundos, desplazamiento UTC
y zona horaria del negocio. Si el proveedor facilita la hora de envío, se
conserva por separado de la recepción. Una confirmación de envío al proveedor
no equivale a entrega o lectura del mensaje.

## Origen y relaciones

Los triggers de Postgres registran creaciones, renovaciones, retiros y
vencimientos de bloqueos, y borrados del historial, en la misma operación que
los produce. Los emisores del bot registran recepciones, decisiones, intentos
de envío y sus resultados, independientemente del log del chat. Las tareas
propagan las referencias mediante sus cabeceras; la publicación se registra
al confirmar el broker. La cancelación se describe como solicitud, porque una
tarea puede estar ya ejecutándose.

Cada decisión de silencio incorpora una instantánea estructurada del bloqueo
consultado y la referencia de su último evento conocido. `parent_event_id`,
`correlation_id`, `cause_event_id`, `previous_block_event_id` y `task_id`
permiten seguir sus relaciones. `related_trace_ids` enlaza trazas todavía
conservadas cuando existe una referencia verificable, sin inferir relaciones
solo por la proximidad de sus horas.

Desde el dashboard se registra el usuario autenticado y el administrador real
si hubo suplantación. Desde WhatsApp se identifica la cuenta del negocio, no
una persona que el proveedor no identifica. El contexto SQL del dashboard se
restablece antes de devolver la conexión al pool; el del bot usa configuración
local a la sentencia para que no se mezcle entre hilos.

## Despliegue y límites

1. Aplicar la migración `036_auditoria_conversaciones.sql` con el mecanismo
   habitual del dashboard antes de desplegar el bot y los workers actualizados.
2. Reiniciar los procesos actualizados, manteniendo Celery beat activo para
   la limpieza diaria. No cambiar las prioridades ni las duraciones de bloqueos.
3. Descargar un diagnóstico de prueba y comprobar la cobertura, el responsable,
   las referencias y la zona horaria.

La migración observa los bloqueos existentes como `block_observed`; su fecha
es la de observación, no una creación histórica reconstruida. `stored_initial_at`
es el dato legado de `blocked_at` y no acredita la última renovación.

Los antecedentes ya borrados antes del despliegue no se recuperan. Si el evento
de origen ya salió de la ventana de tres meses, el diagnóstico indica
`origin_event_available=false` y conserva la causa estructurada disponible del
bloqueo actual. La auditoría se mantiene dentro del proyecto; eliminar el propio
proyecto elimina sus datos por cascada.

Un fallo de auditoría no cambia la respuesta ni el estado de bloqueo: la capa
Python lo registra en los logs y los triggers emiten una advertencia SQL sin
abortar la operación principal. Los logs operativos deben revisarse si aparecen
advertencias `conversation_audit_*`; una interrupción de almacenamiento puede
producir huecos, que no deben reconstruirse como hechos ciertos.
