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

> Estado actual: la **salida** de mensajes sigue usando la `WASENDER_API_KEY`
> global del entorno. La columna `wasender_api_key` de la tabla existe para
> cuando haya más de un número que responder, pero todavía no está cableada al
> envío. Con un solo cliente no cambia nada; con el segundo hay que enrutar el
> envío por cliente antes de conectarlo.

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

Las salidas del grupo y los cambios de administrador llegan por el mismo evento
y se ignoran.

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

`/admin/logs` — listado por actividad reciente, **búsqueda solo por número de
teléfono** (los guiones, espacios y el `+` se ignoran). No se busca dentro del
texto de los mensajes: eso obligaría a recorrer todo el historial y dejaría el
panel lento justo cuando más conversaciones hay.

`/admin/logs/{canal}/{numero}` — el chat. Tres voces distinguidas: el **cliente**
a la izquierda, y a la derecha el **bot** y el **dueño del negocio** (este con
marca de color). Los eventos técnicos (llamadas a herramientas) están ocultos
salvo que se pidan.

Se carga **por tandas** de 60 mensajes, con «Cargar mensajes anteriores»: una
conversación con meses de historial no puede llegar entera en una página.

Las horas son las de **`ZONA_HORARIA`** (`America/Costa_Rica` por defecto), no
las del servidor, y cada día lleva su separador con la fecha completa.

> Recuerda que la retención es de 20 días desde la última interacción
> (`CONVERSATION_RETENTION_DAYS`): pasado ese plazo de inactividad la
> conversación se purga.

---

## 6. Comprobar que funciona

1. En el perfil del cliente (`/admin/negocios/{id}`), la columna **«Último evento»** empieza a moverse en
   cuanto WasenderAPI manda algo. Si sigue vacía, los eventos no están llegando
   (dominio mal apuntado, URL mal pegada o token rotado sin actualizar).
2. Escribe al número desde otro teléfono: debe aparecer la conversación en
   `/admin/logs`.
3. Responde desde el teléfono del negocio: en el chat debe salir como «Dueño del
   negocio» y el bot debe quedarse callado.
4. Únete al grupo desde un número que venga de publicidad: deben cancelarse sus
   recordatorios y llegar la bienvenida.
