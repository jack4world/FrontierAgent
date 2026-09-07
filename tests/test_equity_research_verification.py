"""Seam C — the deterministic verification gate.

``verify`` is a pure function: two tables in, an injected XBRL lookup and an
injected source-text reader, verdicts out. No LLM, no network, no agent loop.
That is the whole point of making the gate deterministic rather than an agent —
the part most worth testing is also the cheapest part to test.
"""

from __future__ import annotations

from typing import Any

from workflows.equity_research.verification import Verdict, verify


def _fact(**overrides: Any) -> dict[str, Any]:
    """An ``xbrl_verified`` fact, shaped as the spec's schema."""
    fact: dict[str, Any] = {
        "fact_id": "f-meta-capex-2025q3-a3f9",
        "entity": "META",
        "metric": "capex",
        "value": 37_400_000_000,
        "unit": "USD",
        "period": {"fiscal": "FY2025Q3", "calendar": "CY2025Q3"},
        "tier": "xbrl_verified",
        "source": {
            "url": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany",
            "accession": "0001326801-25-000090",
            "concept": "PaymentsToAcquirePropertyPlantAndEquipment",
            "verbatim": None,
        },
        "as_of": "2025-10-29",
    }
    fact.update(overrides)
    return fact


def test_xbrl_fact_disagreeing_with_the_filing_is_corrected_to_the_filed_value() -> None:
    # The model wrote $38B; the filing says $37.4B. This is the "restatement
    # drift" failure the two-table split exists to catch.
    reported = _fact(value=38_000_000_000)
    filed = 37_400_000_000

    result = verify(
        [reported],
        [],
        xbrl_lookup=lambda _fact: filed,
        source_text=lambda _fact: "",
    )

    assert result.facts[0]["value"] == filed

    finding = result.findings[0]
    assert finding.verdict is Verdict.CORRECTED
    assert finding.reported_value == 38_000_000_000
    assert finding.authoritative_value == filed


def _guidance_fact(**overrides: Any) -> dict[str, Any]:
    """A ``quoted_primary`` fact — forward guidance, which XBRL does not carry."""
    return _fact(
        fact_id="f-meta-capex-guidance-2026-b7c1",
        metric="capex_guidance_low",
        value=38_000_000_000,
        period={"fiscal": "FY2026", "calendar": "CY2026"},
        tier="quoted_primary",
        source={
            "url": "https://www.sec.gov/Archives/edgar/data/1326801/8-k.htm",
            "accession": "0001326801-25-000091",
            "concept": None,
            "verbatim": "2026 capital expenditures of $38 billion",
        },
        **overrides,
    )


def test_quoted_primary_fact_whose_verbatim_is_absent_from_the_source_is_deleted() -> None:
    # The press release says $40-45B; the quoted sentence was never written.
    # A fabricated citation cannot be "corrected" — there is no way to know
    # what the author meant — so the assertion is removed outright.
    fabricated = _guidance_fact()
    press_release = (
        "We expect 2026 capital expenditures to be in the range of "
        "$40-45 billion, driven by investments in AI infrastructure."
    )

    def _never(fact: dict[str, Any]) -> float | None:
        raise AssertionError("guidance has no XBRL counterpart; do not hard-gate it")

    result = verify(
        [fabricated],
        [],
        xbrl_lookup=_never,
        source_text=lambda _fact: press_release,
    )

    assert result.facts == []
    assert result.findings[0].verdict is Verdict.DELETED


def test_quoted_primary_fact_whose_verbatim_appears_in_the_source_is_kept() -> None:
    quoted = _guidance_fact()
    press_release = (
        "We expect 2026 capital expenditures of $38 billion at the low end "
        "of our range."
    )

    result = verify(
        [quoted],
        [],
        xbrl_lookup=lambda _fact: None,
        source_text=lambda _fact: press_release,
    )

    assert [f["fact_id"] for f in result.facts] == ["f-meta-capex-guidance-2026-b7c1"]
    assert result.findings[0].verdict is Verdict.QUOTE_CONFIRMED


def test_secondary_fact_is_annotated_and_left_untouched() -> None:
    # TSMC monthly capacity has no first-party counterpart at all: it files
    # 20-F annually and publishes monthly revenue to the TWSE, not the SEC.
    # Grading it as unverified is the honest outcome — dropping it would lose
    # the highest-frequency node on the chain.
    rumour = _fact(
        fact_id="f-tsmc-cowos-2025q4-c4d2",
        entity="TSMC",
        metric="cowos_capacity",
        value=70_000,
        unit="wafers_per_month",
        tier="secondary",
        source={
            "url": "https://example-supply-chain-news.test/cowos",
            "accession": None,
            "concept": None,
            "verbatim": None,
        },
    )

    def _never(fact: dict[str, Any]) -> Any:
        raise AssertionError("secondary sources are annotated, never gated")

    result = verify([rumour], [], xbrl_lookup=_never, source_text=_never)

    assert result.facts[0]["value"] == 70_000
    assert result.findings[0].verdict is Verdict.UNVERIFIED


def _claim(**overrides: Any) -> dict[str, Any]:
    claim: dict[str, Any] = {
        "claim_id": "c-001",
        "edge": {"from": "csp_capex", "to": "optical_modules"},
        "statement": "Raised CSP capex pulls forward 800G module orders.",
        "depends_on": ["f-meta-capex-2025q3-a3f9"],
        "derivation": [],
        "lag": {"quarters": 2, "basis": "f-meta-capex-2025q3-a3f9"},
        "falsification": "No sequential growth in Q1 datacom revenue.",
        "counter_evidence": [],
    }
    claim.update(overrides)
    return claim


def test_claim_resting_on_a_deleted_fact_is_flagged_unsupported() -> None:
    # The claim survives, but it is marked: one of its supports turned out to
    # be fabricated, so a reader must not treat it as evidenced.
    fabricated = _guidance_fact()
    surviving = _fact()
    claim = _claim(depends_on=[fabricated["fact_id"], surviving["fact_id"]])

    result = verify(
        [fabricated, surviving],
        [claim],
        xbrl_lookup=lambda f: f["value"],
        source_text=lambda _fact: "an unrelated press release",
    )

    assert result.claims[0]["unsupported_by"] == ["f-meta-capex-guidance-2026-b7c1"]


def test_claim_citing_only_surviving_facts_is_left_alone() -> None:
    surviving = _fact()
    claim = _claim(depends_on=[surviving["fact_id"]])

    result = verify(
        [surviving],
        [claim],
        xbrl_lookup=lambda f: f["value"],
        source_text=lambda _fact: "",
    )

    assert result.claims[0]["unsupported_by"] == []
