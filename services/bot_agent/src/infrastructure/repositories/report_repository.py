"""Reportes para el asesor humano (antes: tabla `agente humano` en NocoDB).

La hora y la fecha ya no se guardan por separado: `creado_en` es un TIMESTAMPTZ
y el dashboard lo formatea al mostrarlo.
"""

from src.infrastructure.repositories.postgres_conn import ejecutar

# Cuánto sobrevive un reporte YA REVISADO, desde que se marcó como tal. Lo
# pendiente no caduca: que nadie lo haya mirado en un mes no lo hace menos
# urgente. El valor tiene que coincidir con `REPORTES_RETENCION_DIAS` del
# dashboard, que es quien lo enseña.
REPORTES_RETENCION_DIAS = 7


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

    @staticmethod
    def purge_reviewed(days: int = REPORTES_RETENCION_DIAS) -> int:
        """Borra los reportes revisados que ya cumplieron su plazo.

        La purga vive AQUÍ y no en el dashboard porque el que tiene reloj es el
        bot: Celery beat ya dispara la retención de conversaciones. El dashboard
        no tiene ningún proceso periódico, y hacerlo al abrir la página dejaría
        el borrado a merced de que alguien la mire.
        """
        return ejecutar(
            """
            DELETE FROM reportes
            WHERE revisado
              AND revisado_en IS NOT NULL
              AND revisado_en < NOW() - (%s || ' days')::interval
            """,
            (int(days),),
        )
