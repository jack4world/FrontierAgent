"""Seam A — the finance tools, exercised through ``Tool.ainvoke``.

Same shape as ``tests/test_network_tools.py``: invoke the registered tool with a
dict, parse the JSON it returns, and replace network dependencies at the module
boundary. No test here touches the network.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest

from plugins.tools.finance import edgar
from plugins.tools.finance.emit import emit_claim, emit_fact
from plugins.tools.finance.ledger import clear_ledger, ledger_claims, ledger_facts


@pytest.fixture(autouse=True)
def _clean_ledger() -> Iterator[None]:
    clear_ledger()
    yield
    clear_ledger()


def _fact_args(**overrides: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "entity": "META",
        "metric": "capex",
        "value": 37_400_000_000,
        "unit": "USD",
        "fiscal_period": "FY2025Q3",
        "calendar_period": "CY2025Q3",
        "tier": "xbrl_verified",
        "source_url": "https://www.sec.gov/Archives/edgar/data/1326801/10-q.htm",
        "accession": "0001326801-25-000090",
        "concept": "PaymentsToAcquirePropertyPlantAndEquipment",
        "as_of": "2025-10-29",
    }
    args.update(overrides)
    return args


async def test_the_same_number_emitted_twice_collapses_to_one_ledger_entry() -> None:
    # Two Analysts working different links of the chain both cite Meta's Q3
    # capex. Model-chosen ids would collide or diverge; a content hash makes
    # dedupe a property of the data rather than of the model's discipline.
    first = json.loads(await emit_fact.ainvoke(_fact_args()))
    second = json.loads(await emit_fact.ainvoke(_fact_args()))

    assert first["fact_id"] == second["fact_id"]
    assert len(ledger_facts()) == 1


async def test_fact_id_carries_a_readable_prefix() -> None:
    emitted = json.loads(await emit_fact.ainvoke(_fact_args()))

    assert emitted["fact_id"].startswith("f-meta-capex-cy2025q3-")


async def test_a_different_period_is_a_different_fact() -> None:
    q3 = json.loads(await emit_fact.ainvoke(_fact_args()))
    q2 = json.loads(
        await emit_fact.ainvoke(
            _fact_args(fiscal_period="FY2025Q2", calendar_period="CY2025Q2")
        )
    )

    assert q3["fact_id"] != q2["fact_id"]
    assert len(ledger_facts()) == 2


async def test_disagreeing_values_for_one_identity_surface_as_a_conflict() -> None:
    # Same company, metric, period and source — but two different numbers.
    # Averaging or last-write-wins would bury the disagreement; the id
    # collision is what forces it into the open.
    await emit_fact.ainvoke(_fact_args(value=37_400_000_000))
    clash = json.loads(await emit_fact.ainvoke(_fact_args(value=38_000_000_000)))

    assert clash["status"] == "conflict"
    assert clash["recorded_value"] == 37_400_000_000
    assert clash["submitted_value"] == 38_000_000_000

    # The first writer stands; the ledger never holds two values for one id.
    assert len(ledger_facts()) == 1
    assert ledger_facts()[0]["value"] == 37_400_000_000


def _claim_args(**overrides: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "edge_from": "csp_capex",
        "edge_to": "optical_modules",
        "statement": "Raised CSP capex pulls forward 800G module orders.",
        "depends_on": [],
        "falsification": "No sequential growth in Q1 datacom revenue.",
        "lag_quarters": 2,
    }
    args.update(overrides)
    return args


async def _emit_supporting_fact() -> str:
    return json.loads(await emit_fact.ainvoke(_fact_args()))["fact_id"]


async def test_a_claim_citing_an_unknown_fact_is_rejected() -> None:
    # Ids are minted by the ledger, so an id it has never issued is one the
    # model invented — the exact move the two-table split exists to block.
    rejected = json.loads(
        await emit_claim.ainvoke(_claim_args(depends_on=["f-meta-capex-cy2025q3-deadbeef"]))
    )

    assert rejected["status"] == "rejected"
    assert "f-meta-capex-cy2025q3-deadbeef" in rejected["error"]
    assert ledger_claims() == []


async def test_a_claim_without_a_falsification_condition_is_rejected() -> None:
    # A claim that cannot say what would prove it wrong cannot be monitored
    # later, and cannot be told apart from consensus restated.
    fact_id = await _emit_supporting_fact()

    rejected = json.loads(
        await emit_claim.ainvoke(
            _claim_args(depends_on=[fact_id], falsification="  ")
        )
    )

    assert rejected["status"] == "rejected"
    assert "falsification" in rejected["error"]


async def test_a_quantified_claim_without_a_derivation_path_is_rejected() -> None:
    # Naming a number is cheap and the model will happily do it. Requiring the
    # arithmetic is what stops false precision: the figure is only allowed when
    # each step is attached to a fact the gate can re-check.
    fact_id = await _emit_supporting_fact()

    rejected = json.loads(
        await emit_claim.ainvoke(
            _claim_args(
                depends_on=[fact_id],
                impact_value=300_000_000,
                impact_unit="USD",
                derivation=[],
            )
        )
    )

    assert rejected["status"] == "rejected"
    assert "derivation" in rejected["error"]


async def test_a_directional_claim_needs_no_derivation() -> None:
    # The honest downgrade: no number, no path required.
    fact_id = await _emit_supporting_fact()

    recorded = json.loads(
        await emit_claim.ainvoke(_claim_args(depends_on=[fact_id]))
    )

    assert recorded["status"] == "recorded"
    claim = ledger_claims()[0]
    assert claim["claim_id"] == recorded["claim_id"]
    assert claim["depends_on"] == [fact_id]
    assert claim["edge"] == {"from": "csp_capex", "to": "optical_modules"}
    assert claim["impact"] is None


# ── EDGAR XBRL ────────────────────────────────────────────────────────────

# Shape of one us-gaap concept response, trimmed to the fields the tool reads.
_META_CAPEX_CONCEPT = {
    "cik": 1326801,
    "tag": "PaymentsToAcquirePropertyPlantAndEquipment",
    "units": {
        "USD": [
            {
                "start": "2025-04-01", "end": "2025-06-30", "val": 1,
                "fy": 2025, "fp": "Q2", "form": "10-Q", "frame": "CY2025Q2",
                "accn": "0001326801-25-000070",
            },
            {
                "start": "2025-07-01", "end": "2025-09-30", "val": 2,
                "fy": 2025, "fp": "Q3", "form": "10-Q", "frame": "CY2025Q3",
                "accn": "0001326801-25-000090",
            },
        ],
    },
}


async def test_a_semantic_metric_is_resolved_through_the_tag_map(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The caller asks for "capex", never for a us-gaap tag. Which tag that
    # means is a per-company judgement and lives in the mapping file.
    captured: dict[str, str] = {}

    async def fake_get(url: str) -> dict[str, Any]:
        captured["url"] = url
        return _META_CAPEX_CONCEPT

    monkeypatch.setattr(edgar, "_get_json", fake_get)

    result = json.loads(
        await edgar.fetch_xbrl_metric.ainvoke(
            {"ticker": "META", "metric": "capex", "calendar_period": "CY2025Q3"}
        )
    )

    assert result["status"] == "ok"
    assert result["concept"] == "PaymentsToAcquirePropertyPlantAndEquipment"
    assert result["value"] == 2
    assert result["accession"] == "0001326801-25-000090"
    assert "CIK0001326801" in captured["url"]


async def test_both_period_labels_are_returned_and_never_collapsed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Fiscal and calendar quarters diverge for anyone whose year-end is not
    # December. Returning both is what stops a cross-company comparison from
    # lining up quarters that are not the same three months.
    async def fake_get(url: str) -> dict[str, Any]:
        return _META_CAPEX_CONCEPT

    monkeypatch.setattr(edgar, "_get_json", fake_get)

    result = json.loads(
        await edgar.fetch_xbrl_metric.ainvoke(
            {"ticker": "META", "metric": "capex", "calendar_period": "CY2025Q3"}
        )
    )

    assert result["calendar_period"] == "CY2025Q3"
    assert result["fiscal_period"] == "FY2025Q3"


async def test_an_unmapped_metric_is_refused_rather_than_guessed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Guessing a tag is how a verifier silently starts checking the wrong
    # number, which is worse than having no verifier at all.
    async def fake_get(url: str) -> dict[str, Any]:
        raise AssertionError("must not reach the network without a mapping")

    monkeypatch.setattr(edgar, "_get_json", fake_get)

    result = json.loads(
        await edgar.fetch_xbrl_metric.ainvoke(
            {"ticker": "META", "metric": "gross_margin", "calendar_period": "CY2025Q3"}
        )
    )

    assert result["status"] == "unmapped"
    assert "gross_margin" in result["error"]
