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
        # basis is shown alongside tier because the analyst has to be able to
        # pick the consensus figures out of the table to cite them; without it
        # a market expectation is indistinguishable from a measurement.
        lines.append(
            f"- `{fact['fact_id']}` — {fact.get('entity')} {fact.get('metric')} "
            f"{period}: {fact.get('value')} {fact.get('unit')} "
            f"[tier={fact.get('tier')}, basis={fact.get('basis', 'reported')}]"
        )
    return "\n".join(lines)


def render_removed_block(removed: list[Fact]) -> str:
    """What the gate threw out, with enough detail to diagnose why.

    A removed fact is either a fabricated quotation or a real figure filed
    against the wrong source. The fact_id alone cannot tell those apart, and
    the difference decides whether you distrust the model or fix a URL.
    """
    if not removed:
        return "_(nothing removed)_"
    lines = []
    for fact in removed:
        source = fact.get("source") or {}
        lines.append(
            f"- `{fact['fact_id']}` — {fact.get('entity')} {fact.get('metric')} "
            f"= {fact.get('value')} {fact.get('unit')}"
        )
        lines.append(f"  - claimed source: {source.get('url') or '—'}")
        if source.get("verbatim"):
            lines.append(f"  - quote not found in that document: \"{source['verbatim']}\"")
    return "\n".join(lines)


_VERIFIED_VERDICTS = frozenset({"match", "quote_confirmed"})


def score_claim(claim: Any, findings: list[Any]) -> tuple[str, str]:
    """Return (evidence strength, distance from the market view).

    The two axes are deliberately not combined into one number. A claim that is
    well evidenced and says what everyone already says is sound and worthless;
    one that departs from the market on unverified figures is a bet wearing a
    report's clothes. Collapsing them to a single score hides which of those
    you are holding.
    """
    verdicts = {f.fact_id: f.verdict.value for f in findings}
    supports = claim.get("depends_on", []) or []
    verified = sum(1 for ref in supports if verdicts.get(ref) in _VERIFIED_VERDICTS)
    evidence = f"{verified}/{len(supports)} verified" if supports else "no support"

    if claim.get("consensus_refs") and claim.get("consensus_delta"):
        stance = "stated"
    elif claim.get("consensus_refs"):
        stance = "cited, not stated"
    else:
        stance = "unknown — no market view cited"
    return evidence, stance


def render_report(
    question: str,
    facts: list[Any],
    claims: list[Any],
    findings: list[Any],
    removed: list[Fact] | None = None,
) -> str:
    """Assemble the deliverable: claims first, then what the gate did."""
    out = [f"# Supply-chain transmission analysis\n\n**Question:** {question}\n"]

    if claims:
        out.append("## At a glance\n")
        out.append("| claim | edge | evidence | vs. market |")
        out.append("|---|---|---|---|")
        for claim in claims:
            edge = claim.get("edge", {})
            evidence, stance = score_claim(claim, findings)
            out.append(
                f"| `{claim.get('claim_id')}` | {edge.get('from')} → {edge.get('to')} "
                f"| {evidence} | {stance} |"
            )
        out.append(
            "\n_Well-evidenced and identical to the market view is sound and "
            "adds nothing; departing from it on unverified figures is a bet. "
            "The pairing is the point._\n"
        )

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
        if claim.get("consensus_delta"):
            out.append(f"- **Vs. market view:** {claim['consensus_delta']}")
            out.append(f"  - market figures cited: {', '.join(claim.get('consensus_refs', []))}")
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

    out.append("\n## Removed by the gate\n")
    out.append(render_removed_block(removed or []))

    out.append("\n## Facts\n")
    out.append(render_facts_block(facts))
    return "\n".join(out)
