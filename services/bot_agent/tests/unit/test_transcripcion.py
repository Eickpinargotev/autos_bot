"""Notas de voz: se transcriben, y del audio no queda rastro.

Dos reglas del producto que conviene tener fijadas, porque las dos se rompen
sin que nadie lo note:

1. El audio NO se guarda. Ni en disco, ni en la base, ni en el historial. Lo
   único que sobrevive es el texto. Si alguien "mejora" esto guardando el
   binario para depurar, la base se llena de datos personales que nadie va a
   volver a oír.
2. El LLM recibe TEXTO PLANO. La etiqueta "[Audio transcrito]" es solo para el
   panel (viaja en `event_type`). Si se colara en el texto, el modelo empezaría
   a razonar sobre el formato del mensaje en vez de sobre lo que dice.
"""

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("POSTGRES_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("OPENAI_API_KEY", "")

from src.application import transcripcion_service as ts


def _evento(segundos=8, mimetype="audio/ogg; codecs=opus", url="https://mmg.whatsapp.net/x.enc"):
    return {
        "event": "messages.received",
        "data": {
            "messages": {
                "key": {"id": "ABC123", "remoteJid": "50688888888@s.whatsapp.net"},
                "message": {
                    "audioMessage": {
                        "url": url,
                        "mediaKey": "clave-de-media",
                        "mimetype": mimetype,
                        "seconds": segundos,
                        "ptt": True,
                    }
                },
            }
        },
    }


class LecturaDelEventoTests(unittest.TestCase):
    def test_lee_la_duracion_declarada_por_whatsapp(self):
        """Es el dato con el que se factura, y viene gratis en el evento."""
        self.assertEqual(ts.segundos_de(_evento(segundos=23)), 23)

    def test_sin_duracion_se_factura_cero_en_vez_de_inventarla(self):
        evento = _evento()
        del evento["data"]["messages"]["message"]["audioMessage"]["seconds"]
        self.assertEqual(ts.segundos_de(evento), 0)

    def test_deduce_la_extension_del_mimetype(self):
        """El SDK necesita el nombre de archivo para saber el formato."""
        nodo = {"mimetype": "audio/ogg; codecs=opus"}
        self.assertEqual(ts._extension(nodo), "ogg")
        self.assertEqual(ts._extension({"mimetype": "audio/mpeg"}), "mp3")
        self.assertEqual(ts._extension({}), "ogg")

    def test_un_evento_sin_audio_no_intenta_nada(self):
        vacio = {"data": {"messages": {"message": {"conversation": "hola"}}}}
        self.assertEqual(ts.transcribir(vacio, "clave"), ts.Transcripcion())


class TranscripcionTests(unittest.TestCase):
    def test_descifra_descarga_y_devuelve_solo_el_texto(self):
        with patch.object(ts, "_url_descifrada", return_value="https://tmp/audio.ogg") as descifrar, \
             patch.object(ts, "_descargar", return_value=b"bytes-de-audio") as descargar, \
             patch("openai.OpenAI") as openai_mock:
            openai_mock.return_value.audio.transcriptions.create.return_value = MagicMock(
                text="  buenas, quiero informacion del curso  "
            )
            resultado = ts.transcribir(_evento(segundos=12), "clave-del-negocio")

        descifrar.assert_called_once()
        descargar.assert_called_once_with("https://tmp/audio.ogg")
        self.assertEqual(resultado.texto, "buenas, quiero informacion del curso")
        self.assertEqual(resultado.segundos, 12)
        self.assertTrue(resultado.hay_texto)

    def test_el_audio_nunca_toca_el_disco(self):
        """Se transcribe desde memoria: `open()` no debe aparecer por aquí."""
        with patch.object(ts, "_url_descifrada", return_value="https://tmp/a.ogg"), \
             patch.object(ts, "_descargar", return_value=b"bytes"), \
             patch("openai.OpenAI") as openai_mock, \
             patch("builtins.open") as open_mock:
            openai_mock.return_value.audio.transcriptions.create.return_value = MagicMock(text="hola")
            ts.transcribir(_evento(), "clave")

        open_mock.assert_not_called()
        enviado = openai_mock.return_value.audio.transcriptions.create.call_args.kwargs["file"]
        self.assertTrue(hasattr(enviado, "read"), "debe ser un archivo en memoria")

    def test_un_fallo_no_propaga_y_deja_la_duracion_para_cobrar(self):
        """Si el proveedor cobró el audio, se registra aunque no haya texto."""
        with patch.object(ts, "_url_descifrada", side_effect=RuntimeError("caída")):
            resultado = ts.transcribir(_evento(segundos=9), "clave")

        self.assertFalse(resultado.hay_texto)
        self.assertEqual(resultado.segundos, 9)


class HistorialTests(unittest.TestCase):
    def test_el_llm_recibe_texto_plano_sin_la_etiqueta_del_panel(self):
        """La marca "[Audio transcrito]" viaja en `event_type`, no en el texto."""
        from src.infrastructure.tasks import celery_app

        with patch.object(celery_app.transcripcion_service, "transcribir",
                          return_value=ts.Transcripcion(texto="cuanto cuesta", segundos=5, modelo="m")), \
             patch.object(celery_app.clientes_whatsapp_repo, "api_key_de_envio", return_value="k"), \
             patch.object(celery_app.seguimiento_service, "registrar_uso_audio"), \
             patch.object(celery_app.ConversationLogRepository, "log_inbound") as log, \
             patch.object(celery_app.BufferService, "add_message", return_value=1) as buffer:
            celery_app.transcribir_nota_de_voz("whatsapp", "50688888888", "Ana", _evento())

        # Al historial va el texto, marcado en event_type.
        self.assertEqual(log.call_args.kwargs["text"], "cuanto cuesta")
        self.assertEqual(log.call_args.kwargs["event_type"], "audio_transcrito")
        # Al buffer del agente va SOLO el texto, sin ninguna etiqueta.
        self.assertEqual(buffer.call_args.args[1], "cuanto cuesta")

    def test_se_cobra_el_audio_por_su_duracion(self):
        from src.infrastructure.tasks import celery_app

        with patch.object(celery_app.transcripcion_service, "transcribir",
                          return_value=ts.Transcripcion(texto="hola", segundos=17, modelo="gpt-4o-transcribe")), \
             patch.object(celery_app.clientes_whatsapp_repo, "api_key_de_envio", return_value="k"), \
             patch.object(celery_app.seguimiento_service, "registrar_uso_audio") as cobro, \
             patch.object(celery_app.ConversationLogRepository, "log_inbound"), \
             patch.object(celery_app.BufferService, "add_message", return_value=1):
            celery_app.transcribir_nota_de_voz("whatsapp", "50688888888", "Ana", _evento())

        self.assertEqual(cobro.call_args.args[2], 17)


if __name__ == "__main__":
    unittest.main()
