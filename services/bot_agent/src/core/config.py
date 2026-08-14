import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    TELEGRAM_BOT_TOKEN: str
    OPENAI_API_KEY: str = ""
    # --- Modelos, uno por tipo de tarea ------------------------------------
    #
    # No todas las llamadas necesitan la misma inteligencia, y pagar la del caso
    # difícil en el fácil se nota en la factura del cliente. Tres niveles:
    #
    # - SUPERVISOR: enruta y decide sobre el prompt grande (>2000 tokens). Es la
    #   llamada que más se beneficia del prompt caching y la que más daño hace
    #   si se equivoca. `gpt-5.6-terra` cuesta menos que `gpt-5.4` completo
    #   ($2.00 vs $2.50 por millón de entrada) y cachea igual al 90%.
    # - ESPECIALISTA: ya sabe de qué área habla; el trabajo es más acotado.
    # - AUXILIAR: decisiones chicas (recordatorio) y redacción del RAG.
    #
    # OJO con `temperature`: gpt-5.6 la RECHAZA (400 si se manda 0), mientras
    # que gpt-5.4-mini/nano la aceptan. Por eso el determinismo se pide por
    # modelo y no globalmente — ver `MODELOS_SIN_TEMPERATURE`.
    OPENAI_MODEL_SUPERVISOR: str = "gpt-5.6-terra"
    OPENAI_MODEL_ESPECIALISTA: str = "gpt-5.4-mini"
    OPENAI_MODEL_AUXILIAR: str = "gpt-5.4-nano"
    # Transcripción de notas de voz. Se cobra por MINUTO de audio, no por token.
    OPENAI_MODEL_TRANSCRIPCION: str = "gpt-4o-transcribe"
    # Más holgado que el de decisión: aquí hay que descifrar la media, bajarla y
    # subirla al modelo. Aun así acotado — corre dentro del candado de
    # conversación (120s) y no puede agotarlo.
    TRANSCRIPCION_TIMEOUT_SECONDS: float = 60.0
    # El negocio atiende en español. Decírselo al modelo evita que una nota
    # corta y con ruido de fondo se interprete como otro idioma.
    TRANSCRIPCION_IDIOMA: str = "es"
    # Compatibilidad: código y despliegues viejos que aún nombran OPENAI_MODEL.
    OPENAI_MODEL: str = "gpt-5.4-mini"
    # Se envía como reasoning_effort en las llamadas de decisión. Déjalo vacío
    # ("") si cambias a un modelo que no acepte ese parámetro. "none" da cero
    # tokens de razonamiento, que es lo que queremos: decidir, no rumiar.
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
    # Aquí va SOLO lo que es del proveedor y vale igual para todos: su dominio.
    # La API key de envío NO está aquí a propósito: es de cada negocio, vive en
    # `clientes_whatsapp.wasender_api_key` y se administra desde el panel. Una
    # clave global obligaría a editar el entorno y redesplegar por cada alta, y
    # no habría forma de tener dos números conectados a la vez.
    WASENDER_API_URL: str = "https://wasenderapi.com"
    # WasenderAPI limita el ritmo de envío según el plan (el de prueba: 1 mensaje
    # por minuto) y contesta 429 con `retry_after`. Se espera y se reintenta, en
    # vez de perder la respuesta del cliente.
    #
    # UN solo reintento, y la espera acotada a poco más de un minuto: el envío
    # ocurre dentro del candado por conversación (`_PROCESSING_LOCK_TTL_SECONDS`,
    # 120s en `celery_app.py`). Si la suma de esperas lo superara, el candado
    # expiraría a media respuesta y entraría un segundo turno del mismo usuario,
    # que es justo el cruce que ese candado existe para impedir. Si el plan no da
    # para el ritmo de la conversación, la solución es subir de plan.
    WASENDER_MAX_REINTENTOS_429: int = 1
    WASENDER_ESPERA_429_POR_DEFECTO: float = 15.0
    WASENDER_ESPERA_429_MAXIMA: float = 65.0
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
    # RESPALDO únicamente. Los precios de verdad viven en `precios_modelo` (uno
    # por modelo, editable desde el panel: ver migración 010). Estos valores solo
    # se usan si la base no responde o si un modelo quedó sin precio cargado —
    # registrar el consumo como gratis sería mucho peor que aproximarlo.
    OPENAI_PRICE_INPUT_USD_PER_1M: float = 0.75
    OPENAI_PRICE_CACHED_INPUT_USD_PER_1M: float = 0.075
    OPENAI_PRICE_OUTPUT_USD_PER_1M: float = 4.50
    OPENAI_PRICE_AUDIO_USD_PER_MINUTE: float = 0.006
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
