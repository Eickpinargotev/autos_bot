import hmac
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query

from src.application.message_handler import MessageHandler
from src.application import human_intervention
from src.application.project_context import ambito_proyecto
from src.application.rag_service import RagService
from src.core.config import settings
from src.domain.entities import Channel, MessageType
from src.infrastructure.channels import inbound_registry, outbound_registry, wasender
from src.infrastructure.channels.senders import ChannelSenderRegistry
from src.infrastructure.repositories import clientes_whatsapp_repo
from src.infrastructure.repositories.conversation_log_repository import ConversationLogRepository
from src.infrastructure.logging.trace_sanitizer import MAX_PROVIDER_BYTES, sanitize

app = FastAPI(title="Bot Agent Webhooks")
rag_service = RagService()


@app.get("/health")
def health():
    return {"status": "ok"}


# --- WhatsApp (WasenderAPI) --------------------------------------------------
#
# Hay dos formas de entrar, con el MISMO procesamiento detrás:
#
# - `/webhooks/wasender/{token}`: una URL por cliente (negocio). El token sale
#   de `clientes_whatsapp` y se revoca desde el panel. Es la forma de conectar
#   negocios nuevos sin tocar el .env ni redeplegar.
# - `/webhooks/wasender`: la URL única de siempre, autenticada con
#   WASENDER_WEBHOOK_SECRET. Se mantiene para no romper una instalación ya
#   conectada.
#
# En ambos casos, sin credencial válida el endpoint no procesa nada: hace que
# el bot conteste y gaste tokens, así que nunca se deja abierto.


@app.post("/webhooks/wasender/{token}")
async def wasender_webhook_cliente(
    token: str,
    payload: dict[str, Any],
    x_webhook_signature: str = Header(default=""),
):
    """Eventos de WhatsApp del negocio dueño de ese token.

    Dos comprobaciones que responden a preguntas distintas:

    1. El **token de la ruta** dice a QUÉ negocio va dirigido el evento. Es
       obligatorio: sin él no se sabría de quién es la conversación.
    2. El **secreto de WasenderAPI** (cabecera `X-Webhook-Signature`), si el
       negocio lo tiene configurado, demuestra que el evento lo mandó
       WasenderAPI y no alguien que consiguió la URL. Es opcional porque no
       todas las sesiones lo tienen, pero en cuanto se configura se exige: que
       una credencial puesta se pueda saltar omitiéndola sería peor que no
       tenerla.
    """
    cliente = clientes_whatsapp_repo.por_token(token)
    if not cliente:
        raise HTTPException(status_code=401, detail="Webhook token desconocido o inactivo")

    secreto = str(cliente.get("wasender_webhook_secret") or "")
    if secreto and not hmac.compare_digest(x_webhook_signature, secreto):
        raise HTTPException(status_code=401, detail="Firma del webhook inválida")

    clientes_whatsapp_repo.registrar_evento(cliente["id"], wasender.nombre_evento(payload))
    with ambito_proyecto(cliente["id"]):
        return _procesar_evento(
            payload,
            cliente_id=cliente["id"],
            wasender_api_key=str(cliente.get("wasender_api_key") or ""),
        )


@app.post("/webhooks/wasender")
async def wasender_webhook(
    payload: dict[str, Any],
    x_webhook_signature: str = Header(default=""),
    token: str = Query(default=""),
):
    """Igual que la anterior, con el secreto global del entorno.

    Sin secreto configurado el webhook queda DESHABILITADO (503), no abierto.
    El secreto se acepta por cabecera o por query porque la documentación de
    WasenderAPI no precisa cuál usa; ambas se comparan en tiempo constante.
    """
    if not settings.WASENDER_WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="Webhook disabled: set WASENDER_WEBHOOK_SECRET")

    enviado = x_webhook_signature or token
    if not hmac.compare_digest(enviado, settings.WASENDER_WEBHOOK_SECRET):
        raise HTTPException(status_code=401, detail="Invalid webhook token")

    return _procesar_evento(payload)


def _vincular(cliente_id: int | None, user_id: str) -> None:
    """Deja anotado el negocio de esa conversación, si el evento trae uno."""
    if cliente_id and user_id:
        clientes_whatsapp_repo.vincular_conversacion(
            cliente_id, Channel.WHATSAPP.value, user_id
        )


def _trazar_webhook(user_id: str, payload: dict[str, Any], status: str) -> None:
    """Guarda el evento original sanitizado como traza invisible del chat."""
    if not user_id:
        return
    ConversationLogRepository.log_tool_event(
        client_id=user_id,
        canal=Channel.WHATSAPP,
        tool_name="wasender.webhook",
        status=status,
        input_data=sanitize(payload, MAX_PROVIDER_BYTES),
        output_data={"branch": status},
        text=f"Webhook WasenderAPI: {status}",
        event_type="provider_webhook",
    )


def _procesar_evento(
    payload: dict[str, Any],
    cliente_id: int | None = None,
    wasender_api_key: str = "",
) -> dict[str, str]:
    """Traduce un evento de WasenderAPI a una acción del bot.

    Nunca responde con error por un evento que no interesa: un 4xx haría que
    WasenderAPI reintente en bucle. Lo que no se procesa se acepta y se ignora.

    `cliente_id` es el negocio dueño de la URL por la que entró el evento. Se
    anota en cada mensaje porque es el ÚNICO punto del recorrido donde se sabe:
    la respuesta se envía después, desde el worker, que solo recibe canal y
    número. De esa anotación sale luego la credencial de salida.
    """
    # 1. Alguien entró al grupo del curso. Cierra el flujo de publicidad: se
    #    cancelan los recordatorios pendientes y el chat queda bloqueado.
    ingresos = wasender.ingresos_a_grupo(payload)
    if ingresos:
        for numero in ingresos:
            # En sesiones con addressingMode=LID el alta puede traer el LID y
            # no el teléfono. Se traduce antes de vincular y bloquear para que
            # caiga en la misma conversación que sus mensajes individuales.
            numero = wasender.numero_para_envio(numero, wasender_api_key)
            _vincular(cliente_id, numero)
            MessageHandler.handle_incoming_message(
                user_id=numero,
                content="",
                msg_type=MessageType.OTHER,
                channel=Channel.WHATSAPP,
                event_type="group_join",
                proyecto_id=int(cliente_id or 0),
            )
            _trazar_webhook(numero, payload, "group_join")
        return {"status": "group_join", "procesados": str(len(ingresos))}

    # 2. Una salida no cambia el flujo ni envía nada, pero debe quedar visible
    #    en la conversación. Antes `remove` caía hasta `mensaje_entrante` y se
    #    devolvía como ignorado, por lo que parecía que el webhook no funcionó.
    salidas = wasender.salidas_de_grupo(payload)
    if salidas:
        for numero in salidas:
            numero = wasender.numero_para_envio(numero, wasender_api_key)
            _vincular(cliente_id, numero)
            MessageHandler.handle_incoming_message(
                user_id=numero,
                content="",
                msg_type=MessageType.OTHER,
                channel=Channel.WHATSAPP,
                event_type="group_leave",
                proyecto_id=int(cliente_id or 0),
            )
            _trazar_webhook(numero, payload, "group_leave")
        return {"status": "group_leave", "procesados": str(len(salidas))}

    mensaje = wasender.mensaje_entrante(payload)
    if mensaje is None:
        # Recibos de lectura, estados de sesión, mensajes de grupo: nada que hacer.
        return {"status": "ignored"}

    if mensaje.from_me:
        # En un mensaje saliente `remoteJid` identifica al DESTINATARIO, pero
        # puede venir como LID. `cleanedSenderPn` no sirve aquí: es el número
        # del negocio que envió, no el cliente que recibe. Se resuelve el LID
        # con la libreta de la sesión para que la intervención del dueño quede
        # en la misma conversación (y bloquee el mismo teléfono) que el inbound.
        api_key = wasender_api_key or clientes_whatsapp_repo.api_key_de_envio(
            Channel.WHATSAPP.value, mensaje.user_id
        )
        mensaje.user_id = wasender.numero_para_envio(mensaje.user_id, api_key)

    # Los webhooks tienen entrega al menos una vez: el proveedor puede repetir
    # exactamente el mismo message_id, incluso en dos peticiones simultáneas.
    # Se reclama antes de cualquier efecto para que un comando inmediato como
    # `/d` no conteste dos veces y un texto normal no entre dos veces al buffer.
    if not inbound_registry.reclamar(mensaje.user_id, mensaje.message_id):
        _vincular(cliente_id, mensaje.user_id)
        _trazar_webhook(mensaje.user_id, payload, "duplicate")
        return {"status": "duplicate"}

    _vincular(cliente_id, mensaje.user_id)

    # 3. Mensaje SALIENTE. Puede ser el eco de lo que mandó el propio bot o el
    #    dueño escribiendo desde su teléfono; solo el segundo caso es una
    #    intervención humana (y bloquea el chat 12 días).
    if mensaje.from_me:
        if outbound_registry.es_envio_del_bot(
            mensaje.user_id, mensaje_id=mensaje.message_id, texto=mensaje.text
        ):
            _trazar_webhook(mensaje.user_id, payload, "bot_echo")
            return {"status": "ignored"}

        MessageHandler.handle_incoming_message(
            user_id=mensaje.user_id,
            content=mensaje.text,
            msg_type=mensaje.message_type,
            user_name=mensaje.user_name,
            channel=Channel.WHATSAPP,
            from_me=True,
            message_id=mensaje.message_id,
            proyecto_id=int(cliente_id or 0),
        )
        _trazar_webhook(mensaje.user_id, payload, "human_intervention")
        return {"status": "intervencion_humana"}

    # 4. Mensaje del cliente: el camino normal.
    MessageHandler.handle_incoming_message(
        user_id=mensaje.user_id,
        content=mensaje.text,
        msg_type=mensaje.message_type,
        user_name=mensaje.user_name,
        channel=Channel.WHATSAPP,
        message_id=mensaje.message_id,
        # El evento completo, que la nota de voz necesita para descifrar su
        # media. No se guarda: solo viaja hasta la tarea de transcripción.
        raw_payload=payload,
        # El anuncio citado viaja separado del saludo genérico del cliente. El
        # orquestador solo lo aceptará si contiene una clave real del catálogo.
        advertisement_text=mensaje.advertisement_text,
        proyecto_id=int(cliente_id or 0),
    )
    _trazar_webhook(mensaje.user_id, payload, "accepted")
    return {"status": "ok"}


@app.post("/internal/proyectos/{proyecto_id}/rag/sync/{chunk_id}")
def sincronizar_chunk(proyecto_id: int, chunk_id: int, token: str = Query(default="")):
    """Re-indexa un chunk que el dashboard acaba de editar.

    Reemplaza al antiguo webhook de NocoDB. Sin token configurado responde 503,
    porque escribe en la base de conocimiento del RAG. Aunque no se use, el RAG
    igual se actualiza solo por la sincronización perezosa
    (RAG_SYNC_TTL_SECONDS); esto únicamente la hace instantánea.
    """
    _exigir_token_interno(token)
    with ambito_proyecto(proyecto_id):
        return {"status": "ok", **rag_service.sync_chunk_id(chunk_id)}


@app.post("/internal/proyectos/{proyecto_id}/conversaciones/{canal}/{client_id}/olvidar")
def olvidar_conversacion(
    proyecto_id: int, canal: str, client_id: str, token: str = Query(default="")
):
    """Suelta lo que el bot recuerda de una conversación que el panel borró.

    El historial durable lo borra el dashboard (esas tablas son suyas); lo que
    solo el bot puede tocar es Redis y las tareas agendadas, porque el esquema
    de claves y los ids de tarea son de aquí. Sin esto, borrar una conversación
    dejaría al bot contestando el siguiente mensaje con el hilo entero en
    memoria, y llegarían recordatorios de un chat que ya no existe.

    Mismo guardarraíl que el reindexado: sin `INTERNAL_API_TOKEN` responde 503
    en vez de quedar abierto — es un endpoint que borra estado.
    """
    _exigir_token_interno(token)
    try:
        canal_valido = Channel(canal)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Canal desconocido: {canal}")

    from src.application.conversation_reset import olvidar_conversacion as olvidar

    with ambito_proyecto(proyecto_id):
        return {"status": "ok", **olvidar(canal_valido, client_id)}


@app.post("/internal/proyectos/{proyecto_id}/conversaciones/{canal}/{client_id}/responder")
def responder_como_dueno(
    proyecto_id: int,
    canal: str,
    client_id: str,
    payload: dict[str, Any],
    token: str = Query(default=""),
):
    """Envía desde el número del negocio y transfiere el chat al asesor humano."""
    _exigir_token_interno(token)
    try:
        canal_valido = Channel(canal)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Canal desconocido: {canal}")
    if canal_valido != Channel.WHATSAPP:
        raise HTTPException(status_code=400, detail="Solo se responde WhatsApp desde el panel")

    texto = str(payload.get("texto") or "").strip()
    if not texto:
        raise HTTPException(status_code=400, detail="El mensaje no puede estar vacío")
    if len(texto) > 4000:
        raise HTTPException(status_code=400, detail="El mensaje supera 4000 caracteres")

    with ambito_proyecto(proyecto_id):
        if not clientes_whatsapp_repo.conversacion_pertenece(
            proyecto_id, canal_valido.value, client_id
        ):
            raise HTTPException(status_code=404, detail="La conversación no pertenece al proyecto")
        try:
            ChannelSenderRegistry.send(
                canal_valido, client_id, texto, log_conversation=False
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"No se pudo enviar: {exc}") from exc
        human_intervention.registrar(canal_valido, client_id, texto)
    return {"status": "ok"}


def _exigir_token_interno(token: str) -> None:
    if not settings.INTERNAL_API_TOKEN:
        raise HTTPException(status_code=503, detail="Endpoint disabled: set INTERNAL_API_TOKEN")
    if not hmac.compare_digest(token, settings.INTERNAL_API_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid internal token")
