# IZS Bioinformatics AI Platform

Self-hostable platform that lets bioinformaticians describe a sequencing analysis in plain English and get back a valid Nextflow DSL2 pipeline for the [cohesive-ngsmanager](https://github.com/genpat-it/cohesive-ngsmanager) framework.

## Credits

This platform is based on the work of three master students at IZS:

- **Martinus Grady** — [@mgradyn](https://github.com/mgradyn)
- **Ligan Cai** — [@Tsailgan](https://github.com/Tsailgan)
- **Zeynal Mardanli** — [@Lshiroc](https://github.com/Lshiroc)

Upstream repositories:

- **Backend** (LangGraph + FastAPI LLM agent) — [mgradyn/izs-llm](https://github.com/mgradyn/izs-llm)
- **Frontend** (chat UI) — [mgradyn/izs-bioinformatics-AI-demo](https://github.com/mgradyn/izs-bioinformatics-AI-demo)

The Nextflow framework being targeted is [genpat-it/cohesive-ngsmanager](https://github.com/genpat-it/cohesive-ngsmanager).

## What's inside

```
izs-bioinformatics-platform/
├── backend/             FastAPI + LangGraph LLM (anti-hallucination, AST validation)
├── frontend/            Static chat UI (HTML/CSS/JS)
├── caddy/               Reverse proxy template
├── auth/authelia/       Optional auth (file-based or LDAP)
├── scripts/             Render configs and start the stack
├── docker-compose.yml   All services orchestrated
└── .env.example         Configuration template
```

## Quick start for developers — local, no auth, plain HTTP

This is the fastest way to get the stack running on your laptop without any login or HTTPS hassle.

```bash
git clone <this-repo>
cd izs-bioinformatics-platform
cp .env.example .env
```

Open `.env` and make sure you have:

```env
DOMAIN=localhost
HTTPS_MODE=off
AUTH_MODE=none
MISTRAL_API_KEY=your_real_key_here
```

Then start the stack:

```bash
./scripts/up.sh
```

The first run will:
1. Clone `cohesive-ngsmanager` into a Docker volume (one-time, ~1 min)
2. Build the backend image (downloads Python deps and embeddings, ~5 min)
3. Start backend, frontend, and Caddy reverse proxy

When it's done, open **http://localhost** in your browser. You should land directly on the chat — no login required.

To stop everything:

```bash
docker compose down
```

To wipe state and start fresh (re-clones ngsmanager, rebuilds FAISS):

```bash
docker compose down -v
```

### Tail the logs

```bash
docker compose logs -f backend
docker compose logs -f caddy
```

### Test the API directly (bypass the frontend)

```bash
curl http://localhost/api/health
curl -X POST http://localhost/api/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id":"dev","message":"I want to trim with fastp"}'
```

## Deployment modes

### Local development (HTTP, no domain)
```env
DOMAIN=localhost
HTTPS_MODE=off
AUTH_MODE=none
```

### IZS intranet (internal hostname, no public domain)
```env
DOMAIN=ai.izs.intra
HTTPS_MODE=internal     # self-signed cert
AUTH_MODE=ldap          # corporate login
```
Browser will warn about self-signed cert on first visit (one click to accept).

### Public deployment (real domain, Let's Encrypt)
```env
DOMAIN=ai.example.com
HTTPS_MODE=auto         # automatic Let's Encrypt
AUTH_MODE=ldap
ACME_EMAIL=ops@example.com
```
Requires ports 80 and 443 reachable from the internet and DNS pointing at the server.

### Behind another reverse proxy (e.g. corporate ingress)

When you can't expose ports 80/443 directly and an upstream proxy
(nginx, F5, Caddy, IIS, ...) handles TLS for a public URL like
`https://cohesive.example.com/llm`, run the platform on a custom HTTP
port and let the upstream proxy forward to it.

```env
DOMAIN=cohesive.example.com
HTTPS_MODE=off                # upstream proxy handles TLS
AUTH_MODE=file                # or 'ldap'
CADDY_HOST_PORT=10000         # host port Caddy listens on (HTTP)
CADDY_HOST_HTTPS_PORT=10443   # host port for HTTPS (unused in this mode)
TRUSTED_PROXIES=10.0.0.0/8    # CIDR of the upstream proxy network
```

Then ask your sysadmins to configure the upstream proxy to:

1. Forward `https://cohesive.example.com/llm/*` → `http://your-server:10000/*`
2. **Strip the `/llm` path prefix** before forwarding
3. Set the headers: `X-Forwarded-Proto: https`, `X-Forwarded-Host: cohesive.example.com`, `X-Real-IP`
4. Allow body sizes up to 10 MB
5. Tell you the proxy IP/CIDR so you can set `TRUSTED_PROXIES` correctly

Once configured, login flows (Authelia file/LDAP) work end-to-end because
Caddy trusts the upstream's `X-Forwarded-Proto` header and treats the
request as if it were HTTPS.

## Authentication modes

Set `AUTH_MODE` in `.env`:

| Mode | Use case | Setup |
|------|----------|-------|
| `none` | Demo, internal trusted network | Nothing — open access |
| `file` | Small team, no LDAP | Edit `auth/authelia/users.yml` (created from `users.example.yml` on first run) |
| `ldap` | Corporate (e.g. IZS Active Directory) | Set `LDAP_*` variables in `.env` |

For `file` and `ldap`, generate three secrets in `.env`:
```bash
openssl rand -hex 32  # AUTHELIA_JWT_SECRET
openssl rand -hex 32  # AUTHELIA_SESSION_SECRET
openssl rand -hex 32  # AUTHELIA_STORAGE_KEY
```

Generate a password hash for file mode:
```bash
docker run --rm authelia/authelia:latest \
  authelia crypto hash generate argon2 --password 'your-password'
```

Then paste the resulting `$argon2id$...` string into `auth/authelia/users.yml`.

### Notes on Authelia and HTTPS

Authelia requires HTTPS for session cookies in production.
For local testing without TLS:
- Use `HTTPS_MODE=internal` to get a self-signed cert via Caddy local CA, or
- Use `HTTPS_MODE=off` only when sitting behind an upstream HTTPS-terminating proxy
  (the upstream's `X-Forwarded-Proto: https` header is what Authelia trusts)

Authelia rejects `localhost` as a cookie domain. Use a real FQDN or a development
TLD like `.localhost` (e.g. `app.localhost`).

## How it works

1. `ngsmanager-init` (init container) clones [cohesive-ngsmanager](https://github.com/genpat-it/cohesive-ngsmanager) into a Docker volume on first start.
2. `backend` reads the framework from `/ngsmanager` and uses it to:
   - Build the RAG knowledge base (FAISS + Qwen embeddings)
   - Validate every generated pipeline against the real `.nf` files
3. `frontend` (static HTML/JS) sends prompts to `/api/chat`.
4. `caddy` routes requests:
   - `/api/*` → `backend:8080`
   - `/*` → `frontend:80`
   - Optionally enforces auth via Authelia
5. `authelia` (optional) checks user credentials before letting requests through.

## Updating the ngsmanager framework

```bash
docker compose run --rm ngsmanager-init
docker compose restart backend
```

## Scripts

- `scripts/up.sh` — render configs, pick auth profile, start everything
- `scripts/render-caddyfile.sh` — generate Caddyfile from template + .env
- `scripts/render-authelia.sh` — generate Authelia config from template + .env

## Configuration reference

| Variable | Default | Description |
|----------|---------|-------------|
| `DOMAIN` | `localhost` | Hostname users access the platform with |
| `HTTPS_MODE` | `off` | `off` / `internal` / `auto` |
| `AUTH_MODE` | `none` | `none` / `file` / `ldap` |
| `CADDY_HOST_PORT` | `80` | Host port for HTTP listener |
| `CADDY_HOST_HTTPS_PORT` | `443` | Host port for HTTPS listener |
| `TRUSTED_PROXIES` | `private_ranges` | CIDR(s) of upstream proxies |
| `MISTRAL_API_KEY` | _required_ | API key for the LLM provider |
| `NGSMANAGER_REPO` | github.com/genpat-it/cohesive-ngsmanager | Framework repo to clone |
| `NGSMANAGER_BRANCH` | `main` | Framework branch |
| `AUTHELIA_JWT_SECRET` | _required if auth_ | 32+ char random string |
| `AUTHELIA_SESSION_SECRET` | _required if auth_ | 32+ char random string |
| `AUTHELIA_STORAGE_KEY` | _required if auth_ | 32+ char random string |
| `LDAP_*` | _required if ldap_ | See [`.env.example`](.env.example) |

Full reference with comments: [`.env.example`](.env.example)
