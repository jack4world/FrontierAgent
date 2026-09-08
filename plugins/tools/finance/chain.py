"""Mapping chain nodes to the companies behind them.

A claim names nodes; facts name companies. This is what lets ``emit_claim``
check that a claim about a node actually rests on evidence about that node,
rather than merely on evidence about two different things.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_NODES_PATH = (
    Path(__file__).resolve().parents[2] / "skills" / "ai-compute-chain" / "nodes.yaml"
)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
# Entities collapse completely: "SK Hynix", "SK_Hynix" and "SKHYNIX" are one
# company, and a gate that rejects a claim over a space teaches the analyst
# that citing the right company does not help.
_ENTITY_STRIP = re.compile(r"[^a-z0-9]")


def _normalise(name: str) -> str:
    """Fold case and punctuation; the analyst writes edge names freehand."""
    return _NON_ALNUM.sub("_", (name or "").strip().lower()).strip("_")


def load_nodes() -> dict[str, Any]:
    if not _NODES_PATH.is_file():
        logger.warning("chain node map not found at %s", _NODES_PATH)
        return {}
    loaded = yaml.safe_load(_NODES_PATH.read_text(encoding="utf-8"))
    return (loaded or {}).get("nodes", {}) if isinstance(loaded, dict) else {}


def resolve_node(name: str) -> str | None:
    """Return the canonical node for a free-form edge name, or None."""
    key = _normalise(name)
    if not key:
        return None
    for node, spec in load_nodes().items():
        if key == _normalise(node):
            return node
        if any(key == _normalise(a) for a in (spec or {}).get("aliases", [])):
            return node
    return None


def _fold_entity(name: str) -> str:
    return _ENTITY_STRIP.sub("", (name or "").lower())


def entities_for_node(node: str) -> set[str]:
    """Tickers and company names belonging to a canonical node, as written."""
    spec = load_nodes().get(node) or {}
    return {str(e).upper() for e in spec.get("entities", [])}


def entity_in_node(entity: str, node: str) -> bool:
    """Whether a fact's entity belongs to a node, however it was spelled."""
    folded = _fold_entity(entity)
    if not folded:
        return False
    spec = load_nodes().get(node) or {}
    return any(folded == _fold_entity(e) for e in spec.get("entities", []))


def known_node_names() -> list[str]:
    return sorted(load_nodes())
