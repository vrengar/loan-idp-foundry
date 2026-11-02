import pytest

from app import cu_router
from app.config import settings


class FakeResponse:
    def __init__(self, status_code=200, json_body=None, headers=None, text=""):
        self.status_code = status_code
        self._json = json_body or {}
        self.headers = headers or {}
        self.text = text

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeRequests:
    def __init__(self, get_responses, put_response=None, patch_response=None):
        self.calls = []
        self._get = list(get_responses)
        self._put = put_response
        self._patch = patch_response or FakeResponse(200)

    def patch(self, url, headers=None, json=None, timeout=None):
        self.calls.append(("PATCH", url, json))
        return self._patch

    def get(self, url, headers=None, timeout=None):
        self.calls.append(("GET", url, None))
        return self._get.pop(0) if len(self._get) > 1 else self._get[0]

    def put(self, url, headers=None, json=None, timeout=None):
        self.calls.append(("PUT", url, json))
        return self._put


class FakeCredential:
    def get_token(self, scope):
        class Token:
            token = "test-token"

        return Token()


ENDPOINT = "https://foundry.example.cognitiveservices.azure.com/"


def test_router_definition_uses_given_completion_model():
    definition = cu_router.router_definition("gpt-4.1")
    assert definition["models"] == {"completion": "gpt-4.1"}
    categories = definition["config"]["contentCategories"]
    assert set(categories) == {"paystub", "bank_statement", "w2", "passport", "drivers_license", "other"}
    assert "analyzerId" not in categories["other"]


def test_ensure_router_creates_missing_analyzer(monkeypatch):
    fake = FakeRequests(
        get_responses=[FakeResponse(404), FakeResponse(200, {"status": "succeeded"})],
        put_response=FakeResponse(201, headers={"Operation-Location": "https://foundry.example/op/1"}),
    )
    monkeypatch.setattr(cu_router, "requests", fake)
    cu_router.ensure_router(ENDPOINT, "loan_docs_router", FakeCredential(), "gpt-4.1", "text-embedding-3-large")

    patch_body = next(c[2] for c in fake.calls if c[0] == "PATCH")
    assert patch_body["modelDeployments"]["prebuilt-analyzer-completion"] == "gpt-4.1"
    assert patch_body["modelDeployments"]["gpt-4.1"] == "gpt-4.1"
    put_body = next(c[2] for c in fake.calls if c[0] == "PUT")
    assert put_body["models"] == {"completion": "gpt-4.1"}


def test_ensure_router_skips_when_analyzer_exists(monkeypatch):
    fake = FakeRequests(get_responses=[FakeResponse(200, {"analyzerId": "loan_docs_router"})])
    monkeypatch.setattr(cu_router, "requests", fake)
    cu_router.ensure_router(ENDPOINT, "loan_docs_router", FakeCredential(), "gpt-4.1", "text-embedding-3-large")
    assert not any(c[0] == "PUT" for c in fake.calls)


def test_ensure_router_waits_when_another_replica_created_it(monkeypatch):
    fake = FakeRequests(
        get_responses=[FakeResponse(404), FakeResponse(200)],
        put_response=FakeResponse(409),
    )
    monkeypatch.setattr(cu_router, "requests", fake)
    cu_router.ensure_router(ENDPOINT, "loan_docs_router", FakeCredential(), "gpt-4.1", "text-embedding-3-large")


def test_ensure_router_raises_when_provisioning_fails(monkeypatch):
    fake = FakeRequests(
        get_responses=[FakeResponse(404), FakeResponse(200, {"status": "failed"})],
        put_response=FakeResponse(201, headers={"Operation-Location": "https://foundry.example/op/2"}),
    )
    monkeypatch.setattr(cu_router, "requests", fake)
    with pytest.raises(RuntimeError, match="provisioning failed"):
        cu_router.ensure_router(ENDPOINT, "loan_docs_router", FakeCredential(), "gpt-4.1", "text-embedding-3-large")


def test_background_setup_is_skipped_without_endpoint(monkeypatch):
    monkeypatch.setattr(settings, "cu_endpoint", "")
    monkeypatch.setattr(settings, "di_endpoint", "")
    cu_router.start_background_setup()
    assert cu_router.router_state()["status"] == "skipped"
