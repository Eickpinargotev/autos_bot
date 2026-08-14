"""Sesiones de envío: preparar una tanda, verla avanzar y saber qué falló.

El dashboard solo ENCOLA; quien manda es una tarea del bot, que es el proceso
con los canales configurados. Se comunican por la tabla y no por HTTP: si
cualquiera de los dos se reinicia a medias, no se pierde ni se duplica nada.

Lo que aporta esta capa sobre la tabla `envios` es la **sesión**: la unidad que
le importa a quien envía. Cien filas sueltas no contestan «¿cómo va lo que mandé
hace un rato?»; una sesión con su progreso y su lista de fallos, sí.
"""

import json
from typing import Any

from src.db import pool
from src.services import mensajeria, palabras_clave

# Cuánto se queda una sesión antes de borrarse sola. Es un histórico de trabajo,
# no un libro contable: pasado ese plazo, a nadie le sirve saber que hace dos
# semanas un número no contestó. (`uso_eventos` sí guarda el consumo para
# siempre, y eso no se toca.)
RETENCION_DIAS = 12

CATEGORIAS = {
    "mensaje": "Mensajes",
    "palabra_clave": "Palabras clave",
}


# --- Qué se puede enviar ------------------------------------------------------

def opciones(categoria: str) -> list[dict[str, Any]]:
    """Lo que hay para elegir dentro de una categoría, con sus problemas.

    Los dos orígenes viven en tablas distintas y se leen distinto, pero desde
    aquí se ven iguales: id, etiqueta y qué le falta para poder enviarse. La
    pantalla no tiene por qué saber de dónde sale cada uno. Y como son tablas
    distintas, **sus ids colisionan**: el id 1 existe en las dos, así que nada
    se resuelve por el id suelto sino por `categoria` + `referencia_id`.
    """
    if categoria == "mensaje":
        return [
            {"id": p["id"], "etiqueta": p["clave"], "piezas": len(p["partes"]), "problemas": p["problemas"]}
            for p in mensajeria.listar_plantillas()
        ]

    if categoria == "palabra_clave":
        # De una palabra clave se manda lo que sale AL INSTANTE. Los
        # recordatorios no: son una consecuencia de que el cliente la escriba,
        # y mandarlos a mano no tendría a qué recordar.
        return [
            {
                "id": p["id"],
                "etiqueta": p["palabra"],
                "piezas": len(p["mensajes"]),
                "problemas": p["problemas"],
            }
            for p in palabras_clave.listar()
        ]

    raise ValueError(f"Categoría desconocida: {categoria}")


def _contenido(categoria: str, referencia_id: int) -> tuple[str, list[dict[str, Any]]]:
    """(etiqueta, partes) de lo que se va a mandar, ya validado.

    Las partes se COPIAN a cada envío: editar el mensaje después no cambia lo
    que ya se encoló, así el histórico refleja lo que de verdad salió.
    """
    if categoria == "mensaje":
        plantilla = mensajeria.obtener_plantilla(referencia_id)
        if not plantilla:
            raise ValueError("Ese mensaje ya no existe.")
        _sin_problemas(plantilla["clave"], plantilla["problemas"])
        return plantilla["clave"], [
            {"texto": p["texto"], "media_tipo": p["media_tipo"], "media_ref": p["media_ref"]}
            for p in plantilla["partes"]
        ]

    if categoria == "palabra_clave":
        palabra = palabras_clave.obtener(referencia_id)
        if not palabra:
            raise ValueError("Esa palabra clave ya no existe.")
        # Solo se miran los MENSAJES, no los recordatorios: aquí se manda lo que
        # sale al instante, y un recordatorio a medio escribir no tiene por qué
        # impedirlo — el envío a mano no agenda ninguno.
        _sin_problemas(
            palabra["palabra"],
            [f"Mensaje {p['orden']}: {p['problema']}" for p in palabra["mensajes"] if p["problema"]]
            or ([] if palabra["mensajes"] else ["no tiene ningún mensaje que enviar."]),
        )
        return palabra["palabra"], [
            {"texto": p["texto"], "media_tipo": p["media_tipo"], "media_ref": p["media_ref"]}
            for p in palabra["mensajes"]
        ]

    raise ValueError(f"Categoría desconocida: {categoria}")


def _sin_problemas(etiqueta: str, problemas: list[str]) -> None:
    """Encolar algo que se sabe roto solo produce un cliente a medio atender."""
    if problemas:
        raise ValueError(f"«{etiqueta}» tiene algo que revisar: {problemas[0]}")


# --- Los destinatarios --------------------------------------------------------

def numeros(texto: str) -> tuple[list[str], list[str]]:
    """Separa los números escritos uno por línea. Devuelve (válidos, rechazados).

    Se aceptan también las comas por comodidad, y se quitan espacios, guiones y
    paréntesis: la gente pega los números como los tiene.

    Lo que NO se acepta es un número sin código de país. En WhatsApp el
    destinatario ES el número completo: sin código, el mensaje o no sale o sale
    a otra persona en otro país. Ocho dígitos es un número local de Costa Rica;
    se pide desde diez para arriba, que es lo mínimo con código.
    """
    validos: list[str] = []
    rechazados: list[str] = []
    vistos: set[str] = set()

    for linea in str(texto or "").replace(",", "\n").splitlines():
        crudo = linea.strip()
        if not crudo:
            continue
        digitos = "".join(c for c in crudo if c.isdigit())
        if len(digitos) < 10:
            rechazados.append(crudo)
            continue
        # Un número repetido en la lista recibiría el mensaje dos veces.
        if digitos in vistos:
            continue
        vistos.add(digitos)
        validos.append(digitos)

    return validos, rechazados


# --- Crear la sesión ----------------------------------------------------------

def crear_lote(
    *,
    categoria: str,
    referencia_id: int,
    canal: str,
    destinos: list[str],
    usuario: str,
    empieza_en: Any = None,
) -> dict[str, Any]:
    """Deja la tanda en cola. El bot la va soltando a su ritmo.

    `empieza_en` permite programarla para más tarde; vacío significa ahora. El
    ritmo entre mensajes lo pone el bot al ir tomándolos (ver `envios_repository`
    allí): aquí solo se marca desde cuándo puede empezar.
    """
    if canal not in mensajeria.CANALES:
        raise ValueError(f"Canal desconocido: {canal}")
    if not destinos:
        raise ValueError("No hay ningún número al que enviar.")

    etiqueta, partes = _contenido(categoria, referencia_id)
    lote = pool.consultar_uno(
        """
        INSERT INTO envios_lote (categoria, referencia_id, etiqueta, canal, creado_por,
                                 empieza_en, proximo_en)
        VALUES (%s, %s, %s, %s, %s, COALESCE(%s, NOW()), COALESCE(%s, NOW()))
        RETURNING *
        """,
        (categoria, referencia_id, etiqueta[:120], canal, usuario, empieza_en, empieza_en),
    )

    contenido = json.dumps(partes, ensure_ascii=False)
    plantilla_id = referencia_id if categoria == "mensaje" else None
    for destino in destinos:
        pool.ejecutar(
            """
            INSERT INTO envios (lote_id, plantilla_id, partes, canal, destino_id, creado_por)
            VALUES (%s, %s, %s::jsonb, %s, %s, %s)
            """,
            (lote["id"], plantilla_id, contenido, canal, destino, usuario),
        )
    return lote


# --- Ver cómo van -------------------------------------------------------------

def listar_lotes(limite: int = 60) -> list[dict[str, Any]]:
    """Las sesiones, las que están en marcha primero.

    El orden es «en marcha» y después por fecha descendente. Eso ya cumple lo
    que hace falta: una tanda de hace dos horas queda por encima de una de
    ayer, y las viejas se van al fondo solas sin ninguna regla aparte.
    """
    return pool.consultar(
        """
        SELECT l.*,
               COUNT(e.id)                                      AS total,
               COUNT(e.id) FILTER (WHERE e.estado = 'enviado')   AS enviados,
               COUNT(e.id) FILTER (WHERE e.estado = 'error')     AS errores,
               COUNT(e.id) FILTER (WHERE e.estado IN ('pendiente', 'enviando')) AS quedan,
               MAX(e.enviado_en)                                 AS ultimo_envio,
               -- «Programada» solo si se pidió para MÁS TARDE de verdad. Sin el
               -- minuto de margen, cualquier diferencia de reloj entre las dos
               -- columnas haría que una tanda inmediata dijera que está
               -- programada para la hora a la que ya está saliendo.
               (l.empieza_en > l.creado_en + INTERVAL '1 minute')  AS programada
        FROM envios_lote l
        LEFT JOIN envios e ON e.lote_id = l.id
        GROUP BY l.id
        ORDER BY (COUNT(e.id) FILTER (WHERE e.estado IN ('pendiente', 'enviando')) > 0
                  AND NOT l.cancelado) DESC,
                 l.creado_en DESC
        LIMIT %s
        """,
        (int(limite),),
    )


def con_progreso(lote: dict[str, Any]) -> dict[str, Any]:
    """Añade el porcentaje y el estado legible de una sesión."""
    total = int(lote.get("total") or 0)
    hechos = int(lote.get("enviados") or 0) + int(lote.get("errores") or 0)
    lote["porcentaje"] = round(hechos * 100 / total) if total else 0
    lote["terminado"] = total > 0 and hechos >= total

    if lote.get("cancelado"):
        lote["estado"] = "cancelada"
    elif lote["terminado"]:
        lote["estado"] = "terminada"
    elif total == 0:
        lote["estado"] = "vacía"
    else:
        lote["estado"] = "en curso"
    return lote


def obtener_lote(lote_id: int) -> dict[str, Any] | None:
    filas = pool.consultar(
        """
        SELECT l.*,
               COUNT(e.id)                                      AS total,
               COUNT(e.id) FILTER (WHERE e.estado = 'enviado')   AS enviados,
               COUNT(e.id) FILTER (WHERE e.estado = 'error')     AS errores,
               COUNT(e.id) FILTER (WHERE e.estado IN ('pendiente', 'enviando')) AS quedan,
               MAX(e.enviado_en)                                 AS ultimo_envio,
               -- «Programada» solo si se pidió para MÁS TARDE de verdad. Sin el
               -- minuto de margen, cualquier diferencia de reloj entre las dos
               -- columnas haría que una tanda inmediata dijera que está
               -- programada para la hora a la que ya está saliendo.
               (l.empieza_en > l.creado_en + INTERVAL '1 minute')  AS programada
        FROM envios_lote l
        LEFT JOIN envios e ON e.lote_id = l.id
        WHERE l.id = %s
        GROUP BY l.id
        """,
        (int(lote_id),),
    )
    return con_progreso(filas[0]) if filas else None


def destinos_de(lote_id: int, solo_fallidos: bool = False) -> list[dict[str, Any]]:
    """Los números de una sesión, con lo que pasó con cada uno.

    Se puede mirar EN CUALQUIER MOMENTO, no solo al terminar: si de los primeros
    veinte fallan quince, más vale enterarse antes de que salgan los ochenta
    restantes.
    """
    where = "AND estado = 'error'" if solo_fallidos else ""
    return pool.consultar(
        f"""
        SELECT id, destino_id, estado, intentos, error_cliente, enviado_en,
               partes_enviadas, jsonb_array_length(partes) AS total_partes
        FROM envios
        WHERE lote_id = %s {where}
        ORDER BY (estado = 'error') DESC, id
        """,
        (int(lote_id),),
    )


def cancelar(lote_id: int) -> int:
    """Para lo que queda por salir. Lo ya enviado no se puede deshacer."""
    pool.ejecutar("UPDATE envios_lote SET cancelado = TRUE WHERE id = %s", (int(lote_id),))
    return pool.ejecutar(
        "DELETE FROM envios WHERE lote_id = %s AND estado = 'pendiente'", (int(lote_id),)
    )


def eliminar_lote(lote_id: int) -> int:
    return pool.ejecutar("DELETE FROM envios_lote WHERE id = %s", (int(lote_id),))


def purgar_lotes_vencidos(dias: int = RETENCION_DIAS) -> int:
    """Borra las sesiones que ya cumplieron su plazo, con sus envíos.

    NO toca `uso_eventos`: el consumo de esos envíos ya se facturó y el pasado
    no se recalcula.
    """
    return pool.ejecutar(
        "DELETE FROM envios_lote WHERE creado_en < NOW() - (%s || ' days')::interval",
        (int(dias),),
    )
