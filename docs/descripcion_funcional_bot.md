# Descripcion funcional del bot de atencion al cliente

## Introduccion

Este es un agente de atencion al cliente robusto para una escuela de manejo. Su objetivo es recibir a cada cliente, entender que necesita y guiarlo con mensajes claros hacia el proceso correcto: obtencion de licencia, curso teorico, prueba de manejo, alquiler de vehiculo, clases de manejo, dictamen medico, quejas, publicidad, ingreso a grupos o seguimiento por palabras clave.

El sistema cuenta con un RAG avanzado. RAG significa "generacion aumentada por recuperacion" y, en palabras sencillas, es una forma de darle al agente informacion actualizada antes de responder. En vez de inventar una respuesta o depender solo de lo que el modelo conoce, el bot busca fragmentos de informacion guardados en una base de conocimiento, toma los mas relevantes para la pregunta del cliente y con eso construye una respuesta breve, util y respaldada.

El bot tambien maneja mensajes entrantes, clientes que llegan desde publicidad, ingresos a grupos, procesos por palabras clave como `tareas` y `transporte`, recordatorios y reportes para el equipo humano. Cuando detecta molestias, solicitudes de asesor, comprobantes, revisiones manuales o respuestas que requieren seguimiento personalizado, crea un reporte y pausa temporalmente la automatizacion para que una persona pueda atender el caso sin interrupciones.

## Vision general del sistema

El bot trabaja como una recepcion automatica con memoria de conversacion. Cada mensaje entrante se registra y luego se procesa para decidir que camino debe seguir.

Cuando llega un mensaje nuevo, el sistema puede tomar estos caminos:

1. Si el cliente viene desde publicidad, entra al flujo de publicidad correspondiente. Este camino se usa para personas que llegan por invitaciones, campanas o mensajes promocionales.
2. Si el mensaje no viene desde publicidad, entra primero al agente recepcionista. Este agente entiende la intencion del cliente, responde dudas iniciales cuando corresponde y deriva al proceso adecuado.
3. Si el mensaje corresponde a una palabra clave especial, como `tareas` o `transporte`, se activa el flujo respectivo de seguimiento.

Desde el agente recepcionista, el cliente puede ser derivado a estos procesos:

- Informacion general: cuando pregunta por licencia, curso teorico, citas, prueba de manejo, COSEVI, MOPT u orientacion general.
- Alquiler de vehiculo: cuando pide alquilar, rentar o reservar un vehiculo para prueba de manejo.
- Clases de manejo: cuando solicita clases practicas, lecciones o practica de conduccion.
- Dictamen medico: cuando consulta por dictamen, examen medico, cita, requisitos o formulario.
- Quejas: cuando expresa molestia, reclamo, devolucion, mal servicio o enojo.
- Clientes que aprobaron: cuando informa que gano, aprobo o paso una prueba.
- Publicidad: cuando el contexto indica que viene por una invitacion o campana.
- Bienvenida a grupos: cuando se detecta que el cliente ingreso a un grupo.

Las palabras clave `tareas` y `transporte` funcionan como accesos directos a procesos especiales. Cuando alguno de estos caminos se inicia, el sistema registra al cliente en NocoDB para reconocerlo en futuras interacciones. Ese registro permite aplicar informacion diferenciada cuando corresponda, por ejemplo enviar SINPE distintos en casos de dictamen o en informacion relacionada con alquiler de motos.

Tanto el agente recepcionista como los procesos guiados pueden responder preguntas. Esto significa que el cliente puede hacer una duda al inicio de la conversacion o mientras ya esta dentro de un proceso, y el bot intentara responderla sin perder el hilo principal.

## Canales y tipos de mensajes

El sistema esta preparado para trabajar por distintos canales de mensajeria. Cada cliente mantiene su propia conversacion, de modo que el historial y el seguimiento no se mezclan con otros usuarios.

Los mensajes que atiende son:

- Texto: se procesa normalmente y puede activar procesos de atencion, RAG, publicidad o palabras clave.
- Audio: se agrega a la conversacion como texto transcrito y luego se procesa junto con otros mensajes recientes.
- Imagen o documento: el bot avisa que no puede ver imagenes/documentos. Si el cliente insiste, se interpreta como una solicitud de ayuda para revision humana.
- Eventos de grupo: cuando el sistema detecta ingreso a grupo, puede enviar el mensaje de bienvenida y cambiar el estado del cliente; este mensaje de bienvenida se envía solo si el cliente primero recibió el mensaje de invitación cuando llego por publicidad.

## Recepcion inicial

Cuando un cliente escribe por primera vez, o cuando no hay un proceso activo, el sistema entra en una etapa de recepcion. En esta etapa no se limita a buscar palabras sueltas; interpreta el mensaje completo para entender si el cliente esta preguntando algo, si quiere contratar un servicio, si tiene una queja o si necesita una persona.

La recepcion decide entre varias acciones:

- Responder una duda y luego iniciar un proceso guiado.
- Responder una duda y hacer una pregunta aclaratoria.
- Iniciar directamente un proceso guiado.
- Pedir una aclaracion breve.
- Crear reporte para asesor humano.
- Cerrar la conversacion si el cliente indica que no necesita continuar.

Esta recepcion es importante porque muchos clientes escriben mensajes mezclados, por ejemplo: "quiero sacar licencia de moto, que necesito?" En ese caso el bot puede responder primero la duda con RAG y luego preguntar si desea mas información sobre el proceso.

El detalle paso a paso de cada proceso se revisa en el diagrama de flujo del sistema. Este documento se enfoca en explicar como se comporta el agente en la operacion diaria y que debe esperar el equipo al usarlo.

## RAG y base de conocimiento

El RAG es el componente que permite responder preguntas que no estan completamente cubiertas por las respuestas fijas del bot.

La informacion del RAG se administra desde una base de conocimiento editable en NocoDB/FAQ, donde se actualizan requisitos, costos, instrucciones, enlaces, condiciones y cualquier otro dato operativo que el bot deba usar para responder.

Cuando se agrega o modifica informacion, el bot puede empezar a utilizarla hasta 5 minutos despues de la actualizacion.

Cuando el cliente hace una pregunta:

1. El bot identifica la duda del cliente.
2. Busca en la base de conocimiento la informacion mas relacionada.
3. Usa solo la informacion encontrada para construir una respuesta clara.
4. Si no hay informacion suficiente, no inventa: registra la pregunta como no respondida NocoDB/No answer y ofrece contactar o contacta directamente a un asesor.

El RAG tambien toma en cuenta el contexto reciente de la conversacion. Esto permite que, si el cliente pregunta algo mientras avanza por un proceso, el bot responda la duda y luego retome la pregunta pendiente.

## Actualizacion de la base de conocimiento

La base de conocimiento se puede actualizar para que el bot responda con informacion vigente. Esto permite ajustar preguntas frecuentes, requisitos, costos, instrucciones, enlaces o politicas sin reentrenar al agente ni rehacer el proceso completo.

Si el equipo agrega, modifica o elimina informacion de esa base, el bot puede usar esos cambios en sus siguientes respuestas.

## Preguntas laterales durante un proceso

Un cliente puede responder una pregunta del proceso y al mismo tiempo hacer una duda adicional. Por ejemplo: "Si, es en Liberia, pero que pasa si pierdo la prueba?"

En ese caso el sistema:

- Detecta la respuesta principal para avanzar el proceso.
- Extrae la pregunta lateral.
- Usa RAG para responder esa duda.
- Luego envia los mensajes del siguiente paso del proceso.

Si la pregunta lateral no tiene respuesta en la base de conocimiento, queda registrada como pregunta sin respuesta y el bot indica que puede contactar a un asesor.

## Reportes y seguimiento humano

Los reportes son la forma en que el bot avisa al equipo que una conversacion necesita revision humana. No todos los mensajes generan un reporte: el bot primero intenta guiar, responder o continuar el proceso. El reporte aparece cuando el cliente llega a un punto donde ya conviene que una persona revise el caso.

En terminos practicos, un reporte significa: "este cliente respondio o tiene una situacion que el equipo debe atender". El reporte normalmente contiene:

- Nombre del cliente.
- Numero de contacto.
- Problema o motivo.
- Link directo a WhatsApp.
- Canal de origen.

La lista de reportes se revisa en la tabla de reportes del sistema (NocoDB). Los reportes nuevos aparecen al inicio.

Hay dos formas comunes en que nace un reporte:

- Reporte inmediato: ocurre cuando el cliente muestra una queja, pide asesor, envia algo que requiere revision manual o hace una consulta que el bot no debe resolver solo.
- Reporte por respuesta posterior: ocurre cuando el bot ya envio un mensaje importante o un recordatorio y queda esperando. Si el cliente responde en ese punto, el sistema avisa al equipo para que continue la atencion.

Se generan reportes en casos como:

- Quejas o enojo.
- Solicitud explicita de asesor, persona o humano.
- Casos administrativos como pagos, comprobantes, revision, validacion o seguimiento.
- Respuesta a mensajes finales de formularios.
- Respuesta a recordatorios que ya requieren revision.
- Respuesta a bienvenida de grupo.
- Respuesta a recordatorios de publicidad.
- Respuesta a recordatorios de palabras clave.
- Falta de datos importantes en configuraciones de publicidad.
- Preguntas que no tienen respuesta en RAG, cuando corresponde atencion humana.

## Que ocurre despues de un reporte

Cuando se genera un reporte, el sistema pausa temporalmente la automatizacion para ese usuario, normalmente por 12 dias en los procesos formales. Esto evita que el bot siga enviando mensajes mientras el asesor atiende el caso.

La pausa no significa que el cliente queda ignorado. Significa que la conversacion queda protegida para que el equipo pueda contactar al cliente sin respuestas automaticas cruzadas.

La logica general es:

1. El bot envia un mensaje importante o un recordatorio.
2. El sistema queda esperando por si el cliente contesta.
3. Si el cliente responde en un punto que requiere revision humana, se crea un reporte.
4. Despues del reporte, la automatizacion queda pausada para ese usuario.
5. El equipo revisa el reporte y continua la atencion manualmente.

Tambien puede haber pausas temporales por publicidad, ingreso a grupo, palabras clave o pausas por intervención manual el equipo. En todos los casos, la finalidad es la misma: evitar que el bot interfiera cuando hay una secuencia especial o un seguimiento humano en curso.

## Recordatorios

Los recordatorios permiten dar seguimiento automatico cuando el cliente no responde. Sirven para mantener viva la conversacion sin que una persona tenga que escribir manualmente cada vez.

No todos los recordatorios generan reportes. Algunos solo buscan que el cliente retome la conversacion; otros continuan el proceso si el cliente responde; y otros si estan pensados para avisar al equipo cuando el cliente contesta.

El funcionamiento general es:

1. El bot envia un mensaje que espera respuesta.
2. Si el cliente responde antes del recordatorio, el bot cancela ese seguimiento y continua la conversacion.
3. Si el cliente no responde, el bot envia un recordatorio.
4. Despues del recordatorio, el sistema queda atento por si el cliente contesta.
5. Segun el tipo de recordatorio, la respuesta del cliente puede continuar la conversacion, cerrar el seguimiento o generar un reporte para el equipo.

Cada recordatorio puede tener:

- Tiempo de espera en segundos.
- Mensajes que se enviaran.
- Indicacion de si la respuesta del cliente requiere reporte.
- Otro recordatorio encadenado, si el proceso necesita mas de un intento.

En algunos procesos puede haber mas de un recordatorio. Por ejemplo, el bot puede enviar un primer seguimiento y, si el cliente sigue sin responder, enviar un segundo. Si el cliente contesta despues, el sistema revisa que corresponde hacer en ese punto: continuar automaticamente, dejar la conversacion lista para seguir o avisar al equipo.

En publicidad y palabras clave tambien hay recordatorios programados. En esos casos el objetivo es dar seguimiento a una invitacion, a un ingreso a grupo o a un proceso especial. Si el cliente responde en un momento que requiere revision humana, el equipo recibe un reporte para revisar la conversacion y continuar manualmente si corresponde.

## Agrupacion de mensajes

Para evitar respuestas precipitadas, el bot espera unos segundos antes de procesar el mensaje. Actualmente espera 2 segundos. Si el cliente envia varios textos seguidos durante ese tiempo, el sistema los une y analiza todo como una sola entrada.

Esto mejora la comprension en conversaciones reales. Por ejemplo, si una persona escribe:

"Hola"

"quiero sacar licencia"

"pero no se que necesito"

El bot no responde tres veces por separado; espera brevemente y responde tomando en cuenta el mensaje completo.

## Registro de conversaciones

El sistema guarda conversaciones entrantes y salientes para que el equipo pueda revisar el historial, entender que se respondio y mejorar la atencion.

Tambien registra preguntas que el bot no pudo responder. Esto ayuda a detectar temas que deben agregarse a la base de conocimiento o casos que necesitan una respuesta mas clara.

Estos registros sirven para auditoria, seguimiento y mejora continua.

## Reinicio para pruebas

Para hacer pruebas, el dueño del negocio o una persona autorizada puede escribir `/d`. Este comando limpia la conversacion de prueba y permite empezar desde cero, como si el cliente estuviera escribiendo por primera vez.

Es util cuando se quiere probar otro camino del bot, repetir un proceso o validar cambios sin que el historial anterior afecte la nueva conversacion.

## Cierre de conversacion

El cierre puede ocurrir tanto en la recepcion inicial como durante cualquier proceso guiado. Si el cliente indica que no desea continuar, que solo estaba preguntando o que ya no necesita ayuda, el bot cierra con un mensaje cordial y limpia el estado de conversacion.

Esto permite que una conversacion no quede forzada dentro de un proceso. Por ejemplo, si el cliente recibio una respuesta y luego dice que no necesita mas informacion, el bot entiende el rechazo, cierra correctamente y queda listo para una nueva conversacion futura.
