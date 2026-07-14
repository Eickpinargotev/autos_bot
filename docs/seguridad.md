# Seguridad: infraestructura, aplicación y datos

Postura de seguridad del bot y decisiones tomadas. Complementa
`docs/operacion_escala_y_trazabilidad.md` (integridad y concurrencia).

---

## 1. Infraestructura (Docker / red)

| Superficie | Estado | Detalle |
| ---------- | ------ | ------- |
| Puertos en producción (`docker-compose.yml`) | ✅ Cerrados | Ningún servicio publica puertos al host. Postgres, Redis y Qdrant solo son alcanzables por la red interna de Compose. NocoDB y el webhook usan `expose` (solo red interna + reverse proxy de EasyPanel). |
| Puertos en local (`docker-compose.local.yml`) | ⚠️ Abiertos a propósito | 5432/6379/6333/8080 publicados para desarrollo. **No usar este archivo en un servidor con IP pública.** |
| Secretos | ✅ Fuera del repo | Todo llega por `.env` (no versionado). Los compose solo interpolan variables. Nunca commitear `.env` ni pegar tokens en código, prompts o docs. |
| Imagen | ✅ Separación prod/dev | La etapa `prod` (default) no incluye pytest ni herramientas de test. |
| Redis | ✅ Acotado | `maxmemory` + `volatile-lru` evita OOM (ver doc de operación). Sin password: aceptable solo porque no publica puerto en producción; si algún día se expone, exigir `requirepass`. |

### Defaults de credenciales

`POSTGRES_PASSWORD` tiene un default de conveniencia (`mi_password_seguro`) en los
compose. En producción **siempre** debe definirse en `.env` un valor real; el default
existe solo para no romper el arranque local.

## 2. Aplicación

### Webhook de sincronización RAG (`/webhooks/nocodb-rag-chunks`)

Escribe en la base de conocimiento — un atacante con acceso podría **envenenar las
respuestas del RAG**. Por eso:

- Si `NOCODB_RAG_WEBHOOK_TOKEN` no está configurado, el endpoint responde **503
  (deshabilitado)** — nunca queda abierto por omisión.
- El token se compara con `hmac.compare_digest` (tiempo constante).
- Sin webhook, el RAG se sincroniza igual por polling lazy (`RAG_SYNC_TTL_SECONDS`,
  5 min): el token solo habilita la actualización instantánea.
- Tests: `tests/unit/test_webhook_app.py`.

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
`headers`, `xc-token` antes de persistir cualquier evento en NocoDB, y trunca
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
  deslizante), borrado automático en Redis (TTL) y NocoDB (purga diaria). Ver
  `README.md` §retención.
- Imágenes/documentos entrantes **no se procesan ni almacenan** (se responde que un
  asesor puede revisarlos).
- `/d` permite el borrado inmediato de la conversación propia.

## 4. Checklist de despliegue seguro (producción)

1. `.env` con valores reales: `POSTGRES_PASSWORD` fuerte, `NOCODB_TOKEN`,
   `NOCODB_RAG_WEBHOOK_TOKEN` aleatorio largo, `TELEGRAM_BOT_TOKEN`, `OPENAI_API_KEY`.
2. Usar `docker-compose.yml` (nunca el `.local` en el servidor).
3. Verificar que el reverse proxy solo exponga lo necesario (NocoDB si se usa el
   panel; el webhook 8010 solo si NocoDB lo llama desde fuera).
4. `docker compose config -q` en ambos archivos tras cualquier cambio.
5. Revisar que ninguna URL de NocoDB con token quede en logs públicos (el logger ya
   redacta, pero los `print` de errores de red pueden incluir URLs — no incluyen token
   porque va en header).
