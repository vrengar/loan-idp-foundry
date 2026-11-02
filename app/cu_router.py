"""Content Understanding router analyzer for the `cu` split strategy.

The app creates the model defaults and the analyzer at startup with its managed identity,
so the router exists in every environment, including private ones, without a manual step.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

import requests

from .config import settings

log = logging.getLogger("idp.cu_router")

CU_API_VERSION = "2025-11-01"
DEFAULT_ANALYZER_ID = "loan_docs_router"
PROVISION_TIMEOUT_S = 300

_state: dict[str, str] = {"status": "pending", "detail": ""}
_lock = threading.Lock()


def router_definition(completion_model: str) -> dict[str, Any]:
    return {
        "baseAnalyzerId": "prebuilt-document",
        "description": (
            "Loan-application packet router. Classifies multi-doc PDFs into paystub, "
            "bank statement, W-2, passport, and driver's license, splits them into "
            "page-range segments, and routes each segment to the matching CU "
            "prebuilt extractor. One API call replaces the app-side splitter + dict."
        ),
        "models": {"completion": completion_model},
        "config": {
            "returnDetails": True,
            "enableSegment": True,
            "contentCategories": {
                "paystub": {
                    "description": (
                        "US pay stub or paycheck statement showing employer name, employee "
                        "name, pay period dates, gross/net pay, deductions, and YTD totals."
                    ),
                    "analyzerId": "prebuilt-payStub.us",
                },
                "bank_statement": {
                    "description": (
                        "Bank account statement listing the account holder, account number, "
                        "statement period, opening/closing balances, and a transactions table."
                    ),
                    "analyzerId": "prebuilt-bankStatement.us",
                },
                "w2": {
                    "description": (
                        "IRS Form W-2 'Wage and Tax Statement' showing employer EIN, "
                        "employee SSN, wages tips and other compensation, and federal/state "
                        "tax withheld for a tax year."
                    ),
                    "analyzerId": "prebuilt-tax.us.w2",
                },
                "passport": {
                    "description": (
                        "Passport identity / data page showing full name, date of birth, "
                        "passport number, issuing country, and machine-readable zone."
                    ),
                    "analyzerId": "prebuilt-idDocument.passport",
                },
                "drivers_license": {
                    "description": (
                        "US driver's license or state-issued ID card showing full name, "
                        "date of birth, address, license number, and issuing state."
                    ),
                    "analyzerId": "prebuilt-idDocument",
                },
                "other": {
                    "description": (
                        "Anything that is not clearly one of the loan document types above "
                        "(e.g., cover letters, blank separator pages, unknown forms)."
                    ),
                },
            },
        },
    }


def router_state() -> dict[str, str]:
    with _lock:
        return dict(_state)


def _set(status: str, detail: str = "") -> None:
    with _lock:
        _state.update(status=status, detail=detail)


def _headers(credential: Any) -> dict[str, str]:
    token = credential.get_token("https://cognitiveservices.azure.com/.default").token
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _wait_until_present(url: str, headers: dict[str, str]) -> None:
    deadline = time.time() + PROVISION_TIMEOUT_S
    while time.time() < deadline:
        if requests.get(url, headers=headers, timeout=60).status_code == 200:
            return
        time.sleep(3)
    raise RuntimeError("analyzer did not become available in time")


def ensure_router(
    endpoint: str,
    analyzer_id: str,
    credential: Any,
    completion_model: str,
    embedding_model: str,
) -> None:
    base = endpoint.rstrip("/")
    headers = _headers(credential)
    defaults = {
        "modelDeployments": {
            "prebuilt-analyzer-completion": completion_model,
            "prebuilt-analyzer-embedding": embedding_model,
            completion_model: completion_model,
            embedding_model: embedding_model,
        }
    }
    patched = requests.patch(
        f"{base}/contentunderstanding/defaults?api-version={CU_API_VERSION}",
        headers=headers,
        json=defaults,
        timeout=60,
    )
    patched.raise_for_status()

    url = f"{base}/contentunderstanding/analyzers/{analyzer_id}?api-version={CU_API_VERSION}"
    existing = requests.get(url, headers=headers, timeout=60)
    if existing.status_code == 200:
        return
    if existing.status_code != 404:
        existing.raise_for_status()

    created = requests.put(url, headers=headers, json=router_definition(completion_model), timeout=60)
    if created.status_code == 409:
        _wait_until_present(url, headers)
        return
    if created.status_code not in (200, 201, 202):
        raise RuntimeError(f"PUT analyzer failed: HTTP {created.status_code} {created.text[:300]}")

    operation = created.headers.get("Operation-Location") or created.headers.get("operation-location")
    if not operation:
        return
    deadline = time.time() + PROVISION_TIMEOUT_S
    while time.time() < deadline:
        poll = requests.get(operation, headers=_headers(credential), timeout=60)
        poll.raise_for_status()
        status = (poll.json().get("status") or "").lower()
        if status == "succeeded":
            return
        if status in ("failed", "cancelled", "canceled"):
            raise RuntimeError(f"analyzer provisioning {status}")
        time.sleep(3)
    raise RuntimeError("analyzer provisioning timed out")


def start_background_setup() -> None:
    endpoint = settings.cu_endpoint or settings.di_endpoint
    if not endpoint:
        _set("skipped", "CU_ENDPOINT or DI_ENDPOINT is not set")
        return

    def run() -> None:
        from azure.identity import DefaultAzureCredential

        _set("creating")
        try:
            ensure_router(
                endpoint,
                settings.cu_router_analyzer_id or DEFAULT_ANALYZER_ID,
                DefaultAzureCredential(),
                settings.cu_completion_deployment,
                settings.cu_embedding_deployment,
            )
            _set("ready", settings.cu_router_analyzer_id or DEFAULT_ANALYZER_ID)
        except Exception as exc:
            log.exception("CU router setup failed")
            _set("error", f"{type(exc).__name__}: {str(exc)[:300]}")

    threading.Thread(target=run, name="cu-router-setup", daemon=True).start()
