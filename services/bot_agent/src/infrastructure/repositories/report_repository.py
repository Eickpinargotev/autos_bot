"""Reportes para el asesor humano (antes: tabla `agente humano` en NocoDB).

La hora y la fecha ya no se guardan por separado: `creado_en` es un TIMESTAMPTZ
y el dashboard lo formatea al mostrarlo.
"""

from src.infrastructure.repositories.postgres_conn import ejecutar


class ReportRepository:
    @staticmethod
    def create_report(nombre: str, numero: str, problema: str, link_whatsapp: str):
        try:
            ejecutar(
                """
                INSERT INTO reportes (nombre, numero, problema, link_whatsapp)
                VALUES (%s, %s, %s, %s)
                """,
                (nombre or "", str(numero or ""), problema or "", link_whatsapp or ""),
            )
            return True, {}
        except Exception as e:
            print(f"Error guardando reporte en Postgres: {e}")
            return False, str(e)
