"""Preguntas que el RAG no supo responder, para alimentar la base de conocimiento."""

from src.infrastructure.repositories.postgres_conn import ejecutar
from src.application.project_context import proyecto_actual

# Cuánto sobrevive una pregunta ya marcada como entendida, desde que se marcó.
# Es corto porque lo único que hace falta después del clic es poder deshacerlo
# si fue sin querer. Lo pendiente no caduca: sigue siendo un agujero en la base
# de conocimiento. Tiene que coincidir con `PREGUNTAS_RETENCION_HORAS` del
# dashboard, que es quien lo enseña.
PREGUNTAS_RETENCION_HORAS = 24


class UnansweredQuestionRepository:
    @staticmethod
    def create(question: str) -> bool:
        proyecto_id = proyecto_actual()
        if not proyecto_id or not (question or "").strip():
            return False
        try:
            ejecutar(
                "INSERT INTO preguntas_sin_respuesta (proyecto_id, pregunta) VALUES (%s, %s)",
                (proyecto_id, question),
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
