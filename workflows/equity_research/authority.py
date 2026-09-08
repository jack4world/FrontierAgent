"""Grading the sources the gate cannot verify.

A ``secondary`` figure cannot be checked against anything. What can still be
said is what kind of place it came from — and without that, a wire service and
a site nobody has heard of carry the identical label in the report, leaving the
reader no way to weight them.

Two rules keep this honest:

- **Unknown means unrated, never "low".** Absence from a list is not evidence
  of poor quality; a specialist trade publication that happens not to be listed
  may be the best source on a niche. Grading it "low" would invent a judgement
  nobody made — the same sin the verification gate exists to prevent.
- **The gate assigns the grade, not the harvest.** Otherwise the model awards
  authority to the sources it chose, which is not a check.
"""

from __future__ import annotations

from urllib.parse import urlparse

from workflows._shared.research.observers.evidence_observer import _AUTHORITY_DOMAINS

# The shared table is oriented at scientific and general web sources. These are
# uncontroversially established financial press; the list is deliberately short,
# because every name added is a judgement, and a long list of judgements dressed
# as a lookup table is worse than a short honest one.
_FINANCIAL_PRESS = frozenset({
    "wsj.com", "ft.com", "bloomberg.com", "cnbc.com", "nikkei.com",
    "economist.com", "barrons.com", "marketwatch.com",
})

ESTABLISHED = "established"
UNRATED = "unrated"


def grade_source(url: str) -> str:
    """Return ``established`` or ``unrated`` for a source URL.

    Only two bands, on purpose. A finer scale would imply a precision about
    source quality that this table does not have.
    """
    if not url:
        return UNRATED

    host = (urlparse(url).hostname or "").lower()
    if not host:
        return UNRATED

    if any(host == name or host.endswith(f".{name}") for name in _FINANCIAL_PRESS):
        return ESTABLISHED

    for marker in _AUTHORITY_DOMAINS:
        # The shared table mixes suffixes (".gov") with hostnames
        # ("reuters.com"), so both shapes have to match.
        if marker.startswith("."):
            if host.endswith(marker):
                return ESTABLISHED
        elif host == marker or host.endswith(f".{marker}"):
            return ESTABLISHED

    return UNRATED


def summarize_authority(facts: list[dict]) -> dict[str, int]:
    """Count graded secondary sources, for the report's at-a-glance line."""
    counts = {ESTABLISHED: 0, UNRATED: 0}
    for fact in facts:
        if fact.get("tier") != "secondary":
            continue
        grade = (fact.get("source") or {}).get("authority") or UNRATED
        counts[grade] = counts.get(grade, 0) + 1
    return counts
