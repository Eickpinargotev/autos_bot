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
    # estado en Redis expira solo y la purga programada borra el log en Postgres.
    CONVERSATION_RETENTION_DAYS: int = 20
    # --- WhatsApp vía WasenderAPI ------------------------------------------
    # Sin credenciales el canal queda INACTIVO (el sender falla con un mensaje
    # claro y el webhook responde 503), nunca abierto a medias.
    WASENDER_API_URL: str = "https://wasenderapi.com"
    WASENDER_API_KEY: str = ""
    # Secreto que firma/acompaña los webhooks entrantes de WasenderAPI.
    WASENDER_WEBHOOK_SECRET: str = ""
    WASENDER_TIMEOUT_SECONDS: float = 20.0
    # Token compartido con el dashboard para los endpoints internos del bot
    # (p. ej. re-indexar un contenido de la base de conocimiento recién editado).
    # Vacío = deshabilitados.
    INTERNAL_API_TOKEN: str = ""
    # Espera aleatoria entre las partes de un mensaje en cadena. Un envío a
    # ritmo constante es la firma más obvia de un bot.
    ENVIO_DELAY_MIN_SEGUNDOS: float = 1.0
    ENVIO_DELAY_MAX_SEGUNDOS: float = 6.0
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
