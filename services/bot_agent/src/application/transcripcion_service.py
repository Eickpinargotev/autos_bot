"""Convierte una nota de voz en texto, y deja SOLO el texto.

Regla del producto: el audio no se guarda en ninguna parte. Ni en disco, ni en la
base, ni en el historial. Se descarga a memoria, se transcribe y se descarta; lo
único que sobrevive es la transcripción, que entra al historial como si el
cliente lo hubiera escrito. Guardar el binario ensuciaría la base con datos que
nadie va a volver a oír y que además son personales.

El recorrido tiene tres tramos, y ninguno es evitable:

1. WhatsApp entrega la media CIFRADA de extremo a extremo: el `url` del evento
   no se puede descargar sin la `mediaKey`. WasenderAPI expone
   `POST /api/decrypt-media`, que la descifra y devuelve una `publicUrl`
   temporal (vive una hora).
2. Se descarga esa URL a memoria.
3. Se transcribe con el modelo de `OPENAI_MODEL_TRANSCRIPCION`.

Nada de esto puede correr dentro del webhook: descifrar + descargar + transcribir
tarda segundos, y WasenderAPI reintentaría el evento creyendo que se cayó. Por
eso lo dispara una tarea de Celery (ver `celery_app.transcribir_nota_de_voz`).
"""

import io
from dataclasses import dataclass
from typing import Any

import httpx

from src.core.config import settings
from src.infrastructure.channels import wasender

# Tipos de media que son voz. `pttMessage` es la nota de voz clásica;
# `audioMessage` cubre tanto la nota de voz nueva (con `ptt: true`) como un
# archivo de audio adjunto. Los dos se transcriben igual.
_CLAVES_AUDIO = ("audioMessage", "pttMessage")

# Tope de descarga. Una nota de voz de WhatsApp no pasa de unos pocos MB; un
# archivo enorme solo puede ser un adjunto raro, y transcribirlo costaría
# proporcionalmente a su duración sin que nadie lo haya pedido.
_MAX_BYTES = 25 * 1024 * 1024


@dataclass(frozen=True)
class Transcripcion:
    texto: str = ""
    segundos: int = 0
    modelo: str = ""

    @property
    def hay_texto(self) -> bool:
        return bool(self.texto.strip())


def _nodo_de_audio(payload: dict[str, Any]) -> dict[str, Any] | None:
    """El sub-objeto `audioMessage` del evento, con `url`, `mediaKey` y `seconds`."""
    datos = payload.get("data") or payload
    mensaje = datos.get("messages") or datos.get("message") or datos
    if isinstance(mensaje, list):
        mensaje = mensaje[0] if mensaje else {}
    if not isinstance(mensaje, dict):
        return None
    contenido = mensaje.get("message")
    if not isinstance(contenido, dict):
        return None
    for clave in _CLAVES_AUDIO:
        nodo = contenido.get(clave)
        if isinstance(nodo, dict):
            return nodo
    return None


def segundos_de(payload: dict[str, Any]) -> int:
    """Duración declarada por WhatsApp, en segundos.

    Se lee del propio evento en vez de medir el archivo: es el dato con el que
    se factura y viene gratis. Si faltara, se factura 0 antes que inventar una
    duración — cobrar de más por un dato que no se tiene sería lo peor.
    """
    nodo = _nodo_de_audio(payload) or {}
    try:
        return max(int(nodo.get("seconds") or 0), 0)
    except (TypeError, ValueError):
        return 0


def _url_descifrada(payload: dict[str, Any], api_key: str) -> str:
    """Pide a WasenderAPI que descifre la media y devuelve su URL temporal.

    El cuerpo que espera el endpoint es el mismo evento que ya recibimos, así
    que se reenvía tal cual en vez de recomponerlo campo por campo: cualquier
    dato que el proveedor añada mañana viaja solo.
    """
    datos = payload.get("data") or payload
    mensaje = datos.get("messages") or datos.get("message") or datos
    if isinstance(mensaje, list):
        mensaje = mensaje[0] if mensaje else {}

    respuesta = httpx.post(
        f"{settings.WASENDER_API_URL.rstrip('/')}/api/decrypt-media",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"data": {"messages": mensaje}},
        timeout=settings.WASENDER_TIMEOUT_SECONDS,
    )
    respuesta.raise_for_status()
    cuerpo = respuesta.json() or {}
    return str(cuerpo.get("publicUrl") or "")


def _descargar(url: str) -> bytes:
    with httpx.stream("GET", url, timeout=settings.TRANSCRIPCION_TIMEOUT_SECONDS) as respuesta:
        respuesta.raise_for_status()
        buffer = bytearray()
        for trozo in respuesta.iter_bytes():
            buffer.extend(trozo)
            if len(buffer) > _MAX_BYTES:
                raise ValueError("El audio supera el tamaño máximo aceptado.")
        return bytes(buffer)


def transcribir(payload: dict[str, Any], api_key: str) -> Transcripcion:
    """Descifra, descarga y transcribe. Devuelve texto vacío si algo falla.

    Nunca propaga la excepción: un fallo de transcripción no puede dejar al
    cliente sin atención. Quien llama decide qué hacer con un texto vacío (hoy:
    responder el acuse de "no pude escucharlo").
    """
    from openai import OpenAI

    nodo = _nodo_de_audio(payload)
    if not nodo or not nodo.get("url"):
        return Transcripcion()

    segundos = segundos_de(payload)
    modelo = settings.OPENAI_MODEL_TRANSCRIPCION
    try:
        url = _url_descifrada(payload, api_key)
        if not url:
            print("WasenderAPI no devolvió publicUrl al descifrar la nota de voz.")
            return Transcripcion(segundos=segundos, modelo=modelo)

        audio = _descargar(url)
        if not audio:
            return Transcripcion(segundos=segundos, modelo=modelo)

        # El SDK necesita un nombre de archivo para deducir el formato. El
        # contenido va en memoria: el audio no toca el disco en ningún momento.
        archivo = io.BytesIO(audio)
        archivo.name = f"nota-de-voz.{_extension(nodo)}"

        cliente = OpenAI(
            api_key=settings.OPENAI_API_KEY or "test",
            timeout=settings.TRANSCRIPCION_TIMEOUT_SECONDS,
            max_retries=settings.OPENAI_MAX_RETRIES,
        )
        resultado = cliente.audio.transcriptions.create(
            model=modelo,
            file=archivo,
            # El negocio atiende en español; decírselo evita que una nota corta
            # y con ruido se interprete como otro idioma.
            language=settings.TRANSCRIPCION_IDIOMA,
        )
        return Transcripcion(
            texto=str(getattr(resultado, "text", "") or "").strip(),
            segundos=segundos,
            modelo=modelo,
        )
    except Exception as e:
        print(f"Error transcribiendo la nota de voz: {e}")
        return Transcripcion(segundos=segundos, modelo=modelo)


def _extension(nodo: dict[str, Any]) -> str:
    """Extensión a partir del mimetype ('audio/ogg; codecs=opus' -> 'ogg')."""
    mimetype = str(nodo.get("mimetype") or "")
    tipo = mimetype.split(";", 1)[0].strip()
    return {
        "audio/ogg": "ogg",
        "audio/mpeg": "mp3",
        "audio/mp4": "m4a",
        "audio/amr": "amr",
        "audio/wav": "wav",
        "audio/webm": "webm",
    }.get(tipo, "ogg")
