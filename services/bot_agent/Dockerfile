# Imagen multi-stage:
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

# --- Etapa de desarrollo/test: añade las dependencias de test ---
FROM base AS dev
COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt
CMD ["python", "src/main.py"]

# --- Etapa de producción (FINAL / por defecto): sin dependencias de test ---
FROM base AS prod
CMD ["python", "src/main.py"]
