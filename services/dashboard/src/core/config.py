from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # --- Base de datos -----------------------------------------------------
    POSTGRES_URL: str
    # Tope de conexiones del pool. Postgres acepta 100 por defecto y el bot,
    # el worker y el webhook ya consumen algunas: dejar margen.
    DB_MAX_CONNECTIONS: int = 10

    # --- Presentación ------------------------------------------------------
    # Todas las fechas y horas del panel se muestran en esta zona horaria, no
    # en la del servidor: quien lee los chats está en Costa Rica y necesita
    # saber a qué hora local pasó cada cosa. En la base todo sigue guardándose
    # en TIMESTAMPTZ (UTC); esto es solo cómo se muestra.
    ZONA_HORARIA: str = "America/Costa_Rica"

    # --- Sesiones ----------------------------------------------------------
    # Firma las cookies de sesión. DEBE definirse en el .env de producción:
    # con el valor por defecto el arranque falla a propósito (ver main.py).
    SESSION_SECRET: str = ""
    SESSION_TTL_HOURS: int = 12
    SESSION_COOKIE_NAME: str = "dash_sesion"
    # En producción el dashboard va detrás de HTTPS; en local, no. Con
    # COOKIE_SECURE=true el navegador no manda la cookie por HTTP plano.
    COOKIE_SECURE: bool = True

    # --- Cuenta de administrador -------------------------------------------
    # El `.env` es la fuente de verdad de esta cuenta: al arrancar, el
    # dashboard la crea si no existe y le aplica la contraseña de aquí si la
    # cambiaste en el archivo. Así no depende de un mensaje en la consola que
    # se pierde si no lo copias a tiempo.
    #
    # Si más adelante cambias la contraseña desde el panel (o recuperándola por
    # Telegram), el arranque NO la pisa: solo vuelve a aplicarse cuando el valor
    # del `.env` cambia. Ver `usuarios.sincronizar_admin`.
    ADMIN_USER: str = "admin"
    ADMIN_PASSWORD: str = ""

    # --- Recuperación de la contraseña por Telegram ------------------------
    # Único destino autorizado del código de recuperación. Cualquiera puede
    # PEDIR un código, pero solo llega a este chat: quien no tenga acceso a él
    # no puede recuperar la cuenta.
    ADMIN_TELEGRAM_CHAT_ID: str = ""
    # Token del bot que envía el código (el mismo del bot de atención).
    TELEGRAM_BOT_TOKEN: str = ""
    RECUPERACION_CODIGO_MINUTOS: int = 10
    RECUPERACION_MAX_INTENTOS: int = 5

    # --- Login: freno a la fuerza bruta ------------------------------------
    LOGIN_MAX_INTENTOS: int = 8
    LOGIN_VENTANA_SEGUNDOS: int = 300

    # --- Envío de mensajes en cadena ---------------------------------------
    # Espera aleatoria entre las partes de un mismo mensaje. Un envío a ritmo
    # constante es la firma más obvia de un bot; el intervalo variable lo evita.
    ENVIO_DELAY_MIN_SEGUNDOS: float = 1.0
    ENVIO_DELAY_MAX_SEGUNDOS: float = 6.0

    # --- Comunicación con el bot -------------------------------------------
    # Solo para avisos opcionales (reindexar la base de conocimiento al
    # instante). El trabajo de fondo va por tablas, no por HTTP, así que si esto
    # falta no se rompe nada.
    BOT_WEBHOOK_URL: str = "http://whatsapp_webhook:8010"
    INTERNAL_API_TOKEN: str = ""

    # --- Webhooks de WhatsApp por cliente ----------------------------------
    # Dirección PÚBLICA del servicio de webhooks (la que se pega en WasenderAPI).
    # El panel solo la usa para armar la URL que el administrador copia; si está
    # vacía se muestra la ruta sola y se avisa de que falta configurarla.
    PUBLIC_WEBHOOK_BASE_URL: str = ""

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
