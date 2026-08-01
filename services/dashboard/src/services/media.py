"""Adjuntos de los mensajes: normalizar la referencia y comprobar que se puede abrir.

La comprobación se hace **al guardar el mensaje**, no al enviarlo. Un enlace mal
copiado o un archivo de Drive sin permiso público no fallan hasta el momento del
envío, y para entonces el cliente ya recibió un mensaje incompleto y el error
llega tarde. Descubriéndolo aquí, se arregla antes de que cueste algo.
"""

import re
from typing import Any

import httpx

# Formas en las que Google Drive presenta el mismo archivo. Se acepta cualquiera
# para no obligar a nadie a extraer el ID a mano.
_PATRONES_DRIVE = (
    re.compile(r"/file/d/([A-Za-z0-9_-]{10,})"),
    re.compile(r"[?&]id=([A-Za-z0-9_-]{10,})"),
    re.compile(r"/d/([A-Za-z0-9_-]{10,})"),
)

_PLANTILLA_DESCARGA = "https://drive.google.com/uc?export=download&id={id}"

# Tiempo máximo esperando a que responda el servidor del archivo. Generoso, pero
# acotado: el usuario está esperando en el formulario.
_TIMEOUT = 15.0

_TIPOS_ESPERADOS = {
    "imagen": ("image/",),
    "video": ("video/",),
}


def extraer_referencia(valor: str) -> str:
    """Normaliza lo que el usuario pegó a un ID de Drive o una URL directa.

    Acepta el enlace completo de Drive (en cualquiera de sus formatos), el ID
    suelto, o una URL de otro sitio, que se deja tal cual.
    """
    valor = (valor or "").strip()
    if not valor:
        return ""

    if "drive.google.com" in valor or "docs.google.com" in valor:
        for patron in _PATRONES_DRIVE:
            encontrado = patron.search(valor)
            if encontrado:
                return encontrado.group(1)
        # Es un enlace de Drive del que no se reconoce el ID: se devuelve tal
        # cual y la comprobación dirá que no se puede abrir.
        return valor

    return valor


def url_publica(referencia: str) -> str:
    """URL con la que se descarga el adjunto."""
    referencia = (referencia or "").strip()
    if not referencia:
        return ""
    if referencia.startswith("http://") or referencia.startswith("https://"):
        return referencia
    return _PLANTILLA_DESCARGA.format(id=referencia)


def verificar(referencia: str, tipo: str) -> tuple[bool, str]:
    """Intenta abrir el adjunto. Devuelve `(sirve, motivo_si_no_sirve)`.

    El motivo está escrito para quien creó el mensaje, no para un programador:
    tiene que poder arreglarlo sin ayuda.
    """
    referencia = (referencia or "").strip()
    if not referencia:
        return True, ""  # Un mensaje sin adjunto es válido.

    url = url_publica(referencia)
    try:
        # Se pide solo el principio del archivo: basta para saber si existe y de
        # qué tipo es, sin descargar un video entero.
        with httpx.stream(
            "GET", url, follow_redirects=True, timeout=_TIMEOUT, headers={"Range": "bytes=0-2048"}
        ) as respuesta:
            if respuesta.status_code in (401, 403):
                return False, (
                    "El archivo existe pero no es público. En Google Drive, ábrelo con "
                    "«Compartir» y ponlo como «Cualquier persona con el enlace»."
                )
            if respuesta.status_code == 404:
                return False, "No se encontró el archivo. Revisa que el enlace o el ID sea correcto."
            if respuesta.status_code >= 400:
                return False, f"El servidor del archivo respondió con un error ({respuesta.status_code})."

            content_type = (respuesta.headers.get("content-type") or "").split(";", 1)[0].lower()

            # Drive devuelve una página HTML cuando el archivo es privado o pide
            # confirmación por tamaño: parece un 200 correcto pero no es el archivo.
            if content_type.startswith("text/html"):
                return False, (
                    "El enlace no devuelve el archivo sino una página de Google. "
                    "Suele ser porque no es público o porque es demasiado grande para "
                    "descargarse directo."
                )

            esperados = _TIPOS_ESPERADOS.get(tipo)
            if esperados and content_type and not content_type.startswith(esperados):
                legible = "una imagen" if tipo == "imagen" else "un video"
                return False, f"El archivo no parece ser {legible} (es «{content_type}»)."

        return True, ""
    except httpx.TimeoutException:
        return False, "El archivo tardó demasiado en responder. Inténtalo de nuevo."
    except Exception as e:
        return False, f"No se pudo abrir el archivo: {e}"


def revisar_parte(texto: str, tipo: str, referencia: str) -> dict[str, Any]:
    """Normaliza y comprueba el adjunto de una parte de un mensaje."""
    tipo = tipo if tipo in ("imagen", "video") else ""
    referencia = extraer_referencia(referencia) if tipo else ""
    if not tipo or not referencia:
        return {
            "texto": texto,
            "media_tipo": tipo,
            "media_ref": referencia,
            "media_ok": None,
            "media_error": "",
        }

    ok, error = verificar(referencia, tipo)
    return {
        "texto": texto,
        "media_tipo": tipo,
        "media_ref": referencia,
        "media_ok": ok,
        "media_error": error,
    }
