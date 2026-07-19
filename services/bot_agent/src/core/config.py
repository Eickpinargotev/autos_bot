import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    TELEGRAM_BOT_TOKEN: str
    OPENAI_API_KEY: str = ""
    # gpt-5.4-mini: modelo de decisión del agente único. temperature=0 en
    # decisiones. Se usa SIN razonamiento (reasoning_effort="none", que además
    # es el default del modelo): respuestas directas, sin tokens de reasoning.
    OPENAI_MODEL: str = "gpt-5.4-mini"
    # Se envía como reasoning_effort en las llamadas de decisión. Déjalo vacío
    # ("") si cambias OPENAI_MODEL a un modelo que no acepte ese parámetro.
    OPENAI_REASONING_EFFORT: str = "none"
    EVAL_JUDGE_MODEL: str = "gpt-4o-mini"
    # Timeout/reintentos de las llamadas al LLM. El default del SDK es 600s con
    # 2 reintentos: una llamada colgada retendría un hilo del worker ~30 min y
    # con varias así se atasca la cola entera de clientes.
    OPENAI_TIMEOUT_SECONDS: float = 30.0
    OPENAI_MAX_RETRIES: int = 1
    POSTGRES_URL: str
    REDIS_URL: str = "redis://redis:6379/0"
    QDRANT_URL: str = "http://qdrant:6333"
    MESSAGE_BUFFER_SECONDS: int = 15
    MAX_INFO_MESSAGES_PER_5_MIN: int = 2
    # Retención del historial de conversaciones: se conserva hasta N días desde la
    # ÚLTIMA interacción (ventana deslizante). Pasado ese plazo de inactividad el
    # estado en Redis expira solo y la purga programada borra el log en NocoDB.
    CONVERSATION_RETENTION_DAYS: int = 20
    # Tope de mensajes conservados por conversación en el log de NocoDB
    # (json_mensajes). Sin tope, cada mensaje re-escribe un JSON cada vez más
    # grande (crecimiento O(n²) en tráfico) hasta volver lento el guardado.
    CONVERSATION_LOG_MAX_MESSAGES: int = 400
    NOCODB_INVITACIONES_URL: str = ""
    NOCODB_REPORTES_URL: str = ""
    NOCODB_CONVERSATIONS_URL: str = "http://nocodb:8080/api/v3/data/p4f9fruiaxeixtc/mjgl77lakf4yfu1/records?pageSize=25&viewId=vw9rg1umoeoa3fv5"
    NOCODB_CONVERSATION_SHOTS_URL: str = ""
    NOCODB_KEYWORD_REGISTROS_URL: str = "http://nocodb:8080/api/v3/data/pw9kkys1galzvcp/mkzxugucz0hw8jm/records?pageSize=25&viewId=vw9k9ye2nq4vkiyz"
    NOCODB_RAG_CHUNKS_URL: str = "http://nocodb:8080/api/v3/data/pw9kkys1galzvcp/mlk30zxjzj4lfd8/records?pageSize=25&viewId=vwg13qjbfyahfw61"
    NOCODB_UNANSWERED_QUESTIONS_URL: str = "http://nocodb:8080/api/v3/data/pw9kkys1galzvcp/m3s3ug74dil489y/records?pageSize=25&viewId=vwkfzfhfedwl5u2n"
    NOCODB_RAG_WEBHOOK_TOKEN: str = ""
    NOCODB_TOKEN: str = ""
    # Seguimiento por cliente y resumen mensual (base LOGs_Autos_Mensajes).
    NOCODB_SEGUIMIENTO_CLIENTES_URL: str = "http://nocodb:8080/api/v3/data/p4f9fruiaxeixtc/m0z6xtcemlnwtu7/records?pageSize=25"
    NOCODB_RESUMEN_MENSUAL_URL: str = "http://nocodb:8080/api/v3/data/p4f9fruiaxeixtc/mk51gipqv4xblxk/records?pageSize=25"
    # Precios de gpt-5.4-mini (USD por millón de tokens). Si cambias
    # OPENAI_MODEL, actualiza estos tres valores en el mismo despliegue: el
    # costo acumulado se calcula con ellos.
    OPENAI_PRICE_INPUT_USD_PER_1M: float = 0.75
    OPENAI_PRICE_CACHED_INPUT_USD_PER_1M: float = 0.075
    OPENAI_PRICE_OUTPUT_USD_PER_1M: float = 4.50
    # Una "conversación" dura hasta 24h desde el primer mensaje del cliente;
    # pasado ese plazo, el siguiente mensaje cuenta como conversación nueva.
    SEGUIMIENTO_VENTANA_CONVERSACION_HORAS: int = 24
    # Tope del historial simplificado por cliente (mismo motivo que
    # CONVERSATION_LOG_MAX_MESSAGES: el JSON se re-escribe completo cada vez).
    SEGUIMIENTO_HISTORIAL_MAX_MENSAJES: int = 400
    RAG_CONVERSATION_HISTORY_LIMIT: int = 5
    # Historial que ve el agente único por turno. Los fragmentos se guardan
    # como etiquetas [[frag:ID]] (no texto completo), así que cabe más contexto
    # sin inflar Redis ni el prompt.
    AGENT_HISTORY_LIMIT: int = 12
    # Recordatorios inteligentes: tras responder, si el cliente no contesta en
    # FOLLOWUP_FIRST_DELAY_SECONDS se evalúa un recordatorio con LLM; los
    # siguientes niveles esperan FOLLOWUP_NEXT_DELAY_SECONDS. Nunca se envían
    # más de FOLLOWUP_MAX_REMINDERS sin respuesta del cliente (anti-bucle).
    FOLLOWUP_FIRST_DELAY_SECONDS: int = 40
    FOLLOWUP_NEXT_DELAY_SECONDS: int = 7200
    FOLLOWUP_MAX_REMINDERS: int = 2
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
