#!/usr/bin/env bash
# Expone el webhook local a internet para poder probar WasenderAPI de verdad.
#
# WasenderAPI necesita una URL pública con HTTPS: no puede entregarle nada a
# `localhost`. Este script levanta un túnel de Cloudflare contra el webhook y
# deja la URL pública escrita en el `.env` (`PUBLIC_WEBHOOK_BASE_URL`), que es
# de donde el panel arma la dirección que se copia en WasenderAPI.
#
# El puerto del host es AUTOMÁTICO (ver la cabecera de docker-compose.local.yml),
# así que no se puede escribir aquí: se le pregunta a Docker con `compose port`
# después de levantar el servicio.
#
# Se usa cloudflared y no ngrok porque ngrok exige crear cuenta y configurar un
# authtoken hasta en el plan gratuito; los túneles rápidos de Cloudflare no
# piden nada.
#
#   ./scripts/tunel_webhook.sh
#
# La URL CAMBIA cada vez que se reinicia el túnel: hay que volver a pegarla en
# WasenderAPI. Es para pruebas; en el VPS se usa un dominio fijo.

set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$RAIZ/.env"
LOG="$(mktemp -t tunel_webhook)"

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "Falta cloudflared. Instálalo con:  brew install cloudflared" >&2
  exit 1
fi

COMPOSE=(docker compose -f "$RAIZ/docker-compose.local.yml")

echo "Levantando el webhook local…"
"${COMPOSE[@]}" up -d whatsapp_webhook >/dev/null

# `compose port` devuelve "0.0.0.0:54321": nos quedamos con lo de después de los
# dos puntos, que es el puerto que Docker le asignó en esta arrancada.
PUERTO="$("${COMPOSE[@]}" port whatsapp_webhook 8010 2>/dev/null | tail -1 | sed 's/.*://')"
if [ -z "$PUERTO" ]; then
  echo "No se pudo averiguar el puerto del webhook. ¿Arrancó el contenedor?" >&2
  "${COMPOSE[@]}" ps whatsapp_webhook >&2
  exit 1
fi
echo "El webhook quedó en el puerto $PUERTO del host."

echo "Abriendo el túnel…"
cloudflared tunnel --url "http://localhost:$PUERTO" --no-autoupdate >"$LOG" 2>&1 &
TUNEL_PID=$!
# Si se corta el script, que no quede un túnel huérfano ocupando el puerto.
trap 'kill $TUNEL_PID 2>/dev/null || true' EXIT

URL=""
for _ in $(seq 1 30); do
  URL="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" | head -1 || true)"
  [ -n "$URL" ] && break
  sleep 1
done

if [ -z "$URL" ]; then
  echo "El túnel no dio una URL. Log completo en $LOG" >&2
  exit 1
fi

# Se reescribe la variable en el .env para que el panel muestre la URL correcta.
if [ -f "$ENV_FILE" ] && grep -q '^PUBLIC_WEBHOOK_BASE_URL=' "$ENV_FILE"; then
  # -i '' es la forma de sed en macOS; en Linux sería -i sin argumento.
  sed -i '' "s|^PUBLIC_WEBHOOK_BASE_URL=.*|PUBLIC_WEBHOOK_BASE_URL=$URL|" "$ENV_FILE"
else
  printf '\nPUBLIC_WEBHOOK_BASE_URL=%s\n' "$URL" >>"$ENV_FILE"
fi

echo "Reiniciando el panel para que tome la URL nueva…"
"${COMPOSE[@]}" up -d dashboard >/dev/null

# Igual que el del webhook: el panel también sale en un puerto automático.
PUERTO_PANEL="$("${COMPOSE[@]}" port dashboard 8020 2>/dev/null | tail -1 | sed 's/.*://')"
PANEL="http://localhost:${PUERTO_PANEL:-8020}"

cat <<FIN

  URL pública del webhook:  $URL

  La dirección completa que se pega en WasenderAPI (con el token de cada
  proyecto) está en su perfil:  $PANEL/admin/negocios

  Comprobación rápida:
    curl $URL/health

  Deja esta terminal abierta: al cerrarla se cae el túnel y la URL se pierde.

FIN

wait $TUNEL_PID
