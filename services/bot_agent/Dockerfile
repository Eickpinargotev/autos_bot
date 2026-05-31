FROM python:3.11-slim

WORKDIR /app

# Copiamos primero los requirements para aprovechar el caché de Docker
COPY requirements.txt .

# Instalamos las dependencias
RUN pip install --no-cache-dir -r requirements.txt

# Copiamos el resto del código (aunque en desarrollo se sobreescribirá con el volumen)
COPY . .

# Comando por defecto para iniciar el bot
CMD ["python", "src/main.py"]
