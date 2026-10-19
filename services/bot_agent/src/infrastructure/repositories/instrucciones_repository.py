"""Prompts comerciales y recordatorios activos del proyecto."""

from src.application.project_context import proyecto_actual
from src.infrastructure.repositories.postgres_conn import consultar_uno

TIPOS = frozenset({
    "supervisor", "general", "curso_teorico", "alquiler", "clases",
    "dictamen", "tramites", "recordatorio",
})


def activas(tipo: str = "principal") -> str:
    proyecto_id = proyecto_actual()
    if not proyecto_id:
        return ""
    tipo = str(tipo or "principal").strip().lower()
    if tipo not in TIPOS:
        return ""
    try:
        fila = consultar_uno(
            "SELECT contenido FROM proyecto_instrucciones "
            "WHERE proyecto_id = %s AND tipo = %s AND activa ORDER BY version DESC LIMIT 1",
            (proyecto_id, tipo),
        )
    except Exception as exc:
        print(f"Error leyendo instrucciones del proyecto: {exc}")
        return ""
    contenido = str((fila or {}).get("contenido") or "").strip()
    return contenido


def configuracion_recordatorios() -> dict:
    proyecto_id = proyecto_actual()
    if not proyecto_id:
        return {"habilitado": True, "intervalo_minutos": 60, "maximo_recordatorios": 2}
    try:
        fila = consultar_uno(
            "SELECT habilitado, intervalo_minutos, maximo_recordatorios "
            "FROM proyecto_recordatorios "
            "WHERE proyecto_id = %s",
            (proyecto_id,),
        )
    except Exception as exc:
        print(f"Error leyendo configuración de recordatorios: {exc}")
        return {"habilitado": True, "intervalo_minutos": 60, "maximo_recordatorios": 2}
    return fila or {
        "habilitado": True, "intervalo_minutos": 60, "maximo_recordatorios": 2
    }


def configuracion_tiempos_mensajes() -> dict:
    """Ritmo editable del proyecto; los respaldos conservan el contrato anterior."""
    proyecto_id = proyecto_actual()
    respaldo = {
        "intervalo_mensajes_segundos": 5,
        "publicidad_recordatorio_1_segundos": 7200,
        "publicidad_recordatorio_2_segundos": 72000,
        "publicidad_recordatorio_3_segundos": 82800,
    }
    if not proyecto_id:
        return respaldo
    try:
        fila = consultar_uno(
            "SELECT intervalo_mensajes_segundos, "
            "publicidad_recordatorio_1_segundos, "
            "publicidad_recordatorio_2_segundos, "
            "publicidad_recordatorio_3_segundos "
            "FROM proyecto_tiempos_mensajes WHERE proyecto_id = %s",
            (proyecto_id,),
        )
    except Exception as exc:
        print(f"Error leyendo tiempos de mensajes: {exc}")
        return respaldo
    return fila or respaldo


def intervalo_entre_mensajes() -> int:
    config = configuracion_tiempos_mensajes()
    return max(1, min(int(config.get("intervalo_mensajes_segundos") or 5), 60))


TEXTOS_PUBLICIDAD_ORIGINALES = (
    '📌 Hola!!!  No recibí respuesta a nuestra conversación.  Podemos continuar???',
    (
        '👋🏻Vi que no se unió al grupo👋🏻\n'
        '\n'
        '*¿¿¿Tiene alguna duda duda antes de unirse al grupo???'
    ),
    (
        '📌Hola!!!\n'
        '\n'
        'Le comparto la información de nuestro curso.\n'
        '\n'
        'Fecha: {dia}\n'
        '\n'
        'Hora: {hora}\n'
        'Valor: {valor} colones\n'
        '\n'
        'Unirse al grupo: {enlace}\n'
        '\n'
        'Le esperamos'
    ),
)


def textos_recordatorios_publicidad() -> list[str]:
    """Lee al programar, nunca al enviar: las tareas pendientes guardan su copia."""
    proyecto_id = proyecto_actual()
    fila = None
    if proyecto_id:
        try:
            fila = consultar_uno(
                "SELECT publicidad_recordatorio_1_texto, publicidad_recordatorio_2_texto, "
                "publicidad_recordatorio_3_texto FROM proyecto_tiempos_mensajes WHERE proyecto_id = %s",
                (proyecto_id,),
            )
        except Exception as exc:
            # Permite actualizar el bot antes de que arranque la migración del panel.
            print(f"Error leyendo textos de publicidad: {exc}")
    return [(fila or {}).get(f"publicidad_recordatorio_{i}_texto") or original
            for i, original in enumerate(TEXTOS_PUBLICIDAD_ORIGINALES, 1)]
