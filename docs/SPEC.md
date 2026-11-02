# IDP Demo — Technical Specification

> **Historical.** This spec predates the document library, the Content Understanding router, and the Foundry IQ knowledge base. It also describes the retired classifier mode. For the current architecture, see the [README](../README.md).

## 1. App name
**IDP Demo** — *Intelligent Document Processing for Loan Application Review*

Container App: `ca-api-demo`
Endpoint: `https://ca-api-demo.<env-suffix>.eastus.azurecontainerapps.io`

---

## 2. Description

A FastAPI service that ingests **merged loan-application PDFs** (paystub + bank statement + W-2, optionally passport / driver's license) and returns structured, per-document extraction results suitable for downstream loan-origination workflows.

The service demonstrates an end-to-end **Intelligent Document Processing (IDP)** pipeline on Azure:

- **Splitting** — detects document boundaries inside a single merged PDF
- **Routing** — sends each segment to the most appropriate prebuilt extraction model
- **Extraction** — pulls structured fields and confidence scores
- **Per-tenant cost telemetry** — every Document Intelligence call emits a structured event tagged with a tenant identifier so a SaaS operator can attribute spend per customer
- **Compare mode** — runs the same input through all three strategies: fast heuristic splitter, trained DI custom classifier, and Content Understanding router analyzer, showing fields, latency, billed pages, and estimated cost side by side

It is designed as a deployable reference for teams evaluating Azure AI Document Intelligence in a multi-tenant SaaS context.

### Primary use cases
- Structured data extraction from common income/identity documents
- Document classification + routing of merged PDFs
- Multi-document boundary detection inside a single PDF
- Per-tenant usage and cost attribution for chargeback / billing

### Non-goals
- Production-grade authentication / authorization (the demo trusts an `x-tenant-id` header)
- Downstream loan-decisioning / underwriting logic
- Storage of extracted data — results are returned in the HTTP response only

---

## 3. Architecture

### 3.1 Logical flow

```
┌──────────┐  POST /process              ┌─────────────────────────┐
│ Customer │ ──────────────────────────▶ │  FastAPI (ca-api-demo)  │
│  (UI or  │   x-tenant-id: <id>          │                         │
│   API)   │   multipart PDF              │  1. read PDF            │
└──────────┘                              │  2. split (strategy)    │
                                          │     ├─ heuristic, OR    │
                                          │     ├─ DI classifier, OR│
                                          │     └─ CU router        │
                                          │  3. route segments (H/C)│
                                          │  4. extract per segment │
                                          │     (or single CU call) │
                                          │  5. emit telemetry      │
                                          │  6. return JSON         │
                                          └────────────┬────────────┘
                                                       │
                                ┌──────────────────────┼──────────────────────┐
                                ▼                      ▼                      ▼
                       ┌────────────────┐    ┌──────────────────┐   ┌─────────────────┐
                       │ Azure AI       │    │ Application      │   │ User-Assigned   │
                       │ Services       │    │ Insights         │   │ Managed Identity│
                       │ — DI prebuilt, │    │ — di.pages.      │   │ — Cog Svcs User │
                       │   DI custom,   │    │   processed      │   │ — AcrPull       │
                       │ — CU router    │    │ — cu.call.       │   └─────────────────┘
                       └───────┬────────┘    │   processed      │
                               │ classifier            │
                               │ training data         ▼
                               ▼              Log Analytics workspace
                       Azure Storage          (App Insights backing store)
                       (training PDFs +
                        future uploads)
```

### 3.2 Billing models by strategy

**Heuristic and Classifier strategies** use a two-stage model where each document goes through Document Intelligence **twice** for traceable per-segment extraction:

| Stage | Heuristic mode | Classifier mode | Content Understanding mode |
|---|---|---|---|
| **Stage 1 — split** | One call to `prebuilt-layout` over the full PDF (used for keyword-based boundary detection) | One call to the trained custom classifier (`idp-loan-docs-v1`) over the full PDF | N/A (single-call model) |
| **Stage 2 — extract** | One call per detected segment, model chosen by document type | Same — one call per segment, model chosen by classifier label | Single router analyzer call handles split + classify + extract |

For Heuristic/Classifier: Billed pages = `total_pages (stage 1) + sum(segment_pages) (stage 2)`.

**Content Understanding strategy** uses a single-call model where the router analyzer performs split, classification, and extraction in one operation. Billing is based on total pages processed once, with no separate stage-1 and stage-2 calls. The router analyzer (typically `router-classifier-extract-{version}`) is deployed via [scripts/deploy_cu_classifier.py](../scripts/deploy_cu_classifier.py).

Compare mode runs all three strategies and reports cost/latency differences so operators can quantify the trade-offs between implementation complexity, accuracy, and cost.

### 3.3 Deployed Azure resources (`infra/main.bicep`)

| Resource | Purpose | Notes |
|---|---|---|
| **Container App** `ca-api-demo` | Runs the FastAPI image | Min 1 replica, ingress public, MI-pull from ACR |
| **Container App Environment** | Hosts the app | Workload profile *Consumption* |
| **Azure Container Registry** `acrdemo…` | Stores the API image | `azd deploy` builds & pushes |
| **Azure AI Services account** `ai-demo-…` (kind `AIServices`, SKU `S0`) | Document Intelligence APIs | `disableLocalAuth: true`, custom subdomain enabled, public network on |
| **User-Assigned Managed Identity** `id-demo-…` | App ↔ Azure auth | Roles: `Cognitive Services User` on the AI account, `AcrPull` on the registry |
| **Storage Account** `stdemo…` | Classifier training data; future PDF uploads | `allowSharedKeyAccess: false` — Managed Identity only |
| **Application Insights** `appi-demo-…` | Per-call usage / cost / latency telemetry | Workspace-based |
| **Log Analytics Workspace** `log-demo-…` | Backing store for App Insights + (recommended) AI account diagnostics | 30-day default retention |

### 3.4 Authentication & secrets

- **No keys in config** — `disableLocalAuth: true` on the AI account.
- The Container App receives `AZURE_CLIENT_ID` (the UAMI client id) and `DI_ENDPOINT` from Bicep.
- `app/di_client.py` constructs `DefaultAzureCredential()`; in-cluster this resolves to the UAMI.
- `APPLICATIONINSIGHTS_CONNECTION_STRING` is injected from `appi.properties.ConnectionString`.
- `CLASSIFIER_ID` env var enables the classifier path; absence falls back to heuristic-only.

### 3.5 Telemetry contract

Every DI call emits an App Insights `traces` row (`message == "di.pages.processed"`) with `customDimensions`:

| Field | Description |
|---|---|
| `tenantId` | from `x-tenant-id` request header (defaults to `unknown`) |
| `correlationId` | UUID per HTTP request — joins stage-1 + stage-2 calls |
| `splitStrategy` | `heuristic` \| `classifier` |
| `stage` | `split` (stage 1) or empty (stage 2 segment) |
| `model` | DI model id (`prebuilt-layout`, `prebuilt-tax.us.w2`, `prebuilt-idDocument`, `idp-loan-docs-v1`, …) |
| `docType` | segment classification (`paystub`, `bank_statement`, `w2`, …) |
| `pageStart` / `pageEnd` | segment page range |
| `pageCount` | pages billed for that single call |
| `estimatedCostUsd` | `pages/1000 × app/pricing.py` rate |
| `durationMs` | DI call wall-time |

Reference KQL lives in [`loadtest/cost-allocation.kql`](../loadtest/cost-allocation.kql).

---

## 4. Folder structure

```
idp-app/
├── app/                              # FastAPI service
│   ├── __init__.py
│   ├── main.py                       # FastAPI app, /process, /healthz, embedded SPA UI
│   ├── config.py                     # env-var resolution
│   ├── di_client.py                  # DI SDK wrapper, MI auth, analyze() / classify()
│   ├── cu_client.py                  # Content Understanding REST client
│   ├── splitter.py                   # heuristic boundary detection + MODEL_BY_TYPE
│   ├── splitter_classifier.py        # DI custom-classifier split path
│   ├── derived.py                    # derived/aggregated fields (e.g. annualized income)
│   ├── pricing.py                    # DI list-price table, estimate_cost_usd()
│   ├── telemetry.py                  # App Insights wiring + emit_pages_processed()
│   └── sample/
│       └── generate_sample_pdf.py    # builds samples/loan_application_demo.pdf
│
├── infra/                            # Bicep IaC (subscription-scoped via azd)
│   ├── main.bicep                    # entry point, RG creation, module call
│   ├── main.parameters.json          # azd-resolved params
│   └── modules/
│       └── resources.bicep           # all RG-scoped resources + role assignments
│
├── scripts/
│   └── train_classifier.ps1          # trains + deploys idp-loan-docs-v1 from samples/training
│
├── samples/
│   ├── loan_application_demo.pdf     # 7-page merged demo PDF
│   └── training/                     # 5 doc types × 8 PDFs for classifier training
│
├── loadtest/                         # Azure Load Testing scenario + KQL
│   ├── loadtest.jmx
│   ├── loadtest-config.yaml
│   ├── tenants.csv
│   ├── cost-allocation.kql           # per-tenant cost rollup (mirrors pricing.py)
│   └── README.md
│
├── docs/
│   ├── SPEC.md                       # this file
│   └── custom-classifier.md          # classifier training walkthrough
│
├── .azure/                           # azd environment state (gitignored values)
├── azure.yaml                        # azd service mapping (api → app/)
├── Dockerfile                        # Python 3.12 slim, uvicorn entrypoint
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

---

## 5. API surface

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/`         | Single-page UI (upload, KPI cards, side-by-side compare, fields tables, raw JSON) |
| `GET`  | `/healthz`  | Liveness — `{ status: "ok", classifierConfigured: bool }` |
| `POST` | `/process`  | Multipart upload `file`; query `mode = heuristic | classifier | compare`; header `x-tenant-id` |

Response shape (abbreviated):

```jsonc
{
  "correlationId": "…",
  "tenantId": "acme-corp",
  "strategy": "classifier",
  "totalPages": 7,
  "billedPages": 14,
  "estimatedCostUsd": 0.091,
  "segments": [
    { "docType": "paystub", "pageStart": 1, "pageEnd": 2, "model": "prebuilt-layout",
      "pageCount": 2, "estimatedCostUsd": 0.020, "durationMs": 4231,
      "fields": [ { "name": "GrossPay", "value": "…", "confidence": 0.96 }, … ] },
    …
  ],
  "compare": { "heuristic": { … }, "classifier": { … }, "savingsUsd": 0.049, "savingsPct": 35.0 }
}
```

---

## 6. Pricing table (`app/pricing.py`)

DI S0 list price, USD per 1,000 pages — kept deliberately simple and explicit.

| Model id | $ / 1k pages |
|---|---|
| `prebuilt-read` | 1.50 |
| `prebuilt-layout` | 10.00 |
| `prebuilt-idDocument` | 10.00 |
| `prebuilt-tax.us.w2` / `1098` / `1099` | 10.00 |
| `custom` | 30.00 |
| `classifier` | 3.00 |

`loadtest/cost-allocation.kql` mirrors the same table — they must be updated together.

Caveats documented in `pricing.py` docstring:
- List price only; commitment / batch / regional discounts not modeled
- Free tier not modeled
- Add-on capabilities (Query Fields, Generative) not modeled

---

## 7. Local development

```powershell
python -m venv .venv
. .venv/Scripts/Activate.ps1
pip install -r requirements.txt
python -m app.sample.generate_sample_pdf

$env:DI_ENDPOINT = "https://ai-demo-….cognitiveservices.azure.com/"
# leave DI_KEY unset to use az login (DefaultAzureCredential)
$env:TENANT_ID_HEADER = "x-tenant-id"
$env:CLASSIFIER_ID    = "idp-loan-docs-v1"

uvicorn app.main:app --reload --port 8000
```

## 8. Deploy

```powershell
az login
az account set --subscription <sub-id>
azd up                      # full stack
azd deploy api              # code-only
```

Classifier is trained out-of-band via `scripts/train_classifier.ps1` after the AI account and storage exist.

---

## 9. Operational considerations

- **Telemetry ingestion lag:** ~30–90 seconds App Insights, 8–24h Cost Management.
- **Reconciliation:** App-emitted `estimatedCostUsd` should be reconciled monthly against the Cost Management invoice for the AI account. Drift > a few % indicates traffic outside the instrumented path (training scripts, retries, another consumer on the same account).
- **Retention:** Log Analytics defaults to 30 days. For billing-grade audit, configure Log Analytics *Data Export* on the `AppTraces` table to a Storage container for long-term immutable archive.
- **Diagnostic settings:** Recommended to enable `AllMetrics` + `RequestResponse` on the AI account → ship to the same LAW for platform-side reconciliation (`AzureMetrics.TotalCalls`).
- **Multi-tenant isolation model:** This deployment uses a *shared* AI account across tenants. Cost Management cannot break this down per tenant — that is the sole job of `tenantId` in the App Insights telemetry. For stronger isolation, a per-tenant AI account model is possible at higher cost floor.

---

## 10. Roadmap / known optimizations (not implemented)

| Level | Idea | Expected impact |
|---|---|---|
| 1 | Reuse the stage-1 `prebuilt-layout` result for stage-2 layout-routed segments | ~36 % cost reduction in heuristic mode on the demo PDF |
| 2 | Use `prebuilt-read` ($1.50/1k) instead of `prebuilt-layout` for stage-1 split | Cheaper splitter when layout structure is not needed |
| 3 | `asyncio.gather()` per-segment extraction calls | Latency reduction, no cost change |
| 4 | Long-term archive of `AppTraces` to Storage via LAW Data Export | Billing audit retention |
| 5 | AI account diagnostic settings → LAW for reconciliation | Detect drift between app telemetry and platform metrics |
| 6 | Workbook + Azure Dashboard pinning the per-tenant cost / latency / drift charts | Single pane of glass for ops |

---

## 11. Tags

All resources are tagged for cost allocation & inventory:

| Tag | Value |
|---|---|
| `app` | `idp-demo` |
| `costcenter` | `loan-ops` |
| `env` | `demo` |
