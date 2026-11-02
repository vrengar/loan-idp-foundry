"""Ask-the-agent and the three-agent underwriting review. Both run on Claude through Foundry."""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import Any

from azure.identity import DefaultAzureCredential

from .claude import ClaudeClient
from .config import settings
from .knowledge import KnowledgeClient, KnowledgeError, ready_knowledge
from .policy import UNDERWRITING_RULES
from .telemetry import agent_span

log = logging.getLogger("idp.agents")

ASK_SYSTEM = (
    "You answer questions about one loan application packet. Use only the extracted fields in the "
    "packet and the policy passages. Cite packet segments like [S1] and policy passages like [P1]. "
    "If neither answers the question, say so. Be brief."
)
LIBRARY_SYSTEM = (
    "You answer questions across a library of loan application packets. Use only the passages. "
    "Cite each passage you use like [P1] and name its file. If the passages do not answer the "
    "question, say so. Be brief."
)
INTAKE_SYSTEM = (
    "Extract the applicant profile from the packet. Return only JSON with keys: applicant_name, "
    "employer, monthly_gross_income, documents_present (list), documents_missing (list), notes."
)
COMPLIANCE_SYSTEM = (
    "Check the packet against these underwriting policy passages:\n{policy}\n"
    'Return only JSON: {{"checks": [{{"rule": string, "status": "pass" | "flag" | "missing", '
    '"evidence": string}}]}}'
)
UNDERWRITER_SYSTEM = (
    "You write the underwriting recommendation for a human reviewer. Return only JSON with keys: "
    "recommendation (approve | refer | decline), rationale (string), conditions (list of strings). "
    "Recommend refer when any check is flagged or missing."
)


def packet_context(result: dict[str, Any]) -> str:
    lines: list[str] = []
    for index, segment in enumerate(result.get("segments") or [], start=1):
        pages = "-".join(str(p) for p in segment.get("page_range", []))
        lines.append(f"[S{index}] {segment.get('doc_type')} (pages {pages})")
        for document in segment.get("documents") or []:
            for name, field in (document.get("fields") or {}).items():
                value = field.get("value") if isinstance(field, dict) else field
                lines.append(f"  - {name}: {value}")
    return "\n".join(lines) or "(no extracted fields)"


def parse_json(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return {}
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def numbered(passages: list[str]) -> str:
    return "\n".join(f"[P{number}] {text}" for number, text in enumerate(passages, start=1))


def _tags(result: dict[str, Any]) -> dict[str, str]:
    tags: dict[str, str] = {}
    if result.get("tenantId"):
        tags["idp.tenant_id"] = str(result["tenantId"])
    if result.get("correlationId"):
        tags["idp.correlation_id"] = str(result["correlationId"])
    return tags


class LoanAgents:
    def __init__(self, client: ClaudeClient, knowledge: Callable[[], KnowledgeClient] | None = None) -> None:
        self._client = client
        self._knowledge = knowledge

    def _policy(self, query: str | None) -> tuple[list[dict[str, str]], str]:
        """Policy rules for the agents.

        A question retrieves the most relevant rules from the Foundry IQ knowledge base. Review
        (query None) and questions that match nothing read every rule from the search index.
        The inline rules are used only when the knowledge layer is not ready.
        """
        if self._knowledge is not None:
            try:
                client = self._knowledge()
                if query is not None:
                    passages = client.retrieve_policy(query)
                    if passages:
                        return passages, "knowledge-base"
                passages = client.policy_rules()
                if passages:
                    return passages, "search-index"
            except KnowledgeError as exc:
                log.info("knowledge layer not used for policy: %s", exc)
            except Exception:
                log.warning("policy retrieval failed; using the inline policy", exc_info=True)
        inline = [{"title": f"Underwriting policy rule {number}", "content": rule}
                  for number, rule in enumerate(UNDERWRITING_RULES, start=1)]
        return inline, "inline"

    def ask(self, question: str, result: dict[str, Any]) -> dict[str, Any]:
        model = settings.claude_sonnet_deployment
        with agent_span("packet_qa", model=model, tags=_tags(result)) as span:
            policy, source = self._policy(question)
            span.set_attribute("idp.policy_source", source)
            user = (
                f"Packet:\n{packet_context(result)}\n\n"
                f"Policy passages:\n{numbered([p['content'] for p in policy])}\n\n"
                f"Question: {question}"
            )
            answer = self._client.complete(model, ASK_SYSTEM, user, max_tokens=600)
        return {"answer": answer.strip(), "model": model, "policySource": source}

    def ask_library(self, question: str, passages: list[dict[str, str]], tenant_id: str | None = None) -> dict[str, Any]:
        model = settings.claude_sonnet_deployment
        tags: dict[str, Any] = {"idp.passage_count": len(passages)}
        if tenant_id:
            tags["idp.tenant_id"] = tenant_id
        with agent_span("library_qa", model=model, tags=tags):
            listing = "\n\n".join(f"[P{n}] {p['title']}\n{p['content']}" for n, p in enumerate(passages, start=1))
            user = f"Passages:\n{listing or '(no passages found)'}\n\nQuestion: {question}"
            answer = self._client.complete(model, LIBRARY_SYSTEM, user, max_tokens=600)
        cited = [
            {"label": f"P{n}", "title": p["title"], "content": p["content"]}
            for n, p in enumerate(passages, start=1)
        ]
        return {"answer": answer.strip(), "model": model, "passages": cited}

    def review(self, result: dict[str, Any]) -> dict[str, Any]:
        tags = _tags(result)
        steps: list[dict[str, str]] = []
        with agent_span("underwriting_review", tags=tags) as root:
            context = packet_context(result)
            policy, source = self._policy(None)
            root.set_attribute("idp.policy_source", source)
            rules = numbered([p["content"] for p in policy])

            profile = self._step(steps, "Intake", settings.claude_haiku_deployment, INTAKE_SYSTEM,
                                 f"Packet:\n{context}", tags)
            checks = self._step(steps, "Compliance", settings.claude_sonnet_deployment,
                                COMPLIANCE_SYSTEM.format(policy=rules),
                                f"Applicant profile:\n{profile}\n\nPacket:\n{context}", tags)
            decision_text = self._step(steps, "Underwriter", settings.claude_sonnet_deployment, UNDERWRITER_SYSTEM,
                                       f"Applicant profile:\n{profile}\n\nCompliance checks:\n{checks}", tags)

            decision = parse_json(decision_text)
            recommendation = str(decision.get("recommendation", "refer")).lower()
            if recommendation not in ("approve", "refer", "decline"):
                recommendation = "refer"
            root.set_attribute("idp.recommendation", recommendation)
        return {
            "recommendation": recommendation,
            "rationale": decision.get("rationale") or decision_text,
            "conditions": decision.get("conditions") or [],
            "steps": steps,
            "policySource": source,
        }

    def _step(self, steps: list[dict[str, str]], agent: str, model: str, system: str, user: str,
              tags: dict[str, str]) -> str:
        with agent_span(agent.lower(), model=model, tags=tags):
            text = self._client.complete(model, system, user, max_tokens=2000).strip()
        steps.append({"agent": agent, "model": model, "output": text})
        return text


_agents: LoanAgents | None = None


def get_loan_agents() -> LoanAgents:
    global _agents
    if _agents is None:
        if not settings.claude_base_url:
            raise RuntimeError("CLAUDE_BASE_URL is not set")
        _agents = LoanAgents(ClaudeClient(settings.claude_base_url, DefaultAzureCredential()), knowledge=ready_knowledge)
    return _agents
