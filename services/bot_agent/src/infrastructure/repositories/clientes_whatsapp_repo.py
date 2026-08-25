"""El negocio dueño de un webhook, y la credencial con la que ese negocio responde.

Cada cliente (negocio) tiene su propia URL `/webhooks/wasender/<token>`. El
token identifica Y autentica: no hay una segunda credencial que comparar.

Aquí vive también el otro extremo del recorrido: con qué **API key** se contesta.
Es por negocio y sale de la base (`clientes_whatsapp.wasender_api_key`), no del
entorno, porque cada negocio enlaza su propio número en WasenderAPI y se
administra desde el panel. La entrada sabe de qué negocio es el mensaje; la
salida ocurre después, en el worker, que solo conoce canal y número. El puente
entre los dos es `conversacion_negocio` (ver migración 009).

Se cachea en memoria unos segundos porque esto corre en CADA evento entrante
(mensajes, recibos, cambios de grupo) y sería una consulta a Postgres por
evento. El TTL es corto a propósito: revocar un token o cambiar una clave desde
el panel tiene que surtir efecto sin reiniciar nada.

El contador de eventos se actualiza fuera del camino de la respuesta y nunca
propaga excepciones: es diagnóstico, no puede tumbar la atención de un cliente.
"""

import hmac
import time
from typing import Any

from src.infrastructure.repositories.postgres_conn import consultar, consultar_uno, ejecutar
from src.application.project_context import proyecto_actual

# Cuánto se recuerda la resolución de un token (y también su ausencia: así un
# token inválido repetido no golpea la base en cada intento).
CACHE_TTL_SEGUNDOS = 30

_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}
_cache_credenciales: dict[tuple[int, str, str], tuple[float, str]] = {}


def limpiar_cache() -> None:
    _cache.clear()
    _cache_credenciales.clear()


def zona_horaria_del_proyecto(proyecto_id: int | None = None) -> str:
    """Zona local del negocio actual; respaldo seguro para tareas antiguas."""
    proyecto_id = int(proyecto_id or proyecto_actual() or 0)
    if not proyecto_id:
        return "America/Costa_Rica"
    try:
        fila = consultar_uno(
            "SELECT zona_horaria FROM clientes_whatsapp WHERE id = %s",
            (proyecto_id,),
        )
    except Exception as exc:
        print(f"Error leyendo zona horaria del negocio: {exc}")
        return "America/Costa_Rica"
    return str((fila or {}).get("zona_horaria") or "America/Costa_Rica")


def por_token(token: str) -> dict[str, Any] | None:
    """El negocio activo dueño de ese token, o None si no existe o está inactivo."""
    if not token:
        return None

    ahora = time.monotonic()
    guardado = _cache.get(token)
    if guardado and (ahora - guardado[0]) < CACHE_TTL_SEGUNDOS:
        return guardado[1]

    try:
        fila = consultar_uno(
            """
            SELECT id, nombre, slug, webhook_token, wasender_api_key,
                   wasender_webhook_secret, numero, activo
            FROM clientes_whatsapp
            WHERE webhook_token = %s AND activo
            """,
            (token,),
        )
    except Exception as e:
        print(f"Error resolviendo el webhook por token: {e}")
        return None

    # La comparación en tiempo constante no cambia nada frente al índice único
    # (Postgres ya respondió), pero deja explícito que esto es una credencial.
    if fila and not hmac.compare_digest(str(fila.get("webhook_token") or ""), token):
        fila = None

    _cache[token] = (ahora, fila)
    return fila


def vincular_conversacion(proyecto_id: int, canal: str, client_id: str) -> None:
    """Anota a qué negocio pertenece una conversación.

    Se llama en la ENTRADA, que es el único momento en que se sabe: el mensaje
    llegó por la URL de ese negocio. Sin esto, al responder solo habría canal y
    número, que no dicen de quién es el número.

    El `DO UPDATE` no es decorativo: un mismo cliente puede cambiar de negocio
    (un número que se traslada, un negocio que se rehace), y el último que
    recibió el mensaje es el que tiene que contestarlo. Nunca propaga la
    excepción — perder la anotación degrada el envío a un aviso claro, pero
    tumbar aquí dejaría al cliente sin respuesta.
    """
    try:
        ejecutar(
            """
            INSERT INTO conversacion_negocio (proyecto_id, canal, client_id)
            VALUES (%s, %s, %s)
            ON CONFLICT (proyecto_id, canal, client_id) DO UPDATE
                SET actualizado_en = NOW()
            """,
            (int(proyecto_id), str(canal), str(client_id)[:80]),
        )
    except Exception as e:
        print(f"Error vinculando la conversación con su negocio: {e}")
    else:
        _cache_credenciales.pop((int(proyecto_id), str(canal), str(client_id)), None)


def conversacion_pertenece(proyecto_id: int, canal: str, client_id: str) -> bool:
    fila = consultar_uno(
        "SELECT 1 AS existe FROM conversacion_negocio "
        "WHERE proyecto_id = %s AND canal = %s AND client_id = %s",
        (int(proyecto_id), str(canal), str(client_id)),
    )
    return bool(fila)


def api_key_de_envio(canal: str, client_id: str, proyecto_id: int | None = None) -> str:
    """La clave de WasenderAPI con la que se le responde a ese número.

    Orden de resolución:

    1. El negocio al que pertenece la conversación (lo normal: el mensaje entró
       por su webhook y quedó anotado).
    2. Si no está anotada y hay UN SOLO negocio activo con clave, ese. Cubre lo
       que no nace de un mensaje entrante: un envío manual desde el panel a
       alguien que nunca escribió, o una conversación anterior a la migración.
       Con dos o más negocios no se adivina: sería mandarle el mensaje a un
       cliente desde el número de otro negocio.
    3. Nada. El que llama decide qué hacer; hoy avisa de que falta configurarla.
    """
    proyecto_id = int(proyecto_id or proyecto_actual() or 0)
    clave_cache = (proyecto_id, str(canal), str(client_id))
    ahora = time.monotonic()
    guardado = _cache_credenciales.get(clave_cache)
    if guardado and (ahora - guardado[0]) < CACHE_TTL_SEGUNDOS:
        return guardado[1]

    try:
        fila = consultar_uno(
            """
            SELECT c.wasender_api_key
            FROM clientes_whatsapp c
            WHERE c.id = %s AND c.activo
            """,
            (proyecto_id,),
        )
        if (not proyecto_id) and (not fila or not (fila.get("wasender_api_key") or "")):
            # LIMIT 2 y no LIMIT 1: la pregunta no es «dame uno», es «¿hay
            # exactamente uno?». Con dos filas la respuesta es que no se puede
            # decidir, y se prefiere no enviar a enviar por el número ajeno.
            candidatos = consultar(
                """
                SELECT wasender_api_key
                FROM clientes_whatsapp
                WHERE activo AND wasender_api_key <> ''
                LIMIT 2
                """
            )
            fila = candidatos[0] if len(candidatos) == 1 else None
    except Exception as e:
        print(f"Error resolviendo la credencial de envío: {e}")
        return ""

    api_key = str((fila or {}).get("wasender_api_key") or "")
    _cache_credenciales[clave_cache] = (ahora, api_key)
    return api_key


def registrar_evento(cliente_id: int, evento: str) -> None:
    """Deja constancia de que ese webhook está recibiendo tráfico."""
    try:
        ejecutar(
            """
            UPDATE clientes_whatsapp
            SET ultimo_evento_en = NOW(),
                ultimo_evento = %s,
                eventos_recibidos = eventos_recibidos + 1
            WHERE id = %s
            """,
            (str(evento or "")[:60], int(cliente_id)),
        )
    except Exception as e:
        print(f"Error registrando el evento del webhook: {e}")
