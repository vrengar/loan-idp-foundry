"""Azure AI Search index and Foundry IQ knowledge base for policy and loan packet passages.

Processed packets are indexed one chunk per segment and scoped to the caller's tenant. The
underwriting policy is indexed once per startup under the `_global` tenant. The knowledge base
wraps the index as a knowledge source and returns extractive passages with minimal reasoning
effort, so retrieval does not call an LLM. REST calls use api-version 2026-04-01 (generally
available) and the app's managed identity.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections.abc import Sequence
from typing import Any

import requests

from .config import settings

log = logging.getLogger("idp.knowledge")

API_VERSION = "2026-04-01"
SEARCH_SCOPE = "https://search.azure.com/.default"
GLOBAL_TENANT = "_global"
POLICY_DOC_ID = "underwriting-policy"
POLICY_FILTER = "chunkType eq 'policy'"
REQUEST_TIMEOUT_S = 60
RETRIEVE_RUNTIME_S = 30
UPSERT_BATCH = 500
MAX_LIBRARY_PASSAGES = 10
SETUP_ATTEMPTS = 10
SETUP_RETRY_S = 20
_SAFE_VALUE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

_state: dict[str, str] = {"status": "pending", "detail": ""}
_state_lock = threading.Lock()
_client: KnowledgeClient | None = None
_client_lock = threading.Lock()


class KnowledgeError(RuntimeError):
    """Azure AI Search or the knowledge base could not serve the request."""


def knowledge_state() -> dict[str, str]:
    with _state_lock:
        return dict(_state)


def _set_state(status: str, detail: str = "") -> None:
    with _state_lock:
        _state.update(status=status, detail=detail)


def _safe(name: str, value: str) -> str:
    if not _SAFE_VALUE.match(value or ""):
        raise ValueError(f"{name} contains characters that are not allowed in a search filter")
    return value


def _field(name: str, *, key: bool = False, filterable: bool = False, searchable: bool = False) -> dict[str, Any]:
    return {
        "name": name,
        "type": "Edm.String",
        "key": key,
        "retrievable": True,
        "filterable": filterable or key,
        "searchable": searchable,
    }


def index_definition(index_name: str) -> dict[str, Any]:
    return {
        "name": index_name,
        "fields": [
            _field("id", key=True),
            _field("tenantId", filterable=True),
            _field("docId", filterable=True),
            _field("chunkType", filterable=True),
            _field("docType", filterable=True),
            _field("fileName"),
            _field("title", searchable=True),
            _field("content", searchable=True),
        ],
        "semantic": {
            "defaultConfiguration": "default",
            "configurations": [{
                "name": "default",
                "prioritizedFields": {
                    "titleField": {"fieldName": "title"},
                    "prioritizedContentFields": [{"fieldName": "content"}],
                    "prioritizedKeywordsFields": [],
                },
            }],
        },
    }


def knowledge_source_definition(source_name: str, index_name: str) -> dict[str, Any]:
    return {
        "name": source_name,
        "kind": "searchIndex",
        "description": "Loan packet segments and underwriting policy rules.",
        "searchIndexParameters": {
            "searchIndexName": index_name,
            "semanticConfigurationName": "default",
        },
    }


def knowledge_base_definition(base_name: str, source_name: str) -> dict[str, Any]:
    # 2026-04-01 knowledge bases are extractive (no LLM); the reasoning-effort and model
    # settings belong to the 2026-08-01-preview answer-synthesis path and are not sent here.
    return {
        "name": base_name,
        "description": "Grounding for the loan review agents: underwriting policy and packet passages.",
        "knowledgeSources": [{"name": source_name}],
    }


_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def field_label(name: str) -> str:
    """Readable label for a field key, so keyword search matches words in it (EmployerName -> Employer Name)."""
    return _CAMEL_BOUNDARY.sub(" ", str(name)).replace("_", " ").strip()


def packet_chunks(record_id: str, tenant_id: str, file_name: str, result: dict[str, Any]) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for number, segment in enumerate(result.get("segments") or [], start=1):
        doc_type = str(segment.get("doc_type") or "unknown")
        pages = "-".join(str(page) for page in segment.get("page_range") or [])
        lines = [f"Segment S{number}: {doc_type} (pages {pages}) in {file_name}"]
        for document in segment.get("documents") or []:
            fields = document.get("fields")
            if not isinstance(fields, dict):
                continue
            for name, field in fields.items():
                value = field.get("value") if isinstance(field, dict) else field
                if value not in (None, ""):
                    lines.append(f"{field_label(name)}: {value}")
        chunks.append({
            "id": f"{record_id}_s{number}",
            "tenantId": tenant_id,
            "docId": record_id,
            "chunkType": "packet",
            "docType": doc_type,
            "fileName": file_name,
            "title": f"{file_name}: S{number} {doc_type} (pages {pages})",
            "content": "\n".join(lines),
        })
    return chunks


def policy_chunks(rules: Sequence[str]) -> list[dict[str, Any]]:
    return [
        {
            "id": f"policy-{number}",
            "tenantId": GLOBAL_TENANT,
            "docId": POLICY_DOC_ID,
            "chunkType": "policy",
            "docType": "underwriting_policy",
            "fileName": "underwriting-policy",
            "title": f"Underwriting policy rule {number}",
            "content": rule,
        }
        for number, rule in enumerate(rules, start=1)
    ]


def library_filter(tenant_id: str) -> str:
    return f"{POLICY_FILTER} or tenantId eq '{_safe('tenant', tenant_id)}'"


def document_delete_filter(tenant_id: str, doc_id: str) -> str:
    return f"tenantId eq '{_safe('tenant', tenant_id)}' and docId eq '{_safe('document', doc_id)}'"


def parse_passages(payload: dict[str, Any]) -> list[dict[str, str]]:
    """Extractive retrieval returns a JSON-encoded list of passages in the first assistant message."""
    try:
        items = json.loads(payload["response"][0]["content"][0]["text"])
    except (KeyError, IndexError, TypeError, ValueError):
        return []
    if not isinstance(items, list):
        return []
    return [
        {"title": str(item.get("title") or ""), "content": str(item["content"])}
        for item in items
        if isinstance(item, dict) and item.get("content")
    ]


class KnowledgeClient:
    def __init__(
        self,
        endpoint: str,
        credential: Any,
        *,
        index_name: str,
        source_name: str,
        base_name: str,
        session: Any = None,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._credential = credential
        self.index_name = index_name
        self.source_name = source_name
        self.base_name = base_name
        self._session = session or requests.Session()

    def _call(self, method: str, path: str, body: Any = None) -> dict[str, Any]:
        token = self._credential.get_token(SEARCH_SCOPE).token
        response = self._session.request(
            method,
            f"{self._endpoint}{path}?api-version={API_VERSION}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=body,
            timeout=REQUEST_TIMEOUT_S,
        )
        if response.status_code >= 300:
            raise KnowledgeError(f"{method} {path} failed: HTTP {response.status_code} {response.text[:300]}")
        return response.json() if response.text else {}

    def ensure_schema(self) -> None:
        self._call("PUT", f"/indexes/{self.index_name}", index_definition(self.index_name))
        self._call("PUT", f"/knowledgesources/{self.source_name}",
                   knowledge_source_definition(self.source_name, self.index_name))
        self._call("PUT", f"/knowledgebases/{self.base_name}",
                   knowledge_base_definition(self.base_name, self.source_name))

    def upsert(self, chunks: list[dict[str, Any]]) -> None:
        for start in range(0, len(chunks), UPSERT_BATCH):
            batch = [{"@search.action": "mergeOrUpload", **chunk} for chunk in chunks[start:start + UPSERT_BATCH]]
            payload = self._call("POST", f"/indexes/{self.index_name}/docs/index", {"value": batch})
            rejected = [item for item in payload.get("value", []) if item.get("status") is False]
            if rejected:
                raise KnowledgeError(
                    f"{len(rejected)} chunk(s) rejected: {rejected[0].get('errorMessage', 'unknown error')}"
                )

    def index_packet(self, record_id: str, tenant_id: str, file_name: str, result: dict[str, Any]) -> int:
        chunks = packet_chunks(record_id, tenant_id, file_name, result)
        self.upsert(chunks)
        return len(chunks)

    def seed_policy(self, rules: Sequence[str]) -> int:
        self.upsert(policy_chunks(rules))
        return len(rules)

    def delete_document(self, tenant_id: str, doc_id: str) -> int:
        found = self._call("POST", f"/indexes/{self.index_name}/docs/search", {
            "search": "*",
            "filter": document_delete_filter(tenant_id, doc_id),
            "select": "id",
            "top": 1000,
        })
        ids = [item["id"] for item in found.get("value", []) if "id" in item]
        if ids:
            self._call("POST", f"/indexes/{self.index_name}/docs/index",
                       {"value": [{"@search.action": "delete", "id": chunk_id} for chunk_id in ids]})
        return len(ids)

    def retrieve(self, query: str, filter_expr: str) -> list[dict[str, str]]:
        body = {
            "intents": [{"type": "semantic", "search": query}],
            "knowledgeSourceParams": [{
                "knowledgeSourceName": self.source_name,
                "kind": "searchIndex",
                "filterAddOn": filter_expr,
            }],
            "maxRuntimeInSeconds": RETRIEVE_RUNTIME_S,
        }
        return parse_passages(self._call("POST", f"/knowledgebases/{self.base_name}/retrieve", body))

    def retrieve_policy(self, query: str) -> list[dict[str, str]]:
        return self.retrieve(query, POLICY_FILTER)

    def retrieve_library(self, query: str, tenant_id: str) -> list[dict[str, str]]:
        """Knowledge base retrieval, merged with a direct index search so no packet segment is missed.

        Extractive retrieval applies a relevance cut-off and can return only policy rules for a
        question, so the direct search always runs. Duplicates are removed by content.
        """
        passages: list[dict[str, str]] = []
        try:
            passages.extend(self.retrieve(query, library_filter(tenant_id)))
        except KnowledgeError:
            log.warning("knowledge base retrieval failed; using direct index search", exc_info=True)
        passages.extend(self.search_library(query, tenant_id))
        unique: dict[str, dict[str, str]] = {}
        for passage in passages:
            unique.setdefault(passage["content"], passage)
        return list(unique.values())[:MAX_LIBRARY_PASSAGES]

    def search_library(self, query: str, tenant_id: str, top: int = MAX_LIBRARY_PASSAGES) -> list[dict[str, str]]:
        found = self._call("POST", f"/indexes/{self.index_name}/docs/search", {
            "search": query,
            "queryType": "semantic",
            "semanticConfiguration": "default",
            "filter": library_filter(tenant_id),
            "select": "id,title,content",
            "top": top,
        })
        return [
            {"title": str(row.get("title") or ""), "content": str(row["content"])}
            for row in found.get("value", [])
            if row.get("content")
        ]

    def policy_rules(self) -> list[dict[str, str]]:
        """Every policy rule in the index. Relevance-ranked retrieval can miss rules, so review uses this."""
        found = self._call("POST", f"/indexes/{self.index_name}/docs/search", {
            "search": "*",
            "filter": POLICY_FILTER,
            "select": "id,title,content",
            "top": 100,
        })
        rows = [item for item in found.get("value", []) if item.get("content")]
        rows.sort(key=_policy_order)
        return [{"title": str(row.get("title") or ""), "content": str(row["content"])} for row in rows]


def _policy_order(row: dict[str, Any]) -> int:
    try:
        return int(str(row.get("id", "")).rsplit("-", 1)[-1])
    except ValueError:
        return 0


def get_knowledge() -> KnowledgeClient:
    global _client
    with _client_lock:
        if _client is None:
            if not settings.search_endpoint:
                raise KnowledgeError("SEARCH_ENDPOINT is not set")
            from azure.identity import DefaultAzureCredential

            _client = KnowledgeClient(
                settings.search_endpoint,
                DefaultAzureCredential(),
                index_name=settings.search_index_name,
                source_name=settings.knowledge_source_name,
                base_name=settings.knowledge_base_name,
            )
        return _client


def ready_knowledge() -> KnowledgeClient:
    """Return the client only after setup has finished, so callers can fall back to inline data."""
    status = knowledge_state()["status"]
    if status != "ready":
        raise KnowledgeError(f"knowledge base is {status}")
    return get_knowledge()


def provision(rules: Sequence[str], *, attempts: int = SETUP_ATTEMPTS, delay_s: float = SETUP_RETRY_S) -> bool:
    """Create the index, knowledge source, and knowledge base, then seed the policy.

    Retries because replicas starting together, or a service still finishing an index create,
    can return 409 conflicts for a short while.
    """
    _set_state("creating")
    last_error = ""
    for attempt in range(1, attempts + 1):
        try:
            client = get_knowledge()
            client.ensure_schema()
            client.seed_policy(rules)
            _set_state("ready", settings.knowledge_base_name)
            return True
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {str(exc)[:300]}"
            log.warning("knowledge setup attempt %d of %d failed: %s", attempt, attempts, last_error)
            _set_state("creating", f"attempt {attempt} of {attempts} failed")
            if attempt < attempts:
                time.sleep(delay_s)
    _set_state("error", last_error)
    return False


def start_background_setup(rules: Sequence[str]) -> None:
    if not settings.search_endpoint:
        _set_state("skipped", "SEARCH_ENDPOINT is not set")
        return
    threading.Thread(target=provision, args=(rules,), name="knowledge-setup", daemon=True).start()
