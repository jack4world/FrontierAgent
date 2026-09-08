"""Arithmetic as a tool, not as model output.

The first run asserted that upstream capacity grew "more slowly in relative
terms" than the driver, without ever computing the comparison — and separately
produced an absolute increment that did not answer the question. When the model
does the arithmetic in prose, the number is an opinion. When the tool does it,
the model only chooses the operands.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest

from plugins.tools.finance.compute import compute_metric
from plugins.tools.finance.emit import emit_fact
from plugins.tools.finance.ledger import clear_ledger, ledger_facts


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    clear_ledger()
    yield
    clear_ledger()


async def _capacity(period: str, value: float, tier: str = "secondary") -> str:
    return json.loads(await emit_fact.ainvoke({
        "entity": "TSM", "metric": "cowos_capacity_target", "value": value,
        "unit": "wafers_per_month", "fiscal_period": period,
        "calendar_period": period, "tier": tier,
        "source_url": f"https://example-trade.test/{period}",
        "as_of": "2026-08-26",
        **({"concept": "X"} if tier == "xbrl_verified" else {}),
    }))["fact_id"]


async def test_growth_between_two_facts_is_computed_not_asserted() -> None:
    # 200,000 / 130,000 - 1 = +53.8%. The question "does upstream keep up with
    # a 70% guide" is answerable only as a rate, and only if something computes
    # the rate.
    earlier = await _capacity("CY2026Q4", 130_000)
    later = await _capacity("CY2027Q4", 200_000)

    result = json.loads(await compute_metric.ainvoke({
        "operation": "growth", "fact_ids": [later, earlier],
        "metric": "cowos_capacity_growth", "unit": "ratio",
    }))

    assert result["status"] == "recorded"
    derived = next(f for f in ledger_facts() if f["fact_id"] == result["fact_id"])
    assert round(derived["value"], 4) == 0.5385
    assert derived["basis"] == "derived"
    assert derived["derived_from"] == [later, earlier]


async def test_a_derived_figure_inherits_the_weakest_input() -> None:
    # Dividing two unverified numbers does not produce a verified one.
    # Arithmetic must not launder provenance.
    hard = await _capacity("CY2026Q4", 130_000, tier="xbrl_verified")
    soft = await _capacity("CY2027Q4", 200_000, tier="secondary")

    result = json.loads(await compute_metric.ainvoke({
        "operation": "ratio", "fact_ids": [soft, hard],
        "metric": "capacity_ratio", "unit": "ratio",
    }))

    derived = next(f for f in ledger_facts() if f["fact_id"] == result["fact_id"])
    assert derived["tier"] == "secondary"


async def test_computing_from_an_unknown_fact_is_rejected() -> None:
    result = json.loads(await compute_metric.ainvoke({
        "operation": "growth", "fact_ids": ["f-nope-0000abcd", "f-nope-0000dcba"],
        "metric": "x", "unit": "ratio",
    }))

    assert result["status"] == "rejected"
    assert "f-nope-0000abcd" in result["error"]


async def test_dividing_by_zero_is_refused_rather_than_crashing() -> None:
    zero = await _capacity("CY2026Q4", 0)
    other = await _capacity("CY2027Q4", 200_000)

    result = json.loads(await compute_metric.ainvoke({
        "operation": "growth", "fact_ids": [other, zero],
        "metric": "x", "unit": "ratio",
    }))

    assert result["status"] == "rejected"
    assert "zero" in result["error"].lower()


async def test_mismatched_units_are_refused() -> None:
    # Comparing wafers to dollars produces a number with no meaning, and the
    # number would look exactly as authoritative as a real one.
    wafers = await _capacity("CY2026Q4", 130_000)
    dollars = json.loads(await emit_fact.ainvoke({
        "entity": "TSM", "metric": "revenue", "value": 30_000_000_000,
        "unit": "USD", "fiscal_period": "CY2026Q4", "calendar_period": "CY2026Q4",
        "tier": "secondary", "source_url": "https://example-trade.test/rev",
        "as_of": "2026-08-26",
    }))["fact_id"]

    result = json.loads(await compute_metric.ainvoke({
        "operation": "growth", "fact_ids": [dollars, wafers],
        "metric": "x", "unit": "ratio",
    }))

    assert result["status"] == "rejected"
    assert "unit" in result["error"].lower()
