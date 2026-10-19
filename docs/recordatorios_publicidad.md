# Textos de los recordatorios de publicidad

En **Mensajes → Configuración → Publicidad** se editan los tres recordatorios,
junto con sus tiempos actuales. Los textos son comunes a todas las ciudades del
negocio; no modifican el contenido de las publicidades del catálogo.

Los botones Fecha, Hora, Valor y Enlace del grupo insertan `{dia}`, `{hora}`,
`{valor}` y `{enlace}`. El bot sustituye estos marcadores con los datos del curso
al programar las tareas. La vista previa usa datos de ejemplo, no datos de un
cliente. Se permiten textos sin variables, emojis y saltos de línea, con un
máximo de 2.000 caracteres por recordatorio. No se admiten otros marcadores.

Al guardar se validan todos los campos y se escriben en una sola transacción.
Un error de validación conserva lo escrito y señala el campo correspondiente.
Las solicitudes anteriores que omitan los textos mantienen los guardados.

## Compatibilidad

La migración `035_textos_recordatorios_publicidad.sql` solo añade tres columnas
opcionales en `proyecto_tiempos_mensajes`. No modifica publicidades, tiempos ni
colas de Celery. Los campos sin personalizar utilizan exactamente los textos
originales, tanto para negocios existentes como para nuevos.

Los cambios afectan únicamente a recordatorios programados después de guardar:
cada tarea conserva su copia del mensaje final y su hora. No cambia la lógica
que cancela los dos primeros recordatorios al responder el cliente, conserva
el tercero y cancela todos al entrar al grupo. Tampoco cambia el horario
permitido ni la separación de envíos.

## Despliegue

Actualizar el código e iniciar el dashboard para aplicar la migración automática.
Después reiniciar los servicios del bot (incluido el worker de Celery) con la
nueva versión. No purgar Redis ni las tareas pendientes. No hay nuevas
dependencias ni cambios en Docker Compose.

Si el bot se inicia antes de la migración, utiliza los textos originales como
respaldo y registra el error de lectura. Para revertir el código se pueden dejar
las columnas nuevas en la base: la versión anterior no las utiliza.
