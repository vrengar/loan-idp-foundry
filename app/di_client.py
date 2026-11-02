"""Thin wrapper over Azure AI Document Intelligence SDK."""
from __future__ import annotations

import time
from typing import Any

from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import AnalyzeDocumentRequest, AnalyzeResult
from azure.core.credentials import AzureKeyCredential

from .config import settings


class DIClient:
    """Thin wrapper around the Azure DI SDK.

    Auth strategy:
      - If DI_KEY is set -> AzureKeyCredential (handy for local dev).
      - Otherwise -> DefaultAzureCredential, which inside Container Apps
        picks up the User-Assigned Managed Identity automatically (no secrets).
    """

    def __init__(self) -> None:
        if not settings.di_endpoint:
            raise RuntimeError("DI_ENDPOINT must be set.")
        credential: Any
        if settings.di_key:
            # Key auth — only recommended for local dev.
            credential = AzureKeyCredential(settings.di_key)
        else:
            # Managed-identity / Entra ID auth — production path.
            try:
                from azure.identity import DefaultAzureCredential
            except ImportError as e:
                raise RuntimeError(
                    "DI_KEY not set and azure-identity is not installed. "
                    "Either set DI_KEY or `pip install azure-identity` to use Entra ID auth."
                ) from e
            credential = DefaultAzureCredential()
        self._client = DocumentIntelligenceClient(
            endpoint=settings.di_endpoint,
            credential=credential,
        )

    def analyze(
        self,
        *,
        model_id: str,
        content: bytes,
        pages: str | None = None,
    ) -> tuple[AnalyzeResult, float]:
        """Run an analyze operation against any prebuilt or custom extraction model.

        DI's analyze endpoint is async on the service side: begin_analyze_document()
        returns a poller; .result() blocks until the operation completes (typically
        5–30s depending on model + page count). We measure wall time so the
        pipeline can stamp it on telemetry as `durationMs`.

        Returns (result, duration_ms).
        """
        start = time.perf_counter()
        poller = self._client.begin_analyze_document(
            model_id=model_id,
            body=AnalyzeDocumentRequest(bytes_source=content),
            pages=pages,
        )
        result: AnalyzeResult = poller.result()
        duration_ms = (time.perf_counter() - start) * 1000.0
        return result, duration_ms

    @staticmethod
    def page_text(result: AnalyzeResult) -> list[str]:
        """Return joined text per page, in page order."""
        if not result.pages:
            return []
        out: list[str] = []
        for page in result.pages:
            words = [w.content for w in (page.words or [])]
            out.append(" ".join(words))
        return out

    @staticmethod
    def summarize_fields(result: AnalyzeResult) -> list[dict[str, Any]]:
        """Return a compact per-document field summary with confidences."""
        out: list[dict[str, Any]] = []
        for doc in (result.documents or []):
            fields = {}
            for name, field in (doc.fields or {}).items():
                fields[name] = {
                    "value": getattr(field, "content", None) or _coerce_value(field),
                    "confidence": getattr(field, "confidence", None),
                }
            out.append({
                "doc_type": doc.doc_type,
                "confidence": doc.confidence,
                "fields": fields,
            })
        return out


def _coerce_value(field: Any) -> Any:
    for attr in ("value_string", "value_number", "value_date", "value_currency", "value_address"):
        v = getattr(field, attr, None)
        if v is not None:
            return str(v)
    return None
