from app.agents import LoanAgents, packet_context, parse_json
from app.knowledge import KnowledgeError


class FakeClaude:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def complete(self, deployment, system, user, max_tokens=1024):
        self.calls.append((deployment, user, system))
        return self.replies.pop(0)


RESULT = {
    "segments": [
        {"doc_type": "paystub", "page_range": [1, 2],
         "documents": [{"fields": {"Gross Pay": {"value": "5200", "confidence": 0.9}}}]},
        {"doc_type": "w2", "page_range": [3, 3], "documents": []},
    ]
}


def test_packet_context_lists_segments_and_fields():
    text = packet_context(RESULT)
    assert "[S1] paystub (pages 1-2)" in text
    assert "- Gross Pay: 5200" in text
    assert "[S2] w2 (pages 3-3)" in text


def test_packet_context_handles_empty_result():
    assert packet_context({}) == "(no extracted fields)"


def test_parse_json_extracts_object_from_text():
    assert parse_json('Here: {"recommendation": "approve"} done') == {"recommendation": "approve"}
    assert parse_json("no json here") == {}


def test_ask_returns_answer_and_model():
    agents = LoanAgents(FakeClaude(["Monthly gross pay is 5200 [S1]."]))
    reply = agents.ask("What is the gross pay?", RESULT)
    assert reply["answer"] == "Monthly gross pay is 5200 [S1]."
    assert reply["model"]


def test_review_runs_three_agents_in_order():
    claude = FakeClaude([
        '{"applicant_name": "Jane"}',
        '{"checks": [{"rule": "paystubs", "status": "pass", "evidence": "S1"}]}',
        '{"recommendation": "approve", "rationale": "All checks pass.", "conditions": []}',
    ])
    review = LoanAgents(claude).review(RESULT)
    assert [s["agent"] for s in review["steps"]] == ["Intake", "Compliance", "Underwriter"]
    assert review["recommendation"] == "approve"
    assert review["rationale"] == "All checks pass."
    assert "Applicant profile" in claude.calls[2][1]


def test_review_normalizes_unknown_recommendation():
    claude = FakeClaude(["{}", "{}", '{"recommendation": "maybe"}'])
    review = LoanAgents(claude).review(RESULT)
    assert review["recommendation"] == "refer"


class FakeKnowledge:
    def __init__(self, passages=(), policy=None, error=None):
        self.passages = list(passages)
        self.policy = list(passages if policy is None else policy)
        self.error = error
        self.queries = []

    def retrieve_policy(self, query):
        self.queries.append(query)
        if self.error is not None:
            raise self.error
        return self.passages

    def policy_rules(self):
        if self.error is not None:
            raise self.error
        return self.policy


POLICY_PASSAGES = [{"title": "Underwriting policy rule 1", "content": "Two paystubs are required."}]


def test_review_reads_the_complete_policy_from_the_index():
    knowledge = FakeKnowledge(policy=POLICY_PASSAGES)
    claude = FakeClaude(["{}", "{}", '{"recommendation": "refer"}'])
    review = LoanAgents(claude, knowledge=lambda: knowledge).review(RESULT)
    assert review["policySource"] == "search-index"
    assert "[P1] Two paystubs are required." in claude.calls[1][2]
    assert knowledge.queries == []


def test_ask_falls_back_to_the_complete_policy_when_retrieval_matches_nothing():
    knowledge = FakeKnowledge(passages=[], policy=POLICY_PASSAGES)
    claude = FakeClaude(["Not covered by the policy [P1]."])
    reply = LoanAgents(claude, knowledge=lambda: knowledge).ask("Is there a pet policy?", RESULT)
    assert reply["policySource"] == "search-index"
    assert "[P1] Two paystubs are required." in claude.calls[0][1]


def test_review_falls_back_to_inline_policy_when_knowledge_fails():
    knowledge = FakeKnowledge(error=KnowledgeError("search is down"))
    claude = FakeClaude(["{}", "{}", '{"recommendation": "approve"}'])
    review = LoanAgents(claude, knowledge=lambda: knowledge).review(RESULT)
    assert review["policySource"] == "inline"
    assert "[P1] Both of the two most recent paystubs must be present." in claude.calls[1][2]
    assert review["recommendation"] == "approve"


def test_ask_uses_knowledge_base_policy_passages():
    claude = FakeClaude(["Gross pay is 5200 [S1]; two paystubs are required [P1]."])
    reply = LoanAgents(claude, knowledge=lambda: FakeKnowledge(POLICY_PASSAGES)).ask(
        "Are two paystubs needed?", RESULT)
    assert reply["policySource"] == "knowledge-base"
    assert "[P1] Two paystubs are required." in claude.calls[0][1]


def test_ask_library_labels_passages_for_citation():
    passages = [{"title": "packet.pdf: S1 paystub (pages 1-2)", "content": "Gross Pay: 5200"}]
    claude = FakeClaude(["Gross pay is 5200 [P1]."])
    reply = LoanAgents(claude).ask_library("What is the gross pay?", passages)
    assert reply["passages"] == [
        {"label": "P1", "title": "packet.pdf: S1 paystub (pages 1-2)", "content": "Gross Pay: 5200"}
    ]
    assert "[P1] packet.pdf: S1 paystub (pages 1-2)" in claude.calls[0][1]
