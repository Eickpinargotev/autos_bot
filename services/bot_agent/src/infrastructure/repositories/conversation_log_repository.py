"""Log durable de conversaciones en Postgres.

Antes vivía en NocoDB como un único JSON por conversación (`json_mensajes`), que
se reescribía entero en cada mensaje: el costo de guardar crecía con el largo
del chat y por eso existía un tope artificial de mensajes por conversación.

Ahora cada mensaje es una fila de `conversation_messages`: guardar es un INSERT
de costo constante, la purga por retención es un DELETE por fecha, y el visor
de logs del dashboard pagina por índice. La API pública de la clase no cambió.
"""

import json
from datetime import datetime, timedelta
from typing import Any

from src.domain.entities import Channel, MessageType
from src.infrastructure.repositories.postgres_conn import consultar, ejecutar
from src.application.project_context import proyecto_actual


class ConversationLogRepository:
    @staticmethod
    def log_inbound(
        *,
        client_id: str,
        canal: Channel | str,
        sender_name: str,
        message_type: MessageType | str,
        text: str = "",
        quoted_text: str = "",
        event_type: str = "message",
    ) -> bool:
        if (event_type or "message") == "message":
            ConversationLogRepository._track_seguimiento(
                client_id=client_id,
                canal=canal,
                autor="cliente",
                texto=text or f"[{ConversationLogRepository._message_type_value(message_type)}]",
                nombre=sender_name or "",
            )
        return ConversationLogRepository.append_message(
            client_id=client_id,
            canal=canal,
            message={
                "direction": "inbound",
                "author": "cliente",
                "sender_id": client_id,
                "sender_name": sender_name or "Desconocido",
                "message_type": ConversationLogRepository._message_type_value(message_type),
                "text": text or "",
                "quoted_text": quoted_text or "",
                "event_type": event_type or "message",
            },
        )

    @staticmethod
    def log_outbound(
        *,
        client_id: str,
        canal: Channel | str,
        text: str,
        event_type: str = "bot_reply",
    ) -> bool:
        ConversationLogRepository._track_seguimiento(
            client_id=client_id, canal=canal, autor="bot", texto=text or ""
        )
        return ConversationLogRepository.append_message(
            client_id=client_id,
            canal=canal,
            message={
                "direction": "outbound",
                "author": "ia",
                "sender_id": "bot",
                "sender_name": "IA",
                "message_type": "text",
                "text": text or "",
                "event_type": event_type or "bot_reply",
            },
        )

    @staticmethod
    def log_tool_event(
        *,
        client_id: str,
        canal: Channel | str,
        tool_name: str,
        status: str,
        input_data: dict[str, Any] | list[Any] | str | None = None,
        output_data: dict[str, Any] | list[Any] | str | None = None,
        error: str = "",
        text: str = "",
        duration_ms: int | None = None,
        event_type: str = "tool_call",
    ) -> bool:
        return ConversationLogRepository.append_message(
            client_id=client_id,
            canal=canal,
            message={
                "direction": "internal",
                "author": "tool",
                "sender_id": tool_name,
                "sender_name": ConversationLogRepository._tool_sender_name(tool_name),
                "message_type": "tool_event",
                "text": text or f"{tool_name}: {status}",
                "event_type": event_type or "tool_call",
                "tool_name": tool_name,
                "status": status,
                "input": input_data if input_data is not None else {},
                "output": output_data if output_data is not None else {},
                "error": error or "",
                "duration_ms": duration_ms,
            },
        )

    @staticmethod
    def append_message(client_id: str, canal: Channel | str, message: dict[str, Any]) -> bool:
        """Guarda un mensaje del chat o un evento de herramienta.

        Nunca propaga excepciones: perder una línea de log no puede tumbar la
        atención de un cliente (mismo criterio que tenía la versión NocoDB).
        """
        canal_value = ConversationLogRepository._channel_value(canal)
        proyecto_id = proyecto_actual()
        if not proyecto_id:
            return False
        try:
            ejecutar(
                """
                INSERT INTO conversation_messages (
                    proyecto_id, client_id, canal, direction, author, sender_id, sender_name,
                    message_type, text, event_type, tool_name, status,
                    quoted_text, entrada, salida, error, duration_ms
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    proyecto_id, str(client_id),
                    canal_value,
                    message.get("direction") or "",
                    message.get("author") or "",
                    str(message.get("sender_id") or "")[:120],
                    str(message.get("sender_name") or "")[:200],
                    message.get("message_type") or "text",
                    message.get("text") or "",
                    message.get("event_type") or "message",
                    str(message.get("tool_name") or "")[:120],
                    str(message.get("status") or "")[:40],
                    message.get("quoted_text") or "",
                    ConversationLogRepository._json_o_nulo(message.get("input")),
                    ConversationLogRepository._json_o_nulo(message.get("output")),
                    message.get("error") or "",
                    message.get("duration_ms"),
                ),
            )
            return True
        except Exception as e:
            print(f"Error guardando mensaje de conversacion en Postgres: {e}")
            return False

    @staticmethod
    def obtener_conversacion(client_id: str, canal: Channel | str, limite: int = 400) -> list[dict[str, Any]]:
        """Últimos mensajes de una conversación, del más antiguo al más reciente.

        La usa el visor de logs del dashboard a través de la base; el bot no
        depende de ella para razonar (su contexto vive en Redis).
        """
        canal_value = ConversationLogRepository._channel_value(canal)
        proyecto_id = proyecto_actual()
        if not proyecto_id:
            return []
        filas = consultar(
            """
            SELECT * FROM (
                SELECT * FROM conversation_messages
                WHERE proyecto_id = %s AND client_id = %s AND canal = %s
                ORDER BY created_at DESC, id DESC
                LIMIT %s
            ) AS ultimos
            ORDER BY created_at ASC, id ASC
            """,
            (proyecto_id, str(client_id), canal_value, int(limite)),
        )
        return filas

    @staticmethod
    def delete_conversation(client_id: str, canal: Channel | str) -> bool:
        canal_value = ConversationLogRepository._channel_value(canal)
        proyecto_id = proyecto_actual()
        if not proyecto_id:
            return False
        try:
            ejecutar(
                "DELETE FROM conversation_messages WHERE proyecto_id = %s AND client_id = %s AND canal = %s",
                (proyecto_id, str(client_id), canal_value),
            )
            return True
        except Exception as e:
            print(f"Error eliminando conversacion en Postgres: {e}")
            return False

    @staticmethod
    def purge_older_than(days: int, now: datetime | None = None) -> int:
        """Borra los mensajes cuya última actividad supere `days` días.

        La retención es una ventana deslizante POR CONVERSACIÓN: si un cliente
        escribió ayer, no se borra nada suyo aunque tenga mensajes de hace un
        mes. Por eso el corte se calcula sobre el último mensaje de cada
        (client_id, canal), no mensaje a mensaje.

        Devuelve cuántas conversaciones se eliminaron (no cuántas filas).
        """
        if days <= 0:
            return 0

        corte = (now or datetime.now().astimezone()) - timedelta(days=days)
        try:
            filas = consultar(
                """
                WITH vencidas AS (
                    SELECT proyecto_id, client_id, canal
                    FROM conversation_messages
                    GROUP BY proyecto_id, client_id, canal
                    HAVING MAX(created_at) < %s
                ), borradas AS (
                    DELETE FROM conversation_messages m
                    USING vencidas v
                    WHERE m.proyecto_id = v.proyecto_id AND m.client_id = v.client_id AND m.canal = v.canal
                    RETURNING m.proyecto_id, m.client_id, m.canal
                )
                SELECT COUNT(DISTINCT (proyecto_id, client_id, canal)) AS conversaciones FROM borradas
                """,
                (corte,),
            )
            return int(filas[0]["conversaciones"]) if filas else 0
        except Exception as e:
            print(f"Error purgando conversaciones vencidas en Postgres: {e}")
            return 0

    # --- Helpers -------------------------------------------------------------

    @staticmethod
    def _track_seguimiento(*, client_id: str, canal: Channel | str, autor: str, texto: str, nombre: str = "") -> None:
        """Alimenta el seguimiento por cliente/resumen mensual sin afectar el log.

        Import perezoso para no crear un ciclo (seguimiento_repository importa
        este módulo). Cualquier fallo del seguimiento no debe romper el logueo.
        """
        try:
            from src.application import seguimiento_service

            seguimiento_service.registrar_mensaje(
                client_id=str(client_id), canal=canal, autor=autor, texto=texto, nombre=nombre
            )
        except Exception as e:
            print(f"Error en seguimiento de mensaje: {e}")

    @staticmethod
    def _json_o_nulo(valor: Any) -> str | None:
        """Serializa a JSON para una columna jsonb; los vacíos quedan en NULL."""
        if valor is None or valor == {} or valor == []:
            return None
        try:
            return json.dumps(valor, ensure_ascii=False, default=str)
        except Exception:
            return json.dumps({"valor": str(valor)}, ensure_ascii=False)

    @staticmethod
    def _channel_value(canal: Channel | str) -> str:
        return canal.value if isinstance(canal, Channel) else str(canal)

    @staticmethod
    def _message_type_value(message_type: MessageType | str) -> str:
        return message_type.value if isinstance(message_type, MessageType) else str(message_type)

    @staticmethod
    def _tool_sender_name(tool_name: str) -> str:
        prefix = str(tool_name or "tool").split(".", 1)[0]
        names = {
            "rag": "RAG",
            "reception": "Recepción",
            "classifier": "Clasificador",
            "publicidad": "Publicidad",
            "unanswered_question": "Preguntas sin respuesta",
            "celery": "Celery",
        }
        return names.get(prefix, prefix.replace("_", " ").title())
