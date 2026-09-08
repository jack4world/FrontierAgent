"""Resolving free-form edge names to the companies behind them.

A claim names nodes; facts name companies. Without a mapping between them,
"cites evidence about the node it is claiming about" is uncheckable — and the
live run produced a claim about HBM supply evidenced entirely by the
accelerator vendor and the packaging foundry, which passed a gate that only
counted distinct entities.
"""

from __future__ import annotations

from plugins.tools.finance.chain import entities_for_node, resolve_node


def test_the_canonical_node_name_resolves() -> None:
    assert resolve_node("hbm") == "hbm"
    assert resolve_node("advanced_packaging") == "advanced_packaging"


def test_the_names_the_analyst_actually_writes_resolve() -> None:
    # These are verbatim from live runs; the analyst does not use the canonical
    # names consistently and there is no reason it should have to.
    assert resolve_node("hbm_supply") == "hbm"
    assert resolve_node("hbm_suppliers") == "hbm"
    assert resolve_node("tsm_cowos_capacity") == "advanced_packaging"
    assert resolve_node("tsmc_advanced_packaging") == "advanced_packaging"
    assert resolve_node("nvda_revenue") == "accelerators"


def test_matching_ignores_case_and_punctuation() -> None:
    assert resolve_node("TSMC Advanced Packaging") == "advanced_packaging"
    assert resolve_node("HBM-Supply") == "hbm"


def test_an_unknown_node_resolves_to_nothing() -> None:
    # Refused rather than guessed: the chain graph gets extended in the file,
    # deliberately, not invented mid-run.
    assert resolve_node("quantum_interconnect") is None
    assert resolve_node("") is None


def test_a_node_maps_to_the_companies_behind_it() -> None:
    assert "MU" in entities_for_node("hbm")
    assert "TSM" in entities_for_node("advanced_packaging")
    assert "NVDA" in entities_for_node("accelerators")
    # Korean filers belong to the node even though the XBRL gate cannot reach
    # them: a claim citing them is still better evidenced than one citing none.
    assert "SKHYNIX" in entities_for_node("hbm")
