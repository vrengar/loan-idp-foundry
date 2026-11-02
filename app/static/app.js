const state = { selectedId: null, selected: null, result: null, examples: {} };
const $ = (selector) => document.querySelector(selector);
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
}[c]));
const tenant = () => $("#tenant").value.trim() || "demo";
const pill = (status) => `<span class="pill ${esc(status)}">${esc(status)}</span>`;
const askButton = $("#askForm button");

async function api(path, options = {}) {
  const headers = { "x-tenant-id": tenant(), ...(options.headers || {}) };
  const response = await fetch(path, { ...options, headers });
  const text = await response.text();
  let body = text;
  try { body = text ? JSON.parse(text) : null; } catch (_) { body = text; }
  if (!response.ok) {
    const detail = body && body.detail !== undefined ? body.detail : text;
    const message = typeof detail === "string" ? detail : (detail.message || JSON.stringify(detail));
    throw new Error(`${response.status}: ${message}`);
  }
  return body;
}

async function refreshStatus() {
  const badge = $("#health");
  try {
    const status = await api("/api/status");
    const library = status.library && status.library.status;
    const cu = status.cuRouter && status.cuRouter.status;
    const knowledge = status.knowledge && status.knowledge.status;
    badge.textContent = `library ${library} · CU router ${cu} · knowledge ${knowledge}`;
    badge.className = `pill ${library === "ok" && cu === "ready" && knowledge === "ready" ? "ok" : "warn"}`;
  } catch (err) {
    badge.textContent = err.message;
    badge.className = "pill bad";
  }
}

async function loadDocs() {
  const list = $("#docs");
  try {
    const docs = await api("/api/documents");
    list.innerHTML = docs.map((d) => `
      <li class="${d.id === state.selectedId ? "active" : ""}" data-id="${esc(d.id)}">
        <div class="doc-name">${esc(d.fileName)}${d.exampleId ? ` <span class="pill">example</span>` : ""}</div>
        <div class="doc-meta">${pill(d.status)} ${d.summary ? `${esc(d.summary.segmentCount)} segments · ${esc(d.summary.totalPages)} pages` : ""}</div>
      </li>`).join("") || `<li class="muted">No documents yet. Upload a loan packet.</li>`;
    list.querySelectorAll("li[data-id]").forEach((li) => li.addEventListener("click", () => selectDoc(li.dataset.id)));
  } catch (err) {
    list.innerHTML = `<li class="bad">${esc(err.message)}</li>`;
  }
}

async function selectDoc(id) {
  state.selectedId = id;
  $("#chat").innerHTML = "";
  $("#review").innerHTML = "";
  askButton.disabled = false;
  $("#question").disabled = false;
  $("#runReview").disabled = false;
  const doc = await api(`/api/documents/${encodeURIComponent(id)}`);
  state.selected = doc;
  renderDetails(doc);
  renderReview(doc);
  if (doc.status === "processed") {
    state.result = await api(`/api/documents/${encodeURIComponent(id)}/result`);
    renderSegments(state.result);
  }
  await loadDocs();
}

function renderDetails(doc) {
  const s = doc.summary || {};
  const cost = s.estimatedCostUsd != null ? `$${Number(s.estimatedCostUsd).toFixed(4)}` : "–";
  const latency = s.durationMs ? `${Math.round(s.durationMs)} ms` : "–";
  const idx = doc.searchIndex;
  const searchLine = idx
    ? (idx.error ? ` · <span class="bad">search index: ${esc(idx.error)}</span>` : ` · search index: ${esc(idx.chunks)} chunks`)
    : "";
  $("#details").innerHTML = `
    <div class="kpis">
      <div><span>Status</span>${pill(doc.status)}</div>
      <div><span>Pages</span><b>${esc(s.totalPages ?? "–")}</b></div>
      <div><span>Segments</span><b>${esc(s.segmentCount ?? "–")}</b></div>
      <div><span>Latency</span><b>${esc(latency)}</b></div>
      <div><span>Est. cost</span><b>${esc(cost)}</b></div>
    </div>
    <p class="muted">${esc(doc.fileName)} · strategy ${esc(doc.mode)}${doc.error ? ` · <span class="bad">${esc(doc.error)}</span>` : ""}${searchLine}</p>
    <div id="segments"></div>`;
}

function renderSegments(result) {
  const rows = (result.segments || []).map((seg) => {
    const fields = (seg.documents || []).flatMap((d) => Object.entries(d.fields || {})).slice(0, 6)
      .map(([name, field]) => {
        const value = field && typeof field === "object" ? field.value : field;
        return `${esc(name)}: <b>${esc(value)}</b>`;
      }).join("<br>");
    return `<tr>
      <td>${esc(seg.doc_type)}</td>
      <td>${esc((seg.page_range || []).join("–"))}</td>
      <td>${esc(seg.model_id || "")}</td>
      <td>${fields || '<span class="muted">no fields</span>'}</td>
    </tr>`;
  }).join("");
  $("#segments").innerHTML = `
    <table>
      <thead><tr><th>Type</th><th>Pages</th><th>Model</th><th>Key fields</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <details><summary>Raw result JSON</summary><pre>${esc(JSON.stringify(result, null, 2))}</pre></details>`;
}

function addMessage(who, text, model) {
  const chat = $("#chat");
  const div = document.createElement("div");
  div.className = `msg ${who}`;
  div.innerHTML = `<div class="who">${esc(who)}${model ? ` · ${esc(model)}` : ""}</div>
    <div class="text">${esc(text).replace(/\n/g, "<br>")}</div>`;
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
}

function renderReview(doc) {
  const review = doc.review;
  if (!review) {
    $("#review").innerHTML = `<span class="muted">No review yet.</span>`;
    return;
  }
  const conditions = (review.conditions || []).map((c) => `<li>${esc(c)}</li>`).join("");
  const steps = (review.steps || []).map((s) => `
    <details><summary>${esc(s.agent)} · ${esc(s.model)}</summary><pre>${esc(s.output)}</pre></details>`).join("");
  const decision = doc.decision
    ? `<p>Decision: ${pill(doc.decision.value)} <span class="muted">${esc(doc.decision.note || "")}</span></p>`
    : `<div class="decide">
         <button type="button" data-decision="approve">Approve</button>
         <button type="button" class="ghost" data-decision="refer">Refer</button>
         <button type="button" class="ghost" data-decision="decline">Decline</button>
       </div>`;
  $("#review").innerHTML = `
    <p>Agent recommendation: ${pill(review.recommendation)}</p>
    <p>${esc(review.rationale)}</p>
    ${conditions ? `<ul>${conditions}</ul>` : ""}
    ${decision}
    <div>${steps}</div>`;
  $("#review").querySelectorAll("[data-decision]").forEach((b) =>
    b.addEventListener("click", () => decide(b.dataset.decision)));
}

async function decide(decision) {
  const note = window.prompt("Note for the audit trail (optional):", "") || "";
  try {
    const doc = await api(`/api/documents/${encodeURIComponent(state.selectedId)}/decision`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ decision, note }),
    });
    state.selected = doc;
    renderReview(doc);
  } catch (err) {
    $("#review").insertAdjacentHTML("beforeend", `<p class="bad">${esc(err.message)}</p>`);
  }
}

function renderCompare(data) {
  const c = data.comparison;
  const money = (v) => `$${Number(v).toFixed(4)}`;
  const ms = (v) => `${Math.round(v)} ms`;
  const row = (label, heuristic, cu) => `<tr><td>${esc(label)}</td><td>${esc(heuristic)}</td><td>${esc(cu)}</td></tr>`;
  const saving = c.totalCostUsd.cuVsHeuristic;
  const verdict = saving.savingsUsd >= 0 ? "cheaper" : "more expensive";
  $("#details").innerHTML = `
    <h3>Heuristic vs Content Understanding</h3>
    <table>
      <thead><tr><th>Metric</th><th>Heuristic (Document Intelligence)</th><th>Content Understanding router</th></tr></thead>
      <tbody>
        ${row("Billed pages", c.billedPages.heuristic, c.billedPages.cu)}
        ${row("Estimated cost", money(c.totalCostUsd.heuristic), money(c.totalCostUsd.cu))}
        ${row("Latency", ms(c.totalDurationMs.heuristic), ms(c.totalDurationMs.cu))}
        ${row("Segments", c.segmentCount.heuristic, c.segmentCount.cu)}
      </tbody>
    </table>
    <p class="muted">CU is ${esc(money(Math.abs(saving.savingsUsd)))} ${verdict} on this packet (${esc(Math.abs(saving.savingsPct))}%).</p>
    <p><b>Heuristic segments:</b> ${esc(c.segmentDocTypes.heuristic.join(", ") || "none")}</p>
    <p><b>CU segments:</b> ${esc(c.segmentDocTypes.cu.join(", ") || "none")}</p>
    <details><summary>Raw comparison JSON</summary><pre>${esc(JSON.stringify(data, null, 2))}</pre></details>`;
}

async function runCompare(form) {
  try {
    const data = await api("/process?mode=compare", { method: "POST", body: form });
    $("#uploadStatus").textContent = "Compared both strategies. Nothing was saved to the library.";
    renderCompare(data);
  } catch (err) {
    $("#uploadStatus").textContent = err.message;
  }
}

function strategy() {
  return $("#mode").value === "cu" ? "cu" : "heuristic";
}

async function loadExamples() {
  const list = $("#exampleList");
  try {
    const examples = await api("/api/examples");
    state.examples = Object.fromEntries(examples.map((ex) => [ex.id, ex]));
    list.innerHTML = examples.map((ex) => `
      <li data-id="${esc(ex.id)}">
        <div class="doc-name">${esc(ex.title)}</div>
        <div class="muted">${esc(ex.description)}</div>
        <div class="muted"><b>Try:</b> ${esc(ex.tryIt)}</div>
        <div class="example-actions">
          <button type="button" data-action="open">Open example</button>
          <button type="button" class="ghost" data-action="live">Process live</button>
        </div>
      </li>`).join("");
    list.querySelectorAll("button[data-action]").forEach((button) => {
      button.addEventListener("click", () => runExample(button.closest("li").dataset.id, button.dataset.action));
    });
  } catch (err) {
    list.innerHTML = `<li class="bad">${esc(err.message)}</li>`;
  }
}

async function runExample(id, action) {
  const status = $("#uploadStatus");
  const { fileName } = state.examples[id];
  const mode = strategy();
  try {
    let doc;
    if (action === "open") {
      status.textContent = `Loading ${fileName} with stored ${mode} results…`;
      doc = await api(`/api/examples/${encodeURIComponent(id)}/load?mode=${mode}`, { method: "POST" });
    } else {
      status.textContent = "Fetching the example PDF…";
      const response = await fetch(`/api/examples/${encodeURIComponent(id)}/pdf`);
      if (!response.ok) throw new Error(`${response.status}: could not fetch ${fileName}`);
      const form = new FormData();
      form.append("file", await response.blob(), fileName);
      status.textContent = "Processing live… this can take a minute.";
      doc = await api(`/api/documents?mode=${mode}`, { method: "POST", body: form });
    }
    status.textContent = `${action === "open" ? "Loaded" : "Processed"} ${doc.fileName} (${mode}).`;
    await loadDocs();
    await selectDoc(doc.id);
  } catch (err) {
    status.textContent = err.message;
    await loadDocs();
  }
}

$("#uploadForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = $("#file").files[0];
  if (!file) return;
  const form = new FormData();
  form.append("file", file);
  const mode = $("#mode").value;
  $("#uploadStatus").textContent = "Processing… this can take a minute.";
  if (mode === "compare") {
    await runCompare(form);
    return;
  }
  try {
    const doc = await api(`/api/documents?mode=${encodeURIComponent(mode)}`, { method: "POST", body: form });
    $("#uploadStatus").textContent = `Processed ${doc.fileName}.`;
    await loadDocs();
    await selectDoc(doc.id);
  } catch (err) {
    $("#uploadStatus").textContent = err.message;
    await loadDocs();
  }
});

$("#askForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = $("#question").value.trim();
  if (!question || !state.selectedId) return;
  addMessage("you", question);
  $("#question").value = "";
  try {
    const reply = await api(`/api/documents/${encodeURIComponent(state.selectedId)}/ask`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ question }),
    });
    addMessage("agent", reply.answer, `${reply.model} · policy: ${reply.policySource}`);
  } catch (err) {
    addMessage("error", err.message);
  }
});

$("#libraryAskForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = $("#libraryQuestion").value.trim();
  if (!question) return;
  $("#libraryAnswer").innerHTML = `<div class="muted">Searching the knowledge base…</div>`;
  try {
    const reply = await api("/api/library/ask", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ question }),
    });
    const sources = (reply.passages || []).map((p) =>
      `<li><b>[${esc(p.label)}]</b> ${esc(p.title)}<br><span class="muted">${esc(p.content)}</span></li>`).join("");
    $("#libraryAnswer").innerHTML = `
      <div class="msg agent"><div class="who">agent · ${esc(reply.model)}</div>
        <div class="text">${esc(reply.answer).replace(/\n/g, "<br>")}</div></div>
      <details><summary>${(reply.passages || []).length} passages retrieved</summary><ul>${sources}</ul></details>`;
  } catch (err) {
    $("#libraryAnswer").innerHTML = `<div class="bad">${esc(err.message)}</div>`;
  }
});

$("#runReview").addEventListener("click", async () => {
  if (!state.selectedId) return;
  $("#review").innerHTML = `<span class="muted">Running three Claude agents: intake, compliance, underwriter…</span>`;
  try {
    const doc = await api(`/api/documents/${encodeURIComponent(state.selectedId)}/review`, { method: "POST" });
    state.selected = doc;
    renderReview(doc);
  } catch (err) {
    $("#review").innerHTML = `<span class="bad">${esc(err.message)}</span>`;
  }
});

const ARCH = {
  Overview: `flowchart LR
  Browser[Reviewer browser] --> UI[Web UI on Container Apps]
  UI --> API[FastAPI API]
  API --> DI[Document Intelligence on Foundry]
  API --> CU[Content Understanding router on Foundry]
  API --> Claude[Claude on Foundry]
  API --> Search["AI Search: loan-documents index"]
  Search --> KB["Foundry IQ knowledge base: loan-kb"]
  API --> Cosmos[(Cosmos DB: document records)]
  API --> Blob[(Blob: originals and results)]`,
  Pipeline: `flowchart LR
  Upload[PDF upload] --> Mode{Mode}
  Mode -->|Heuristic| DI2[DI prebuilt per segment]
  Mode -->|CU router| CU2[CU: split, classify, extract]
  DI2 --> Result[Processed JSON in Blob]
  CU2 --> Result
  Result --> Record[(Cosmos record with summary)]
  Result --> Chunks["Segment chunks in AI Search"]`,
  Agents: `flowchart TB
  Packet[Processed packet] --> Intake[Intake agent: Haiku]
  Policy["Policy rules from the search index"] --> Compliance
  Intake --> Compliance[Compliance agent: Sonnet]
  Compliance --> Underwriter[Underwriter agent: Sonnet]
  Underwriter --> Human[Human decision: approve, refer, decline]
  Question["Ask the agent"] --> Ask["Sonnet: packet fields plus KB policy passages"]
  Library["Ask the library"] --> KB["loan-kb retrieval over packets and policy"]
  KB --> Answer["Sonnet with P-number citations"]`,
  Security: `flowchart LR
  Internet[Internet] --> Ingress[Public app ingress: no sign-in, demo data only]
  Ingress --> API[API: library scoped by x-tenant-id header]
  API --> PE[Private endpoints in prod]
  PE --> Foundry[Foundry, Search, Cosmos, Storage]`,
};

function showArch(name) {
  const body = $("#archBody");
  body.textContent = "Rendering…";
  if (!window.mermaid) {
    body.innerHTML = `<pre>${esc(ARCH[name])}</pre>`;
    return;
  }
  window.mermaid.initialize({ startOnLoad: false, securityLevel: "strict" });
  window.mermaid.render(`arch-${name}-${Date.now()}`, ARCH[name])
    .then(({ svg }) => { body.innerHTML = svg; })
    .catch((err) => { body.innerHTML = `<span class="bad">${esc(err.message)}</span>`; });
}

$("#openArch").addEventListener("click", () => {
  const tabs = $("#archTabs");
  tabs.innerHTML = Object.keys(ARCH).map((name, i) =>
    `<button type="button" class="${i ? "ghost" : ""}" data-tab="${esc(name)}">${esc(name)}</button>`).join("");
  tabs.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => showArch(b.dataset.tab)));
  $("#archDialog").showModal();
  showArch("Overview");
});
$("#closeArch").addEventListener("click", () => $("#archDialog").close());

$("#tenant").value = localStorage.getItem("tenant") || "demo";
$("#tenant").addEventListener("change", () => {
  localStorage.setItem("tenant", tenant());
  state.selectedId = null;
  loadDocs();
});

refreshStatus();
loadDocs();
loadExamples();
