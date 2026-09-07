"""Seam B — workflow registration, asserted against fresh in-memory registries.

Same shape as ``tests/test_agent_team_workflow.py``. No model is started: the
properties under test are properties of the registration itself, which is
exactly why they are cheap to pin.
"""

from __future__ import annotations

import pytest

from frontier_agent.core.runtime.registries.agents import AgentRegistry
from frontier_agent.core.runtime.registries.workflows import WorkflowContext
from frontier_agent.scheduling.pipeline_registry import PipelineRegistry
from workflows.agent_team import WEB_TOOL_NAMES
from workflows.equity_research import ANALYST_ROLE_ID, MAIN_ROLE_ID, register


@pytest.fixture
def agents() -> AgentRegistry:
    registry = AgentRegistry()
    register(WorkflowContext(PipelineRegistry(), registry))
    return registry


def test_equity_research_registers_its_two_roles() -> None:
    # No pipeline yet: both shipped coordinator nodes resolve their LLM and
    # tool pool from role ids fixed at import, so pointing a spec at one would
    # silently run these roles as somebody else's. Wiring a coordinator node
    # is the next slice; the roles and their tool boundaries stand on their own.
    pipelines = PipelineRegistry()
    agents = AgentRegistry()

    register(WorkflowContext(pipelines, agents))

    assert agents.has(MAIN_ROLE_ID)
    assert agents.has(ANALYST_ROLE_ID)


def test_the_analyst_cannot_reach_the_network(agents: AgentRegistry) -> None:
    # The load-bearing invariant of the whole design. An analyst that can
    # search will go and find support for the conclusion it already has;
    # withholding the tools is the only thing that actually prevents it,
    # and it is a property of the role, not of the prompt.
    analyst = agents.get(ANALYST_ROLE_ID)

    assert WEB_TOOL_NAMES.isdisjoint(analyst.allowed_tools)


def test_the_analyst_has_no_shell_either(agents: AgentRegistry) -> None:
    # Withholding web_search while leaving a shell in place would be
    # theatre: one curl and the analyst is back on the open internet.
    analyst = agents.get(ANALYST_ROLE_ID)

    assert "bash" not in analyst.allowed_tools
    assert "run_python_code" not in analyst.allowed_tools


def test_the_analyst_can_still_record_findings_and_load_chain_knowledge(
    agents: AgentRegistry,
) -> None:
    # Cutting off the network must not cut off the job: the analyst still
    # emits into the two tables and still loads its own link's notes.
    analyst = agents.get(ANALYST_ROLE_ID)

    assert "emit_fact" in analyst.allowed_tools
    assert "emit_claim" in analyst.allowed_tools
    assert analyst.enable_skills
    # Skill metadata is injected into the prompt, but loading a SKILL.md body
    # needs read_text — without it the analyst can see its knowledge and not
    # open it.
    assert "read_text" in analyst.allowed_tools


def test_data_collection_is_a_tool_on_the_coordinator_not_a_separate_agent(
    agents: AgentRegistry,
) -> None:
    # Harvesting is deterministic API work. Giving it its own reasoning agent
    # buys nothing and adds a turn in which numbers can be mis-transcribed.
    coordinator = agents.get(MAIN_ROLE_ID)

    assert "fetch_xbrl_metric" in coordinator.allowed_tools
    assert not agents.has("equity_research_harvester")
