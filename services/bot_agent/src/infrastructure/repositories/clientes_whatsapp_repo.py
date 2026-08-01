"""Resolución del negocio dueño de un webhook a partir del token de la URL.

Cada cliente (negocio) tiene su propia URL `/webhooks/wasender/<token>`. El
token identifica Y autentica: no hay una segunda credencial que comparar.

Se cachea en memoria unos segundos porque esto corre en CADA evento entrante
(mensajes, recibos, cambios de grupo) y sería una consulta a Postgres por
evento. El TTL es corto a propósito: revocar un token desde el panel tiene que
surtir efecto sin reiniciar el webhook.

El contador de eventos se actualiza fuera del camino de la respuesta y nunca
propaga excepciones: es diagnóstico, no puede tumbar la atención de un cliente.
"""

import hmac
import time
from typing import Any

from src.infrastructure.repositories.postgres_conn import consultar_uno, ejecutar

# Cuánto se recuerda la resolución de un token (y también su ausencia: así un
# token inválido repetido no golpea la base en cada intento).
CACHE_TTL_SEGUNDOS = 30

_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}


def limpiar_cache() -> None:
    _cache.clear()


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
