"""Per-run ledger holding the ``facts`` and ``claims`` tables.

Same shape as ``plugins.tools.task_board``: module-level state keyed by the
run's task id, cleared at run end so nothing leaks between trials.

The facts table is a mapping keyed by ``fact_id``, which is what makes dedupe
free. Two sub-agents that cite the same number produce the same content hash,
write the same key, and every claim referencing it points at one entry — so a
number exists once, is verified once, and has nowhere to drift to.
"""

from __future__ import annotations

from typing import Any

from frontier_agent.core.execution_context import get_current_execution_scope
from plugins.tools._bus_scope import resolve_bus_task_id

# The two table row shapes. verification.py imports these rather than
# redeclaring them, so the gate and the ledger cannot drift apart.
Fact = dict[str, Any]
Claim = dict[str, Any]

_DEFAULT_KEY = "_default"

# task_id -> {fact_id: fact}
_FACTS: dict[str, dict[str, Fact]] = {}
# task_id -> [claim, ...]
_CLAIMS: dict[str, list[Claim]] = {}


def ledger_key() -> str:
    """Task-scoped ledger key; falls back outside a run (tests, REPL)."""
    scope = get_current_execution_scope()
    if scope is None:
        return _DEFAULT_KEY
    try:
        return resolve_bus_task_id(scope)
    except AttributeError:
        return _DEFAULT_KEY


def record_fact(fact: Fact) -> Fact | None:
    """Store a fact.

    Returns ``None`` when it was stored, or the already-present entry when this
    id is taken. The caller decides whether that is a harmless duplicate (same
    value) or a conflict (different value) — the ledger never overwrites, so a
    disagreement cannot be resolved by whoever happens to write last.
    """
    facts = _FACTS.setdefault(ledger_key(), {})
    existing = facts.get(fact["fact_id"])
    if existing is not None:
        return existing
    facts[fact["fact_id"]] = fact
    return None


def record_claim(claim: Claim) -> None:
    """Append a claim to this run's table."""
    _CLAIMS.setdefault(ledger_key(), []).append(claim)


def ledger_facts() -> list[Fact]:
    """Every fact recorded in this run, in insertion order."""
    return list(_FACTS.get(ledger_key(), {}).values())


def ledger_claims() -> list[Claim]:
    """Every claim recorded in this run, in emission order."""
    return list(_CLAIMS.get(ledger_key(), []))


def known_fact_ids() -> set[str]:
    """Ids this run has actually issued.

    ``emit_claim`` validates against this, so an id the ledger never
    minted is one the model invented.
    """
    return set(_FACTS.get(ledger_key(), {}))


def clear_ledger() -> None:
    """Drop this run's tables.

    Called between tests today. It must also be called at run end once a
    pipeline node exists, the way ``task_board.clear_board`` is — otherwise a
    long-lived process carries one run's facts into the next, and dedupe by
    content hash would silently join two unrelated runs' tables.
    """
    key = ledger_key()
    _FACTS.pop(key, None)
    _CLAIMS.pop(key, None)
