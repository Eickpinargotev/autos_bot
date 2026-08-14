"""Cómo se llama a cada modelo, y cuánto cuesta.

Existe porque los modelos dejaron de ser intercambiables. Al repartir el trabajo
en tres niveles (supervisor, especialista, auxiliar) aparecieron dos diferencias
que, si se dejan sueltas por el código, rompen en producción y no en los tests:

1. **`temperature`.** La familia gpt-5.6 la RECHAZA con un 400 (`'temperature'
   does not support 0 with this model`); gpt-5.4-mini/nano la aceptan y la
   necesitan en 0 para que las decisiones sean deterministas (regla del
   proyecto). Mandar el parámetro equivocado no degrada la respuesta: tumba
   TODAS las llamadas y el bot contesta siempre con el fallback genérico.
2. **El precio.** El costo real ya no es uno solo. Cobrarle al cliente el precio
   del modelo caro por una llamada barata (o al revés) le falsea la factura.

Los precios viven en la BASE (`precios_modelo`), no aquí: son del negocio y se
editan desde el panel. Este módulo solo sabe resolverlos y cachearlos.
"""

from src.core.config import settings

# Modelos que rechazan `temperature`. Es una lista por FAMILIA porque el
# comportamiento es de la familia, no de la versión concreta: así un
# `gpt-5.6-luna` nuevo entra solo, sin que nadie se acuerde de añadirlo.
FAMILIAS_SIN_TEMPERATURE = ("gpt-5.6", "gpt-5.5", "o1", "o3", "o4")

# Modelos que aceptan `reasoning_effort`. Enviarlo a un gpt-4o lo hace fallar.
FAMILIAS_CON_RAZONAMIENTO = ("gpt-5",)


def acepta_temperature(modelo: str) -> bool:
    return not str(modelo or "").startswith(FAMILIAS_SIN_TEMPERATURE)


def acepta_razonamiento(modelo: str) -> bool:
    return str(modelo or "").startswith(FAMILIAS_CON_RAZONAMIENTO)


def kwargs_de_decision(modelo: str) -> dict:
    """Parámetros extra de una llamada de DECISIÓN, según lo que el modelo admita.

    `temperature=0` donde se pueda: es lo que hace que la misma conversación se
    enrute igual dos veces. Donde el modelo no la acepte, el determinismo se
    apoya solo en `reasoning_effort="none"` y en el esquema estricto de salida,
    que fija la FORMA de la respuesta aunque no la elección.
    """
    extra: dict = {}
    if acepta_temperature(modelo):
        extra["temperature"] = 0
    if settings.OPENAI_REASONING_EFFORT and acepta_razonamiento(modelo):
        extra["reasoning_effort"] = settings.OPENAI_REASONING_EFFORT
    return extra
