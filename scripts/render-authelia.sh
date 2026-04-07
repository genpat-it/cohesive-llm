#!/bin/sh
# Renders Authelia configuration based on AUTH_MODE.
# Skipped if AUTH_MODE=none.

set -e

cd "$(dirname "$0")/.."

if [ -f .env ]; then
    set -a
    . ./.env
    set +a
fi

AUTH_MODE="${AUTH_MODE:-none}"

if [ "$AUTH_MODE" = "none" ]; then
    echo "[render-authelia] AUTH_MODE=none, skipping Authelia config."
    exit 0
fi

# Required secrets
: "${AUTHELIA_JWT_SECRET:?Set AUTHELIA_JWT_SECRET in .env}"
: "${AUTHELIA_SESSION_SECRET:?Set AUTHELIA_SESSION_SECRET in .env}"
: "${AUTHELIA_STORAGE_KEY:?Set AUTHELIA_STORAGE_KEY in .env}"
: "${DOMAIN:?Set DOMAIN in .env}"

CONFIG=auth/authelia/configuration.yml
TPL=auth/authelia/configuration.template.yml

# 1. Render base template (without backend block)
export AUTHELIA_JWT_SECRET AUTHELIA_SESSION_SECRET AUTHELIA_STORAGE_KEY DOMAIN
envsubst '${AUTHELIA_JWT_SECRET} ${AUTHELIA_SESSION_SECRET} ${AUTHELIA_STORAGE_KEY} ${DOMAIN}' < "$TPL" > "$CONFIG.tmp"

# 2. Build backend block
case "$AUTH_MODE" in
    file)
        if [ ! -f auth/authelia/users.yml ]; then
            echo "[render-authelia] Creating users.yml from example (default password: authelia)"
            cp auth/authelia/users.example.yml auth/authelia/users.yml
        fi
        BACKEND="
  file:
    path: /config/users.yml
    password:
      algorithm: argon2id"
        ;;
    ldap)
        : "${LDAP_URL:?Set LDAP_URL in .env}"
        : "${LDAP_BASE_DN:?Set LDAP_BASE_DN in .env}"
        : "${LDAP_USER:?Set LDAP_USER (bind DN) in .env}"
        : "${LDAP_PASSWORD:?Set LDAP_PASSWORD in .env}"
        : "${LDAP_USERS_FILTER:?Set LDAP_USERS_FILTER in .env}"
        : "${LDAP_GROUPS_FILTER:?Set LDAP_GROUPS_FILTER in .env}"
        LDAP_ADDITIONAL_USERS_DN="${LDAP_ADDITIONAL_USERS_DN:-ou=people}"
        LDAP_ADDITIONAL_GROUPS_DN="${LDAP_ADDITIONAL_GROUPS_DN:-ou=groups}"
        BACKEND="
  ldap:
    address: ${LDAP_URL}
    implementation: custom
    base_dn: ${LDAP_BASE_DN}
    additional_users_dn: ${LDAP_ADDITIONAL_USERS_DN}
    users_filter: ${LDAP_USERS_FILTER}
    additional_groups_dn: ${LDAP_ADDITIONAL_GROUPS_DN}
    groups_filter: ${LDAP_GROUPS_FILTER}
    user: ${LDAP_USER}
    password: ${LDAP_PASSWORD}
    attributes:
      username: uid
      display_name: cn
      mail: mail
      member_of: memberOf"
        ;;
    *)
        echo "ERROR: AUTH_MODE must be one of: none, file, ldap" >&2
        exit 1
        ;;
esac

# 3. Substitute backend placeholder using python (handles special chars safely)
export BACKEND
python3 -c "
import os
content = open('$CONFIG.tmp').read()
content = content.replace('__BACKEND_PLACEHOLDER__', os.environ['BACKEND'])
open('$CONFIG', 'w').write(content)
"
rm -f "$CONFIG.tmp"

echo "[render-authelia] AUTH_MODE=${AUTH_MODE}"
echo "[render-authelia] Wrote ${CONFIG}"
