"""Preguntas que el RAG no supo responder, para alimentar la base de conocimiento."""

import json

from src.infrastructure.repositories.postgres_conn import ejecutar, consultar
from src.infrastructure.repositories import conversation_audit_repository as audit
from src.infrastructure.logging.trace_sanitizer import MAX_PROVIDER_BYTES, sanitize
from src.application.project_context import proyecto_actual

# Cuánto sobrevive una pregunta ya marcada como entendida, desde que se marcó.
# Es corto porque lo único que hace falta después del clic es poder deshacerlo
# si fue sin querer. Lo pendiente no caduca: sigue siendo un agujero en la base
# de conocimiento. Tiene que coincidir con `PREGUNTAS_RETENCION_HORAS` del
# dashboard, que es quien lo enseña.
PREGUNTAS_RETENCION_HORAS = 24


class UnansweredQuestionRepository:
    @staticmethod
    def create(question: str, *, client_id: str = "", canal: str = "",
               current_message: str = "", fallback_message: str = "") -> bool:
        proyecto_id = proyecto_actual()
        if not proyecto_id or not (question or "").strip():
            return False
        try:
            correlation = audit.context().get("correlation_id") or ""
            eventos = []
            if correlation and client_id and canal:
                eventos = consultar(
                    "SELECT * FROM conversation_messages WHERE proyecto_id = %s "
                    "AND client_id = %s AND canal = %s AND correlation_id = %s ORDER BY id",
                    (proyecto_id, client_id, canal, correlation),
                )
            # Congelar las evidencias: editar chunks o purgar el chat después
            # no debe cambiar lo que vio el agente cuando falló esta consulta.
            diagnostico = {
                "consulta_cliente": current_message,
                "consulta_conocimiento": question,
                "respuesta_prevista": fallback_message,
                "eventos": [sanitize(dict(e), 2 * MAX_PROVIDER_BYTES) for e in eventos],
            }
            ejecutar(
                "INSERT INTO preguntas_sin_respuesta "
                "(proyecto_id, pregunta, client_id, canal, correlation_id, diagnostico) "
                "VALUES (%s, %s, %s, %s, %s, %s::jsonb)",
                (proyecto_id, question, client_id, canal, correlation,
                 json.dumps(diagnostico, ensure_ascii=False, default=str)),
            )
            return True
        except Exception as e:
            print(f"Error guardando pregunta sin respuesta en Postgres: {e}")
            return False

    @staticmethod
    def purge_answered(hours: int = PREGUNTAS_RETENCION_HORAS) -> int:
        """Borra las preguntas ya entendidas que cumplieron su plazo.

        Vive en el bot y no en el dashboard por lo mismo que la purga de los
        reportes: el reloj (Celery beat) es del bot. El dashboard no tiene ningún
        proceso periódico, y borrar al abrir la página dejaría la caducidad a
        merced de que alguien la mire.
        """
        return ejecutar(
            """
            DELETE FROM preguntas_sin_respuesta
            WHERE atendida
              AND atendida_en IS NOT NULL
              AND atendida_en < NOW() - (%s || ' hours')::interval
            """,
            (int(hours),),
        )
