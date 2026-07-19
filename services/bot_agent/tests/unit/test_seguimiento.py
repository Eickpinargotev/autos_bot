"""Tests deterministas del seguimiento por cliente y resumen mensual.

Sin Redis, NocoDB ni OpenAI reales: se mockean el cliente Redis y el
repositorio. Cubren el cálculo de costo en micro-USD, la ventana de 24h que
define una "conversación", la aplicación de deltas y el cableado de los hooks.
"""

import json
import os
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("POSTGRES_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("OPENAI_API_KEY", "")

from src.application import seguimiento_service as svc
from src.core.config import settings
from src.domain.entities import Channel


def _hora(base: datetime, **delta) -> str:
    return (base + timedelta(**delta)).astimezone().isoformat(timespec="seconds")


class CostoTests(unittest.TestCase):
    def test_costo_microusd_sin_cache(self):
        # 1000 in * $0.75/M + 1000 out * $4.50/M = $0.00525 → 5250 micro-USD
        self.assertEqual(svc.costo_microusd(1000, 0, 1000), 5250)

    def test_costo_microusd_con_cache(self):
        # 400 in * 0.75 + 600 cached * 0.075 + 1000 out * 4.5 = $0.004845
        self.assertEqual(svc.costo_microusd(1000, 600, 1000), 4845)

    def test_costo_cero(self):
        self.assertEqual(svc.costo_microusd(0, 0, 0), 0)


class VentanaConversacionTests(unittest.TestCase):
    """La ventana de 24h define cuándo un mensaje abre una conversación nueva."""

    def test_primer_mensaje_abre_conversacion(self):
        base = datetime.now()
        entry = {"hora": _hora(base), "autor": "cliente", "texto": "hola"}
        fields = svc._aplicar_deltas_cliente({}, [entry], {})
        self.assertEqual(fields["conversaciones_iniciadas"], 1)
        self.assertEqual(fields["primera_interaccion"], entry["hora"])
        self.assertEqual(fields["ultima_interaccion"], entry["hora"])
        self.assertEqual(fields["conversacion_actual_inicio"], entry["hora"])

    def test_mensaje_dentro_de_24h_no_abre_otra(self):
        base = datetime.now()
        primero = {"hora": _hora(base), "autor": "cliente", "texto": "hola"}
        prev = svc._aplicar_deltas_cliente({}, [primero], {})
        luego = {"hora": _hora(base, hours=23), "autor": "cliente", "texto": "sigo aquí"}
        fields = svc._aplicar_deltas_cliente(prev, [luego], {})
        self.assertEqual(fields["conversaciones_iniciadas"], 1)
        # La ventana NO se desliza: sigue anclada al primer mensaje.
        self.assertEqual(fields["conversacion_actual_inicio"], primero["hora"])
        self.assertEqual(fields["ultima_interaccion"], luego["hora"])

    def test_mensaje_pasadas_24h_abre_conversacion_nueva(self):
        base = datetime.now()
        primero = {"hora": _hora(base), "autor": "cliente", "texto": "hola"}
        prev = svc._aplicar_deltas_cliente({}, [primero], {})
        tarde = {"hora": _hora(base, hours=25), "autor": "cliente", "texto": "volví"}
        fields = svc._aplicar_deltas_cliente(prev, [tarde], {})
        self.assertEqual(fields["conversaciones_iniciadas"], 2)
        self.assertEqual(fields["conversacion_actual_inicio"], tarde["hora"])
        self.assertEqual(fields["primera_interaccion"], primero["hora"])

    def test_mensajes_del_bot_no_cuentan_conversaciones(self):
        base = datetime.now()
        entries = [
            {"hora": _hora(base), "autor": "cliente", "texto": "hola"},
            {"hora": _hora(base, minutes=1), "autor": "bot", "texto": "buenas"},
            {"hora": _hora(base, hours=30), "autor": "bot", "texto": "recordatorio"},
        ]
        fields = svc._aplicar_deltas_cliente({}, entries, {})
        self.assertEqual(fields["conversaciones_iniciadas"], 1)
        # La última interacción es del CLIENTE, no del bot.
        self.assertEqual(fields["ultima_interaccion"], entries[0]["hora"])
        historial = json.loads(fields["historial"])["mensajes"]
        self.assertEqual(len(historial), 3)


class DeltasClienteTests(unittest.TestCase):
    def test_acumula_costo_tokens_y_derivaciones(self):
        prev = {
            "costo_microusd": 1000,
            "tokens_entrada": 10,
            "tokens_salida": 5,
            "derivaciones_asesor": 1,
        }
        deltas = {"costo_microusd": "4845", "tokens_entrada": "1000", "tokens_salida": "1000", "derivaciones": "1"}
        fields = svc._aplicar_deltas_cliente(prev, [], deltas)
        self.assertEqual(fields["costo_microusd"], 5845)
        self.assertEqual(fields["costo_acumulado_usd"], 0.005845)
        self.assertEqual(fields["tokens_entrada"], 1010)
        self.assertEqual(fields["tokens_salida"], 1005)
        self.assertEqual(fields["derivaciones_asesor"], 2)

    def test_historial_respeta_el_tope(self):
        base = datetime.now()
        previos = {"historial": json.dumps({"mensajes": [{"hora": _hora(base), "autor": "bot", "texto": str(i)} for i in range(5)]})}
        entries = [{"hora": _hora(base, minutes=1), "autor": "bot", "texto": "nuevo"}]
        with patch.object(settings, "SEGUIMIENTO_HISTORIAL_MAX_MENSAJES", 3):
            fields = svc._aplicar_deltas_cliente(previos, entries, {})
        historial = json.loads(fields["historial"])["mensajes"]
        self.assertEqual(len(historial), 3)
        self.assertEqual(historial[-1]["texto"], "nuevo")

    def test_nombre_del_buffer_se_persiste(self):
        fields = svc._aplicar_deltas_cliente({}, [], {"nombre": "German", "derivaciones": "1"})
        self.assertEqual(fields["nombre"], "German")


class DeltasMesTests(unittest.TestCase):
    def test_acumula_mensajes_y_costo(self):
        prev = {"mensajes_bot": 10, "mensajes_cliente": 7, "costo_microusd": 2000}
        deltas = {"mensajes_bot": "3", "mensajes_cliente": "2", "costo_microusd": "5250"}
        fields = svc._aplicar_deltas_mes(prev, deltas)
        self.assertEqual(fields["mensajes_bot"], 13)
        self.assertEqual(fields["mensajes_cliente"], 9)
        self.assertEqual(fields["costo_microusd"], 7250)
        self.assertEqual(fields["costo_total_usd"], 0.00725)
        self.assertTrue(fields["actualizado_en"])


class RegistrarUsoLlmTests(unittest.TestCase):
    def _usage(self):
        return SimpleNamespace(
            prompt_tokens=1000,
            completion_tokens=1000,
            prompt_tokens_details=SimpleNamespace(cached_tokens=600),
        )

    def test_acumula_en_buffer_cliente_y_mes(self):
        pipe = MagicMock()
        with patch.object(svc.redis_client, "pipeline", return_value=pipe):
            svc.registrar_uso_llm("123", Channel.TELEGRAM, self._usage())
        llamadas = {(c.args[0], c.args[1]): c.args[2] for c in pipe.hincrby.call_args_list}
        cliente_key = svc.scoped_key(svc.DELTAS_PREFIX, Channel.TELEGRAM, "123")
        mes_key = svc._mes_key(svc._mes_actual())
        self.assertEqual(llamadas[(cliente_key, "costo_microusd")], 4845)
        self.assertEqual(llamadas[(cliente_key, "tokens_entrada")], 1000)
        self.assertEqual(llamadas[(mes_key, "costo_microusd")], 4845)
        self.assertEqual(llamadas[(mes_key, "tokens_salida")], 1000)
        pipe.execute.assert_called_once()

    def test_sin_cliente_solo_acumula_el_mes(self):
        pipe = MagicMock()
        with patch.object(svc.redis_client, "pipeline", return_value=pipe):
            svc.registrar_uso_llm("", "", self._usage())
        keys = {c.args[0] for c in pipe.hincrby.call_args_list}
        self.assertEqual(keys, {svc._mes_key(svc._mes_actual())})

    def test_usage_none_no_toca_redis(self):
        with patch.object(svc.redis_client, "pipeline") as pipeline_mock:
            svc.registrar_uso_llm("123", Channel.TELEGRAM, None)
        pipeline_mock.assert_not_called()


_URL_DUMMY = "http://nocodb.test/api/v3/data/base/tabla/records"


class FlushClienteTests(unittest.TestCase):
    def test_crea_fila_y_descuenta_exactamente_lo_aplicado(self):
        base = datetime.now()
        entry = {"hora": _hora(base), "autor": "cliente", "texto": "hola"}
        pipe = MagicMock()
        with patch.object(settings, "NOCODB_SEGUIMIENTO_CLIENTES_URL", _URL_DUMMY), \
                patch.object(svc.redis_client, "set", return_value=True), \
                patch.object(svc.redis_client, "lrange", return_value=[json.dumps(entry)]), \
                patch.object(svc.redis_client, "hgetall", return_value={"costo_microusd": "5250"}), \
                patch.object(svc.redis_client, "pipeline", return_value=pipe), \
                patch.object(svc.redis_client, "delete") as delete_mock, \
                patch.object(svc.SeguimientoRepository, "find_cliente", return_value=None), \
                patch.object(svc.SeguimientoRepository, "create_cliente", return_value=True) as create_mock:
            self.assertTrue(svc.flush_cliente("123", Channel.TELEGRAM))

        fields = create_mock.call_args.args[0]
        self.assertEqual(fields["client_id"], "123")
        self.assertEqual(fields["canal"], "telegram")
        self.assertEqual(fields["conversaciones_iniciadas"], 1)
        self.assertEqual(fields["costo_microusd"], 5250)
        # Descuenta el delta aplicado y recorta el historial volcado.
        pipe.hincrby.assert_any_call(svc.scoped_key(svc.DELTAS_PREFIX, "telegram", "123"), "costo_microusd", -5250)
        pipe.ltrim.assert_called_once_with(svc.scoped_key(svc.HISTORIAL_PREFIX, "telegram", "123"), 1, -1)
        delete_mock.assert_called()  # libera el candado

    def test_candado_ocupado_no_toca_nocodb(self):
        with patch.object(settings, "NOCODB_SEGUIMIENTO_CLIENTES_URL", _URL_DUMMY), \
                patch.object(svc.redis_client, "set", return_value=False), \
                patch.object(svc.SeguimientoRepository, "find_cliente") as find_mock:
            self.assertFalse(svc.flush_cliente("123", Channel.TELEGRAM))
        find_mock.assert_not_called()

    def test_error_de_nocodb_conserva_el_buffer(self):
        entry = {"hora": _hora(datetime.now()), "autor": "cliente", "texto": "hola"}
        pipe = MagicMock()
        with patch.object(settings, "NOCODB_SEGUIMIENTO_CLIENTES_URL", _URL_DUMMY), \
                patch.object(svc.redis_client, "set", return_value=True), \
                patch.object(svc.redis_client, "lrange", return_value=[json.dumps(entry)]), \
                patch.object(svc.redis_client, "hgetall", return_value={}), \
                patch.object(svc.redis_client, "pipeline", return_value=pipe), \
                patch.object(svc.redis_client, "delete"), \
                patch.object(svc.SeguimientoRepository, "find_cliente", side_effect=RuntimeError("caído")):
            self.assertFalse(svc.flush_cliente("123", Channel.TELEGRAM))
        pipe.ltrim.assert_not_called()
        pipe.hincrby.assert_not_called()


class FlushMesTests(unittest.TestCase):
    def test_actualiza_fila_existente(self):
        record = {"id": 7, "fields": {"mes": "2026-07", "mensajes_bot": 10, "costo_microusd": 100}}
        pipe = MagicMock()
        with patch.object(settings, "NOCODB_RESUMEN_MENSUAL_URL", _URL_DUMMY), \
                patch.object(svc.redis_client, "set", return_value=True), \
                patch.object(svc.redis_client, "hgetall", return_value={"mensajes_bot": "2", "costo_microusd": "50"}), \
                patch.object(svc.redis_client, "pipeline", return_value=pipe), \
                patch.object(svc.redis_client, "delete"), \
                patch.object(svc.SeguimientoRepository, "find_mes", return_value=record), \
                patch.object(svc.SeguimientoRepository, "update_mes", return_value=True) as update_mock:
            self.assertTrue(svc.flush_mes("2026-07"))
        record_id, fields = update_mock.call_args.args
        self.assertEqual(record_id, "7")
        self.assertEqual(fields["mensajes_bot"], 12)
        self.assertEqual(fields["costo_microusd"], 150)
        pipe.hincrby.assert_any_call(svc._mes_key("2026-07"), "mensajes_bot", -2)

    def test_sin_deltas_no_escribe(self):
        with patch.object(settings, "NOCODB_RESUMEN_MENSUAL_URL", _URL_DUMMY), \
                patch.object(svc.redis_client, "set", return_value=True), \
                patch.object(svc.redis_client, "hgetall", return_value={}), \
                patch.object(svc.redis_client, "delete"), \
                patch.object(svc.SeguimientoRepository, "find_mes") as find_mock:
            self.assertTrue(svc.flush_mes("2026-07"))
        find_mock.assert_not_called()


class HooksTests(unittest.TestCase):
    """El log de conversaciones alimenta el seguimiento sin romperse entre sí."""

    def test_log_inbound_registra_mensaje_de_cliente(self):
        from src.infrastructure.repositories.conversation_log_repository import ConversationLogRepository

        with patch("src.application.seguimiento_service.registrar_mensaje") as track_mock, \
                patch.object(settings, "NOCODB_CONVERSATIONS_URL", ""):
            ConversationLogRepository.log_inbound(
                client_id="123", canal=Channel.TELEGRAM, sender_name="German",
                message_type="text", text="hola",
            )
        kwargs = track_mock.call_args.kwargs
        self.assertEqual(kwargs["autor"], "cliente")
        self.assertEqual(kwargs["texto"], "hola")
        self.assertEqual(kwargs["nombre"], "German")

    def test_log_inbound_ignora_eventos_que_no_son_mensajes(self):
        from src.infrastructure.repositories.conversation_log_repository import ConversationLogRepository

        with patch("src.application.seguimiento_service.registrar_mensaje") as track_mock, \
                patch.object(settings, "NOCODB_CONVERSATIONS_URL", ""):
            ConversationLogRepository.log_inbound(
                client_id="123", canal=Channel.TELEGRAM, sender_name="German",
                message_type="text", text="", event_type="group_join",
            )
        track_mock.assert_not_called()

    def test_log_outbound_registra_mensaje_de_bot(self):
        from src.infrastructure.repositories.conversation_log_repository import ConversationLogRepository

        with patch("src.application.seguimiento_service.registrar_mensaje") as track_mock, \
                patch.object(settings, "NOCODB_CONVERSATIONS_URL", ""):
            ConversationLogRepository.log_outbound(client_id="123", canal=Channel.TELEGRAM, text="buenas")
        kwargs = track_mock.call_args.kwargs
        self.assertEqual(kwargs["autor"], "bot")
        self.assertEqual(kwargs["texto"], "buenas")

    def test_fallo_del_seguimiento_no_rompe_el_log(self):
        from src.infrastructure.repositories.conversation_log_repository import ConversationLogRepository

        with patch("src.application.seguimiento_service.registrar_mensaje", side_effect=RuntimeError("redis caído")), \
                patch.object(settings, "NOCODB_CONVERSATIONS_URL", ""):
            # No debe lanzar: el log sigue su curso aunque el seguimiento falle.
            ConversationLogRepository.log_outbound(client_id="123", canal=Channel.TELEGRAM, text="buenas")


if __name__ == "__main__":
    unittest.main()
