"""Catálogo de invitaciones por ciudad (antes: Google Sheet y tabla NocoDB).

Lo edita el dashboard; el bot solo lee. `publicidad_service` sigue trabajando
con las claves históricas ("CIUDAD", "PRIMER MENSAJE"...), así que la traducción
de nombres de columna se hace aquí y el servicio no cambia.
"""

from typing import Any

from src.infrastructure.repositories.postgres_conn import consultar

# columna en Postgres -> clave que espera publicidad_service
_MAPA_CAMPOS = {
    "ciudad": "CIUDAD",
    "mensaje_1": "PRIMER MENSAJE",
    "mensaje_2": "SEGUNDO MENSAJE",
    "mensaje_3": "TERCER MENSAJE",
    "mensaje_4": "CUARTO MENSAJE",
    "mensaje_5": "QUINTO MENSAJE",
    "ciudad_mayuscula": "CIUDAD_MAYUSCULA",
    "link_facebook": "LINK FACEBOOK",
}


class InvitacionesRepository:
    @staticmethod
    def listar_activas() -> list[dict[str, Any]]:
        """Ciudades activas, en el formato de claves que usa el flujo de publicidad."""
        try:
            filas = consultar(
                """
                SELECT id, ciudad, mensaje_1, mensaje_2, mensaje_3, mensaje_4, mensaje_5,
                       ciudad_mayuscula, link_facebook
                FROM invitaciones_ciudades
                WHERE activo
                ORDER BY ciudad
                """
            )
        except Exception as e:
            print(f"Error leyendo invitaciones por ciudad en Postgres: {e}")
            return []

        registros = []
        for fila in filas:
            registro = {destino: fila.get(origen) or "" for origen, destino in _MAPA_CAMPOS.items()}
            registro["id"] = fila.get("id")
            registros.append(registro)
        return registros
