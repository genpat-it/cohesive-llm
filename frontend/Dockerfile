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

# Minimal nginx config: serve files, fall back to index.html.
# Unprivileged image listens on 8080 by default (cannot bind <1024 as non-root).
USER root
RUN echo 'server { \
    listen 8080; \
    server_name _; \
    root /usr/share/nginx/html; \
    index index.html; \
    location / { try_files $uri $uri/ /index.html; } \
}' > /etc/nginx/conf.d/default.conf
USER nginx

EXPOSE 8080
