"""Registro de clientes que entraron por una palabra clave (`tareas`/`transporte`).

`fragment_catalog` consulta `exists()` en caliente para decidir qué fragmentos
aplican, así que la consulta debe ser barata: es una búsqueda por el índice
único (registro, canal).
"""

from typing import Any

from src.domain.entities import Channel
from src.infrastructure.repositories.postgres_conn import consultar_uno, ejecutar


class KeywordRegistryRepository:
    @staticmethod
    def register_if_missing(registro: str, nombre: str, canal: Channel | str, palabra_clave: str) -> bool:
        canal_value = KeywordRegistryRepository._channel_value(canal)
        try:
            # ON CONFLICT DO NOTHING resuelve "registra si no existe" en una sola
            # ida a la base y de forma atómica: dos mensajes simultáneos del mismo
            # cliente no pueden crear filas duplicadas.
            ejecutar(
                """
                INSERT INTO keyword_registros (registro, canal, nombre, palabra_clave)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (registro, canal) DO NOTHING
                """,
                (str(registro), canal_value, nombre or "Desconocido", palabra_clave or ""),
            )
            return True
        except Exception as e:
            print(f"Error registrando keyword en Postgres: {e}")
            return False

    @staticmethod
    def delete(registro: str, canal: Channel | str) -> bool:
        canal_value = KeywordRegistryRepository._channel_value(canal)
        try:
            ejecutar(
                "DELETE FROM keyword_registros WHERE registro = %s AND canal = %s",
                (str(registro), canal_value),
            )
            return True
        except Exception as e:
            print(f"Error eliminando registro de keyword en Postgres: {e}")
            return False

    @staticmethod
    def find_by_registro_channel(registro: str, canal: Channel | str) -> dict[str, Any] | None:
        canal_value = KeywordRegistryRepository._channel_value(canal)
        return consultar_uno(
            "SELECT * FROM keyword_registros WHERE registro = %s AND canal = %s",
            (str(registro), canal_value),
        )

    @staticmethod
    def exists(registro: str, canal: Channel | str) -> bool:
        try:
            return KeywordRegistryRepository.find_by_registro_channel(registro, canal) is not None
        except Exception as e:
            print(f"Error consultando registro de keyword en Postgres: {e}")
            return False

    @staticmethod
    def _channel_value(canal: Channel | str) -> str:
        return canal.value if isinstance(canal, Channel) else str(canal)
