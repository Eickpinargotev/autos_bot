# Seguridad: infraestructura, aplicación y datos

Postura de seguridad del bot y decisiones tomadas. Complementa
`docs/operacion_escala_y_trazabilidad.md` (integridad y concurrencia).

---

## 1. Infraestructura (Docker / red)

| Superficie | Estado | Detalle |
| ---------- | ------ | ------- |
| Puertos en producción (`docker-compose.yml`) | ✅ Cerrados | Ningún servicio publica puertos al host. Postgres, Redis y Qdrant solo son alcanzables por la red interna de Compose. El dashboard (8020) y el webhook (8010) usan `expose` y salen a internet **solo** por el reverse proxy de EasyPanel, cada uno con su dominio. El webhook tiene que ser público (es la URL que se registra en WasenderAPI); lo protege el token de cada cliente, no la red. |
| Puertos en local (`docker-compose.local.yml`) | ⚠️ Abiertos a propósito | 5432/6379/6333/8010/8020 publicados para desarrollo. **No usar este archivo en un servidor con IP pública.** |
| Secretos | ✅ Fuera del repo | Todo llega por `.env` (no versionado). Los compose solo interpolan variables. Nunca commitear `.env` ni pegar tokens en código, prompts o docs. |
| Imagen | ✅ Separación prod/dev | La etapa `prod` (default) no incluye pytest ni herramientas de test. |
| Redis | ✅ Acotado | `maxmemory` + `volatile-lru` evita OOM (ver doc de operación). Sin password: aceptable solo porque no publica puerto en producción; si algún día se expone, exigir `requirepass`. |

### Defaults de credenciales

`POSTGRES_PASSWORD` tiene un default de conveniencia (`mi_password_seguro`) en los
compose. En producción **siempre** debe definirse en `.env` un valor real; el default
existe solo para no romper el arranque local.

## 2. Aplicación

### Endpoints con efectos: apagados por defecto, nunca abiertos

Regla del proyecto: **si falta el secreto, el endpoint responde 503**, no se queda
accesible. Son dos:

| Endpoint | Riesgo si queda abierto | Secreto |
| --- | --- | --- |
| `POST /webhooks/wasender/{token}` | Cualquiera podría hacer que el bot conteste y **gaste tokens de OpenAI** a costa del dueño | El token de la ruta (fila de `clientes_whatsapp`) |
| `POST /webhooks/wasender` | Igual que la anterior, en la instalación de un solo cliente | `WASENDER_WEBHOOK_SECRET` |
| `POST /internal/rag/sync/{id}` | Escribe en la base de conocimiento: se podrían **envenenar las respuestas del RAG** | `INTERNAL_API_TOKEN` |

- Los secretos se comparan con `hmac.compare_digest` (tiempo constante).
- El de Wasender se acepta por cabecera (`X-Webhook-Signature`) o por query, porque la
  documentación pública de WasenderAPI no precisa cuál usa; ambas pasan por la misma
  comparación.
- **Un webhook por cliente (negocio).** Cada fila de `clientes_whatsapp` tiene su
  `webhook_token` de 64 caracteres (`secrets.token_hex`), que es a la vez el
  identificador y la credencial. Un token desconocido o de un cliente desactivado
  responde **401**. Ventajas frente al secreto único del `.env`: se rota desde
  el perfil del cliente (`/admin/negocios/{id}`) sin redeplegar, y la filtración de uno no compromete a los demás.
  La resolución se cachea 30 s (`clientes_whatsapp_repo.CACHE_TTL_SEGUNDOS`), así que
  una revocación tarda como mucho ese tiempo en surtir efecto.
- El servicio `whatsapp_webhook` **sí** está en la red `easypanel`: tiene que ser
  alcanzable desde internet porque es la URL que se registra en WasenderAPI. Lo que lo
  protege es el token, no la red.
- Sin el endpoint interno, el RAG se sincroniza igual por polling lazy
  (`RAG_SYNC_TTL_SECONDS`, 5 min): el token solo habilita la actualización instantánea.
- El webhook **ignora** (200, no 4xx) lo que no le interesa —grupos, difusiones, recibos
  de lectura— para no procesar eventos que le cobrarían al cliente un turno que nunca
  ocurrió, y para que WasenderAPI no reintente en bucle.
- **Los mensajes salientes son ambiguos y hay que desambiguarlos.** El bot y el dueño
  comparten el número de WhatsApp: un `message.sent` puede ser el eco de la propia
  respuesta del bot o el dueño escribiendo desde su teléfono, y solo lo segundo debe
  bloquear el chat 12 días. `outbound_registry` anota lo que el bot envía (por id del
  mensaje, con la huella del texto como respaldo) para poder distinguirlos; sin eso el
  bot se bloquearía a sí mismo en su primera respuesta.
- Tests: `tests/unit/test_webhook_app.py`.

### Dashboard: sesiones y roles

- **No arranca sin `SESSION_SECRET`.** Es deliberado: con un secreto vacío las cookies
  de sesión serían falsificables y el fallo pasaría inadvertido.
- Contraseñas con **bcrypt**; mínimo 10 caracteres. El administrador inicial se crea con
  una clave temporal que **debe cambiarse en el primer ingreso** (un middleware bloquea
  todo el panel hasta entonces, salvo la propia página de cambio y el logout).
- Cookie `HttpOnly` + `SameSite=Lax` + `Secure` (configurable para el local sin HTTPS).
  Las sesiones viven en tabla, así que se pueden revocar: cambiar la contraseña o
  desactivar a un usuario **cierra todas sus sesiones**.
- **CSRF** en toda escritura: token derivado del token de sesión por HMAC, sin estado
  extra que sincronizar.
- Freno a la fuerza bruta por **IP** (no por usuario: si fuera por usuario, cualquiera
  podría dejar fuera al administrador fallando adrede).
- El mensaje de credenciales inválidas es **el mismo** exista o no la cuenta, y se
  verifica un hash falso cuando el usuario no existe, para que el tiempo de respuesta no
  delate qué cuentas hay.
- **`security.requiere_admin` es la única puerta del rol administrador.** Todo lo que
  muestre costo real, logs, tarifas, periodos, incidencias o usuarios depende de ella;
  `tests/test_acceso.py` recorre la lista completa de rutas `/admin/*`.
- El nombre de columna que llega de un formulario (edición de ciudades) se valida contra
  una **lista blanca**; interpolarlo sin validar sería inyección SQL.
- La redirección posterior al login solo acepta rutas internas, para que `siguiente` no
  convierta el login en un trampolín hacia sitios externos.

### Comandos y disparadores estructurados

`/d`, `/block`, `grupo["…"]`, `add["…"]` y las keywords `tareas`/`transporte` se
comparan de forma exacta (excepción consciente a la regla "sin regex para NL").
Consideraciones:

- `/d` y `/block` los puede enviar cualquier usuario sobre **su propia** conversación
  (el `user_id` sale del chat, no del texto): un cliente no puede borrar ni bloquear a
  otro. `grupo[...]` solo actúa si el usuario tiene contexto de publicidad activo.
- El costo de abuso es bajo (borrar tu propio historial / bloquearte a ti mismo).

### Datos del cliente hacia el LLM

- El mensaje del usuario viaja como **dato JSON en el turno de usuario**, nunca
  concatenado dentro del system prompt: la instrucción y el dato quedan separados.
- Los prompts instruyen a no salir del dominio (escuela de manejo), a no inventar
  datos variables y a no revelar datos sensibles del RAG salvo petición explícita.
- **Inyección de prompt (riesgo residual aceptado):** un cliente puede intentar
  instruir al modelo desde su mensaje. Mitigaciones estructurales: las decisiones del
  LLM pasan por **validación estricta de salida** (`_validated_decision`: acciones,
  flujos y fuentes fuera de la lista blanca se normalizan), los mensajes curados del
  flujo salen de `mensajes.json` (el LLM no los genera), y el peor resultado
  alcanzable por inyección es una respuesta conversacional rara o un handoff — no hay
  herramientas destructivas expuestas al modelo.

### Trazabilidad sin fuga de secretos

`ToolCallLogger.sanitize` redacta `token`, `api_key`, `password`, `authorization`,
`headers`, `xc-token` antes de persistir cualquier evento en Postgres, y trunca
payloads. Si añades una herramienta nueva, registra sus llamadas con `ToolCallLogger`
(no con `print`) para heredar la redacción.

### Degradación segura sin LLM

Si no hay `OPENAI_API_KEY` o la llamada falla, la recepción **no adivina con reglas**:
degrada a una aclaración genérica (`_fallback_decision`) y el clasificador devuelve
`unknown`. Nunca se inventa una decisión de negocio por heurística.

## 3. Datos personales y retención

- Se almacena lo mínimo: `user_id` del canal, nombre de pila y el contenido de la
  conversación. No se piden ni almacenan documentos de identidad ni datos de pago.
- Retención de conversaciones: **20 días desde la última interacción** (ventana
  deslizante), borrado automático en Redis (TTL) y Postgres (purga diaria). Ver
  `README.md` §retención.
- Imágenes/documentos entrantes **no se procesan ni almacenan** (se responde que un
  asesor puede revisarlos).
- `/d` permite el borrado inmediato de la conversación propia.

## 4. Checklist de despliegue seguro (producción)

1. `.env` con valores reales: `POSTGRES_PASSWORD` fuerte, `SESSION_SECRET` aleatorio
   largo, `TELEGRAM_BOT_TOKEN`, `OPENAI_API_KEY`. Si se usa WhatsApp:
   `WASENDER_API_KEY`, y `PUBLIC_WEBHOOK_BASE_URL` con el dominio público del
   servicio de webhooks (el panel lo usa para armar la URL que se copia en
   WasenderAPI). Si se quiere reindexado inmediato del RAG: `INTERNAL_API_TOKEN`.
2. `COOKIE_SECURE=true` (el valor por defecto en la nube) y el dashboard **detrás de
   HTTPS**. Con HTTP plano la cookie de sesión viaja en claro.
3. Usar `docker-compose.yml` (nunca el `.local` en el servidor).
4. Verificar que el reverse proxy solo exponga lo necesario: el dominio del
   `dashboard` (8020) y, solo si WhatsApp está activo, el webhook (8010).
5. En el perfil del cliente (`/admin/negocios/{id}`), copiar la URL del cliente y pegarla en WasenderAPI con los
   eventos que esa misma página lista. Comprobar ahí mismo que la columna «Último
   evento» empieza a moverse: si sigue vacía, los eventos no están llegando.
6. Cambiar la contraseña temporal del administrador en el primer ingreso y borrar
   `ADMIN_BOOTSTRAP_PASSWORD` del `.env`.
7. `docker compose config -q` en ambos archivos tras cualquier cambio.
8. Tras migrar los datos, quitar del `.env` las variables `NOCODB_*`: ya no se usan.
