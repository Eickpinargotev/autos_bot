# Auditoría de preguntas sin respuesta

Cada pregunta nueva conserva `client_id`, canal, correlación y una copia de
las trazas de la interacción que provocó la falta de respuesta. La consulta
original del cliente se distingue de la consulta reformulada para buscar en
el conocimiento. La copia incluye las evidencias del agente y del RAG
(recuperación, chunks, puntuaciones, resultado y errores disponibles), además
de la respuesta de respaldo prevista. No representa razonamiento privado.

El dueño del proyecto ve el ID de conversación en su bandeja de preguntas.
El administrador accede desde **Proyectos → proyecto → Auditar preguntas**.
Solo una sesión con rol efectivo administrador puede descargar el JSON en
`/admin/negocios/{proyecto_id}/preguntas/{pregunta_id}/diagnostico.json`.
La suplantación conserva las restricciones del dueño: para descargar se debe
volver a la cuenta de administrador. Se valida también la pertenencia de la
pregunta al proyecto, y la respuesta usa `Cache-Control: no-store`.

El JSON contiene únicamente la interacción identificada por correlación, no
el historial completo. Los prompts pueden incluir el contexto previo que vio
el modelo. La copia se conserva aunque se borre el chat o cambie el conocimiento;
las trazas en vivo completan las salidas inmediatas que sigan disponibles.
Se mantienen los recortes y la redacción de secretos. La pregunta y su copia
caducan juntas, 24 horas después de marcarla como entendida.

Las preguntas históricas solo recuperan su origen si existe una única traza
exitosa de creación que coincida por proyecto, texto y ventana de inserción.
Si no hay evidencia suficiente, el ID aparece como «No disponible» y el JSON
explica la limitación, sin atribuirla a una conversación por aproximación.

## Audios y actividad visible

Un bloqueo impide transcribir/responder, pero el audio queda visible con la
leyenda «Audio recibido · no se procesó porque la IA estaba pausada en esta
conversación». Las trazas técnicas no modifican la hora ni el orden del listado;
los mensajes, audios, reportes y entradas/salidas de grupos visibles sí.

La migración 039 recupera avisos de audios antiguos solo si la auditoría acredita
la recepción y el bloqueo y son posteriores al último evento visible del chat.
No recupera contenido de audio ni descarga medios; evita duplicados y respeta
el orden de la paginación por ID. Los huecos anteriores a mensajes posteriores
no se rellenan automáticamente.

## Despliegue

Arrancar primero el dashboard para aplicar las migraciones 037–039 y después
actualizar bot, webhook y workers. No se agregan dependencias de producción.

## Consultas múltiples y respuestas parciales

Cada búsqueda `rag.search` registra su consulta y sus fuentes; la generación
registra los chunks combinados, sin duplicados, y `unanswered_queries`. Cuando
hay respuesta parcial se registra una pregunta por cada parte sin respaldo,
conservando la consulta original y la respuesta parcial prevista en su copia de
evidencias. No se registra como desconocida la parte que sí se respondió.
El JSON incluye `planned_response`; `planned_fallback` se conserva como alias por
compatibilidad con las descargas anteriores. Ninguno confirma por sí mismo que
el proveedor haya entregado la respuesta; eso se revisa en las trazas de envío.
