"""SEC EDGAR XBRL access — the authoritative source for reported actuals.

Two layers, as agreed: a thin ``fetch_xbrl_concept`` for exploration, and a
semantic ``fetch_xbrl_metric`` for production use that resolves a metric name
through the per-company tag map. An unmapped metric is refused rather than
guessed — guessing a tag is how a gate silently begins verifying the wrong
number, which is worse than having no gate.

XBRL carries reported historicals only. Forward guidance has no structured
counterpart here; it belongs to the ``quoted_primary`` tier and is checked by
verbatim quotation instead.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx
import yaml

from frontier_agent.core.tool import tool

logger = logging.getLogger(__name__)

_BASE = "https://data.sec.gov/api/xbrl"
_TIMEOUT_S = 30.0

# SEC requires a descriptive User-Agent carrying a contact address; requests
# without one are refused. Configurable so a deployment sets its own contact.
_USER_AGENT = os.environ.get(
    "SEC_EDGAR_USER_AGENT", "FrontierAgent equity-research (contact: set SEC_EDGAR_USER_AGENT)",
)

_TAG_MAP_PATH = (
    Path(__file__).resolve().parents[2] / "skills" / "ai-compute-chain" / "tag_map.yaml"
)


def load_tag_map() -> dict[str, Any]:
    """Read the semantic-metric mapping. Missing file yields an empty map."""
    if not _TAG_MAP_PATH.is_file():
        logger.warning("tag map not found at %s", _TAG_MAP_PATH)
        return {}
    with _TAG_MAP_PATH.open(encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)
    return loaded if isinstance(loaded, dict) else {}


def resolve_metric(ticker: str, metric: str) -> dict[str, Any] | None:
    """Return the mapping entry for one company's metric, or None."""
    mapping = load_tag_map().get("metrics", {}).get(metric)
    if not isinstance(mapping, dict):
        return None
    entry = mapping.get("companies", {}).get(ticker.upper())
    return entry if isinstance(entry, dict) else None


async def _get_json(url: str) -> dict[str, Any]:
    """Fetch and parse one EDGAR JSON document. Replaced wholesale in tests."""
    async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
        response = await client.get(url, headers={"User-Agent": _USER_AGENT})
        response.raise_for_status()
        return response.json()


def _concept_url(cik: str, concept: str) -> str:
    """Canonical companyconcept URL for one filer's us-gaap tag."""
    return f"{_BASE}/companyconcept/CIK{cik}/us-gaap/{concept}.json"


async def _fetch_concept(cik: str, concept: str) -> tuple[dict[str, Any] | None, str, str]:
    """Fetch a concept document. Returns (payload, url, error) — never raises.

    Failures come back to the model as JSON it can act on; an exception here
    would abort the agent turn over a transient SEC hiccup.
    """
    url = _concept_url(cik, concept)
    try:
        return await _get_json(url), url, ""
    except Exception as exc:
        logger.warning("EDGAR fetch failed for %s: %s", url, exc)
        return None, url, str(exc)


def _fiscal_label(entry: dict[str, Any]) -> str:
    """Build the filer's own period label from ``fy`` / ``fp``."""
    fy, fp = entry.get("fy"), entry.get("fp")
    if fy is None or not fp:
        return ""
    return f"FY{fy}" if fp == "FY" else f"FY{fy}{fp}"


def _select(entries: list[dict[str, Any]], calendar_period: str) -> dict[str, Any] | None:
    """Pick the observation for one calendar period.

    Matches EDGAR's own ``frame`` label rather than inferring one from dates:
    the frame is what makes cross-company comparison meaningful, and deriving
    it locally would reintroduce exactly the fiscal/calendar confusion the
    two-label contract exists to prevent.
    """
    for entry in entries:
        if entry.get("frame") == calendar_period:
            return entry
    return None


@tool
async def fetch_xbrl_metric(ticker: str, metric: str, calendar_period: str) -> str:
    """Read one reported financial metric from SEC XBRL. Authoritative.

    Use this for historical actuals. Guidance and any forward-looking figure
    is NOT in XBRL — quote it from the filing instead and emit it as
    ``quoted_primary``.

    Args:
        ticker: Company ticker, e.g. ``META``.
        metric: Semantic name from the tag map, e.g. ``capex``. Not a us-gaap
            tag — which tag a metric means is a per-company judgement.
        calendar_period: EDGAR frame label, e.g. ``CY2025Q3``.

    Returns:
        JSON with ``value``, ``unit``, ``concept``, ``accession``, and BOTH
        ``fiscal_period`` and ``calendar_period``; or a status explaining why
        no value was returned.
    """
    entry = resolve_metric(ticker, metric)
    if entry is None:
        return json.dumps({
            "status": "unmapped",
            "error": (
                f"no tag mapping for metric {metric!r} on {ticker.upper()}. "
                "Add it to plugins/skills/ai-compute-chain/tag_map.yaml with a "
                "calibration note; do not guess a us-gaap tag."
            ),
        })

    cik, concept = entry["cik"], entry["concept"]
    payload, url, error = await _fetch_concept(cik, concept)
    if payload is None:
        return json.dumps({"status": "fetch_failed", "error": error, "url": url})

    units: dict[str, Any] = payload.get("units", {}) or {}
    for unit, entries in units.items():
        selected = _select(entries or [], calendar_period)
        if selected is None:
            continue
        return json.dumps({
            "status": "ok",
            "ticker": ticker.upper(),
            "metric": metric,
            "concept": concept,
            "value": selected.get("val"),
            "unit": unit,
            "calendar_period": calendar_period,
            "fiscal_period": _fiscal_label(selected),
            "accession": selected.get("accn"),
            "form": selected.get("form"),
            "url": url,
        })

    return json.dumps({
        "status": "not_reported",
        "error": (
            f"{ticker.upper()} reports no {concept} observation framed "
            f"{calendar_period}. Quarterly frames are absent for filers who "
            "only report the concept annually."
        ),
        "url": url,
    })


@tool
async def fetch_xbrl_concept(cik: str, concept: str) -> str:
    """Read a raw us-gaap concept series for exploration. Prefer the mapped tool.

    Use this to work out which tag a company actually reports a metric under,
    then record the answer in the tag map with a calibration note.

    Args:
        cik: Zero-padded SEC CIK, e.g. ``0001326801``.
        concept: A us-gaap tag, e.g. ``PaymentsToAcquirePropertyPlantAndEquipment``.

    Returns:
        JSON with every reported observation, newest last.
    """
    payload, url, error = await _fetch_concept(cik, concept)
    if payload is None:
        return json.dumps({"status": "fetch_failed", "error": error, "url": url})

    units: dict[str, Any] = payload.get("units", {}) or {}
    observations = [
        {
            "unit": unit,
            "value": entry.get("val"),
            "calendar_period": entry.get("frame"),
            "fiscal_period": _fiscal_label(entry),
            "end": entry.get("end"),
            "form": entry.get("form"),
            "accession": entry.get("accn"),
        }
        for unit, entries in units.items()
        for entry in (entries or [])
    ]
    return json.dumps({
        "status": "ok", "cik": cik, "concept": concept,
        "observations": observations, "url": url,
    })
