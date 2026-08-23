"""El ritmo de una tanda de envíos.

Salían todas de golpe: el worker tomaba veinte por pasada y las mandaba
seguidas. Cien mensajes idénticos en veinte segundos son la firma más obvia de
un bot, y quien lo paga es el número de WhatsApp del negocio.

Aquí se prueba la ARITMÉTICA del ritmo; que la consulta tome una por sesión se
prueba contra Postgres de verdad en el dashboard, porque eso es SQL.
"""

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("POSTGRES_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("OPENAI_API_KEY", "")

from src.infrastructure.repositories import envios_repository


class RitmoTests(unittest.TestCase):
    def test_la_espera_ronda_los_quince_segundos(self):
        esperas = [envios_repository._espera_hasta_el_siguiente() for _ in range(500)]

        self.assertGreaterEqual(min(esperas), 6)
        self.assertLessEqual(max(esperas), 24)
        # Y de media se queda donde toca: el margen no desplaza el ritmo.
        self.assertAlmostEqual(sum(esperas) / len(esperas), 15, delta=1.5)

    def test_las_esperas_no_se_repiten(self):
        """Si todas fueran iguales, el margen no serviría de nada: el patrón
        regular es justo lo que delata a un bot."""
        esperas = {envios_repository._espera_hasta_el_siguiente() for _ in range(50)}

        self.assertGreater(len(esperas), 40)

    def test_cada_sesion_lleva_su_propio_reloj(self):
        """Se adelanta al TOMARLOS y no tras enviar: si el worker muere a mitad,
        la sesión ya tiene su pausa puesta y no se dispara una ráfaga al volver."""
        tomados = [
            {"id": 1, "proyecto_id": 11, "lote_id": 7},
            {"id": 2, "proyecto_id": 22, "lote_id": 9},
            {"id": 3, "proyecto_id": 11, "lote_id": None},  # histórico sin sesión
        ]
        with patch.object(envios_repository, "consultar", return_value=tomados), patch.object(
            envios_repository, "ejecutar"
        ) as escribir:
            envios_repository.tomar_pendientes()

        lotes_tocados = [llamada.args[1][1:] for llamada in escribir.call_args_list]
        self.assertEqual(lotes_tocados, [(11, 7), (22, 9)])


if __name__ == "__main__":
    unittest.main()
