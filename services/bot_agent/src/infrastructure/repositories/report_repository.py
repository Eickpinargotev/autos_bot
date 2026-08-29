"""Reportes para el asesor humano (antes: tabla `agente humano` en NocoDB).

La hora y la fecha ya no se guardan por separado: `creado_en` es un TIMESTAMPTZ
y el dashboard lo formatea al mostrarlo.
"""

from src.infrastructure.repositories.postgres_conn import ejecutar
from src.application.project_context import proyecto_actual

# Plazos de los reportes revisados y pendientes. Deben coincidir con los del
# dashboard, que es quien muestra sus fechas de caducidad.
REPORTES_RETENCION_DIAS = 1
REPORTES_PENDIENTES_RETENCION_DIAS = 2


class ReportRepository:
    @staticmethod
    def create_report(
        nombre: str, numero: str, problema: str, link_whatsapp: str, canal: str
    ):
        proyecto_id = proyecto_actual()
        if not proyecto_id:
            return False, "mensaje sin proyecto"
        try:
            ejecutar(
                """
                WITH nuevo AS (
                    INSERT INTO reportes
                        (proyecto_id, nombre, numero, problema, link_whatsapp)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id
                )
                INSERT INTO conversation_messages (
                    proyecto_id, client_id, canal, direction, author,
                    sender_id, sender_name, message_type, text, event_type, salida
                )
                SELECT %s, %s, %s, 'internal', 'system', 'reportes', 'Sistema',
                       'system_event', %s, 'report_created',
                       jsonb_build_object('reporte_id', id)
                FROM nuevo
                """,
                (
                    proyecto_id, nombre or "", str(numero or ""), problema or "",
                    link_whatsapp or "", proyecto_id, str(numero or ""), str(canal or ""),
                    problema or "",
                ),
            )
            return True, {}
        except Exception as e:
            print(f"Error guardando reporte en Postgres: {e}")
            return False, str(e)

    @staticmethod
    def purge_expired(
        reviewed_days: int = REPORTES_RETENCION_DIAS,
        pending_days: int = REPORTES_PENDIENTES_RETENCION_DIAS,
    ) -> int:
        """Borra reportes revisados vencidos y pendientes de más de dos días.

        La purga vive AQUÍ y no en el dashboard porque el que tiene reloj es el
        bot: Celery beat ya dispara la retención de conversaciones. El dashboard
        no tiene ningún proceso periódico, y hacerlo al abrir la página dejaría
        el borrado a merced de que alguien la mire.
        """
        return ejecutar(
            """
            DELETE FROM reportes
            WHERE (revisado
                   AND revisado_en IS NOT NULL
                   AND revisado_en < NOW() - (%s || ' days')::interval)
               OR (NOT revisado
                   AND creado_en < NOW() - (%s || ' days')::interval)
            """,
            (int(reviewed_days), int(pending_days)),
        )
