import io

from fastapi.testclient import TestClient
from pypdf import PdfWriter

import app.main as main


def make_pdf(pages):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


PAGE_TEXTS = [
    "Pay stub employer pay period gross",
    "Pay stub employer pay period gross",
    "Bank statement account balance",
]


class FakeDI:
    def __init__(self, page_texts):
        self.page_texts = page_texts

    def analyze(self, *, model_id, content):
        return {"modelId": model_id}, 5.0

    def page_text(self, result):
        return list(self.page_texts)

    def summarize_fields(self, result):
        return []


def test_heuristic_pipeline_runs_end_to_end_with_billing_fields():
    result = main._run_pipeline("heuristic", FakeDI(PAGE_TEXTS), make_pdf(3), "tenant-a", "packet.pdf")
    assert result["totalPages"] == 3
    assert result["billing"]["splitPassPricingKey"] == "prebuilt-layout"
    assert result["billing"]["totalCostEstimateUsd"] > 0
    assert result["segments"]


def test_compare_route_runs_the_real_heuristic_pipeline(monkeypatch):
    def fake_cu(di, pdf_bytes, tenant_id, filename):
        return {
            "splitStrategy": "cu",
            "tenantId": tenant_id,
            "filename": filename,
            "totalPages": 3,
            "totalDurationMs": 20.0,
            "billing": {"billedPages": 3, "totalCostEstimateUsd": 0.03},
            "segments": [{"doc_type": "paystub", "page_range": [1, 2]}],
        }

    monkeypatch.setattr(main, "_run_cu_pipeline", fake_cu)
    monkeypatch.setattr(main, "_get_di", lambda: FakeDI(PAGE_TEXTS))
    client = TestClient(main.app)
    response = client.post("/process?mode=compare",
                           files={"file": ("packet.pdf", make_pdf(3), "application/pdf")})
    assert response.status_code == 200
    body = response.json()
    assert body["heuristic"]["splitStrategy"] == "heuristic"
    assert body["comparison"]["segmentCount"]["cu"] == 1
    assert body["comparison"]["totalCostUsd"]["cuVsHeuristic"]["savingsUsd"] is not None
