#!/bin/sh
# Renders caddy/Caddyfile from caddy/Caddyfile.template using .env vars.
# Maps DOMAIN + HTTPS_MODE + AUTH_MODE into Caddy directives.

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
AUTH_MODE="${AUTH_MODE:-none}"
ACME_EMAIL="${ACME_EMAIL:-admin@example.com}"
# Trusted upstream proxy (when behind IZS / another reverse proxy).
# Use "private_ranges" to trust 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16
# Or set a specific IP/CIDR via TRUSTED_PROXIES in .env
TRUSTED_PROXIES="${TRUSTED_PROXIES:-private_ranges}"

# --- HTTPS_MODE -> Caddy directives ---
case "$HTTPS_MODE" in
    off)
        # HTTP only on port 80
        SITE_ADDRESS="http://${DOMAIN}"
        [ "$DOMAIN" = "localhost" ] && SITE_ADDRESS=":80"
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

# --- AUTH_MODE -> Caddy forward_auth + Authelia portal block ---
case "$AUTH_MODE" in
    none)
        AUTH_DIRECTIVE=""
        AUTHELIA_PORTAL_BLOCK=""
        ;;
    file|ldap)
        AUTH_DIRECTIVE='forward_auth authelia:9091 {
        uri /api/verify?rd=https://__DOMAIN__/authelia/
        copy_headers Remote-User Remote-Groups Remote-Name Remote-Email
    }'
        AUTHELIA_PORTAL_BLOCK='handle_path /authelia/* {
        reverse_proxy authelia:9091
    }'
        ;;
    *)
        echo "ERROR: AUTH_MODE must be one of: none, file, ldap" >&2
        exit 1
        ;;
esac

export SITE_ADDRESS TLS_DIRECTIVE CADDY_AUTO_HTTPS ACME_EMAIL TRUSTED_PROXIES

# Render template, then inject AUTH/AUTHELIA blocks via python (envsubst chokes on {placeholders})
envsubst '${SITE_ADDRESS} ${TLS_DIRECTIVE} ${CADDY_AUTO_HTTPS} ${ACME_EMAIL} ${TRUSTED_PROXIES}' < caddy/Caddyfile.template > caddy/Caddyfile.tmp

export AUTH_DIRECTIVE AUTHELIA_PORTAL_BLOCK DOMAIN
python3 -c "
import os
content = open('caddy/Caddyfile.tmp').read()
content = content.replace('\${AUTH_DIRECTIVE}', os.environ['AUTH_DIRECTIVE'])
content = content.replace('\${AUTHELIA_PORTAL_BLOCK}', os.environ['AUTHELIA_PORTAL_BLOCK'])
content = content.replace('__DOMAIN__', os.environ['DOMAIN'])
open('caddy/Caddyfile', 'w').write(content)
"
rm -f caddy/Caddyfile.tmp

echo "[render-caddyfile] DOMAIN=${DOMAIN} HTTPS_MODE=${HTTPS_MODE} AUTH_MODE=${AUTH_MODE}"
echo "[render-caddyfile] Wrote caddy/Caddyfile"
