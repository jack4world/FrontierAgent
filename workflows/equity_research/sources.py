"""Resolving the sources the gate needs, ahead of the gate.

``verify`` is sync and pure on purpose — that is what makes it testable without
a network or a model. The cost is that somebody has to do the async fetching
first. This module is that somebody: it resolves every lookup a batch of facts
will need, then hands ``verify`` two plain dict-backed closures.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

import httpx

from plugins.tools.finance.edgar import fetch_xbrl_metric
from plugins.tools.finance.ledger import Fact

logger = logging.getLogger(__name__)

_FETCH_TIMEOUT_S = 60.0
# Transcripts and press releases are the long tail here; a filing exhibit runs
# to a few hundred KB. Past that we are downloading something that is not the
# document we meant.
_MAX_SOURCE_BYTES = 8_000_000
_BROWSER_UA = "Mozilla/5.0 (compatible; FrontierAgent equity-research)"


async def resolve_xbrl_values(facts: list[Fact]) -> dict[str, float | None]:
    """Look up the filed value for every ``xbrl_verified`` fact.

    Keyed by ``fact_id`` rather than by concept: two facts can name the same
    concept for different periods, and the gate asks per fact.
    """
    values: dict[str, float | None] = {}
    for fact in facts:
        if fact.get("tier") != "xbrl_verified":
            continue
        try:
            raw = json.loads(await fetch_xbrl_metric.ainvoke({
                "ticker": fact.get("entity", ""),
                "metric": fact.get("metric", ""),
                "calendar_period": (fact.get("period") or {}).get("calendar", ""),
            }))
        except Exception as exc:  # a failed lookup is UNRESOLVED, not a crash
            logger.warning("xbrl lookup failed for %s: %s", fact.get("fact_id"), exc)
            values[fact["fact_id"]] = None
            continue
        values[fact["fact_id"]] = raw.get("value") if raw.get("status") == "ok" else None
    return values


async def resolve_source_texts(facts: list[Fact]) -> dict[str, str]:
    """Re-fetch the cited document for every ``quoted_primary`` fact.

    Re-fetching rather than trusting what the harvest phase reported is the
    whole mechanism: the quote is checked against the document as it stands,
    not against the model's memory of it.
    """
    texts: dict[str, str] = {}
    cache: dict[str, str] = {}

    async with httpx.AsyncClient(
        timeout=_FETCH_TIMEOUT_S, follow_redirects=True,
        headers={"User-Agent": _BROWSER_UA},
    ) as client:
        for fact in facts:
            if fact.get("tier") != "quoted_primary":
                continue
            url = (fact.get("source") or {}).get("url") or ""
            if url not in cache:
                cache[url] = await _fetch_text(client, url)
            texts[fact["fact_id"]] = cache[url]
    return texts


async def _fetch_text(client: httpx.AsyncClient, url: str) -> str:
    """Fetch one document as plain text. Returns "" on any failure.

    An empty body fails the verbatim check, which is the right default: a quote
    that cannot be re-read is a quote that cannot be confirmed.
    """
    if not url:
        return ""
    try:
        response = await client.get(url)
        response.raise_for_status()
        body = response.content[:_MAX_SOURCE_BYTES]
    except Exception as exc:
        logger.warning("source fetch failed for %s: %s", url, exc)
        return ""

    if url.lower().endswith(".pdf") or body[:5] == b"%PDF-":
        return _pdf_to_text(body)
    return _html_to_text(body.decode("utf-8", errors="ignore"))


def _pdf_to_text(body: bytes) -> str:
    import io

    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover - depends on the optional extra
        logger.warning("pypdf unavailable; cannot read PDF source")
        return ""
    try:
        reader = PdfReader(io.BytesIO(body))
        return " ".join(
            " ".join((page.extract_text() or "") for page in reader.pages).split()
        )
    except Exception as exc:
        logger.warning("pdf parse failed: %s", exc)
        return ""


def _html_to_text(raw: str) -> str:
    import html
    import re

    stripped = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
    stripped = re.sub(r"<[^>]+>", " ", stripped)
    return " ".join(html.unescape(stripped).split())


def lookups_for(
    xbrl: dict[str, float | None], texts: dict[str, str],
) -> tuple[Callable[[Fact], float | None], Callable[[Fact], str]]:
    """Wrap resolved data as the two closures ``verify`` expects."""
    return (
        lambda fact: xbrl.get(fact.get("fact_id", "")),
        lambda fact: texts.get(fact.get("fact_id", ""), ""),
    )


def render_facts_block(facts: list[Fact]) -> str:
    """The facts table as the analyst sees it — ids first, since it cites by id."""
    if not facts:
        return "_(none — the harvest phase recorded nothing)_"
    lines = []
    for fact in facts:
        period = (fact.get("period") or {}).get("calendar") or ""
        lines.append(
            f"- `{fact['fact_id']}` — {fact.get('entity')} {fact.get('metric')} "
            f"{period}: {fact.get('value')} {fact.get('unit')} "
            f"[tier={fact.get('tier')}]"
        )
    return "\n".join(lines)


def render_report(
    question: str, facts: list[Any], claims: list[Any], findings: list[Any],
) -> str:
    """Assemble the deliverable: claims first, then what the gate did."""
    out = [f"# Supply-chain transmission analysis\n\n**Question:** {question}\n"]

    out.append("## Claims\n")
    if not claims:
        out.append("_No claims survived the analysis phase._\n")
    for claim in claims:
        edge = claim.get("edge", {})
        out.append(f"### {edge.get('from')} → {edge.get('to')}  (`{claim.get('claim_id')}`)\n")
        out.append(f"{claim.get('statement', '')}\n")
        if claim.get("impact"):
            out.append(f"- **Impact:** {claim['impact']['value']} {claim['impact']['unit']}")
            for step in claim.get("derivation", []):
                out.append(f"  - {step}")
        if claim.get("lag", {}).get("quarters"):
            out.append(f"- **Lag:** {claim['lag']['quarters']} quarters")
        out.append(f"- **Falsified if:** {claim.get('falsification', '')}")
        out.append(f"- **Counter-evidence:** {', '.join(claim.get('counter_evidence', [])) or '—'}")
        if claim.get("unsupported_by"):
            out.append(
                f"- ⚠️ **Rests on facts the gate removed:** "
                f"{', '.join(claim['unsupported_by'])}"
            )
        out.append("")

    out.append("## Verification\n")
    out.append("| fact | verdict | reported | filed |")
    out.append("|---|---|---|---|")
    for finding in findings:
        rep = f"{finding.reported_value:,}" if finding.reported_value is not None else "—"
        auth = f"{finding.authoritative_value:,}" if finding.authoritative_value is not None else "—"
        out.append(f"| `{finding.fact_id}` | {finding.verdict.value} | {rep} | {auth} |")

    out.append("\n## Facts\n")
    out.append(render_facts_block(facts))
    return "\n".join(out)
