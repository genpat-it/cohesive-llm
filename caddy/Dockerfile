# Custom Caddy image: reverse proxy + static file server + Go template
# substitution. Replaces the previous standalone nginx-unprivileged
# frontend container.
#
# Build context is the repo root (see docker-compose.yml) so we can COPY
# both the Caddyfile and the frontend assets in one shot.

FROM caddy:2-alpine

# Static frontend assets served by Caddy's file_server
COPY frontend/index.html      /srv/
COPY frontend/login.html      /srv/
COPY frontend/style.css       /srv/
COPY frontend/js              /srv/js
COPY frontend/logo_izs.png    /srv/
COPY frontend/logo-izs-blue.jpg /srv/

# Caddyfile is bind-mounted from the host (rendered by render-caddyfile.sh)
# so we don't bake it into the image — see docker-compose.yml.
