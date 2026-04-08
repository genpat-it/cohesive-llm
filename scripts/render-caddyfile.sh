#!/bin/sh
# Renders caddy/Caddyfile from .env vars.
#
# Caddy serves both the static frontend (file_server + Go templates) and
# the /api/* reverse proxy to the backend, replacing the old separate
# nginx-unprivileged frontend container.
#
# Inputs (all optional):
#   DOMAIN, HTTPS_MODE (off|internal|auto), ACME_EMAIL, TRUSTED_PROXIES
#   PROXY_PREFIX  e.g. "/llm" → makes Caddy serve the app under that
#                 sub-path. Leave empty when an upstream reverse proxy
#                 already strips the prefix.

set -e

cd "$(dirname "$0")/.."

if [ -f .env ]; then
    set -a
    . ./.env
    set +a
fi

DOMAIN="${DOMAIN:-localhost}"
HTTPS_MODE="${HTTPS_MODE:-off}"
ACME_EMAIL="${ACME_EMAIL:-admin@example.com}"
TRUSTED_PROXIES="${TRUSTED_PROXIES:-private_ranges}"
PROXY_PREFIX="${PROXY_PREFIX:-}"
PROXY_PREFIX="${PROXY_PREFIX%/}"  # strip trailing slash

# --- HTTPS_MODE -> Caddy directives ---
case "$HTTPS_MODE" in
    off)
        SITE_ADDRESS=":80"
        TLS_DIRECTIVE=""
        CADDY_AUTO_HTTPS="off"
        ;;
    internal)
        SITE_ADDRESS="${DOMAIN}"
        TLS_DIRECTIVE="    tls internal"
        CADDY_AUTO_HTTPS="disable_redirects"
        ;;
    auto)
        SITE_ADDRESS="${DOMAIN}"
        TLS_DIRECTIVE=""
        CADDY_AUTO_HTTPS="on"
        ;;
    *)
        echo "ERROR: HTTPS_MODE must be one of: off, internal, auto" >&2
        exit 1
        ;;
esac

# --- Routing blocks ---
if [ -z "$PROXY_PREFIX" ]; then
    API_HANDLE="    handle /api/* {
        uri strip_prefix /api
        reverse_proxy backend:8080
    }"
    APP_HANDLE='    handle {
        root * /srv
        templates
        # Allow extension-less URLs like /login → /login.html
        try_files {path} {path}.html {path}/ /index.html
        file_server
    }'
    REDIR_BLOCK=""
else
    API_HANDLE="    handle ${PROXY_PREFIX}/api/* {
        uri strip_prefix ${PROXY_PREFIX}/api
        reverse_proxy backend:8080
    }"
    APP_HANDLE="    handle ${PROXY_PREFIX}/* {
        uri strip_prefix ${PROXY_PREFIX}
        root * /srv
        templates
        try_files {path} {path}.html {path}/ /index.html
        file_server
    }"
    # /llm → /llm/  and  anything outside /llm/* → /llm/
    REDIR_BLOCK="    redir ${PROXY_PREFIX} ${PROXY_PREFIX}/
    @outside not path ${PROXY_PREFIX}/*
    redir @outside ${PROXY_PREFIX}/"
fi

cat > caddy/Caddyfile <<EOF
# Caddyfile rendered by scripts/render-caddyfile.sh from .env values.

{
    auto_https ${CADDY_AUTO_HTTPS}
    email ${ACME_EMAIL}

    # Trust upstream reverse proxy headers (X-Forwarded-Proto, X-Real-IP).
    servers {
        trusted_proxies static ${TRUSTED_PROXIES}
    }
}

${SITE_ADDRESS} {
${TLS_DIRECTIVE}
${API_HANDLE}

${APP_HANDLE}

${REDIR_BLOCK}
    log {
        output stdout
        format console
    }
}
EOF

echo "[render-caddyfile] DOMAIN=${DOMAIN} HTTPS_MODE=${HTTPS_MODE} PROXY_PREFIX='${PROXY_PREFIX}'"
echo "[render-caddyfile] Wrote caddy/Caddyfile"
