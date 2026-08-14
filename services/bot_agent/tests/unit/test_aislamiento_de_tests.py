"""El propio aislamiento de los tests, vigilado por tests.

Esto no prueba lógica de negocio: prueba que la suite no pueda tocar los
servicios reales. Existe por un incidente concreto (01/08/2026): un test de
detección de enlaces ejerció el orquestador de verdad, `apply_async` encoló en
el broker REAL, y el worker que estaba corriendo procesó "buenas, cuanto cuesta
el curso? gracias" como si fuera un cliente. Resultado: llamadas pagadas a
OpenAI, filas basura en la base del negocio y un usuario bloqueado por 12 días.

El fallo no se ve al escribir el test —dentro del proceso de pytest todo está
parcheado—, porque el daño ocurre en OTRO proceso. Por eso hace falta fijarlo
aquí: el día que alguien quite una línea del conftest, falla esto y no la
factura del cliente.
"""

import os
import unittest

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("POSTGRES_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("OPENAI_API_KEY", "")


class AislamientoTests(unittest.TestCase):
    def test_no_se_encola_nada_en_el_broker_real(self):
        """`apply_async` cruza a otro proceso, donde no hay ningún mock."""
        from unittest.mock import MagicMock

        from src.infrastructure.tasks.celery_app import process_buffered_messages

        self.assertIsInstance(process_buffered_messages.apply_async, MagicMock)

    def test_todos_los_modulos_comparten_un_redis_en_memoria(self):
        """Ni el real, ni uno distinto por módulo.

        Cada módulo hace `from ... import redis_client`, así que el nombre queda
        copiado en el suyo: sustituirlo solo en `buffer_service` deja al
        orquestador escribiendo en el Redis de verdad.
        """
        import importlib

        import fakeredis

        from tests.conftest import _MODULOS_CON_REDIS

        clientes = {}
        for nombre in _MODULOS_CON_REDIS:
            modulo = importlib.import_module(nombre)
            if hasattr(modulo, "redis_client"):
                clientes[nombre] = modulo.redis_client

        self.assertTrue(clientes)
        for nombre, cliente in clientes.items():
            with self.subTest(modulo=nombre):
                self.assertIsInstance(cliente, fakeredis.FakeRedis)
        self.assertEqual(
            len({id(c) for c in clientes.values()}), 1, "todos deben compartir el mismo"
        )

    def test_ningun_repositorio_llega_a_postgres(self):
        """Incluido `postgres_user_repo`, que usa `run_query` y no `ejecutar`."""
        import importlib
        from unittest.mock import MagicMock

        from tests.conftest import _MODULOS_CON_POSTGRES, _RETORNOS

        for nombre in _MODULOS_CON_POSTGRES:
            modulo = importlib.import_module(nombre)
            for funcion in _RETORNOS:
                if hasattr(modulo, funcion):
                    with self.subTest(modulo=nombre, funcion=funcion):
                        self.assertIsInstance(getattr(modulo, funcion), MagicMock)

    def test_un_turno_completo_no_toca_nada_real(self):
        """La prueba de fuego: el camino exacto que causó el incidente.

        El orquestador SÍ debe encolar —es su trabajo—; lo que no puede es que
        eso llegue al broker de verdad. Se comprueba que la llamada quedó
        retenida por el doble, que es la frontera entre procesos.
        """
        from unittest.mock import MagicMock

        from src.application.conversation_orchestrator import ConversationOrchestrator
        from src.domain.entities import Channel, InboundMessage, MessageType
        from src.infrastructure.tasks.celery_app import process_buffered_messages

        mensaje = InboundMessage(
            channel=Channel.WHATSAPP,
            user_id="50688888888",
            user_name="Ana",
            message_type=MessageType.TEXT,
            text="buenas, cuanto cuesta el curso? gracias",
        )
        ConversationOrchestrator().handle(mensaje)

        encolar = process_buffered_messages.apply_async
        self.assertIsInstance(encolar, MagicMock, "la tarea saldría al broker real")
        encolar.assert_called_once()


if __name__ == "__main__":
    unittest.main()
