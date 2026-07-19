"""Aislamiento global de los tests frente a servicios reales.

Los tests deterministas corren en el contenedor dev con Redis y NocoDB
REALES alcanzables (el compose local los levanta). Cualquier camino de código
con efectos colaterales que un test ejerza sin mockear escribiría datos basura
en esos servicios. Este fixture autouse corta ese riesgo para el seguimiento:

- Reemplaza el cliente Redis del seguimiento por un MagicMock (los tests que
  necesitan verificar llamadas siguen parcheando sobre ese mock).
- Vacía las URLs de seguimiento/resumen para que los volcados sean no-op; los
  tests de volcado las re-parchean con un valor dummy explícito.
"""

from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _aisla_seguimiento(monkeypatch):
    from src.application import seguimiento_service
    from src.core.config import settings

    monkeypatch.setattr(seguimiento_service, "redis_client", MagicMock())
    monkeypatch.setattr(settings, "NOCODB_SEGUIMIENTO_CLIENTES_URL", "")
    monkeypatch.setattr(settings, "NOCODB_RESUMEN_MENSUAL_URL", "")
    yield
