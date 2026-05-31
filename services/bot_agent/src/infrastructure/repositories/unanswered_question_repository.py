import httpx
from urllib.parse import urlparse

from src.core.config import settings


class UnansweredQuestionRepository:
    @staticmethod
    def create(question: str) -> bool:
        if not settings.NOCODB_UNANSWERED_QUESTIONS_URL:
            return False

        try:
            response = httpx.post(
                UnansweredQuestionRepository._insert_url(settings.NOCODB_UNANSWERED_QUESTIONS_URL),
                headers={"xc-token": settings.NOCODB_TOKEN},
                json={"fields": {"Question": question}},
                timeout=10.0,
            )
            response.raise_for_status()
            return True
        except Exception as e:
            print(f"Error guardando pregunta sin respuesta en NocoDB: {e}")
            return False

    @staticmethod
    def _insert_url(url: str) -> str:
        parsed = urlparse(url)
        separator = "&" if parsed.query else "?"
        return f"{url}{separator}insertAt=0"
