"""Catálogo de invitaciones por ciudad.

Es la tabla que antes vivía en un Google Sheet. El bot la lee para armar la
secuencia de publicidad; aquí se edita.
"""

from typing import Any

from src.db import pool

CAMPOS_EDITABLES = (
    "ciudad",
    "mensaje_1",
    "mensaje_2",
    "mensaje_3",
    "mensaje_4",
    "mensaje_5",
    "ciudad_mayuscula",
    "link_facebook",
)


def listar(busqueda: str = "", solo_activas: bool = False) -> list[dict[str, Any]]:
    condiciones = []
    params: list[Any] = []
    if busqueda:
        condiciones.append("ciudad ILIKE %s")
        params.append(f"%{busqueda}%")
    if solo_activas:
        condiciones.append("activo")
    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    return pool.consultar(
        f"SELECT * FROM invitaciones_ciudades {where} ORDER BY ciudad",
        tuple(params) or None,
    )


def obtener(ciudad_id: int) -> dict[str, Any] | None:
    return pool.consultar_uno("SELECT * FROM invitaciones_ciudades WHERE id = %s", (ciudad_id,))


def crear(usuario: str) -> dict[str, Any]:
    return pool.consultar_uno(
        """
        INSERT INTO invitaciones_ciudades (ciudad, activo, actualizado_por)
        VALUES ('NUEVA CIUDAD', FALSE, %s)
        RETURNING *
        """,
        (usuario,),
    )


def actualizar_campo(ciudad_id: int, campo: str, valor: str, usuario: str) -> dict[str, Any] | None:
    """Guarda una sola celda. El nombre de columna se valida contra la lista blanca."""
    if campo not in CAMPOS_EDITABLES:
        raise ValueError(f"Campo no editable: {campo}")
    return pool.consultar_uno(
        f"""
        UPDATE invitaciones_ciudades
        SET {campo} = %s, actualizado_en = NOW(), actualizado_por = %s
        WHERE id = %s
        RETURNING *
        """,
        (valor, usuario, ciudad_id),
    )


def alternar_activo(ciudad_id: int, usuario: str) -> dict[str, Any] | None:
    return pool.consultar_uno(
        """
        UPDATE invitaciones_ciudades
        SET activo = NOT activo, actualizado_en = NOW(), actualizado_por = %s
        WHERE id = %s
        RETURNING *
        """,
        (usuario, ciudad_id),
    )


def eliminar(ciudad_id: int) -> int:
    return pool.ejecutar("DELETE FROM invitaciones_ciudades WHERE id = %s", (ciudad_id,))


def avisos(fila: dict[str, Any]) -> list[str]:
    """Problemas que dejarían el flujo de publicidad a medias.

    No bloquean el guardado (una ciudad se arma por partes), pero se muestran en
    la fila para que no pasen inadvertidos: hoy, sin el enlace del grupo en el
    mensaje 4, el bot corta el flujo y genera un reporte al asesor sin que nadie
    entienda por qué.
    """
    problemas = []
    if not str(fila.get("ciudad") or "").strip():
        problemas.append("Falta el nombre de la ciudad.")
    if not str(fila.get("mensaje_1") or "").strip():
        problemas.append("Falta el primer mensaje.")
    if "chat.whatsapp.com" not in str(fila.get("mensaje_4") or ""):
        problemas.append("El mensaje 4 debe incluir el enlace del grupo de WhatsApp.")
    return problemas
