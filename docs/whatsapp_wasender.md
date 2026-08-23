# WhatsApp con WasenderAPI: conexión, eventos y flujos

Qué hay que configurar para que el bot atienda por WhatsApp, qué eventos activar
y qué hace el sistema con cada uno. Complementa `docs/seguridad.md` (por qué el
webhook se protege así) y `docs/despliegue_docker_easypanel.md` (dominios).

---

## 1. La URL del webhook

Cada **cliente** —el negocio que conecta su número, hoy la escuela de manejo—
tiene su propia URL:

```
https://<dominio-del-webhook>/webhooks/wasender/<token>
```

El token sale de la tabla `clientes_whatsapp` y se ve, ya armado y listo para
copiar, en **el perfil del cliente (`/admin/negocios/{id}`)** del panel del administrador.

Ese token es la credencial: no hay una segunda contraseña. Si no coincide con
un cliente **activo**, el webhook responde 401 y no procesa nada. Se puede rotar
desde el panel (la URL vieja deja de funcionar en ≤30 s, que es lo que dura la
caché) y desactivar un cliente corta su webhook al instante.

Para que el panel pueda armar la URL completa hace falta `PUBLIC_WEBHOOK_BASE_URL`
en el `.env` del dashboard, con el dominio público del servicio `whatsapp_webhook`
(por ejemplo `https://webhook.tudominio.com`). Sin eso el panel avisa.

**El servicio `whatsapp_webhook` tiene que ser alcanzable desde internet.** Por eso
está en la red `easypanel` y necesita su propio dominio, igual que el dashboard.

### Añadir un cliente nuevo

En el perfil del cliente (`/admin/negocios/{id}`) → «Nuevo cliente». Se le genera su token y su URL. Cada uno
enlaza su propio número en WasenderAPI y sus eventos entran por su propia URL.

### Qué hace el bot con cada tipo de mensaje

El canal sigue llamándose **WhatsApp**; WasenderAPI es el proveedor que lo
implementa (y por eso vive en `infrastructure/channels/`, detrás de
`ChannelSender`). Cambiar de proveedor no debería tocar nada de lo de abajo,
que es del dominio y vale igual para Telegram.

| Llega | Qué pasa | ¿LLM? | ¿Historial? | ¿Se cobra? |
| --- | --- | --- | --- | --- |
| Texto | Al agente | Sí | Sí | Sí |
| Texto con enlace | Acuse `MEDIA_ENLACE` | No | Sí | No |
| Imagen | Acuse `MEDIA_IMAGEN` | No | Sí (`media_avisada`) | No |
| Documento | Acuse `MEDIA_DOCUMENTO` | No | Sí (`media_avisada`) | No |
| Video | Acuse `MEDIA_VIDEO` | No | Sí (`media_avisada`) | No |
| Sticker | **No se responde** | No | Sí (`sticker_ignorado`) | No |
| Audio | Se transcribe y sigue como texto | Sí | Sí (`audio_transcrito`) | Sí, categoría `audio` |

Los acuses **no se facturan**: no pasan por el modelo ni entregan contenido del
negocio. Quien insiste con varios adjuntos seguidos recibe `MEDIA_INSISTE`, que
pide ayuda humana en vez de repetir el mismo aviso.

El **sticker no se responde**: es un gesto, no una consulta, y en WhatsApp cada
envío consume la cuota de ritmo del plan — contestarle deja sin respuesta al
mensaje que sí importaba. Sí queda en el historial, para que quien lea el chat
en el panel vea lo que pasó de verdad.

### Notas de voz

Se transcriben y entran al flujo como si el cliente lo hubiera escrito. **El
audio no se guarda en ninguna parte**: ni en disco, ni en la base, ni en el
historial. Solo sobrevive el texto.

El recorrido tiene tres tramos y ninguno es evitable: WhatsApp entrega la media
cifrada de extremo a extremo, así que primero hay que pedirle a WasenderAPI que
la descifre (`POST /api/decrypt-media`, devuelve una URL válida una hora), luego
descargarla a memoria y por último transcribirla con
`OPENAI_MODEL_TRANSCRIPCION`. Nada de eso corre en el webhook —tarda segundos y
WasenderAPI reintentaría el evento creyendo que se cayó—: lo hace la tarea
`transcribir_nota_de_voz` en el worker.

La duración se lee del propio evento (`audioMessage.seconds`) y se factura **por
segundo**, no por minuto entero: una nota de 8 segundos cuesta 800 micro-USD, no
6.000. Si no se puede transcribir, se cobra igual lo que el proveedor procesó y
el cliente recibe el acuse `MEDIA_AUDIO_ILEGIBLE`.

La etiqueta que el panel muestra ("🎤 Audio transcrito") viaja en `event_type`,
nunca en el texto: **el LLM recibe texto plano**, igual que un mensaje escrito.

Los **enlaces** se reconocen por estructura (`http(s)://`, `www.`, dominio
suelto), lo cual no contradice la regla de no usar regex para interpretar
lenguaje natural: reconocer una URL es lo mismo que reconocer `Imagen=` o `/d`.

Los correos quedan excluidos a propósito. Hoy responde el acuse **aunque el
mensaje traiga además una pregunta**; es decisión del negocio y la condición a
cambiar está señalada en `conversation_orchestrator._handle_text`.

### Límite de ritmo del plan

WasenderAPI limita los envíos según el plan (el de prueba: **1 mensaje por
minuto**) y responde `429` con `retry_after`. El envío espera lo que indica el
proveedor y reintenta una vez; la espera está acotada para caber dentro del
candado por conversación (120 s). Si el plan no da para el ritmo de la
conversación, la solución es subir de plan, no alargar la espera.

### LID: el `remoteJid` no siempre es un teléfono

Según el `addressingMode` de la sesión, WhatsApp manda el **LID**
(`258540019138808@lid`), un identificador interno. Reconoce al cliente, pero no
sirve como destino: enviar ahí devuelve `422 The provided JID does not exist on
WhatsApp`, ni sirve para el enlace `wa.me`, ni le dice nada a quien lee el panel.

Se resuelve en dos capas:

1. **Al entrar**, el teléfono sale de `key.cleanedSenderPn` / `key.senderPn`, que
   es lo que indica la documentación de WasenderAPI. Solo para mensajes
   entrantes: en un `fromMe` el "sender" es el negocio, no el cliente.
2. **Al enviar**, un destino que sea un LID se traduce con la libreta de la
   sesión (`GET /api/contacts`, cacheada 10 min). Es la red de seguridad para
   las conversaciones que quedaron guardadas con el LID como identificador.

Los eventos salientes (`fromMe`) usan esa misma traducción antes de decidir su
origen. En ellos `cleanedSenderPn` identifica al número del negocio, no al
cliente. Si el `message_id` fue registrado al enviar por API, el webhook lo
ignora como eco del bot; si no, lo registra como mensaje del dueño y bloquea esa
misma conversación durante 12 días.

El dueño también puede responder desde el compositor del dashboard. El panel
pide el envío al endpoint interno autenticado del bot; este usa la misma sesión
WasenderAPI, registra la burbuja como **Dueño del negocio**, cancela los
recordatorios y pausa la IA 12 días. El eco `message.sent` queda marcado como
salida propia para no duplicar la burbuja.

Si la libreta falla, el destino se manda tal cual: es preferible el error del
proveedor a arriesgarse a escribirle a otra persona.

### Con qué credencial responde cada negocio

La **salida** usa la `wasender_api_key` del negocio dueño de la conversación. No
hay ninguna clave global en el entorno: se administra en el perfil del cliente y
un negocio nuevo queda operativo sin tocar el `.env` ni redesplegar.

Para saber cuál toca hay un dato que resolver. El mensaje entra por la URL de un
negocio (ahí se sabe de quién es), pero la respuesta sale después, desde el
worker de Celery, que solo recibe canal y número. El puente es la tabla
`conversacion_negocio`: al entrar el mensaje se anota la pertenencia, y al
responder se lee de ahí. El orden de resolución es:

1. El negocio anotado para esa conversación. Es el caso normal.
2. Si no hay anotación y existe **un solo** negocio activo con clave, ese. Cubre
   lo que no nace de un mensaje entrante (un envío manual a alguien que nunca
   escribió) y las conversaciones anteriores a la migración 009.
3. Con dos o más negocios y sin anotación **no se envía**: se avisa de que falta
   la credencial. Adivinar sería escribirle a un cliente desde el número de otro
   negocio, que es peor que no responder.

Rotar la clave desde el panel surte efecto en ≤30 s (lo que dura la caché).

---

## 2. Eventos a activar en WasenderAPI

En la pantalla «Enable Webhook Notifications», con la URL de arriba:

| Evento | Para qué |
| --- | --- |
| `messages.received` | Los mensajes que escriben los clientes. Sin esto el bot no contesta nada. |
| `message.sent` | Los mensajes que salen del número. Es como se detecta que **el dueño escribió desde su teléfono**. |
| `group-participants.update` | Ingresos al grupo del curso. Cierra el flujo de publicidad. |
| `session.status` | Avisa si la sesión de WhatsApp se cae. Opcional, pero conviene. |

Y **dejar apagados**, entre otros:

| Evento | Por qué no |
| --- | --- |
| `messages.upsert` | Duplica `messages.received` y `message.sent`: el mismo mensaje entraría dos veces. |
| `messages-group.received` | Mensajes *dentro* del grupo. El bot no atiende grupos; solo interesa quién entra. |
| `message-receipt.update` | Recibos de entrega y lectura: muchísimo tráfico y no cambia ninguna decisión. |

La misma tabla está en el perfil del cliente (`/admin/negocios/{id}`) y en `services/dashboard/src/services/clientes_whatsapp.py`
(`EVENTOS_REQUERIDOS` / `EVENTOS_DESACONSEJADOS`).

---

## 3. Qué hace el bot con cada evento

### Mensaje del cliente (`messages.received`)

El camino normal: se registra en el log, y según el caso entra al flujo de
publicidad (`add["ciudad"]`), a una keyword (`tareas`, `transporte`) o al agente.

Si el cliente está **bloqueado**, el mensaje **igual se guarda** —para que la
conversación quede completa en el panel— pero el bot no responde.

### Mensaje saliente (`message.sent`)

Aquí está la parte delicada: **el bot y el dueño comparten el número**, así que
un mensaje saliente puede ser de cualquiera de los dos y solo uno de los casos
debe callar al bot.

- Si el mensaje lo mandó el bot (lo sabe `outbound_registry`, que anota el id de
  cada envío y, como respaldo, la huella del texto): se ignora.
- Si no: **intervención humana**. Se registra en el chat como «Dueño del
  negocio», se cuenta en el seguimiento del cliente, el número queda **bloqueado
  12 días** y se **cancelan los recordatorios pendientes** (los de publicidad y
  los inteligentes). A partir de ahí atiende la persona; el bot no vuelve a
  intervenir.

Sin esa distinción el bot leería su propia primera respuesta como una
intervención humana y se bloquearía a sí mismo.

### Ingreso al grupo (`group-participants.update`, acción `add`)

Solo se actúa sobre quien tenga **contexto de publicidad** activo: es la señal
que cierra ese flujo. Al entrar:

1. Se cancelan los recordatorios programados de la publicidad.
2. Se envía la bienvenida al grupo (`WELCOME`/`W` de `mensajes.json`).
3. El número queda **bloqueado 12 días**.

Las salidas del grupo llegan por el mismo evento con acción `remove` y quedan
visibles en la conversación como una nota de sistema. No envían mensajes ni
deshacen automáticamente el bloqueo. Los cambios de administrador se ignoran.

---

## 4. Los dos orígenes de un cliente

**Viene de publicidad.** Escribe con el texto de la campaña, se detecta la
ciudad y se le manda la invitación de esa ciudad (que incluye el link del
grupo). Se programan los recordatorios de publicidad. Se cancelan cuando entra
al grupo o cuando el dueño escribe.

**Escribe normal** («Hola»). Entra el flujo conversacional del agente, con sus
recordatorios inteligentes. Se cancelan igual si el dueño escribe.

---

## 5. Ver las conversaciones

`/conversaciones` — listado del proyecto autenticado por actividad reciente, **búsqueda solo por número de
teléfono** (los guiones, espacios y el `+` se ignoran). No se busca dentro del
texto de los mensajes: eso obligaría a recorrer todo el historial y dejaría el
panel lento justo cuando más conversaciones hay.

`/conversaciones/{canal}/{numero}` — el chat. Tres voces distinguidas: el **cliente**
a la izquierda, y a la derecha el **bot** y el **dueño del negocio** (este con
marca de color). Los eventos técnicos (llamadas a herramientas) están ocultos
salvo que se pidan.

Se carga **por tandas** de 60 mensajes, con «Cargar mensajes anteriores»: una
conversación con meses de historial no puede llegar entera en una página.

Las horas son las de la configuración del proyecto, no las del servidor. El dueño
puede bloquear permanentemente desde el hilo; la lista se administra con carga
diferida en «Configuración del proyecto». El administrador solo accede tras entrar a
la cuenta mediante la suplantación auditada.

> Recuerda que la retención es de 20 días desde la última interacción
> (`CONVERSATION_RETENTION_DAYS`): pasado ese plazo de inactividad la
> conversación se purga.

---

## 6. Comprobar que funciona

1. En el perfil del cliente (`/admin/negocios/{id}`), la columna **«Último evento»** empieza a moverse en
   cuanto WasenderAPI manda algo. Si sigue vacía, los eventos no están llegando
   (dominio mal apuntado, URL mal pegada o token rotado sin actualizar).
2. Escribe al número desde otro teléfono: debe aparecer la conversación en
   `/conversaciones` al entrar a la cuenta del proyecto.
3. Responde desde el teléfono del negocio: en el chat debe salir como «Dueño del
   negocio» y el bot debe quedarse callado.
4. Únete al grupo desde un número que venga de publicidad: deben cancelarse sus
   recordatorios y llegar la bienvenida.
