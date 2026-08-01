"""Seguimiento por cliente y resumen mensual, en Postgres.

Tablas:
- `seguimiento_clientes`: una fila por (client_id, canal) con contadores de
  conversaciones (ventana de 24h), derivaciones a asesor, costo acumulado del
  LLM y el historial simplificado del chat.
- `resumen_mensual`: una fila por mes (YYYY-MM) con mensajes del bot, mensajes
  del cliente y costo total.

El costo se persiste como entero en micro-USD (`costo_microusd`): sumar enteros
no acumula error de punto flotante. El valor decimal legible se deriva al
mostrarlo, ya no se guarda.

La API pública (find/create/update + record_fields/record_id) es la misma que
usaba `seguimiento_service` cuando esto vivía en NocoDB, así que el servicio no
cambió. Por eso `record_fields` devuelve las fechas como texto ISO: el servicio
las compara con `parse_timestamp`.
"""

import json
from datetime import datetime
from typing import Any

from src.infrastructure.repositories.fechas import a_iso
from src.infrastructure.repositories.postgres_conn import consultar_uno, ejecutar

# Columnas que el servicio puede escribir. Cualquier otra clave que llegue en
# `fields` se ignora: así un campo derivado (p. ej. el costo en USD decimal, que
# ya no se guarda) no rompe el INSERT.
_COLUMNAS_CLIENTE = (
    "nombre",
    "conversaciones_iniciadas",
    "conversacion_actual_inicio",
    "primera_interaccion",
    "ultima_interaccion",
    "derivaciones_asesor",
    "costo_microusd",
    "tokens_entrada",
    "tokens_salida",
    "historial",
)

_COLUMNAS_MES = (
    "mensajes_bot",
    "mensajes_cliente",
    "costo_microusd",
    "tokens_entrada",
    "tokens_salida",
    "actualizado_en",
)

_COLUMNAS_FECHA = {
    "conversacion_actual_inicio",
    "primera_interaccion",
    "ultima_interaccion",
    "actualizado_en",
}


class SeguimientoRepository:
    # ------------------------- seguimiento_clientes -------------------------

    @staticmethod
    def find_cliente(client_id: str, canal: str) -> dict[str, Any] | None:
        return consultar_uno(
            "SELECT * FROM seguimiento_clientes WHERE client_id = %s AND canal = %s",
            (str(client_id), str(canal)),
        )

    @staticmethod
    def create_cliente(fields: dict[str, Any]) -> bool:
        datos = SeguimientoRepository._filtrar(fields, _COLUMNAS_CLIENTE)
        datos["client_id"] = str(fields.get("client_id") or "")
        datos["canal"] = str(fields.get("canal") or "")
        columnas = list(datos.keys())
        marcadores = ", ".join(["%s"] * len(columnas))
        # ON CONFLICT: si dos procesos crean la misma fila a la vez, el segundo
        # actualiza en vez de fallar por violación de unicidad.
        actualizaciones = ", ".join(
            f"{col} = EXCLUDED.{col}" for col in columnas if col not in ("client_id", "canal")
        )
        try:
            ejecutar(
                f"""
                INSERT INTO seguimiento_clientes ({", ".join(columnas)})
                VALUES ({marcadores})
                ON CONFLICT (client_id, canal) DO UPDATE SET {actualizaciones}
                """,
                tuple(datos[col] for col in columnas),
            )
            return True
        except Exception as e:
            print(f"Error creando seguimiento de cliente: {e}")
            return False

    @staticmethod
    def update_cliente(record_id: str, fields: dict[str, Any]) -> bool:
        return SeguimientoRepository._update(
            "seguimiento_clientes", "id", record_id, fields, _COLUMNAS_CLIENTE
        )

    # --------------------------- resumen_mensual ----------------------------

    @staticmethod
    def find_mes(mes: str) -> dict[str, Any] | None:
        return consultar_uno("SELECT * FROM resumen_mensual WHERE mes = %s", (str(mes),))

    @staticmethod
    def create_mes(fields: dict[str, Any]) -> bool:
        datos = SeguimientoRepository._filtrar(fields, _COLUMNAS_MES)
        datos["mes"] = str(fields.get("mes") or "")
        columnas = list(datos.keys())
        marcadores = ", ".join(["%s"] * len(columnas))
        actualizaciones = ", ".join(f"{col} = EXCLUDED.{col}" for col in columnas if col != "mes")
        try:
            ejecutar(
                f"""
                INSERT INTO resumen_mensual ({", ".join(columnas)})
                VALUES ({marcadores})
                ON CONFLICT (mes) DO UPDATE SET {actualizaciones}
                """,
                tuple(datos[col] for col in columnas),
            )
            return True
        except Exception as e:
            print(f"Error creando resumen mensual: {e}")
            return False

    @staticmethod
    def update_mes(record_id: str, fields: dict[str, Any]) -> bool:
        return SeguimientoRepository._update("resumen_mensual", "mes", record_id, fields, _COLUMNAS_MES)

    # ------------------------------ comunes ---------------------------------

    @staticmethod
    def record_id(record: dict[str, Any] | None) -> str:
        """Clave con la que se actualiza la fila: `id` para clientes, `mes` para el resumen."""
        if not record:
            return ""
        return str(record.get("id") or record.get("mes") or "")

    @staticmethod
    def record_fields(record: dict[str, Any] | None) -> dict[str, Any]:
        """Fila normalizada: fechas como ISO y el historial como texto JSON.

        `seguimiento_service` trata estos valores igual que cuando venían de
        NocoDB (los pasa por `parse_timestamp` y `json.loads`), así que la
        conversión se hace aquí y el servicio no se entera del cambio de motor.
        """
        if not record:
            return {}
        salida: dict[str, Any] = {}
        for clave, valor in record.items():
            if isinstance(valor, datetime):
                salida[clave] = a_iso(valor)
            elif clave == "historial" and isinstance(valor, (dict, list)):
                salida[clave] = json.dumps(valor, ensure_ascii=False)
            else:
                salida[clave] = valor
        return salida

    @staticmethod
    def _filtrar(fields: dict[str, Any], columnas: tuple[str, ...]) -> dict[str, Any]:
        datos: dict[str, Any] = {}
        for columna in columnas:
            if columna not in fields:
                continue
            valor = fields[columna]
            if columna in _COLUMNAS_FECHA:
                # Cadena vacía no es una fecha válida en Postgres: se guarda NULL.
                datos[columna] = valor or None
            else:
                datos[columna] = valor
        return datos

    @staticmethod
    def _update(
        tabla: str,
        columna_clave: str,
        clave: Any,
        fields: dict[str, Any],
        columnas: tuple[str, ...],
    ) -> bool:
        datos = SeguimientoRepository._filtrar(fields, columnas)
        if not datos or not clave:
            return False
        asignaciones = ", ".join(f"{col} = %s" for col in datos)
        try:
            ejecutar(
                f"UPDATE {tabla} SET {asignaciones} WHERE {columna_clave} = %s",
                tuple(datos.values()) + (clave,),
            )
            return True
        except Exception as e:
            print(f"Error actualizando {tabla}: {e}")
            return False

    @staticmethod
    def purgar_clientes_vencidos(dias: int) -> int:
        """Borra el seguimiento de clientes inactivos hace más de `dias`."""
        if dias <= 0:
            return 0
        try:
            return ejecutar(
                """
                DELETE FROM seguimiento_clientes
                WHERE ultima_interaccion IS NOT NULL
                  AND ultima_interaccion < NOW() - (%s || ' days')::interval
                """,
                (str(int(dias)),),
            )
        except Exception as e:
            print(f"Error purgando seguimiento vencido: {e}")
            return 0
