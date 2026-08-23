"""Idempotencia de los mensajes que entrega el webhook de WhatsApp.

WasenderAPI puede volver a entregar un evento aunque la primera petición haya
terminado correctamente (o incluso entregarlo dos veces a la vez). Los comandos
como ``/d`` se responden dentro del webhook, antes del buffer, por lo que las
garantías del debounce de Celery no los protegen: sin esta guarda cada entrega
manda otra respuesta al cliente.

La reclamación es un ``SET NX`` atómico en Redis. Se hace antes de ejecutar los
efectos del mensaje y vive siete días, tiempo holgado para los reintentos del
proveedor sin conservar identificadores para siempre.
"""

import hashlib

from src.application.buffer_service import redis_client, scoped_key
from src.domain.entities import Channel


TTL_MENSAJE_SEGUNDOS = 7 * 24 * 60 * 60


def _clave(user_id: str, message_id: str) -> str:
    # El id viene de un tercero: se resume para que una entrada inesperadamente
    # larga no termine convertida en una clave de Redis igual de larga.
    huella = hashlib.sha256(str(message_id).encode("utf-8")).hexdigest()
    return f"{scoped_key('wa_entrada', Channel.WHATSAPP, user_id)}:{huella}"


def reclamar(user_id: str, message_id: str) -> bool:
    """Devuelve True solo para la primera entrega de ese mensaje.

    Los payloads antiguos o incompletos que no traen id siguen pasando: deducir
    identidad por texto borraría mensajes legítimos como dos ``hola`` seguidos.
    Si Redis está caído se prioriza no perder el mensaje y se procesa; el fallo
    queda visible en logs y la deduplicación vuelve sola al recuperarse Redis.
    """
    if not message_id:
        return True
    try:
        return bool(
            redis_client.set(
                _clave(user_id, message_id),
                "1",
                nx=True,
                ex=TTL_MENSAJE_SEGUNDOS,
            )
        )
    except Exception as exc:
        print(f"Error deduplicando mensaje entrante de WhatsApp: {exc}")
        return True
