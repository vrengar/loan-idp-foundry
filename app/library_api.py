"""HTTP routes for the document library (/api/*). Processing reuses the /process pipeline."""
from __future__ import annotations

import logging
from typing import Any, Callable, Literal, NoReturn

from fastapi import APIRouter, File, Header, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field

from .config import settings
from .cu_router import router_state
from .examples import (
    ExampleNotFound,
    example_doc_id,
    get_example,
    load_manifest,
    pdf_bytes,
    stored_result,
    summarize_example,
)
from .knowledge import KnowledgeClient, knowledge_state
from .library import LibraryError, LibraryService, LibraryUnavailable, NotFound, normalize_tenant
from .telemetry import annotate_current_span

log = logging.getLogger("idp.library")

Strategy = Literal["heuristic", "cu"]


class AskBody(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class DecisionBody(BaseModel):
    decision: Literal["approve", "refer", "decline"]
    note: str = Field(default="", max_length=1000)


def _http(exc: LibraryError) -> NoReturn:
    raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


def create_library_router(
    *,
    get_service: Callable[[], LibraryService],
    get_di: Callable[[], Any],
    run_pipeline: Callable[..., dict[str, Any]],
    get_agents: Callable[[], Any] | None = None,
    get_knowledge: Callable[[], KnowledgeClient] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["library"])
    max_bytes = settings.max_upload_mb * 1024 * 1024

    def _knowledge() -> KnowledgeClient:
        state = knowledge_state()["status"]
        if get_knowledge is None or state != "ready":
            raise HTTPException(status_code=503, detail=f"Knowledge base is not ready ({state}).")
        return get_knowledge()

    def _index_packet(service: LibraryService, record: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        if get_knowledge is None or knowledge_state()["status"] != "ready":
            return service.set_search_index(record, None, "knowledge base is not ready")
        try:
            chunks = get_knowledge().index_packet(
                record["id"], record["tenantId"], record.get("fileName") or "packet.pdf", result
            )
        except Exception as exc:
            log.warning("packet indexing failed document=%s", record["id"], exc_info=True)
            return service.set_search_index(record, None, f"{type(exc).__name__}: {str(exc)[:300]}")
        return service.set_search_index(record, chunks)

    def _purge_packet(record: dict[str, Any]) -> None:
        if get_knowledge is None or knowledge_state()["status"] != "ready":
            return
        try:
            get_knowledge().delete_document(record["tenantId"], record["id"])
        except Exception:
            log.warning("search purge failed document=%s", record["id"], exc_info=True)

    @router.get("/status")
    def status() -> dict[str, Any]:
        cu = router_state()
        knowledge = knowledge_state()
        try:
            return {"library": get_service().health(), "cuRouter": cu, "knowledge": knowledge}
        except LibraryUnavailable as exc:
            return {"library": {"status": "unavailable", "detail": str(exc)}, "cuRouter": cu, "knowledge": knowledge}

    @router.get("/documents")
    def list_documents(
        x_tenant_id: str | None = Header(default=None, alias=settings.tenant_id_header),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> list[dict[str, Any]]:
        try:
            return get_service().list_documents(x_tenant_id, limit)
        except LibraryError as exc:
            _http(exc)

    @router.post("/documents", status_code=201)
    def upload_document(
        file: UploadFile = File(...),
        mode: Strategy = Query(default="heuristic"),
        x_tenant_id: str | None = Header(default=None, alias=settings.tenant_id_header),
    ) -> dict[str, Any]:
        try:
            service = get_service()
            data = file.file.read(max_bytes + 1)
            record = service.create_document(x_tenant_id, file.filename, file.content_type, data, mode)
            record = service.set_status(record, "processing")
        except LibraryError as exc:
            _http(exc)
        try:
            result = run_pipeline(mode, get_di(), data, record["tenantId"], file.filename)
            saved = service.save_result(record, result)
        except HTTPException as exc:
            service.mark_failed(record, str(exc.detail))
            raise
        except Exception as exc:
            log.exception("processing failed document=%s", record["id"])
            service.mark_failed(record, f"{type(exc).__name__}: {exc}")
            raise HTTPException(
                status_code=502,
                detail={
                    "message": "Processing failed. The document is saved with status 'failed'.",
                    "documentId": record["id"],
                },
            ) from exc
        saved = _index_packet(service, saved, result)
        return saved

    @router.get("/documents/{doc_id}")
    def get_document(
        doc_id: str,
        x_tenant_id: str | None = Header(default=None, alias=settings.tenant_id_header),
    ) -> dict[str, Any]:
        try:
            return get_service().get_document(x_tenant_id, doc_id)
        except LibraryError as exc:
            _http(exc)

    @router.get("/documents/{doc_id}/result")
    def get_result(
        doc_id: str,
        x_tenant_id: str | None = Header(default=None, alias=settings.tenant_id_header),
    ) -> dict[str, Any]:
        try:
            return get_service().get_result(x_tenant_id, doc_id)
        except LibraryError as exc:
            _http(exc)

    @router.delete("/documents/{doc_id}", status_code=204)
    def delete_document(
        doc_id: str,
        x_tenant_id: str | None = Header(default=None, alias=settings.tenant_id_header),
    ) -> Response:
        try:
            service = get_service()
            record = service.get_document(x_tenant_id, doc_id)
            service.delete_document(x_tenant_id, doc_id)
        except LibraryError as exc:
            _http(exc)
        _purge_packet(record)
        return Response(status_code=204)

    def _agents() -> Any:
        if get_agents is None:
            raise HTTPException(status_code=503, detail="Agents are not configured.")
        return get_agents()

    @router.post("/documents/{doc_id}/ask")
    def ask_document(
        doc_id: str,
        body: AskBody,
        x_tenant_id: str | None = Header(default=None, alias=settings.tenant_id_header),
    ) -> dict[str, Any]:
        try:
            result = get_service().get_result(x_tenant_id, doc_id)
        except LibraryError as exc:
            _http(exc)
        annotate_current_span({"idp.tenant_id": normalize_tenant(x_tenant_id), "idp.document_id": doc_id})
        try:
            return _agents().ask(body.question, result)
        except HTTPException:
            raise
        except Exception as exc:
            log.exception("ask failed document=%s", doc_id)
            raise HTTPException(status_code=502, detail=f"Agent error: {type(exc).__name__}: {exc}") from exc

    @router.post("/documents/{doc_id}/review")
    def review_document(
        doc_id: str,
        x_tenant_id: str | None = Header(default=None, alias=settings.tenant_id_header),
    ) -> dict[str, Any]:
        try:
            service = get_service()
            record = service.get_document(x_tenant_id, doc_id)
            result = service.get_result(x_tenant_id, doc_id)
        except LibraryError as exc:
            _http(exc)
        annotate_current_span({"idp.tenant_id": normalize_tenant(x_tenant_id), "idp.document_id": doc_id})
        try:
            review = _agents().review(result)
        except HTTPException:
            raise
        except Exception as exc:
            log.exception("review failed document=%s", doc_id)
            raise HTTPException(status_code=502, detail=f"Review error: {type(exc).__name__}: {exc}") from exc
        return service.set_review(record, review)

    @router.post("/documents/{doc_id}/decision")
    def decide_document(
        doc_id: str,
        body: DecisionBody,
        x_tenant_id: str | None = Header(default=None, alias=settings.tenant_id_header),
    ) -> dict[str, Any]:
        try:
            service = get_service()
            record = service.get_document(x_tenant_id, doc_id)
        except LibraryError as exc:
            _http(exc)
        if not record.get("review"):
            raise HTTPException(status_code=409, detail="Run the review before recording a decision.")
        return service.set_decision(record, body.decision, body.note)

    @router.post("/library/ask")
    def ask_library(
        body: AskBody,
        x_tenant_id: str | None = Header(default=None, alias=settings.tenant_id_header),
    ) -> dict[str, Any]:
        try:
            tenant = normalize_tenant(x_tenant_id)
        except LibraryError as exc:
            _http(exc)
        annotate_current_span({"idp.tenant_id": tenant})
        try:
            passages = _knowledge().retrieve_library(body.question, tenant)
        except HTTPException:
            raise
        except Exception as exc:
            log.exception("library retrieval failed")
            raise HTTPException(status_code=502, detail=f"Knowledge base error: {type(exc).__name__}: {exc}") from exc
        try:
            return _agents().ask_library(body.question, passages, tenant)
        except HTTPException:
            raise
        except Exception as exc:
            log.exception("library ask failed")
            raise HTTPException(status_code=502, detail=f"Agent error: {type(exc).__name__}: {exc}") from exc

    def _example(example_id: str) -> dict[str, Any]:
        try:
            return get_example(example_id)
        except ExampleNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    def _find_document(service: LibraryService, tenant: str, doc_id: str) -> dict[str, Any] | None:
        try:
            return service.get_document(tenant, doc_id)
        except NotFound:
            return None

    @router.get("/examples")
    def list_example_packets() -> list[dict[str, Any]]:
        return [summarize_example(item) for item in load_manifest()]

    @router.get("/examples/{example_id}/pdf")
    def example_packet_pdf(example_id: str) -> Response:
        example = _example(example_id)
        return Response(
            content=pdf_bytes(example),
            media_type="application/pdf",
            headers={"content-disposition": f'inline; filename="{example["file"]}"'},
        )

    @router.post("/examples/{example_id}/load")
    def load_example_packet(
        example_id: str,
        mode: Strategy = Query(default="heuristic"),
        x_tenant_id: str | None = Header(default=None, alias=settings.tenant_id_header),
    ) -> dict[str, Any]:
        example = _example(example_id)
        try:
            result = stored_result(example, mode)
        except ExampleNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        try:
            service = get_service()
            tenant = normalize_tenant(x_tenant_id)
            doc_id = example_doc_id(example_id, mode)
            record = _find_document(service, tenant, doc_id)
            if record is None:
                record = service.create_document(
                    tenant, example["file"], "application/pdf", pdf_bytes(example), mode,
                    doc_id=doc_id, example_id=example_id,
                )
            result = {**result, "tenantId": tenant, "filename": example["file"]}
            # An earlier load may have stopped after the PDF was stored or before indexing; finish whichever step is missing.
            if record["status"] != "processed":
                record = service.save_result(record, result)
            if not (record.get("searchIndex") or {}).get("chunks"):
                record = _index_packet(service, record, result)
            return record
        except LibraryError as exc:
            _http(exc)

    return router
