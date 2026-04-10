import os
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any

# Import your custom modules
from app.core.loader import data_loader
from app.services.graph import app_graph, global_store

# Auth / DB
from app.db import Base, engine, get_db
from app.models.db_models import User
from app.routes.auth import router as auth_router
from app.routes.conversations import (
    router as conversations_router,
    get_or_create_conversation,
    append_message,
)
from app.services.auth import get_current_user, hash_password
from app.services.rate_limit import limiter


# --- 1. DATA MODELS (Request/Response) ---
class ChatRequest(BaseModel):
    session_id: str = Field(..., description="Unique ID for the user session to remember chat history")
    message: str = Field(..., description="The user's prompt or reply")

class ChatResponse(BaseModel):
    status: str
    reply: str
    conversation_id: Optional[int] = None
    nextflow_code: Optional[str] = None
    mermaid_code: Optional[str] = None
    ast_json: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


# --- 2. STARTUP HELPERS ---
def init_db_and_seed():
    """Create tables, run small in-place migrations and seed the demo user."""
    Base.metadata.create_all(bind=engine)

    # Lightweight migrations (idempotent). Used instead of Alembic because the
    # schema is small and only ever grows by adding nullable columns.
    from sqlalchemy import text as _sql_text
    with engine.begin() as conn:
        conn.execute(_sql_text(
            "ALTER TABLE messages ADD COLUMN IF NOT EXISTS nextflow_code TEXT"
        ))
        conn.execute(_sql_text(
            "ALTER TABLE messages ADD COLUMN IF NOT EXISTS mermaid_code TEXT"
        ))
        conn.execute(_sql_text(
            "ALTER TABLE messages ADD COLUMN IF NOT EXISTS ast_json JSONB"
        ))

    from app.db import SessionLocal
    db: Session = SessionLocal()
    try:
        username = os.getenv("DEMO_USER", "demo")
        password = os.getenv("DEMO_PASSWORD", "change_me_please")
        existing = db.query(User).filter(User.username == username).first()
        if not existing:
            user = User(username=username, password_hash=hash_password(password))
            db.add(user)
            db.commit()
            print(f"[startup] Seeded user '{username}'")
        else:
            print(f"[startup] User '{username}' already exists")
    finally:
        db.close()


# --- 3. LIFESPAN ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        init_db_and_seed()
    except Exception as e:
        print(f"DB INIT ERROR: {e}")
    try:
        data_loader.load_all(store=global_store)
    except Exception as e:
        print(f"CRITICAL STARTUP ERROR {e}")
    yield
    print("Server shutting down...")


# --- 4. APP DEFINITION ---
app = FastAPI(
    title="Nextflow AI Agent API",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS origins driven by env. Comma-separated list.
# Dev default keeps localhost so the dev workflow on http://localhost:9000 works.
# Production: set CORS_ORIGINS=https://cohesive.izs.it
_cors_env = os.getenv("CORS_ORIGINS", "http://localhost:9000,http://127.0.0.1:9000")
_cors_origins = [o.strip() for o in _cors_env.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate limiting (slowapi). The actual per-route limits are declared on the
# routes themselves with @limiter.limit("…"). When the limit is exceeded the
# default handler returns 429 with a JSON body.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# Routers
app.include_router(auth_router)
app.include_router(conversations_router)


# --- 5. ENDPOINTS ---
@app.get("/health")
def health_check():
    return {
        "status": "online",
        "vector_store": "loaded" if data_loader.vector_store else "not_loaded",
    }


@app.get("/system-info")
def system_info():
    """Return model names, GPU and RAM stats for the frontend dashboard."""
    import shutil
    import subprocess

    from app.core.config import settings

    info: dict = {
        "llm_model": settings.LLM_MODEL,
        "embedding_model": settings.EMBEDDING_MODEL,
        "gpu": None,
        "ram": None,
    }

    # --- GPU (nvidia-smi) ---
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu",
             "--format=csv,noheader,nounits"],
            text=True, timeout=5,
        ).strip()
        if out:
            parts = [p.strip() for p in out.split(",")]
            info["gpu"] = {
                "name": parts[0],
                "vram_used_mb": int(parts[1]),
                "vram_total_mb": int(parts[2]),
                "utilization_pct": int(parts[3]),
                "temperature_c": int(parts[4]),
            }
    except Exception:
        pass

    # --- RAM ---
    try:
        import psutil
        vm = psutil.virtual_memory()
        info["ram"] = {
            "used_mb": int(vm.used / 1024 / 1024),
            "total_mb": int(vm.total / 1024 / 1024),
            "percent": vm.percent,
        }
    except ImportError:
        # fallback: read /proc/meminfo
        try:
            with open("/proc/meminfo") as f:
                lines = {l.split(":")[0]: l.split(":")[1].strip() for l in f if ":" in l}
            total_kb = int(lines["MemTotal"].split()[0])
            avail_kb = int(lines["MemAvailable"].split()[0])
            used_kb = total_kb - avail_kb
            info["ram"] = {
                "used_mb": used_kb // 1024,
                "total_mb": total_kb // 1024,
                "percent": round(used_kb / total_kb * 100, 1),
            }
        except Exception:
            pass

    return info


class ValidateRequest(BaseModel):
    nextflow_code: str = Field(..., description="The Nextflow code to validate")


class ValidateResponse(BaseModel):
    success: bool
    errors: List[str] = []
    stdout: Optional[str] = None


@app.post("/validate", response_model=ValidateResponse)
async def validate_pipeline(
    request: ValidateRequest,
    user: User = Depends(get_current_user),
):
    """Validate Nextflow code via nextflow -preview against the framework."""
    import subprocess
    import tempfile
    from pathlib import Path

    framework_dir = Path(os.getenv("NGSMANAGER_DIR", "/ngsmanager"))
    pipelines_dir = framework_dir / "pipelines"

    if not pipelines_dir.exists():
        return ValidateResponse(success=False, errors=["Framework not found at /ngsmanager"])

    # Write to /tmp, then symlink into pipelines/ so relative includes resolve
    tmp_file = Path("/tmp/_llm_validate_tmp.nf")
    link_file = pipelines_dir / "_llm_validate_tmp.nf"
    try:
        tmp_file.write_text(request.nextflow_code)

        # Create symlink (remove stale one first)
        if link_file.exists() or link_file.is_symlink():
            link_file.unlink()
        link_file.symlink_to(tmp_file)

        env = {
            **os.environ,
            "NXF_HOME": "/tmp/nxf_home",
            "NXF_WORK": "/tmp/nxf_work",
            "NXF_TEMP": "/tmp",
            "NXF_LOG_FILE": "/tmp/nxf.log",
        }
        os.makedirs("/tmp/nxf_home", exist_ok=True)
        os.makedirs("/tmp/nxf_work", exist_ok=True)

        result = subprocess.run(
            ["nextflow", "run", str(link_file), "-preview"],
            capture_output=True,
            text=True,
            cwd="/tmp",
            env=env,
            timeout=30,
        )

        if result.returncode == 0:
            return ValidateResponse(success=True, stdout=result.stdout[-1000:] if result.stdout else None)

        # Extract error lines from both stderr and stdout
        errors = []
        for output in [result.stderr, result.stdout]:
            for line in (output or "").split("\n"):
                line = line.strip()
                if any(kw in line for kw in ["ERROR", "Error", "No such file", "Unable to", "not found", "Cannot find"]):
                    errors.append(line)

        if not errors:
            errors = [result.stderr.strip()[-500:]] if result.stderr.strip() else [f"Exit code {result.returncode}"]
        return ValidateResponse(success=False, errors=errors[:10])

    except subprocess.TimeoutExpired:
        return ValidateResponse(success=False, errors=["Validation timed out after 30s"])
    except Exception as e:
        return ValidateResponse(success=False, errors=[str(e)])
    finally:
        if link_file.is_symlink():
            link_file.unlink()
        if tmp_file.exists():
            tmp_file.unlink()


@app.post("/chat", response_model=ChatResponse)
async def chat_with_agent(
    request: ChatRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        # Get or create the conversation row for this user/session
        conv = get_or_create_conversation(db, user, request.session_id, request.message)
        append_message(db, conv, "user", request.message)

        # Per-user thread id so different users never share LangGraph state
        thread_id = f"u{user.id}:{request.session_id}"
        config = {"configurable": {"thread_id": thread_id}}

        result = await app_graph.ainvoke(
            {
                "user_query": request.message,
                "messages": [("user", request.message)],
            },
            config=config,
        )

        if result.get("error"):
            return ChatResponse(
                status="failed",
                reply="The agent encountered an error.",
                conversation_id=conv.id,
                error=result["error"],
            )

        messages = result.get("messages", [])
        ai_reply = "No response generated."
        for msg in reversed(messages):
            if msg.type == "ai" and msg.content:
                ai_reply = msg.content
                break

        status = result.get("consultant_status", "CHATTING")
        nf_code = result.get("nextflow_code")
        ast_json = result.get("ast_json")
        mermaid = result.get("mermaid_code")

        # Persist assistant reply (with pipeline artifacts when available)
        append_message(
            db, conv, "assistant", ai_reply,
            nextflow_code=nf_code,
            mermaid_code=mermaid,
            ast_json=ast_json,
        )

        return ChatResponse(
            status=status,
            reply=ai_reply,
            conversation_id=conv.id,
            nextflow_code=nf_code,
            mermaid_code=mermaid,
            ast_json=ast_json,
            error=None,
        )

    except HTTPException:
        raise
    except Exception as e:
        print(f"Server Error {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
