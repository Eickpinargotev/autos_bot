"""Cola de envíos manuales creada desde el dashboard.

El dashboard solo inserta filas en estado `pendiente`; enviarlas es cosa del
bot, que es el proceso con los canales configurados. La comunicación por tabla
(y no por HTTP) hace que un reinicio de cualquiera de los dos no pierda ni
duplique envíos.

`tomar_pendientes` usa `FOR UPDATE SKIP LOCKED`: si algún día corren dos
workers, cada uno se lleva filas distintas sin bloquearse entre sí y sin que un
mismo envío salga dos veces.
"""

import random
from typing import Any

from src.infrastructure.repositories.postgres_conn import consultar, ejecutar

# Ritmo de una tanda: un mensaje cada ~15 segundos. El margen aleatorio es lo
# que importa: a intervalos exactos, cien mensajes puntuales cada quince
# segundos son la firma más obvia de un bot. Con un 60% de margen cada espera
# cae entre 6 y 24 segundos y el patrón desaparece.
RITMO_SEGUNDOS = 15
RITMO_MARGEN = 0.6


def _espera_hasta_el_siguiente() -> float:
    minimo = RITMO_SEGUNDOS * (1 - RITMO_MARGEN)
    maximo = RITMO_SEGUNDOS * (1 + RITMO_MARGEN)
    return random.uniform(minimo, maximo)


def tomar_pendientes(limite: int = 20) -> list[dict[str, Any]]:
    """Toma UN envío por sesión, y solo de las sesiones a las que les toca.

    Tres reglas, todas en la misma consulta para que sigan valiendo con varios
    workers:

    * **Una por sesión y por pasada** (`DISTINCT ON (lote_id)`). Si se tomaran
      veinte de la misma tanda, saldrían las veinte seguidas y el ritmo no
      existiría.
    * **Solo si ya toca** (`proximo_en <= NOW()`), y solo si la tanda ya empezó
      (`empieza_en <= NOW()`, que es lo que permite programarla para más tarde).
    * **Nada de tandas canceladas.**

    Los envíos sin sesión (los que se crearon antes de que existieran los lotes)
    siguen saliendo sin ritmo: no tienen dónde apuntarlo y son historia.

    Después de tomarlos se adelanta el reloj de cada sesión con una espera
    aleatoria. Se hace AQUÍ y no tras enviar a propósito: si el worker muere a
    mitad, la sesión ya tiene su pausa puesta y no se dispara una ráfaga al
    volver.
    """
    tomados = consultar(
        """
        WITH elegibles AS (
            -- `DISTINCT ON` y `FOR UPDATE` no se pueden combinar, así que
            -- primero se ELIGE (sin bloquear) y después se bloquea por id.
            --
            -- El COALESCE es lo que deja fuera del agrupado a los envíos sin
            -- sesión: `-id` nunca choca con un `lote_id` (que es positivo), así
            -- que cada uno queda en su propio grupo y siguen saliendo todos de
            -- una pasada, como siempre. Son de antes de que existieran los
            -- lotes y no tienen dónde apuntar un ritmo.
            SELECT DISTINCT ON (COALESCE(e.lote_id, -e.id)) e.id
            FROM envios e
            LEFT JOIN envios_lote l ON l.id = e.lote_id
            WHERE e.estado = 'pendiente'
              AND (
                    e.lote_id IS NULL
                    OR (NOT l.cancelado AND l.empieza_en <= NOW() AND l.proximo_en <= NOW())
                  )
            ORDER BY COALESCE(e.lote_id, -e.id), e.creado_en, e.id
            LIMIT %s
        ),
        tomados AS (
            SELECT e.id FROM envios e
            WHERE e.id IN (SELECT id FROM elegibles)
              -- Se vuelve a comprobar bajo el candado: entre elegir y bloquear,
              -- otro worker pudo llevárselo.
              AND e.estado = 'pendiente'
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

    for envio in tomados:
        if envio.get("lote_id"):
            ejecutar(
                "UPDATE envios_lote SET proximo_en = NOW() + (%s || ' seconds')::interval "
                "WHERE proyecto_id = %s AND id = %s",
                (str(_espera_hasta_el_siguiente()), envio["proyecto_id"], envio["lote_id"]),
            )
    return tomados


def marcar_parte_enviada(proyecto_id: int, envio_id: int, enviadas: int) -> None:
    """Deja constancia de hasta dónde llegó la cadena.

    Si el envío falla en la parte 3 de 5, al reintentar se retoma desde la 3 y
    no se le repiten al cliente las dos que ya recibió.
    """
    ejecutar(
        "UPDATE envios SET partes_enviadas = %s, actualizado_en = NOW() "
        "WHERE proyecto_id = %s AND id = %s",
        (int(enviadas), int(proyecto_id), envio_id),
    )


def marcar_enviado(proyecto_id: int, envio_id: int) -> None:
    ejecutar(
        """
        UPDATE envios
        SET estado = 'enviado', enviado_en = NOW(), actualizado_en = NOW(),
            error_cliente = '', error_tecnico = ''
        WHERE proyecto_id = %s AND id = %s
        """,
        (int(proyecto_id), envio_id),
    )


def marcar_error(
    proyecto_id: int, envio_id: int, mensaje_cliente: str, detalle_tecnico: str
) -> None:
    """Registra el fallo separando lo accionable de la traza.

    `error_cliente` es lo que ve quien hizo el envío y puede arreglar solo
    ("no se pudo abrir la imagen"); `error_tecnico` queda para el administrador.
    """
    ejecutar(
        """
        UPDATE envios
        SET estado = 'error', error_cliente = %s, error_tecnico = %s, actualizado_en = NOW()
        WHERE proyecto_id = %s AND id = %s
        """,
        (mensaje_cliente[:500], detalle_tecnico[:4000], int(proyecto_id), envio_id),
    )


def devolver_a_la_cola(proyecto_id: int, envio_id: int) -> None:
    """Regresa a 'pendiente' un envío que quedó a medias (p. ej. el worker murió)."""
    ejecutar(
        "UPDATE envios SET estado = 'pendiente', actualizado_en = NOW() "
        "WHERE proyecto_id = %s AND id = %s AND estado = 'enviando'",
        (int(proyecto_id), envio_id),
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


def purgar_lotes_vencidos(dias: int = 12) -> int:
    """Borra las sesiones de envío que cumplieron su plazo, con sus destinatarios.

    Es un histórico de trabajo, no un libro contable: pasado el plazo, a nadie le
    sirve saber que hace dos semanas un número no contestó. `uso_eventos` NO se
    toca — el consumo de esos envíos ya se facturó y el pasado no se recalcula.

    El plazo tiene que coincidir con `envios.RETENCION_DIAS` del dashboard, que
    es quien lo anuncia en pantalla.
    """
    return ejecutar(
        "DELETE FROM envios_lote WHERE creado_en < NOW() - (%s || ' days')::interval",
        (str(int(dias)),),
    )
