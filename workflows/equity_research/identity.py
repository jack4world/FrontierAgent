"""Canonical identifiers for the equity-research workflow.

Separate from ``__init__`` so nodes can import the ids without importing the
package that registers them — the import cycle that would otherwise force the
node to hardcode its role, which is the exact coupling that stopped us reusing
a shipped coordinator in the first place.
"""

from __future__ import annotations

PIPELINE_ID = "equity_research"

MAIN_ROLE_ID = "equity_research_main"
ANALYST_ROLE_ID = "equity_research_analyst"

ENTRY_NODE_ID = "equity_research_main"

__all__ = [
    "ANALYST_ROLE_ID",
    "ENTRY_NODE_ID",
    "MAIN_ROLE_ID",
    "PIPELINE_ID",
]
