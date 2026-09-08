"""The equity-research node: harvest, then analyse, then gate.

Two sequential loops rather than a coordinator spawning sub-agents. The
separation that matters is not parallelism, it is the tool boundary: phase one
holds the network and may not conclude, phase two may conclude and holds no
network. Running them in sequence enforces that with far less machinery than
spawning would, and the parallel-analyst-per-link version can come later
without changing the contract.
"""

from __future__ import annotations

import logging
from typing import Any

from frontier_agent.core.loop_types import LoopConfig
from frontier_agent.core.runtime.loop.agent_loop import run_agent_loop
from frontier_agent.core.runtime.registries import services as registry
from frontier_agent.core.runtime.resources.manager import ResourceManager
from frontier_agent.models.node_context import NodeContext
from plugins.tools.finance.ledger import (
    clear_ledger,
    ledger_claims,
    ledger_facts,
    reset_ledger_scope,
    use_ledger_scope,
)
from workflows.equity_research.identity import ANALYST_ROLE_ID, MAIN_ROLE_ID
from workflows.equity_research.prompts import (
    ANALYST_SYSTEM,
    HARVEST_SYSTEM,
    analysis_user_message,
)
from workflows.equity_research.sources import (
    lookups_for,
    render_facts_block,
    render_report,
    resolve_source_texts,
    resolve_xbrl_values,
)
from workflows.equity_research.verification import verify

logger = logging.getLogger(__name__)

HARVEST_MAX_TURNS = 40
ANALYSIS_MAX_TURNS = 30
TOOL_TIMEOUT_S = 120
LLM_TIMEOUT_S = 600


async def equity_research_node(
    state: dict[str, Any], ctx: NodeContext,
) -> dict[str, Any]:
    """Run harvest → analysis → verification for one research question."""
    question = str(state.get("original_question") or "").strip()
    if not question:
        raise ValueError("equity_research_node requires 'original_question' in state")

    resources = registry.get(ResourceManager)
    agent_cfg = (state.get("metadata") or {}).get("agent", {})

    # Pin the ledger to this run for the whole node, so the tables the tools
    # write from inside the loop are the tables read here after it returns.
    # Without this the two resolve to different keys and every emit_fact
    # succeeds into a table nobody reads.
    scope_token = use_ledger_scope(ctx.task_id or "equity_research")

    # A long-lived process reuses this module. Start clean so one run's facts
    # can never be cited by the next — content-hash dedupe would happily join
    # two unrelated runs' tables.
    clear_ledger()

    try:
        harvest = await _run_phase(
            role_id=MAIN_ROLE_ID,
            system_prompt=HARVEST_SYSTEM,
            user_message=question,
            resources=resources,
            task_id=ctx.task_id,
            max_turns=int(agent_cfg.get("harvest_max_turns", HARVEST_MAX_TURNS)),
        )
        facts = ledger_facts()
        logger.info("harvest recorded %d fact(s)", len(facts))

        analysis = await _run_phase(
            role_id=ANALYST_ROLE_ID,
            system_prompt=ANALYST_SYSTEM,
            user_message=analysis_user_message(question, render_facts_block(facts)),
            resources=resources,
            task_id=ctx.task_id,
            max_turns=int(agent_cfg.get("analysis_max_turns", ANALYSIS_MAX_TURNS)),
        )

        # The gate is sync and pure, so its inputs are resolved here.
        facts = ledger_facts()
        claims = ledger_claims()
        xbrl = await resolve_xbrl_values(facts)
        texts = await resolve_source_texts(facts)
        xbrl_lookup, source_text = lookups_for(xbrl, texts)
        result = verify(facts, claims, xbrl_lookup=xbrl_lookup, source_text=source_text)

        verdicts = [f.verdict.value for f in result.findings]
        logger.info(
            "gate: %d fact(s) in, %d surviving, verdicts=%s",
            len(facts), len(result.facts), verdicts,
        )

        harvest_stop = getattr(harvest, "stopped_by", None)
        analysis_stop = getattr(analysis, "stopped_by", None)
        report = render_report(
            question, result.facts, result.claims, result.findings, result.removed,
            harvest_stopped_by=harvest_stop, analysis_stopped_by=analysis_stop,
        )
        return {
            "final_answer": report,
            "final_content": report,
            "equity_research_facts": result.facts,
            "equity_research_claims": result.claims,
            "equity_research_removed": result.removed,
            "equity_research_findings": [
                {
                    "fact_id": f.fact_id,
                    "verdict": f.verdict.value,
                    "reported_value": f.reported_value,
                    "authoritative_value": f.authoritative_value,
                }
                for f in result.findings
            ],
            "harvest_stopped_by": harvest_stop,
            "analysis_stopped_by": analysis_stop,
        }
    finally:
        # Clear while the scope is still pinned, then release it.
        clear_ledger()
        reset_ledger_scope(scope_token)


async def _run_phase(
    *,
    role_id: str,
    system_prompt: str,
    user_message: str,
    resources: ResourceManager,
    task_id: str,
    max_turns: int,
) -> Any:
    """Run one ReAct loop bound to one role's tool pool.

    The tools come from the role, never from the caller. That is what keeps the
    analyst's lack of network access a property of the registration rather than
    a promise made in a prompt.
    """
    tools = list(resources.get_tools_for_role(role_id))
    logger.info(
        "phase %s: %d tool(s) — %s",
        role_id, len(tools), ", ".join(sorted(t.name for t in tools)),
    )
    return await run_agent_loop(
        system_prompt=system_prompt,
        user_message=user_message,
        llm=resources.get_llm(role_id),
        tools=tools,
        config=LoopConfig(
            max_turns=max_turns,
            tool_timeout=TOOL_TIMEOUT_S,
            llm_timeout=LLM_TIMEOUT_S,
            task_id=task_id,
            role_id=role_id,
        ),
    )
