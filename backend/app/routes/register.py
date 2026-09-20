"""Register a merged pipeline in CMDBuild, pinned to the commit that was reviewed.

Called *after* a human has reviewed and merged the proposal: the merge is the
approval, so the card is created available. Note that CMDBuild's Status is its
logical-delete flag (A/N), not an approval state — there is no native "pending"
on this class, which is precisely why the gate lives in the pull request.
"""
import json
import os
from typing import Any, Dict, List, Optional

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.models.db_models import User
from app.services.auth import get_current_user

log = structlog.get_logger("core.plugin_loader")

router = APIRouter(prefix="/register-workflow", tags=["publish"])

CMDB_URL = os.environ.get("CMDBUILD_URL", "")
CMDB_USER = os.environ.get("CMDBUILD_USER", "")
CMDB_PASSWORD = os.environ.get("CMDBUILD_PASSWORD", "")
CMDB_CLASS = os.environ.get("CMDBUILD_WORKFLOW_CLASS", "m_workflow")
CMDB_MENU_CLASS = os.environ.get("CMDBUILD_MENU_CLASS", "m_menu_analysis")
CMDB_MENU_PARENT = os.environ.get("CMDBUILD_MENU_PARENT", "")
# senza un template di risultato il pannello "Result data" resta vuoto e la UI
# risponde "Analysis run details could not be loaded": e' li' che vivono i link
# alla cartella degli output. 'default' e' il browser di file usato da quasi
# tutte le pipeline di produzione.
CMDB_RESULT_TEMPLATE = os.environ.get("CMDBUILD_RESULT_TEMPLATE", "default")
PARAM_MAP_FILE = os.environ.get("COMPONENT_PARAMETERS_FILE", "")

_PARAM_CACHE: Optional[Dict[str, List[Dict[str, Any]]]] = None


def _param_map() -> Dict[str, List[Dict[str, Any]]]:
    """Parameters each component accepts, extracted from the framework sources.

    The generated module never mentions them: steps read them at runtime via
    param(). They surface to the user through the launch form, so the card is
    where they have to be declared.
    """
    global _PARAM_CACHE
    if _PARAM_CACHE is None:
        _PARAM_CACHE = {}
        if PARAM_MAP_FILE and os.path.exists(PARAM_MAP_FILE):
            try:
                data = json.load(open(PARAM_MAP_FILE))
                for c in data.get("components", []):
                    if c.get("parameters"):
                        _PARAM_CACHE[c["id"]] = c["parameters"]
            except Exception as e:  # a missing map must not block registration
                log.warning("param_map_unreadable", error=str(e))
    return _PARAM_CACHE


def _derive_params(components: List[str]) -> List[Dict[str, Any]]:
    """Build json_params for the launch form from the pipeline's components."""
    pmap = _param_map()
    seen, out = set(), []
    for comp in components:
        for p in pmap.get(comp, []):
            if p["name"] in seen:
                continue
            seen.add(p["name"])
            entry = {
                "name": p["name"],
                # the bare name is what a bioinformatician recognises; the
                # namespace prefix is noise in a form label.
                "label": p["name"].split("__")[-1].replace("_", " ").strip().capitalize(),
                "type": "text",
                "required": bool(p.get("required", True)),
            }
            if p.get("default_value") is not None:
                entry["default_value"] = p["default_value"]
            out.append(entry)
    return out


class RegisterRequest(BaseModel):
    code: str = Field(description="Short identifier, must be unique in m_workflow")
    description: str = Field(description="Label shown to users")
    module: str = Field(description="e.g. module_salmonella_denovo.nf")
    commit_sha: str = Field(description="The reviewed commit — executions pin this, not a branch")
    json_params: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Parameter definitions for the launch form; derived from "
                    "the components when left empty",
    )
    components: List[str] = Field(
        default_factory=list, description="Component ids used by the pipeline"
    )
    add_to_menu: bool = Field(
        default=True, description="Also create the entry under the LLM category"
    )
    multi_sample_input: bool = False
    pull_request_url: str = ""


class RegisterResponse(BaseModel):
    card_id: Any
    menu_id: Any = None
    parameters: List[Dict[str, Any]] = []
    input_types: List[str] = []
    status: str
    detail: str


def _check_config() -> None:
    if not (CMDB_URL and CMDB_USER and CMDB_PASSWORD):
        raise HTTPException(
            status_code=503,
            detail="CMDBuild not configured (CMDBUILD_URL, CMDBUILD_USER, CMDBUILD_PASSWORD).",
        )


@router.post("", response_model=RegisterResponse)
def register_workflow(
    req: RegisterRequest,
    user: User = Depends(get_current_user),
) -> RegisterResponse:
    params = req.json_params or _derive_params(req.components)
    notes = {
        "source": "cohesive-llm",
        "commit_sha": req.commit_sha,
        "pull_request": req.pull_request_url,
        "reviewed": True,
    }
    payload = {
        # CMDBuild needs the concrete subclass: m_workflow itself is abstract.
        "_type": CMDB_CLASS,
        "Code": req.code,
        "Description": req.description,
        "script": req.module,
        "json_params": json.dumps(params) if params else None,
        "multi_sample_input": req.multi_sample_input,
        # senza questo i risultati restano nella cartella di lavoro e non
        # rientrano mai nella piattaforma: nessun dataset, nessuna cartella
        # visibile sotto il campione. Tutte le pipeline di produzione hanno true.
        "importable": True,
        "custom_processing": False,
        "uuid_folder": False,
        "Notes": json.dumps(notes),
    }

    _check_config()
    # REST v3 accepts HTTP Basic; the /sessions token is not usable for service calls.
    with httpx.Client(timeout=30.0, auth=(CMDB_USER, CMDB_PASSWORD)) as c:
        r = c.post(
            f"{CMDB_URL}/services/rest/v3/classes/{CMDB_CLASS}/cards",
            json={k: v for k, v in payload.items() if v is not None},
        )
        if r.status_code not in (200, 201):
            raise HTTPException(status_code=502, detail=f"Card creation failed: {r.text[:300]}")
        card_id = (r.json().get("data") or {}).get("_id")

        # without the declared input types the launch form has nothing to offer
        input_types = _declare_inputs(c, card_id, req.components) if card_id else []
        if card_id:
            _attach_result_template(c, card_id)

        menu_id = None
        if req.add_to_menu and CMDB_MENU_PARENT and card_id:
            m = c.post(
                f"{CMDB_URL}/services/rest/v3/classes/{CMDB_MENU_CLASS}/cards",
                json={
                    "_type": CMDB_MENU_CLASS,
                    "Code": req.code,
                    "Description": req.description,
                    # title e' vincolato a 50 caratteri dal modello dati
                    "title": req.description[:50],
                    "hierarchy": f"/llm/{req.code}",
                    "parent": int(CMDB_MENU_PARENT),
                    "module": card_id,
                    "json_settings": json.dumps({
                        "quick_filter": True, "quick_filter_colour": "#7C3AED",
                        "icon": "iconic iconic-cpu", "img_path": None,
                    }),
                    "wiki": json.dumps({"href": req.pull_request_url, "description": req.description}),
                    "wiki_page": False,
                },
            )
            if m.status_code in (200, 201):
                menu_id = (m.json().get("data") or {}).get("_id")
            else:  # the pipeline is registered either way
                log.warning("menu_entry_failed", detail=m.text[:200])

    log.info(
        "workflow_registered",
        code=req.code,
        module=req.module,
        commit=req.commit_sha,
        card_id=card_id,
        input_types=input_types,
        user_id=getattr(user, "id", None),
    )
    return RegisterResponse(
        card_id=card_id,
        menu_id=menu_id,
        parameters=params,
        input_types=input_types,
        status="registered",
        detail=f"Registered from reviewed commit {req.commit_sha[:10]}. "
               "Approval happened at merge time; CMDBuild has no separate pending state.",
    )


def _step_types(client: httpx.Client, code: str) -> Dict[str, List[Dict[str, Any]]]:
    """What a step consumes and produces, as declared in CMDBuild."""
    r = client.get(
        f"{CMDB_URL}/services/rest/v3/classes/m_step/cards",
        params={"filter": json.dumps(
            {"attribute": {"simple": {"attribute": "Code", "operator": "equal",
                                      "value": [code]}}}), "limit": 1},
    )
    data = (r.json().get("data") or []) if r.status_code == 200 else []
    if not data:
        return {"in": [], "out": []}
    rel = client.get(
        f"{CMDB_URL}/services/rest/v3/classes/m_step/cards/{data[0]['_id']}/relations"
    )
    out: Dict[str, List[Dict[str, Any]]] = {"in": [], "out": []}
    for x in (rel.json().get("data") or []):
        if not x.get("_is_direct"):
            continue
        t = {"id": x["_destinationId"], "code": x.get("_destinationCode")}
        if x.get("_type") == "m_rel_wrkflow_intype":
            out["in"].append(t)
        elif x.get("_type") == "m_rel_step_outtype":
            out["out"].append(t)
    return out


def _declare_inputs(client: httpx.Client, card_id: Any, components: List[str]) -> List[str]:
    """Tell CMDBuild which result types the pipeline consumes from outside.

    Without this the launch form has nothing to offer and reports "there are
    not inputs available": bitw2 resolves the selectable datasets by joining
    the workflow's declared input types against the steps that produce them.

    A component whose input is produced by another component of the same
    pipeline is satisfied internally, so only the unsatisfied ones surface.
    """
    types = {c: _step_types(client, c) for c in components}
    produced = {t["code"] for v in types.values() for t in v["out"]}
    external: Dict[Any, str] = {}
    for spec in types.values():
        if spec["in"] and not any(t["code"] in produced for t in spec["in"]):
            for t in spec["in"]:
                external[t["id"]] = t["code"]

    declared = []
    for type_id, code in external.items():
        r = client.post(
            f"{CMDB_URL}/services/rest/v3/domains/m_rel_wrkflow_intype/relations",
            json={"_type": "m_rel_wrkflow_intype",
                  "_sourceId": card_id, "_sourceType": CMDB_CLASS,
                  "_destinationId": type_id, "_destinationType": "m_result_type"},
        )
        if r.status_code in (200, 201):
            declared.append(code)
        else:
            log.warning("input_type_failed", code=code, detail=r.text[:200])
    return declared


def _attach_result_template(client: httpx.Client, card_id: Any) -> None:
    """Link the pipeline to the result template that renders the output folder."""
    r = client.get(
        f"{CMDB_URL}/services/rest/v3/classes/m_result_template/cards",
        params={"filter": json.dumps(
            {"attribute": {"simple": {"attribute": "Code", "operator": "equal",
                                      "value": [CMDB_RESULT_TEMPLATE]}}}), "limit": 1},
    )
    data = (r.json().get("data") or []) if r.status_code == 200 else []
    if not data:
        log.warning("result_template_missing", code=CMDB_RESULT_TEMPLATE)
        return
    rel = client.post(
        f"{CMDB_URL}/services/rest/v3/domains/m_rel_wflow_restmpl/relations",
        json={"_type": "m_rel_wflow_restmpl",
              "_sourceId": card_id, "_sourceType": CMDB_CLASS,
              "_destinationId": data[0]["_id"], "_destinationType": "m_result_template"},
    )
    if rel.status_code not in (200, 201):
        log.warning("result_template_failed", detail=rel.text[:200])
