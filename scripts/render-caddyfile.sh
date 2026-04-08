#!/bin/sh
# Renders caddy/Caddyfile from caddy/Caddyfile.template using .env vars.
# Maps DOMAIN + HTTPS_MODE into Caddy directives.

set -e

cd "$(dirname "$0")/.."

# Load .env
if [ -f .env ]; then
    set -a
    . ./.env
    set +a
fi

DOMAIN="${DOMAIN:-localhost}"
HTTPS_MODE="${HTTPS_MODE:-off}"
ACME_EMAIL="${ACME_EMAIL:-admin@example.com}"
# Trusted upstream proxy (when behind another reverse proxy).
# Use "private_ranges" to trust 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16
# Or set a specific IP/CIDR via TRUSTED_PROXIES in .env
TRUSTED_PROXIES="${TRUSTED_PROXIES:-private_ranges}"

# --- HTTPS_MODE -> Caddy directives ---
case "$HTTPS_MODE" in
    off)
        # HTTP only. Accept any Host header (more flexible for port forwarding,
        # different DNS aliases, behind reverse proxies that rewrite Host, etc).
        SITE_ADDRESS=":80"
        TLS_DIRECTIVE=""
        CADDY_AUTO_HTTPS="off"
        ;;
    internal)
        # Self-signed via Caddy local CA
        SITE_ADDRESS="${DOMAIN}"
        TLS_DIRECTIVE="tls internal"
        CADDY_AUTO_HTTPS="disable_redirects"
        ;;
    auto)
        # Let's Encrypt automatic
        SITE_ADDRESS="${DOMAIN}"
        TLS_DIRECTIVE=""
        CADDY_AUTO_HTTPS="on"
        ;;
    *)
        echo "ERROR: HTTPS_MODE must be one of: off, internal, auto" >&2
        exit 1
        ;;
esac

export SITE_ADDRESS TLS_DIRECTIVE CADDY_AUTO_HTTPS ACME_EMAIL TRUSTED_PROXIES

envsubst '${SITE_ADDRESS} ${TLS_DIRECTIVE} ${CADDY_AUTO_HTTPS} ${ACME_EMAIL} ${TRUSTED_PROXIES}' \
    < caddy/Caddyfile.template > caddy/Caddyfile

echo "[render-caddyfile] DOMAIN=${DOMAIN} HTTPS_MODE=${HTTPS_MODE}"
echo "[render-caddyfile] Wrote caddy/Caddyfile"
