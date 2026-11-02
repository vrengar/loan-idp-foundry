import pytest

from app import library
from app.library import (
    InvalidRequest,
    LibraryService,
    LibraryUnavailable,
    NotFound,
    TooLarge,
    UnsupportedMedia,
    normalize_tenant,
    summarize_result,
)
from app.persistence.memory import InMemoryBlobStore, InMemoryDocumentStore

PDF = b"%PDF-1.7\n" + b"x" * 64
MAX_BYTES = 1024


@pytest.fixture
def blobs():
    return InMemoryBlobStore()


@pytest.fixture
def service(blobs):
    return LibraryService(
        InMemoryDocumentStore(),
        blobs,
        raw_container="documents",
        processed_container="processed",
        max_upload_bytes=MAX_BYTES,
    )


def test_create_stores_raw_pdf_and_uploaded_record(service, blobs):
    record = service.create_document("tenant-a", "packet.pdf", "application/pdf", PDF, "heuristic")
    assert record["status"] == "uploaded"
    assert record["tenantId"] == "tenant-a"
    assert record["sizeBytes"] == len(PDF)
    assert blobs.get("documents", record["rawBlob"]) == PDF


def test_default_tenant_when_header_missing(service):
    record = service.create_document(None, "packet.pdf", "application/pdf", PDF, "heuristic")
    assert record["tenantId"] == "anonymous"


def test_octet_stream_pdf_is_accepted(service):
    record = service.create_document("tenant-a", "packet.pdf", "application/octet-stream", PDF, "heuristic")
    assert record["status"] == "uploaded"


@pytest.mark.parametrize(
    "content_type, data, error",
    [
        ("text/plain", PDF, UnsupportedMedia),
        ("application/pdf", b"", InvalidRequest),
        ("application/pdf", b"not a pdf at all", UnsupportedMedia),
        ("application/pdf", b"%PDF-" + b"x" * MAX_BYTES, TooLarge),
    ],
)
def test_rejects_invalid_uploads(service, content_type, data, error):
    with pytest.raises(error):
        service.create_document("tenant-a", "packet.pdf", content_type, data, "heuristic")


@pytest.mark.parametrize("value", ["bad tenant", "../escape", "x" * 65])
def test_rejects_invalid_tenant(value):
    with pytest.raises(InvalidRequest):
        normalize_tenant(value)


def test_list_is_scoped_to_tenant(service):
    service.create_document("tenant-a", "a.pdf", "application/pdf", PDF, "heuristic")
    service.create_document("tenant-b", "b.pdf", "application/pdf", PDF, "heuristic")
    names = [d["fileName"] for d in service.list_documents("tenant-a")]
    assert names == ["a.pdf"]


def test_other_tenant_cannot_read_document(service):
    record = service.create_document("tenant-a", "a.pdf", "application/pdf", PDF, "heuristic")
    with pytest.raises(NotFound):
        service.get_document("tenant-b", record["id"])


def test_save_result_stores_processed_json_and_summary(service):
    record = service.create_document("tenant-a", "packet.pdf", "application/pdf", PDF, "heuristic")
    result = {
        "splitStrategy": "heuristic",
        "totalPages": 4,
        "totalDurationMs": 12.5,
        "billing": {"totalCostEstimateUsd": 0.02},
        "segments": [{"doc_type": "paystub"}, {"doc_type": "bank_statement"}],
    }
    saved = service.save_result(record, result)
    assert saved["status"] == "processed"
    assert saved["summary"]["segmentCount"] == 2
    assert saved["summary"]["docTypes"] == ["paystub", "bank_statement"]
    assert service.get_result("tenant-a", record["id"]) == result


def test_result_not_available_before_processing(service):
    record = service.create_document("tenant-a", "packet.pdf", "application/pdf", PDF, "heuristic")
    with pytest.raises(NotFound):
        service.get_result("tenant-a", record["id"])


def test_mark_failed_records_error(service):
    record = service.create_document("tenant-a", "packet.pdf", "application/pdf", PDF, "heuristic")
    failed = service.mark_failed(record, "DI timeout")
    assert failed["status"] == "failed"
    assert failed["error"] == "DI timeout"


def test_delete_removes_record_and_blobs(service, blobs):
    record = service.create_document("tenant-a", "packet.pdf", "application/pdf", PDF, "heuristic")
    service.save_result(record, {"segments": []})
    service.delete_document("tenant-a", record["id"])
    with pytest.raises(NotFound):
        service.get_document("tenant-a", record["id"])
    with pytest.raises(FileNotFoundError):
        blobs.get("documents", record["rawBlob"])


def test_health_reports_ok_for_in_memory_stores(service):
    assert service.health() == {
        "status": "ok",
        "checks": {"cosmos": "ok", "rawBlobs": "ok", "processedBlobs": "ok"},
    }


def test_summarize_result_handles_missing_billing():
    summary = summarize_result({"segments": [{"doc_type": "w2"}]})
    assert summary["segmentCount"] == 1
    assert summary["estimatedCostUsd"] is None


def test_get_library_service_requires_endpoints(monkeypatch):
    monkeypatch.setattr(library, "_service", None)
    monkeypatch.setattr(library.settings, "cosmos_endpoint", "")
    with pytest.raises(LibraryUnavailable):
        library.get_library_service()


def test_get_library_service_reports_init_failures(monkeypatch):
    monkeypatch.setattr(library, "_service", None)
    monkeypatch.setattr(library.settings, "cosmos_endpoint", "https://example.documents.azure.com:443/")
    monkeypatch.setattr(library.settings, "storage_blob_url", "https://example.blob.core.windows.net/")

    def boom(*args, **kwargs):
        raise RuntimeError("Forbidden by firewall")

    monkeypatch.setattr(library, "CosmosDocumentStore", boom)
    with pytest.raises(LibraryUnavailable, match="Forbidden by firewall"):
        library.get_library_service()
    assert library._service is None
