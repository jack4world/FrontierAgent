"""``compute_metric`` — arithmetic as a tool rather than as model output.

The question a transmission claim has to answer is a rate: does the upstream
node grow as fast as the driver? The first live run asserted that it did not,
qualitatively, without ever computing the comparison — and separately produced
an absolute increment that answered a different question.

When the model does arithmetic in prose, the number is an opinion that reads
like a calculation. Here it chooses the operands and the code computes, so the
result is correct by construction and carries its own provenance.

Two rules stop this from laundering anything:

- A derived figure inherits the **weakest** tier and basis of its inputs.
  Dividing two unverified numbers does not produce a verified one.
- Operands must share a unit. A growth rate computed from wafers over dollars
  is meaningless and would look exactly as authoritative as a real one.
"""

from __future__ import annotations

import json
import logging

from frontier_agent.core.tool import tool
from plugins.tools.finance.emit import _rejected, _slug, build_fact_id
from plugins.tools.finance.ledger import ledger_facts, record_fact

logger = logging.getLogger(__name__)

OPERATIONS = ("growth", "ratio", "difference")

# Weakest first — a derived fact takes the weakest of what it consumed.
_TIER_ORDER = ("secondary", "quoted_primary", "xbrl_verified")
_BASIS_ORDER = ("consensus", "derived", "guidance", "reported")


def _weakest(values: list[str], order: tuple[str, ...], default: str) -> str:
    present = [v for v in values if v in order]
    if not present:
        return default
    return min(present, key=order.index)


@tool
async def compute_metric(
    operation: str,
    fact_ids: list[str],
    metric: str,
    unit: str,
    note: str = "",
) -> str:
    """Compute a figure from facts already in the table, and record the result.

    Use this instead of doing arithmetic yourself. A transmission question is
    almost always a rate — "does upstream capacity grow as fast as the driver's
    guided revenue?" — and a rate has to be computed from two figures, not
    asserted. The result becomes a fact with its own id, so a claim can cite it
    and a reader can re-run it.

    Args:
        operation: ``growth`` for ``a / b - 1`` (a = later, b = earlier),
            ``ratio`` for ``a / b``, ``difference`` for ``a - b``.
        fact_ids: Exactly two ids, in the order the operation names. Both must
            already exist and share a unit.
        metric: Semantic name for the result, e.g. ``cowos_capacity_growth``.
        unit: Unit of the result — ``ratio`` for growth rates and ratios, or the
            shared input unit for a difference.
        note: Optional one-line statement of what the figure is for.

    Returns:
        JSON with the new ``fact_id``, or a rejection explaining what to fix.
    """
    if operation not in OPERATIONS:
        return _rejected(
            f"operation must be one of {list(OPERATIONS)}; got {operation!r}"
        )

    refs = list(fact_ids or [])
    if len(refs) != 2:
        return _rejected(
            f"`fact_ids` needs exactly two ids in operand order; got {len(refs)}"
        )

    table = {f["fact_id"]: f for f in ledger_facts()}
    unknown = [ref for ref in refs if ref not in table]
    if unknown:
        return _rejected(
            f"unknown fact_id(s): {unknown}. Compute only from figures already "
            "recorded with emit_fact."
        )

    left, right = table[refs[0]], table[refs[1]]
    if left.get("unit") != right.get("unit"):
        return _rejected(
            f"unit mismatch: {left.get('unit')!r} vs {right.get('unit')!r}. A "
            "figure computed across units has no meaning and would look exactly "
            "as authoritative as a real one."
        )

    a, b = float(left.get("value") or 0), float(right.get("value") or 0)
    if operation in ("growth", "ratio") and b == 0:
        return _rejected("denominator is zero; the result would be undefined")

    value = a - b if operation == "difference" else (
        a / b - 1 if operation == "growth" else a / b
    )

    # Identity includes the operands, so recomputing the same thing twice
    # collapses rather than accumulating near-duplicate derived figures.
    fact_id = build_fact_id(
        _slug(left.get("entity", "")), metric,
        (left.get("period") or {}).get("calendar", ""),
        f"derived:{operation}:{refs[0]}:{refs[1]}", "", "",
    )
    fact = {
        "fact_id": fact_id,
        "entity": left.get("entity"),
        "metric": metric,
        "value": value,
        "unit": unit,
        "period": left.get("period"),
        # A derived figure is only as good as what went into it.
        "tier": _weakest(
            [left.get("tier", ""), right.get("tier", "")], _TIER_ORDER, "secondary",
        ),
        "basis": "derived",
        "source": {
            "url": None, "accession": None, "concept": None, "verbatim": None,
            "authority": _weakest(
                [
                    (left.get("source") or {}).get("authority", ""),
                    (right.get("source") or {}).get("authority", ""),
                ],
                ("unrated", "established"), "unrated",
            ),
        },
        "derived_from": refs,
        "operation": operation,
        "note": note.strip(),
        "as_of": left.get("as_of"),
        "inputs_basis": _weakest(
            [left.get("basis", ""), right.get("basis", "")], _BASIS_ORDER, "reported",
        ),
    }

    existing = record_fact(fact)
    if existing is not None and existing.get("value") != value:
        return _rejected(
            f"a different value is already recorded for {fact_id}",
            recorded_value=existing.get("value"), submitted_value=value,
        )

    logger.info(
        "compute_metric %s = %s (%s of %s)", fact_id, value, operation, refs,
    )
    return json.dumps({
        "status": "recorded", "fact_id": fact_id, "value": value, "unit": unit,
    })
