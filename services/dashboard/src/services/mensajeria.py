"""Plantillas de mensaje y envíos manuales.

El dashboard solo ENCOLA envíos (estado `pendiente`); quien los manda es una
tarea Celery del bot, que es el proceso que tiene los canales configurados. Los
dos servicios se comunican por la tabla `envios`, no por HTTP: si cualquiera de
los dos se reinicia a medias, nada se pierde ni se envía dos veces.
"""

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from src.db import pool
from src.services import media

MAX_INTENTOS = 3
CANALES = ("telegram", "whatsapp")


# --- Plantillas (mensajes en cadena) -----------------------------------------
#
# Un "mensaje" del panel puede ser en realidad varios mensajes que salen uno tras
# otro. La plantilla es la cabecera (clave + nombre) y las partes son las piezas
# ordenadas que se envían.

def listar_plantillas() -> list[dict[str, Any]]:
    plantillas = pool.consultar("SELECT * FROM plantillas_mensaje ORDER BY clave")
    for plantilla in plantillas:
        plantilla["partes"] = partes_de(plantilla["id"])
        plantilla["problemas"] = problemas_de(plantilla)
    return plantillas


def obtener_plantilla(plantilla_id: int) -> dict[str, Any] | None:
    plantilla = pool.consultar_uno("SELECT * FROM plantillas_mensaje WHERE id = %s", (plantilla_id,))
    if plantilla:
        plantilla["partes"] = partes_de(plantilla_id)
        plantilla["problemas"] = problemas_de(plantilla)
    return plantilla


def problema_de_parte(parte: dict[str, Any]) -> str:
    """Qué le impide a ESTE mensaje salir bien. "" si está listo.

    Es lo que decide que el botoncito del mensaje se vea en verde: verde =
    «esto se puede enviar tal cual». Un mensaje de solo texto está listo sin más;
    uno con adjunto lo está cuando el archivo se pudo abrir de verdad.
    """
    texto = (parte.get("texto") or "").strip()
    referencia = (parte.get("media_ref") or "").strip()

    if not texto and not referencia:
        return "Está vacío: escribe el texto o pon un adjunto."
    if referencia and parte.get("media_ok") is False:
        return parte.get("media_error") or "El adjunto no se pudo abrir."
    if referencia and parte.get("media_ok") is None:
        # No debería pasar por el panel (se comprueba en cada guardado), pero una
        # fila cargada a mano sí puede quedar así. Sin adjunto comprobado no se
        # promete que el envío vaya a salir bien.
        return "El adjunto no se ha comprobado todavía."
    return ""


def parte_lista(parte: dict[str, Any]) -> bool:
    return not problema_de_parte(parte)


def buscar_por_clave(clave: str) -> dict[str, Any] | None:
    plantilla = pool.consultar_uno(
        "SELECT * FROM plantillas_mensaje WHERE clave = %s", ((clave or "").strip().upper(),)
    )
    return obtener_plantilla(plantilla["id"]) if plantilla else None


def partes_de(plantilla_id: int) -> list[dict[str, Any]]:
    """Los mensajes de la cadena, en orden y cada uno con su estado.

    `problema` viene resuelto desde aquí y no se calcula en la plantilla: es lo
    que decide el color del botoncito, y una regla de negocio no se escribe en
    Jinja.
    """
    partes = pool.consultar(
        "SELECT * FROM plantilla_partes WHERE plantilla_id = %s ORDER BY orden", (plantilla_id,)
    )
    for parte in partes:
        parte["problema"] = problema_de_parte(parte)
    return partes


def _clave_valida(clave: str, excepto_id: int | None = None) -> str:
    """Normaliza la clave y comprueba que no la tenga ya otro mensaje.

    La clave es lo ÚNICO que identifica un mensaje: es lo que se elige al
    enviarlo y lo que el bot busca. Dos iguales harían que «enviar ALAJUELA»
    dependa de cuál encuentre primero, así que se rechaza aquí además de estar
    el índice único en la base — así el usuario lee un motivo en vez de un 500.

    Se guarda en mayúsculas para que «alajuela» y «ALAJUELA» sean la misma y no
    dos que chocan al enviar.
    """
    clave = (clave or "").strip().upper()
    if not clave:
        raise ValueError("El mensaje necesita una clave.")

    otra = pool.consultar_uno("SELECT id FROM plantillas_mensaje WHERE clave = %s", (clave,))
    if otra and otra["id"] != excepto_id:
        raise ValueError(f"Ya existe un mensaje con la clave «{clave}».")
    return clave


def crear_plantilla(clave: str, usuario: str) -> dict[str, Any]:
    return pool.consultar_uno(
        "INSERT INTO plantillas_mensaje (clave, creado_por) VALUES (%s, %s) RETURNING *",
        (_clave_valida(clave), usuario),
    )


def renombrar_plantilla(plantilla_id: int, clave: str) -> dict[str, Any] | None:
    """Cambia la clave. La unicidad se comprueba TAMBIÉN aquí.

    Solo se comprobaba al crear: renombrar un mensaje a una clave que ya existía
    llegaba al índice único de la base y salía un 500 sin explicación.
    """
    return pool.consultar_uno(
        "UPDATE plantillas_mensaje SET clave = %s, actualizado_en = NOW() WHERE id = %s RETURNING *",
        (_clave_valida(clave, excepto_id=plantilla_id), plantilla_id),
    )


def eliminar_plantilla(plantilla_id: int) -> int:
    return pool.ejecutar("DELETE FROM plantillas_mensaje WHERE id = %s", (plantilla_id,))


def guardar_parte(
    plantilla_id: int, orden: int, texto: str, media_tipo: str, media_ref: str
) -> dict[str, Any]:
    """Crea o actualiza una parte, comprobando el adjunto antes de guardarlo.

    La comprobación ocurre AQUÍ y no al enviar: así un enlace roto o un archivo
    de Drive sin permiso público se descubre mientras se escribe el mensaje, y
    no cuando el cliente ya recibió medio mensaje.
    """
    revisado = media.revisar_parte(texto, media_tipo, media_ref)
    return pool.consultar_uno(
        """
        INSERT INTO plantilla_partes (
            plantilla_id, orden, texto, media_tipo, media_ref, media_ok, media_error, media_revisada_en
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (plantilla_id, orden) DO UPDATE SET
            texto = EXCLUDED.texto,
            media_tipo = EXCLUDED.media_tipo,
            media_ref = EXCLUDED.media_ref,
            media_ok = EXCLUDED.media_ok,
            media_error = EXCLUDED.media_error,
            media_revisada_en = NOW()
        RETURNING *
        """,
        (
            plantilla_id,
            int(orden),
            revisado["texto"],
            revisado["media_tipo"],
            revisado["media_ref"],
            revisado["media_ok"],
            revisado["media_error"],
        ),
    )


def agregar_parte(plantilla_id: int) -> dict[str, Any]:
    fila = pool.consultar_uno(
        "SELECT COALESCE(MAX(orden), 0) + 1 AS siguiente FROM plantilla_partes WHERE plantilla_id = %s",
        (plantilla_id,),
    )
    return guardar_parte(plantilla_id, fila["siguiente"], "", "", "")


def eliminar_parte(parte_id: int) -> int:
    return pool.ejecutar("DELETE FROM plantilla_partes WHERE id = %s", (parte_id,))


def problemas_de(plantilla: dict[str, Any]) -> list[str]:
    """Lo que impediría que esta cadena salga bien, mensaje por mensaje."""
    partes = plantilla.get("partes") or []
    if not partes:
        return ["No tiene ningún mensaje que enviar."]
    return [
        f"Mensaje {parte['orden']}: {problema}"
        for parte in partes
        if (problema := problema_de_parte(parte))
    ]


def revisar_media_de(plantilla_id: int) -> list[dict[str, Any]]:
    """Vuelve a comprobar todos los adjuntos de un mensaje."""
    for parte in partes_de(plantilla_id):
        guardar_parte(
            plantilla_id, parte["orden"], parte["texto"], parte["media_tipo"], parte["media_ref"]
        )
    return partes_de(plantilla_id)


# Cuántos adjuntos se comprueban a la vez en la revisión general. Cada uno es una
# petición a un servidor ajeno (Drive casi siempre): en fila india, setenta
# adjuntos son setenta esperas seguidas y quien pulsó el botón se queda mirando
# la pantalla. En paralelo son unos segundos. El número es bajo a propósito: no
# se trata de exprimir a Drive, sino de no ir de uno en uno.
REVISION_EN_PARALELO = 8


def revisar_todos_los_adjuntos() -> tuple[int, int]:
    """Comprueba de una pasada los adjuntos de TODO el catálogo.

    Existe porque el estado del adjunto no depende solo de nosotros: un archivo
    de Drive al que le quitan el permiso público sigue guardado igual y solo se
    descubre volviendo a mirarlo. Mensaje por mensaje eso son decenas de clics.

    Solo se tocan las columnas del adjunto —nunca el texto—, así que revisar no
    puede estropear lo que alguien esté escribiendo. Devuelve
    `(revisados, con_problema)`.
    """
    partes = pool.consultar(
        "SELECT id, media_tipo, media_ref FROM plantilla_partes WHERE COALESCE(media_ref, '') <> ''"
    )
    if not partes:
        return 0, 0

    def comprobar(parte: dict[str, Any]) -> tuple[int, bool, str]:
        ok, error = media.verificar(parte["media_ref"], parte["media_tipo"])
        return parte["id"], ok, error

    con_problema = 0
    with ThreadPoolExecutor(max_workers=REVISION_EN_PARALELO) as ejecutor:
        # El resultado se escribe desde este hilo, no desde los del pool: lo que
        # se reparte es la espera de la red, no el acceso a la base.
        for parte_id, ok, error in ejecutor.map(comprobar, partes):
            pool.ejecutar(
                """
                UPDATE plantilla_partes
                SET media_ok = %s, media_error = %s, media_revisada_en = NOW()
                WHERE id = %s
                """,
                (ok, error, parte_id),
            )
            if not ok:
                con_problema += 1

    return len(partes), con_problema


# --- Envíos ------------------------------------------------------------------

# `encolar_envios` y `listar_envios` vivían aquí y se los llevó
# `services/envios.py`: un envío ya no es «una plantilla y unos destinos»
# sino una SESIÓN, con su origen (mensaje, palabra clave o ciudad), su ritmo
# y su progreso. Aquí queda lo que sigue siendo de un envío suelto:
# reintentarlo, reportarlo y las incidencias.


def obtener_envio(envio_id: int) -> dict[str, Any] | None:
    return pool.consultar_uno("SELECT * FROM envios WHERE id = %s", (envio_id,))


def reintentar(envio_id: int) -> tuple[bool, str]:
    """Devuelve un envío fallido a la cola, si le quedan intentos.

    El tope de 3 se aplica en el UPDATE, no antes: dos clics rápidos no pueden
    colar un cuarto intento.
    """
    fila = pool.consultar_uno(
        """
        UPDATE envios
        SET estado = 'pendiente', error_cliente = '', error_tecnico = '', actualizado_en = NOW()
        WHERE id = %s AND estado = 'error' AND intentos < %s
        RETURNING *
        """,
        (envio_id, MAX_INTENTOS),
    )
    if fila:
        return True, "Reintento encolado."

    actual = obtener_envio(envio_id)
    if not actual:
        return False, "Ese envío ya no existe."
    if actual["estado"] != "error":
        return False, f"No se puede reintentar un envío en estado '{actual['estado']}'."
    return False, f"Ya se agotaron los {MAX_INTENTOS} intentos. Repórtalo o elimínalo."


def reportar(envio_id: int, usuario: str) -> tuple[bool, str]:
    """Escala un envío fallido al administrador y lo deja "en revisión"."""
    envio = obtener_envio(envio_id)
    if not envio:
        return False, "Ese envío ya no existe."
    if envio["estado"] != "error":
        return False, "Solo se reportan los envíos con error."

    pool.ejecutar(
        """
        INSERT INTO incidencias (envio_id, reportado_por, detalle)
        VALUES (%s, %s, %s::jsonb)
        """,
        (
            envio_id,
            usuario,
            _detalle_incidencia(envio),
        ),
    )
    pool.ejecutar(
        "UPDATE envios SET estado = 'en_revision', actualizado_en = NOW() WHERE id = %s",
        (envio_id,),
    )
    return True, "Reportado. El administrador ya lo puede revisar."


def _detalle_incidencia(envio: dict[str, Any]) -> str:
    return json.dumps(
        {
            "canal": envio.get("canal"),
            "destino_id": envio.get("destino_id"),
            "intentos": envio.get("intentos"),
            "error_cliente": envio.get("error_cliente"),
            "error_tecnico": envio.get("error_tecnico"),
            "partes": envio.get("partes"),
            "partes_enviadas": envio.get("partes_enviadas"),
            "creado_por": envio.get("creado_por"),
            "creado_en": str(envio.get("creado_en") or ""),
        },
        ensure_ascii=False,
    )


# --- Incidencias (bandeja del administrador) ---------------------------------

def listar_incidencias(solo_abiertas: bool = False) -> list[dict[str, Any]]:
    where = "WHERE estado = 'abierta'" if solo_abiertas else ""
    return pool.consultar(f"SELECT * FROM incidencias {where} ORDER BY creado_en DESC LIMIT 200")


def marcar_incidencia_revisada(incidencia_id: int, nota: str) -> int:
    return pool.ejecutar(
        """
        UPDATE incidencias
        SET estado = 'revisada', nota_admin = %s, revisado_en = NOW()
        WHERE id = %s
        """,
        (nota, incidencia_id),
    )


def contar_incidencias_abiertas() -> int:
    fila = pool.consultar_uno("SELECT COUNT(*) AS total FROM incidencias WHERE estado = 'abierta'")
    return int((fila or {}).get("total") or 0)
