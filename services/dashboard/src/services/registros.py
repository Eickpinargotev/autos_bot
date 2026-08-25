"""Registro de quienes dispararon alguna palabra clave del negocio.

Esta tabla también decide las variantes de SINPE del bot. El dashboard solo la
consulta: una página nunca trae más de diez filas y la exportación recorre la
base por lotes para no construir el archivo entero en memoria.
"""

import base64
import csv
import io
import json
from datetime import datetime
from typing import Any, Iterator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.db import pool

TAMANO_PAGINA = 10
TAMANO_EXPORTACION = 1000


def normalizar_numero(valor: str) -> str:
    """Deja solo dígitos sin convertir el teléfono a entero."""
    return "".join(c for c in str(valor or "") if c.isdigit())


def _codificar_cursor(fecha: datetime, registro_id: int) -> str:
    datos = json.dumps([fecha.isoformat(), int(registro_id)], separators=(",", ":"))
    return base64.urlsafe_b64encode(datos.encode("utf-8")).decode("ascii").rstrip("=")


def _decodificar_cursor(cursor: str) -> tuple[datetime, int]:
    try:
        relleno = "=" * (-len(cursor) % 4)
        fecha, registro_id = json.loads(
            base64.urlsafe_b64decode((cursor + relleno).encode("ascii")).decode("utf-8")
        )
        instante = datetime.fromisoformat(fecha)
        if instante.tzinfo is None:
            raise ValueError
        return instante, int(registro_id)
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("El cursor de registros no es válido.") from exc


def _lote(
    proyecto_id: int,
    *,
    limite: int,
    despues_de: tuple[datetime, int] | None = None,
) -> list[dict[str, Any]]:
    filtro = ""
    params: list[Any] = [int(proyecto_id)]
    if despues_de:
        filtro = "AND (creado_en, id) < (%s, %s)"
        params.extend(despues_de)
    params.append(int(limite))
    return pool.consultar(
        f"""
        SELECT id, registro, canal, nombre, palabra_clave, creado_en
        FROM keyword_registros
        WHERE proyecto_id = %s {filtro}
        ORDER BY creado_en DESC, id DESC
        LIMIT %s
        """,
        tuple(params),
    )


def pagina(proyecto_id: int, cursor: str = "") -> dict[str, Any]:
    """Una tanda de diez filas y el cursor opaco de la siguiente."""
    despues_de = _decodificar_cursor(cursor) if cursor else None
    filas = _lote(
        proyecto_id,
        limite=TAMANO_PAGINA + 1,
        despues_de=despues_de,
    )
    visibles = filas[:TAMANO_PAGINA]
    siguiente = ""
    if len(filas) > TAMANO_PAGINA and visibles:
        ultima = visibles[-1]
        siguiente = _codificar_cursor(ultima["creado_en"], ultima["id"])
    return {"registros": visibles, "siguiente_cursor": siguiente}


def buscar(proyecto_id: int, numero: str) -> list[dict[str, Any]]:
    """Coincidencia exacta tras quitar formato del teléfono."""
    limpio = normalizar_numero(numero)
    if not limpio:
        return []
    return pool.consultar(
        """
        SELECT id, registro, canal, nombre, palabra_clave, creado_en
        FROM keyword_registros
        WHERE proyecto_id = %s AND registro = %s
        ORDER BY creado_en DESC, id DESC
        """,
        (int(proyecto_id), limpio),
    )


def _todos(proyecto_id: int) -> Iterator[dict[str, Any]]:
    cursor: tuple[datetime, int] | None = None
    while True:
        filas = _lote(
            proyecto_id,
            limite=TAMANO_EXPORTACION,
            despues_de=cursor,
        )
        if not filas:
            return
        yield from filas
        ultima = filas[-1]
        cursor = (ultima["creado_en"], ultima["id"])
        if len(filas) < TAMANO_EXPORTACION:
            return


def _linea_csv(valores: list[str]) -> str:
    salida = io.StringIO(newline="")
    csv.writer(salida, lineterminator="\r\n").writerow(valores)
    return salida.getvalue()


def exportar_csv(proyecto_id: int, zona_horaria: str) -> Iterator[str]:
    """CSV UTF-8 con BOM, producido fila a fila para ``StreamingResponse``."""
    try:
        zona = ZoneInfo(zona_horaria)
    except (ZoneInfoNotFoundError, ValueError):
        zona = ZoneInfo("UTC")

    yield "\ufeff"
    yield _linea_csv(["numero", "Nombre", "Palabra clave", "Canal", "Fecha de registro"])
    for fila in _todos(proyecto_id):
        fecha = fila["creado_en"].astimezone(zona).strftime("%d/%m/%Y")
        yield _linea_csv(
            [
                str(fila["registro"]),
                str(fila["nombre"] or ""),
                str(fila["palabra_clave"] or "Histórico"),
                str(fila["canal"]),
                fecha,
            ]
        )
