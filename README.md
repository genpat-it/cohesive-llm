# Cohesive LLM — IZS Bioinformatics AI Platform

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
cohesive-llm/
├── backend/             FastAPI + LangGraph LLM (anti-hallucination, AST validation)
├── frontend/            Static chat UI (HTML/CSS/JS)
├── caddy/               Reverse proxy template
├── scripts/             Render configs and start the stack
├── docker-compose.yml   All services orchestrated
└── .env.example         Configuration template
```

## Quick start (local development)

```bash
git clone <this-repo>
cd cohesive-llm
cp .env.example .env
```

Open `.env` and at minimum set:

```env
MISTRAL_API_KEY=your_real_key_here
JWT_SECRET=$(openssl rand -hex 32)
DEMO_PASSWORD=pick_something_strong
```

Then start the stack:

```bash
./scripts/up.sh
```

The first run will:
1. Clone `cohesive-ngsmanager` into a Docker volume (one-time, ~1 min)
2. Build the backend image (downloads Python deps and embeddings, ~5 min)
3. Start Postgres, the backend, the frontend and Caddy

Open **http://localhost:9000** and log in with `demo` / the password you set in `DEMO_PASSWORD`.

To stop everything:

```bash
docker compose down
```

To wipe state and start fresh (re-clones ngsmanager, drops the DB, rebuilds FAISS):

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
curl -c cookies.txt -X POST http://localhost:9000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"demo","password":"<your password>"}'

curl -b cookies.txt http://localhost:9000/api/health
curl -b cookies.txt -X POST http://localhost:9000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id":"dev","message":"I want to trim with fastp"}'
```

## Authentication

Login is built into the backend itself: a single demo user is seeded automatically
on first start with the credentials taken from `DEMO_USER` / `DEMO_PASSWORD` in `.env`.

The session uses a stateless JWT stored in an `HttpOnly` cookie. Requests with no
valid cookie get a 401 from any `/api/*` route except `/api/auth/login`. The frontend
intercepts that 401 and redirects the browser to `/login.html`.

Failed login attempts are rate-limited per real client IP via `slowapi`
(default `5/minute`, tunable with `LOGIN_RATE_LIMIT`).

> Multi-user / corporate SSO (LDAP, OIDC) is **not** included on purpose — drop in
> a proxy-level auth layer (Authelia, oauth2-proxy, ...) in front of Caddy if you
> need it for production.

## Deployment modes

### Local development (HTTP, single demo user)
```env
DOMAIN=localhost
HTTPS_MODE=off
COOKIE_SECURE=false
```

### Behind a corporate reverse proxy (e.g. `https://cohesive.izs.it/llm`)

When you can't expose ports 80/443 directly and an upstream proxy
(nginx, F5, Caddy, IIS, …) handles TLS for a public URL like
`https://cohesive.izs.it/llm`, run the platform on a custom HTTP port and
let the upstream proxy forward to it.

```env
DOMAIN=cohesive.izs.it
HTTPS_MODE=off                # upstream proxy handles TLS
CADDY_HOST_PORT=9000          # host port Caddy listens on (HTTP)
TRUSTED_PROXIES=10.0.0.0/8    # CIDR of the upstream proxy network
CORS_ORIGINS=https://cohesive.izs.it
COOKIE_SECURE=true            # cookie sent only over HTTPS
COOKIE_PATH=/llm              # scope cookie to the sub-path
COOKIE_SAMESITE=lax
```

Then ask the sysadmins to configure the upstream proxy to:

1. Forward `https://cohesive.izs.it/llm/*` → `http://your-server:9000/*`
2. **Strip the `/llm` path prefix** before forwarding
3. Set the headers: `X-Forwarded-Proto: https`, `X-Forwarded-Host: cohesive.izs.it`, `X-Real-IP`, `X-Forwarded-For`
4. Allow body sizes up to ~10 MB
5. Tell you the proxy IP/CIDR so you can set `TRUSTED_PROXIES` correctly

### Self-signed HTTPS (no upstream proxy)
```env
DOMAIN=ai.izs.intra
HTTPS_MODE=internal
COOKIE_SECURE=true
```
Browser will warn about the self-signed cert on first visit (one click to accept).

### Public deployment (real domain, Let's Encrypt)
```env
DOMAIN=ai.example.com
HTTPS_MODE=auto
ACME_EMAIL=ops@example.com
COOKIE_SECURE=true
```
Requires ports 80 and 443 reachable from the internet and DNS pointing at the server.

## How it works

1. `ngsmanager-init` (init container) clones [cohesive-ngsmanager](https://github.com/genpat-it/cohesive-ngsmanager) into a Docker volume on first start.
2. `postgres` stores users, conversations and messages.
3. `backend` reads the framework from `/ngsmanager` and uses it to:
   - Build the RAG knowledge base (FAISS + Qwen embeddings)
   - Validate every generated pipeline against the real `.nf` files
   - Serve `/api/auth/*`, `/api/chat`, `/api/conversations/*`
4. `frontend` (static HTML/JS) shows the login page, the chat UI and a ChatGPT-style sidebar with conversation history.
5. `caddy` routes requests:
   - `/api/*` → `backend:8080`
   - `/*`     → `frontend:8080`

## Updating the ngsmanager framework

```bash
docker compose run --rm ngsmanager-init
docker compose restart backend
```

## Scripts

- `scripts/up.sh` — render Caddyfile and start the stack
- `scripts/render-caddyfile.sh` — generate the Caddyfile from `.env`
- `scripts/check-secrets.sh` — secret scanner, run before `git push`

Install `check-secrets.sh` as a pre-push hook:
```bash
ln -sf ../../scripts/check-secrets.sh .git/hooks/pre-push
```

## Configuration reference

| Variable | Default | Description |
|----------|---------|-------------|
| `DOMAIN` | `localhost` | Hostname users access the platform with |
| `HTTPS_MODE` | `off` | `off` / `internal` / `auto` |
| `CADDY_HOST_PORT` | `9000` | Host port for the HTTP listener |
| `CADDY_HOST_HTTPS_PORT` | `9443` | Host port for the HTTPS listener |
| `TRUSTED_PROXIES` | `private_ranges` | CIDR(s) of upstream proxies |
| `MISTRAL_API_KEY` | _required_ | API key for the LLM provider |
| `NGSMANAGER_REPO` | github.com/genpat-it/cohesive-ngsmanager | Framework repo to clone |
| `NGSMANAGER_BRANCH` | `main` | Framework branch |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `cohesive` / dev / `cohesive` | Database credentials |
| `DATABASE_URL` | `postgresql+psycopg2://...` | Connection string for SQLAlchemy |
| `JWT_SECRET` | _required_ | 32-byte random string for signing JWTs |
| `DEMO_USER` / `DEMO_PASSWORD` | `demo` / placeholder | Initial seed user |
| `CORS_ORIGINS` | `http://localhost:9000,...` | Comma-separated allowed origins |
| `COOKIE_SECURE` | `false` | Set to `true` behind HTTPS |
| `COOKIE_PATH` | `/` | Set to `/llm` if behind a path-prefix proxy |
| `COOKIE_SAMESITE` | `lax` | `lax` / `strict` / `none` |
| `LOGIN_RATE_LIMIT` | `5/minute` | Per-IP rate limit on `/auth/login` |

Full reference with comments: [`.env.example`](.env.example)
