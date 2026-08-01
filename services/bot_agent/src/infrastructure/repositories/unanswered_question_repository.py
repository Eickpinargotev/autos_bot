"""Preguntas que el RAG no supo responder, para alimentar la base de conocimiento."""

from src.infrastructure.repositories.postgres_conn import ejecutar


class UnansweredQuestionRepository:
    @staticmethod
    def create(question: str) -> bool:
        if not (question or "").strip():
            return False
        try:
            ejecutar("INSERT INTO preguntas_sin_respuesta (pregunta) VALUES (%s)", (question,))
            return True
        except Exception as e:
            print(f"Error guardando pregunta sin respuesta en Postgres: {e}")
            return False
