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

# Render Authelia config (no-op if AUTH_MODE=none)
./scripts/render-authelia.sh || true

# Pick profile based on AUTH_MODE
set -a
. ./.env
set +a
AUTH_MODE="${AUTH_MODE:-none}"
LDAP_URL="${LDAP_URL:-}"

PROFILES=""
if [ "$AUTH_MODE" != "none" ]; then
    PROFILES="--profile auth"
fi

# Auto-enable test LLDAP if AUTH_MODE=ldap and LDAP_URL points at local lldap container
case "$LDAP_URL" in
    *lldap:*)
        PROFILES="$PROFILES --profile dev-ldap"
        echo "[up] LDAP_URL points at local lldap, enabling dev-ldap profile."
        ;;
esac

docker compose $PROFILES up -d "$@"

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
