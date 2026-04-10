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
        conn.execute(_sql_text(
            "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS drawing_id INTEGER"
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

    # --- Framework (ngsmanager git hash) ---
    try:
        framework_dir = os.getenv("NGSMANAGER_DIR", "/ngsmanager")
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=framework_dir, text=True, timeout=5,
        ).strip()
        repo_url = subprocess.check_output(
            ["git", "config", "--get", "remote.origin.url"],
            cwd=framework_dir, text=True, timeout=5,
        ).strip().replace(".git", "")
        info["framework"] = {
            "commit": commit,
            "repo_url": repo_url,
        }
    except Exception:
        pass

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
    warnings: List[str] = []
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
        all_output = (result.stderr or "") + "\n" + (result.stdout or "")
        errors = []
        for line in all_output.split("\n"):
            line = line.strip()
            if any(kw in line for kw in ["ERROR", "Error", "No such file", "Unable to", "not found", "Cannot find"]):
                errors.append(line)

        if not errors:
            errors = [result.stderr.strip()[-500:]] if result.stderr.strip() else [f"Exit code {result.returncode}"]

        # "missing required params" means the code is syntactically valid
        # but needs runtime parameters — treat as success with warning
        is_params_only = all(
            "missing required params" in e.lower() or "missing params" in e.lower()
            for e in errors
        )
        if is_params_only:
            return ValidateResponse(success=True, warnings=errors[:10])

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


def _get_version_info():
    """Collect framework commit + LLM model for stamping artifacts."""
    import subprocess
    from app.core.config import settings
    info = {"llm_model": settings.LLM_MODEL}
    try:
        framework_dir = os.getenv("NGSMANAGER_DIR", "/ngsmanager")
        info["framework_commit"] = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=framework_dir, text=True, timeout=5,
        ).strip()
    except Exception:
        info["framework_commit"] = "unknown"
    return info


@app.get("/catalog/components")
def get_catalog_components(user: User = Depends(get_current_user)):
    """Return components grouped by domain for the visual drawer."""
    from collections import defaultdict
    groups = defaultdict(list)
    for comp_id, comp in data_loader.comp_db.items():
        domain = comp.get("domain", "Other") or "Other"
        groups[domain].append({
            "id": comp_id,
            "tool": comp.get("tool", ""),
            "description": comp.get("description", ""),
            "inputs": comp.get("input_channels", comp.get("input_types", [])),
            "outputs": comp.get("output_channels", comp.get("out", [])),
            "seq_types": comp.get("compatible_seq_types", []),
            "file_path": comp.get("file_path", ""),
        })
    return dict(sorted(groups.items()))


class GraphGenerateRequest(BaseModel):
    nodes: List[Dict[str, Any]] = Field(..., description="List of component nodes with IDs and positions")
    edges: List[Dict[str, Any]] = Field(..., description="List of connections between nodes")
    drawing_id: Optional[int] = None
    graph_json: Optional[Dict[str, Any]] = None


@app.post("/generate-from-graph", response_model=ChatResponse)
async def generate_from_graph(
    request: GraphGenerateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Generate a pipeline from a visual graph, skipping the consultant."""
    from app.routes.conversations import get_or_create_conversation, append_message

    # Build a design plan from the graph
    component_ids = [n["component_id"] for n in request.nodes]
    connections = []
    for e in request.edges:
        src = next((n for n in request.nodes if n["node_id"] == e["source"]), None)
        tgt = next((n for n in request.nodes if n["node_id"] == e["target"]), None)
        if src and tgt:
            connections.append(f"{src['component_id']} → {tgt['component_id']}")

    plan = "## Visual Pipeline Design\n\n"
    plan += "### Components (in order):\n"
    for cid in component_ids:
        plan += f"- {cid}\n"
    plan += "\n### Data Flow:\n"
    for conn in connections:
        plan += f"- {conn}\n"
    plan += "\n### Instructions:\n"
    plan += "Generate a Nextflow DSL2 pipeline using exactly these components in the order and connections shown above.\n"

    # Auto-save drawing
    from app.models.db_models import Drawing
    drawing_id = request.drawing_id
    if request.graph_json:
        if drawing_id:
            drawing = db.query(Drawing).filter(Drawing.id == drawing_id, Drawing.user_id == user.id).first()
            if drawing:
                request.graph_json["_version"] = _get_version_info()
                drawing.graph_json = request.graph_json
                db.commit()
        else:
            title = "Drawer: " + ", ".join(c.split("__")[-1] for c in component_ids[:3])
            request.graph_json["_version"] = _get_version_info()
            drawing = Drawing(user_id=user.id, title=title, graph_json=request.graph_json)
            db.add(drawing)
            db.commit()
            db.refresh(drawing)
            drawing_id = drawing.id

    # Create conversation
    session_id = f"drawer_{user.id}_{os.urandom(4).hex()}"
    conv = get_or_create_conversation(db, user, session_id, "Visual pipeline design")
    conv.drawing_id = drawing_id
    db.commit()
    append_message(db, conv, "user", f"[Visual drawer] Components: {', '.join(component_ids)}")

    # Run executor subgraph directly
    thread_id = f"u{user.id}:drawer_{os.urandom(4).hex()}"
    config = {"configurable": {"thread_id": thread_id}}

    try:
        result = await app_graph.ainvoke(
            {
                "user_query": plan,
                "messages": [("user", plan)],
                "consultant_status": "APPROVED",
                "design_plan": plan,
                "selected_module_ids": component_ids,
                "strategy_selector": "CUSTOM_BUILD",
                "used_template_id": None,
            },
            config=config,
        )

        nf_code = result.get("nextflow_code")
        mermaid = result.get("mermaid_code")
        ast_json = result.get("ast_json")
        error = result.get("error")

        messages = result.get("messages", [])
        reply = "Pipeline generated from visual design."
        for msg in reversed(messages):
            if msg.type == "ai" and msg.content:
                reply = msg.content
                break

        append_message(db, conv, "assistant", reply,
                       nextflow_code=nf_code, mermaid_code=mermaid, ast_json=ast_json)

        return ChatResponse(
            status="APPROVED" if nf_code else "failed",
            reply=reply,
            conversation_id=conv.id,
            nextflow_code=nf_code,
            mermaid_code=mermaid,
            ast_json=ast_json,
            error=error,
        )
    except Exception as e:
        return ChatResponse(status="failed", reply="Generation failed", error=str(e))


# --- Drawing CRUD ---
class DrawingSave(BaseModel):
    title: str = "Untitled"
    graph_json: Dict[str, Any]


class DrawingOut(BaseModel):
    id: int
    title: str
    created_at: Any
    updated_at: Any

    class Config:
        from_attributes = True


class DrawingDetail(DrawingOut):
    graph_json: Dict[str, Any]


@app.get("/drawings")
def list_drawings(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.models.db_models import Drawing
    rows = db.query(Drawing).filter(Drawing.user_id == user.id).order_by(Drawing.updated_at.desc()).all()
    return [{"id": r.id, "title": r.title, "created_at": r.created_at, "updated_at": r.updated_at} for r in rows]


@app.post("/drawings")
def save_drawing(payload: DrawingSave, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.models.db_models import Drawing
    # Stamp version info
    payload.graph_json["_version"] = _get_version_info()
    drawing = Drawing(user_id=user.id, title=payload.title, graph_json=payload.graph_json)
    db.add(drawing)
    db.commit()
    db.refresh(drawing)
    return {"id": drawing.id, "title": drawing.title}


@app.put("/drawings/{drawing_id}")
def update_drawing(drawing_id: int, payload: DrawingSave, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.models.db_models import Drawing
    from datetime import datetime
    d = db.query(Drawing).filter(Drawing.id == drawing_id, Drawing.user_id == user.id).first()
    if not d:
        raise HTTPException(status_code=404, detail="Drawing not found")
    d.title = payload.title
    payload.graph_json["_version"] = _get_version_info()
    d.graph_json = payload.graph_json
    d.updated_at = datetime.utcnow()
    db.commit()
    return {"id": d.id, "title": d.title}


@app.get("/drawings/{drawing_id}")
def get_drawing(drawing_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.models.db_models import Drawing
    d = db.query(Drawing).filter(Drawing.id == drawing_id, Drawing.user_id == user.id).first()
    if not d:
        raise HTTPException(status_code=404, detail="Drawing not found")
    return {"id": d.id, "title": d.title, "graph_json": d.graph_json, "created_at": d.created_at, "updated_at": d.updated_at}


@app.delete("/drawings/{drawing_id}")
def delete_drawing(drawing_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.models.db_models import Drawing
    d = db.query(Drawing).filter(Drawing.id == drawing_id, Drawing.user_id == user.id).first()
    if not d:
        raise HTTPException(status_code=404, detail="Drawing not found")
    db.delete(d)
    db.commit()
    return {"status": "ok"}


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
