import json

import pytest

from app import knowledge
from app.knowledge import (
    KnowledgeClient,
    KnowledgeError,
    document_delete_filter,
    field_label,
    index_definition,
    knowledge_base_definition,
    knowledge_source_definition,
    library_filter,
    packet_chunks,
    parse_passages,
    policy_chunks,
)

RESULT = {
    "segments": [
        {
            "doc_type": "paystub",
            "page_range": [1, 2],
            "documents": [{"fields": {
                "Employer": {"value": "Umbrella Health", "confidence": 0.9},
                "Gross Pay": {"value": "3127.82", "confidence": 0.8},
            }}],
        },
        {"doc_type": "w2", "page_range": [3], "documents": [{"fields": None}]},
    ]
}


class FakeToken:
    token = "test-token"


class FakeCredential:
    def get_token(self, scope):
        assert scope == "https://search.azure.com/.default"
        return FakeToken()


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.text = json.dumps(payload) if payload is not None else ""
        self._payload = payload

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0) if self.responses else FakeResponse(200, {})


def make_client(session):
    return KnowledgeClient(
        "https://srch.example.net/",
        FakeCredential(),
        index_name="loan-documents",
        source_name="loan-docs-ks",
        base_name="loan-kb",
        session=session,
    )


def extractive_payload(items):
    return {"response": [{"role": "assistant", "content": [{"type": "text", "text": json.dumps(items)}]}]}


def test_packet_chunks_are_one_per_segment_with_labels_and_tenant_scope():
    chunks = packet_chunks("doc-1", "tenant-a", "packet.pdf", RESULT)
    assert [chunk["id"] for chunk in chunks] == ["doc-1_s1", "doc-1_s2"]
    first = chunks[0]
    assert first["tenantId"] == "tenant-a"
    assert first["docId"] == "doc-1"
    assert first["chunkType"] == "packet"
    assert "Segment S1: paystub (pages 1-2) in packet.pdf" in first["content"]
    assert "Gross Pay: 3127.82" in first["content"]
    assert "\n" not in chunks[1]["content"]


def test_policy_chunks_are_global_and_one_per_rule():
    chunks = policy_chunks(["Two paystubs.", "A W-2."])
    assert [chunk["id"] for chunk in chunks] == ["policy-1", "policy-2"]
    assert all(chunk["tenantId"] == "_global" and chunk["chunkType"] == "policy" for chunk in chunks)
    assert chunks[1]["content"] == "A W-2."


def test_filters_reject_values_that_could_change_the_query():
    with pytest.raises(ValueError):
        library_filter("tenant' or '1'='1")
    with pytest.raises(ValueError):
        document_delete_filter("tenant-a", "x' or docId ne 'y")
    assert library_filter("tenant-a") == "chunkType eq 'policy' or tenantId eq 'tenant-a'"


def test_index_has_default_semantic_configuration_and_filterable_scope_fields():
    index = index_definition("loan-documents")
    assert index["semantic"]["defaultConfiguration"] == "default"
    assert index["semantic"]["configurations"][0]["name"] == "default"
    fields = {field["name"]: field for field in index["fields"]}
    assert fields["id"]["key"] is True
    assert fields["tenantId"]["filterable"] is True
    assert fields["docId"]["filterable"] is True
    assert fields["chunkType"]["filterable"] is True


def test_knowledge_base_is_extractive_without_a_model():
    base = knowledge_base_definition("loan-kb", "loan-docs-ks")
    assert "models" not in base
    assert "retrievalReasoningEffort" not in base
    assert base["knowledgeSources"] == [{"name": "loan-docs-ks"}]
    source = knowledge_source_definition("loan-docs-ks", "loan-documents")
    assert source["kind"] == "searchIndex"
    assert source["searchIndexParameters"] == {
        "searchIndexName": "loan-documents",
        "semanticConfigurationName": "default",
    }


def test_parse_passages_reads_the_extractive_json_text():
    payload = extractive_payload([
        {"ref_id": "0", "title": "Rule 1", "content": "Two paystubs."},
        {"title": "no content"},
    ])
    assert parse_passages(payload) == [{"title": "Rule 1", "content": "Two paystubs."}]
    assert parse_passages({"response": []}) == []
    assert parse_passages({"response": [{"content": [{"text": "not json"}]}]}) == []


def test_retrieve_sends_intents_and_the_filter_to_the_knowledge_base():
    session = FakeSession([FakeResponse(200, extractive_payload([{"title": "Rule 1", "content": "Two paystubs."}]))])
    passages = make_client(session).retrieve_policy("paystub requirements")
    assert passages == [{"title": "Rule 1", "content": "Two paystubs."}]
    call = session.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://srch.example.net/knowledgebases/loan-kb/retrieve?api-version=2026-04-01"
    assert call["headers"]["Authorization"] == "Bearer test-token"
    assert call["json"]["intents"] == [{"type": "semantic", "search": "paystub requirements"}]
    source_params = call["json"]["knowledgeSourceParams"][0]
    assert source_params["knowledgeSourceName"] == "loan-docs-ks"
    assert source_params["filterAddOn"] == "chunkType eq 'policy'"


def test_http_errors_surface_as_knowledge_error():
    session = FakeSession([FakeResponse(403, {"error": {"message": "forbidden"}})])
    with pytest.raises(KnowledgeError, match="HTTP 403"):
        make_client(session).retrieve_policy("question")


def test_upsert_raises_when_a_chunk_is_rejected():
    session = FakeSession([FakeResponse(200, {"value": [{"key": "x", "status": False, "errorMessage": "bad field"}]})])
    with pytest.raises(KnowledgeError, match="bad field"):
        make_client(session).upsert(policy_chunks(["rule"]))


def test_index_packet_sends_merge_or_upload_actions():
    session = FakeSession([FakeResponse(200, {"value": [
        {"key": "doc-1_s1", "status": True},
        {"key": "doc-1_s2", "status": True},
    ]})])
    assert make_client(session).index_packet("doc-1", "tenant-a", "packet.pdf", RESULT) == 2
    actions = session.calls[0]["json"]["value"]
    assert all(action["@search.action"] == "mergeOrUpload" for action in actions)
    assert session.calls[0]["url"].endswith("/indexes/loan-documents/docs/index?api-version=2026-04-01")


def test_delete_document_finds_then_deletes_its_chunks():
    session = FakeSession([
        FakeResponse(200, {"value": [{"id": "doc-1_s1"}, {"id": "doc-1_s2"}]}),
        FakeResponse(200, {"value": [{"key": "doc-1_s1", "status": True}, {"key": "doc-1_s2", "status": True}]}),
    ])
    assert make_client(session).delete_document("tenant-a", "doc-1") == 2
    assert session.calls[0]["json"]["filter"] == "tenantId eq 'tenant-a' and docId eq 'doc-1'"
    actions = session.calls[1]["json"]["value"]
    assert [action["@search.action"] for action in actions] == ["delete", "delete"]
    assert [action["id"] for action in actions] == ["doc-1_s1", "doc-1_s2"]


def test_policy_rules_read_every_rule_in_numeric_order():
    session = FakeSession([FakeResponse(200, {"value": [
        {"id": "policy-10", "title": "Rule 10", "content": "Ten."},
        {"id": "policy-2", "title": "Rule 2", "content": "Two."},
        {"id": "policy-1", "title": "Rule 1", "content": "One."},
        {"id": "policy-3", "title": "Rule 3", "content": ""},
    ]})])
    rules = make_client(session).policy_rules()
    assert [rule["content"] for rule in rules] == ["One.", "Two.", "Ten."]
    call = session.calls[0]
    assert call["json"]["filter"] == "chunkType eq 'policy'"
    assert call["url"].endswith("/indexes/loan-documents/docs/search?api-version=2026-04-01")


def test_library_retrieval_merges_knowledge_base_and_direct_search():
    session = FakeSession([
        FakeResponse(200, extractive_payload([{"title": "Rule 2", "content": "Monthly gross income must be supported."}])),
        FakeResponse(200, {"value": [
            {"id": "doc-1_s1", "title": "packet.pdf: S1 paystub", "content": "Gross Pay: 3127.82"},
            {"id": "policy-2", "title": "Rule 2", "content": "Monthly gross income must be supported."},
        ]}),
    ])
    passages = make_client(session).retrieve_library("gross pay employer", "tenant-a")
    contents = [passage["content"] for passage in passages]
    assert "Gross Pay: 3127.82" in contents
    assert contents.count("Monthly gross income must be supported.") == 1
    search_body = session.calls[1]["json"]
    assert search_body["queryType"] == "semantic"
    assert search_body["filter"] == "chunkType eq 'policy' or tenantId eq 'tenant-a'"


def test_library_retrieval_survives_knowledge_base_errors():
    session = FakeSession([
        FakeResponse(503, {"error": "busy"}),
        FakeResponse(200, {"value": [{"id": "doc-1_s1", "title": "packet.pdf: S1", "content": "Umbrella Health"}]}),
    ])
    passages = make_client(session).retrieve_library("employer", "tenant-a")
    assert [passage["content"] for passage in passages] == ["Umbrella Health"]


def test_field_labels_split_camel_case_so_keyword_search_matches_words():
    assert field_label("EmployerName") == "Employer Name"
    assert field_label("CurrentPeriodGrossPay") == "Current Period Gross Pay"
    assert field_label("EmployeeSSN") == "Employee SSN"
    assert field_label("pay_date") == "pay date"
    result = {"segments": [{"doc_type": "paystub", "page_range": [1, 1], "documents": [{"fields": {
        "EmployerName": {"value": "Umbrella Health", "confidence": 0.9},
        "CurrentPeriodGrossPay": {"value": "3127.82", "confidence": 0.8},
    }}]}]}
    content = packet_chunks("doc-1", "tenant-a", "packet.pdf", result)[0]["content"]
    assert "Employer Name: Umbrella Health" in content
    assert "Current Period Gross Pay: 3127.82" in content


def test_provision_retries_transient_conflicts(monkeypatch):
    monkeypatch.setitem(knowledge._state, "status", "pending")
    monkeypatch.setitem(knowledge._state, "detail", "")
    calls = {"schema": 0, "seed": 0}

    class Flaky:
        def ensure_schema(self):
            calls["schema"] += 1
            if calls["schema"] < 3:
                raise KnowledgeError("PUT /knowledgesources/loan-docs-ks failed: HTTP 409")

        def seed_policy(self, rules):
            calls["seed"] += 1
            return len(rules)

    monkeypatch.setattr(knowledge, "get_knowledge", lambda: Flaky())
    assert knowledge.provision(["rule"], attempts=5, delay_s=0) is True
    assert calls == {"schema": 3, "seed": 1}
    assert knowledge.knowledge_state()["status"] == "ready"


def test_provision_reports_error_after_the_last_attempt(monkeypatch):
    monkeypatch.setitem(knowledge._state, "status", "pending")
    monkeypatch.setitem(knowledge._state, "detail", "")

    class Broken:
        def ensure_schema(self):
            raise KnowledgeError("PUT /indexes/loan-documents failed: HTTP 403")

        def seed_policy(self, rules):
            return len(rules)

    monkeypatch.setattr(knowledge, "get_knowledge", lambda: Broken())
    assert knowledge.provision(["rule"], attempts=2, delay_s=0) is False
    state = knowledge.knowledge_state()
    assert state["status"] == "error"
    assert "HTTP 403" in state["detail"]


def test_ready_knowledge_refuses_until_setup_finishes(monkeypatch):
    monkeypatch.setitem(knowledge._state, "status", "creating")
    with pytest.raises(KnowledgeError, match="creating"):
        knowledge.ready_knowledge()
