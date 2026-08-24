"""Configuración por proyecto del ritmo de mensajes automáticos."""

from typing import Any

from src.db import pool

MAX_INTERVALO_MENSAJES_SEGUNDOS = 60
MAX_RECORDATORIO_SEGUNDOS = 14 * 24 * 60 * 60
UNIDADES = {"segundos": 1, "minutos": 60, "horas": 3600, "dias": 86400}


def configuracion(proyecto_id: int) -> dict[str, Any]:
    return pool.consultar_uno(
        "SELECT * FROM proyecto_tiempos_mensajes WHERE proyecto_id = %s",
        (int(proyecto_id),),
    ) or {
        "proyecto_id": int(proyecto_id),
        "intervalo_mensajes_segundos": 5,
        "publicidad_recordatorio_1_segundos": 7200,
        "publicidad_recordatorio_2_segundos": 72000,
        "publicidad_recordatorio_3_segundos": 82800,
    }


def _a_segundos(cantidad: Any, unidad: str, nombre: str) -> int:
    try:
        cantidad = int(cantidad)
    except (TypeError, ValueError):
        raise ValueError(f"{nombre} tiene que ser un número entero.") from None
    unidad = str(unidad or "").strip().lower()
    if unidad not in UNIDADES:
        raise ValueError(f"La unidad de {nombre.lower()} no es válida.")
    segundos = cantidad * UNIDADES[unidad]
    if segundos < 5 or segundos > MAX_RECORDATORIO_SEGUNDOS:
        raise ValueError(f"{nombre} debe estar entre 5 segundos y 14 días.")
    return segundos


def validar(
    intervalo_mensajes_segundos: Any,
    recordatorios: list[tuple[Any, str]],
) -> tuple[int, list[int]]:
    try:
        intervalo = int(intervalo_mensajes_segundos)
    except (TypeError, ValueError):
        raise ValueError("La pausa entre mensajes tiene que ser un número entero.") from None
    if intervalo < 1 or intervalo > MAX_INTERVALO_MENSAJES_SEGUNDOS:
        raise ValueError("La pausa entre mensajes debe estar entre 1 y 60 segundos.")
    if len(recordatorios) != 3:
        raise ValueError("Se necesitan los tres tiempos de publicidad.")

    segundos = [
        _a_segundos(cantidad, unidad, f"El recordatorio {indice}")
        for indice, (cantidad, unidad) in enumerate(recordatorios, start=1)
    ]
    if not segundos[0] < segundos[1] < segundos[2]:
        raise ValueError("Cada recordatorio de publicidad debe salir después del anterior.")
    return intervalo, segundos


def guardar(
    proyecto_id: int,
    intervalo_mensajes_segundos: Any,
    recordatorios: list[tuple[Any, str]],
    usuario: str,
) -> dict[str, Any]:
    intervalo, segundos = validar(intervalo_mensajes_segundos, recordatorios)

    return pool.consultar_uno(
        """
        INSERT INTO proyecto_tiempos_mensajes (
            proyecto_id, intervalo_mensajes_segundos,
            publicidad_recordatorio_1_segundos,
            publicidad_recordatorio_2_segundos,
            publicidad_recordatorio_3_segundos,
            actualizado_por, actualizado_en
        ) VALUES (%s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (proyecto_id) DO UPDATE SET
            intervalo_mensajes_segundos = EXCLUDED.intervalo_mensajes_segundos,
            publicidad_recordatorio_1_segundos = EXCLUDED.publicidad_recordatorio_1_segundos,
            publicidad_recordatorio_2_segundos = EXCLUDED.publicidad_recordatorio_2_segundos,
            publicidad_recordatorio_3_segundos = EXCLUDED.publicidad_recordatorio_3_segundos,
            actualizado_por = EXCLUDED.actualizado_por,
            actualizado_en = NOW()
        RETURNING *
        """,
        (int(proyecto_id), intervalo, *segundos, str(usuario)[:120]),
    )


def para_formulario(segundos: int) -> dict[str, Any]:
    """Presenta un intervalo usando la unidad exacta más cómoda."""
    segundos = int(segundos)
    for unidad, factor in (("dias", 86400), ("horas", 3600), ("minutos", 60)):
        if segundos % factor == 0:
            return {"cantidad": segundos // factor, "unidad": unidad}
    return {"cantidad": segundos, "unidad": "segundos"}
