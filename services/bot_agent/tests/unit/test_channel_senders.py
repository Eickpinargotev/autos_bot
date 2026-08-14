import os
import unittest
from unittest.mock import MagicMock, patch


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

        with patch(
            "src.infrastructure.channels.senders.clientes_whatsapp_repo.api_key_de_envio",
            return_value="",
        ):
            with self.assertRaises(wasender.WasenderNoConfigurado):
                sender.send_message_sync("50688888888", "Hola")

    def test_usa_la_clave_del_negocio_dueno_de_la_conversacion(self):
        """La credencial sale de la base por destinatario, nunca del entorno.

        Es lo que permite que dos negocios con números distintos convivan: el
        mismo worker responde a cada uno con la clave de su propia sesión.
        """
        sender = WhatsAppSender()
        with patch(
            "src.infrastructure.channels.senders.clientes_whatsapp_repo.api_key_de_envio",
            return_value="clave-del-negocio-2",
        ) as resolver_mock:
            with patch("src.infrastructure.channels.senders.wasender.enviar_texto") as enviar_mock:
                sender.send_message_sync("50688888888", "Hola")

        self.assertEqual(resolver_mock.call_args.args, ("whatsapp", "50688888888"))
        self.assertEqual(
            enviar_mock.call_args.args, ("50688888888", "Hola", "clave-del-negocio-2")
        )

    def test_envia_media_por_url_sin_descargar_el_binario(self):
        """WasenderAPI descarga la media él mismo; subir el archivo sería inútil."""
        sender = WhatsAppSender()
        with patch(
            "src.infrastructure.channels.senders.clientes_whatsapp_repo.api_key_de_envio",
            return_value="clave",
        ):
            with patch("src.infrastructure.channels.senders.wasender.enviar_imagen") as imagen_mock:
                sender.send_image_url_sync("50688888888", "1abcDEF", "pie de foto")

        destino, url, pie, api_key = imagen_mock.call_args.args
        self.assertEqual(destino, "50688888888")
        self.assertIn("1abcDEF", url)
        self.assertEqual(pie, "pie de foto")
        self.assertEqual(api_key, "clave")


class DestinoLidTests(unittest.TestCase):
    """La conversación guardada con un LID igual tiene que poder responderse."""

    def setUp(self):
        wasender._contactos_cache.clear()

    def tearDown(self):
        wasender._contactos_cache.clear()

    def test_traduce_el_lid_guardado_al_numero_antes_de_enviar(self):
        libreta = {
            "success": True,
            "data": [
                {"id": "593983512981@s.whatsapp.net", "lid": "258540019138808@lid"},
                {"id": "115182617518101@lid", "lid": None},
            ],
        }
        with patch("src.infrastructure.channels.wasender.httpx.get") as get_mock:
            get_mock.return_value.json.return_value = libreta
            with patch("src.infrastructure.channels.wasender.enviar", return_value={}) as enviar_mock:
                wasender.enviar_texto("258540019138808", "hola", "clave")

        payload, _ = enviar_mock.call_args.args
        self.assertEqual(payload["to"], "593983512981")

    def test_un_numero_normal_pasa_de_largo(self):
        with patch("src.infrastructure.channels.wasender.httpx.get") as get_mock:
            get_mock.return_value.json.return_value = {"data": []}
            self.assertEqual(wasender.numero_para_envio("50688888888", "clave"), "50688888888")

    def test_si_la_libreta_falla_no_se_inventa_un_destino(self):
        """Mejor el error del proveedor que enviarle el mensaje a otra persona."""
        with patch(
            "src.infrastructure.channels.wasender.httpx.get", side_effect=RuntimeError("caída")
        ):
            self.assertEqual(wasender.numero_para_envio("258540019138808", "clave"), "258540019138808")


class LimiteDeRitmoTests(unittest.TestCase):
    """El plan limita los envíos por minuto; un 429 no puede perder la respuesta."""

    def _respuesta(self, status, cuerpo=None):
        r = MagicMock()
        r.status_code = status
        r.headers = {}
        r.json.return_value = cuerpo or {}
        r.raise_for_status.side_effect = None
        return r

    def test_espera_lo_que_pide_el_proveedor_y_reintenta(self):
        limitado = self._respuesta(429, {"message": "free trial", "retry_after": 14})
        bien = self._respuesta(200, {"success": True})

        with patch(
            "src.infrastructure.channels.wasender.httpx.post", side_effect=[limitado, bien]
        ) as post_mock:
            with patch("src.infrastructure.channels.wasender.time.sleep") as dormir:
                wasender.enviar({"to": "50688888888", "text": "hola"}, "clave")

        self.assertEqual(post_mock.call_count, 2)
        # +1s de colchón: reintentar en el segundo exacto vuelve a dar 429.
        self.assertEqual(dormir.call_args.args[0], 15.0)

    def test_no_reintenta_para_siempre(self):
        limitado = self._respuesta(429, {"retry_after": 1})
        limitado.raise_for_status.side_effect = RuntimeError("429")

        with patch(
            "src.infrastructure.channels.wasender.httpx.post", return_value=limitado
        ) as post_mock:
            with patch("src.infrastructure.channels.wasender.time.sleep"):
                with self.assertRaises(RuntimeError):
                    wasender.enviar({"to": "50688888888", "text": "hola"}, "clave")

        self.assertEqual(post_mock.call_count, 2)  # el intento inicial + 1 reintento

    def test_la_espera_no_supera_el_candado_de_conversacion(self):
        """Un `retry_after` absurdo no puede dejar la tarea colgada.

        El envío ocurre dentro del candado por conversación (120s): si la espera
        lo superara, expiraría a media respuesta y entraría un segundo turno.
        """
        from src.core.config import settings

        absurdo = self._respuesta(429, {"retry_after": 99999})
        espera = wasender._segundos_de_espera(absurdo)

        self.assertEqual(espera, settings.WASENDER_ESPERA_429_MAXIMA)
        # Se duerme una vez por REINTENTO; el intento inicial no espera.
        self.assertLess(
            espera * settings.WASENDER_MAX_REINTENTOS_429, 120,
            "la suma de esperas debe caber en el candado de conversación",
        )


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

    def test_usa_el_telefono_cuando_el_remoteJid_viene_como_lid(self):
        """Un LID no sirve para responder: WasenderAPI lo rechaza con 422.

        Según el `addressingMode` de la sesión, `remoteJid` llega como LID. La
        documentación del proveedor marca `cleanedSenderPn`/`senderPn` como los
        campos de los que sacar el teléfono.
        """
        mensaje = wasender.mensaje_entrante(
            {
                "data": {
                    "messages": {
                        "key": {
                            "remoteJid": "258540019138808@lid",
                            "addressingMode": "lid",
                            "senderPn": "593983512981@s.whatsapp.net",
                            "cleanedSenderPn": "593983512981",
                        },
                        "message": {"conversation": "hola"},
                    }
                }
            }
        )

        self.assertEqual(mensaje.user_id, "593983512981")

    def test_en_un_saliente_no_se_toma_el_sender_como_la_conversacion(self):
        """En un `fromMe` el 'sender' es el negocio, no el cliente."""
        mensaje = wasender.mensaje_entrante(
            {
                "data": {
                    "messages": {
                        "key": {
                            "remoteJid": "258540019138808@lid",
                            "fromMe": True,
                            "cleanedSenderPn": "50611110000",
                        },
                        "message": {"conversation": "voy saliendo"},
                    }
                }
            }
        )

        self.assertTrue(mensaje.from_me)
        self.assertNotEqual(mensaje.user_id, "50611110000")

    def test_descarta_lo_que_no_es_chat_individual(self):
        """Lista blanca: grupos, canales, estados y difusiones no son clientes.

        Un canal `@newsletter` se coló una vez y quedó en el panel como si fuera
        una persona, con su id de 18 dígitos por número.
        """
        self.assertIsNone(wasender.mensaje_entrante({}))

        for jid in (
            "12345@g.us",
            "120363169319669622@newsletter",
            "status@broadcast",
            "12345@broadcast",
        ):
            with self.subTest(jid=jid):
                self.assertIsNone(
                    wasender.mensaje_entrante(
                        {"data": {"messages": {"key": {"remoteJid": jid}, "message": {"conversation": "x"}}}}
                    )
                )

        for jid in ("50688888888@s.whatsapp.net", "258540019138808@lid"):
            with self.subTest(jid=jid):
                self.assertIsNotNone(
                    wasender.mensaje_entrante(
                        {"data": {"messages": {"key": {"remoteJid": jid}, "message": {"conversation": "x"}}}}
                    )
                )


if __name__ == "__main__":
    unittest.main()
