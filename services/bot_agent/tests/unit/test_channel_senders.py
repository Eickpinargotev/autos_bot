import os
import unittest
from unittest.mock import patch


os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("POSTGRES_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("OPENAI_API_KEY", "")

from src.domain.entities import Channel
from src.infrastructure.channels import wasender
from src.infrastructure.channels.outbound_attachments import parse_outbound_message, url_publica
from src.infrastructure.channels.senders import ChannelSenderRegistry, WhatsAppSender


class WhatsAppSenderTests(unittest.TestCase):
    def test_sin_credenciales_el_canal_queda_inactivo_con_mensaje_claro(self):
        """Nunca debe fallar con un error críptico de red: el canal está apagado."""
        sender = ChannelSenderRegistry.get(Channel.WHATSAPP)
        self.assertIsInstance(sender, WhatsAppSender)

        with patch("src.infrastructure.channels.wasender.settings.WASENDER_API_KEY", ""):
            with self.assertRaises(wasender.WasenderNoConfigurado):
                sender.send_message_sync("50688888888", "Hola")

    def test_envia_media_por_url_sin_descargar_el_binario(self):
        """WasenderAPI descarga la media él mismo; subir el archivo sería inútil."""
        sender = WhatsAppSender()
        with patch("src.infrastructure.channels.senders.wasender.enviar_imagen") as imagen_mock:
            sender.send_image_url_sync("50688888888", "1abcDEF", "pie de foto")

        destino, url, pie = imagen_mock.call_args.args
        self.assertEqual(destino, "50688888888")
        self.assertIn("1abcDEF", url)
        self.assertEqual(pie, "pie de foto")


class MarcadoresDeMediaTests(unittest.TestCase):
    def test_reconoce_imagen_y_video(self):
        parsed = parse_outbound_message("Mira esto\n\nImagen=1abcDEF\nVideo=9xyzGHI")

        self.assertEqual(parsed.image_ids, ["1abcDEF"])
        self.assertEqual(parsed.video_ids, ["9xyzGHI"])
        self.assertEqual(parsed.clean_text, "Mira esto")

    def test_acepta_url_directa_ademas_del_id_de_drive(self):
        parsed = parse_outbound_message("Video=https://cdn.ejemplo.com/clip.mp4")

        self.assertEqual(parsed.video_ids, ["https://cdn.ejemplo.com/clip.mp4"])
        self.assertEqual(url_publica("https://cdn.ejemplo.com/clip.mp4"), "https://cdn.ejemplo.com/clip.mp4")

    def test_no_arrastra_la_puntuacion_final(self):
        """"Imagen=1abc." dentro de una frase no debe incluir el punto en el ID."""
        parsed = parse_outbound_message("Te dejo la foto Imagen=1abc.")

        self.assertEqual(parsed.image_ids, ["1abc"])

    def test_id_de_drive_se_expande_a_url_descargable(self):
        self.assertIn("1abcDEF", url_publica("1abcDEF"))
        self.assertTrue(url_publica("1abcDEF").startswith("http"))


class EventosEntrantesTests(unittest.TestCase):
    def test_extrae_numero_texto_y_nombre(self):
        mensaje = wasender.mensaje_entrante(
            {
                "data": {
                    "messages": {
                        "key": {"remoteJid": "50688888888@s.whatsapp.net"},
                        "pushName": "Ana",
                        "message": {"conversation": "buenas"},
                    }
                }
            }
        )

        self.assertIsNotNone(mensaje)
        self.assertEqual(mensaje.user_id, "50688888888")
        self.assertEqual(mensaje.user_name, "Ana")
        self.assertEqual(mensaje.text, "buenas")
        self.assertEqual(mensaje.channel, Channel.WHATSAPP)

    def test_lee_el_pie_de_una_imagen(self):
        mensaje = wasender.mensaje_entrante(
            {
                "data": {
                    "messages": {
                        "key": {"remoteJid": "50688888888@s.whatsapp.net"},
                        "message": {"imageMessage": {"caption": "aquí va mi cédula"}},
                    }
                }
            }
        )

        self.assertEqual(mensaje.text, "aquí va mi cédula")

    def test_descarta_lo_que_no_es_chat_individual(self):
        self.assertIsNone(wasender.mensaje_entrante({}))
        self.assertIsNone(
            wasender.mensaje_entrante(
                {"data": {"messages": {"key": {"remoteJid": "12345@g.us"}, "message": {"conversation": "x"}}}}
            )
        )


if __name__ == "__main__":
    unittest.main()
