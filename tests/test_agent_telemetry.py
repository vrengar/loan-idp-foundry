"""Agent and Claude call spans: nesting, status, token usage, and what stays out of telemetry."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from app import claude as claude_module
from app import telemetry
from app.agents import LoanAgents
from app.claude import ClaudeClient, ClaudeError
from app.config import settings
from app.main import app as api_app

BASE_URL = "https://example.test/anthropic"
TOKEN = "token-do-not-record"

RESULT = {
    "tenantId": "tenant-a",
    "correlationId": "corr-1",
    "segments": [
        {
            "doc_type": "paystub",
            "page_range": [1, 2],
            "documents": [{"fields": {"Gross Pay": {"value": "5200", "confidence": 0.9}}}],
        },
    ],
}


class FakeCredential:
    def get_token(self, *scopes: Any, **kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(token=TOKEN, expires_on=0)


class FakeResponse:
    def __init__(self, status_code: int = 200, payload: dict[str, Any] | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self) -> dict[str, Any]:
        return self._payload


def reply(text: str, model: str, input_tokens: int = 120, output_tokens: int = 40) -> FakeResponse:
    return FakeResponse(payload={
        "model": model,
        "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        "stop_reason": "end_turn",
    })


@pytest.fixture
def spans(monkeypatch: pytest.MonkeyPatch) -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(telemetry, "_tracer", provider.get_tracer("test"))
    monkeypatch.setattr(claude_module.time, "sleep", lambda seconds: None)
    yield exporter
    provider.shutdown()


def use_replies(monkeypatch: pytest.MonkeyPatch, responses: list[FakeResponse]) -> None:
    queue = list(responses)

    def post(url: str, headers: dict[str, str], json: dict[str, Any], timeout: int) -> FakeResponse:
        return queue.pop(0)

    monkeypatch.setattr(claude_module.requests, "post", post)


def agents_with(monkeypatch: pytest.MonkeyPatch, responses: list[FakeResponse]) -> LoanAgents:
    use_replies(monkeypatch, responses)
    return LoanAgents(ClaudeClient(BASE_URL, FakeCredential()))


def finished_named(exporter: InMemorySpanExporter, name: str) -> ReadableSpan:
    matches = [span for span in exporter.get_finished_spans() if span.name == name]
    assert len(matches) == 1, f"expected one span named {name!r}, found {len(matches)}"
    return matches[0]


def chat_spans(exporter: InMemorySpanExporter) -> list[ReadableSpan]:
    return [span for span in exporter.get_finished_spans() if span.name.startswith("chat ")]


def test_review_nests_each_role_and_its_model_call(spans: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch):
    agents = agents_with(monkeypatch, [
        reply('{"applicant_name": "Jane"}', settings.claude_haiku_deployment),
        reply('{"checks": [{"rule": "paystubs", "status": "pass", "evidence": "S1"}]}',
              settings.claude_sonnet_deployment),
        reply('{"recommendation": "approve", "rationale": "All checks pass.", "conditions": []}',
              settings.claude_sonnet_deployment),
    ])
    agents.review(RESULT)

    root = finished_named(spans, "invoke_agent underwriting_review")
    assert root.attributes["gen_ai.agent.name"] == "underwriting_review"
    assert root.attributes["idp.tenant_id"] == "tenant-a"
    assert root.attributes["idp.correlation_id"] == "corr-1"
    assert root.attributes["idp.policy_source"] == "inline"
    assert root.attributes["idp.recommendation"] == "approve"

    roles = {name: finished_named(spans, f"invoke_agent {name}") for name in ("intake", "compliance", "underwriter")}
    for role in roles.values():
        assert role.parent.span_id == root.context.span_id
        assert role.attributes["idp.tenant_id"] == "tenant-a"
    assert roles["intake"].attributes["gen_ai.request.model"] == settings.claude_haiku_deployment
    assert roles["compliance"].attributes["gen_ai.request.model"] == settings.claude_sonnet_deployment

    chats = chat_spans(spans)
    assert len(chats) == 3
    role_ids = {role.context.span_id for role in roles.values()}
    assert all(chat.parent.span_id in role_ids for chat in chats)
    assert all(chat.attributes["gen_ai.usage.input_tokens"] == 120 for chat in chats)


def test_packet_question_records_model_usage_and_passage_source(
    spans: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch
):
    agents = agents_with(monkeypatch, [reply("Gross pay is 5200 [S1].", settings.claude_sonnet_deployment, 300, 25)])
    agents.ask("What is the gross pay?", RESULT)

    ask = finished_named(spans, "invoke_agent packet_qa")
    (chat,) = chat_spans(spans)
    assert chat.parent.span_id == ask.context.span_id
    assert chat.attributes["gen_ai.provider.name"] == "anthropic"
    assert chat.attributes["gen_ai.request.max_tokens"] == 600
    assert chat.attributes["gen_ai.response.model"] == settings.claude_sonnet_deployment
    assert chat.attributes["gen_ai.usage.input_tokens"] == 300
    assert chat.attributes["gen_ai.usage.output_tokens"] == 25
    assert chat.attributes["gen_ai.response.finish_reasons"] == ("end_turn",)
    assert chat.attributes["idp.attempts"] == 1
    assert ask.attributes["idp.tenant_id"] == "tenant-a"
    assert ask.attributes["idp.policy_source"] == "inline"


def test_rate_limited_calls_record_attempts_and_succeed(spans: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch):
    use_replies(monkeypatch, [
        FakeResponse(429, text="slow down"),
        FakeResponse(429, text="slow down"),
        reply("ok", settings.claude_sonnet_deployment),
    ])
    client = ClaudeClient(BASE_URL, FakeCredential())
    assert client.complete(settings.claude_sonnet_deployment, "system", "user", max_tokens=50) == "ok"

    (chat,) = chat_spans(spans)
    assert chat.attributes["idp.attempts"] == 3
    assert chat.status.status_code == StatusCode.UNSET


def test_failed_model_call_marks_chat_and_agent_spans_as_errors(
    spans: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch
):
    agents = agents_with(monkeypatch, [FakeResponse(500, text="upstream unavailable")])
    with pytest.raises(ClaudeError):
        agents.ask("What is the gross pay?", RESULT)

    ask = finished_named(spans, "invoke_agent packet_qa")
    (chat,) = chat_spans(spans)
    assert chat.status.status_code == StatusCode.ERROR
    assert ask.status.status_code == StatusCode.ERROR
    assert chat.attributes["idp.attempts"] == 1


def test_telemetry_never_carries_packet_question_answer_or_token(
    spans: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch
):
    private = {
        "tenantId": "tenant-a",
        "segments": [{
            "doc_type": "paystub",
            "page_range": [1, 1],
            "documents": [{"fields": {"Employer": {"value": "Contoso Private Employer", "confidence": 0.9}}}],
        }],
    }
    agents = agents_with(
        monkeypatch, [reply("Works at Contoso Private Employer [S1].", settings.claude_sonnet_deployment)]
    )
    agents.ask("Where does the applicant work? PRIVATE-QUESTION", private)

    recorded: list[str] = []
    for span in spans.get_finished_spans():
        recorded.append(span.name)
        recorded.extend(str(value) for value in span.attributes.values())
        for event in span.events:
            recorded.extend(str(value) for value in event.attributes.values())
    text = "\n".join(recorded)
    for secret in ("Contoso Private Employer", "PRIVATE-QUESTION", TOKEN):
        assert secret not in text


def test_library_question_records_passage_count_and_tenant(spans: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch):
    agents = agents_with(monkeypatch, [reply("Passage P1 applies.", settings.claude_sonnet_deployment)])
    passages = [{"title": "policy.pdf", "content": "Rule one."}, {"title": "guide.pdf", "content": "Rule two."}]
    agents.ask_library("What is the rule?", passages, tenant_id="tenant-a")

    library = finished_named(spans, "invoke_agent library_qa")
    assert library.attributes["idp.passage_count"] == 2
    assert library.attributes["idp.tenant_id"] == "tenant-a"


def test_annotate_current_span_sets_attributes_on_the_active_span(spans: InMemorySpanExporter):
    with telemetry._tracer.start_as_current_span("POST /api/documents/doc-1/review"):
        telemetry.annotate_current_span({"idp.tenant_id": "tenant-a", "idp.document_id": "doc-1"})

    request = finished_named(spans, "POST /api/documents/doc-1/review")
    assert request.attributes["idp.document_id"] == "doc-1"


def test_fastapi_app_is_instrumented_for_request_spans():
    assert getattr(api_app, "_is_instrumented_by_opentelemetry", False) is True
