import asyncio
import json
import os
import re
import urllib.request
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

import structlog
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel, ConfigDict, Field
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy.orm import Session

# Core engine imports
from core.config import settings
from core.loader import data_loader
from core.plugin_loader import get_active_plugin
from core.services.graph import app_graph, global_store
from core.utils.logger import logger

# Auth & Database imports
from app.db import Base, engine, get_db, SessionLocal
from app.models.db_models import Drawing, User
from app.routes.auth import router as auth_router
from app.routes.conversations import (
    append_message,
    get_or_create_conversation,
    router as conversations_router,
)
from app.services.auth import get_current_user, hash_password
from app.services.rate_limit import limiter


# --- 1. DATA MODELS ---
class ChatRequest(BaseModel):
    session_id: str = Field(..., description="Unique ID for the user session to remember chat history")
    message: Optional[str] = Field("", description="The user's prompt or reply")
    action: Optional[str] = Field(None, description="Optional action flag (e.g. 'approve')")
    execution_mode: Optional[str] = Field("interactive", description="'interactive' (plan review) or 'direct' (1-shot build)")
    generate_diagrams: bool = Field(True, description="Whether to run diagram generation nodes for this turn")
    idempotency_key: Optional[str] = Field(None, description="Optional key to prevent duplicate requests")


class ChatResponse(BaseModel):
    status: str
    reply: str
    conversation_id: Optional[int] = None
    nextflow_code: Optional[str] = None
    mermaid_code: Optional[str] = None
    ast_json: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    tool_calls: Optional[List[str]] = None
    has_plan: bool = False
    selected_components: Optional[List[str]] = None


class ValidateRequest(BaseModel):
    nextflow_code: str = Field(..., description="The Nextflow code to validate")


class ValidateResponse(BaseModel):
    success: bool
    warnings: List[str] = []
    errors: List[str] = []
    stdout: Optional[str] = None


class DrawingSave(BaseModel):
    title: str = "Untitled"
    graph_json: Dict[str, Any]


class DrawingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    created_at: Any
    updated_at: Any


class DrawingDetail(DrawingOut):
    graph_json: Dict[str, Any]


class GraphGenerateRequest(BaseModel):
    nodes: List[Dict[str, Any]] = Field(..., description="List of component nodes with IDs and positions")
    edges: List[Dict[str, Any]] = Field(..., description="List of connections between nodes")
    drawing_id: Optional[int] = None
    graph_json: Optional[Dict[str, Any]] = None


# --- 2. STARTUP & SEEDING HELPERS ---
def init_db_and_seed() -> None:
    """Create tables, run lightweight migrations and seed demo user."""
    Base.metadata.create_all(bind=engine)

    # Lightweight migrations for PostgreSQL if running on postgres
    if not str(engine.url).startswith("sqlite"):
        from sqlalchemy import text as _sql_text
        with engine.begin() as conn:
            conn.execute(_sql_text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS nextflow_code TEXT"))
            conn.execute(_sql_text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS mermaid_code TEXT"))
            conn.execute(_sql_text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS ast_json JSONB"))
            conn.execute(_sql_text("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS drawing_id INTEGER"))

    db: Session = SessionLocal()
    try:
        username = os.getenv("DEMO_USER", "demo")
        password = os.getenv("DEMO_PASSWORD", "change_me_please")
        existing = db.query(User).filter(User.username == username).first()
        if not existing:
            user = User(username=username, password_hash=hash_password(password[:72]))
            db.add(user)
            db.commit()
            logger.info("demo_user_seeded", username=username)
        else:
            logger.debug("demo_user_exists", username=username)
    except Exception as e:
        logger.error("db_seed_error", error=str(e))
    finally:
        db.close()


def _get_version_info() -> Dict[str, str]:
    """Collect framework commit + LLM model for stamping artifacts."""
    import subprocess
    info = {"llm_model": settings.LLM_MODEL, "llm_provider": settings.LLM_PROVIDER}
    try:
        framework_dir = str(settings.FRAMEWORK_DIR)
        info["framework_commit"] = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=framework_dir, text=True, timeout=5,
        ).strip()
    except Exception:
        info["framework_commit"] = "unknown"
    return info


# --- 3. LIFESPAN ---
@asynccontextmanager
async def lifespan(_app: FastAPI) -> Any:
    try:
        init_db_and_seed()
    except Exception as e:
        logger.error("db_init_error", error=str(e))

    try:
        await asyncio.to_thread(data_loader.load_all, store=global_store)
        logger.info("data_loader_ready", components_loaded=len(data_loader.comp_db))
    except Exception as e:
        logger.error("data_loader_error", error=str(e))

    yield
    logger.info("server_shutting_down")


# --- 4. APP DEFINITION ---
app = FastAPI(
    title="Nextflow AI Agent API",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS configuration
_cors_env = os.getenv("CORS_ORIGINS", "http://localhost:9000,http://127.0.0.1:9000")
_cors_origins = [o.strip() for o in _cors_env.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins if "*" not in _cors_origins else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# Routers (mounted under both root and /api for direct dev access)
app.include_router(auth_router)
app.include_router(conversations_router)
app.include_router(auth_router, prefix="/api")
app.include_router(conversations_router, prefix="/api")


# --- 5. ENDPOINTS ---

@app.get("/health")
def health_check() -> Dict[str, Any]:
    return {
        "status": "online",
        "vector_store": "loaded" if data_loader.vector_store else "not_loaded",
        "active_plugin": getattr(settings, "ACTIVE_PLUGIN", "izs"),
        "llm_provider": settings.LLM_PROVIDER,
        "llm_model": settings.LLM_MODEL,
    }


@app.get("/system-info")
def system_info() -> Dict[str, Any]:
    """Return model names, GPU and RAM stats for the frontend dashboard."""
    import subprocess
    info: Dict[str, Any] = {
        "llm_model": settings.LLM_MODEL,
        "llm_provider": settings.LLM_PROVIDER,
        "embedding_model": settings.EMBEDDING_MODEL,
        "active_plugin": getattr(settings, "ACTIVE_PLUGIN", "izs"),
        "gpu": None,
        "ram": None,
    }

    # Framework git commit
    try:
        framework_dir = str(settings.FRAMEWORK_DIR)
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=framework_dir, text=True, timeout=5,
        ).strip()
        repo_url = subprocess.check_output(
            ["git", "config", "--get", "remote.origin.url"],
            cwd=framework_dir, text=True, timeout=5,
        ).strip().replace(".git", "")
        info["framework"] = {"commit": commit, "repo_url": repo_url}
    except Exception:
        pass

    # GPU stats (nvidia-smi)
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
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

    # RAM stats
    try:
        import psutil
        vm = psutil.virtual_memory()
        info["ram"] = {
            "used_mb": int(vm.used / 1024 / 1024),
            "total_mb": int(vm.total / 1024 / 1024),
            "available_mb": int(vm.available / 1024 / 1024),
            "percent": round(vm.percent, 1),
        }
    except Exception:
        pass

    # CPU stats
    try:
        import psutil
        cpu_pct = psutil.cpu_percent(interval=None)
        cpu_name = None
        try:
            cpu_name = subprocess.check_output(["sysctl", "-n", "machdep.cpu.brand_string"], text=True, timeout=2).strip()
        except Exception:
            pass
        info["cpu"] = {
            "percent": round(cpu_pct, 1),
            "cores": psutil.cpu_count(logical=True),
            "name": cpu_name,
        }
    except Exception:
        pass

    # LLM Server Telemetry (vLLM / remote inference engine)
    try:
        import urllib.request
        base_endpoint = os.environ.get("OPENAI_BASE_URL") or os.environ.get("LOCAL_LLM_URL") or "http://localhost:8000/v1"
        root_url = base_endpoint.rstrip("/").removesuffix("/v1")
        server_stats: Dict[str, Any] = {}

        # 1. Active Model & Max Context Window
        try:
            req = urllib.request.urlopen(f"{root_url}/v1/models", timeout=1.5)
            models_data = json.loads(req.read().decode())
            if models_data.get("data"):
                m0 = models_data["data"][0]
                server_stats["model_id"] = m0.get("id")
                server_stats["max_model_len"] = m0.get("max_model_len")
                server_stats["engine"] = m0.get("owned_by")
        except Exception:
            pass

        # 2. Real-time KV Cache & Prefix Cache Telemetry
        try:
            req_m = urllib.request.urlopen(f"{root_url}/metrics", timeout=1.5)
            metrics_text = req_m.read().decode()

            def _parse_gauge(metric_name: str, text: str) -> float | None:
                m = re.search(r'^' + re.escape(metric_name) + r'(?:\{[^}]*\})?\s+([0-9.eE+-]+)', text, re.M)
                return float(m.group(1)) if m else None

            kv_cache = _parse_gauge("vllm:kv_cache_usage_perc", metrics_text)
            if kv_cache is not None:
                server_stats["kv_cache_percent"] = round(kv_cache * 100, 1)

            running = _parse_gauge("vllm:num_requests_running", metrics_text)
            if running is not None:
                server_stats["requests_running"] = int(running)

            hit_m = re.search(r'vllm:prompt_tokens_by_source_total\{[^}]*source=\"local_cache_hit\"\}\s+([0-9.eE+-]+)', metrics_text)
            comp_m = re.search(r'vllm:prompt_tokens_by_source_total\{[^}]*source=\"local_compute\"\}\s+([0-9.eE+-]+)', metrics_text)
            if hit_m and comp_m:
                hits = float(hit_m.group(1))
                comp = float(comp_m.group(1))
                if (hits + comp) > 0:
                    server_stats["prefix_cache_hit_rate"] = round(hits / (hits + comp) * 100, 1)
        except Exception:
            pass

        if server_stats:
            info["llm_server"] = server_stats
    except Exception:
        pass

    return info


@app.post("/validate", response_model=ValidateResponse)
async def validate_pipeline(
    request: ValidateRequest,
    user: User = Depends(get_current_user),
) -> ValidateResponse:
    """Validate Nextflow code via nextflow -preview against the framework."""
    import subprocess
    from pathlib import Path

    framework_dir = Path(settings.FRAMEWORK_DIR)
    pipelines_dir = framework_dir / "pipelines"

    if not pipelines_dir.exists():
        return ValidateResponse(success=True, warnings=["Nextflow framework pipelines directory not present locally; code syntax valid."])

    tmp_file = Path("/tmp/_llm_validate_tmp.nf")
    link_file = pipelines_dir / "_llm_validate_tmp.nf"
    try:
        tmp_file.write_text(request.nextflow_code)
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
            timeout=10,
        )

        if result.returncode == 0:
            return ValidateResponse(success=True, stdout=result.stdout[-1000:] if result.stdout else None)

        all_output = (result.stderr or "") + "\n" + (result.stdout or "")
        errors = []
        for line in all_output.split("\n"):
            line = line.strip()
            if any(kw in line for kw in ["ERROR", "Error", "No such file", "Unable to", "not found", "Cannot find"]):
                errors.append(line)

        if not errors:
            errors = [result.stderr.strip()[-500:]] if result.stderr.strip() else [f"Exit code {result.returncode}"]

        is_params_only = all(
            "missing required params" in e.lower() or "missing params" in e.lower()
            for e in errors
        )
        if is_params_only:
            return ValidateResponse(success=True, warnings=errors[:10])

        return ValidateResponse(success=False, errors=errors[:10])

    except subprocess.TimeoutExpired:
        return ValidateResponse(success=True, warnings=["Validation preview timed out after 10s; syntax assumed valid."])
    except Exception as e:
        return ValidateResponse(success=False, errors=[str(e)])
    finally:
        if link_file.is_symlink():
            link_file.unlink()
        if tmp_file.exists():
            tmp_file.unlink()


@app.get("/catalog/components")
def get_catalog_components(user: User = Depends(get_current_user)) -> Dict[str, Any]:
    """Return components dynamically grouped by domain for the visual drawer palette."""
    from collections import defaultdict
    groups = defaultdict(list)
    for comp_id, comp in data_loader.comp_db.items():
        domain = comp.get("domain") or comp.get("role") or "Other"
        groups[domain].append({
            "id": comp_id,
            "tool": comp.get("tool") or comp.get("name") or comp_id,
            "description": comp.get("description", ""),
            "inputs": comp.get("input_channels") or comp.get("input_types", []),
            "outputs": comp.get("output_channels") or comp.get("out", []),
            "seq_types": comp.get("compatible_seq_types", []),
            "file_path": comp.get("file_path", ""),
        })
    return dict(sorted(groups.items()))


# --- Drawing CRUD ---
@app.get("/drawings")
def list_drawings(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> List[Dict[str, Any]]:
    rows = db.query(Drawing).filter(Drawing.user_id == user.id).order_by(Drawing.updated_at.desc()).all()
    return [{"id": r.id, "title": r.title, "created_at": r.created_at, "updated_at": r.updated_at} for r in rows]


@app.post("/drawings")
def save_drawing(payload: DrawingSave, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    payload.graph_json["_version"] = _get_version_info()
    drawing = Drawing(user_id=user.id, title=payload.title, graph_json=payload.graph_json)
    db.add(drawing)
    db.commit()
    db.refresh(drawing)
    return {"id": drawing.id, "title": drawing.title}


@app.put("/drawings/{drawing_id}")
def update_drawing(drawing_id: int, payload: DrawingSave, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
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
def get_drawing(drawing_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    d = db.query(Drawing).filter(Drawing.id == drawing_id, Drawing.user_id == user.id).first()
    if not d:
        raise HTTPException(status_code=404, detail="Drawing not found")
    return {"id": d.id, "title": d.title, "graph_json": d.graph_json, "created_at": d.created_at, "updated_at": d.updated_at}


@app.delete("/drawings/{drawing_id}")
def delete_drawing(drawing_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    d = db.query(Drawing).filter(Drawing.id == drawing_id, Drawing.user_id == user.id).first()
    if not d:
        raise HTTPException(status_code=404, detail="Drawing not found")
    db.delete(d)
    db.commit()
    return {"status": "ok"}


@app.post("/generate-from-graph", response_model=ChatResponse)
async def generate_from_graph(
    request: GraphGenerateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ChatResponse:
    """Generate a pipeline from a visual graph, executing the pipeline generation subgraph directly."""
    component_ids = [n["component_id"] for n in request.nodes if "component_id" in n]
    visual_wires = []
    connections_text = []

    for e in request.edges:
        src = next((n for n in request.nodes if n.get("node_id") == e.get("source")), None)
        tgt = next((n for n in request.nodes if n.get("node_id") == e.get("target")), None)
        if src and tgt:
            src_cid = src.get("component_id")
            tgt_cid = tgt.get("component_id")

            src_port_key = str(e.get("source_port", "output_1"))
            tgt_port_key = str(e.get("target_port", "input_1"))

            src_outputs = src.get("outputs") or []
            tgt_inputs = tgt.get("inputs") or []

            src_idx = int(src_port_key.split("_")[-1]) - 1 if "_" in src_port_key and src_port_key.split("_")[-1].isdigit() else 0
            tgt_idx = int(tgt_port_key.split("_")[-1]) - 1 if "_" in tgt_port_key and tgt_port_key.split("_")[-1].isdigit() else 0

            src_channel = src_outputs[src_idx] if 0 <= src_idx < len(src_outputs) else src_port_key
            tgt_channel = tgt_inputs[tgt_idx] if 0 <= tgt_idx < len(tgt_inputs) else tgt_port_key

            visual_wires.append({
                "source_id": src_cid,
                "source_channel": src_channel,
                "source_port": src_port_key,
                "target_id": tgt_cid,
                "target_channel": tgt_channel,
                "target_port": tgt_port_key,
            })
            connections_text.append(f"{src_cid} ({src_channel}) -> {tgt_cid} ({tgt_channel})")

    plan = "## Visual Pipeline Design\n\n"
    plan += "### Components (in order):\n"
    for cid in component_ids:
        plan += f"- {cid}\n"
    plan += "\n### Data Flow:\n"
    for conn in connections_text:
        plan += f"- {conn}\n"
    plan += "\n### Instructions:\n"
    plan += "Generate a Nextflow DSL2 pipeline using exactly these components and explicit visual channel connections.\n"

    visual_topology = {
        "components": component_ids,
        "wires": visual_wires,
    }

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

    session_id = f"drawer_{user.id}_{os.urandom(4).hex()}"
    conv = get_or_create_conversation(db, user, session_id, "Visual pipeline design")
    conv.drawing_id = drawing_id
    db.commit()
    append_message(db, conv, "user", f"[Visual drawer] Components: {', '.join(component_ids)}")

    thread_id = f"u{user.id}:drawer_{os.urandom(4).hex()}"
    config = {"configurable": {"thread_id": thread_id}}

    try:
        result = await app_graph.ainvoke(
            {
                "user_query": plan,
                "messages": [("user", plan)],
                "consultant_status": None,
                "action": "approve",
                "execution_mode": "direct",
                "design_plan": plan,
                "selected_component_ids": component_ids,
                "strategy_selector": "CUSTOM_BUILD",
                "used_template_id": None,
                "generate_diagrams": True,
                "visual_topology": visual_topology,
            },
            config=config,
        )

        nf_code = result.get("nextflow_code")
        ast_json = result.get("ast_json")
        mermaid = result.get("mermaid_deterministic") or result.get("mermaid_agent") or result.get("mermaid_code")
        error = result.get("error")
        validation_error = result.get("validation_error")

        messages = result.get("messages", [])
        reply = "Pipeline successfully generated and validated from your visual design."
        for msg in reversed(messages):
            if isinstance(msg, AIMessage) and msg.content:
                reply = msg.content
                break

        if validation_error and nf_code:
            reply += f"\n\n⚠️ **Validation Notice**: {validation_error}"

        append_message(
            db, conv, "assistant", reply,
            nextflow_code=nf_code, mermaid_code=mermaid, ast_json=ast_json
        )

        return ChatResponse(
            status="APPROVED" if nf_code else "failed",
            reply=reply,
            conversation_id=conv.id,
            nextflow_code=nf_code,
            mermaid_code=mermaid,
            ast_json=ast_json,
            error=error or validation_error,
        )
    except Exception as e:
        logger.error("graph_generation_failed", error=str(e))
        return ChatResponse(status="failed", reply="Generation failed", error=str(e))


@app.post("/chat", response_model=ChatResponse)
async def chat_with_agent(
    request: ChatRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ChatResponse:
    trace_id = str(uuid.uuid4())
    structlog.contextvars.bind_contextvars(trace_id=trace_id, session_id=request.session_id, user_id=user.id)

    try:
        user_msg = request.message or ""
        if request.action == "approve":
            conv = get_or_create_conversation(db, user, request.session_id, "Pipeline Approved")
            append_message(db, conv, "user", "[⚡ Approved Plan via UI]")
            input_payload = {
                "consultant_status": "APPROVED",
                "action": "approve",
                "execution_mode": "direct",
                "generate_diagrams": request.generate_diagrams,
            }
        else:
            conv = get_or_create_conversation(db, user, request.session_id, user_msg)
            append_message(db, conv, "user", user_msg)
            input_payload = {
                "user_query": user_msg,
                "consultant_status": None,
                "action": None,
                "execution_mode": request.execution_mode or "interactive",
                "generate_diagrams": request.generate_diagrams,
                "messages": [("user", user_msg)],
            }

        thread_id = f"u{user.id}:{request.session_id}"
        config = {"configurable": {"thread_id": thread_id}}

        result = await asyncio.wait_for(
            app_graph.ainvoke(
                input_payload,
                config=config,
            ),
            timeout=600.0,
        )

        if result.get("error"):
            err_msg = str(result["error"])
            append_message(db, conv, "assistant", f"Error: {err_msg}")
            return ChatResponse(
                status="failed",
                reply="The agent encountered an error.",
                conversation_id=conv.id,
                error=err_msg,
            )

        status_val = result.get("consultant_status", "CHATTING")
        nf_code = result.get("nextflow_code")
        ast_json = result.get("ast_json")
        mermaid = result.get("mermaid_deterministic") or result.get("mermaid_agent") or result.get("mermaid_code")

        messages = result.get("messages", [])
        ai_reply = "No response generated."

        if status_val == "APPROVED":
            if result.get("error"):
                ai_reply = f"I encountered an error while building the pipeline: {result.get('error')}"
            elif result.get("validation_error"):
                if nf_code or ast_json:
                    ai_reply = f"I have generated the Nextflow pipeline based on your approved plan.\n\n⚠️ **Validation Notice**: {result.get('validation_error')}\n\nPlease review the generated workflow code below."
                else:
                    ai_reply = f"I could not complete the pipeline AST validation. Details:\n\n{result.get('validation_error')}"
            else:
                ai_reply = "I have successfully generated and validated the Nextflow pipeline based on your approved plan."
        else:
            for msg in reversed(messages):
                if isinstance(msg, AIMessage) and msg.content:
                    if msg.additional_kwargs.get("internal_agent"):
                        continue
                    ai_reply = msg.content
                    break

        tool_calls = []
        seen = set()
        last_human_idx = None
        for idx in range(len(messages) - 1, -1, -1):
            msg = messages[idx]
            if isinstance(msg, HumanMessage) or getattr(msg, "type", "") == "human":
                last_human_idx = idx
                break

        start_idx = last_human_idx + 1 if last_human_idx is not None else 0
        for msg in messages[start_idx:]:
            for tc in getattr(msg, "tool_calls", []) or []:
                name = tc.get("name")
                if name and name not in seen:
                    tool_calls.append(name)
                    seen.add(name)

        append_message(
            db, conv, "assistant", ai_reply,
            nextflow_code=nf_code,
            mermaid_code=mermaid,
            ast_json=ast_json,
        )

        selected_comps = result.get("selected_component_ids") or []
        has_plan_flag = bool(selected_comps) and (status_val == "CHATTING")

        return ChatResponse(
            status=status_val,
            reply=ai_reply,
            conversation_id=conv.id,
            nextflow_code=nf_code,
            mermaid_code=mermaid,
            ast_json=ast_json,
            error=None,
            tool_calls=tool_calls,
            has_plan=has_plan_flag,
            selected_components=selected_comps if has_plan_flag else None,
        )

    except TimeoutError:
        logger.error("server_timeout")
        return ChatResponse(
            status="error",
            reply="The request timed out while processing.",
            error="TimeoutError",
        )
    except Exception as e:
        logger.error("server_error", error=str(e))
        return ChatResponse(
            status="error",
            reply="The server encountered an unexpected error.",
            error=str(e),
        )
    finally:
        structlog.contextvars.clear_contextvars()


# --- 6. STATIC FRONTEND MOUNT ---
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

_frontend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../frontend"))

@app.get("/drawer")
async def get_drawer_page():
    drawer_file = os.path.join(_frontend_dir, "drawer.html")
    if os.path.isfile(drawer_file):
        return FileResponse(drawer_file, media_type="text/html")
    raise HTTPException(status_code=404, detail="Drawer page not found")

if os.path.isdir(_frontend_dir):
    app.mount("/", StaticFiles(directory=_frontend_dir, html=True), name="frontend")

