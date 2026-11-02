from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.library import LibraryService, LibraryUnavailable
from app.library_api import create_library_router
from app.persistence.memory import InMemoryBlobStore, InMemoryDocumentStore

PDF = b"%PDF-1.7\n" + b"x" * 64


def pipeline_ok(strategy, di, pdf_bytes, tenant_id, filename):
    return {
        "splitStrategy": strategy,
        "tenantId": tenant_id,
        "filename": filename,
        "totalPages": 4,
        "totalDurationMs": 12.5,
        "billing": {"totalCostEstimateUsd": 0.02},
        "segments": [
            {"doc_type": "paystub", "page_range": [1, 2]},
            {"doc_type": "bank_statement", "page_range": [3, 4]},
        ],
    }


def pipeline_fails(strategy, di, pdf_bytes, tenant_id, filename):
    raise RuntimeError("DI timeout")


def make_service():
    return LibraryService(
        InMemoryDocumentStore(),
        InMemoryBlobStore(),
        raw_container="documents",
        processed_container="processed",
        max_upload_bytes=1024 * 1024,
    )


def make_client(pipeline=pipeline_ok, get_service=None):
    service = make_service()
    app = FastAPI()
    app.include_router(create_library_router(
        get_service=get_service or (lambda: service),
        get_di=lambda: object(),
        run_pipeline=pipeline,
    ))
    return TestClient(app)


def upload(client, tenant="tenant-a", mode="heuristic", data=PDF, content_type="application/pdf"):
    return client.post(
        f"/api/documents?mode={mode}",
        headers={"x-tenant-id": tenant},
        files={"file": ("packet.pdf", data, content_type)},
    )


def test_upload_processes_document_and_returns_summary():
    client = make_client()
    response = upload(client)
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "processed"
    assert body["summary"]["segmentCount"] == 2
    assert body["summary"]["docTypes"] == ["paystub", "bank_statement"]


def test_result_endpoint_returns_processed_json():
    client = make_client()
    doc_id = upload(client).json()["id"]
    response = client.get(f"/api/documents/{doc_id}/result", headers={"x-tenant-id": "tenant-a"})
    assert response.status_code == 200
    assert response.json()["splitStrategy"] == "heuristic"


def test_failed_processing_is_saved_and_reported():
    client = make_client(pipeline=pipeline_fails)
    response = upload(client)
    assert response.status_code == 502
    doc_id = response.json()["detail"]["documentId"]
    doc = client.get(f"/api/documents/{doc_id}", headers={"x-tenant-id": "tenant-a"}).json()
    assert doc["status"] == "failed"
    assert "DI timeout" in doc["error"]


def test_list_only_returns_callers_documents():
    client = make_client()
    upload(client, tenant="tenant-a")
    upload(client, tenant="tenant-b")
    docs = client.get("/api/documents", headers={"x-tenant-id": "tenant-a"}).json()
    assert len(docs) == 1
    assert docs[0]["tenantId"] == "tenant-a"


def test_other_tenant_gets_404():
    client = make_client()
    doc_id = upload(client, tenant="tenant-a").json()["id"]
    response = client.get(f"/api/documents/{doc_id}", headers={"x-tenant-id": "tenant-b"})
    assert response.status_code == 404


def test_delete_then_not_found():
    client = make_client()
    doc_id = upload(client).json()["id"]
    assert client.delete(f"/api/documents/{doc_id}", headers={"x-tenant-id": "tenant-a"}).status_code == 204
    assert client.get(f"/api/documents/{doc_id}", headers={"x-tenant-id": "tenant-a"}).status_code == 404


def test_rejects_non_pdf_upload():
    client = make_client()
    response = upload(client, data=b"hello", content_type="text/plain")
    assert response.status_code == 415


def test_rejects_invalid_tenant_header():
    client = make_client()
    response = client.get("/api/documents", headers={"x-tenant-id": "bad tenant"})
    assert response.status_code == 400


def test_invalid_mode_is_rejected():
    client = make_client()
    response = upload(client, mode="compare")
    assert response.status_code == 422


def test_unconfigured_library_returns_503():
    def unavailable():
        raise LibraryUnavailable("not configured")

    client = make_client(get_service=unavailable)
    assert client.get("/api/documents", headers={"x-tenant-id": "tenant-a"}).status_code == 503
    assert client.get("/api/status").json()["library"]["status"] == "unavailable"


def test_status_reports_store_checks():
    client = make_client()
    status = client.get("/api/status").json()
    assert status["library"]["status"] == "ok"
    assert set(status["library"]["checks"]) == {"cosmos", "rawBlobs", "processedBlobs"}
