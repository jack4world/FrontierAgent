"""Live EDGAR regression for the tag map. OPT-IN — hits the network.

Run deliberately:

    EQUITY_RESEARCH_LIVE_EDGAR=1 SEC_EDGAR_USER_AGENT="you you@example.com" \
        uv run pytest tests/network -q

This is the only suite here that cannot be faked, because the failure it exists
to catch is a filer quietly switching the tag it reports a metric under. When
that happens the lookup keeps succeeding, the gate keeps passing, and it is now
verifying the wrong number — a verifier broken without saying so is worse than
no verifier. Only a live comparison notices.

It is kept out of the default run on purpose: in CI it would fail on SEC rate
limits, which says nothing about our code.
"""

from __future__ import annotations

import json
import os

import pytest

from plugins.tools.finance.edgar import fetch_xbrl_metric, load_tag_map

_ENABLED = os.environ.get("EQUITY_RESEARCH_LIVE_EDGAR", "").lower() in (
    "1", "true", "yes", "on",
)

_live_only = pytest.mark.skipif(
    not _ENABLED,
    reason="live EDGAR suite; set EQUITY_RESEARCH_LIVE_EDGAR=1 to run",
)


# The pins live HERE, not in the tag map.
#
# They used to sit beside each mapping, which put real financial values inside a
# file the agent loads as chain knowledge — and a live run duly read them out
# and emitted them as harvested market facts, graded `secondary` so the gate
# skipped the very numbers it could most easily have checked. Correct values,
# so nothing looked wrong; a stale pin would have been reported as current fact
# with no symptom at all. Test fixtures belong in the tests.
#
# Each entry: (ticker, metric, calendar_period, filed value).
_PINS: list[tuple[str, str, str, float]] = [
    ("GOOGL", "capex", "CY2026Q1", 35674000000),
    ("META", "capex", "CY2026Q1", 18997000000),
    ("MSFT", "capex", "CY2026Q1", 30876000000),
    ("MU", "capex", "CY2025Q4", 5389000000),
    ("MU", "revenue", "CY2026Q2", 41456000000),
    ("NVDA", "revenue", "CY2026Q2", 96221000000),
]


def _pinned_cases() -> list[tuple[str, str, str, float]]:
    """Pins whose mapping still exists, so a removed mapping fails loudly."""
    mappings = load_tag_map().get("metrics", {})
    return [
        pin for pin in _PINS
        if pin[0] in (mappings.get(pin[1], {}).get("companies") or {})
    ]


def test_every_pin_still_has_a_mapping() -> None:
    """A pin without a mapping is a silently skipped check."""
    mappings = load_tag_map().get("metrics", {})
    orphans = [
        (t, m) for t, m, _, _ in _PINS
        if t not in (mappings.get(m, {}).get("companies") or {})
    ]
    assert orphans == [], f"pins with no mapping: {orphans}"


@_live_only
@pytest.mark.parametrize(
    ("ticker", "metric", "calendar_period", "expected"), _pinned_cases(),
)
async def test_pinned_value_still_matches_the_filing(
    ticker: str, metric: str, calendar_period: str, expected: float,
) -> None:
    result = json.loads(
        await fetch_xbrl_metric.ainvoke({
            "ticker": ticker, "metric": metric, "calendar_period": calendar_period,
        })
    )

    assert result["status"] == "ok", result
    assert result["value"] == expected, (
        f"{ticker}/{metric} {calendar_period}: EDGAR now reports "
        f"{result['value']}, pinned value is {expected}. Either the filer "
        "restated, or the concept in the tag map is no longer the right one."
    )
