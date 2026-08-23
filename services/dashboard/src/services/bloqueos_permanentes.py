"""Lista de exclusión permanente, aislada por negocio."""

from typing import Any

from src.db import pool


def normalizar_numero(valor: str) -> str:
    """Deja solo dígitos sin perder ceros iniciales."""
    return "".join(c for c in str(valor or "") if c.isdigit())


def listar(proyecto_id: int, busqueda: str = "") -> list[dict[str, Any]]:
    numero = normalizar_numero(busqueda)
    if busqueda and not numero:
        return []
    filtro = "AND numero LIKE %s" if numero else ""
    params: tuple[Any, ...] = (int(proyecto_id), f"%{numero}%") if numero else (int(proyecto_id),)
    return pool.consultar(
        f"""
        SELECT id, proyecto_id, canal, numero, creado_en, creado_por
        FROM bloqueos_permanentes
        WHERE proyecto_id = %s {filtro}
        ORDER BY creado_en DESC, id DESC
        """,
        params,
    )


def estado_de(proyecto_id: int, canal: str, numero: str) -> dict[str, Any] | None:
    limpio = normalizar_numero(numero)
    if not limpio:
        return None
    return pool.consultar_uno(
        """
        SELECT id, proyecto_id, canal, numero, creado_en, creado_por
        FROM bloqueos_permanentes
        WHERE proyecto_id = %s AND canal = %s AND numero = %s
        """,
        (int(proyecto_id), str(canal), limpio),
    )


def agregar(proyecto_id: int, numero: str, creado_por: str = "", canal: str = "whatsapp") -> dict[str, Any]:
    limpio = normalizar_numero(numero)
    if len(limpio) < 7:
        raise ValueError("Escribe un número válido con código de país.")
    fila = pool.consultar_uno(
        """
        INSERT INTO bloqueos_permanentes (proyecto_id, canal, numero, creado_por)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (proyecto_id, canal, numero) DO NOTHING
        RETURNING id, proyecto_id, canal, numero, creado_en, creado_por
        """,
        (int(proyecto_id), str(canal), limpio, str(creado_por or "")[:120]),
    )
    if not fila:
        raise ValueError(f"El número {limpio} ya está en la lista.")
    return fila


def eliminar(proyecto_id: int, bloqueo_id: int) -> int:
    """Borra solo dentro del negocio autenticado."""
    return pool.ejecutar(
        "DELETE FROM bloqueos_permanentes WHERE id = %s AND proyecto_id = %s",
        (int(bloqueo_id), int(proyecto_id)),
    )


def eliminar_numero(proyecto_id: int, canal: str, numero: str) -> int:
    return pool.ejecutar(
        "DELETE FROM bloqueos_permanentes WHERE proyecto_id = %s AND canal = %s AND numero = %s",
        (int(proyecto_id), str(canal), normalizar_numero(numero)),
    )
