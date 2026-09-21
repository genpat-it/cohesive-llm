"""Publish a generated pipeline as a Pull Request on the Nextflow framework repo.

The agent's only privilege on the production framework is *proposing* a change:
it opens a branch and a PR carrying the generated module plus its provenance.
Validation is left to CI, approval to a human reviewer, and registration in
CMDBuild happens as a consequence of the merge — never from here.
"""
import os
import re
import time
from typing import Any, Dict, List, Optional

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.models.db_models import User
from app.services.auth import get_current_user

log = structlog.get_logger("core.plugin_loader")

router = APIRouter(prefix="/publish", tags=["publish"])

GIT_API = os.environ.get("FRAMEWORK_GIT_API", "https://api.github.com")
GIT_REPO = os.environ.get("FRAMEWORK_GIT_REPO", "genpat-it/cohesive-ngsmanager")
GIT_BASE = os.environ.get("FRAMEWORK_GIT_BASE_BRANCH", "main")
GIT_TOKEN_FILE = os.environ.get("FRAMEWORK_GIT_TOKEN_FILE", "")
GIT_TOKEN = os.environ.get("FRAMEWORK_GIT_TOKEN", "")


def _token() -> str:
    if GIT_TOKEN:
        return GIT_TOKEN
    if GIT_TOKEN_FILE and os.path.exists(GIT_TOKEN_FILE):
        return open(GIT_TOKEN_FILE).read().strip()
    raise HTTPException(
        status_code=503,
        detail="No framework git token configured (FRAMEWORK_GIT_TOKEN or FRAMEWORK_GIT_TOKEN_FILE).",
    )



def _components_from_code(code: str) -> List[str]:
    """Which components a module uses, read from the code itself.

    The caller passes the list the model reported, but the model does not
    always report one — and then the pull request goes out with no components,
    the reviewer cannot see what it composes, and registration cannot derive
    the input types. The include lines always say the truth.
    """
    found = []
    for path in re.findall(r"include\s*\{[^}]*\}\s*from\s*'\.\./(?:steps|multi)/([^']+)'", code):
        name = path.rsplit("/", 1)[-1].removesuffix(".nf")
        if name not in found:
            found.append(name)
    return found


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return (s or "pipeline")[:40]


class PublishRequest(BaseModel):
    nextflow_code: str = Field(description="The generated DSL2 pipeline")
    name: str = Field(description="Short name, becomes module_<name>.nf")
    description: str = Field(default="", description="Human-readable purpose")
    user_query: str = Field(default="", description="The request that produced it")
    components: List[str] = Field(default_factory=list)
    validation: Optional[Dict[str, Any]] = Field(
        default=None, description="Output of /validate for this code"
    )
    model: str = Field(default="", description="Model that produced the AST")
    plugin: str = Field(default="", description="Active catalogue plugin")


class PublishResponse(BaseModel):
    pull_request_url: str
    branch: str
    module_path: str
    commit_sha: str



def _title(req: "PublishRequest", slug: str) -> str:
    """A pull request title a reviewer can scan in a list.

    The description defaults to the user's request, which is often a paragraph
    written in the first person. GitHub shows titles on one line, so that turns
    into an unreadable wall starting with "I have...". A short description is
    used as-is; anything longer falls back to the module name the author chose,
    and the full request stays in the body where a reviewer wants it.
    """
    text = re.sub(r"\s+", " ", (req.description or "").strip())
    if 0 < len(text) <= 60 and not text.lower().startswith(("i have", "i need", "i want")):
        return f"Add pipeline: {text}"
    return f"Add pipeline: {slug.replace('_', ' ')}"

def _body(req: PublishRequest, module_path: str) -> str:
    """PR description: what a reviewer needs in order to judge it."""
    v = req.validation or {}
    verdict = "not run"
    if v:
        verdict = "passed" if v.get("success") else "FAILED"
        warns = v.get("warnings") or []
        errs = v.get("errors") or []
        if warns:
            verdict += " — warnings: " + "; ".join(str(w) for w in warns[:3])
        if errs:
            verdict += " — errors: " + "; ".join(str(e) for e in errs[:3])

    lines = [
        "Pipeline proposed by **Cohesive LLM**. It has not been executed.",
        "",
        "### Request",
        "",
        f"> {req.user_query or '(not recorded)'}",
        "",
        "### What this adds",
        "",
        f"- `{module_path}`",
    ]
    if req.components:
        lines += ["", "### Components used", ""]
        lines += [f"- `{c}`" for c in req.components]
    lines += [
        "",
        "### Provenance",
        "",
        f"- model: `{req.model or 'n/a'}`",
        f"- catalogue plugin: `{req.plugin or 'n/a'}`",
        f"- local `nextflow -preview` validation: {verdict}",
        "",
        "### Review checklist",
        "",
        "- [ ] every process called is imported",
        "- [ ] the dataflow is connected end to end (no orphan steps)",
        "- [ ] components and channels match the intended analysis",
        "- [ ] the biological question is actually answered by this topology",
    ]
    return "\n".join(lines)


@router.post("", response_model=PublishResponse)
def publish_pipeline(
    req: PublishRequest,
    user: User = Depends(get_current_user),
) -> PublishResponse:
    if not req.nextflow_code.strip():
        raise HTTPException(status_code=400, detail="nextflow_code is empty")

    # trust the code, not the report: see _components_from_code
    if not req.components:
        req.components = _components_from_code(req.nextflow_code)

    slug = _slug(req.name)
    branch = f"llm/{slug}-{int(time.time())}"
    module_path = f"modules/module_{slug}.nf"
    headers = {
        "Authorization": f"Bearer {_token()}",
        "Accept": "application/vnd.github+json",
    }
    base = f"{GIT_API}/repos/{GIT_REPO}"

    with httpx.Client(timeout=30.0, headers=headers) as c:
        r = c.get(f"{base}/git/ref/heads/{GIT_BASE}")
        if r.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Cannot read {GIT_BASE}: {r.text[:200]}")
        base_sha = r.json()["object"]["sha"]

        r = c.post(f"{base}/git/refs", json={"ref": f"refs/heads/{branch}", "sha": base_sha})
        if r.status_code not in (200, 201):
            raise HTTPException(status_code=502, detail=f"Cannot create branch: {r.text[:200]}")

        import base64

        content = base64.b64encode(req.nextflow_code.encode()).decode()
        r = c.put(
            f"{base}/contents/{module_path}",
            json={
                "message": f"feat({slug}): add pipeline generated by Cohesive LLM",
                "content": content,
                "branch": branch,
            },
        )
        if r.status_code not in (200, 201):
            raise HTTPException(status_code=502, detail=f"Cannot commit module: {r.text[:200]}")
        commit_sha = r.json()["commit"]["sha"]

        r = c.post(
            f"{base}/pulls",
            json={
                "title": _title(req, slug),
                "head": branch,
                "base": GIT_BASE,
                "body": _body(req, module_path),
                "draft": True,
            },
        )
        if r.status_code not in (200, 201):
            raise HTTPException(status_code=502, detail=f"Cannot open PR: {r.text[:200]}")
        pr_url = r.json()["html_url"]

    log.info(
        "pipeline_published",
        branch=branch,
        module=module_path,
        pr=pr_url,
        user_id=getattr(user, "id", None),
    )
    return PublishResponse(
        pull_request_url=pr_url,
        branch=branch,
        module_path=module_path,
        commit_sha=commit_sha,
    )
