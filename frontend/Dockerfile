# Static frontend served by the unprivileged nginx image (runs as UID 101).
# This avoids the default nginx image starting as root just to bind port 80.
FROM nginxinc/nginx-unprivileged:1.27-alpine

# Copy static assets
COPY --chown=nginx:nginx index.html /usr/share/nginx/html/
COPY --chown=nginx:nginx login.html /usr/share/nginx/html/
COPY --chown=nginx:nginx style.css /usr/share/nginx/html/
COPY --chown=nginx:nginx js /usr/share/nginx/html/js
COPY --chown=nginx:nginx logo_izs.png /usr/share/nginx/html/
COPY --chown=nginx:nginx logo-izs-blue.jpg /usr/share/nginx/html/

# Use the nginx-unprivileged template mechanism: any file in
# /etc/nginx/templates/*.template is processed with envsubst at container
# start, with output written to /etc/nginx/conf.d/<name>. This lets us inject
# the BASE_PATH env var into the sub_filter directive.
COPY --chown=nginx:nginx nginx.conf.template /etc/nginx/templates/default.conf.template

# Default to site root; override at runtime by setting BASE_PATH=/llm/ etc.
ENV BASE_PATH=/

EXPOSE 8080
