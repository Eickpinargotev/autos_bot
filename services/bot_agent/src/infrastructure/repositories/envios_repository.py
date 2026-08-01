"""Cola de envíos manuales creada desde el dashboard.

El dashboard solo inserta filas en estado `pendiente`; enviarlas es cosa del
bot, que es el proceso con los canales configurados. La comunicación por tabla
(y no por HTTP) hace que un reinicio de cualquiera de los dos no pierda ni
duplique envíos.

`tomar_pendientes` usa `FOR UPDATE SKIP LOCKED`: si algún día corren dos
workers, cada uno se lleva filas distintas sin bloquearse entre sí y sin que un
mismo envío salga dos veces.
"""

from typing import Any

from src.infrastructure.repositories.postgres_conn import consultar, ejecutar


def tomar_pendientes(limite: int = 20) -> list[dict[str, Any]]:
    """Marca como 'enviando' y devuelve los envíos pendientes más antiguos."""
    return consultar(
        """
        WITH tomados AS (
            SELECT id FROM envios
            WHERE estado = 'pendiente'
            ORDER BY creado_en
            LIMIT %s
            FOR UPDATE SKIP LOCKED
        )
        UPDATE envios e
        SET estado = 'enviando', intentos = e.intentos + 1, actualizado_en = NOW()
        FROM tomados
        WHERE e.id = tomados.id
        RETURNING e.*
        """,
        (int(limite),),
    )


def marcar_parte_enviada(envio_id: int, enviadas: int) -> None:
    """Deja constancia de hasta dónde llegó la cadena.

    Si el envío falla en la parte 3 de 5, al reintentar se retoma desde la 3 y
    no se le repiten al cliente las dos que ya recibió.
    """
    ejecutar(
        "UPDATE envios SET partes_enviadas = %s, actualizado_en = NOW() WHERE id = %s",
        (int(enviadas), envio_id),
    )


def marcar_enviado(envio_id: int) -> None:
    ejecutar(
        """
        UPDATE envios
        SET estado = 'enviado', enviado_en = NOW(), actualizado_en = NOW(),
            error_cliente = '', error_tecnico = ''
        WHERE id = %s
        """,
        (envio_id,),
    )


def marcar_error(envio_id: int, mensaje_cliente: str, detalle_tecnico: str) -> None:
    """Registra el fallo separando lo accionable de la traza.

    `error_cliente` es lo que ve quien hizo el envío y puede arreglar solo
    ("no se pudo abrir la imagen"); `error_tecnico` queda para el administrador.
    """
    ejecutar(
        """
        UPDATE envios
        SET estado = 'error', error_cliente = %s, error_tecnico = %s, actualizado_en = NOW()
        WHERE id = %s
        """,
        (mensaje_cliente[:500], detalle_tecnico[:4000], envio_id),
    )


def devolver_a_la_cola(envio_id: int) -> None:
    """Regresa a 'pendiente' un envío que quedó a medias (p. ej. el worker murió)."""
    ejecutar(
        "UPDATE envios SET estado = 'pendiente', actualizado_en = NOW() WHERE id = %s AND estado = 'enviando'",
        (envio_id,),
    )


def rescatar_atascados(minutos: int = 5) -> int:
    """Devuelve a la cola los envíos que llevan demasiado en 'enviando'.

    Solo puede pasar si el worker murió justo después de tomarlos. Sin esto se
    quedarían atascados para siempre en un estado que la interfaz no deja tocar.
    """
    return ejecutar(
        """
        UPDATE envios
        SET estado = 'pendiente', actualizado_en = NOW()
        WHERE estado = 'enviando' AND actualizado_en < NOW() - (%s || ' minutes')::interval
        """,
        (str(int(minutos)),),
    )
