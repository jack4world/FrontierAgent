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

pytestmark = pytest.mark.skipif(
    not _ENABLED,
    reason="live EDGAR suite; set EQUITY_RESEARCH_LIVE_EDGAR=1 to run",
)


def _pinned_cases() -> list[tuple[str, str, str, float]]:
    """Every mapping that has a pinned known-good value."""
    cases: list[tuple[str, str, str, float]] = []
    for metric, mapping in load_tag_map().get("metrics", {}).items():
        for ticker, entry in mapping.get("companies", {}).items():
            regression = entry.get("regression")
            if not isinstance(regression, dict):
                continue  # not yet calibrated against a real filing
            cases.append((
                ticker, metric,
                regression["calendar_period"], regression["value"],
            ))
    return cases


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
