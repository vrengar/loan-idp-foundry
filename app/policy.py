"""Underwriting policy rules. Indexed into the Foundry IQ knowledge base at startup and
used inline by the agents only when the knowledge base is not ready."""

UNDERWRITING_RULES: tuple[str, ...] = (
    "Both of the two most recent paystubs must be present.",
    "Monthly gross income must be supported by the paystubs.",
    "Bank statements must cover at least two consecutive months.",
    "A government photo ID (passport or driver's license) must be present.",
    "Salaried applicants need a W-2 for the most recent tax year.",
    "Any required document that is missing means the recommendation is refer.",
)
