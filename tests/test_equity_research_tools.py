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
        "edge_to": "advanced_packaging",
        "statement": "Raised CSP capex pulls forward 800G module orders.",
        "depends_on": [],
        "falsification": "No sequential growth in Q1 datacom revenue.",
        "lag_quarters": 2,
    }
    args.update(overrides)
    return args


async def _emit_supporting_fact() -> str:
    return json.loads(await emit_fact.ainvoke(_fact_args()))["fact_id"]


async def _both_ends() -> tuple[str, str]:
    """A driver fact and an upstream fact — the minimum a claim may rest on."""
    return await _emit_supporting_fact(), await _emit_upstream_fact()


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
    fact_id, upstream = await _both_ends()

    rejected = json.loads(
        await emit_claim.ainvoke(
            _claim_args(
                depends_on=[fact_id, upstream],
                counter_evidence=[fact_id],
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
    fact_id, upstream = await _both_ends()

    recorded = json.loads(
        await emit_claim.ainvoke(
            _claim_args(depends_on=[fact_id, upstream], counter_evidence=[fact_id])
        )
    )

    assert recorded["status"] == "recorded"
    claim = ledger_claims()[0]
    assert claim["claim_id"] == recorded["claim_id"]
    assert claim["depends_on"] == [fact_id, upstream]
    assert claim["edge"] == {"from": "csp_capex", "to": "advanced_packaging"}
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


async def test_a_claim_without_counter_evidence_is_rejected() -> None:
    # The spec makes counter_evidence mandatory for a reason: four analysts
    # each arguing their own link produces a report that agrees with itself
    # and has checked nothing. Enforcing falsification while leaving this
    # optional would drop half the guard.
    fact_id, upstream = await _both_ends()

    rejected = json.loads(
        await emit_claim.ainvoke(
            _claim_args(depends_on=[fact_id, upstream], counter_evidence=[])
        )
    )

    assert rejected["status"] == "rejected"
    assert "counter_evidence" in rejected["error"]


async def test_counter_evidence_must_cite_real_facts() -> None:
    # Requiring counter-evidence would invite inventing it, so the ids are
    # checked the same way supports are: the analyst has to emit a sourced
    # fact before it can point at one.
    fact_id, upstream = await _both_ends()

    rejected = json.loads(
        await emit_claim.ainvoke(
            _claim_args(depends_on=[fact_id, upstream],
                        counter_evidence=["f-invented-0000"])
        )
    )

    assert rejected["status"] == "rejected"
    assert "f-invented-0000" in rejected["error"]


async def test_a_derivation_step_that_cites_no_fact_is_rejected() -> None:
    # "Each step names the fact_id it consumes" is what makes the arithmetic
    # re-checkable. A step of prose is a number with extra words around it.
    fact_id, upstream = await _both_ends()

    rejected = json.loads(
        await emit_claim.ainvoke(
            _claim_args(
                depends_on=[fact_id, upstream],
                counter_evidence=[fact_id],
                impact_value=300_000_000,
                impact_unit="USD",
                derivation=["capex rises, so module orders rise by about 8%"],
            )
        )
    )

    assert rejected["status"] == "rejected"
    assert "derivation" in rejected["error"]


async def test_a_quantified_claim_with_a_grounded_derivation_is_recorded() -> None:
    fact_id, upstream = await _both_ends()

    recorded = json.loads(
        await emit_claim.ainvoke(
            _claim_args(
                depends_on=[fact_id, upstream],
                counter_evidence=[fact_id],
                impact_value=300_000_000,
                impact_unit="USD",
                derivation=[f"{fact_id} * 0.008 = 300000000 USD of module content"],
            )
        )
    )

    assert recorded["status"] == "recorded"
    assert ledger_claims()[0]["impact"] == {"value": 300_000_000, "unit": "USD"}


# ── Ledger scoping ────────────────────────────────────────────────────────


async def test_facts_written_inside_a_loop_scope_are_readable_outside_it() -> None:
    # Tools run inside the agent loop's execution scope; the node reads the
    # ledger after the loop returns, with no ambient scope at all. If the key
    # is derived from the ambient scope, those are two different ledgers and
    # the node sees an empty table while every emit_fact logged success.
    from frontier_agent.core.execution_context import (
        build_execution_scope,
        reset_current_execution_scope,
        set_current_execution_scope,
    )
    from plugins.tools.finance.ledger import reset_ledger_scope, use_ledger_scope

    run_token = use_ledger_scope("task-abc")
    try:
        scope = build_execution_scope(
            task_id="task-abc", phase_id="harvest", role_id="equity_research_main",
        )
        scope_token = set_current_execution_scope(scope)
        try:
            recorded = json.loads(await emit_fact.ainvoke(_fact_args()))
        finally:
            reset_current_execution_scope(scope_token)

        assert recorded["status"] == "recorded"
        # Read the way the node does: after the loop, outside any scope.
        assert [f["fact_id"] for f in ledger_facts()] == [recorded["fact_id"]]
    finally:
        clear_ledger()
        reset_ledger_scope(run_token)


# ── Consensus: what the market already thinks ─────────────────────────────


async def test_a_fact_records_whether_it_is_an_actual_a_guide_or_an_expectation() -> None:
    # Tier says how verifiable a number is. Basis says what kind of number it
    # is. A street estimate can be perfectly well sourced and still be nobody's
    # observation of anything — conflating the two hides that.
    recorded = json.loads(await emit_fact.ainvoke(_fact_args(
        metric="revenue_estimate", basis="consensus", tier="secondary",
        source_url="https://example-broker.test/nvda", concept="", accession="",
    )))

    assert recorded["status"] == "recorded"
    assert ledger_facts()[0]["basis"] == "consensus"


async def test_an_unknown_basis_is_rejected() -> None:
    rejected = json.loads(await emit_fact.ainvoke(_fact_args(basis="vibes")))

    assert rejected["status"] == "rejected"
    assert "basis" in rejected["error"]


async def _emit_consensus_fact() -> str:
    return json.loads(await emit_fact.ainvoke(_fact_args(
        metric="revenue_estimate", basis="consensus", tier="secondary",
        source_url="https://example-broker.test/nvda", concept="", accession="",
    )))["fact_id"]


async def test_citing_consensus_without_saying_how_you_differ_is_rejected() -> None:
    # Naming what the market thinks and then not saying how your view departs
    # from it is the shape of a report that quietly agrees while sounding
    # independent.
    support, upstream = await _both_ends()
    consensus = await _emit_consensus_fact()

    rejected = json.loads(await emit_claim.ainvoke(_claim_args(
        depends_on=[support, upstream], counter_evidence=[support],
        consensus_refs=[consensus], consensus_delta="  ",
    )))

    assert rejected["status"] == "rejected"
    assert "consensus_delta" in rejected["error"]


async def test_a_reported_actual_cannot_be_passed_off_as_the_consensus() -> None:
    # Otherwise "differs from consensus" can be manufactured by pointing at any
    # convenient number and calling it the market's view.
    support, upstream = await _both_ends()

    rejected = json.loads(await emit_claim.ainvoke(_claim_args(
        depends_on=[support, upstream], counter_evidence=[support],
        consensus_refs=[support], consensus_delta="we are above the street",
    )))

    assert rejected["status"] == "rejected"
    assert "basis" in rejected["error"]


async def test_a_claim_records_how_it_departs_from_the_market_view() -> None:
    support, upstream = await _both_ends()
    consensus = await _emit_consensus_fact()

    recorded = json.loads(await emit_claim.ainvoke(_claim_args(
        depends_on=[support, upstream], counter_evidence=[support],
        consensus_refs=[consensus],
        consensus_delta="Street assumes packaging is not the binding constraint.",
    )))

    assert recorded["status"] == "recorded"
    claim = ledger_claims()[0]
    assert claim["consensus_refs"] == [consensus]
    assert claim["consensus_delta"].startswith("Street assumes")


# ── Relevance: a claim about an edge needs evidence from both ends ─────────


async def _emit_upstream_fact() -> str:
    """A fact about a different company than _fact_args' META."""
    return json.loads(await emit_fact.ainvoke(_fact_args(
        entity="TSM", metric="cowos_capacity", value=130_000,
        unit="wafers_per_month", tier="secondary", concept="", accession="",
        source_url="https://example-trade.test/cowos",
    )))["fact_id"]


async def test_a_transmission_claim_built_only_from_the_driver_is_rejected() -> None:
    # The live run produced nine claims about TSMC packaging and HBM suppliers
    # while citing nothing but NVIDIA figures. Every other gate passed it:
    # falsification present, ids real, counter-evidence supplied. Nothing asked
    # whether the evidence had anything to do with the node being claimed about.
    driver = await _emit_supporting_fact()

    rejected = json.loads(await emit_claim.ainvoke(_claim_args(
        edge_from="nvda", edge_to="tsmc_advanced_packaging",
        depends_on=[driver], counter_evidence=[driver],
    )))

    assert rejected["status"] == "rejected"
    # META is a csp_capex company, so the packaging node has no evidence at all.
    assert "advanced_packaging" in rejected["error"]


async def test_a_claim_citing_both_ends_of_the_edge_is_recorded() -> None:
    driver = await _emit_supporting_fact()
    upstream = await _emit_upstream_fact()

    recorded = json.loads(await emit_claim.ainvoke(_claim_args(
        edge_from="nvda", edge_to="tsmc_advanced_packaging",
        depends_on=[driver, upstream], counter_evidence=[upstream],
    )))

    assert recorded["status"] == "recorded"


async def test_a_claim_about_hbm_evidenced_by_nvidia_and_tsmc_is_rejected() -> None:
    # The exact shape the live run produced and the entity-count gate let
    # through: two distinct entities cited, neither of them a memory supplier,
    # and the claim is about HBM supply.
    driver = await _emit_supporting_fact()          # META
    packaging = await _emit_upstream_fact()         # TSM

    rejected = json.loads(await emit_claim.ainvoke(_claim_args(
        edge_from="nvda_revenue", edge_to="hbm_supply",
        depends_on=[driver, packaging], counter_evidence=[packaging],
    )))

    assert rejected["status"] == "rejected"
    assert "hbm" in rejected["error"].lower()


async def test_a_claim_about_an_undefined_node_is_rejected() -> None:
    # Otherwise the downstream check is evaded by inventing a node name.
    driver = await _emit_supporting_fact()
    packaging = await _emit_upstream_fact()

    rejected = json.loads(await emit_claim.ainvoke(_claim_args(
        edge_from="nvda_revenue", edge_to="quantum_interconnect",
        depends_on=[driver, packaging], counter_evidence=[packaging],
    )))

    assert rejected["status"] == "rejected"
    assert "quantum_interconnect" in rejected["error"]


async def test_a_claim_citing_the_downstream_node_is_recorded() -> None:
    driver = await _emit_supporting_fact()
    packaging = await _emit_upstream_fact()         # TSM = advanced_packaging

    recorded = json.loads(await emit_claim.ainvoke(_claim_args(
        edge_from="nvda_revenue", edge_to="tsm_cowos_capacity",
        depends_on=[driver, packaging], counter_evidence=[packaging],
    )))

    assert recorded["status"] == "recorded"
