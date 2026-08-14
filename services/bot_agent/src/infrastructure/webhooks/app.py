import hmac
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query

from src.application.message_handler import MessageHandler
from src.application.rag_service import RagService
from src.core.config import settings
from src.domain.entities import Channel, MessageType
from src.infrastructure.channels import outbound_registry, wasender
from src.infrastructure.repositories import clientes_whatsapp_repo

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
    return _procesar_evento(payload, cliente_id=cliente["id"])


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


def _procesar_evento(payload: dict[str, Any], cliente_id: int | None = None) -> dict[str, str]:
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
            _vincular(cliente_id, numero)
            MessageHandler.handle_incoming_message(
                user_id=numero,
                content="",
                msg_type=MessageType.OTHER,
                channel=Channel.WHATSAPP,
                event_type="group_join",
            )
        return {"status": "group_join", "procesados": str(len(ingresos))}

    mensaje = wasender.mensaje_entrante(payload)
    if mensaje is None:
        # Recibos de lectura, estados de sesión, mensajes de grupo: nada que hacer.
        return {"status": "ignored"}

    _vincular(cliente_id, mensaje.user_id)

    # 2. Mensaje SALIENTE. Puede ser el eco de lo que mandó el propio bot o el
    #    dueño escribiendo desde su teléfono; solo el segundo caso es una
    #    intervención humana (y bloquea el chat 12 días).
    if mensaje.from_me:
        if outbound_registry.es_envio_del_bot(
            mensaje.user_id, mensaje_id=mensaje.message_id, texto=mensaje.text
        ):
            return {"status": "ignored"}

        MessageHandler.handle_incoming_message(
            user_id=mensaje.user_id,
            content=mensaje.text,
            msg_type=mensaje.message_type,
            user_name=mensaje.user_name,
            channel=Channel.WHATSAPP,
            from_me=True,
            message_id=mensaje.message_id,
        )
        return {"status": "intervencion_humana"}

    # 3. Mensaje del cliente: el camino normal.
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
    )
    return {"status": "ok"}


@app.post("/internal/rag/sync/{chunk_id}")
def sincronizar_chunk(chunk_id: int, token: str = Query(default="")):
    """Re-indexa un chunk que el dashboard acaba de editar.

    Reemplaza al antiguo webhook de NocoDB. Sin token configurado responde 503,
    porque escribe en la base de conocimiento del RAG. Aunque no se use, el RAG
    igual se actualiza solo por la sincronización perezosa
    (RAG_SYNC_TTL_SECONDS); esto únicamente la hace instantánea.
    """
    _exigir_token_interno(token)
    return {"status": "ok", **rag_service.sync_chunk_id(chunk_id)}


@app.post("/internal/conversaciones/{canal}/{client_id}/olvidar")
def olvidar_conversacion(canal: str, client_id: str, token: str = Query(default="")):
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

    return {"status": "ok", **olvidar(canal_valido, client_id)}


def _exigir_token_interno(token: str) -> None:
    if not settings.INTERNAL_API_TOKEN:
        raise HTTPException(status_code=503, detail="Endpoint disabled: set INTERNAL_API_TOKEN")
    if not hmac.compare_digest(token, settings.INTERNAL_API_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid internal token")
