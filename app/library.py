"""Document library service: upload validation, tenant scoping, status and result storage."""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Protocol

from azure.identity import DefaultAzureCredential

from .config import settings
from .persistence.blobs import AzureBlobStore
from .persistence.documents import CosmosDocumentStore

PDF_MAGIC = b"%PDF-"
PDF_CONTENT_TYPES = ("application/pdf", "application/octet-stream")
TENANT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
DEFAULT_TENANT = "anonymous"


class LibraryError(Exception):
    status_code = 500


class LibraryUnavailable(LibraryError):
    status_code = 503


class InvalidRequest(LibraryError):
    status_code = 400


class UnsupportedMedia(LibraryError):
    status_code = 415


class TooLarge(LibraryError):
    status_code = 413


class NotFound(LibraryError):
    status_code = 404


class DocumentStore(Protocol):
    def create(self, record: dict[str, Any]) -> dict[str, Any]: ...
    def get(self, tenant_id: str, doc_id: str) -> dict[str, Any] | None: ...
    def list_recent(self, tenant_id: str, limit: int) -> list[dict[str, Any]]: ...
    def replace(self, record: dict[str, Any]) -> dict[str, Any]: ...
    def delete(self, tenant_id: str, doc_id: str) -> None: ...
    def ping(self) -> None: ...


class BlobStore(Protocol):
    def put(self, container: str, name: str, data: bytes, content_type: str) -> None: ...
    def get(self, container: str, name: str) -> bytes: ...
    def delete(self, container: str, name: str) -> None: ...
    def ping(self, container: str) -> None: ...


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_tenant(tenant_id: str | None) -> str:
    tenant = (tenant_id or "").strip() or DEFAULT_TENANT
    if not TENANT_PATTERN.match(tenant):
        raise InvalidRequest("x-tenant-id must be 1-64 characters: letters, digits, '-' or '_'.")
    return tenant


def validate_pdf(data: bytes, content_type: str | None, max_bytes: int) -> None:
    if content_type not in PDF_CONTENT_TYPES:
        raise UnsupportedMedia("PDF required")
    if not data:
        raise InvalidRequest("Empty file")
    if len(data) > max_bytes:
        raise TooLarge(f"File exceeds the {max_bytes // (1024 * 1024)} MB limit")
    if not data.startswith(PDF_MAGIC):
        raise UnsupportedMedia("File is not a PDF")


def summarize_result(result: dict[str, Any]) -> dict[str, Any]:
    segments = result.get("segments") or []
    billing = result.get("billing") or {}
    return {
        "splitStrategy": result.get("splitStrategy"),
        "totalPages": result.get("totalPages"),
        "segmentCount": len(segments),
        "docTypes": [s.get("doc_type") for s in segments],
        "durationMs": result.get("totalDurationMs"),
        "estimatedCostUsd": billing.get("totalCostEstimateUsd"),
    }


class LibraryService:
    def __init__(
        self,
        documents: DocumentStore,
        blobs: BlobStore,
        *,
        raw_container: str,
        processed_container: str,
        max_upload_bytes: int,
    ) -> None:
        self._documents = documents
        self._blobs = blobs
        self._raw = raw_container
        self._processed = processed_container
        self._max_upload_bytes = max_upload_bytes

    def create_document(
        self,
        tenant_id: str | None,
        file_name: str | None,
        content_type: str | None,
        data: bytes,
        mode: str,
        *,
        doc_id: str | None = None,
        example_id: str | None = None,
    ) -> dict[str, Any]:
        tenant = normalize_tenant(tenant_id)
        validate_pdf(data, content_type, self._max_upload_bytes)
        doc_id = doc_id or uuid.uuid4().hex
        raw_name = f"{tenant}/{doc_id}/original.pdf"
        self._blobs.put(self._raw, raw_name, data, "application/pdf")
        now = _now()
        record = {
            "id": doc_id,
            "tenantId": tenant,
            "type": "document",
            "fileName": (file_name or "document.pdf")[:255],
            "contentType": "application/pdf",
            "sizeBytes": len(data),
            "mode": mode,
            "status": "uploaded",
            "createdAt": now,
            "updatedAt": now,
            "rawBlob": raw_name,
            "processedBlob": None,
            "exampleId": example_id,
            "summary": None,
            "error": None,
        }
        return self._documents.create(record)

    def list_documents(self, tenant_id: str | None, limit: int = 50) -> list[dict[str, Any]]:
        return self._documents.list_recent(normalize_tenant(tenant_id), limit)

    def get_document(self, tenant_id: str | None, doc_id: str) -> dict[str, Any]:
        record = self._documents.get(normalize_tenant(tenant_id), doc_id)
        if record is None:
            raise NotFound("Document not found")
        return record

    def set_status(self, record: dict[str, Any], status: str) -> dict[str, Any]:
        record["status"] = status
        record["updatedAt"] = _now()
        return self._documents.replace(record)

    def save_result(self, record: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        processed_name = f"{record['tenantId']}/{record['id']}.json"
        payload = json.dumps(result, default=str).encode("utf-8")
        self._blobs.put(self._processed, processed_name, payload, "application/json")
        record.update(
            status="processed",
            updatedAt=_now(),
            processedBlob=processed_name,
            summary=summarize_result(result),
            error=None,
        )
        return self._documents.replace(record)

    def mark_failed(self, record: dict[str, Any], message: str) -> dict[str, Any]:
        record.update(status="failed", updatedAt=_now(), error=message[:1000])
        return self._documents.replace(record)

    def get_result(self, tenant_id: str | None, doc_id: str) -> dict[str, Any]:
        record = self.get_document(tenant_id, doc_id)
        if not record.get("processedBlob"):
            raise NotFound("Document has no processed result yet")
        try:
            payload = self._blobs.get(self._processed, record["processedBlob"])
        except FileNotFoundError as exc:
            raise NotFound("Processed result is missing") from exc
        return json.loads(payload)

    def delete_document(self, tenant_id: str | None, doc_id: str) -> None:
        record = self.get_document(tenant_id, doc_id)
        self._blobs.delete(self._raw, record["rawBlob"])
        if record.get("processedBlob"):
            self._blobs.delete(self._processed, record["processedBlob"])
        self._documents.delete(record["tenantId"], record["id"])

    def set_review(self, record: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
        record.update(review=review, reviewStatus="awaiting_decision", decision=None, updatedAt=_now())
        return self._documents.replace(record)

    def set_decision(self, record: dict[str, Any], decision: str, note: str) -> dict[str, Any]:
        record.update(
            decision={"value": decision, "note": note, "decidedAt": _now()},
            reviewStatus="decided",
            updatedAt=_now(),
        )
        return self._documents.replace(record)

    def set_search_index(self, record: dict[str, Any], chunks: int | None, error: str = "") -> dict[str, Any]:
        record.update(
            searchIndex={"chunks": chunks, "error": error or None, "indexedAt": _now()},
            updatedAt=_now(),
        )
        return self._documents.replace(record)

    def health(self) -> dict[str, Any]:
        checks = {
            "cosmos": self._documents.ping,
            "rawBlobs": lambda: self._blobs.ping(self._raw),
            "processedBlobs": lambda: self._blobs.ping(self._processed),
        }
        results: dict[str, str] = {}
        for name, check in checks.items():
            try:
                check()
                results[name] = "ok"
            except Exception as exc:
                results[name] = f"error: {type(exc).__name__}: {str(exc)[:200]}"
        status = "ok" if all(v == "ok" for v in results.values()) else "degraded"
        return {"status": status, "checks": results}


_service: LibraryService | None = None


def get_library_service() -> LibraryService:
    global _service
    if _service is None:
        if not settings.cosmos_endpoint or not settings.storage_blob_url:
            raise LibraryUnavailable(
                "Document library not configured. Set COSMOS_ENDPOINT and STORAGE_BLOB_URL and restart."
            )
        try:
            credential = DefaultAzureCredential()
            _service = LibraryService(
                CosmosDocumentStore(
                    settings.cosmos_endpoint,
                    settings.cosmos_database,
                    settings.cosmos_documents_container,
                    credential,
                ),
                AzureBlobStore(settings.storage_blob_url, credential),
                raw_container=settings.blob_raw_container,
                processed_container=settings.blob_processed_container,
                max_upload_bytes=settings.max_upload_mb * 1024 * 1024,
            )
        except Exception as exc:
            raise LibraryUnavailable(
                f"Document library could not be initialized: {type(exc).__name__}: {str(exc)[:300]}"
            ) from exc
    return _service
