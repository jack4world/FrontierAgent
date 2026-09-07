"""Structural integrity of the tag map. Offline — no network.

The live value check lives in ``tests/network/test_tag_map_regression.py`` and
is opt-in. What is asserted here is only what can be known without EDGAR: that
every mapping is complete enough to be auditable.
"""

from __future__ import annotations

import re

from plugins.tools.finance.edgar import load_tag_map, resolve_metric

_CIK_RE = re.compile(r"^\d{10}$")


def test_every_mapping_carries_a_cik_a_concept_and_a_calibration_note() -> None:
    # The calibration note is why a mapping is auditable at all: without a
    # record of how the tag was confirmed for this filer, nobody can tell a
    # verified mapping from one that merely happened to fetch cleanly once.
    metrics = load_tag_map().get("metrics", {})
    assert metrics, "tag map is empty"

    for metric, mapping in metrics.items():
        for ticker, entry in mapping.get("companies", {}).items():
            where = f"{metric}/{ticker}"
            assert _CIK_RE.match(entry.get("cik", "")), f"{where}: cik must be 10 digits"
            assert entry.get("concept"), f"{where}: missing concept"
            assert entry.get("calibration", "").strip(), f"{where}: missing calibration"
            assert "regression" in entry, f"{where}: missing regression key"


def test_an_unmapped_company_resolves_to_nothing() -> None:
    assert resolve_metric("NVDA", "capex") is None
    assert resolve_metric("META", "not_a_metric") is None
