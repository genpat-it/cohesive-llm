# Static frontend served by nginx
FROM nginx:1.27-alpine

# Copy static assets
COPY index.html /usr/share/nginx/html/
COPY style.css /usr/share/nginx/html/
COPY js /usr/share/nginx/html/js
COPY logo_izs.png /usr/share/nginx/html/
COPY logo-izs-blue.jpg /usr/share/nginx/html/

# Minimal nginx config: serve files, fall back to index.html
RUN echo 'server { \
    listen 80; \
    server_name _; \
    root /usr/share/nginx/html; \
    index index.html; \
    location / { try_files $uri $uri/ /index.html; } \
}' > /etc/nginx/conf.d/default.conf

EXPOSE 80
