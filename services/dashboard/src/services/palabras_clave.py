"""Palabras clave: lo que el cliente escribe para disparar un flujo entero.

No son mensajes de `plantillas_mensaje` aunque se parezcan. Un **mensaje** lo
mandas tú a quien elijas; una **palabra clave** la dispara el cliente
escribiéndola, y arrastra consigo dos cosas más: la conversación queda en manos
del dueño (el bot se calla) y se agendan unos recordatorios a futuro.

El match es EXACTO y por el mensaje entero: escribir «examen» dispara el flujo,
«tengo dudas del examen» no. Eso no contradice la regla de no interpretar
lenguaje natural con comparaciones de texto (ver CLAUDE.md §5): aquí no se
interpreta nada, se reconoce un disparador que el negocio anuncia tal cual.
"""

from typing import Any

from src.db import pool
from src.services import media

# Tope de un recordatorio, en minutos. NO es un número puesto al azar: el
# `visibility_timeout` de Celery en el bot es el doble del countdown más largo
# que se agenda, y una tarea cuyo countdown lo supere la re-entrega Redis — el
# cliente recibiría el mismo recordatorio una y otra vez. El bot reserva
# exactamente este margen; si aquí sube, allí también (ver celery_app.py).
MAX_MINUTOS = 20160  # 14 días


# --- Palabras -----------------------------------------------------------------

def listar() -> list[dict[str, Any]]:
    palabras = pool.consultar("SELECT * FROM palabras_clave ORDER BY palabra")
    for palabra in palabras:
        _completar(palabra)
    return palabras


def obtener(palabra_id: int) -> dict[str, Any] | None:
    palabra = pool.consultar_uno("SELECT * FROM palabras_clave WHERE id = %s", (int(palabra_id),))
    return _completar(palabra) if palabra else None


def _completar(palabra: dict[str, Any]) -> dict[str, Any]:
    palabra["mensajes"] = piezas_de(palabra["id"], "mensaje")
    palabra["recordatorios"] = piezas_de(palabra["id"], "recordatorio")
    palabra["problemas"] = problemas_de(palabra)
    return palabra


def _palabra_valida(palabra: str, excepto_id: int | None = None) -> str:
    """Normaliza y comprueba que no la tenga ya otra.

    Se guarda en minúsculas y sin espacios porque así es como se compara con lo
    que escribe el cliente. Dos palabras iguales competirían por el mismo mensaje
    entrante y ganaría la que la base devolviera primero.
    """
    palabra = " ".join(str(palabra or "").split()).lower()
    if not palabra:
        raise ValueError("La palabra clave no puede estar vacía.")
    if len(palabra) > 60:
        raise ValueError("La palabra clave es demasiado larga (máximo 60 caracteres).")

    otra = pool.consultar_uno(
        "SELECT id FROM palabras_clave WHERE lower(palabra) = %s", (palabra,)
    )
    if otra and otra["id"] != excepto_id:
        raise ValueError(f"Ya existe la palabra clave «{palabra}».")
    return palabra


def crear(palabra: str, usuario: str) -> dict[str, Any]:
    fila = pool.consultar_uno(
        "INSERT INTO palabras_clave (palabra, creado_por) VALUES (%s, %s) RETURNING *",
        (_palabra_valida(palabra), usuario),
    )
    # Nace con un mensaje vacío: una palabra clave sin nada que enviar no hace
    # nada, y así queda claro qué es lo siguiente que hay que rellenar.
    agregar_pieza(fila["id"], "mensaje")
    return obtener(fila["id"])


def renombrar(palabra_id: int, palabra: str) -> dict[str, Any] | None:
    return pool.consultar_uno(
        "UPDATE palabras_clave SET palabra = %s, actualizado_en = NOW() WHERE id = %s RETURNING *",
        (_palabra_valida(palabra, excepto_id=palabra_id), int(palabra_id)),
    )


def alternar_activa(palabra_id: int) -> dict[str, Any] | None:
    """Desactivarla la deja escrita pero el bot deja de reconocerla."""
    return pool.consultar_uno(
        "UPDATE palabras_clave SET activa = NOT activa, actualizado_en = NOW() "
        "WHERE id = %s RETURNING *",
        (int(palabra_id),),
    )


def eliminar(palabra_id: int) -> int:
    return pool.ejecutar("DELETE FROM palabras_clave WHERE id = %s", (int(palabra_id),))


# --- Piezas (mensajes y recordatorios) ----------------------------------------

def piezas_de(palabra_id: int, tipo: str) -> list[dict[str, Any]]:
    piezas = pool.consultar(
        "SELECT * FROM palabra_clave_piezas WHERE palabra_id = %s AND tipo = %s ORDER BY orden",
        (int(palabra_id), tipo),
    )
    for pieza in piezas:
        pieza["problema"] = problema_de_pieza(pieza)
    return piezas


def problema_de_pieza(pieza: dict[str, Any]) -> str:
    """Qué le impide a esta pieza salir bien. "" si está lista.

    Mismo criterio que en los mensajes del panel: verde significa «esto se puede
    enviar tal cual», no «alguien lo escribió».
    """
    texto = (pieza.get("texto") or "").strip()
    referencia = (pieza.get("media_ref") or "").strip()

    if not texto and not referencia:
        return "Está vacío: escribe el texto o pon un adjunto."
    if referencia and pieza.get("media_ok") is False:
        return pieza.get("media_error") or "El adjunto no se pudo abrir."
    if referencia and pieza.get("media_ok") is None:
        return "El adjunto no se ha comprobado todavía."
    return ""


def agregar_pieza(palabra_id: int, tipo: str) -> dict[str, Any]:
    """Añade un mensaje o un recordatorio al final.

    Un recordatorio nuevo se coloca DESPUÉS del último: los minutos tienen que
    ir creciendo, así que empezar por debajo del anterior sería crear algo
    inválido desde el primer momento.
    """
    fila = pool.consultar_uno(
        """
        SELECT COALESCE(MAX(orden), 0) + 1     AS siguiente,
               COALESCE(MAX(minutos), 0)       AS ultimo_minuto
        FROM palabra_clave_piezas WHERE palabra_id = %s AND tipo = %s
        """,
        (int(palabra_id), tipo),
    )
    minutos = None
    if tipo == "recordatorio":
        minutos = min(int(fila["ultimo_minuto"]) + 60, MAX_MINUTOS)

    return pool.consultar_uno(
        """
        INSERT INTO palabra_clave_piezas (palabra_id, tipo, orden, minutos)
        VALUES (%s, %s, %s, %s) RETURNING *
        """,
        (int(palabra_id), tipo, fila["siguiente"], minutos),
    )


def guardar_pieza(
    pieza_id: int,
    *,
    texto: str,
    media_tipo: str,
    media_ref: str,
    minutos: int | None = None,
    activo: bool = True,
) -> dict[str, Any]:
    """Guarda una pieza comprobando el adjunto ANTES de darla por buena.

    Igual que en los mensajes: el archivo de Drive se intenta abrir aquí, no al
    enviar. Se descarga solo para comprobar que existe y es público; no se
    guarda ninguna copia.
    """
    actual = pool.consultar_uno("SELECT * FROM palabra_clave_piezas WHERE id = %s", (int(pieza_id),))
    if not actual:
        raise ValueError("Esa pieza ya no existe.")

    if actual["tipo"] == "recordatorio":
        minutos = _minutos_validos(actual, minutos)
    else:
        minutos = None

    revisado = media.revisar_parte(texto, media_tipo, media_ref)
    return pool.consultar_uno(
        """
        UPDATE palabra_clave_piezas
        SET texto = %s, media_tipo = %s, media_ref = %s, media_ok = %s,
            media_error = %s, media_revisada_en = NOW(), minutos = %s, activo = %s
        WHERE id = %s
        RETURNING *
        """,
        (
            revisado["texto"],
            revisado["media_tipo"],
            revisado["media_ref"],
            revisado["media_ok"],
            revisado["media_error"],
            minutos,
            bool(activo),
            int(pieza_id),
        ),
    )


def _minutos_validos(pieza: dict[str, Any], minutos: int | None) -> int:
    """Los minutos de un recordatorio, comprobados contra sus vecinos.

    La regla es que cada recordatorio salga DESPUÉS del anterior. Se cuenta
    siempre desde que se disparó la palabra clave, no desde el recordatorio
    previo: en cascada, cambiar el de en medio obligaría a rehacer la cuenta de
    todos los siguientes.

    Sin esta comprobación, dos recordatorios con el mismo minuto le llegarían al
    cliente pegados uno detrás de otro sin ningún motivo aparente.
    """
    try:
        minutos = int(minutos)
    except (TypeError, ValueError):
        raise ValueError("Los minutos del recordatorio tienen que ser un número.") from None

    if minutos < 1:
        raise ValueError("Un recordatorio no puede salir antes de un minuto.")
    if minutos > MAX_MINUTOS:
        raise ValueError(
            f"El máximo son {MAX_MINUTOS} minutos ({MAX_MINUTOS // 1440} días). "
            "Más allá, el envío no se puede garantizar."
        )

    vecinos = pool.consultar(
        """
        SELECT orden, minutos FROM palabra_clave_piezas
        WHERE palabra_id = %s AND tipo = 'recordatorio' AND id <> %s
        ORDER BY orden
        """,
        (pieza["palabra_id"], pieza["id"]),
    )
    anterior = max(
        (v["minutos"] for v in vecinos if v["orden"] < pieza["orden"]), default=None
    )
    siguiente = min(
        (v["minutos"] for v in vecinos if v["orden"] > pieza["orden"]), default=None
    )

    if anterior is not None and minutos <= anterior:
        raise ValueError(
            f"El recordatorio {pieza['orden']} tiene que salir después del anterior, "
            f"que está en {anterior} minutos."
        )
    if siguiente is not None and minutos >= siguiente:
        raise ValueError(
            f"El recordatorio {pieza['orden']} tiene que salir antes del siguiente, "
            f"que está en {siguiente} minutos."
        )
    return minutos


def eliminar_pieza(pieza_id: int) -> int:
    return pool.ejecutar("DELETE FROM palabra_clave_piezas WHERE id = %s", (int(pieza_id),))


def revisar_media_de(palabra_id: int) -> None:
    """Vuelve a comprobar todos los adjuntos, por si cambió un permiso en Drive."""
    for tipo in ("mensaje", "recordatorio"):
        for pieza in piezas_de(palabra_id, tipo):
            guardar_pieza(
                pieza["id"],
                texto=pieza["texto"],
                media_tipo=pieza["media_tipo"],
                media_ref=pieza["media_ref"],
                minutos=pieza["minutos"],
                activo=pieza["activo"],
            )


def problemas_de(palabra: dict[str, Any]) -> list[str]:
    """Lo que impediría que esta palabra clave funcione bien."""
    problemas = []
    if not palabra.get("mensajes"):
        problemas.append("No tiene ningún mensaje que enviar al dispararse.")

    for pieza in palabra.get("mensajes") or []:
        if pieza["problema"]:
            problemas.append(f"Mensaje {pieza['orden']}: {pieza['problema']}")
    for pieza in palabra.get("recordatorios") or []:
        if pieza["activo"] and pieza["problema"]:
            problemas.append(f"Recordatorio {pieza['orden']}: {pieza['problema']}")
    return problemas


def como_texto(minutos: int | None) -> str:
    """«90» → «1 h 30 min». Los minutos son la unidad; esto solo los hace legibles."""
    if not minutos:
        return "—"
    dias, resto = divmod(int(minutos), 1440)
    horas, mins = divmod(resto, 60)
    partes = []
    if dias:
        partes.append(f"{dias} d")
    if horas:
        partes.append(f"{horas} h")
    if mins or not partes:
        partes.append(f"{mins} min")
    return " ".join(partes)
