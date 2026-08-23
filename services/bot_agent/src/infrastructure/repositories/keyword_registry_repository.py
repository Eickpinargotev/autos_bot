"""Registro de clientes que entraron por una palabra clave (`tareas`/`transporte`).

`fragment_catalog` consulta `exists()` en caliente para decidir qué fragmentos
aplican, así que la consulta debe ser barata: es una búsqueda por el índice
único (registro, canal).
"""

from typing import Any

from src.domain.entities import Channel
from src.infrastructure.repositories.postgres_conn import consultar_uno, ejecutar
from src.application.project_context import proyecto_actual


class KeywordRegistryRepository:
    @staticmethod
    def register_if_missing(registro: str, nombre: str, canal: Channel | str, palabra_clave: str) -> bool:
        canal_value = KeywordRegistryRepository._channel_value(canal)
        proyecto_id = proyecto_actual()
        if not proyecto_id:
            return False
        try:
            # ON CONFLICT DO NOTHING resuelve "registra si no existe" en una sola
            # ida a la base y de forma atómica: dos mensajes simultáneos del mismo
            # cliente no pueden crear filas duplicadas.
            ejecutar(
                """
                INSERT INTO keyword_registros (proyecto_id, registro, canal, nombre, palabra_clave)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (proyecto_id, registro, canal) DO NOTHING
                """,
                (proyecto_id, str(registro), canal_value, nombre or "Desconocido", palabra_clave or ""),
            )
            return True
        except Exception as e:
            print(f"Error registrando keyword en Postgres: {e}")
            return False

    @staticmethod
    def delete(registro: str, canal: Channel | str) -> bool:
        canal_value = KeywordRegistryRepository._channel_value(canal)
        proyecto_id = proyecto_actual()
        if not proyecto_id:
            return False
        try:
            ejecutar(
                "DELETE FROM keyword_registros WHERE proyecto_id = %s AND registro = %s AND canal = %s",
                (proyecto_id, str(registro), canal_value),
            )
            return True
        except Exception as e:
            print(f"Error eliminando registro de keyword en Postgres: {e}")
            return False

    @staticmethod
    def find_by_registro_channel(registro: str, canal: Channel | str) -> dict[str, Any] | None:
        canal_value = KeywordRegistryRepository._channel_value(canal)
        proyecto_id = proyecto_actual()
        if not proyecto_id:
            return None
        return consultar_uno(
            "SELECT * FROM keyword_registros WHERE proyecto_id = %s AND registro = %s AND canal = %s",
            (proyecto_id, str(registro), canal_value),
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
