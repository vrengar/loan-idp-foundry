import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.library import LibraryService
from app.library_api import create_library_router
from app.persistence.memory import InMemoryBlobStore, InMemoryDocumentStore

PDF = b"%PDF-1.7\n" + b"x" * 64


def pipeline(strategy, di, pdf_bytes, tenant_id, filename):
    return {
        "splitStrategy": strategy,
        "tenantId": tenant_id,
        "filename": filename,
        "totalPages": 2,
        "totalDurationMs": 5.0,
        "billing": {"totalCostEstimateUsd": 0.01},
        "segments": [
            {"doc_type": "paystub", "page_range": [1, 2],
             "documents": [{"fields": [{"name": "Gross", "value": "5200"}]}]},
        ],
    }


class FakeAgents:
    def ask(self, question, result):
        return {"answer": f"You asked: {question} [S1]", "model": "claude-sonnet-5-5"}

    def review(self, result):
        return {"recommendation": "refer", "rationale": "W-2 missing",
                "conditions": ["Provide W-2"], "steps": []}

    def ask_library(self, question, passages, tenant_id=None):
        return {"answer": f"{len(passages)} passage(s) found [P1]", "model": "claude-sonnet-5-5",
                "passages": [{"label": f"P{n}", **p} for n, p in enumerate(passages, start=1)]}


class FakeKnowledge:
    def __init__(self):
        self.indexed = []
        self.purged = []
        self.library_queries = []

    def index_packet(self, record_id, tenant_id, file_name, result):
        self.indexed.append((record_id, tenant_id, file_name))
        return 1

    def delete_document(self, tenant_id, doc_id):
        self.purged.append((tenant_id, doc_id))
        return 1

    def retrieve_library(self, query, tenant_id):
        self.library_queries.append((query, tenant_id))
        return [{"title": "packet.pdf: S1 paystub (pages 1-2)", "content": "Gross Pay: 5200"}]


@pytest.fixture
def knowledge_ready(monkeypatch):
    monkeypatch.setattr("app.library_api.knowledge_state", lambda: {"status": "ready", "detail": ""})


def make_client(get_agents=lambda: FakeAgents(), get_knowledge=None):
    service = LibraryService(
        InMemoryDocumentStore(),
        InMemoryBlobStore(),
        raw_container="documents",
        processed_container="processed",
        max_upload_bytes=1024 * 1024,
    )
    app = FastAPI()
    app.include_router(create_library_router(
        get_service=lambda: service,
        get_di=lambda: object(),
        run_pipeline=pipeline,
        get_agents=get_agents,
        get_knowledge=get_knowledge,
    ))
    return TestClient(app)


def upload(client):
    return client.post(
        "/api/documents?mode=heuristic",
        headers={"x-tenant-id": "t1"},
        files={"file": ("packet.pdf", PDF, "application/pdf")},
    )


def test_ask_returns_agent_answer():
    client = make_client()
    doc_id = upload(client).json()["id"]
    reply = client.post(f"/api/documents/{doc_id}/ask", headers={"x-tenant-id": "t1"}, json={"question": "Income?"})
    assert reply.status_code == 200
    assert reply.json()["answer"] == "You asked: Income? [S1]"


def test_review_then_decision_is_recorded():
    client = make_client()
    doc_id = upload(client).json()["id"]
    review = client.post(f"/api/documents/{doc_id}/review", headers={"x-tenant-id": "t1"})
    assert review.status_code == 200
    assert review.json()["reviewStatus"] == "awaiting_decision"
    decided = client.post(
        f"/api/documents/{doc_id}/decision",
        headers={"x-tenant-id": "t1"},
        json={"decision": "approve", "note": "ok"},
    )
    assert decided.status_code == 200
    assert decided.json()["decision"]["value"] == "approve"
    assert decided.json()["reviewStatus"] == "decided"


def test_decision_requires_review_first():
    client = make_client()
    doc_id = upload(client).json()["id"]
    response = client.post(f"/api/documents/{doc_id}/decision", headers={"x-tenant-id": "t1"},
                           json={"decision": "approve"})
    assert response.status_code == 409


def test_agents_not_configured_returns_503():
    client = make_client(get_agents=None)
    doc_id = upload(client).json()["id"]
    response = client.post(f"/api/documents/{doc_id}/ask", headers={"x-tenant-id": "t1"},
                           json={"question": "Income?"})
    assert response.status_code == 503


def test_home_page_is_the_new_ui():
    from app.main import app as main_app

    response = TestClient(main_app).get("/")
    assert response.status_code == 200
    assert "Loan Document Intelligence" in response.text


def test_processed_packet_is_indexed_when_knowledge_is_ready(knowledge_ready):
    knowledge = FakeKnowledge()
    client = make_client(get_knowledge=lambda: knowledge)
    response = upload(client)
    assert response.status_code == 201
    doc_id = response.json()["id"]
    assert knowledge.indexed == [(doc_id, "t1", "packet.pdf")]
    assert response.json()["searchIndex"]["chunks"] == 1


def test_indexing_failure_is_recorded_on_the_document(knowledge_ready):
    class BrokenKnowledge(FakeKnowledge):
        def index_packet(self, record_id, tenant_id, file_name, result):
            raise RuntimeError("search rejected the chunk")

    client = make_client(get_knowledge=lambda: BrokenKnowledge())
    response = upload(client)
    assert response.status_code == 201
    assert response.json()["searchIndex"]["chunks"] is None
    assert "search rejected the chunk" in response.json()["searchIndex"]["error"]


def test_indexing_is_skipped_when_knowledge_is_not_ready():
    knowledge = FakeKnowledge()
    client = make_client(get_knowledge=lambda: knowledge)
    assert upload(client).status_code == 201
    assert knowledge.indexed == []


def test_delete_purges_search_chunks(knowledge_ready):
    knowledge = FakeKnowledge()
    client = make_client(get_knowledge=lambda: knowledge)
    doc_id = upload(client).json()["id"]
    assert client.delete(f"/api/documents/{doc_id}", headers={"x-tenant-id": "t1"}).status_code == 204
    assert knowledge.purged == [("t1", doc_id)]


def test_library_ask_answers_from_retrieved_passages(knowledge_ready):
    knowledge = FakeKnowledge()
    client = make_client(get_knowledge=lambda: knowledge)
    response = client.post("/api/library/ask", headers={"x-tenant-id": "t1"},
                           json={"question": "Who has paystubs?"})
    assert response.status_code == 200
    assert knowledge.library_queries == [("Who has paystubs?", "t1")]
    body = response.json()
    assert body["passages"][0]["label"] == "P1"
    assert body["answer"].startswith("1 passage")


def test_library_ask_is_unavailable_without_knowledge_base():
    client = make_client()
    response = client.post("/api/library/ask", headers={"x-tenant-id": "t1"}, json={"question": "Who?"})
    assert response.status_code == 503
