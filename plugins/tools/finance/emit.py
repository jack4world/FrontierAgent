"""``emit_fact`` / ``emit_claim`` — structured emission of the two tables.

Records are emitted one tool call at a time rather than as a JSON block at the
end of the report. Whether the model can fill this schema reliably is the
load-bearing assumption of the whole design, so it has to be observable from
day one: every call lands in the trace as a success or a rejection, which is
what makes fill rate and drift-onset measurable rather than anecdotal. A
trailing JSON block would only fail to parse hundreds of turns later, after
compaction has already eaten the context needed to refill it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re

from frontier_agent.core.tool import tool
from plugins.tools.finance.ledger import (
    known_fact_ids,
    ledger_claims,
    ledger_facts,
    record_claim,
    record_fact,
)

logger = logging.getLogger(__name__)

TIERS = ("xbrl_verified", "quoted_primary", "secondary")

# What kind of number this is, orthogonal to how verifiable it is. A street
# estimate can be impeccably sourced and still be nobody's observation of
# anything; keeping basis separate from tier is what stops a well-sourced
# expectation from reading as a measurement.
BASES = ("reported", "guidance", "consensus")

_SLUG_RE = re.compile(r"[^a-z0-9]+")
# fact ids are minted by build_fact_id, so this is the shape a derivation
# step must contain for the arithmetic to be traceable to a source.
_FACT_REF_RE = re.compile(r"f-[a-z0-9-]+-[0-9a-f]{8}")


def _rejected(error: str, **extra: object) -> str:
    return json.dumps({"status": "rejected", "error": error, **extra})


def _slug(text: str) -> str:
    return _SLUG_RE.sub("-", (text or "").lower()).strip("-")


def build_fact_id(
    entity: str, metric: str, calendar_period: str, source_url: str,
    accession: str, concept: str,
) -> str:
    """Derive a fact's id from what identifies it, not from who emitted it.

    The hash covers identity — entity, metric, period, source — and pointedly
    NOT the value. Two agents citing the same number land on the same id and
    collapse into one entry; two agents citing *different* numbers for the same
    identity collide instead of quietly coexisting, which is what surfaces the
    disagreement rather than averaging it away.
    """
    identity = "|".join([
        _slug(entity), _slug(metric), _slug(calendar_period),
        (source_url or "").strip(), (accession or "").strip(),
        (concept or "").strip(),
    ])
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:8]
    return f"f-{_slug(entity)}-{_slug(metric)}-{_slug(calendar_period)}-{digest}"


@tool
async def emit_fact(
    entity: str,
    metric: str,
    value: float,
    unit: str,
    fiscal_period: str,
    calendar_period: str,
    tier: str,
    source_url: str,
    as_of: str,
    accession: str = "",
    concept: str = "",
    verbatim: str = "",
    basis: str = "reported",
) -> str:
    """Record one number in the facts table. Emit every number you rely on.

    Never write a number into prose without emitting it here first: claims cite
    facts by id, and an uncited number cannot be verified.

    Args:
        entity: Ticker (``META``) or, for a non-listed link in the chain, its
            node name (``cowos_packaging``).
        metric: Semantic name (``capex``), not a us-gaap tag.
        value: The number itself, in ``unit``.
        unit: ``USD``, ``wafers_per_month``, ``percent``, …
        fiscal_period: The company's own period label (``FY2025Q3``).
        calendar_period: The calendar period (``CY2025Q3``). These differ for
            anyone whose fiscal year is not the calendar year; never collapse
            them, or cross-company comparison silently comes out wrong.
        tier: ``xbrl_verified`` for a reported historical actual,
            ``quoted_primary`` for guidance or any figure whose only first-party
            home is a filing's prose, ``secondary`` for news and supply-chain
            reporting.
        source_url: Where the number came from. Must be fetchable.
        as_of: Publication date of the source (``YYYY-MM-DD``).
        accession: SEC accession number, when the source is a filing.
        concept: The us-gaap tag, required for ``xbrl_verified``.
        verbatim: The exact sentence containing the number. **Required for
            ``quoted_primary``** — the gate re-fetches the source and checks
            this string appears in it, which is what makes an invented
            quotation impossible to pass off.
        basis: What kind of number this is. ``reported`` for something that
            happened, ``guidance`` for the company's own forward statement,
            ``consensus`` for what the market expects (street estimates,
            published forecasts). Record consensus figures whenever you find
            them: a view is only differentiated relative to what is already
            expected, and nothing else in this system knows what that is.

    Returns:
        JSON with the assigned ``fact_id``, or a rejection explaining what to fix.
    """
    if tier not in TIERS:
        return _rejected(f"tier must be one of {list(TIERS)}; got {tier!r}")

    if basis not in BASES:
        return _rejected(f"basis must be one of {list(BASES)}; got {basis!r}")

    if tier == "quoted_primary" and not (verbatim or "").strip():
        return _rejected(
                "tier=quoted_primary requires `verbatim`: the exact sentence "
                "from the source containing this number."
            )

    if tier == "xbrl_verified" and not (concept or "").strip():
        return _rejected(
                "tier=xbrl_verified requires `concept`: the us-gaap tag the "
                "value was read from."
            )

    fact_id = build_fact_id(
        entity, metric, calendar_period, source_url, accession, concept,
    )
    fact = {
        "fact_id": fact_id,
        "entity": entity,
        "metric": metric,
        "value": value,
        "unit": unit,
        "period": {"fiscal": fiscal_period, "calendar": calendar_period},
        "tier": tier,
        "basis": basis,
        "source": {
            "url": source_url,
            "accession": accession or None,
            "concept": concept or None,
            "verbatim": verbatim or None,
        },
        "as_of": as_of,
    }

    existing = record_fact(fact)
    if existing is None:
        logger.info("emit_fact %s recorded", fact_id)
        return json.dumps({
            "status": "recorded", "fact_id": fact_id, "deduplicated": False,
        })

    if existing["value"] == value:
        logger.info("emit_fact %s deduplicated", fact_id)
        return json.dumps({
            "status": "recorded", "fact_id": fact_id, "deduplicated": True,
        })

    logger.warning(
        "emit_fact %s conflict: recorded=%s submitted=%s",
        fact_id, existing["value"], value,
    )
    return json.dumps({
        "status": "conflict",
        "fact_id": fact_id,
        "recorded_value": existing["value"],
        "submitted_value": value,
        "error": (
            "This entity/metric/period/source already holds a different value. "
            "The ledger was NOT changed. Re-read the source and either correct "
            "your number or emit under the source you actually used."
        ),
    })


@tool
async def emit_claim(
    edge_from: str,
    edge_to: str,
    statement: str,
    depends_on: list[str],
    falsification: str,
    counter_evidence: list[str] | None = None,
    lag_quarters: int = 0,
    impact_value: float | None = None,
    impact_unit: str = "",
    derivation: list[str] | None = None,
    consensus_refs: list[str] | None = None,
    consensus_delta: str = "",
) -> str:
    """Record one transmission claim. Cite facts by id; never restate a number.

    Args:
        edge_from: Upstream node of the supply-chain edge (``csp_capex``).
        edge_to: Downstream node (``optical_modules``).
        statement: The claim in prose. Refer to figures by what they are, not
            by repeating the digits — the digits live in the facts table.
        depends_on: ``fact_id`` values this claim rests on. Every id must have
            been issued by ``emit_fact``; anything else is rejected.
        falsification: What observation would show this claim to be wrong.
            Required. A claim that cannot be disproved cannot be monitored, and
            cannot be told apart from consensus restated confidently.
        counter_evidence: ``fact_id`` values that cut against this claim.
            **Required, and checked against the ledger like ``depends_on``.**
            You are responsible for the evidence against your own link — if you
            looked and genuinely found none, emit the fact that demonstrates the
            absence and cite that.
        lag_quarters: Expected transmission lag, in quarters.
        impact_value: Quantified downstream impact. Optional — and only allowed
            alongside ``derivation``.
        impact_unit: Unit for ``impact_value``.
        consensus_refs: ``fact_id`` values whose ``basis`` is ``consensus`` —
            what the market already expects on this edge.
        consensus_delta: How your view departs from those expectations. Required
            whenever ``consensus_refs`` is given. Naming what the market thinks
            and then not saying how you differ is how a report agrees with
            everyone while sounding independent. "We agree with the street" is
            an acceptable answer; saying nothing is not.
        derivation: The arithmetic, one step per entry. **Every step must
            contain at least one ``fact_id``**, or the step is rejected — a step
            without one cannot be re-checked. Required whenever ``impact_value``
            is given. If you cannot show the steps, leave the number out and let
            the claim stand on direction alone — that is the honest answer, and
            it is accepted.

    Returns:
        JSON with the assigned ``claim_id``, or a rejection explaining what to fix.
    """
    if not (falsification or "").strip():
        return _rejected(
                "`falsification` is required: state what observation would "
                "show this claim to be wrong."
            )

    refs = list(depends_on or [])
    facts_by_id = {f["fact_id"]: f for f in ledger_facts()}
    known = known_fact_ids()
    unknown = [ref for ref in refs if ref not in known]
    if unknown:
        return _rejected(
                f"unknown fact_id(s): {unknown}. Claims may only cite ids "
                "returned by emit_fact. Emit the number as a fact first."
            )

    against = list(counter_evidence or [])
    if not against:
        return _rejected(
            "`counter_evidence` is required: cite the fact_id(s) that cut "
            "against this claim. You know this link best, so you are the one "
            "positioned to say where it breaks. If you looked and found "
            "nothing, emit the fact that shows the absence and cite that."
        )

    unknown_against = [ref for ref in against if ref not in known]
    if unknown_against:
        return _rejected(
            f"unknown counter_evidence fact_id(s): {unknown_against}. "
            "Counter-evidence is held to the same standard as support: emit "
            "the sourced fact first, then cite it."
        )

    market = list(consensus_refs or [])
    if market:
        if not (consensus_delta or "").strip():
            return _rejected(
                "`consensus_delta` is required whenever you cite "
                "`consensus_refs`: say how your view departs from what the "
                "market already expects. Agreeing is a valid answer; silence "
                "is not."
            )
        unknown_market = [ref for ref in market if ref not in known]
        if unknown_market:
            return _rejected(f"unknown consensus fact_id(s): {unknown_market}.")
        mislabelled = [
            ref for ref in market
            if (facts_by_id.get(ref) or {}).get("basis") != "consensus"
        ]
        if mislabelled:
            return _rejected(
                f"these fact_id(s) are not consensus figures: {mislabelled}. "
                "Their `basis` must be 'consensus'. Otherwise any convenient "
                "number can be relabelled as the market's view and a claim can "
                "manufacture its own differentiation."
            )

    steps = list(derivation or [])
    if impact_value is not None and not steps:
        return _rejected(
            "`impact_value` requires `derivation`: show each arithmetic step "
            "and the fact_id it consumes. Without the path, drop the number "
            "and state direction only."
        )

    ungrounded = [step for step in steps if not _FACT_REF_RE.search(step)]
    if ungrounded:
        return _rejected(
            f"these `derivation` steps cite no fact_id: {ungrounded}. Each "
            "step must name the fact it consumes, or the arithmetic cannot be "
            "re-checked and the number is prose with digits in it."
        )

    claim_id = f"c-{len(ledger_claims()) + 1:03d}"
    claim = {
        "claim_id": claim_id,
        "edge": {"from": edge_from, "to": edge_to},
        "statement": statement,
        "depends_on": refs,
        "derivation": steps,
        "impact": (
            None if impact_value is None
            else {"value": impact_value, "unit": impact_unit}
        ),
        "lag": {"quarters": lag_quarters, "basis": refs[0] if refs else None},
        "falsification": falsification.strip(),
        "counter_evidence": against,
        "consensus_refs": market,
        "consensus_delta": (consensus_delta or "").strip(),
    }

    record_claim(claim)
    logger.info("emit_claim %s (%s -> %s)", claim_id, edge_from, edge_to)
    return json.dumps({"status": "recorded", "claim_id": claim_id})
