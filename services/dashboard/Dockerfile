# Misma estructura multi-stage que el bot:
#   - base: dependencias de producción + código (común a todo).
#   - dev:  base + dependencias de test (requirements-dev.txt). Se usa en LOCAL.
#   - prod: etapa FINAL por defecto. La nube (docker-compose.yml) la construye.
#
# IMPORTANTE: `prod` es la última etapa, así `docker build` sin --target produce
# la imagen de producción (sin pytest ni herramientas de test).

FROM python:3.11-slim AS base
WORKDIR /app
# Copiamos primero los requirements para aprovechar el caché de Docker
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Copiamos el resto del código (en desarrollo se sobreescribe con el bind mount)
COPY . .

# Sin esto, Python almacena la salida en un buffer y mensajes importantes del
# arranque (como la contraseña temporal del administrador) no aparecen en
# `docker compose logs` hasta que el buffer se llena.
ENV PYTHONUNBUFFERED=1
# Para que los tests y cualquier script suelto importen `src.*` igual que la app.
ENV PYTHONPATH=/app

# `--timeout-graceful-shutdown` NO es un detalle: el panel mantiene abierto un
# flujo de eventos (`GET /eventos`) por cada pestaña, y uvicorn, al apagarse,
# espera a que las conexiones vivas terminen solas. Un flujo no termina nunca,
# así que el proceso se quedaba en «Waiting for connections to close» hasta que
# Docker lo mataba a los 10 s (código 137). Con esto corta a los 5 y sale
# limpio: los reinicios y los despliegues dejan de tardar y de matar a golpes.
#
# UN SOLO WORKER, a propósito: el hub de novedades (`core/eventos.py`) vive
# dentro del proceso, y con varios cada uno haría su propio tick contra Postgres.

# --- Etapa de desarrollo/test: añade las dependencias de test ---
FROM base AS dev
COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8020", \
     "--reload", "--timeout-graceful-shutdown", "5"]

# --- Etapa de producción (FINAL / por defecto): sin dependencias de test ---
FROM base AS prod
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8020", \
     "--timeout-graceful-shutdown", "5"]
