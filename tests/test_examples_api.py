from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import library_api
from app.examples import ExampleNotFound, get_example, load_manifest, stored_result
from app.library import LibraryService
from app.library_api import create_library_router
from app.persistence.memory import InMemoryBlobStore, InMemoryDocumentStore

EXAMPLE_IDS = [item["id"] for item in load_manifest()]
TENANT_A = {"x-tenant-id": "tenant-a"}


def no_live_pipeline(*args, **kwargs):
    raise AssertionError("example routes must not run the live pipeline")


def make_client():
    service = LibraryService(
        InMemoryDocumentStore(),
        InMemoryBlobStore(),
        raw_container="documents",
        processed_container="processed",
        max_upload_bytes=50 * 1024 * 1024,
    )
    app = FastAPI()
    app.include_router(create_library_router(
        get_service=lambda: service,
        get_di=lambda: object(),
        run_pipeline=no_live_pipeline,
    ))
    return TestClient(app)


def load(client, example_id, mode="heuristic", tenant=TENANT_A):
    return client.post(f"/api/examples/{example_id}/load?mode={mode}", headers=tenant)


def test_lists_examples_with_public_fields_only():
    body = make_client().get("/api/examples").json()
    assert [item["id"] for item in body] == EXAMPLE_IDS
    for item in body:
        assert set(item) == {"id", "title", "fileName", "description", "tryIt", "modes"}
        assert item["modes"]


def test_serves_example_pdf_inline():
    response = make_client().get(f"/api/examples/{EXAMPLE_IDS[0]}/pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF-")


def test_unknown_example_returns_404():
    client = make_client()
    assert client.get("/api/examples/no-such-packet/pdf").status_code == 404
    assert load(client, "no-such-packet").status_code == 404


def test_unsupported_strategy_is_rejected():
    assert load(make_client(), EXAMPLE_IDS[0], mode="compare").status_code == 422


def test_missing_stored_result_for_strategy_returns_404(monkeypatch):
    def missing(example, mode):
        raise ExampleNotFound(f"No stored {mode} result for {example['id']}")

    monkeypatch.setattr(library_api, "stored_result", missing)
    assert load(make_client(), EXAMPLE_IDS[0], mode="cu").status_code == 404


def test_load_creates_one_processed_record_and_reuses_it():
    client = make_client()
    example_id = EXAMPLE_IDS[0]
    first = load(client, example_id)
    assert first.status_code == 200
    doc = first.json()
    assert doc["status"] == "processed"
    assert doc["exampleId"] == example_id
    assert doc["mode"] == "heuristic"
    assert doc["tenantId"] == "tenant-a"
    segments = stored_result(get_example(example_id), "heuristic")["segments"]
    assert doc["summary"]["segmentCount"] == len(segments)
    assert doc["searchIndex"]["error"] == "knowledge base is not ready"

    again = load(client, example_id)
    assert again.json()["id"] == doc["id"]
    assert len(client.get("/api/documents", headers=TENANT_A).json()) == 1


def test_loaded_result_is_stamped_with_tenant_and_file_name():
    client = make_client()
    example = get_example(EXAMPLE_IDS[0])
    doc = load(client, EXAMPLE_IDS[0]).json()
    result = client.get(f"/api/documents/{doc['id']}/result", headers=TENANT_A).json()
    assert result["tenantId"] == "tenant-a"
    assert result["filename"] == example["file"]


def test_loaded_examples_are_scoped_to_the_tenant():
    client = make_client()
    load(client, EXAMPLE_IDS[0])
    assert client.get("/api/documents", headers={"x-tenant-id": "tenant-b"}).json() == []


def test_deleted_example_can_be_loaded_again():
    client = make_client()
    doc = load(client, EXAMPLE_IDS[0]).json()
    assert client.delete(f"/api/documents/{doc['id']}", headers=TENANT_A).status_code == 204
    again = load(client, EXAMPLE_IDS[0]).json()
    assert again["id"] == doc["id"]
    assert again["status"] == "processed"
