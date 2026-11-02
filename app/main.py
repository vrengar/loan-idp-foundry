"""FastAPI app: upload a merged loan-application PDF, get per-document extraction."""
from __future__ import annotations

import io
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from pypdf import PdfReader, PdfWriter

from .config import settings
from .cu_client import CUClient
from .di_client import DIClient
from .pricing import estimate_cost_usd
from .splitter import classify_page, segments_from_page_types
from .telemetry import (
    configure as configure_telemetry,
    emit_cu_call_processed,
    emit_pages_processed,
)
from .library import get_library_service
from .library_api import create_library_router
from .cu_router import start_background_setup as start_cu_router_setup
from .agents import get_loan_agents
from .knowledge import get_knowledge, start_background_setup as start_knowledge_setup
from .policy import UNDERWRITING_RULES

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("idp.api")

# Wire OTel -> App Insights at import time so even startup logs are captured.
configure_telemetry()


@asynccontextmanager
async def _lifespan(_: FastAPI):
    start_cu_router_setup()
    start_knowledge_setup(UNDERWRITING_RULES)
    yield


app = FastAPI(title="IDP Demo — Loan Application Review", lifespan=_lifespan)
FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,static")

# Two strategies are supported on /process. "compare" runs both (heuristic, cu)
# back-to-back for side-by-side cost/latency. The DI custom classifier was retired.
SplitStrategy = Literal["heuristic", "cu"]

# CU prebuilt analyzer routing.
#
# As of the CU "router" pipeline (see _run_cu_pipeline below), this dict is
# NO LONGER used at request time -- the equivalent mapping lives inside the
# deployed CU classifier analyzer (`contentCategories[*].analyzerId`, see
# scripts/deploy_cu_classifier.py). The dict is retained, commented, as
# reference and as a fallback if you re-enable the legacy app-side dispatch
# pipeline (`_run_cu_pipeline_legacy`).
#
# CU_ANALYZER_BY_TYPE: dict[str, str] = {
#     "paystub":         "prebuilt-payStub.us",
#     "bank_statement":  "prebuilt-bankStatement.us",
#     "w2":              "prebuilt-tax.us.w2",
#     "drivers_license": "prebuilt-idDocument",
#     "passport":        "prebuilt-idDocument.passport",
#     "unknown":         "prebuilt-layout",
# }


@app.get("/legacy", response_class=HTMLResponse)
def index() -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>IDP Demo — Loan Application Review</title>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<style>
  :root {{
    --bg:#f6f7fb; --card:#fff; --ink:#1a1f2c; --muted:#6b7280; --line:#e6e8ef;
    --brand:#0b5fff; --brand-soft:#e8efff;
    --good:#0a8a4f; --good-soft:#e6f6ee;
    --warn:#a35a00; --warn-soft:#fff3df;
    --bad:#b3261e;  --bad-soft:#fde7e6;
    --chip:#eef0f5;
  }}
  *{{box-sizing:border-box}}
  body{{margin:0;background:var(--bg);color:var(--ink);
       font:14px/1.5 -apple-system,Segoe UI,Roboto,Inter,sans-serif;}}
  header{{padding:18px 24px;background:var(--card);border-bottom:1px solid var(--line);
         display:flex;align-items:center;gap:14px;}}
  header h1{{margin:0;font-size:18px}}
  header .meta{{margin-left:auto;color:var(--muted);font-size:12px}}
  .dot{{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;vertical-align:middle}}
  .dot.ok{{background:var(--good)}} .dot.muted{{background:#bbb}}
  main{{max-width:1180px;margin:0 auto;padding:24px}}
  .card{{background:var(--card);border:1px solid var(--line);border-radius:12px;
        padding:18px 20px;margin-bottom:18px;box-shadow:0 1px 2px rgba(20,30,60,.04)}}
  .card h2{{margin:0 0 12px;font-size:15px;letter-spacing:.02em;text-transform:uppercase;color:var(--muted)}}
  .row{{display:flex;flex-wrap:wrap;gap:14px;align-items:center}}
  label.field{{display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--muted)}}
  label.field input,label.field select{{padding:8px 10px;border:1px solid var(--line);border-radius:8px;
    font-size:14px;background:#fff;color:var(--ink);min-width:200px}}
  .modes{{display:flex;gap:8px;flex-wrap:wrap}}
  .modes label{{padding:8px 12px;border:1px solid var(--line);border-radius:999px;cursor:pointer;
    font-size:13px;background:#fff;display:inline-flex;align-items:center;gap:6px}}
  .modes input{{accent-color:var(--brand)}}
  .modes label.sel{{background:var(--brand-soft);border-color:var(--brand);color:var(--brand);font-weight:600}}
  .file-wrap{{display:flex;gap:8px;align-items:center}}
  .btn{{background:var(--brand);color:#fff;border:0;padding:10px 18px;border-radius:8px;
       font-weight:600;cursor:pointer;font-size:14px}}
  .btn:disabled{{opacity:.5;cursor:not-allowed}}
  .btn.ghost{{background:#fff;color:var(--ink);border:1px solid var(--line)}}
  .grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
  .grid.cols-3{{grid-template-columns:repeat(3,1fr)}}
  @media (max-width:1100px){{.grid.cols-3{{grid-template-columns:1fr}}}}
  @media (max-width:900px){{.grid{{grid-template-columns:1fr}}}}
  .kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}
  @media (max-width:700px){{.kpis{{grid-template-columns:repeat(2,1fr)}}}}
  .kpi{{background:#fafbff;border:1px solid var(--line);border-radius:10px;padding:12px 14px}}
  .kpi .lbl{{font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}}
  .kpi .val{{font-size:22px;font-weight:700;margin-top:4px}}
  .kpi .delta{{font-size:12px;margin-top:2px}}
  .delta.good{{color:var(--good)}} .delta.bad{{color:var(--bad)}}
  table{{width:100%;border-collapse:collapse;font-size:13px}}
  th,td{{padding:8px 10px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}}
  th{{font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);font-weight:600}}
  tbody tr:hover{{background:#fafbff}}
  .chip{{display:inline-block;padding:2px 8px;border-radius:999px;background:var(--chip);
        font-size:12px;margin-right:4px;margin-bottom:4px}}
  .chip.t-paystub{{background:#e8efff;color:#0b3aab}}
  .chip.t-bank_statement{{background:#e6f6ee;color:#0a6a3f}}
  .chip.t-w2{{background:#fff3df;color:#7a4400}}
  .chip.t-passport{{background:#f1e6fd;color:#5a259c}}
  .chip.t-drivers_license{{background:#fde7e6;color:#a02019}}
  .chip.t-unknown{{background:#eee;color:#555}}
  .pill{{display:inline-block;padding:1px 8px;border-radius:6px;font-size:11px;font-weight:600}}
  .pill.h{{background:var(--warn-soft);color:var(--warn)}}
  .pill.c{{background:var(--brand-soft);color:var(--brand)}}
  .pill.u{{background:#e6f6ee;color:#0a6a3f}}
  .conf{{display:inline-block;width:36px;text-align:right;font-variant-numeric:tabular-nums}}
  .conf.lo{{color:var(--bad)}} .conf.mid{{color:var(--warn)}} .conf.hi{{color:var(--good)}}
  .seg-list{{display:flex;flex-direction:column;gap:6px}}
  .seg-row{{display:flex;align-items:center;gap:8px;padding:6px 8px;border:1px solid var(--line);border-radius:8px;background:#fff}}
  .seg-row .pages{{color:var(--muted);font-size:12px;min-width:60px}}
  .empty{{color:var(--muted);font-size:13px;text-align:center;padding:30px}}
  details{{background:#fafbff;border:1px solid var(--line);border-radius:8px;padding:8px 12px}}
  details summary{{cursor:pointer;font-size:12px;color:var(--muted)}}
  pre{{margin:8px 0 0;font-size:12px;background:#0e1424;color:#dfe5ff;padding:12px;border-radius:8px;
       overflow:auto;max-height:380px}}
  .spinner{{display:inline-block;width:16px;height:16px;border:2px solid var(--line);border-top-color:var(--brand);
           border-radius:50%;animation:spin 1s linear infinite;vertical-align:middle;margin-right:8px}}
  @keyframes spin{{to{{transform:rotate(360deg)}}}}
  .err{{background:var(--bad-soft);color:var(--bad);padding:12px;border-radius:8px;border:1px solid #f3c4c1}}
  .strategy-h{{border-left:3px solid var(--warn)}}
  .strategy-u{{border-left:3px solid #0a6a3f}}
</style>
</head>
<body>
<header>
  <h1>IDP Demo — Loan Application Review</h1>
  <span class="meta"><a href="/healthz">/healthz</a> · <a href="/docs">/docs</a></span>
</header>
<main>
  <section class="card">
    <h2>Process a loan PDF</h2>
    <form id="f" class="row">
      <label class="field">Tenant ID
        <input name="tenantId" value="demo-tenant"/>
      </label>
      <div class="modes" role="radiogroup">
        <label class="sel"><input type="radio" name="mode" value="heuristic" checked/> Heuristic (DI)</label>
        <label><input type="radio" name="mode" value="cu"/> Content Understanding</label>
        <label><input type="radio" name="mode" value="compare"/> Compare heuristic vs CU</label>
      </div>
      <label class="field" style="flex:1">PDF file
        <input type="file" name="file" accept="application/pdf" required/>
      </label>
      <button class="btn" id="go" type="submit">Process</button>
    </form>
  </section>

  <div id="status"></div>
  <div id="results"></div>
</main>

<script>
const $ = (s, r=document) => r.querySelector(s);
const $$ = (s, r=document) => Array.from(r.querySelectorAll(s));

// Highlight selected mode pill
$$('.modes input').forEach(r => r.addEventListener('change', () => {{
  $$('.modes label').forEach(l => l.classList.toggle('sel', l.querySelector('input').checked));
}}));

const fmtMs = ms => ms < 1000 ? `${{Math.round(ms)}} ms` : `${{(ms/1000).toFixed(1)}} s`;
const fmtUsd = v => `$${{(v ?? 0).toFixed(3)}}`;
const confClass = c => c == null ? '' : (c < 0.6 ? 'lo' : c < 0.85 ? 'mid' : 'hi');
const escape = s => String(s ?? '').replace(/[&<>"']/g, m => (
  {{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[m]));

function chip(t) {{
  return `<span class="chip t-${{escape(t)}}">${{escape(t)}}</span>`;
}}

function segmentsTable(segments) {{
  if (!segments?.length) return '<div class="empty">No segments</div>';
  const rows = segments.map(s => `
    <tr>
      <td>${{chip(s.doc_type)}}</td>
      <td><code>${{escape(s.model_id)}}</code></td>
      <td>p${{s.page_range[0]}}${{s.page_range[1]!==s.page_range[0]?'–'+s.page_range[1]:''}}
        <span style="color:var(--muted)">(${{s.pages}}p)</span></td>
      <td>${{fmtMs(s.duration_ms)}}</td>
      <td>${{fmtUsd(s.cost_estimate_usd)}}</td>
      <td>${{s.documents?.length ?? 0}}</td>
    </tr>`).join('');
  return `<table>
    <thead><tr><th>Doc type</th><th>Model</th><th>Pages</th>
      <th>Duration</th><th>Cost</th><th>Fields</th></tr></thead>
    <tbody>${{rows}}</tbody></table>`;
}}

function fieldsBlock(segments) {{
  const docs = segments.flatMap(s => (s.documents ?? []).map(d => ({{...d, _seg: s.doc_type}})));
  if (!docs.length) return '';
  return docs.map(d => {{
    const fields = Object.entries(d.fields ?? {{}});
    if (!fields.length) return '';
    const rows = fields.map(([k, v]) => `
      <tr>
        <td><b>${{escape(k)}}</b></td>
        <td>${{escape(v.value ?? '—')}}</td>
        <td><span class="conf ${{confClass(v.confidence)}}">${{
          v.confidence != null ? v.confidence.toFixed(2) : '—'}}</span></td>
      </tr>`).join('');
    return `<div style="margin-top:14px">
      <div style="font-size:12px;color:var(--muted);margin-bottom:6px">
        Extracted from ${{chip(d._seg)}} <code>${{escape(d.doc_type)}}</code>
      </div>
      <table>
        <thead><tr><th>Field</th><th>Value</th><th>Conf</th></tr></thead>
        <tbody>${{rows}}</tbody></table></div>`;
  }}).join('');
}}

function singleResult(r) {{
  return `
    <section class="card">
      <h2>Run summary — ${{escape(r.splitStrategy)}}</h2>
      <div class="kpis">
        <div class="kpi"><div class="lbl">Total pages</div><div class="val">${{r.totalPages}}</div></div>
        <div class="kpi"><div class="lbl">Billed pages</div><div class="val">${{r.billing.billedPages}}</div></div>
        <div class="kpi"><div class="lbl">Duration</div><div class="val">${{fmtMs(r.totalDurationMs)}}</div></div>
        <div class="kpi"><div class="lbl">Cost (est)</div><div class="val">${{fmtUsd(r.billing.totalCostEstimateUsd)}}</div></div>
      </div>
    </section>
    <section class="card">
      <h2>Segments</h2>
      ${{segmentsTable(r.segments)}}
      ${{fieldsBlock(r.segments)}}
    </section>
    <section class="card">
      <h2>Models used</h2>
      <table>
        <thead><tr><th>Model</th><th>Calls</th><th>Pages</th></tr></thead>
        <tbody>${{r.modelsUsed.map(m => `
          <tr><td><code>${{escape(m.model_id)}}</code></td><td>${{m.calls}}</td><td>${{m.pages}}</td></tr>`).join('')}}
        </tbody>
      </table>
    </section>
    <details><summary>Raw JSON</summary><pre>${{escape(JSON.stringify(r, null, 2))}}</pre></details>`;
}}

function compareResult(d) {{
  const cmp = d.comparison, h = d.heuristic, u = d.cu;
  const col = (label, cls, run, baselineCost, baselineDur) => {{
    const dCost = baselineCost - run.billing.totalCostEstimateUsd;
    const dDur  = baselineDur  - run.totalDurationMs;
    const dCostPct = baselineCost ? (dCost / baselineCost * 100) : 0;
    const dDurPct  = baselineDur  ? (dDur  / baselineDur  * 100) : 0;
    const sign = v => v > 0 ? '−' : '+';
    const showDelta = baselineCost !== run.billing.totalCostEstimateUsd;
    return `
      <div class="kpi">
        <div class="lbl">${{label}}</div>
        <div class="val">${{fmtUsd(run.billing.totalCostEstimateUsd)}}</div>
        ${{showDelta ? `<div class="delta ${{dCost>0?'good':'bad'}}">${{sign(dCost)}}${{fmtUsd(Math.abs(dCost))}} (${{dCostPct.toFixed(1)}}%) vs heuristic</div>` : ''}}
        <div class="lbl" style="margin-top:8px">Duration</div>
        <div class="val" style="font-size:16px">${{fmtMs(run.totalDurationMs)}}</div>
        ${{showDelta ? `<div class="delta ${{dDur>0?'good':'bad'}}">${{sign(dDur)}}${{fmtMs(Math.abs(dDur))}} (${{dDurPct.toFixed(1)}}%) vs heuristic</div>` : ''}}
      </div>`;
  }};
  const segCol = (pillCls, pillLabel, stratCls, run) => `
    <div>
      <div style="margin-bottom:8px"><span class="pill ${{pillCls}}">${{pillLabel}}</span>
        <span style="color:var(--muted);font-size:12px;margin-left:6px">${{run.segments.length}} segments</span></div>
      <div class="seg-list">${{run.segments.map(s => `
        <div class="seg-row ${{stratCls}}">
          ${{chip(s.doc_type)}}
          <span class="pages">p${{s.page_range[0]}}${{s.page_range[1]!==s.page_range[0]?'–'+s.page_range[1]:''}}</span>
          <code style="font-size:11px;color:var(--muted)">${{escape(s.model_id)}}</code>
          <span style="margin-left:auto;color:var(--muted);font-size:12px">${{fmtUsd(s.cost_estimate_usd)}}</span>
        </div>`).join('')}}</div>
    </div>`;
  const fieldsCol = (pillCls, pillLabel, run) => `
    <div><div style="margin-bottom:6px"><span class="pill ${{pillCls}}">${{pillLabel}}</span></div>${{fieldsBlock(run.segments) || '<div class="empty">No structured fields</div>'}}</div>`;

  // Build a unified field-coverage matrix grouped by doc_type.
  // For each doc_type that appears in ANY strategy, list all fields
  // (union of names across H/C/CU) with one column per strategy showing
  // value + confidence, or ✗ if missing. This is the headline quality view.
  const matrixByType = (() => {{
    const collect = (run) => {{
      const out = {{}}; // doc_type -> {{ fieldName -> {{value, confidence}} }}
      for (const seg of (run.segments ?? [])) {{
        for (const doc of (seg.documents ?? [])) {{
          const t = doc.doc_type || seg.doc_type || 'unknown';
          out[t] ??= {{}};
          for (const [k, v] of Object.entries(doc.fields ?? {{}})) {{
            // If a field appears in multiple docs of same type, keep the highest-confidence one.
            const prev = out[t][k];
            if (!prev || (v.confidence ?? 0) > (prev.confidence ?? 0)) out[t][k] = v;
          }}
        }}
      }}
      return out;
    }};
    const H = collect(h), U = collect(u);
    const types = Array.from(new Set([...Object.keys(H), ...Object.keys(U)])).sort();
    return types.map(t => {{
      const names = Array.from(new Set([
        ...Object.keys(H[t] || {{}}),
        ...Object.keys(U[t] || {{}}),
      ])).sort();
      return {{ docType: t, fieldNames: names, H: H[t] || {{}}, U: U[t] || {{}} }};
    }});
  }})();
  const cell = (v) => v == null
    ? `<span style="color:var(--bad);font-weight:600">✗</span>`
    : `<div>${{escape(v.value ?? '—')}}</div>${{v.confidence != null ? `<span class="conf ${{confClass(v.confidence)}}">${{v.confidence.toFixed(2)}}</span>` : ''}}`;
  const matrixSection = matrixByType.length === 0
    ? '<div class="empty">No structured fields extracted by any strategy</div>'
    : matrixByType.map(g => {{
        // Counts per strategy for the header
        const cH = g.fieldNames.filter(n => g.H[n] != null).length;
        const cU = g.fieldNames.filter(n => g.U[n] != null).length;
        const total = g.fieldNames.length;
        const rows = g.fieldNames.map(n => {{
          const present = [g.H[n], g.U[n]].filter(x => x != null).length;
          const isGap = present > 0 && present < 2;  // one strategy missed it
          const rowStyle = isGap ? 'background:#fffbe6' : '';
          return `<tr style="${{rowStyle}}">
            <td><b>${{escape(n)}}</b></td>
            <td>${{cell(g.H[n])}}</td>
            <td>${{cell(g.U[n])}}</td>
          </tr>`;
        }}).join('');
        return `<div style="margin-top:14px">
          <div style="margin-bottom:8px;display:flex;align-items:center;gap:10px;flex-wrap:wrap">
            ${{chip(g.docType)}}
            <span style="color:var(--muted);font-size:12px">
              ${{total}} unique fields ·
              <span class="pill h">H ${{cH}}/${{total}}</span>
              <span class="pill u">CU ${{cU}}/${{total}}</span>
            </span>
          </div>
          <table>
            <thead><tr>
              <th>Field</th>
              <th><span class="pill h">HEURISTIC</span></th>
              <th><span class="pill u">CU</span></th>
            </tr></thead>
            <tbody>${{rows}}</tbody>
          </table>
        </div>`;
      }}).join('');
  return `
    <section class="card">
      <h2>Comparison summary</h2>
      <div class="kpis" style="grid-template-columns:repeat(2,1fr)">
        ${{col('Heuristic (DI)',  'h', h, h.billing.totalCostEstimateUsd, h.totalDurationMs)}}
        ${{col('Content Understanding', 'u', u, h.billing.totalCostEstimateUsd, h.totalDurationMs)}}
      </div>
    </section>

    <section class="card">
      <h2>Detected segments side-by-side</h2>
      <div class="grid">
        ${{segCol('h', 'HEURISTIC',  'strategy-h', h)}}
        ${{segCol('u', 'CU',         'strategy-u', u)}}
      </div>
    </section>

    <section class="card">
      <h2>Extracted fields — coverage matrix</h2>
      <div style="font-size:12px;color:var(--muted);margin-bottom:6px">
        Rows highlighted in yellow indicate a field that was extracted by at least one strategy but missed by another — these are quality gaps worth reviewing.
      </div>
      ${{matrixSection}}
    </section>

    <details><summary>Raw JSON</summary><pre>${{escape(JSON.stringify(d, null, 2))}}</pre></details>
  `;
}}

$('#f').addEventListener('submit', async (e) => {{
  e.preventDefault();
  const form = e.target;
  const file = form.file.files[0];
  if (!file) return;
  const mode = form.mode.value;
  const tenant = form.tenantId.value || 'demo-tenant';
  const fd = new FormData();
  fd.append('file', file);
  $('#status').innerHTML = `<div class="card"><span class="spinner"></span>Processing <b>${{escape(file.name)}}</b> in <b>${{escape(mode)}}</b> mode… (DI calls can take 10–60s)</div>`;
  $('#results').innerHTML = '';
  $('#go').disabled = true;
  try {{
    const res = await fetch(`/process?mode=${{encodeURIComponent(mode)}}`, {{
      method:'POST', body: fd, headers: {{'x-tenant-id': tenant}}
    }});
    const data = await res.json();
    $('#status').innerHTML = '';
    if (!res.ok) {{
      $('#results').innerHTML = `<div class="card"><div class="err">${{escape(data.detail || res.statusText)}}</div></div>`;
      return;
    }}
    $('#results').innerHTML = data.comparison ? compareResult(data) : singleResult(data);
  }} catch (err) {{
    $('#status').innerHTML = '';
    $('#results').innerHTML = `<div class="card"><div class="err">${{escape(err.message)}}</div></div>`;
  }} finally {{
    $('#go').disabled = false;
  }}
}});
</script>
</body>
</html>"""


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.post("/process")
async def process(
    request: Request,
    file: UploadFile = File(...),
    x_tenant_id: str | None = Header(default=None, alias="x-tenant-id"),
    mode: str = Query(default="heuristic"),
) -> JSONResponse:
    """Main entry point. Accepts a merged loan-application PDF and returns
    structured per-document extraction.

    Inputs:
      - file:        multipart upload (PDF)
      - x-tenant-id: HTTP header identifying the SaaS tenant (for cost allocation)
      - mode:        "heuristic" | "cu" | "compare" (query string or form field)
    """
    # Reject non-PDF uploads early. octet-stream is allowed because some
    # browsers / clients don't sniff the type.
    if file.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(status_code=415, detail="PDF required")

    # Read multipart form once so we can pull both `tenantId` and `mode` fields
    # from it (the HTML UI submits both as form fields).
    form = await request.form() if request.headers.get("content-type", "").startswith("multipart/") else {}
    # Resolve tenant in priority order: header > form field > "anonymous".
    # The HTML form posts the tenant under the field name "tenantId" (see UI markup).
    tenant_id = (
        x_tenant_id
        or (form.get("tenantId") if form else None)  # type: ignore[union-attr]
        or "anonymous"
    )
    # The HTML form posts mode as a form field; query param is the API path.
    chosen_mode = (form.get("mode") if form else None) or mode  # type: ignore[union-attr]
    chosen_mode = str(chosen_mode).lower()

    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="Empty file")

    # Lazily build the DI client (returns 503 with a friendly message if misconfigured).
    di = _get_di()

    # Compare mode runs both strategies sequentially. We tried thread
    # parallelism here but a single AI Services account throttles concurrent
    # DI+CU requests, which triggers SDK retry/backoff and made wall time
    # *worse*, not better. Sequential is the safer demo path.
    if chosen_mode == "compare":
        h = _run_pipeline("heuristic", di, pdf_bytes, tenant_id, file.filename)
        u = _run_pipeline("cu", di, pdf_bytes, tenant_id, file.filename)
        return JSONResponse({
            "tenantId": tenant_id,
            "filename": file.filename,
            "comparison": _summarize_compare(h, u),
            "heuristic": h,
            "cu": u,
        })

    if chosen_mode == "classifier":
        raise HTTPException(
            status_code=400,
            detail="The custom classifier mode was retired. Use heuristic, cu, or compare.",
        )
    if chosen_mode not in ("heuristic", "cu"):
        raise HTTPException(
            status_code=400,
            detail="mode must be heuristic, cu, or compare",
        )

    # Single-strategy path.
    out = _run_pipeline(chosen_mode, di, pdf_bytes, tenant_id, file.filename)  # type: ignore[arg-type]
    return JSONResponse(out)


# ---------- core pipeline ----------

def _get_di() -> DIClient:
    try:
        return DIClient()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                f"Document Intelligence not configured: {exc}. "
                "Set DI_ENDPOINT (and DI_KEY or use managed identity) and restart."
            ),
        ) from exc


def _run_pipeline(
    strategy: SplitStrategy,
    di: DIClient,
    pdf_bytes: bytes,
    tenant_id: str,
    filename: str | None,
) -> dict:
    """Two-stage IDP pipeline: SPLIT (boundaries + types) then EXTRACT (per segment).

    Stage 1 — Split pass:
        heuristic   -> prebuilt-layout over whole doc + keyword classifier
        cu          -> heuristic split (DI prebuilt-layout) then CU prebuilts for extraction

    Stage 2 — Per-segment extraction:
        slice the PDF down to each segment, then call the model picked by
        MODEL_BY_TYPE (prebuilt-tax.us.w2 for W2s, prebuilt-idDocument for
        IDs, prebuilt-layout otherwise).

    Every DI call emits one `di.pages.processed` trace; CU calls emit
    `cu.calls.processed`. All rows in a single request share one correlationId
    so they're easy to stitch together in App Insights.
    """
    # cu strategy has its own orchestration (mixes DI for split, CU for extract);
    # delegate to a dedicated function to keep the DI-only pipeline readable.
    if strategy == "cu":
        return _run_cu_pipeline(di, pdf_bytes, tenant_id, filename)

    # correlationId threads all telemetry rows for this request together.
    correlation_id = str(uuid.uuid4())
    log.info("pipeline start strategy=%s tenant=%s corr=%s", strategy, tenant_id, correlation_id)
    overall_start = time.perf_counter()

    # ---- Split pass ----
    if strategy == "heuristic":
        # One DI call returns layout (text + structure) for every page.
        split_model = "prebuilt-layout"
        layout_result, split_ms = di.analyze(model_id=split_model, content=pdf_bytes)
        page_texts = di.page_text(layout_result)
        total_pages = len(page_texts)
        # Keyword-score each page, then collapse runs of same-typed pages into segments.
        page_types = [classify_page(t) for t in page_texts]
        segments = segments_from_page_types(page_types)
    split_cost = estimate_cost_usd("prebuilt-layout", total_pages)
    # Telemetry row #1 for this request: the split pass.
    emit_pages_processed(
        tenant_id=tenant_id,
        model=split_model,
        pages=total_pages,
        duration_ms=split_ms,
        extra={"correlationId": correlation_id, "stage": "split", "splitStrategy": strategy},
    )

    log.info("segments tenant=%s strategy=%s segments=%s",
             tenant_id, strategy,
             [(s.doc_type, s.page_start, s.page_end) for s in segments])

    # ---- Per-segment extraction ----
    # For each Segment, slice out just its pages, send to the model chosen by
    # MODEL_BY_TYPE, and accumulate cost. One telemetry row per segment.
    # NOTE: billed_pages = split-pass pages + sum(segment pages); the split
    # pass and per-segment analyze are billed on separate meters by Azure DI.
    per_doc_results: list[dict] = []
    total_cost = split_cost
    billed_pages = total_pages
    reader = PdfReader(io.BytesIO(pdf_bytes))
    for seg in segments:
        # Build a small PDF containing only this segment's pages.
        seg_bytes = _extract_pages(reader, seg.page_start, seg.page_end)
        result, dur_ms = di.analyze(model_id=seg.model_id, content=seg_bytes)
        seg_pages = seg.page_end - seg.page_start + 1
        seg_cost = estimate_cost_usd(seg.model_id, seg_pages)
        total_cost += seg_cost
        billed_pages += seg_pages
        emit_pages_processed(
            tenant_id=tenant_id,
            model=seg.model_id,
            pages=seg_pages,
            duration_ms=dur_ms,
            extra={
                "correlationId": correlation_id,
                "docType": seg.doc_type,
                "pageStart": seg.page_start,
                "pageEnd": seg.page_end,
                "splitStrategy": strategy,
            },
        )
        per_doc_results.append({
            "doc_type": seg.doc_type,
            "model_id": seg.model_id,
            "page_range": [seg.page_start, seg.page_end],
            "pages": seg_pages,
            "duration_ms": round(dur_ms, 2),
            "cost_estimate_usd": seg_cost,
            "documents": di.summarize_fields(result),
        })

    overall_ms = (time.perf_counter() - overall_start) * 1000.0

    models_used: dict[str, dict[str, int]] = {}
    def _bump(model: str, pages: int) -> None:
        slot = models_used.setdefault(model, {"calls": 0, "pages": 0})
        slot["calls"] += 1
        slot["pages"] += pages
    _bump(split_model, total_pages)
    for seg in segments:
        _bump(seg.model_id, seg.page_end - seg.page_start + 1)
    models_used_list = [
        {"model_id": m, "calls": v["calls"], "pages": v["pages"]}
        for m, v in sorted(models_used.items())
    ]

    return {
        "correlationId": correlation_id,
        "tenantId": tenant_id,
        "filename": filename,
        "splitStrategy": strategy,
        "totalPages": total_pages,
        "totalDurationMs": round(overall_ms, 2),
        "modelsUsed": models_used_list,
        "billing": {
            "billedPages": billed_pages,
            "splitPassPages": total_pages,
            "splitPassModel": split_model,
            "splitPassPricingKey": "prebuilt-layout",
            "splitPassCostEstimateUsd": round(split_cost, 6),
            "totalCostEstimateUsd": round(total_cost, 6),
            "note": "Approximate. Split pass + per-segment analyze are billed separately.",
        },
        "segments": per_doc_results,
    }


def _summarize_compare(h: dict, u: dict) -> dict:
    h_cost = h["billing"]["totalCostEstimateUsd"]
    u_cost = u["billing"]["totalCostEstimateUsd"]
    def _sav(base: float, other: float) -> dict:
        return {
            "savingsUsd": round(base - other, 6),
            "savingsPct": round((base - other) / base * 100, 1) if base else 0.0,
        }
    return {
        "billedPages":     {"heuristic": h["billing"]["billedPages"], "cu": u["billing"]["billedPages"]},
        "totalCostUsd":    {"heuristic": h_cost, "cu": u_cost,
                            "cuVsHeuristic": _sav(h_cost, u_cost)},
        "totalDurationMs": {"heuristic": h["totalDurationMs"], "cu": u["totalDurationMs"]},
        "segmentCount":    {"heuristic": len(h["segments"]), "cu": len(u["segments"])},
        "segmentDocTypes": {"heuristic": [s["doc_type"] for s in h["segments"]],
                            "cu":        [s["doc_type"] for s in u["segments"]]},
    }


def _extract_pages(reader: PdfReader, start_1based: int, end_1based: int) -> bytes:
    """Return a new PDF (as bytes) containing only pages [start..end] (1-based).

    Used to send each segment to DI individually so we only pay for the right
    model per page range (e.g., prebuilt-tax.us.w2 only for the W2 page).
    """
    writer = PdfWriter()
    for i in range(start_1based - 1, end_1based):
        writer.add_page(reader.pages[i])
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


# ---------- Content Understanding pipeline ----------

def _get_cu() -> CUClient:
    """Lazy-build the CU client. Returns 503 with a friendly hint on misconfig."""
    try:
        return CUClient()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                f"Content Understanding not configured: {exc}. "
                "Set CU_ENDPOINT (or reuse DI_ENDPOINT on the same Foundry resource) and restart."
            ),
        ) from exc


def _run_cu_pipeline(
    di: DIClient,
    pdf_bytes: bytes,
    tenant_id: str,
    filename: str | None,
) -> dict:
    """CU strategy: ONE Content Understanding call does split + classify + route + extract.

    Calls a CU `contentCategories`-style classifier analyzer (deployed via
    scripts/deploy_cu_classifier.py) with `enableSegment=true`. Each category
    in the analyzer carries an `analyzerId` pointing to a CU prebuilt
    extractor (e.g., paystub -> prebuilt-payStub.us), so the service runs
    classification, boundary detection, and per-segment extraction inside
    a single HTTP call.

    Net effect vs the old pipeline (kept commented at the bottom of this file
    as `_run_cu_pipeline_legacy` for reference):
      - No DI prebuilt-layout call (no app-side splitter).
      - No keyword-regex page classifier.
      - No CU_ANALYZER_BY_TYPE dict / per-segment dispatch loop.
      - One `cu.calls.processed` row per detected segment, parsed from the
        single response.

    Requires CU_ROUTER_ANALYZER_ID env var to be set to the deployed analyzer
    id (default: "loan-docs-router"). Returns 503 with a setup hint if missing.
    """
    cu = _get_cu()
    router_id = settings.cu_router_analyzer_id or "loan_docs_router"
    correlation_id = str(uuid.uuid4())
    log.info("cu pipeline (router) start tenant=%s analyzer=%s corr=%s",
             tenant_id, router_id, correlation_id)
    overall_start = time.perf_counter()

    # ---- Single CU call: split + classify + route + extract ----
    try:
        result, total_ms = cu.analyze(
            analyzer_id=router_id,
            content=pdf_bytes,
            enable_segment=True,
        )
    except RuntimeError as exc:
        # Most likely the analyzer hasn't been deployed yet on this account.
        raise HTTPException(
            status_code=503,
            detail=(
                f"CU router analyzer '{router_id}' not callable: {exc}. "
                f"Deploy it with: python scripts/deploy_cu_classifier.py "
                f"--analyzer-id {router_id}"
            ),
        ) from exc

    segments = cu.segments_from_router_result(result)
    total_pages = cu.page_count(result) or sum((s.get("pages") or 0) for s in segments)

    # ---- Telemetry: one row per segment ----
    # Cost is approximated at the cu.prebuilt rate per segment. The classifier
    # itself is billed on its own meter in production accounting; for the demo
    # we keep the cost line simple and attribute everything to the extractor.
    per_doc_results: list[dict] = []
    total_cost = 0.0
    billed_pages = 0
    for seg in segments:
        seg_pages = seg.get("pages") or 0
        analyzer_id = seg.get("analyzer_id") or "(classified-only)"
        seg_cost = estimate_cost_usd("cu.prebuilt", seg_pages) if seg_pages else 0.0
        total_cost += seg_cost
        billed_pages += seg_pages
        emit_cu_call_processed(
            tenant_id=tenant_id,
            analyzer_id=analyzer_id,
            pricing_key="cu.prebuilt",
            pages=seg_pages,
            # Per-segment duration isn't broken out in the response; we report
            # the overall call duration as a shared upper bound. KQL aggregates
            # by correlationId so this still rolls up correctly per request.
            duration_ms=total_ms,
            extra={
                "correlationId": correlation_id,
                "docType": seg.get("doc_type"),
                "category": seg.get("category"),
                "pageStart": seg.get("page_start"),
                "pageEnd": seg.get("page_end"),
                "splitStrategy": "cu",
                "routerAnalyzerId": router_id,
                "classificationConfidence": seg.get("confidence"),
            },
        )
        per_doc_results.append({
            "doc_type": seg.get("doc_type"),
            "model_id": analyzer_id,
            "page_range": [seg.get("page_start"), seg.get("page_end")],
            "pages": seg_pages,
            # Per-segment duration isn't reported by the service; show the
            # whole-call duration so the UI panel still has a number.
            "duration_ms": round(total_ms, 2),
            "cost_estimate_usd": seg_cost,
            "documents": [{
                "doc_type": seg.get("doc_type"),
                "confidence": seg.get("confidence"),
                "fields": seg.get("fields") or {},
            }],
        })

    overall_ms = (time.perf_counter() - overall_start) * 1000.0

    # Aggregate per-model usage for the "Models used" panel in the UI.
    models_used: dict[str, dict[str, int]] = {}
    def _bump(model: str, pages: int) -> None:
        slot = models_used.setdefault(model, {"calls": 0, "pages": 0})
        slot["calls"] += 1
        slot["pages"] += pages
    # The router itself is one logical call against `router_id`; each routed
    # extractor is billed once per segment.
    _bump(router_id, total_pages)
    for seg in segments:
        _bump(seg.get("analyzer_id") or "(classified-only)", seg.get("pages") or 0)
    models_used_list = [
        {"model_id": m, "calls": v["calls"], "pages": v["pages"]}
        for m, v in sorted(models_used.items())
    ]

    return {
        "correlationId": correlation_id,
        "tenantId": tenant_id,
        "filename": filename,
        "splitStrategy": "cu",
        "totalPages": total_pages,
        "totalDurationMs": round(overall_ms, 2),
        "modelsUsed": models_used_list,
        "billing": {
            "billedPages": billed_pages,
            "splitPassPages": 0,         # No separate split pass — the router does it.
            "splitPassModel": router_id,
            "splitPassPricingKey": "cu.prebuilt",
            "splitPassCostEstimateUsd": 0.0,
            "totalCostEstimateUsd": round(total_cost, 6),
            "note": (
                "CU router analyzer did split + classify + route + extract in one call. "
                "Cost approximated per segment at the cu.prebuilt rate."
            ),
        },
        "segments": per_doc_results,
    }


# ---------------------------------------------------------------------------
# LEGACY: app-side CU pipeline (DI prebuilt-layout splitter + keyword regex +
# CU_ANALYZER_BY_TYPE dispatch dict). Replaced by `_run_cu_pipeline` above
# which makes one CU router call instead of 1+N calls. Kept commented for
# reference / fallback / cost-comparison demos.
# ---------------------------------------------------------------------------
# def _run_cu_pipeline_legacy(
#     di: DIClient,
#     pdf_bytes: bytes,
#     tenant_id: str,
#     filename: str | None,
# ) -> dict:
#     """OLD CU strategy: heuristic split (DI prebuilt-layout) + per-segment CU prebuilt extraction.
#
#     The split pass uses DI's prebuilt-layout (cheapest reliable way to get
#     per-page text for the keyword classifier, and emits a `di.pages.processed`
#     row for cost transparency). Per-segment extraction then routes each
#     segment to a CU prebuilt analyzer via CUClient and emits one
#     `cu.calls.processed` row per segment.
#     """
#     cu = _get_cu()
#     correlation_id = str(uuid.uuid4())
#     log.info("cu pipeline start tenant=%s corr=%s", tenant_id, correlation_id)
#     overall_start = time.perf_counter()
#
#     # ---- Split pass (DI prebuilt-layout, same as heuristic mode) ----
#     split_model = "prebuilt-layout"
#     layout_result, split_ms = di.analyze(model_id=split_model, content=pdf_bytes)
#     page_texts = di.page_text(layout_result)
#     total_pages = len(page_texts)
#     page_types = [classify_page(t) for t in page_texts]
#     segments = segments_from_page_types(page_types)
#
#     split_pricing_key = "prebuilt-layout"
#     split_cost = estimate_cost_usd(split_pricing_key, total_pages)
#     emit_pages_processed(
#         tenant_id=tenant_id,
#         model=split_model,
#         pages=total_pages,
#         duration_ms=split_ms,
#         extra={"correlationId": correlation_id, "stage": "split", "splitStrategy": "cu"},
#     )
#
#     # ---- Per-segment extraction via CU prebuilts (app-side dispatch loop) ----
#     per_doc_results: list[dict] = []
#     total_cost = split_cost
#     billed_pages = total_pages
#     reader = PdfReader(io.BytesIO(pdf_bytes))
#     for seg in segments:
#         analyzer_id = CU_ANALYZER_BY_TYPE.get(seg.doc_type, CU_ANALYZER_BY_TYPE["unknown"])
#         seg_bytes = _extract_pages(reader, seg.page_start, seg.page_end)
#         result, dur_ms = cu.analyze(analyzer_id=analyzer_id, content=seg_bytes)
#         seg_pages = seg.page_end - seg.page_start + 1
#         seg_cost = estimate_cost_usd("cu.prebuilt", seg_pages)
#         total_cost += seg_cost
#         billed_pages += seg_pages
#         emit_cu_call_processed(
#             tenant_id=tenant_id,
#             analyzer_id=analyzer_id,
#             pricing_key="cu.prebuilt",
#             pages=seg_pages,
#             duration_ms=dur_ms,
#             extra={
#                 "correlationId": correlation_id,
#                 "docType": seg.doc_type,
#                 "pageStart": seg.page_start,
#                 "pageEnd": seg.page_end,
#                 "splitStrategy": "cu",
#             },
#         )
#         per_doc_results.append({
#             "doc_type": seg.doc_type,
#             "model_id": analyzer_id,
#             "page_range": [seg.page_start, seg.page_end],
#             "pages": seg_pages,
#             "duration_ms": round(dur_ms, 2),
#             "cost_estimate_usd": seg_cost,
#             "documents": cu.summarize_fields(result),
#         })
#
#     overall_ms = (time.perf_counter() - overall_start) * 1000.0
#     # ... (response assembly identical to current pipeline)

app.include_router(create_library_router(
    get_service=get_library_service,
    get_di=_get_di,
    run_pipeline=_run_pipeline,
    get_agents=get_loan_agents,
    get_knowledge=get_knowledge,
))


_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


@app.get("/")
def home() -> FileResponse:
    return FileResponse(os.path.join(_STATIC_DIR, "index.html"))

