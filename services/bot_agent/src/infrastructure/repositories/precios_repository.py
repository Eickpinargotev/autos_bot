"""Precio vigente de cada modelo, tal como lo cobra el proveedor.

Vive en la base (`precios_modelo`) y se administra desde el panel: cuando OpenAI
mueve un precio no hay que redesplegar nada. Ver la migración 010.

Se cachea unos segundos porque se consulta en CADA llamada al LLM. El TTL es
corto a propósito: corregir un precio mal puesto desde el panel tiene que surtir
efecto sin reiniciar el worker.

Si la base no responde, se devuelven los precios de respaldo del entorno en vez
de un cero. Un cero silencioso sería peor que un precio aproximado: el consumo
se registraría como gratis y nadie lo notaría hasta cerrar el mes.
"""

import time
from dataclasses import dataclass

from src.core.config import settings
from src.infrastructure.repositories.postgres_conn import consultar_uno

CACHE_TTL_SEGUNDOS = 60

_cache: dict[str, tuple[float, "PrecioModelo"]] = {}


@dataclass(frozen=True)
class PrecioModelo:
    """Precios de un modelo. Tokens por millón; audio por minuto."""

    entrada_usd_1m: float = 0.0
    cacheado_usd_1m: float = 0.0
    salida_usd_1m: float = 0.0
    audio_usd_minuto: float = 0.0


def _respaldo() -> PrecioModelo:
    return PrecioModelo(
        entrada_usd_1m=settings.OPENAI_PRICE_INPUT_USD_PER_1M,
        cacheado_usd_1m=settings.OPENAI_PRICE_CACHED_INPUT_USD_PER_1M,
        salida_usd_1m=settings.OPENAI_PRICE_OUTPUT_USD_PER_1M,
        audio_usd_minuto=settings.OPENAI_PRICE_AUDIO_USD_PER_MINUTE,
    )


def limpiar_cache() -> None:
    _cache.clear()


def precio_de(modelo: str) -> PrecioModelo:
    """El precio vigente de ese modelo; los de respaldo si no hay fila ni base."""
    clave = str(modelo or "")
    ahora = time.monotonic()
    guardado = _cache.get(clave)
    if guardado and (ahora - guardado[0]) < CACHE_TTL_SEGUNDOS:
        return guardado[1]

    try:
        fila = consultar_uno(
            """
            SELECT precio_input_usd_1m, precio_cached_input_usd_1m,
                   precio_output_usd_1m, precio_audio_usd_minuto
            FROM precios_modelo
            WHERE modelo = %s AND vigente_desde <= NOW()
            ORDER BY vigente_desde DESC, id DESC
            LIMIT 1
            """,
            (clave,),
        )
    except Exception as e:
        print(f"Error resolviendo el precio del modelo {clave}: {e}")
        return _respaldo()

    if not fila:
        # Un modelo sin fila es un despliegue a medias (se cambió el modelo y no
        # se cargó su precio). Se avisa y se cobra con el respaldo: registrar el
        # consumo como gratis sería el error caro.
        print(f"Sin precio configurado para el modelo {clave}; se usa el de respaldo.")
        return _respaldo()

    precio = PrecioModelo(
        entrada_usd_1m=float(fila["precio_input_usd_1m"]),
        cacheado_usd_1m=float(fila["precio_cached_input_usd_1m"]),
        salida_usd_1m=float(fila["precio_output_usd_1m"]),
        audio_usd_minuto=float(fila["precio_audio_usd_minuto"]),
    )
    _cache[clave] = (ahora, precio)
    return precio
