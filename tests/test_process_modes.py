from fastapi.testclient import TestClient

import app.main as main

PDF = b"%PDF-1.7\n" + b"x" * 64


def fake_pipeline(strategy, di, pdf_bytes, tenant_id, filename):
    heuristic = strategy == "heuristic"
    return {
        "splitStrategy": strategy,
        "totalDurationMs": 10.0 if heuristic else 20.0,
        "billing": {
            "billedPages": 4,
            "totalCostEstimateUsd": 0.04 if heuristic else 0.02,
        },
        "segments": [{"doc_type": "paystub", "page_range": [1, 2]}],
    }


def make_client(monkeypatch):
    monkeypatch.setattr(main, "_run_pipeline", fake_pipeline)
    monkeypatch.setattr(main, "_get_di", lambda: object())
    return TestClient(main.app)


def post(client, mode):
    return client.post(
        f"/process?mode={mode}",
        files={"file": ("packet.pdf", PDF, "application/pdf")},
    )


def test_compare_runs_heuristic_and_cu_only(monkeypatch):
    client = make_client(monkeypatch)
    response = post(client, "compare")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"tenantId", "filename", "comparison", "heuristic", "cu"}
    assert body["comparison"]["totalCostUsd"]["cuVsHeuristic"]["savingsUsd"] == 0.02
    assert set(body["comparison"]["segmentCount"]) == {"heuristic", "cu"}


def test_single_cu_mode_returns_pipeline_output(monkeypatch):
    client = make_client(monkeypatch)
    response = post(client, "cu")
    assert response.status_code == 200
    assert response.json()["splitStrategy"] == "cu"


def test_classifier_mode_is_retired(monkeypatch):
    client = make_client(monkeypatch)
    response = post(client, "classifier")
    assert response.status_code == 400
    assert "retired" in response.json()["detail"]


def test_unknown_mode_is_rejected(monkeypatch):
    client = make_client(monkeypatch)
    assert post(client, "bogus").status_code == 400


def test_healthz_no_longer_reports_classifier(monkeypatch):
    client = make_client(monkeypatch)
    assert client.get("/healthz").json() == {"status": "ok"}
