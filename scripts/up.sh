#!/bin/sh
# One-shot bootstrap: render configs from .env and bring up the stack.

set -e

cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
    echo "ERROR: .env not found. Run: cp .env.example .env  and edit it." >&2
    exit 1
fi

# Render Caddyfile from template
./scripts/render-caddyfile.sh

set -a
. ./.env
set +a

docker compose up -d "$@"

PORT="${CADDY_HOST_PORT:-80}"
HTTPS_PORT="${CADDY_HOST_HTTPS_PORT:-443}"

echo ""
echo "Stack is up. Access it at:"
case "${HTTPS_MODE:-off}" in
    off)
        if [ "$PORT" = "80" ]; then
            echo "  http://${DOMAIN:-localhost}"
        else
            echo "  http://${DOMAIN:-localhost}:${PORT}"
        fi
        ;;
    internal|auto)
        if [ "$HTTPS_PORT" = "443" ]; then
            echo "  https://${DOMAIN}"
        else
            echo "  https://${DOMAIN}:${HTTPS_PORT}"
        fi
        ;;
esac
