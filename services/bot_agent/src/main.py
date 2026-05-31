import sys
import os

# Asegurar que la raíz del proyecto esté en el path para las importaciones (directorio padre de src)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.config import settings
from src.infrastructure.channels.telegram_channel import TelegramChannel
from src.infrastructure.repositories.postgres_user_repo import PostgresUserRepo
import logging

logging.basicConfig(level=logging.INFO)

def main():
    logging.info("Iniciando Bot Agent FSM...")
    
    # Inicializar Base de datos
    repo = PostgresUserRepo()
    
    channel = TelegramChannel(settings.TELEGRAM_BOT_TOKEN)
    channel.start()

if __name__ == "__main__":
    main()
