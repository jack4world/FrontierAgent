"""Grading secondary sources.

The gate cannot verify a secondary figure, but it can say what kind of place it
came from. Without that, a wire service and an unknown site carry the same
label in the report, and the reader has no way to weight them.
"""

from __future__ import annotations

from typing import Any

from workflows.equity_research.authority import grade_source
from workflows.equity_research.verification import verify


def test_a_wire_service_and_a_government_domain_are_graded_established() -> None:
    assert grade_source("https://www.reuters.com/technology/x") == "established"
    assert grade_source("https://www.sec.gov/Archives/edgar/data/1/x.htm") == "established"


def test_an_unknown_domain_is_unrated_not_condemned() -> None:
    # We have no basis to call it bad. Saying so is the honest label; grading it
    # "low" would be inventing a judgement we did not make.
    assert grade_source("https://siliconanalysts.test/tools/allocation") == "unrated"
    assert grade_source("") == "unrated"


def test_the_gate_stamps_a_grade_onto_secondary_facts() -> None:
    # Attached by the gate rather than by the harvest, so the model cannot
    # award authority to its own sources.
    rumour: dict[str, Any] = {
        "fact_id": "f-x-cowos-cy2026q2-0000abcd",
        "entity": "TSMC", "metric": "cowos_capacity", "value": 70_000,
        "unit": "wafers_per_month",
        "period": {"fiscal": "FY2026Q2", "calendar": "CY2026Q2"},
        "tier": "secondary",
        "source": {"url": "https://siliconanalysts.test/tools/allocation",
                   "accession": None, "concept": None, "verbatim": None},
        "as_of": "2026-08-26",
    }

    result = verify(
        [rumour], [],
        xbrl_lookup=lambda _f: None,
        source_text=lambda _f: "",
    )

    assert result.facts[0]["source"]["authority"] == "unrated"


def test_a_run_cut_short_says_so_at_the_top_of_its_report() -> None:
    # A report thin because the harvest crashed looks identical to one thin
    # because the evidence is genuinely scarce. The reader cannot tell those
    # apart, and they mean opposite things.
    from workflows.equity_research.sources import render_run_health

    banner = render_run_health("llm_error", "max_turns")

    assert "cut short" in banner
    assert "Harvest did not complete" in banner
    assert "Analysis did not complete" in banner
    assert render_run_health(None, None) == ""
