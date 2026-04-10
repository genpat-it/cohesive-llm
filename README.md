# Cohesive LLM — IZS Bioinformatics AI Platform

[![Status](https://img.shields.io/badge/status-under%20development-orange?style=flat-square)](#)
[![License: MIT](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12-blue?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![LangGraph](https://img.shields.io/badge/LangGraph-1.0-1c3c3c?style=flat-square)](https://langchain-ai.github.io/langgraph/)
[![Mistral](https://img.shields.io/badge/LLM-Mistral-ff7000?style=flat-square)](https://mistral.ai/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?style=flat-square&logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![Caddy](https://img.shields.io/badge/Caddy-2-1f88c0?style=flat-square&logo=caddy&logoColor=white)](https://caddyserver.com/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=flat-square&logo=docker&logoColor=white)](https://docs.docker.com/compose/)
[![Last commit](https://img.shields.io/github/last-commit/genpat-it/cohesive-llm?style=flat-square)](https://github.com/genpat-it/cohesive-llm/commits/main)
[![Issues](https://img.shields.io/github/issues/genpat-it/cohesive-llm?style=flat-square)](https://github.com/genpat-it/cohesive-llm/issues)

> ⚠️ **Status: under active development.** APIs, the database schema, the
> docker-compose layout and the configuration variables can all change between
> commits without notice. Do **not** use this in production yet — it has no
> stable release, no formal security audit and no upgrade story.

Self-hostable platform that lets bioinformaticians describe a sequencing analysis in plain English and get back a valid Nextflow DSL2 pipeline for the [cohesive-ngsmanager](https://github.com/genpat-it/cohesive-ngsmanager) framework.

## Credits

This platform was started as a master thesis project by three students of the
[**EDISS — European Master in Digital Innovation for Sustainable Society**](https://www.master-ediss.eu/),
carried out remotely in collaboration with the **Istituto Zooprofilattico
Sperimentale dell'Abruzzo e del Molise "G. Caporale" (IZS Teramo)**:

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
├── frontend/            Static chat UI (HTML/CSS/JS) — served by Caddy
├── caddy/               Caddy image (reverse proxy + static file server)
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

### Behind a corporate reverse proxy (e.g. `https://intranet.example.com/llm`)

When you can't expose ports 80/443 directly and an upstream proxy
(nginx, F5, Caddy, IIS, …) handles TLS for a public URL like
`https://intranet.example.com/llm`, run the platform on a custom HTTP port and
let the upstream proxy forward to it.

```env
DOMAIN=intranet.example.com
HTTPS_MODE=off                # upstream proxy handles TLS
CADDY_HOST_PORT=9000          # host port Caddy listens on (HTTP)
TRUSTED_PROXIES=10.0.0.0/8    # CIDR of the upstream proxy network
CORS_ORIGINS=https://intranet.example.com
COOKIE_SECURE=true            # cookie sent only over HTTPS
COOKIE_PATH=/llm              # scope the session cookie to the sub-path
COOKIE_SAMESITE=lax
BASE_PATH=/llm/               # MUST end with a trailing slash
```

`BASE_PATH` is what makes the app sub-path-aware: Caddy substitutes the
`{{env "BASE_PATH"}}` placeholder in `index.html` and `login.html` at
request time (via the built-in Go `templates` directive), so all relative
URLs and fetch calls resolve under `/llm/...`. Set it once in `.env`,
restart Caddy, done. No code changes.

`PROXY_PREFIX` is **only** needed if you want to test the sub-path
locally without an upstream proxy: set `PROXY_PREFIX=/llm` and Caddy
itself handles `/llm/*` so `http://localhost:9000/llm/` works
end-to-end. In production, leave it empty — the corporate proxy strips
the prefix before reaching Caddy.

Then ask the sysadmins to configure the upstream proxy to:

1. Forward `https://intranet.example.com/llm/*` → `http://your-server:9000/*`
2. **Strip the `/llm` path prefix** before forwarding
3. Set the headers: `X-Forwarded-Proto: https`, `X-Forwarded-Host: intranet.example.com`, `X-Real-IP`, `X-Forwarded-For`
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

The container layout is intentionally minimal (3 services + 1 init container):

1. `ngsmanager-init` clones [cohesive-ngsmanager](https://github.com/genpat-it/cohesive-ngsmanager) into a Docker volume on first start.
2. `postgres` stores users, conversations and messages.
3. `backend` (FastAPI + LangGraph) reads the framework from `/ngsmanager`, builds the RAG knowledge base on first start, and serves `/api/auth/*`, `/api/chat`, `/api/conversations/*`.
4. `caddy` is the single entry point: serves the static frontend (HTML/JS/CSS) from `/srv` via `file_server` + `templates`, and reverse-proxies `/api/*` to the backend.

```
                  ┌──────────┐
   browser   ───▶ │  caddy   │  serves frontend at /llm/* (file_server)
                  │  :9000   │  proxies /llm/api/* → backend:8080
                  └────┬─────┘
                       │
              ┌────────┴────────┐
              ▼                 ▼
        ┌──────────┐      ┌──────────┐
        │ backend  │      │ postgres │
        │  :8080   │◀────▶│  :5432   │
        └──────────┘      └──────────┘
              │
              │ reads (read-only)
              ▼
        ┌──────────────┐
        │ /ngsmanager  │  (Docker volume, cloned by ngsmanager-init)
        └──────────────┘
```

## LLM architecture

The "AI" of the platform is a **LangGraph state machine** with grounded RAG.
The whole point is to make it impossible for the LLM to hallucinate tools
that don't exist in the cohesive-ngsmanager framework.

### The agent graph

```
         ┌──────────────┐
         │   /api/chat  │
         └──────┬───────┘
                ▼
       ┌──────────────────┐
       │ planner subgraph │
       │  ┌────────────┐  │
       │  │ consultant │  │  ← natural-language conversation,
       │  └────────────┘  │    builds the design plan with
       │  ┌────────────┐  │    RAG-validated component IDs
       │  │  trim msgs │  │
       │  └────────────┘  │
       └────────┬─────────┘
                │ status == APPROVED?
        ┌───────┴───────┐
        │ no            │ yes
        ▼               ▼
       END    ┌─────────────────────┐
              │ executor subgraph   │
              │  ┌─────────────┐    │
              │  │  hydrator   │    │  ← injects the actual .nf source
              │  └──────┬──────┘    │    code for every selected step
              │         ▼           │
              │  ┌─────────────┐    │
              │  │  architect  │◀──┐│  ← generates a strict Pydantic AST
              │  └──────┬──────┘   ││    (NextflowPipelineAST)
              │         │ valid?   ││
              │      ┌──┴──┐       ││
              │      │ no  │ yes   ││
              │      ▼     │       ││
              │   ┌──────┐ │       ││
              │   │repair├─┘       ││  ← max 8 retries with the
              │   └──┬───┘         ││    validation error injected
              │      │             ││
              │      └─────────────┘│
              │         ▼           │
              │  ┌─────────────┐    │
              │  │  renderer   │    │  ← AST → Nextflow Groovy via Jinja2
              │  └──────┬──────┘    │
              │         ▼           │
              │  ┌─────────────┐    │
              │  │   diagram   │    │  ← AST → Mermaid (deterministic)
              │  └─────────────┘    │
              └─────────────────────┘
                         │
                         ▼
                 nextflow_code, mermaid_code, ast_json
```

Source: `backend/app/services/graph.py` and `backend/app/services/agents.py`.

### Models

| Role | Model | Provider |
|---|---|---|
| Main LLM (consultant + architect) | `labs-devstral-small-2512` | Mistral (`langchain-mistralai`) |
| Embeddings (RAG semantic search) | `Qwen/Qwen3-Embedding-0.6B` | local (`langchain-huggingface`) |
| Judge LLM (eval / `test_consultant_rag.py` only) | `llama-3.3-70b-versatile` | Groq (optional, only for evaluation) |

Configured in `backend/app/services/llm.py` and `backend/app/core/config.py`.
The Mistral key is **required** at runtime; the Groq key is only needed for the evaluation suite.

### Knowledge base

Lives in `backend/data/`:

| File / dir | What it is | Generated by |
|---|---|---|
| `data/catalog/catalog_part1_components.json` | Per-step metadata: tool, domain, inputs, outputs, keywords | `sync_framework.py` |
| `data/catalog/catalog_part2_templates.json` | Per-pipeline-template metadata + `logic_flow` | `sync_framework.py` |
| `data/catalog/catalog_part3_resources.json` | Helper Groovy functions extracted from the framework | `sync_framework.py` |
| `data/catalog/tool_whitelist.json` | Flat allow-list of every tool the LLM is allowed to mention | `sync_framework.py` |
| `data/code_store_hollow.jsonl` | The **actual** Groovy source of every step / template, indexed by ID | `sync_framework.py` |
| `data/faiss_index/` | Binary FAISS index built from the catalog text, used for semantic search | `rebuild_faiss_index.py` |

The two-stage retrieval inside `backend/app/services/tools.py` first does
**keyword + metadata scoring** over the JSON catalogs, then falls back to a
**FAISS semantic search** with relative-distance pruning, and finally
**hydrates** every selected ID with the verbatim Groovy source from
`code_store_hollow.jsonl` so the architect LLM never has to invent tool flags.

A diagram of the same flow lives in [`backend/data/README.md`](backend/data/README.md).

## Updating the framework and rebuilding the knowledge base

The catalog and FAISS index are derived artifacts that must be regenerated
whenever the upstream `cohesive-ngsmanager` framework changes (new step,
renamed module, updated params, …).

### Update only the framework checkout

```bash
docker compose run --rm ngsmanager-init   # git pull inside the volume
docker compose restart backend
```

This refreshes the source files under `/ngsmanager` but **does not** rebuild
the catalog or the embeddings — the backend keeps using the previously
generated `data/catalog/*.json` and `data/faiss_index/`.

### Full sync (catalog + whitelist + FAISS)

When the framework changed in a meaningful way, run the full sync. It walks
`/ngsmanager`, regenerates all catalog files, the tool whitelist, and the
FAISS index:

```bash
# inside the backend container, with the framework already mounted at /ngsmanager
docker compose exec backend python sync_framework.py

# or, if you want to skip the (slower) FAISS rebuild during iteration:
docker compose exec backend python sync_framework.py --skip-faiss

# from the host, pointing at any local checkout:
docker compose exec backend python sync_framework.py --ngsmanager-dir /ngsmanager
```

After it finishes, **restart the backend** so the new index is loaded into memory:

```bash
docker compose restart backend
```

### Only rebuild the FAISS index

If you tweaked one of the `catalog_part*.json` files by hand and just want
to refresh the embeddings (no framework re-scan):

```bash
docker compose exec backend python rebuild_faiss_index.py
docker compose restart backend
```

The first run downloads the `Qwen/Qwen3-Embedding-0.6B` model (~1.2 GB)
into `HF_HOME=/tmp/huggingface`, so it takes a couple of minutes; subsequent
rebuilds reuse the cached model and finish in seconds.

## Testing the LLM pipeline

The backend ships with several test/eval scripts under `backend/`. They are
**not** wired into the runtime container and use `subprocess` against the
real Nextflow CLI for validation, so they're meant to be run manually
(typically from a host with `nextflow` installed and the framework cloned
side-by-side).

### Quick smoke test of the LangGraph state machine

`backend/test_graph.py` runs a single in-process invocation, useful to
verify the graph compiles and can produce a Nextflow string:

```bash
docker compose exec backend python test_graph.py
```

### Mermaid renderer unit tests

`backend/test_mermaid.py` validates the deterministic AST → Mermaid renderer
in isolation (no LLM call, no Mistral key required):

```bash
docker compose exec backend python test_mermaid.py
```

### End-to-end pipeline validation against the real framework

`backend/test_e2e.py` is the heavy one: it sends a curated list of
user prompts (`L1` simple → `L4` complex) to `POST /api/chat`, extracts the
generated `.nf` code, drops it into the framework's `pipelines/` dir as
`_llm_e2e_test.nf`, and runs `nextflow -preview` on it to make sure the
generated workflow is syntactically and semantically valid against the
actual cohesive-ngsmanager modules.

```bash
# all scenarios
python backend/test_e2e.py

# only the L1 + L2 levels
python backend/test_e2e.py --levels 1 2

# single ad-hoc prompt
python backend/test_e2e.py --prompt "Trim Illumina paired-end reads with fastp"
```

Requires:
- The backend reachable at `http://localhost:8080` (override with `API_URL`)
- A working `nextflow` binary on the host
- The `cohesive-ngsmanager` repo cloned locally and pointed at via `NGSMANAGER_DIR`

### LLM-as-judge academic evaluation

`backend/test_consultant_rag.py` uses **pytest** plus a Groq-hosted Llama-3.3
"strict academic reviewer" to score the consultant, architect and diagram
nodes on faithfulness, relevance, syntax, logic and mapping. Needs both
`MISTRAL_API_KEY` and `GROQ_API_KEY` in the environment:

```bash
docker compose exec -e GROQ_API_KEY=... backend pytest -v test_consultant_rag.py
```

### Full evaluation report

`backend/evaluate_llm.py` is the most exhaustive: it walks every prompt
scenario, validates every generated pipeline against a hard-coded allow-list
of valid framework components, and writes a full markdown report.

```bash
docker compose exec backend python evaluate_llm.py --output report.md
```

### Single-pipeline validator

`backend/validate_pipeline.py` takes one `.nf` file (or one prompt) and runs
just the `nextflow -preview` validation step against the framework. Handy
when iterating on a single failing scenario:

```bash
python backend/validate_pipeline.py path/to/pipeline.nf
```

## Scripts

### Repo-level (`scripts/`)
- `up.sh` — render Caddyfile from `.env` and `docker compose up -d`
- `render-caddyfile.sh` — generate `caddy/Caddyfile` from the env vars
- `check-secrets.sh` — secret scanner, runs as a git pre-push hook

Install the pre-push hook:
```bash
ln -sf ../../scripts/check-secrets.sh .git/hooks/pre-push
```

### Backend-level (`backend/`, run inside the container)
- `sync_framework.py` — full sync from cohesive-ngsmanager: code store, catalog, whitelist, FAISS
- `rebuild_faiss_index.py` — only rebuild the FAISS index from the existing catalog JSONs
- `generate_catalog.py` — only regenerate the catalog (sub-step of `sync_framework.py`)
- `test_graph.py` — quick LangGraph smoke test
- `test_mermaid.py` — unit tests for the AST → Mermaid renderer
- `test_e2e.py` — end-to-end pipeline generation + `nextflow -preview` validation
- `test_consultant_rag.py` — pytest + Groq-judged academic evaluation
- `evaluate_llm.py` — full markdown evaluation report
- `validate_pipeline.py` — single-`.nf`/single-prompt validator
- `generate_report.py` — assemble a report from previously cached results
- `generate_catalog.py` — regenerate catalog files from the framework

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
| `BASE_PATH` | `/` | Sub-path the app is served under (must end with `/`) |

Full reference with comments: [`.env.example`](.env.example)
