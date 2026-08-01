"""Plantillas de mensaje y envíos manuales.

El dashboard solo ENCOLA envíos (estado `pendiente`); quien los manda es una
tarea Celery del bot, que es el proceso que tiene los canales configurados. Los
dos servicios se comunican por la tabla `envios`, no por HTTP: si cualquiera de
los dos se reinicia a medias, nada se pierde ni se envía dos veces.
"""

import json
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


def buscar_por_clave(clave: str) -> dict[str, Any] | None:
    plantilla = pool.consultar_uno(
        "SELECT * FROM plantillas_mensaje WHERE clave = %s", ((clave or "").strip().upper(),)
    )
    return obtener_plantilla(plantilla["id"]) if plantilla else None


def partes_de(plantilla_id: int) -> list[dict[str, Any]]:
    return pool.consultar(
        "SELECT * FROM plantilla_partes WHERE plantilla_id = %s ORDER BY orden", (plantilla_id,)
    )


def crear_plantilla(clave: str, nombre: str, usuario: str) -> dict[str, Any]:
    clave = (clave or "").strip().upper()
    if not clave:
        raise ValueError("El mensaje necesita una clave.")
    return pool.consultar_uno(
        "INSERT INTO plantillas_mensaje (clave, nombre, creado_por) VALUES (%s, %s, %s) RETURNING *",
        (clave, (nombre or clave).strip(), usuario),
    )


def renombrar_plantilla(plantilla_id: int, clave: str, nombre: str) -> dict[str, Any] | None:
    return pool.consultar_uno(
        """
        UPDATE plantillas_mensaje SET clave = %s, nombre = %s, actualizado_en = NOW()
        WHERE id = %s RETURNING *
        """,
        ((clave or "").strip().upper(), (nombre or "").strip(), plantilla_id),
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
    """Lo que impediría que este mensaje salga bien."""
    problemas = []
    partes = plantilla.get("partes") or []
    if not partes:
        problemas.append("No tiene ninguna parte que enviar.")
    for parte in partes:
        if not (parte["texto"] or "").strip() and not parte["media_ref"]:
            problemas.append(f"La parte {parte['orden']} está vacía.")
        if parte["media_ref"] and parte["media_ok"] is False:
            problemas.append(f"Parte {parte['orden']}: {parte['media_error']}")
    return problemas


def revisar_media_de(plantilla_id: int) -> list[dict[str, Any]]:
    """Vuelve a comprobar todos los adjuntos de un mensaje."""
    for parte in partes_de(plantilla_id):
        guardar_parte(
            plantilla_id, parte["orden"], parte["texto"], parte["media_tipo"], parte["media_ref"]
        )
    return partes_de(plantilla_id)


# --- Envíos ------------------------------------------------------------------

def encolar_envios(
    plantilla_id: int, canal: str, destinos: list[str], usuario: str
) -> list[dict[str, Any]]:
    """Crea un envío pendiente por destino, con la cadena completa de partes.

    Las partes se COPIAN al envío: editar el mensaje después no cambia lo que ya
    se encoló, así el histórico refleja lo que de verdad se mandó.
    """
    plantilla = obtener_plantilla(plantilla_id)
    if not plantilla:
        raise ValueError("El mensaje no existe.")
    if canal not in CANALES:
        raise ValueError(f"Canal desconocido: {canal}")

    problemas = plantilla["problemas"]
    if problemas:
        # Encolar algo que se sabe roto solo produce errores más tarde y un
        # cliente que ya recibió medio mensaje.
        raise ValueError(f"El mensaje «{plantilla['clave']}» tiene problemas: {problemas[0]}")

    partes = json.dumps(
        [
            {"texto": p["texto"], "media_tipo": p["media_tipo"], "media_ref": p["media_ref"]}
            for p in plantilla["partes"]
        ],
        ensure_ascii=False,
    )

    creados = []
    for destino in destinos:
        destino = destino.strip()
        if not destino:
            continue
        creados.append(
            pool.consultar_uno(
                """
                INSERT INTO envios (plantilla_id, partes, canal, destino_id, creado_por)
                VALUES (%s, %s::jsonb, %s, %s, %s)
                RETURNING *
                """,
                (plantilla["id"], partes, canal, destino, usuario),
            )
        )
    return creados


def listar_envios(limite: int = 200, incluir_tecnico: bool = False) -> list[dict[str, Any]]:
    """Histórico de envíos.

    `error_tecnico` solo se devuelve al administrador: al cliente se le muestra
    `error_cliente`, que es la parte accionable ("no se pudo abrir la imagen").
    """
    columnas = (
        "e.id, e.canal, e.destino_id, e.estado, e.intentos, e.error_cliente, "
        "e.creado_en, e.enviado_en, e.creado_por, e.partes_enviadas, "
        "jsonb_array_length(e.partes) AS total_partes, p.clave AS plantilla"
    )
    if incluir_tecnico:
        columnas += ", e.error_tecnico"
    return pool.consultar(
        f"""
        SELECT {columnas}
        FROM envios e
        LEFT JOIN plantillas_mensaje p ON p.id = e.plantilla_id
        ORDER BY e.creado_en DESC
        LIMIT %s
        """,
        (int(limite),),
    )


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


def eliminar_envio(envio_id: int) -> int:
    return pool.ejecutar("DELETE FROM envios WHERE id = %s", (envio_id,))


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
