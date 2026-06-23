import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    TELEGRAM_BOT_TOKEN: str
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"
    EVAL_JUDGE_MODEL: str = "gpt-4o-mini"
    POSTGRES_URL: str
    REDIS_URL: str = "redis://redis:6379/0"
    QDRANT_URL: str = "http://qdrant:6333"
    MESSAGE_BUFFER_SECONDS: int = 15
    MAX_INFO_MESSAGES_PER_5_MIN: int = 2
    # Retención del historial de conversaciones: se conserva hasta N días desde la
    # ÚLTIMA interacción (ventana deslizante). Pasado ese plazo de inactividad el
    # estado en Redis expira solo y la purga programada borra el log en NocoDB.
    CONVERSATION_RETENTION_DAYS: int = 20
    NOCODB_INVITACIONES_URL: str = ""
    NOCODB_REPORTES_URL: str = ""
    NOCODB_CONVERSATIONS_URL: str = "http://nocodb:8080/api/v3/data/p4f9fruiaxeixtc/mjgl77lakf4yfu1/records?pageSize=25&viewId=vw9rg1umoeoa3fv5"
    NOCODB_CONVERSATION_SHOTS_URL: str = ""
    NOCODB_KEYWORD_REGISTROS_URL: str = "http://nocodb:8080/api/v3/data/pw9kkys1galzvcp/mkzxugucz0hw8jm/records?pageSize=25&viewId=vw9k9ye2nq4vkiyz"
    NOCODB_RAG_CHUNKS_URL: str = "http://nocodb:8080/api/v3/data/pw9kkys1galzvcp/mlk30zxjzj4lfd8/records?pageSize=25&viewId=vwg13qjbfyahfw61"
    NOCODB_UNANSWERED_QUESTIONS_URL: str = "http://nocodb:8080/api/v3/data/pw9kkys1galzvcp/m3s3ug74dil489y/records?pageSize=25&viewId=vwkfzfhfedwl5u2n"
    NOCODB_RAG_WEBHOOK_TOKEN: str = ""
    NOCODB_TOKEN: str = ""
    RAG_CONVERSATION_HISTORY_LIMIT: int = 5
    RAG_EMBEDDING_MODEL: str = "text-embedding-3-small"
    RAG_SYNC_TTL_SECONDS: int = 300
    GOOGLE_DRIVE_IMAGE_DOWNLOAD_URL_TEMPLATE: str = "https://drive.google.com/uc?export=download&id={image_id}"
    OUTBOUND_IMAGE_TIMEOUT_SECONDS: int = 30
    OUTBOUND_IMAGE_MAX_BYTES: int = 15000000
    PUB_DELAY_1_SEC: int = 7200
    PUB_DELAY_2_SEC: int = 72000
    PUB_DELAY_3_SEC: int = 82800
    MSG_DELAY_MIN: float = 1.0
    MSG_DELAY_MAX: float = 1.0

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
