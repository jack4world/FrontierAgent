"""The deterministic verification gate.

``verify`` is a pure function. It takes the ``facts`` and ``claims`` tables, an
injected XBRL lookup, and an injected source-text reader; it returns the
post-gate tables plus one finding per fact. It never calls an LLM and never
touches the network.

That is deliberate. A verifier that is itself an LLM hallucinates numbers — so
using one to eliminate number hallucination is self-defeating. Because ``claims``
carry ``fact_id`` references instead of inlined numbers, checking a number
degenerates to ``reported == authoritative``, which is code's job, not a model's.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

Fact = dict[str, Any]
Claim = dict[str, Any]

# (fact) -> the authoritative value from XBRL, or None when the concept /
# period is not reported.
XbrlLookup = Callable[[Fact], float | None]
# (fact) -> the text of the cited source, or "" when it cannot be fetched.
SourceText = Callable[[Fact], str]


class Verdict(Enum):
    """What the gate decided about one fact."""

    MATCH = "match"
    CORRECTED = "corrected"
    QUOTE_CONFIRMED = "quote_confirmed"
    DELETED = "deleted"
    UNVERIFIED = "unverified"


@dataclass(frozen=True)
class Finding:
    """The gate's decision about a single fact."""

    fact_id: str
    verdict: Verdict
    reported_value: float | None = None
    authoritative_value: float | None = None


@dataclass
class VerificationResult:
    """Post-gate tables and the findings that produced them."""

    facts: list[Fact] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)


def verify(
    facts: list[Fact],
    claims: list[Claim],
    *,
    xbrl_lookup: XbrlLookup,
    source_text: SourceText,
) -> VerificationResult:
    """Run every fact through the gate for its source tier."""
    out_facts: list[Fact] = []
    findings: list[Finding] = []

    for fact in facts:
        tier = fact.get("tier")
        if tier == "quoted_primary":
            checked, finding = _check_quote(fact, source_text)
        elif tier == "secondary":
            # Nothing to check against. Say so and keep the number — the
            # highest-frequency nodes on the chain live here, and dropping
            # them would cost more than labelling them.
            checked, finding = fact, Finding(
                fact_id=fact["fact_id"], verdict=Verdict.UNVERIFIED,
            )
        else:
            checked, finding = _check_xbrl(fact, xbrl_lookup)
        findings.append(finding)
        if checked is not None:
            out_facts.append(checked)

    survivors = {fact["fact_id"] for fact in out_facts}
    out_claims = [_mark_unsupported(claim, survivors) for claim in claims]

    return VerificationResult(
        facts=out_facts, claims=out_claims, findings=findings,
    )


def _mark_unsupported(claim: Claim, survivors: set[str]) -> Claim:
    """Record which of a claim's supports did not survive the gate.

    The claim is kept rather than dropped: partial support is a judgement the
    reader has to make, and silently removing reasoning would hide that a
    citation was fabricated. Flagging keeps it visible.
    """
    missing = [
        fact_id
        for fact_id in claim.get("depends_on", [])
        if fact_id not in survivors
    ]
    return {**claim, "unsupported_by": missing}


def _normalize(text: str) -> str:
    """Collapse whitespace and case so formatting is not mistaken for fabrication."""
    return " ".join(text.split()).casefold()


def _check_quote(fact: Fact, source_text: SourceText) -> tuple[Fact | None, Finding]:
    """Confirm the cited sentence exists verbatim in the cited source.

    This gate cannot tell whether guidance is *right* — XBRL carries only
    reported actuals, so forward guidance has no authoritative counterpart. It
    can tell whether the quote was *invented*, which is the failure that string
    comparison eliminates outright. A fabricated citation is deleted rather than
    corrected: there is no correct value to substitute.
    """
    verbatim = (fact.get("source") or {}).get("verbatim") or ""
    body = source_text(fact)

    if verbatim and _normalize(verbatim) in _normalize(body):
        return fact, Finding(fact_id=fact["fact_id"], verdict=Verdict.QUOTE_CONFIRMED)

    return None, Finding(fact_id=fact["fact_id"], verdict=Verdict.DELETED)


def _check_xbrl(fact: Fact, lookup: XbrlLookup) -> tuple[Fact, Finding]:
    """Compare a reported value against the filed value; the filing wins.

    Correcting rather than re-running is deliberate: a wrong number here got
    that way through restatement drift, so a re-run would drift again — while
    the correct value is already in the gate's hand.
    """
    reported = fact.get("value")
    authoritative = lookup(fact)

    if authoritative == reported:
        return fact, Finding(
            fact_id=fact["fact_id"],
            verdict=Verdict.MATCH,
            reported_value=reported,
            authoritative_value=authoritative,
        )

    corrected = {**fact, "value": authoritative}
    return corrected, Finding(
        fact_id=fact["fact_id"],
        verdict=Verdict.CORRECTED,
        reported_value=reported,
        authoritative_value=authoritative,
    )
