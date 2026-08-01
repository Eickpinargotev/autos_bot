#!/usr/bin/env bash
# Expone el webhook local a internet para poder probar WasenderAPI de verdad.
#
# WasenderAPI necesita una URL pública con HTTPS: no puede entregarle nada a
# `localhost`. Este script levanta un túnel de Cloudflare contra el puerto 8010
# y deja la URL pública escrita en el `.env` (`PUBLIC_WEBHOOK_BASE_URL`), que es
# de donde el panel arma la dirección que se copia en WasenderAPI.
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

echo "Levantando el webhook local…"
docker compose -f "$RAIZ/docker-compose.local.yml" up -d whatsapp_webhook >/dev/null

echo "Abriendo el túnel…"
cloudflared tunnel --url http://localhost:8010 --no-autoupdate >"$LOG" 2>&1 &
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
docker compose -f "$RAIZ/docker-compose.local.yml" up -d dashboard >/dev/null

cat <<FIN

  URL pública del webhook:  $URL

  La dirección completa que se pega en WasenderAPI (con el token de cada
  negocio) está en el panel:  http://localhost:8020/admin/webhooks

  Comprobación rápida:
    curl $URL/health

  Deja esta terminal abierta: al cerrarla se cae el túnel y la URL se pierde.

FIN

wait $TUNEL_PID
