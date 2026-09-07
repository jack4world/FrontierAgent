"""Equity Research — supply-chain transmission research.

Registers two roles. Data collection is deliberately NOT one of them: fetching
XBRL is a deterministic API call, and wrapping it in a reasoning agent buys
nothing while adding a turn in which a number can be mis-transcribed. It is a
tool on the coordinator.

Verification is likewise not a role. Because claims cite facts by id instead of
restating numbers, checking one degenerates to comparing two values — see
``workflows.equity_research.verification``. Using an LLM to eliminate number
hallucination would mean trusting a component with the same failure mode.
"""

from __future__ import annotations

from frontier_agent.core.runtime.registries.workflows import WorkflowContext
from frontier_agent.models.agent_definition import AgentDefinition

PIPELINE_ID = "equity_research"
MAIN_ROLE_ID = "equity_research_main"
ANALYST_ROLE_ID = "equity_research_analyst"

# The coordinator harvests and delegates. Harvesting lives here rather than in
# a sub-agent so the numbers enter the ledger straight from the API response.
_MAIN_TOOLS = [
    "create_subagent",
    "assign_task",
    "collect_reports",
    "stop_subagent",
    "add_task",
    "update_task",
    "finish_planning",
    # Harvesting.
    "fetch_xbrl_metric",
    "fetch_xbrl_concept",
    "web_search",
    "web_fetch",
    # Read-only inspection.
    "read_text",
    "grep_search",
    "glob_search",
]

# The analyst reasons; it does not gather. No web tools, and no shell or Python
# either — withholding web_search while leaving an escape hatch open would be
# theatre. The point is not to inconvenience the model but to make confirmation
# bias structurally impossible: an analyst that cannot search cannot go looking
# for support for the conclusion it already holds. Everything it reasons over
# arrives as facts the coordinator harvested.
_ANALYST_TOOLS = [
    "emit_fact",
    "emit_claim",
    "submit_report",
    # read_text is what turns injected skill metadata into loadable content.
    "read_text",
    "grep_search",
    "glob_search",
    "recover_result",
]


MAIN_AGENT_DEF = AgentDefinition(
    role_id=MAIN_ROLE_ID,
    display_name="Equity Research Coordinator",
    system_prompt="Computed per-task.",
    allowed_tools=_MAIN_TOOLS,
    color="#0f766e",
    icon="git-branch",
    description=(
        "Coordinator: harvests first-party data, splits the chain into links, "
        "delegates one analyst per link, and assembles the report."
    ),
)

ANALYST_AGENT_DEF = AgentDefinition(
    role_id=ANALYST_ROLE_ID,
    display_name="Supply-Chain Analyst",
    system_prompt="Routed per-spawn by link.",
    allowed_tools=_ANALYST_TOOLS,
    color="#7c3aed",
    icon="search",
    # Chain knowledge is loaded per link rather than injected whole: an analyst
    # working optical modules should not carry three other links' notes through
    # every turn of its loop.
    enable_skills=True,
    description=(
        "Reasons over harvested facts for one link of the chain and emits "
        "claims, each with its falsification condition and counter-evidence. "
        "Has no network access by construction."
    ),
)


def register(ctx: WorkflowContext) -> None:
    ctx.register_agent(MAIN_AGENT_DEF)
    ctx.register_agent(ANALYST_AGENT_DEF)


__all__ = [
    "ANALYST_AGENT_DEF",
    "ANALYST_ROLE_ID",
    "MAIN_AGENT_DEF",
    "MAIN_ROLE_ID",
    "PIPELINE_ID",
    "register",
]
