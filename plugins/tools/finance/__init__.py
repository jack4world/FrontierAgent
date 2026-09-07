"""Finance tools for the equity-research workflow.

Registered in ``plugins.tools.get_builtin_tools()`` alongside the rest.

That was not the first plan: keeping them out of the pinned allowlist looked
tidier. But ``ResourceManager.get_tools_for_role`` filters the tool map by the
role's permitted names and silently drops anything it cannot resolve, so tools
outside the map leave a role quietly holding nothing. Registration is scoped by
role permissions anyway — workflows that do not name these never see them.
"""

from __future__ import annotations
