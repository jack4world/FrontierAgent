"""Finance tools for the equity-research workflow.

Deliberately NOT part of ``plugins.tools.get_builtin_tools()``. That registry is
an explicit allowlist pinned by ``tests/test_tool_registry.py``; a domain feature
should not force every contributor's diff through it. The equity-research
workflow registers these itself.
"""

from __future__ import annotations
