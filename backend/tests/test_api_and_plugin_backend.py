import os
import sys
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

# Ensure backend root is on sys.path
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

# Enable auth bypass and SQLite for testing
os.environ["AUTH_BYPASS"] = "true"
os.environ["DATABASE_URL"] = "sqlite:///./test_cohesive.db"

from app.api import app
from app.db import Base, engine


@pytest.fixture(scope="module", autouse=True)
def setup_test_db():
    Base.metadata.create_all(bind=engine)
    yield
    # Cleanup test db
    db_file = Path("./test_cohesive.db")
    if db_file.exists():
        try:
            db_file.unlink()
        except Exception:
            pass


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_health_check(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data.get("status") == "online"
    assert "llm_provider" in data
    assert "active_plugin" in data


def test_system_info(client: TestClient):
    response = client.get("/system-info")
    assert response.status_code == 200
    data = response.json()
    assert "llm_model" in data
    assert "llm_provider" in data
    assert "active_plugin" in data


def test_auth_me_with_bypass(client: TestClient):
    response = client.get("/auth/me")
    assert response.status_code == 200
    data = response.json()
    assert data.get("username") == "local_dev"
    assert data.get("id") is not None


def test_catalog_components_dynamic_reflection(client: TestClient):
    response = client.get("/catalog/components")
    assert response.status_code == 200
    catalog = response.json()
    assert isinstance(catalog, dict)
    assert len(catalog) > 0

    # Verify domains and component fields
    for domain, components in catalog.items():
        assert isinstance(components, list)
        for comp in components:
            assert "id" in comp
            assert "tool" in comp
            assert "description" in comp
            assert "inputs" in comp
            assert "outputs" in comp


def test_drawings_crud(client: TestClient):
    # 1. Create drawing
    payload = {
        "title": "Test Pipeline Graph",
        "graph_json": {
            "nodeDataMap": {
                "1": {"component_id": "fastp", "tool": "fastp"},
                "2": {"component_id": "shovill", "tool": "shovill"}
            },
            "drawflow": {}
        }
    }
    create_resp = client.post("/drawings", json=payload)
    assert create_resp.status_code == 200
    drawing_id = create_resp.json()["id"]

    # 2. List drawings
    list_resp = client.get("/drawings")
    assert list_resp.status_code == 200
    drawings = list_resp.json()
    assert any(d["id"] == drawing_id for d in drawings)

    # 3. Get drawing detail
    get_resp = client.get(f"/drawings/{drawing_id}")
    assert get_resp.status_code == 200
    detail = get_resp.json()
    assert detail["title"] == "Test Pipeline Graph"
    assert "nodeDataMap" in detail["graph_json"]

    # 4. Update drawing
    update_resp = client.put(f"/drawings/{drawing_id}", json={"title": "Updated Graph", "graph_json": detail["graph_json"]})
    assert update_resp.status_code == 200

    # 5. Delete drawing
    del_resp = client.delete(f"/drawings/{drawing_id}")
    assert del_resp.status_code == 200


def test_validate_endpoint(client: TestClient):
    sample_nf = """
    nextflow.enable.dsl=2
    workflow {
        log.info "Test validation"
    }
    """
    response = client.post("/validate", json={"nextflow_code": sample_nf})
    assert response.status_code == 200
    data = response.json()
    assert "success" in data
    assert "warnings" in data
    assert "errors" in data
