"""Consulta la lista permanente que administra cada negocio desde el panel."""

from src.domain.entities import Channel
from src.infrastructure.repositories.postgres_conn import consultar_uno
from src.application.project_context import proyecto_actual


def esta_bloqueado(user_id: str, channel: Channel | str) -> bool:
    canal = channel.value if isinstance(channel, Channel) else str(channel)
    proyecto_id = proyecto_actual()
    if not proyecto_id:
        return False
    fila = consultar_uno(
        """
        SELECT b.id
        FROM bloqueos_permanentes b
        WHERE b.proyecto_id = %s AND b.canal = %s AND b.numero = %s
        LIMIT 1
        """,
        (proyecto_id, canal, str(user_id)),
    )
    return bool(fila)
